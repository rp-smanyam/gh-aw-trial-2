"""Generic fire-and-forget Kafka publish wrapper.

`SerializingProducer.produce` is synchronous and under back-pressure can
block the calling thread. Calling it from a coroutine would block the
event loop — unacceptable on the voice hot path. This wrapper hands the
sync call to a thread, wraps it in `asyncio.wait_for(timeout=...)`, and
schedules it as a background task via `loop.create_task(...)` so the
caller's turn never blocks.

Caller owns the `pending_tasks` set so session teardown can await
in-flight publishes; `drain_pending_publishes()` is the drain helper.

`on_success` is an optional callback invoked from the task's
done-callback once delivery succeeds (no timeout, no exception). Used
by callers that need delivery-time side effects — e.g., the
FRUSTRATED_USER once-per-conversation dedup flag flips here so a
publish failure leaves the flag clear and the next turn can retry.
"""

import asyncio
from typing import Callable

import structlog

from agent_leasing.kafka.registry_producer import KafkaProducerProtocol

logger = structlog.getLogger()


def fire_and_forget_publish(
    producer: KafkaProducerProtocol,
    event: dict,
    pending_tasks: set[asyncio.Task],
    *,
    enabled: bool,
    timeout_seconds: float,
    log_prefix: str,
    on_success: Callable[[], None] | None = None,
) -> asyncio.Task | None:
    """Schedule a publish as a non-blocking background task.

    Returns the created task (or None if the feature flag is off or
    there's no running loop — both are logged and dropped cleanly).

    `log_prefix` is prepended to `_published` / `_skipped` event names so
    each caller's logs stay greppable (e.g. `task_activity_published`).
    The asyncio task name is derived from `log_prefix` as well.

    `on_success`, when provided, runs from the task's done-callback only
    if delivery completed without timeout, exception, or cancellation.
    Exceptions raised from `on_success` are caught and logged so a
    misbehaving callback can never poison the pending-tasks bookkeeping.
    """
    if not enabled:
        logger.debug(f"{log_prefix}_skipped", reason="feature_flag_disabled")
        return None

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning(
            f"{log_prefix}_skipped",
            reason="no_running_loop",
        )
        return None

    task = loop.create_task(
        _publish_with_timeout(producer, event, timeout_seconds, log_prefix),
        name=f"{log_prefix}-publish",
    )
    pending_tasks.add(task)
    task.add_done_callback(pending_tasks.discard)
    if on_success is not None:
        task.add_done_callback(_make_on_success_handler(on_success, log_prefix))
    return task


def _make_on_success_handler(
    on_success: Callable[[], None],
    log_prefix: str,
) -> Callable[[asyncio.Task], None]:
    """Build a done_callback that runs `on_success` only on confirmed
    delivery — `_publish_with_timeout` returns True on success and False
    on timeout/exception, so the callback's branch reads the task result
    and discards every non-True outcome.
    """

    def _handler(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            return
        if task.result() is not True:
            return
        try:
            on_success()
        except Exception:
            logger.exception(f"{log_prefix}_on_success_callback_failed")

    return _handler


async def _publish_with_timeout(
    producer: KafkaProducerProtocol,
    event: dict,
    timeout_seconds: float,
    log_prefix: str,
) -> bool:
    """Returns True on confirmed delivery, False if the publish was
    skipped (timeout, exception). Re-raises CancelledError so structured
    cancellation propagates.
    """
    try:
        await asyncio.wait_for(
            asyncio.to_thread(producer.produce, event),
            timeout=timeout_seconds,
        )
    except TimeoutError:
        logger.warning(
            f"{log_prefix}_skipped",
            reason="publish_timeout",
            timeout_seconds=timeout_seconds,
        )
        return False
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception(
            f"{log_prefix}_skipped",
            reason="exception",
            exc_type=type(exc).__name__,
            exc_message=str(exc),
        )
        return False

    logger.info(f"{log_prefix}_published")
    return True


async def drain_pending_publishes(pending_tasks: set[asyncio.Task]) -> None:
    """Await any in-flight publishes at session teardown; clear the set.
    TODO(KNCK-39556 PR 2): wire into non-voice request-complete and voice
    call-end hooks alongside the first extractor.
    """
    if not pending_tasks:
        return
    await asyncio.gather(*list(pending_tasks), return_exceptions=True)
    pending_tasks.clear()

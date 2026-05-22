"""Pytest plugin: pool-based pass/fail for non-deterministic (LLM) tests.

Instead of retrying individual cases with ``@pytest.mark.flaky``, pool all
parametrized cases together and pass the pool when >= *threshold* % succeed.

Marker API
----------
    @pytest.mark.pool(threshold=0.9)                      # auto-named pool
    @pytest.mark.pool(threshold=0.9, name="legal_advice")  # shared pool
    @pytest.mark.pool(threshold=0.9, min_failures=1)       # tolerate at least 1 failure

``min_failures`` (default 1) guarantees that small pools can absorb at least
that many failures regardless of the threshold.  For example, a pool of 3
tests with ``threshold=0.9, min_failures=1`` passes with 1 failure (the
threshold alone would require 0 failures).  For large pools the percentage
threshold dominates naturally.

Compatible with pytest-xdist (``-n auto``): tests in the same pool are
grouped onto one worker via ``xdist_group``, and results are forwarded to
the controller via report sections so the summary and exit code are correct.
"""

from __future__ import annotations

import dataclasses
import json

import pytest


@dataclasses.dataclass
class PoolState:
    threshold: float
    min_failures: int = 1
    passed: int = 0
    failed: int = 0
    failed_nodeids: list[str] = dataclasses.field(default_factory=list)
    failed_details: list[tuple[str, str]] = dataclasses.field(default_factory=list)


_pools: dict[str, PoolState] = {}

_SECTION_KEY = "pool_data"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pool_name_from_item(item: pytest.Item) -> str | None:
    marker = item.get_closest_marker("pool")
    if marker is None:
        return None
    name = marker.kwargs.get("name")
    if name:
        return name
    # Auto-name: strip the parametrize suffix so all params share one pool.
    nodeid = item.nodeid
    bracket = nodeid.find("[")
    return nodeid[:bracket] if bracket != -1 else nodeid


def _threshold_from_item(item: pytest.Item) -> float:
    marker = item.get_closest_marker("pool")
    assert marker is not None
    return marker.kwargs.get("threshold", 0.9)


def _min_failures_from_item(item: pytest.Item) -> int:
    marker = item.get_closest_marker("pool")
    assert marker is not None
    return marker.kwargs.get("min_failures", 1)


def _record(
    pool_name: str, threshold: float, min_failures: int, nodeid: str, passed: bool, longrepr: str = ""
) -> None:
    """Record a single test result into the pool state dict."""
    if pool_name not in _pools:
        _pools[pool_name] = PoolState(threshold=threshold, min_failures=min_failures)
    pool = _pools[pool_name]
    if passed:
        pool.passed += 1
    else:
        pool.failed += 1
        pool.failed_nodeids.append(nodeid)
        pool.failed_details.append((nodeid, longrepr))


def _one_line_summary(nodeid: str, longrepr: str) -> str:
    """Compact failure summary for the pool results section.

    Extracts test_name[param] from the nodeid and the last meaningful
    assertion line from longrepr, truncated for readability.
    """
    # Strip module path — keep test_name[param]
    short = nodeid.rsplit("::", 1)[-1] if "::" in nodeid else nodeid
    # Truncate long parametrize labels
    if "[" in short:
        name, _, param = short.partition("[")
        param = param.rstrip("]")
        if len(param) > 60:
            param = param[:57] + "..."
        short = f"{name}[{param}]"

    # Extract the last AssertionError or E-line from longrepr
    reason = ""
    for line in reversed(longrepr.splitlines()):
        stripped = line.strip()
        if stripped.startswith("AssertionError:") or stripped.startswith("AssertionError"):
            reason = stripped
            break
        if stripped.startswith("E "):
            reason = stripped[2:].strip()
            break
    if not reason:
        reason = "failed"
    if len(reason) > 80:
        reason = reason[:77] + "..."

    return f"  - {short}: {reason}"


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "pool(threshold, name=None, min_failures=1): pool parametrized cases and pass if failures <= max(threshold-based, min_failures)",
    )
    _pools.clear()


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Assign xdist group markers so all tests in the same pool run on one worker.

    Without this, ``-n auto`` splits pool members across workers and each worker
    only sees a subset, computing incorrect pass rates.
    """
    for item in items:
        pool_name = _pool_name_from_item(item)
        if pool_name is not None and not item.get_closest_marker("xdist_group"):
            item.add_marker(pytest.mark.xdist_group(pool_name))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    outcome = yield
    report = outcome.get_result()

    if call.when != "call":
        return

    pool_name = _pool_name_from_item(item)
    if pool_name is None:
        return

    threshold = _threshold_from_item(item)
    min_failures = _min_failures_from_item(item)
    longrepr = str(report.longrepr) if report.failed and report.longrepr else ""

    # Embed pool metadata in report sections so pool state can be aggregated
    # in pytest_runtest_logreport (works in both single-process and xdist).
    report.sections.append(
        (
            _SECTION_KEY,
            json.dumps(
                {
                    "name": pool_name,
                    "threshold": threshold,
                    "min_failures": min_failures,
                    "nodeid": item.nodeid,
                    "passed": report.passed,
                    "longrepr": longrepr,
                }
            ),
        )
    )

    if report.failed:
        # Convert to xfail so individual failures don't fail the session yet.
        report.outcome = "skipped"
        report.wasxfail = f"pool '{pool_name}' — deferred to pool evaluation"


def pytest_runtest_logreport(report) -> None:
    """Aggregate pool results from report sections.

    This hook runs on the controller process in both single-process and xdist
    modes, so it is the single source of truth for pool state — avoiding
    double-counting.
    """
    if report.when != "call":
        return
    for section_name, content in report.sections:
        if section_name != _SECTION_KEY:
            continue
        data = json.loads(content)
        _record(
            data["name"],
            data["threshold"],
            data.get("min_failures", 1),
            data["nodeid"],
            data["passed"],
            data["longrepr"],
        )
        break


def _pool_passed(pool: PoolState) -> bool:
    """Return True if the pool meets its pass criteria.

    A pool passes when failures <= max(allowed_by_threshold, min_failures).
    This ensures small pools can always tolerate at least ``min_failures``
    failures even when the percentage threshold alone would not allow any.
    """
    total = pool.passed + pool.failed
    if total == 0:
        return True
    allowed_by_threshold = int(total * (1 - pool.threshold))
    allowed = max(allowed_by_threshold, pool.min_failures)
    return pool.failed <= allowed


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    for pool in _pools.values():
        if not _pool_passed(pool):
            session.exitstatus = pytest.ExitCode.TESTS_FAILED
            return


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    if not _pools:
        return

    # -- failure details (always printed so you can track flaky cases) ------
    any_failures = any(p.failed > 0 for p in _pools.values())
    if any_failures:
        terminalreporter.section("pool failure details")
        terminalreporter.write_line("")
        terminalreporter.write_line("WARNING: The following tests failed but were absorbed by pool thresholds.")
        terminalreporter.write_line("If your changes could have caused these failures, investigate before merging.")
        terminalreporter.write_line("")
        for pool_name, pool in sorted(_pools.items()):
            if pool.failed == 0:
                continue
            total = pool.passed + pool.failed
            rate = pool.passed / total
            passed = _pool_passed(pool)
            allowed_by_threshold = int(total * (1 - pool.threshold))
            allowed = max(allowed_by_threshold, pool.min_failures)
            terminalreporter.write_line(
                f"pool: {pool_name} ({pool.passed}/{total} passed, {rate:.0%},"
                f" {pool.failed} failed <= {allowed} allowed)  {'PASSED' if passed else 'FAILED'}"
            )
            terminalreporter.write_line("")
            for nodeid, longrepr in pool.failed_details:
                terminalreporter.write_line(f"  FAILED {nodeid}")
                for line in longrepr.splitlines():
                    terminalreporter.write_line(f"    {line}")
                terminalreporter.write_line("")

    # -- pool summary -------------------------------------------------------
    terminalreporter.section("pool results")
    for pool_name, pool in sorted(_pools.items()):
        total = pool.passed + pool.failed
        if total == 0:
            terminalreporter.write_line(f"{pool_name}: no tests collected")
            continue
        rate = pool.passed / total
        passed = _pool_passed(pool)
        allowed_by_threshold = int(total * (1 - pool.threshold))
        allowed = max(allowed_by_threshold, pool.min_failures)
        status = "PASSED" if passed else "FAILED"
        terminalreporter.write_line(
            f"{pool_name}: {pool.passed}/{total} passed ({rate:.0%}),"
            f" {pool.failed} failed <= {allowed} allowed  {status}"
        )
        for nodeid, longrepr in pool.failed_details:
            terminalreporter.write_line(_one_line_summary(nodeid, longrepr))

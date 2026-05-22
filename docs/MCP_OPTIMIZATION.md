# MCP Connection Optimization

## Overview

MCP server connections are established per-request. Each connection involves auth token acquisition and an HTTP handshake (150-300ms each). With 3-4 servers, this adds 600-1200ms of sequential latency. For VOICE, both the responder and thinker agents created identical connection sets, doubling the cost.

Two optimizations reduce this latency:

1. **Parallel connection creation** — all MCP servers connect concurrently via `asyncio.gather()`
2. **Connection sharing for VOICE** — the responder's MCP connections are transferred to the thinker instead of creating a second set

**Expected savings:** ~400-500ms for text channels, ~1000-1300ms for VOICE.

## Parallel Connection Creation

**File:** `src/agent_leasing/agent/util.py` — `AgentWithMCP._connect_mcp_servers()`

Previously, MCP servers were connected sequentially using `exit_stack.enter_async_context(mcp)`. This was safe but slow — each server blocked the next.

Now, all servers connect concurrently:

```
┌─────────────────┐
│  gather()       │
│  ┌─ connect A ──┤  ~200ms
│  ├─ connect B ──┤  ~150ms
│  └─ connect C ──┤  ~250ms
│                 │
│  Total: ~250ms  │  (max of all, not sum)
└─────────────────┘
```

### Why `connect()`/`cleanup()` instead of `enter_async_context()`

`enter_async_context()` mutates the exit stack's internal deque and cannot be called concurrently. Instead:

1. `asyncio.gather()` runs `mcp.connect()` for all servers in parallel
2. After gather completes, `exit_stack.push_async_callback(mcp.cleanup)` is called sequentially for each success — this is synchronous and safe
3. Failed servers are removed from the `mcp_servers` dict

### Cancel safety

Each `_connect_one` runs in its own `asyncio.Task`, so anyio cancel scopes within one MCP connection cannot pollute another. A `connected_mcps` list tracks successful connections as they happen. If `gather()` is interrupted by external cancellation before cleanup callbacks are registered, an `except BaseException` block cleans up any orphaned connections.

### Timeout-protected cleanup

`__aexit__` wraps `exit_stack.aclose()` in `asyncio.wait_for()` with a timeout of 5 seconds per server (minimum 10s). This prevents hangs from stuck anyio cancel scopes that escape the `_patch_anyio_deliver_cancellation` monkey-patch.

## VOICE Connection Sharing

**File:** `src/agent_leasing/agent/resident_one_agent/realtime.py` — `ResidentRealtimeResponderAgent`

The responder's `RealtimeAgent` has no MCP servers of its own — it only uses function tools (thinker_tool, end_call, transfer). Its MCP servers are used only for prefetching during init. The thinker needs the exact same MCP servers for runtime tool calls. Since prefetch completes before runtime begins, there is no concurrent access.

After `super().__aenter__()` connects and prefetches:

1. `self.mcp_servers` is transferred to `self._thinker_agent.mcp_servers`
2. `self._mcp_exit_stack` is transferred to `self._thinker_mcp_exit_stack`
3. The responder's references are cleared (`self.mcp_servers = {}`, `del self._mcp_exit_stack`)
4. The thinker's agent instance is created with the shared servers

On exit, `_thinker_mcp_exit_stack` handles cleanup. `super().__aexit__()` finds no exit stack or servers to clean up.

## Prefetch Server Folding

**File:** `src/agent_leasing/agent/resident_one_agent/agent.py` — `BaseResidentAgent.__aenter__()`

When `facilities_thinker_api_enabled` and `sr_prefetch_via_mcp` are set, a temporary facilities MCP server is needed for service request prefetch. Previously this was connected in a separate `asyncio.gather()` alongside `super().__aenter__()`.

Now the prefetch server is added to `self.mcp_servers` (key `_prefetch_facilities_mcp_server`) before calling `super().__aenter__()`, so it connects in the same parallel batch. After prefetch completes, `cleanup()` is called early and the server is removed from `mcp_servers`. Since `cleanup()` is idempotent, the exit stack callback is a harmless no-op.

## Compatibility with anyio patch

`_patch_anyio_deliver_cancellation` (in `server.py`) patches `CancelScope._deliver_cancellation` at the class level. Every cancel scope instance uses the patched version regardless of which task creates it. The bug triggers during `cleanup()`, not `connect()`, and the cleanup path is unchanged. The patch remains fully effective.

## Prior approach: Connection pooling (removed)

MCP connection pooling (`mcp_pool.py`) was previously implemented to reuse connections across requests. It was disabled due to a slow resource leak caused by anyio cancel scope bugs (anyio #695). The pool code, settings, terraform secrets, tests, and documentation have been fully removed.

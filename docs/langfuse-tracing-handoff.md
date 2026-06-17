# Langfuse Tracing: `session.id` / `user.id` / `trace_name` Propagation Handoff

## Goal

Propagate `session.id`, `user.id`, and `trace_name` to **all** OpenTelemetry spans across the entire agent runtime — main agent, sub-agents, `@traceable`-decorated middleware hooks, and model calls — so Langfuse traces contain session/user context on every span.

## Constraints & Preferences

- Must work across **asyncio task boundaries** created by LangGraph/Pregel task pool (spans run in different tasks than their callbacks)
- Must work for both **callback-created spans** (langfuse `CallbackHandler`) and **`@traceable` spans** (deepagents middleware hooks, model calls, tool wrappers)
- Must support **sub-agents** created by deepagents internal factory (which lack custom callbacks)
- **No changes** to deepagents SDK or langfuse SDK source
- `MetadataLoggerHandler` is added to `config["callbacks"]` **before** the langfuse `CallbackHandler` (`server.py:494-507`), guaranteeing its events fire first

---

## Current Codebase State

### `agent/middleware/callback_metadata_logger.py` — `MetadataLoggerHandler`

A `BaseCallbackHandler` that enters `propagate_attributes` (from `langfuse`) on **every** callback event (`on_chain_start`, `on_chat_model_start`, `on_llm_start`, `on_tool_start`). It:

1. Parses `langfuse_session_id`, `langfuse_user_id`, `langfuse_trace_name` from callback `metadata`
2. Stores the `propagate_attributes` context manager on `self._pa_cm` to prevent GC release
3. Calls `self._pa_cm.__enter__()` on every event — re-entering on each callback ensures the current asyncio task has the context

**Current state:** correctly enters `propagate_attributes` on every event. Logs verbosely at `INFO` level.

### `agent/middleware/log_langfuse_metadata.py` — `log_langfuse_metadata`

A `@before_model` middleware that reads `langfuse_session_id`, `langfuse_user_id`, `langfuse_trace_name` from both `config["metadata"]` and `config["configurable"]` and logs them.

**Current state:** diagnostic-only, zero side effects, adds latency on every model turn. To be removed after C1 verification.

### `agent/utils/tracing_diagnostics.py` — `SessionIdDiagnosticProcessor`

A `SpanProcessor` that logs `session.id`, `user.id`, and parent span ID on every `on_end` event. It is a **read-only diagnostic** — it does NOT inject attributes.

**Current state:** diagnostic-only, noisy, no production value. To be replaced by `LangfuseAttributesProcessor`.

### `agent/utils/tracing.py` — `get_langfuse_handler()`

Singleton factory that creates a langfuse `CallbackHandler` and wires `SessionIdDiagnosticProcessor` into the OTEL tracer provider. Exported and called in `server.py:501`.

### `agent/server.py` — `get_agent()`

Main graph factory. Key sections:

- **Lines 494-507:** wires `MetadataLoggerHandler` and langfuse `CallbackHandler` into `config["callbacks"]`
- **Lines 538-550:** defines the middleware stack (including `log_langfuse_metadata`)
- Metadata for the run is assembled by webhooks (`webapp.py`) and includes `langfuse_session_id`, `langfuse_user_id`, `langfuse_trace_name` in both `config["metadata"]` and `config["configurable"]`

### Metadata source points (where `langfuse_*` keys are set):

- `agent/webapp.py` — multiple webhook handlers set `langfuse_session_id` and `langfuse_user_id` (and occasionally `langfuse_trace_name` for Jira) in the run metadata
- `agent/dashboard/thread_api.py` — dashboard chat API sets the same keys

---

## Problem / Root Cause

### Two independent span creation paths

1. **Langfuse `CallbackHandler` path:** Creates spans when LangChain callbacks fire (`on_chain_start`, `on_tool_start`, etc.). These **work correctly** for the main agent because `MetadataLoggerHandler` enters `propagate_attributes` before the langfuse handler reads it, within the **same asyncio task**.

2. **Deepagents `@traceable` decorator path:** Creates spans directly via `tracer.start_as_current_span()` at function entry, **BEFORE callbacks fire**. These spans **NEVER** get `session.id` via the callback approach — the span is already created by the time any callback runs.

### Three categories of affected spans

| Category | Why it fails |
|---|---|
| **`@traceable` spans** (middleware hooks, `ChatOpenAI` model calls, tool wrappers) | Span created by `@traceable` decorator at function entry, before any callback fires. By the time `MetadataLoggerHandler.on_chat_model_start()` fires, the span already exists without `session.id`. |
| **Sub-agent spans** (all) | Sub-agents are built by deepagents internal factory — they do NOT receive `MetadataLoggerHandler` in their callback chain. No callback = no `propagate_attributes` = no `session.id`. |
| **Cross-task spans** | `on_chain_start` callbacks fire in different asyncio tasks per Pregel node (`asyncio_0` through `asyncio_11`). Even with `propagate_attributes` re-entered on each event, the span may be created in one task while the callback fires in another. |

### Trace evidence

A langfuse trace export for a `task` tool call (sub-agent) was analyzed at `/mnt/c/Users/nishchaygupta/Downloads/trace-8a8e18c4c4296b8e3abffbebfdae259d.json`:

- **276 observations** — ALL have `userId=null`, `sessionId=null`
- The root trace itself has `userId=null`, `sessionId=null`
- User context exists **only** in `trace.metadata` as `user_email` / `jira_issue_key` — not exposed as Langfuse session/user attributes
- Span types affected: CHAIN (274), TOOL (1), AGENT (1) — uniformly missing
- The sub-agent (`general-purpose` CHAIN) had 50 model calls and 173 tool batch spans, all lacking IDs

From an earlier test of the main agent trace:
- `jira_comment` tool span: `session.id=DT-17` ✓ (same task as its callback)
- All `@traceable` spans (middleware, `ChatOpenAI`, tools): `session.id=<MISSING>` (created before callback fires, or in different task)

---

## Plan: Approach C1 — `_AttrsStore` + `LangfuseAttributesProcessor`

Replace the callback-only approach with a **SpanProcessor** that injects attributes from a process-global store, bypassing asyncio task boundaries entirely.

### Architecture

```
                                     Process-global memory (bypasses asyncio)
┌─────────────────────────────────────────────────────────────────────┐
│                        _AttrsStore (class var dict)                 │
│                                                                     │
│   set(thread_id, {"session_id": ..., "user_id": ...})               │
│   get() -> dict                                                     │
└─────────────────────────────────────────────────────────────────────┘
          ▲                                      │
          │ writes                             reads
          │                                      ▼
┌─────────────────────┐          ┌─────────────────────────────────────┐
│  MetadataLogger     │          │  LangfuseAttributesProcessor        │
│  Handler            │          │  (SpanProcessor.on_start)           │
│                     │          │                                     │
│  _ensure_pa()       │          │  Reads _AttrsStore.get()            │
│  calls store.set()  │          │  Injects session.id, user.id,      │
│                     │          │  trace_name into EVERY span         │
│  (belt)             │          │  Guard: "if key not in attrs"       │
└─────────────────────┘          │  (preserves callback-set values)    │
                                 └─────────────────────────────────────┘
```

### Why C1 solves all three categories

1. **`@traceable` spans:** `LangfuseAttributesProcessor.on_start()` fires when the span is created — the **first** moment the span exists. It reads `_AttrsStore.get()` and injects the attributes before any child spans start. No timing dependency on callbacks.

2. **Sub-agent spans:** `_AttrsStore` is a Python class variable — process-global memory, not per-task contextvars. The sub-agent's OTEL tracer is the same global tracer that has `LangfuseAttributesProcessor` registered. Every span created anywhere in the process encounters `on_start()` and reads the store.

3. **Cross-task spans:** Same as above — the store is module-level state, not task-local. Any task can read it.

### Belt-and-suspenders population

- **Suspenders** (`_AttrsStore.set()` in `server.py:get_agent()`): Called in the factory task, just before `create_deep_agent()`. Ensures the store is populated for ALL spans from the start, including pre-graph initialization spans.
- **Belt** (`_AttrsStore.set()` in `MetadataLoggerHandler._ensure_pa()`): Called on every callback event. Ensures the store is updated if sub-agent metadata differs from the factory metadata.

### Precedence: callback path wins

`LangfuseAttributesProcessor` guards injection with `if key not in span.attributes`. For spans created by the langfuse `CallbackHandler` (which correctly sets `session.id`/`user.id`), the guard skips injection — the callback-set value is preserved. The processor only injects for spans that would otherwise be missing (all `@traceable` spans and sub-agent spans).

---

## Implementation Steps

### Step 1: Replace `agent/utils/tracing_diagnostics.py`

Replace `SessionIdDiagnosticProcessor` with:

```python
class _AttrsStore:
    _store: dict[str, Any] = {}

    @classmethod
    def set(cls, thread_id: str = "", **attrs: Any) -> None:
        cls._store["attrs"] = attrs
        cls._store["thread_id"] = thread_id

    @classmethod
    def get(cls) -> dict[str, Any]:
        return cls._store.get("attrs", {})
```

```python
class LangfuseAttributesProcessor(SpanProcessor):
    LANGFUSE_ATTRS = {"session.id": "session_id", "user.id": "user_id", "trace.name": "trace_name"}

    def on_start(self, span, parent_context=None):
        attrs = _AttrsStore.get()
        if not attrs:
            return
        for attr_key, store_key in self.LANGFUSE_ATTRS.items():
            if attr_key not in span.attributes and store_key in attrs:
                span.set_attribute(attr_key, str(attrs[store_key]))
```

Note: `trace.name` is the OTEL attribute name; the `@traceable` decorator may use a different key — verify with a test run.

### Step 2: Wire `_AttrsStore.set()` in `agent/server.py`

In `get_agent()`, after the metadata/configurable is parsed (around line 492), add:

```python
from .utils.tracing_diagnostics import _AttrsStore
# ...
_AttrsStore.set(
    thread_id=thread_id,
    session_id=metadata.get("langfuse_session_id") or configurable.get("langfuse_session_id", thread_id),
    user_id=metadata.get("langfuse_user_id") or configurable.get("langfuse_user_id", "unknown"),
    trace_name=metadata.get("langfuse_trace_name") or configurable.get("langfuse_trace_name"),
)
```

The exact metadata/configurable keys depend on the webhook that triggered the run — `server.py` receives the full `config` dict.

### Step 3: Wire `_AttrsStore.set()` in `MetadataLoggerHandler._ensure_pa()`

In `agent/middleware/callback_metadata_logger.py`, at the end of `_ensure_pa()` (after `propagate_attributes` is entered), add a call to `_AttrsStore.set()`:

```python
from agent.utils.tracing_diagnostics import _AttrsStore
# ...
_AttrsStore.set(
    session_id=session_id,
    user_id=user_id,
    trace_name=trace_name,
)
```

This is the "belt" — it re-populates the store on every callback event, catching cases where sub-agent metadata differs.

### Step 4: Wire `LangfuseAttributesProcessor` in `agent/utils/tracing.py`

Replace:
```python
from .tracing_diagnostics import SessionIdDiagnosticProcessor
provider.add_span_processor(SessionIdDiagnosticProcessor())
```

With:
```python
from .tracing_diagnostics import LangfuseAttributesProcessor
provider.add_span_processor(LangfuseAttributesProcessor())
```

### Step 5: Cleanup diagnostic artifacts

1. **Delete** `agent/middleware/log_langfuse_metadata.py` (entire file)
2. **Edit** `agent/middleware/__init__.py`: remove import and `__all__` entry for `log_langfuse_metadata`
3. **Edit** `agent/server.py`:
   - Remove `log_langfuse_metadata` import
   - Remove `log_langfuse_metadata,` from the middleware list (line 539)

### Step 6: Reduce logging verbosity

In `agent/middleware/callback_metadata_logger.py`, change all `logger.info(...)` calls in callback methods to `logger.debug(...)` to reduce production log noise.

### Step 7 (optional): Remove dead `on_llm_start`

Since only `on_chat_model_start` fires for chat models, `on_llm_start` in `MetadataLoggerHandler` is dead code. Can be removed or kept (harmless).

### Step 8: Verify

1. Deploy and run a test Jira issue thread
2. Export the langfuse trace for the main agent
3. Check that ALL spans have `session.id` and `user.id` — including:
   - Root `agent` CHAIN span
   - `model` CHAIN spans (both main agent and sub-agent)
   - `@traceable` middleware spans (`PatchToolCallsMiddleware.before_agent`, `TodoListMiddleware.after_model`)
   - Tool CALL/TOOL spans
   - Sub-agent `general-purpose` CHAIN and its children
4. Export a sub-agent `task` trace and verify all 276+ spans have `session.id` and `user.id`
5. Remove the `log_langfuse_metadata` middleware

---

## Risks and Concerns

### 1. Multi-tenant contamination (1000 concurrent users)

`_AttrsStore` is a single dict. Under concurrent user runs, a race exists:

```
Run A sets {session_id: "DT-16"} → context switch → Run B sets {session_id: "PR-42"} → Run A's new span reads "PR-42" (contaminated)
```

**Bounded impact:**
- Langfuse callback-created spans are **unaffected** (guarded by `if key not in span.attributes` — the callback path sets them first, processor skips them)
- Only `@traceable` spans can be contaminated, and only during the ~2-3 span creations that happen in the window between context switches
- **~1-3 contaminated `@traceable` spans per context switch**, across thousands of spans per trace
- This is statistical noise — invisible in practice

**Long-term fix:** Thread_id-keyed variant. The `_AttrsStore` interface already accepts `thread_id`, but the `SpanProcessor` currently omits it because deepagents does not expose `thread_id` as an OTEL span attribute. When/if deepagents adds thread_id to span attributes, the processor can select the correct bucket.

### 2. SpanProcessor ordering with Langfuse SDK internals

The `CallbackHandler` internally creates OTEL spans. If the langfuse SDK uses a custom span processor that runs before ours, the guard `if key not in span.attributes` must account for that. If the SDK sets these attributes AFTER `on_start` but before `on_end`, the guard is correct. If the SDK sets them BEFORE `on_start` is called, the guard correctly skips injection.

This depends on the order span processors are registered. `provider.add_span_processor()` appends to a list; processors run in registration order. The langfuse SDK may register its own processor. We should register `LangfuseAttributesProcessor` after the langfuse handler is created to ensure it runs first (or last — test both).

### 3. `trace.name` attribute name mismatch

The OTEL semantic convention attribute for trace name may differ between the `@traceable` decorator and `propagate_attributes`. `propagate_attributes` from langfuse uses `trace_name` internally (not a standard OTEL attribute). The `@traceable` spans may use `langfuse.trace.name` or similar.

**Mitigation:** Test with a real run and check what attribute name the langfuse UI expects. If `trace_name` doesn't work, the processor can simply skip it and focus on `session.id` and `user.id`, which are well-defined.

### 4. Thread safety of `_AttrsStore`

Python dicts are thread-safe for individual operations under the GIL (CPython). `set()` and `get()` are atomic at the bytecode level. No locking is needed for single-op access.

However, a concurrent read (get) while a write (set) is in progress could read partially-updated state. Mitigation: use `_store.copy()` in `get()`, or use `threading.Lock`. For single-tenant or low-concurrency deployments, this is irrelevant.

### 5. Sub-agent metadata divergence

If a sub-agent receives different `langfuse_session_id`/`langfuse_user_id` metadata than the parent (e.g., deepagents assigns a different session ID), the belt-and-suspenders approach would overwrite the parent's values in `_AttrsStore`. This is unlikely in practice — sub-agents inherit the parent's metadata — but worth verifying.

### 6. Memory leak

`_AttrsStore._store` grows unboundedly under multi-tenant with `thread_id`-keyed storage. For the current single-dict approach, every concurrent run overwrites the previous entry, so growth is bounded to 1 entry. If thread_id-keyed storage is added later, implement TTL-based eviction or LRU pruning.

### 7. Startup ordering

`LangfuseAttributesProcessor` is registered in `get_langfuse_handler()` (singleton), which is called lazily from `server.py:501`. If a span is created before `get_langfuse_handler()` is called, it won't have the processor and won't get injection. However, in practice, no OTEL spans exist before `server.py` finishes because `create_deep_agent()` hasn't been called yet.

---

## Relevant Files Summary

| File | Purpose | Action |
|---|---|---|
| `agent/utils/tracing_diagnostics.py` | `SessionIdDiagnosticProcessor` (diagnostic) | Replace with `_AttrsStore` + `LangfuseAttributesProcessor` |
| `agent/utils/tracing.py` | Wires span processor into tracer | Replace `SessionIdDiagnosticProcessor` → `LangfuseAttributesProcessor` |
| `agent/middleware/callback_metadata_logger.py` | `MetadataLoggerHandler` — enters `propagate_attributes` | Add `_AttrsStore.set()` call in `_ensure_pa()`; reduce log verbosity |
| `agent/middleware/log_langfuse_metadata.py` | Diagnostic middleware, no side effects | Delete entire file |
| `agent/middleware/__init__.py` | Middleware exports | Remove `log_langfuse_metadata` import/export |
| `agent/server.py` | Main graph factory | Add `_AttrsStore.set()`; remove `log_langfuse_metadata` import and middleware entry |
| `agent/webapp.py` | Webhook handlers setting `langfuse_*` metadata | No changes needed |
| `agent/dashboard/thread_api.py` | Dashboard thread API setting `langfuse_*` metadata | No changes needed |

---

## Verification Checklist

- [ ] Main agent `agent` root span has `session.id`, `user.id` in langfuse UI
- [ ] Main agent `model` CHAIN spans have `session.id`, `user.id`
- [ ] `@traceable` middleware spans (`PatchToolCallsMiddleware.before_agent`, `TodoListMiddleware.after_model`) have `session.id`, `user.id` in OTEL diagnostic logs
- [ ] `@traceable` model spans (`ChatOpenAI` / model call) have `session.id`, `user.id`
- [ ] Tool spans (`jira_comment`, `linear_comment`, etc.) have `session.id`, `user.id`
- [ ] Sub-agent `general-purpose` spans have `session.id`, `user.id` (verified via exported trace)
- [ ] No double-injection (callback-set values preserved, not overwritten by processor)
- [ ] `log_langfuse_metadata` middleware removed without breaking anything
- [ ] `make lint` passes
- [ ] `make test` passes

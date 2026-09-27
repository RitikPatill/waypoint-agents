# Roadmap

## Intentionally out of scope

### Distributed / multi-process workers

Waypoint runs everything in a single asyncio process. That's enough to prove the durability pattern — the event log survives a process crash because SQLite WAL flushes to disk before `append()` returns. Distributing work across multiple processes introduces a different class of problems: leader election, sharded event logs, distributed locking to prevent two workers from resuming the same run. Those are real engineering challenges, but they're orthogonal to the orchestration pattern demonstrated here. Temporal solves them well; Waypoint deliberately does not try to compete.

### Non-Anthropic LLM providers

The agent loop in `agent.py` is deliberately coupled to the Anthropic SDK's message format: tool-use blocks, `tool_result` turns, `stop_reason == "end_turn"`. Each provider has a subtly different shape for these concepts. Abstracting over providers behind a common interface is a useful but entirely separate problem — it would double the surface area of the codebase without teaching anything new about durable orchestration. If you need provider abstraction, LiteLLM or the OpenAI-compatibility shim are the right tools.

### Visual workflow editor

Workflows in Waypoint are Python dataclasses — cheap to define, trivial to version-control, and directly testable with `pytest`. A drag-and-drop visual editor would add a JavaScript build system, a graph serialisation format, a round-trip import/export layer, and a whole new failure surface. The portfolio point here is orchestration, not UI tooling. Define your workflows in code; use git to version them.

### Auth, multi-tenancy, RBAC

Waypoint is a single-user reference implementation. Adding authentication would require session management, token storage, and request scoping — none of which teaches anything about durable agents. If you deploy this in production, put a reverse proxy (nginx, Caddy, Cloudflare Access) in front of it.

### Vector stores / RAG

The deliberate absence of a vector store is a signal, not an oversight. Waypoint is about orchestration, not retrieval. Any retrieval strategy can be dropped in as a `Tool` primitive — `search_documents`, `query_index`, whatever your stack uses. Baking one in would conflate two orthogonal concerns and obscure the core pattern.

---

## Possible extensions (if this were productionised)

These are not commitments — they are the natural next steps if Waypoint were to grow into a production system:

- **PostgreSQL + LISTEN/NOTIFY** — swap SQLite for Postgres to support multi-process workers; use `LISTEN/NOTIFY` for the in-process event bus so workers on different machines see each other's events
- **Provider abstraction layer** — define an `LLMClient` protocol with `messages_create()` and implement it for OpenAI, Gemini, and Anthropic; all three use similar tool-use patterns under the hood
- **Retry policies and timeout budgets** — per-agent configuration for max retries, backoff strategy, and wall-clock timeout; currently a single unhandled exception fails the whole run
- **Structured handoff payloads** — enforce typed handoff schemas with JSON Schema validation so agents can't pass malformed data downstream; today payloads are untyped dicts
- **Metrics export** — Prometheus counters for LLM call latency, tool execution latency, event throughput, and resume frequency; useful for capacity planning
- **Workflow versioning** — tag runs with the workflow definition hash so replays use the same agent/tool configuration that originally started them; prevents subtle bugs when a workflow is edited mid-run

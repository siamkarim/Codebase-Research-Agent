# Architecture Decisions

## Architecture Overview

The Codebase Research Agent is structured as a layered Django REST API backed by an autonomous AI agent:

- **API Layer (views.py)**: Django REST Framework views handle HTTP concerns — validation, serialization, 202 response, error handling. Views delegate business logic immediately.
- **Repo Manager (repo_manager.py)**: Isolates all git operations (clone/pull). Returns a `(Repository, local_path)` tuple. This is the only synchronous blocking step before the thread spawns.
- **Agent Loop (agent.py)**: `CodebaseResearchAgent` drives the Claude tool-calling loop. It manages message history, token tracking, step counting, and maps tool names to implementations.
- **Tools Layer (tools/)**: `code_tools.py` handles all filesystem interactions; `db_tools.py` handles all ORM writes. Both are pure functions — no class state — making them easy to test independently.
- **Database (models.py)**: Four models capture the full lifecycle: `Repository` → `ResearchSession` → `ToolCallLog` + `Finding`.

Data flows top-down: HTTP request → view → repo_manager → ResearchSession created → background thread → agent → tools → DB writes → polling client reads session status.

## Why Async via Threading (Not Celery)

For this project scope, `threading.Thread` is the right call:

- **Simplicity**: No broker (Redis/RabbitMQ), no worker processes, no separate deployment concern. One `pip install` away from running.
- **Returns 202 immediately**: The clone step happens synchronously in the request (so we can validate the URL fails fast), then the agent runs in a daemon thread. The client polls `GET /api/sessions/{id}/`.
- **Django ORM is thread-safe**: Re-fetching the session inside the thread avoids stale state. `daemon=True` ensures threads don't block server shutdown.

**Known trade-off**: Threading doesn't survive process restarts — a server restart kills in-flight sessions (they'd be stuck as `running`). At production scale, replace with **Celery + Redis** for durable task queues and **WebSockets** for live streaming updates. That's a known, intentional decision for this scope.

## Database Schema Rationale

- **Repository**: Deduplicated by URL (`unique=True`). Single source of truth for repo metadata. `last_analyzed_at` lets you see staleness at a glance without querying sessions.
- **ResearchSession**: One record per question asked. Status transitions: `pending → running → completed | failed`. `total_tokens_used` enables cost tracking per question.
- **ToolCallLog**: Full audit trail of every tool call — name, input args, output summary, success flag, timestamp. This is invaluable for debugging agent behavior and improving system prompts. `output_summary` is capped at 1000 chars to prevent unbounded growth.
- **Finding**: Structured discoveries linked to sessions. The key design insight: findings survive across sessions. `get_previous_findings()` loads all prior findings at agent start, enabling cross-session knowledge accumulation. The agent builds on prior research rather than re-exploring from scratch every time.

**Trade-off**: `ToolCallLog` can grow large in high-volume deployments. At scale, add a retention policy (e.g., delete logs older than 30 days) or move them to a separate append-only log store. The current schema is correct for the use case.

## Agent Design: Why finish() Tool

Using `finish()` as an explicit stop signal is a deliberate design choice over relying on Claude's `end_turn` stop reason:

- **Agent controls completion**: Claude calls `finish()` when it has enough information. This is semantically richer than "I have nothing more to say."
- **Structured output**: `finish()` requires `answer` and `file_references` fields — the agent must produce structured output, not just trail off.
- **Prevents premature answers**: The system prompt instructs the agent to make at least 3 tool calls before finishing. With `end_turn`, there's nothing stopping a premature text response.
- **Hard ceiling**: `max_steps=15` acts as a safety net against infinite loops. When hit, the agent gets one final call *without tools* to summarize what it found — a graceful degradation rather than a silent failure.
- **Token budget**: `MAX_TOKENS_BUDGET=50000` is a secondary ceiling. Both guards prevent runaway costs.

## Context Management for Large Repos

Large repos (like FastAPI's ~200k-line codebase) require careful token management:

- **`list_files()` top-level only by default**: Forces the agent to navigate purposefully. Dumping the entire file tree at once would waste tokens on irrelevant paths. The agent must use `subpath` to drill deeper.
- **`get_file_summary()` before `read_file()`**: Shows first 30 + last 10 lines so the agent can decide whether a file is worth reading fully. Avoids spending 3000 tokens on a file that turns out to be unrelated.
- **`read_file()` truncates at 300 lines**: No single file can blow the context. For longer files, the agent can use `search_code()` to pinpoint the exact region of interest.
- **`search_code()` via grep**: Jumps directly to relevant code without reading directories. Searching "Depends" across the FastAPI repo surfaces the dependency injection machinery in 2-3 results without touching unrelated files.
- **Previous findings loaded first**: `get_previous_findings()` is the mandatory first call. The agent inherits knowledge from prior sessions, reducing redundant exploration on repeated questions about the same repo.

## LLM Choice: Claude claude-sonnet-4-20250514

- **Best-in-class tool calling**: Sonnet reliably follows complex multi-step tool-use instructions, respects ordering constraints (e.g., "always call get_previous_findings first"), and produces well-structured `finish()` outputs.
- **Code understanding**: Strong at reading Python, TypeScript, and configuration files — exactly what this agent needs.
- **Cost/performance balance**: Sonnet offers near-Opus quality at significantly lower cost, which matters when a single research session might involve 10-15 tool calls and 30k input tokens.

## What I'd Do Differently With More Time

- **Celery + Redis**: Proper async task queue for production — survives restarts, supports retries, enables horizontal scaling.
- **Vector embeddings**: Use `pgvector` with OpenAI/Cohere embeddings to enable semantic code search. Currently `search_code()` is grep-based (exact match). Semantic search would let the agent find conceptually related code even without keyword overlap.
- **PostgreSQL + full-text search**: `tsvector`/`tsquery` on `Finding.note` would enable cross-repo knowledge queries.
- **WebSocket streaming**: Push tool call events to the client in real time instead of polling. Each `save_finding()` and `log_tool_call()` would emit an event.
- **Rate limiting + auth**: API keys, per-user rate limits, and request queuing to prevent abuse and control LLM costs.
- **Repo caching strategy**: Currently clones on first use and pulls on subsequent calls. A smarter strategy would track commit SHA and skip the pull if the repo hasn't changed.

## Known Limitations

- **Sync clone blocks the request thread**: The `clone_or_update_repo()` call happens synchronously in the POST handler. A large repo could hold the request open for 10-30 seconds. Mitigation: move clone into the background thread too, and return 202 immediately with a "cloning" status.
- **Threading doesn't scale**: A single Django process can handle a handful of concurrent agents, but each thread holds a Python GIL slot during CPU-bound operations and an OS thread slot during I/O. Beyond ~10 concurrent sessions, you need a proper task queue.
- **No deduplication of sessions**: Two simultaneous POST requests for the same repo/question create two separate sessions. Idempotency key support would prevent this.
- **grep not available on all systems**: `search_code()` falls back to Python's `os.walk`, which is slower but functional.

## How I Used AI Tools

This project was built using Claude as the primary development tool (via the Codebase Research Agent spec itself):

- **AI-generated**: The overall structure, all boilerplate (settings.py, models.py, serializers.py), the TOOL_DEFINITIONS schema, and the agent loop skeleton were generated from the spec. The system prompt wording was also largely spec-driven.
- **Manually written / heavily reviewed**: The `_validate_path()` security check in `code_tools.py` (path traversal prevention), the `search_code()` fallback logic, the grep argument construction (include patterns per extension), and the thread safety notes in `views.py`.
- **Where AI helped most**: Scaffolding the Django boilerplate quickly, ensuring all serializer fields matched the model fields, and generating the comprehensive TOOL_DEFINITIONS with correct JSON Schema.
- **Where AI got it wrong**: Initial drafts of `search_code()` tried to pass all READABLE_EXTENSIONS as a single `--include` glob, which grep doesn't support. Had to restructure to multiple `--include=*.ext` flags. Also, early `run()` implementations didn't correctly handle the case where Claude returns multiple tool_use blocks in a single response — had to iterate over `response.content` and collect all results before appending the user message.
- **Prompting approach**: Used the spec as a structured prompt with explicit section headers and code skeletons. The most effective pattern was providing the exact function signature + docstring and letting the implementation fill in — rather than asking for whole files at once.

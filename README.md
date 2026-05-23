# Codebase Research Agent

## What it does

Codebase Research Agent is a Django REST API that runs an AI agent to answer technical questions about GitHub repositories. You provide a repo URL and a natural language question; the agent clones the repo, explores the codebase using tool calling (file listing, code search, file reading), saves structured findings to a PostgreSQL/SQLite database, and returns a detailed answer with file and line references.

The API returns immediately (202 Accepted) and the agent runs in a background thread — clients poll for results.

## Quick Start

```bash
git clone <repo>
cd codebase_research_agent
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env — set GEMINI_API_KEY (or GOOGLE_API_KEY). With a Gemini key set, Gemini is used by default unless LLM_PROVIDER=anthropic.
python manage.py migrate
python manage.py runserver
```

## Generate Sample Data

In a new terminal, with the server running:

```bash
python manage.py seed_data
```

Or use the standalone script:

```bash
python seed.py
```

## API Usage

### Start a research session

```bash
curl -X POST http://localhost:8000/api/sessions/ \
  -H "Content-Type: application/json" \
  -d '{"repo_url": "https://github.com/tiangolo/fastapi",
       "question": "How does FastAPI handle dependency injection?"}'
```

Response (202 Accepted):
```json
{
  "session_id": 1,
  "status": "pending",
  "message": "Research session started. Poll GET /api/sessions/{id}/ for results.",
  "poll_url": "/api/sessions/1/"
}
```

### Poll for results

```bash
curl http://localhost:8000/api/sessions/1/
```

Poll until `status` is `"completed"` or `"failed"`. The `final_answer` field contains the full response with file references.

### List all sessions

```bash
curl http://localhost:8000/api/sessions/list/
# Filter by repo: ?repo_url=https://github.com/tiangolo/fastapi
# Filter by status: ?status=completed
# Paginate: ?page=2
```

### List repositories researched

```bash
curl http://localhost:8000/api/repos/
```

### See sessions for a specific repo

```bash
curl http://localhost:8000/api/repos/1/sessions/
```

## Django Admin

Visit [http://localhost:8000/admin/](http://localhost:8000/admin/)

Create a superuser first:

```bash
python manage.py createsuperuser
```

The admin shows all repositories, research sessions, tool call logs, and findings with full audit trails.

## Architecture

- **Django REST Framework** handles the API layer with clean serializers and views.
- **Background threading** (`threading.Thread`) runs the agent asynchronously so the API returns 202 immediately.
- **Gemini** (see `GEMINI_MODEL` in settings; default `gemini-2.5-flash`) or **Claude claude-sonnet-4-20250514** drives the agent via function/tool calling — see `LLM_PROVIDER` and API keys in `.env.example`.
- **4 database models**: Repository, ResearchSession, ToolCallLog, Finding — see `agent/models.py`.
- **Tool suite**: `list_files`, `read_file`, `search_code`, `get_file_summary`, `save_finding`, `get_previous_findings`, `list_past_sessions`, `finish`.

See [DECISIONS.md](DECISIONS.md) for full architecture rationale and trade-offs.

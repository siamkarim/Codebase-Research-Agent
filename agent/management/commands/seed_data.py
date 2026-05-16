import time
import requests
from django.core.management.base import BaseCommand

BASE_URL = "http://127.0.0.1:8000/api"
REPO_URL = "https://github.com/tiangolo/fastapi"
QUESTIONS = [
    "How does FastAPI handle dependency injection internally?",
    "What is the request lifecycle in FastAPI from route to response?",
]


class Command(BaseCommand):
    help = "Seed the database by running two research sessions against the FastAPI repo."

    def handle(self, *args, **options):
        self.stdout.write("Starting seed - this will clone repos and run the agent...")
        self.stdout.write(f"Target repo: {REPO_URL}\n")

        for i, question in enumerate(QUESTIONS):
            session_id = self._start_session(question)

            if i == 0:
                time.sleep(3)

            result = self._poll_session(session_id)
            if result:
                self.stdout.write(f"\nFinal answer (first 500 chars):")
                self.stdout.write(f"  {result.get('final_answer', 'N/A')[:500]}")
                self.stdout.write(f"Findings: {len(result.get('findings', []))}")
                self.stdout.write(f"Tool calls: {len(result.get('tool_calls', []))}")
                self.stdout.write(f"Tokens used: {result.get('total_tokens_used', 0)}\n")

        self.stdout.write(
            self.style.SUCCESS(
                "Seed complete. Run: python manage.py runserver then check /admin/"
            )
        )

    def _start_session(self, question: str) -> int:
        self.stdout.write(f"\n→ Submitting: {question!r}")
        try:
            resp = requests.post(
                f"{BASE_URL}/sessions/",
                json={"repo_url": REPO_URL, "question": question},
                timeout=120,
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            self.stderr.write(
                f"Failed to connect to server at {BASE_URL}. Is it running?\n{e}"
            )
            raise SystemExit(1)

        data = resp.json()
        session_id = data["session_id"]
        self.stdout.write(f"  Session ID: {session_id} (status: {data['status']})")
        return session_id

    def _poll_session(self, session_id: int, timeout: int = 300) -> dict:
        self.stdout.write(f"  Polling session {session_id}...", ending="")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = requests.get(f"{BASE_URL}/sessions/{session_id}/", timeout=30)
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                self.stderr.write(f"\nPoll error: {e}")
                time.sleep(5)
                continue

            if data["status"] not in ("pending", "running"):
                self.stdout.write(f" done ({data['status']})")
                return data

            self.stdout.write(".", ending="")
            self.stdout.flush()
            time.sleep(3)

        self.stdout.write(" TIMED OUT")
        return {}

#!/usr/bin/env python
"""
Standalone seed script. Run with the server already started:
    python seed.py
"""
import time
import requests

BASE_URL = "http://127.0.0.1:8000/api"
REPO_URL = "https://github.com/tiangolo/fastapi"
QUESTIONS = [
    "How does FastAPI handle dependency injection internally?",
    "What is the request lifecycle in FastAPI from route to response?",
]


def start_session(question: str) -> int:
    print(f"\n→ Starting session: {question!r}")
    resp = requests.post(
        f"{BASE_URL}/sessions/",
        json={"repo_url": REPO_URL, "question": question},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    session_id = data["session_id"]
    print(f"  Session ID: {session_id} (status: {data['status']})")
    return session_id


def poll_session(session_id: int, timeout: int = 300) -> dict:
    print(f"  Polling session {session_id}...", end="", flush=True)
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.get(f"{BASE_URL}/sessions/{session_id}/", timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data["status"] not in ("pending", "running"):
            print(f" done ({data['status']})")
            return data
        print(".", end="", flush=True)
        time.sleep(3)
    print(" TIMED OUT")
    return {}


def main():
    print("Starting seed - this will clone repos and run the agent...")
    print(f"Target repo: {REPO_URL}")

    for i, question in enumerate(QUESTIONS):
        session_id = start_session(question)

        if i == 0:
            # Wait 3 seconds before first poll as specified
            time.sleep(3)

        result = poll_session(session_id)
        if result:
            print(f"\n  Final answer (first 500 chars):")
            print(f"  {result.get('final_answer', 'N/A')[:500]}")
            print(f"  Findings: {len(result.get('findings', []))}")
            print(f"  Tool calls: {len(result.get('tool_calls', []))}")
            print(f"  Tokens used: {result.get('total_tokens_used', 0)}")

    print("\nSeed complete. Run: python manage.py runserver then check /admin/")


if __name__ == "__main__":
    main()

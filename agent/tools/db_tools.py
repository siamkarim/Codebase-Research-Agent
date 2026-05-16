import logging

logger = logging.getLogger(__name__)


def save_finding(session_id: int, file_path: str, note: str, line_reference: str = "") -> dict:
    from agent.models import Finding
    try:
        finding = Finding.objects.create(
            session_id=session_id,
            file_path=file_path,
            note=note,
            line_reference=line_reference,
        )
        return {"success": True, "finding_id": finding.id}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_previous_findings(repo_url: str) -> dict:
    from agent.models import Repository, Finding
    from collections import defaultdict

    try:
        try:
            repo = Repository.objects.get(url=repo_url)
        except Repository.DoesNotExist:
            return {
                "repo_url": repo_url,
                "total_past_sessions": 0,
                "findings": [],
                "count": 0,
                "tip": "No previous research found for this repo.",
            }

        past_sessions = repo.sessions.filter(status="completed")
        total_past = past_sessions.count()

        findings = (
            Finding.objects.filter(session__repo=repo, session__status="completed")
            .select_related("session")
            .order_by("file_path", "created_at")
        )

        grouped: dict = defaultdict(lambda: {"notes": [], "last_seen_in_session": None})
        for f in findings:
            grouped[f.file_path]["notes"].append(f.note)
            grouped[f.file_path]["last_seen_in_session"] = f.session_id

        findings_list = [
            {
                "file_path": fp,
                "notes": data["notes"],
                "last_seen_in_session": data["last_seen_in_session"],
            }
            for fp, data in grouped.items()
        ]

        return {
            "repo_url": repo_url,
            "total_past_sessions": total_past,
            "findings": findings_list,
            "count": len(findings_list),
            "tip": "These are from previous research. Prioritize unexplored areas.",
        }
    except Exception as e:
        return {"error": str(e)}


def list_past_sessions(repo_url: str) -> dict:
    from agent.models import Repository

    try:
        try:
            repo = Repository.objects.get(url=repo_url)
        except Repository.DoesNotExist:
            return {"sessions": [], "total": 0}

        sessions = repo.sessions.prefetch_related("findings").order_by("-started_at")
        session_list = [
            {
                "id": s.id,
                "question": s.question,
                "status": s.status,
                "completed_at": s.completed_at.isoformat() if s.completed_at else None,
                "finding_count": s.findings.count(),
            }
            for s in sessions
        ]
        return {"sessions": session_list, "total": len(session_list)}
    except Exception as e:
        return {"error": str(e)}


def log_tool_call(
    session_id: int,
    tool_name: str,
    input_args: dict,
    output_summary: str,
    success: bool = True,
) -> None:
    from agent.models import ToolCallLog
    try:
        ToolCallLog.objects.create(
            session_id=session_id,
            tool_name=tool_name,
            input_args=input_args,
            output_summary=output_summary[:1000],
            success=success,
        )
    except Exception as e:
        logger.error(f"Failed to log tool call [{tool_name}] for session {session_id}: {e}")

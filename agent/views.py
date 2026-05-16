import threading
import logging
from django.db.models import Count
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from .models import Repository, ResearchSession
from .serializers import (
    StartSessionSerializer,
    ResearchSessionDetailSerializer,
    ResearchSessionListSerializer,
    RepositorySerializer,
)

logger = logging.getLogger(__name__)

PAGE_SIZE = 20


def run_agent_in_background(session_id: int, repo_local_path: str) -> None:
    from agent.models import ResearchSession
    from agent.agent import CodebaseResearchAgent

    try:
        session = ResearchSession.objects.select_related("repo").get(id=session_id)
        session.status = "running"
        session.save(update_fields=["status"])

        agent = CodebaseResearchAgent(session, repo_local_path)
        agent.run()
    except Exception as e:
        logger.error(f"Agent thread failed for session {session_id}: {e}", exc_info=True)


class StartSessionView(APIView):
    def post(self, request):
        serializer = StartSessionSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        repo_url = serializer.validated_data["repo_url"]
        question = serializer.validated_data["question"]

        try:
            from agent.repo_manager import clone_or_update_repo
            repo, local_path = clone_or_update_repo(repo_url)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"Unexpected error cloning {repo_url}: {e}", exc_info=True)
            return Response(
                {"error": "Failed to clone repository. Check the URL and try again."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session = ResearchSession.objects.create(
            repo=repo,
            question=question,
            status="pending",
        )

        thread = threading.Thread(
            target=run_agent_in_background,
            args=(session.id, local_path),
            daemon=True,
        )
        thread.start()

        return Response(
            {
                "session_id": session.id,
                "status": "pending",
                "message": "Research session started. Poll GET /api/sessions/{id}/ for results.",
                "poll_url": f"/api/sessions/{session.id}/",
            },
            status=status.HTTP_202_ACCEPTED,
        )


class SessionDetailView(APIView):
    def get(self, request, pk):
        try:
            session = (
                ResearchSession.objects.select_related("repo")
                .prefetch_related("tool_calls", "findings")
                .get(pk=pk)
            )
        except ResearchSession.DoesNotExist:
            return Response({"error": "Session not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = ResearchSessionDetailSerializer(session)
        return Response(serializer.data)


class SessionListView(APIView):
    def get(self, request):
        qs = (
            ResearchSession.objects.select_related("repo")
            .prefetch_related("findings", "tool_calls")
            .annotate(
                finding_count=Count("findings", distinct=True),
                tool_call_count=Count("tool_calls", distinct=True),
            )
            .order_by("-started_at")
        )

        repo_url = request.query_params.get("repo_url")
        if repo_url:
            qs = qs.filter(repo__url=repo_url)

        session_status = request.query_params.get("status")
        if session_status:
            qs = qs.filter(status=session_status)

        # Simple manual pagination
        try:
            page = max(1, int(request.query_params.get("page", 1)))
        except (ValueError, TypeError):
            page = 1

        start = (page - 1) * PAGE_SIZE
        end = start + PAGE_SIZE
        total = qs.count()
        items = qs[start:end]

        serializer = ResearchSessionListSerializer(items, many=True)
        return Response({
            "count": total,
            "page": page,
            "page_size": PAGE_SIZE,
            "results": serializer.data,
        })


class RepositoryListView(APIView):
    def get(self, request):
        repos = Repository.objects.annotate(session_count=Count("sessions")).order_by("-created_at")
        serializer = RepositorySerializer(repos, many=True)
        return Response(serializer.data)


class RepoSessionsView(APIView):
    def get(self, request, pk):
        try:
            repo = Repository.objects.get(pk=pk)
        except Repository.DoesNotExist:
            return Response({"error": "Repository not found."}, status=status.HTTP_404_NOT_FOUND)

        sessions = (
            ResearchSession.objects.filter(repo=repo)
            .annotate(
                finding_count=Count("findings", distinct=True),
                tool_call_count=Count("tool_calls", distinct=True),
            )
            .order_by("-started_at")
        )
        serializer = ResearchSessionListSerializer(sessions, many=True)
        return Response({"repo": repo.name, "sessions": serializer.data})

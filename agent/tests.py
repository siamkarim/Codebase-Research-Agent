import json
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.urls import reverse

from agent.models import Repository, ResearchSession
from agent.tools.code_tools import list_files


class ListFilesTest(TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        root = Path(self.tmpdir)
        (root / "main.py").write_text("print('hello')")
        (root / "README.md").write_text("# Test")
        (root / "src").mkdir()
        (root / "src" / "utils.py").write_text("def helper(): pass")
        (root / "__pycache__").mkdir()
        (root / "__pycache__" / "main.cpython-311.pyc").write_bytes(b"")
        (root / "image.png").write_bytes(b"")

    def test_returns_correct_structure(self):
        result = list_files(self.tmpdir, "")
        self.assertIn("files", result)
        self.assertIn("dirs", result)
        self.assertIn("path", result)
        self.assertIn("total_entries", result)

    def test_skips_pycache_and_binary_files(self):
        result = list_files(self.tmpdir, "")
        names = [f["name"] for f in result["files"]]
        dir_names = result["dirs"]
        self.assertNotIn("__pycache__", dir_names)
        self.assertNotIn("image.png", names)
        self.assertIn("main.py", names)
        self.assertIn("README.md", names)

    def test_subpath_lists_nested_dir(self):
        result = list_files(self.tmpdir, "src")
        names = [f["name"] for f in result["files"]]
        self.assertIn("utils.py", names)

    def test_path_traversal_blocked(self):
        result = list_files(self.tmpdir, "../../etc")
        self.assertIn("error", result)

    def test_max_entries_limit(self):
        # Create 90 files to verify the 80-entry cap
        root = Path(self.tmpdir) / "big"
        root.mkdir()
        for i in range(90):
            (root / f"file_{i}.py").write_text("")
        result = list_files(str(root), "")
        self.assertLessEqual(result["total_entries"], 80)


class ResearchSessionModelTest(TestCase):
    def setUp(self):
        self.repo = Repository.objects.create(
            url="https://github.com/test/repo",
            name="test/repo",
        )

    def test_session_creation_defaults(self):
        session = ResearchSession.objects.create(
            repo=self.repo,
            question="How does X work in this codebase?",
        )
        self.assertEqual(session.status, "pending")
        self.assertEqual(session.total_tokens_used, 0)
        self.assertEqual(session.final_answer, "")
        self.assertEqual(session.error_message, "")
        self.assertIsNone(session.completed_at)

    def test_status_transitions(self):
        session = ResearchSession.objects.create(
            repo=self.repo,
            question="How does Y work in this codebase?",
        )
        session.status = "running"
        session.save(update_fields=["status"])

        refreshed = ResearchSession.objects.get(pk=session.pk)
        self.assertEqual(refreshed.status, "running")

        refreshed.status = "completed"
        refreshed.final_answer = "The answer is 42."
        refreshed.save(update_fields=["status", "final_answer"])

        final = ResearchSession.objects.get(pk=session.pk)
        self.assertEqual(final.status, "completed")
        self.assertEqual(final.final_answer, "The answer is 42.")

    def test_session_count_property(self):
        self.assertEqual(self.repo.session_count, 0)
        ResearchSession.objects.create(
            repo=self.repo,
            question="What does Z do in this codebase?",
        )
        self.assertEqual(self.repo.session_count, 1)

    def test_str_representation(self):
        session = ResearchSession.objects.create(
            repo=self.repo,
            question="What is the authentication flow in this codebase?",
        )
        self.assertIn(str(session.id), str(session))


class StartSessionViewTest(TestCase):
    @patch("agent.views.threading.Thread")
    @patch("agent.repo_manager.clone_or_update_repo")
    def test_post_returns_202_with_session_id(self, mock_clone, mock_thread):
        repo = Repository.objects.create(
            url="https://github.com/tiangolo/fastapi",
            name="tiangolo/fastapi",
        )
        mock_clone.return_value = (repo, "/tmp/fake/path")

        mock_thread_instance = MagicMock()
        mock_thread.return_value = mock_thread_instance

        response = self.client.post(
            "/api/sessions/",
            data=json.dumps({
                "repo_url": "https://github.com/tiangolo/fastapi",
                "question": "How does FastAPI handle dependency injection?",
            }),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 202)
        data = response.json()
        self.assertIn("session_id", data)
        self.assertEqual(data["status"], "pending")
        self.assertIn("poll_url", data)

        # Verify thread was started
        mock_thread_instance.start.assert_called_once()
        # Verify it's a daemon thread
        self.assertTrue(mock_thread.call_args.kwargs.get("daemon", False))

    def test_post_rejects_non_github_url(self):
        response = self.client.post(
            "/api/sessions/",
            data=json.dumps({
                "repo_url": "https://gitlab.com/some/repo",
                "question": "How does this project handle authentication?",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_post_rejects_short_question(self):
        response = self.client.post(
            "/api/sessions/",
            data=json.dumps({
                "repo_url": "https://github.com/tiangolo/fastapi",
                "question": "short",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    @patch("agent.repo_manager.clone_or_update_repo")
    def test_post_handles_clone_failure(self, mock_clone):
        mock_clone.side_effect = ValueError("Repo clone timed out")

        response = self.client.post(
            "/api/sessions/",
            data=json.dumps({
                "repo_url": "https://github.com/tiangolo/fastapi",
                "question": "How does FastAPI handle dependency injection?",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())

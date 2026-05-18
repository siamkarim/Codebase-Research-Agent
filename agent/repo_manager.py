import shutil
import logging
from pathlib import Path
from django.conf import settings
from django.utils import timezone
import git
from git import Repo, GitCommandError, InvalidGitRepositoryError

logger = logging.getLogger(__name__)


def parse_repo_name(repo_url: str) -> str:
    """Extract owner/repo slug from a GitHub URL and return as owner_repo."""
    url = repo_url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    parts = url.rstrip("/").split("/")
    if len(parts) >= 2:
        return f"{parts[-2]}_{parts[-1]}"
    return parts[-1]


def clone_or_update_repo(repo_url: str):
    """
    Clones a GitHub repo or pulls updates if already cloned.
    Returns (Repository instance, local_path string).
    """
    from agent.models import Repository

    folder_name = parse_repo_name(repo_url)
    local_path = settings.REPOS_DIR / folder_name

    if local_path.exists():
        logger.info(f"Repo exists at {local_path}, attempting git pull")
        try:
            repo_git = Repo(local_path)
            origin = repo_git.remotes.origin
            origin.pull(ff_only=True)
        except InvalidGitRepositoryError:
            logger.warning(f"{local_path} is not a valid git repo, re-cloning")
            shutil.rmtree(local_path)
            _clone(repo_url, local_path)
        except GitCommandError as e:
            logger.warning(f"git pull failed, re-cloning: {e}")
            shutil.rmtree(local_path)
            _clone(repo_url, local_path)
    else:
        _clone(repo_url, local_path)

    # Derive a human-readable name: owner/repo
    parts = repo_url.rstrip("/").split("/")
    if len(parts) >= 2:
        name = f"{parts[-2]}/{parts[-1]}"
    else:
        name = parts[-1]
    name = name.replace(".git", "")

    repo, _ = Repository.objects.get_or_create(
        url=repo_url,
        defaults={"name": name},
    )
    if not repo.name:
        repo.name = name
    repo.local_path = str(local_path)
    repo.last_analyzed_at = timezone.now()
    repo.save()

    return repo, str(local_path)


def _clone(repo_url: str, local_path: Path) -> None:
    logger.info(f"Cloning {repo_url} → {local_path}")
    try:
        Repo.clone_from(repo_url, str(local_path))
    except GitCommandError as e:
        raise ValueError(f"Git error: {e.stderr.strip() if e.stderr else str(e)}")

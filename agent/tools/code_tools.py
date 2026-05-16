import os
import subprocess
from pathlib import Path
from django.conf import settings

SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    "dist", "build", ".eggs",
}

SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".png", ".jpg", ".jpeg", ".gif",
    ".ico", ".svg", ".woff", ".ttf", ".pdf", ".zip",
    ".tar", ".gz", ".exe", ".bin", ".lock",
}

READABLE_EXTENSIONS = {
    ".py", ".js", ".ts", ".tsx", ".jsx", ".md",
    ".txt", ".yml", ".yaml", ".toml", ".cfg",
    ".ini", ".json", ".html", ".css", ".sh", ".env.example",
}


def _validate_path(repo_local_path: str, file_path: str) -> Path:
    """Validate that file_path resolves inside repo_local_path. Raises ValueError on traversal."""
    repo_root = Path(repo_local_path).resolve()
    target = (repo_root / file_path).resolve()
    if not str(target).startswith(str(repo_root)):
        raise ValueError(f"Path traversal detected: {file_path!r} is outside the repository.")
    return target


def list_files(repo_local_path: str, subpath: str = "") -> dict:
    repo_root = Path(repo_local_path).resolve()
    target_dir = (repo_root / subpath).resolve() if subpath else repo_root

    if not str(target_dir).startswith(str(repo_root)):
        return {"error": "Path traversal detected", "subpath": subpath}

    if not target_dir.exists() or not target_dir.is_dir():
        return {"error": f"Directory not found: {subpath!r}", "subpath": subpath}

    files = []
    dirs = []
    entry_count = 0

    try:
        entries = sorted(target_dir.iterdir(), key=lambda e: (e.is_file(), e.name))
        for entry in entries:
            if entry_count >= 80:
                break
            name = entry.name

            if entry.is_dir():
                if name in SKIP_DIRS or name.endswith(".egg-info"):
                    continue
                dirs.append(name)
                entry_count += 1
            elif entry.is_file():
                if entry.suffix in SKIP_EXTENSIONS:
                    continue
                try:
                    size = entry.stat().st_size
                except OSError:
                    size = 0
                files.append({"name": name, "size": size})
                entry_count += 1
    except PermissionError as e:
        return {"error": str(e), "subpath": subpath}

    return {
        "path": subpath or "",
        "files": files,
        "dirs": dirs,
        "total_entries": entry_count,
        "note": "showing top-level only — use subpath param to explore deeper" if not subpath else f"listing '{subpath}'",
    }


def read_file(repo_local_path: str, file_path: str) -> dict:
    try:
        target = _validate_path(repo_local_path, file_path)
    except ValueError as e:
        return {"error": str(e), "file_path": file_path}

    if not target.exists():
        return {"error": "File not found", "file_path": file_path}

    if target.suffix not in READABLE_EXTENSIONS and target.name not in {".env.example"}:
        return {"error": f"File type not readable: {target.suffix!r}", "file_path": file_path}

    try:
        raw = target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"error": str(e), "file_path": file_path}

    all_lines = raw.splitlines()
    total_lines = len(all_lines)
    max_lines = settings.MAX_FILE_LINES
    truncated = total_lines > max_lines
    shown_lines = all_lines[:max_lines]

    numbered = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(shown_lines))

    result: dict = {
        "file_path": file_path,
        "content": numbered,
        "total_lines": total_lines,
        "lines_shown": len(shown_lines),
        "truncated": truncated,
    }
    if truncated:
        result["truncation_note"] = (
            f"File truncated at line {max_lines}. Use search_code() for specific patterns."
        )
    return result


def search_code(repo_local_path: str, query: str) -> dict:
    repo_root = Path(repo_local_path).resolve()
    matches = []
    total_found = 0

    # Build include patterns for grep
    include_args = []
    for ext in READABLE_EXTENSIONS:
        include_args += [f"--include=*{ext}"]

    try:
        result = subprocess.run(
            ["grep", "-rn", "-i", query, str(repo_root)] + include_args,
            capture_output=True,
            text=True,
            timeout=30,
        )
        raw_lines = result.stdout.splitlines()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        raw_lines = _python_search(repo_root, query)

    # Build context: collect all (file, lineno, content) then add surrounding lines
    raw_matches = []
    for raw in raw_lines:
        parts = raw.split(":", 2)
        if len(parts) < 3:
            continue
        file_abs, lineno_str, content = parts[0], parts[1], parts[2]
        if not lineno_str.isdigit():
            continue
        try:
            rel = str(Path(file_abs).relative_to(repo_root))
        except ValueError:
            rel = file_abs
        raw_matches.append((rel, int(lineno_str), content))

    total_found = len(raw_matches)

    for rel, lineno, content in raw_matches[:20]:
        context_lines = _get_context(repo_root / rel, lineno, context=2)
        matches.append({
            "file": rel,
            "line_number": lineno,
            "line_content": content.strip(),
            "context": context_lines,
        })

    result_dict: dict = {
        "query": query,
        "matches": matches,
        "total_found": total_found,
    }
    if total_found > 20:
        result_dict["note"] = "showing first 20 matches"
    return result_dict


def _python_search(repo_root: Path, query: str) -> list[str]:
    results = []
    q = query.lower()
    for dirpath, dirnames, filenames in os.walk(repo_root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.endswith(".egg-info")]
        for fname in filenames:
            fpath = Path(dirpath) / fname
            if fpath.suffix not in READABLE_EXTENSIONS:
                continue
            try:
                lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
                for i, line in enumerate(lines, 1):
                    if q in line.lower():
                        results.append(f"{fpath}:{i}:{line}")
            except OSError:
                continue
    return results


def _get_context(file_path: Path, lineno: int, context: int = 2) -> str:
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(0, lineno - 1 - context)
        end = min(len(lines), lineno + context)
        return "\n".join(
            f"{i+1:4d} | {lines[i]}" for i in range(start, end)
        )
    except OSError:
        return ""


def get_file_summary(repo_local_path: str, file_path: str) -> dict:
    try:
        target = _validate_path(repo_local_path, file_path)
    except ValueError as e:
        return {"error": str(e), "file_path": file_path}

    if not target.exists():
        return {"error": "File not found", "file_path": file_path}

    try:
        raw = target.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return {"error": str(e), "file_path": file_path}

    all_lines = raw.splitlines()
    total_lines = len(all_lines)

    beginning_lines = all_lines[:30]
    ending_lines = all_lines[-10:] if total_lines > 30 else []

    beginning = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(beginning_lines))
    if ending_lines:
        start_idx = total_lines - len(ending_lines)
        ending = "\n".join(f"{start_idx+i+1:4d} | {line}" for i, line in enumerate(ending_lines))
    else:
        ending = ""

    return {
        "file_path": file_path,
        "total_lines": total_lines,
        "beginning": beginning,
        "ending": ending,
        "tip": "Use read_file() to get full content",
    }

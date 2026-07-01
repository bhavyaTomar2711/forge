"""Repository Agent.

One job: given the current task (and optionally a step from the Planner),
return the relevant files from the cloned repo as {filename: content}.

Strategy (per FORGE_BRAIN.md):
  - file-tree walk (no vector DB)
  - keyword/grep search to surface candidate files
  - return content for the most relevant hits, capped so we don't blow context

Scoring heuristic (simple, no ML):
  1. exact filename mention in the task -> strong signal
  2. symbol/path token match (camelCase split, dot-paths, kebab) -> strong
  3. content grep hit weighted by # occurrences, capped
  4. recency/size tiebreakers

Tunable cap so a 5k-file monorepo doesn't return 5k files.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from graph.state import SessionState

# directories we never descend into
_SKIP_DIRS = {
    ".git", "node_modules", ".next", "dist", "build", "out",
    ".turbo", ".cache", "coverage", "__pycache__", ".venv", "venv",
    ".idea", ".vscode", ".DS_Store",
}

# binary / non-text extensions we skip when reading content
_BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
    ".pdf", ".zip", ".tar", ".gz", ".7z", ".rar",
    ".mp4", ".mp3", ".wav", ".mov", ".avi",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".lock", ".pyc", ".class", ".so", ".dll", ".exe",
}

_MAX_FILE_BYTES = 200_000          # skip huge files
_MAX_CONTENT_CHARS = 8_000         # per-file truncation for context budget
_DEFAULT_MAX_FILES = 20            # how many files to return to the next agent
_MAX_GREP_HITS = 50                # raw grep hits before scoring


# ---------- tokenization ----------

def _tokenize(text: str) -> list[str]:
    """Split text into lowercase tokens. CamelCase, kebab-case, dot-paths, slashes all split."""
    text = text.lower()
    # split on non-alphanum, but keep dots/slashes as separators too
    text = re.sub(r"[/\\\.\-_]+", " ", text)
    parts = re.findall(r"[a-z0-9]+", text)
    # drop noise
    return [t for t in parts if len(t) > 1]


@dataclass
class _FileEntry:
    path: Path
    rel: str
    tokens: set[str]
    grep_hits: int = 0
    name_hit: bool = False


# ---------- file tree walk ----------

def _walk_repo(repo_path: Path) -> list[_FileEntry]:
    """Walk the repo, returning metadata for every text-like file (no content yet)."""
    entries: list[_FileEntry] = []
    if not repo_path.exists():
        return entries

    # Path.walk is py 3.12+; user is on 3.11 so use os.walk compat shim
    import os
    for root, dirs, files in os.walk(repo_path):
        # prune in-place so os.walk skips them
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            p = Path(root) / fname
            if p.suffix.lower() in _BINARY_EXTS:
                continue
            try:
                if p.stat().st_size > _MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            try:
                rel = p.relative_to(repo_path).as_posix()
            except ValueError:
                continue
            entries.append(_FileEntry(path=p, rel=rel, tokens=set(_tokenize(rel))))
    return entries


# ---------- content grep ----------

def _grep_files(entries: list[_FileEntry], query_tokens: list[str]) -> None:
    """Count content-level token matches per file. Mutates entries in place."""
    if not query_tokens:
        return
    # build a single regex with alternation for speed
    pattern = re.compile(r"\b(" + "|".join(re.escape(t) for t in query_tokens) + r")\b",
                         re.IGNORECASE)
    for e in entries:
        try:
            text = e.path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        hits = pattern.findall(text)
        if hits:
            e.grep_hits = len(hits)


# ---------- scoring ----------

def _score(e: _FileEntry, query_tokens: list[str]) -> int:
    """Higher = more relevant."""
    s = 0
    if not query_tokens:
        # no query -> just return small files first (likely most relevant config / src)
        return -e.path.stat().st_size if e.path.exists() else 0
    qset = set(query_tokens)
    # filename/path token overlap
    overlap = len(e.tokens & qset)
    s += overlap * 3
    if e.name_hit:
        s += 10
    s += min(e.grep_hits, 20)  # cap so a single huge file doesn't dominate
    # small bonus for shallow paths (src/foo.ts > src/components/nested/deep/foo.ts)
    depth = e.rel.count("/")
    s -= depth
    return s


def _is_name_hit(rel: str, query_tokens: list[str]) -> bool:
    name = Path(rel).name.lower()
    for t in query_tokens:
        if t in name:
            return True
    return False


# ---------- public API ----------

def find_relevant_files(
    repo_path: str | Path,
    task: str,
    max_files: int = _DEFAULT_MAX_FILES,
) -> dict[str, str]:
    """Return {relative_path: file_content} for files most relevant to `task`.

    Pure function. No I/O side effects beyond reading files. Synchronous.
    """
    repo = Path(repo_path).resolve()
    if not repo.exists() or not repo.is_dir():
        return {}

    tokens = _tokenize(task)
    if not tokens:
        # no useful tokens -> bail with empty (caller can decide to walk whole tree)
        return {}

    entries = _walk_repo(repo)
    if not entries:
        return {}

    # mark filename hits before scoring
    for e in entries:
        e.name_hit = _is_name_hit(e.rel, tokens)

    _grep_files(entries, tokens)

    # score + sort
    scored = sorted(entries, key=lambda e: _score(e, tokens), reverse=True)

    # take top N with content
    result: dict[str, str] = {}
    for e in scored:
        if len(result) >= max_files:
            break
        # skip files with zero signal unless we have nothing yet
        if not (e.name_hit or e.grep_hits or e.tokens & set(tokens)):
            if result:
                continue
        try:
            content = e.path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if len(content) > _MAX_CONTENT_CHARS:
            content = content[:_MAX_CONTENT_CHARS] + "\n... [truncated]"
        result[e.rel] = content
    return result


# ---------- LangGraph node wrapper ----------

def repository_node(state: SessionState) -> dict:
    """LangGraph node. Reads state, returns the partial state update.

    Reads: state.repo_path, state.task, state.plan
    Writes: state.relevant_files, state.status
    """
    repo_path = state.get("repo_path", "")
    # planner's current step (if any) overrides the bare task for scoping
    plan = state.get("plan") or []
    retry_count = state.get("retry_count", 0)
    # if we're being called as part of a retry, planner has already digested the error;
    # the most recent plan step is the right focus. else use the task verbatim.
    if plan and retry_count > 0:
        focus = plan[0]  # in a real run we'd track "current step" index; keep simple for phase 1
    elif plan:
        focus = plan[0]
    else:
        focus = state.get("task", "")

    relevant = find_relevant_files(repo_path, focus)
    return {
        "relevant_files": relevant,
        "status": "repository_done",
    }

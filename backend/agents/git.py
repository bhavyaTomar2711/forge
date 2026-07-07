"""Git Agent.

Phase 5 part 2: takes the session's edited repo, commits the changes on a
new branch, pushes the branch to GitHub, and opens a PR with an LLM-
generated description summarizing the task + diff.

The user controls the "ship" trigger via session_store.ship_session() --
this node never auto-runs the push, it only fires when explicitly asked.
That preserves the "we never push without permission" promise from the
real-repo tests in phase 2.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import github_api as gh
from graph.state import SessionState
from llm import chat_text

_GIT_BIN_CANDIDATES = ["git", r"C:\Program Files\Git\cmd\git.exe"]


def _git_bin() -> str:
    for c in _GIT_BIN_CANDIDATES:
        try:
            r = subprocess.run([c, "--version"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                return c
        except (OSError, subprocess.TimeoutExpired):
            continue
    return "git"


def _run_git(repo: Path, *args: str, timeout: int = 60) -> tuple[int, str, str]:
    proc = subprocess.run(
        [_git_bin(), "-C", str(repo), *args],
        capture_output=True, text=True, timeout=timeout,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _authed_remote_url(clone_url: str, token: str) -> str:
    """Inject the token into an HTTPS GitHub URL so pushes work."""
    return clone_url.replace("https://", f"https://x-access-token:{token}@", 1)


def _sanitize_branch(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9._/-]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "forge/change"


# ---------- PR description generation ----------

_PR_DESC_SYSTEM = """\
You write GitHub pull request descriptions. Given a task and a list of
files changed with their added/removed line counts, produce a short PR
description in Markdown with:
  - a 1-line title-style summary
  - a "What changed" section listing the touched files
  - a "Why" section restating the task in user-facing language
Keep it under 200 words total. Output ONLY the markdown -- no preamble.
"""


def _build_pr_description(task: str, diff_summary: list[dict]) -> str:
    diff_text = "\n".join(
        f"- {d.get('path','?')}: +{d.get('added',0)}/-{d.get('removed',0)}"
        for d in (diff_summary or [])
    ) or "(no diff summary available)"
    try:
        return chat_text(
            _PR_DESC_SYSTEM,
            f"Task: {task}\n\nFiles changed:\n{diff_text}",
            max_tokens=400,
        )
    except Exception:
        # LLM failed (rate-limit etc) -- ship a clean fallback so the push
        # can still go through.
        return f"## What changed\n{diff_text}\n\n## Why\n{task}\n"


# ---------- public API ----------

def ship(state: SessionState, *, token: str, branch: str | None = None,
         commit_message: str | None = None) -> dict:
    """Commit all current edits on a new branch, push, and open a PR.

    Returns a dict with `branch`, `commit`, `pull_request_url`, etc.
    Raises on any failure. The caller is responsible for triggering this --
    the graph's git_node stays a no-op until invoked by ship_session()."""
    repo_path = Path(state["repo_path"]).resolve()
    if not (repo_path / ".git").exists():
        raise RuntimeError(f"no git repo at {repo_path}")

    # figure out the remote URL from origin
    _, origin_url, _ = _run_git(repo_path, "remote", "get-url", "origin")
    origin_url = origin_url.strip()
    if not origin_url:
        raise RuntimeError("repo has no 'origin' remote")

    # extract owner/repo from the clone URL
    # e.g. https://github.com/bhavyaTomar2711/Therapist_Assignment.git
    m = re.search(r"github\.com[:/]([^/]+)/([^/\s]+?)(?:\.git)?$", origin_url)
    if not m:
        raise RuntimeError(f"could not parse owner/repo from origin: {origin_url}")
    full_name = f"{m.group(1)}/{m.group(2)}"

    # switch remote URL to the authed one so we can push
    authed = _authed_remote_url(origin_url, token)
    _run_git(repo_path, "remote", "set-url", "origin", authed, timeout=15)

    # check we're not on the default branch (we shouldn't be -- coding agent
    # never creates a branch itself)
    code, current_branch, _ = _run_git(repo_path, "symbolic-ref", "--short", "HEAD")
    if code != 0:
        # detached HEAD -- create a branch so we have something to work from
        base = branch or "forge/change"
        _run_git(repo_path, "checkout", "-B", base, timeout=30)
        current_branch = base
    current_branch = current_branch.strip()

    # decide the working branch name
    working_branch = branch or _sanitize_branch(
        "forge/" + (state.get("task", "change")[:50] or "change")
    )

    # 1. create the branch on the remote (via API) so we have a ref to push to
    if working_branch != current_branch:
        _run_git(repo_path, "checkout", "-B", working_branch, timeout=30)

    # stage everything that was edited (the diff_summary lists touched files)
    edited_files = [d.get("path") for d in (state.get("diff_summary") or []) if d.get("path")]
    if edited_files:
        _run_git(repo_path, "add", "--", *edited_files, timeout=30)
    else:
        _run_git(repo_path, "add", "-A", timeout=30)

    # commit (--allow-empty in case a re-run produced no net diff)
    msg = commit_message or f"forge: {state.get('task', 'change')[:72]}"
    code, _, err = _run_git(repo_path, "commit", "-m", msg, "--allow-empty", timeout=30)
    if code != 0:
        raise RuntimeError(f"git commit failed: {err}")

    # 2. push the new branch
    code, _, err = _run_git(repo_path, "push", "-u", "origin", working_branch, timeout=120)
    if code != 0:
        safe_err = err.replace(token, "***")
        raise RuntimeError(f"git push failed: {safe_err}")

    # 3. find the default branch so we can open a PR against it
    code, head_branch, _ = _run_git(repo_path, "rev-parse", "--abbrev-ref", "HEAD")
    head_branch = head_branch.strip() or working_branch
    repo_meta_resp = gh.list_repos(token, per_page=100)
    base = next((r["default_branch"] for r in repo_meta_resp
                 if r["full_name"] == full_name), "main")

    # 4. generate PR description and open
    body = _build_pr_description(state.get("task", ""), state.get("diff_summary") or [])
    pr = gh.open_pull_request(
        token, full_name,
        head=head_branch, base=base,
        title=msg, body=body,
    )
    return {
        "branch": head_branch,
        "commit_message": msg,
        "pr_url": pr.get("html_url", ""),
        "pr_number": pr.get("number"),
        "repo": full_name,
    }


# ---------- LangGraph node ----------

def git_node(state: SessionState) -> dict:
    """Graph-internal stub. Real ship logic is invoked explicitly via
    session_store.ship_session() so the user always sees the PR before it
    opens. Auto-running this node from the graph would violate that."""
    return {"status": "git_ready_for_ship"}
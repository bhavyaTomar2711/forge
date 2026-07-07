"""GitHub REST API helpers.

Phase 5 part 1: list the user's repos + clone one with the OAuth token
(so private repos work, not just public). Part 2 (Git Agent) adds
branch/commit/push/PR helpers on top of this module.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import requests

_API_BASE = "https://api.github.com"


def _git_bin() -> str:
    return shutil.which("git") or r"C:\Program Files\Git\cmd\git.exe"


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}


def _safe_gh_error(e: requests.RequestException, token: str) -> str:
    """Drop the token from any raised error text before it reaches a log/UI."""
    return str(e).replace(token, "***")


def list_repos(token: str, *, per_page: int = 50) -> list[dict]:
    """Repos the authenticated user owns or collaborates on, most recently
    pushed first."""
    resp = requests.get(
        f"{_API_BASE}/user/repos",
        headers=_headers(token),
        params={"per_page": per_page, "sort": "pushed", "affiliation": "owner,collaborator"},
        timeout=15,
    )
    try:
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(_safe_gh_error(e, token)) from e
    return [
        {
            "full_name": r["full_name"],
            "private": r["private"],
            "clone_url": r["clone_url"],
            "default_branch": r["default_branch"],
        }
        for r in resp.json()
    ]


def clone_repo(token: str, clone_url: str, dest_dir: str | Path) -> None:
    """Clones over HTTPS using the token for auth. Never logs the
    authenticated URL or includes the token in any raised error text."""
    authed_url = clone_url.replace("https://", f"https://x-access-token:{token}@", 1)
    proc = subprocess.run(
        [_git_bin(), "clone", "--depth", "1", authed_url, str(dest_dir)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        safe_err = proc.stderr.replace(token, "***")
        raise RuntimeError(f"git clone failed: {safe_err}")


# ---------- branch / commit / push / PR ----------

def create_branch(token: str, repo_full_name: str, new_branch: str,
                  *, from_branch: str | None = None) -> dict:
    """Create a new branch off the default (or specified) branch, using the
    GitHub API. Returns the branch's API response."""
    if not from_branch:
        # fetch the default branch via repo metadata
        resp = requests.get(f"{_API_BASE}/repos/{repo_full_name}",
                            headers=_headers(token), timeout=15)
        try:
            resp.raise_for_status()
        except requests.RequestException as e:
            raise RuntimeError(_safe_gh_error(e, token)) from e
        from_branch = resp.json()["default_branch"]

    # get the head sha of the source branch
    resp = requests.get(
        f"{_API_BASE}/repos/{repo_full_name}/git/ref/heads/{from_branch}",
        headers=_headers(token), timeout=15,
    )
    try:
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(_safe_gh_error(e, token)) from e
    sha = resp.json()["object"]["sha"]

    # create new ref pointing at that sha
    resp = requests.post(
        f"{_API_BASE}/repos/{repo_full_name}/git/refs",
        headers=_headers(token),
        json={"ref": f"refs/heads/{new_branch}", "sha": sha},
        timeout=15,
    )
    try:
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"create branch failed: {_safe_gh_error(e, token)}") from e
    return resp.json()


def open_pull_request(token: str, repo_full_name: str, *, head: str, base: str,
                      title: str, body: str) -> dict:
    resp = requests.post(
        f"{_API_BASE}/repos/{repo_full_name}/pulls",
        headers=_headers(token),
        json={"title": title, "body": body, "head": head, "base": base},
        timeout=20,
    )
    try:
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"open PR failed: {_safe_gh_error(e, token)}") from e
    return resp.json()

"""Terminal Agent.

Phase 2: runs npm install + (optional) lint + build inside the docker container.
Returns build_passed and full output. Retry loop is wired in graph.py.
"""
from __future__ import annotations

from graph.state import SessionState
from docker import manager as docker_mgr

_MAX_OUTPUT_CHARS = 12_000


def _truncate(s: str) -> str:
    if len(s) <= _MAX_OUTPUT_CHARS:
        return s
    head = s[:_MAX_OUTPUT_CHARS // 2]
    tail = s[-(_MAX_OUTPUT_CHARS // 2):]
    return f"{head}\n... [truncated {len(s) - _MAX_OUTPUT_CHARS} chars] ...\n{tail}"


def _header(title: str) -> str:
    bar = "=" * 60
    return f"\n{bar}\n{title}\n{bar}\n"


def terminal_node(state: SessionState) -> dict:
    session_id = state.get("session_id", "")
    repo_path = state.get("repo_path", "")
    if not session_id or not repo_path:
        return {
            "build_output": "terminal skipped: missing session_id or repo_path",
            "build_passed": False,
            "status": "terminal_no_session",
        }

    if docker_mgr.get_session(session_id) is None:
        try:
            docker_mgr.start_session(session_id, repo_path)
        except Exception as e:
            return {
                "build_output": f"failed to start docker container: {e}",
                "build_passed": False,
                "status": "terminal_docker_failed",
            }

    out_parts: list[str] = []
    overall_pass = True

    # step 1: install
    install_cmd = (
        "if [ ! -d node_modules ]; then npm install --no-audit --no-fund --prefer-offline; "
        "else echo 'node_modules already present, skipping install'; fi"
    )
    out_parts.append(_header("STEP 1: npm install"))
    code, stdout, stderr = docker_mgr.run_command(session_id, install_cmd, timeout_s=600)
    out_parts.append(f"$ {install_cmd}")
    out_parts.append(f"exit={code}")
    if stdout:
        out_parts.append(stdout)
    if stderr:
        out_parts.append("[stderr]\n" + stderr)
    if code != 0:
        overall_pass = False

    # step 2: lint (only if `lint` script exists)
    if overall_pass:
        has_lint = docker_mgr.run_command_quiet(
            session_id,
            "node -e \"const p=require('./package.json'); process.exit(p.scripts&&p.scripts.lint?0:1)\"",
            timeout_s=15,
        ) == 0
        if has_lint:
            out_parts.append(_header("STEP 2: npm run lint"))
            lint_cmd = "npm run lint --silent"
            code, stdout, stderr = docker_mgr.run_command(session_id, lint_cmd, timeout_s=300)
            out_parts.append(f"$ {lint_cmd}")
            out_parts.append(f"exit={code}")
            if stdout:
                out_parts.append(stdout)
            if stderr:
                out_parts.append("[stderr]\n" + stderr)
            if code != 0:
                overall_pass = False
        else:
            out_parts.append(_header("STEP 2: npm run lint (skipped: no lint script)"))

    # step 3: build
    if overall_pass:
        out_parts.append(_header("STEP 3: npm run build"))
        build_cmd = "npm run build --silent"
        code, stdout, stderr = docker_mgr.run_command(session_id, build_cmd, timeout_s=600)
        out_parts.append(f"$ {build_cmd}")
        out_parts.append(f"exit={code}")
        if stdout:
            out_parts.append(stdout)
        if stderr:
            out_parts.append("[stderr]\n" + stderr)
        if code != 0:
            overall_pass = False

    combined = _truncate("\n".join(out_parts))
    return {
        "build_output": combined,
        "build_passed": overall_pass,
        "status": "terminal_passed" if overall_pass else "terminal_failed",
    }

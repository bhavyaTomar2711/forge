"""Container manager for forge.

One container per session, repo mounted as a bind volume so file edits from
the host (the Coding Agent writes directly) are visible inside the container
for the build pipeline. Per FORGE_BRAIN.md Docker Setup.

Lifecycle:
  start_session(session_id, repo_path) -> container_id
  run_command(session_id, cmd)         -> (exit_code, stdout, stderr)
  stop_session(session_id)             -> None

State is in-memory keyed by session_id. Cleanup on idle is phase 4+ work.
"""
from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path


def _resolve_docker_bin() -> str:
    explicit = os.getenv("FORGE_DOCKER_BIN")
    if explicit and Path(explicit).exists():
        return explicit
    on_path = shutil.which("docker")
    if on_path:
        return on_path
    candidates = [
        r"C:\Program Files\Docker\Docker\resources\bin\docker.exe",
        r"C:\Program Files\Docker\Docker\docker.exe",
    ]
    for c in candidates:
        if Path(c).exists():
            return c
    return "docker"


DOCKER_BIN = _resolve_docker_bin()
DEFAULT_IMAGE = os.getenv("FORGE_DOCKER_IMAGE", "forge-runner:latest")
DEFAULT_TIMEOUT_S = 600


@dataclass
class _Session:
    container_id: str
    repo_path: str
    workdir: str = "/workspace"
    image: str = DEFAULT_IMAGE


_sessions: dict[str, _Session] = {}
_lock = threading.Lock()


def _run_docker(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    """Run a docker CLI command. Forces utf-8 decode so npm output (which can
    contain chars outside cp1252) doesn't crash on Windows."""
    proc = subprocess.run([DOCKER_BIN, *args], capture_output=True, timeout=timeout)
    out = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    err = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
    proc.stdout = out
    proc.stderr = err
    return proc


# ---------- image build ----------

def build_image(tag: str = DEFAULT_IMAGE, dockerfile_dir: str | Path | None = None,
                verbose: bool = False) -> tuple[int, str, str]:
    if dockerfile_dir is None:
        dockerfile_dir = Path(__file__).resolve().parent
    proc = _run_docker([
        "build",
        "-t", tag,
        "-f", str(Path(dockerfile_dir) / "Dockerfile"),
        str(dockerfile_dir),
    ], timeout=900)
    if verbose or proc.returncode != 0:
        return proc.returncode, proc.stdout, proc.stderr
    return proc.returncode, "", ""


def image_exists(tag: str = DEFAULT_IMAGE) -> bool:
    proc = _run_docker(["image", "inspect", tag], timeout=30)
    return proc.returncode == 0


# ---------- session lifecycle ----------

APP_PORT = 3000  # container-side port Next.js apps listen on (npm start / next start)

def start_session(session_id: str, repo_path: str | Path,
                  *, image: str = DEFAULT_IMAGE,
                  network: bool = True, publish_app_port: bool = True) -> str:
    repo = Path(repo_path).resolve()
    if not repo.exists() or not repo.is_dir():
        raise ValueError(f"repo path not found or not a directory: {repo}")

    mount_src = str(repo)

    flags = ["-d"]
    if not network:
        flags += ["--network=none"]
    if publish_app_port:
        # publish to an OS-assigned host port; resolved later via get_app_port()
        flags += ["-p", f"0:{APP_PORT}"]

    proc = _run_docker([
        "run", *flags,
        "-v", f"{mount_src}:/workspace",
        "-w", "/workspace",
        image,
        "sleep", "infinity",
    ], timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(
            f"docker run failed (exit {proc.returncode}):\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    container_id = proc.stdout.strip()
    if not container_id:
        raise RuntimeError(f"docker run returned empty container id. stderr: {proc.stderr}")

    with _lock:
        _sessions[session_id] = _Session(
            container_id=container_id, repo_path=str(repo), image=image,
        )
    return container_id


def stop_session(session_id: str) -> None:
    with _lock:
        s = _sessions.pop(session_id, None)
    if not s:
        return
    _run_docker(["rm", "-f", s.container_id], timeout=60)


def get_session(session_id: str) -> _Session | None:
    with _lock:
        return _sessions.get(session_id)


def list_sessions() -> list[str]:
    with _lock:
        return list(_sessions.keys())


def stop_all() -> None:
    """Stop every session this process started. Used by atexit + tests."""
    with _lock:
        sids = list(_sessions.keys())
    for sid in sids:
        try:
            stop_session(sid)
        except Exception:
            # best-effort cleanup; never raise from atexit
            pass


# On interpreter shutdown, kill any containers we still own so a crashing
# test script doesn't leave orphans. Phase 4 will replace this with a real
# idle-timeout sweeper.
atexit.register(stop_all)


# ---------- command execution ----------

def run_command(session_id: str, cmd: str, *, timeout_s: int = DEFAULT_TIMEOUT_S,
                workdir: str | None = None) -> tuple[int, str, str]:
    s = get_session(session_id)
    if not s:
        raise KeyError(f"no session: {session_id}")

    wd = workdir or s.workdir
    proc = _run_docker([
        "exec",
        "-w", wd,
        s.container_id,
        "bash", "-lc", cmd,
    ], timeout=timeout_s)
    return proc.returncode, proc.stdout, proc.stderr


def run_command_quiet(session_id: str, cmd: str, *,
                      timeout_s: int = DEFAULT_TIMEOUT_S) -> int:
    code, _, _ = run_command(session_id, cmd, timeout_s=timeout_s)
    return code


# ---------- app server (for QA agent) ----------

def start_server(session_id: str, cmd: str = "npm start", *,
                 workdir: str | None = None) -> None:
    """Start the app's server in the background inside the container.
    Fire-and-forget: stdout/stderr redirected to a log file in-container so
    the exec call returns immediately instead of blocking on the long-running
    process."""
    s = get_session(session_id)
    if not s:
        raise KeyError(f"no session: {session_id}")
    wd = workdir or s.workdir
    bg_cmd = f"nohup {cmd} > /tmp/forge_server.log 2>&1 &"
    _run_docker(["exec", "-w", wd, s.container_id, "bash", "-lc", bg_cmd], timeout=30)


def get_app_port(session_id: str, *, container_port: int = APP_PORT) -> int | None:
    """Resolve the OS-assigned host port mapped to the container's app port."""
    s = get_session(session_id)
    if not s:
        return None
    proc = _run_docker(["port", s.container_id, str(container_port)], timeout=15)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    # output like "0.0.0.0:54321\n[::]:54321"
    first_line = proc.stdout.strip().splitlines()[0]
    try:
        return int(first_line.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None

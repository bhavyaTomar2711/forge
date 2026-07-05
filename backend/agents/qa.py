"""QA Agent.

Phase 3 part 1: generic browser-level check. Launches the app the Terminal
Agent started (npm start, inside the docker container) and confirms via
Playwright that the page actually loads in a real browser -- not just that
the build compiled. Task-specific checks (phase 3 part 2) build on top of
this by adding assertions to `_run_checks`.
"""
from __future__ import annotations

import time
import urllib.error
import urllib.request

from graph.state import SessionState
from docker import manager as docker_mgr

_SERVER_READY_TIMEOUT_S = 30
_SERVER_READY_POLL_S = 1
_PAGE_LOAD_TIMEOUT_MS = 15_000


def _wait_for_server(url: str, timeout_s: int = _SERVER_READY_TIMEOUT_S) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=3)
            return True
        except urllib.error.HTTPError:
            # server answered (even with an error page) -- it's up
            return True
        except Exception:
            time.sleep(_SERVER_READY_POLL_S)
    return False


def _run_checks(url: str) -> dict:
    """Generic checks: page loads, no console errors, no uncaught exceptions.
    Returns a qa_result dict. Task-specific checks get added here in part 2."""
    from playwright.sync_api import sync_playwright

    console_errors: list[str] = []
    page_errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.on("console", lambda msg: console_errors.append(msg.text)
                    if msg.type == "error" else None)
            page.on("pageerror", lambda exc: page_errors.append(str(exc)))

            response = page.goto(url, timeout=_PAGE_LOAD_TIMEOUT_MS, wait_until="load")
            status = response.status if response else None
            passed = bool(status and status < 400) and not page_errors

            return {
                "passed": passed,
                "url": url,
                "http_status": status,
                "console_errors": console_errors,
                "page_errors": page_errors,
            }
        finally:
            browser.close()


def qa_node(state: SessionState) -> dict:
    session_id = state.get("session_id", "")
    if not state.get("build_passed") or not session_id:
        return {
            "qa_result": {"passed": True, "skipped": True, "reason": "build not passed or no session"},
            "status": "qa_skipped",
        }

    port = docker_mgr.get_app_port(session_id)
    if not port:
        return {
            "qa_result": {"passed": True, "skipped": True, "reason": "no app port published"},
            "status": "qa_skipped_no_port",
        }

    url = f"http://localhost:{port}"
    if not _wait_for_server(url):
        return {
            "qa_result": {"passed": False, "url": url, "reason": "server did not become ready in time"},
            "status": "qa_failed",
        }

    try:
        qa_result = _run_checks(url)
    except Exception as e:
        return {
            "qa_result": {"passed": False, "url": url, "reason": f"qa check crashed: {e}"},
            "status": "qa_failed",
        }

    return {
        "qa_result": qa_result,
        "status": "qa_passed" if qa_result.get("passed") else "qa_failed",
    }

"""QA Agent.

Phase 3 part 1: generic browser-level check -- page loads, no console errors.
Phase 3 part 2: task-specific checks layered on top. The task text is pattern
-matched (same style as the Planner Agent's regex fallback) to decide which
extra assertions to run, e.g. dark mode -> find toggle, click it, confirm the
page actually changed appearance, reload, confirm it persisted.
"""
from __future__ import annotations

import re
import time
import urllib.error
import urllib.request

from graph.state import SessionState
from docker import manager as docker_mgr
from paths import session_dir

_SERVER_READY_TIMEOUT_S = 30
_SERVER_READY_POLL_S = 1
_PAGE_LOAD_TIMEOUT_MS = 15_000
_DARK_MODE_TASK_RE = re.compile(r"dark\s*mode|theme\s*toggle|light\s*/\s*dark", re.I)


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


def _find_toggle(page):
    """Heuristic locator for a dark-mode/theme toggle: any clickable element
    whose visible text or aria-label mentions dark/light/theme. Implementation
    details (class names, storage keys) are the Coding Agent's choice, so we
    don't assume any of that -- just find *something* clickable and themed."""
    candidates = [
        page.locator("button, [role=button], a").filter(has_text=re.compile(r"dark|light|theme", re.I)),
        page.locator("[aria-label*='dark' i], [aria-label*='theme' i], [aria-label*='light' i]"),
        page.locator("[data-testid*='theme' i], [data-testid*='dark' i]"),
    ]
    for loc in candidates:
        if loc.count() > 0:
            return loc.first
    return None


def _check_dark_mode(page) -> dict:
    """Task-specific check for a dark mode toggle: it must exist, clicking it
    must visibly change the page (background color), and a reload must keep
    the new state (persistence)."""
    toggle = _find_toggle(page)
    if toggle is None:
        return {"dark_mode_check": "failed", "reason": "no theme/dark-mode toggle found on page"}

    before = page.evaluate("getComputedStyle(document.body).backgroundColor")
    toggle.click()
    page.wait_for_timeout(300)
    after = page.evaluate("getComputedStyle(document.body).backgroundColor")
    if after == before:
        return {"dark_mode_check": "failed", "reason": "clicking toggle did not change page appearance"}

    page.reload(wait_until="load")
    page.wait_for_timeout(300)
    persisted = page.evaluate("getComputedStyle(document.body).backgroundColor")
    if persisted != after:
        return {"dark_mode_check": "failed", "reason": "theme did not persist across reload",
                "before": before, "after_click": after, "after_reload": persisted}

    return {"dark_mode_check": "passed", "before": before, "after_click": after}


def _take_screenshot(page, session_id: str) -> str | None:
    """Live preview (phase 4 part 2): a screenshot of current app state, so
    a user can see progress without a real frontend yet."""
    try:
        path = session_dir(session_id) / "preview.png"
        page.screenshot(path=str(path), full_page=False)
        return str(path)
    except Exception:
        return None


def _run_checks(url: str, task: str = "", session_id: str = "") -> dict:
    """Generic checks (page loads, no console errors) plus any task-specific
    assertions matched from the task text."""
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

            task_check = None
            if passed and _DARK_MODE_TASK_RE.search(task or ""):
                task_check = _check_dark_mode(page)
                if task_check.get("dark_mode_check") != "passed":
                    passed = False

            screenshot_path = _take_screenshot(page, session_id) if session_id else None

            result = {
                "passed": passed,
                "url": url,
                "http_status": status,
                "console_errors": console_errors,
                "page_errors": page_errors,
            }
            if task_check:
                result["task_check"] = task_check
            if screenshot_path:
                result["screenshot_path"] = screenshot_path
            return result
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
        qa_result = _run_checks(url, state.get("task", ""), session_id)
    except Exception as e:
        return {
            "qa_result": {"passed": False, "url": url, "reason": f"qa check crashed: {e}"},
            "status": "qa_failed",
        }

    return {
        "qa_result": qa_result,
        "preview_url": url,
        "preview_screenshot": qa_result.get("screenshot_path", ""),
        "status": "qa_passed" if qa_result.get("passed") else "qa_failed",
    }

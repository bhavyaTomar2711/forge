"""Coding Agent.

One job: given the user task + the relevant files surfaced by the Repository
Agent, produce full-file edits and write them to disk.

Hard rules (FORGE_BRAIN.md):
  - only touch files the Repository Agent flagged. never invent new paths
    that weren't in `relevant_files` (except for genuine new files the LLM
    says it needs to create -- we still gate on the LLM justifying it).
  - generate FULL file contents, not diffs/patches. easier to verify, easier
    to apply. (per FORGE_BRAIN.md "File Editing Mechanism".)
  - snapshot the original file content in state before writing, so the UI
    can compute and show a diff. the diff is for display ONLY.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from graph.state import SessionState
from llm import chat_text

# Cap on how many chars of context to send per file. Tight on purpose --
# Groq's free tier caps at 12K TPM, so a too-large request returns 413
# and the agent silently does nothing. Production tier can bump these.
_PER_FILE_CONTEXT_CHARS = 2_500
_MAX_NEW_FILE_CHARS = 20_000
_MAX_FILES_IN_PROMPT = 8


_SYSTEM_PROMPT = """\
You are the Coding agent inside Forge, an autonomous AI software engineer.

You receive:
  1. A TASK (natural language feature request)
  2. A PLAN (ordered engineering steps)
  3. RELEVANT FILES (relative path + current content, may be truncated)

Your job: produce the minimum set of FULL FILE CONTENTS needed to implement
the task. You will return a SINGLE JSON OBJECT with this exact shape:

  {
    "edits":     { "path/to/file.ext": "<COMPLETE file content>", ... },
    "new_files": { "path/to/new.ext": "<COMPLETE file content>", ... },
    "reason":    "<optional one-line note>"
  }

CRITICAL FORMATTING RULES (the response must parse as valid JSON):
  - The value of every file entry is a JSON STRING. Inside that string you
    MUST escape any literal double-quote as \\" and any newline as \\n.
    Do NOT emit raw unescaped double-quotes inside string values.
  - "edits"     = files that EXIST in the relevant files list and need to change
  - "new_files" = files that do NOT exist yet
  - EVERY value in "edits" and "new_files" must be the COMPLETE file content
    from first line to last. NO diffs, NO patches, NO "// ...rest unchanged..."
    placeholders.
  - Keys are RELATIVE paths exactly as given in the relevant files list
    (or a sensible new path for "new_files").
  - Do not include any file path under "edits" that was not in the relevant
    files list.
  - Keep changes minimal. Preserve the project's existing style, imports,
    naming, and TypeScript/TSX conventions.
  - If the task is impossible or already done, return:
    {"edits":{}, "new_files":{}, "reason":"<why>"}

Output format:
  - Pure JSON. No markdown fences, no commentary, no preamble, no postscript.
  - The response must be valid JSON that parses in one call.
"""


# ---------- prompt assembly ----------

def _format_files_block(relevant_files: dict[str, str]) -> str:
    if not relevant_files:
        return "(no relevant files provided)"
    # only send the top N most-relevant files
    items = list(relevant_files.items())[:_MAX_FILES_IN_PROMPT]
    chunks: list[str] = []
    for path, content in items:
        if len(content) > _PER_FILE_CONTEXT_CHARS:
            content = content[:_PER_FILE_CONTEXT_CHARS] + "\n... [truncated]"
        chunks.append(f"### FILE: {path}\n```\n{content}\n```")
    return "\n\n".join(chunks)


def _build_user_prompt(task: str, plan: list[str], relevant_files: dict[str, str],
                       error_context: str = "") -> str:
    parts = [
        f"TASK: {task}",
        "",
        "PLAN:",
        "\n".join(f"{i+1}. {s}" for i, s in enumerate(plan)) if plan else "(no plan)",
        "",
        "RELEVANT FILES:",
        _format_files_block(relevant_files),
    ]
    if error_context:
        parts.extend([
            "",
            "PREVIOUS ATTEMPT FAILED:",
            error_context,
            "Apply a fix.",
        ])
    return "\n".join(parts)


# ---------- response parsing ----------

def _parse_response(raw: str) -> dict | None:
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    start = s.find("{")
    end = s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    candidate = s[start:end + 1]
    data = None
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        data = _recover_from_broken_quotes(candidate)
    if not isinstance(data, dict):
        return None
    edits = data.get("edits", {}) or {}
    new_files = data.get("new_files", {}) or {}
    if not isinstance(edits, dict) or not isinstance(new_files, dict):
        return None
    cleaned_edits = {str(k): str(v) for k, v in edits.items() if isinstance(v, (str, int, float))}
    cleaned_new = {str(k): str(v) for k, v in new_files.items() if isinstance(v, (str, int, float))}
    return {"edits": cleaned_edits, "new_files": cleaned_new, "reason": data.get("reason")}


def _recover_from_broken_quotes(candidate: str) -> dict | None:
    out: dict = {}
    for key in ("edits", "new_files"):
        m = re.search(rf'"{key}"\s*:\s*\{{', candidate)
        if not m:
            continue
        i = m.end()
        depth = 1
        end_i = i
        while end_i < len(candidate) and depth > 0:
            ch = candidate[end_i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            end_i += 1
        if depth != 0:
            continue
        out[key] = _parse_string_dict(candidate[i:end_i])
    if "reason" in candidate:
        rm = re.search(r'"reason"\s*:\s*"((?:[^"\\]|\\.)*)"', candidate)
        if rm:
            try:
                out["reason"] = json.loads('"' + rm.group(1) + '"')
            except json.JSONDecodeError:
                out["reason"] = rm.group(1)
    return out


def _parse_string_dict(body: str) -> dict[str, str]:
    result: dict[str, str] = {}
    key_matches = list(re.finditer(r'"([^"\\]*(?:\\.[^"\\]*)*)"\s*:\s*"', body))
    for idx, km in enumerate(key_matches):
        key = km.group(1)
        val_start = km.end()
        j = val_start
        last_good_end = -1
        while j < len(body):
            c = body[j]
            if c == "\\" and j + 1 < len(body):
                j += 2
                continue
            if c == '"':
                k = j + 1
                while k < len(body) and body[k] in " \t\r\n":
                    k += 1
                if k < len(body) and body[k] in ",}":
                    last_good_end = j
                    break
            j += 1
        if last_good_end == -1:
            continue
        raw_val = body[val_start:last_good_end]
        try:
            fixed = _escape_bare_quotes(raw_val)
            decoded = json.loads(fixed)
        except json.JSONDecodeError:
            decoded = raw_val
        result[key] = decoded
    return result


def _escape_bare_quotes(s: str) -> str:
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            out.append(c)
            out.append(s[i + 1])
            i += 2
            continue
        if c == '"':
            out.append('\\"')
            i += 1
            continue
        out.append(c)
        i += 1
    return '"' + "".join(out) + '"'


# ---------- scoping + diff ----------

def _scope_check(edits: dict[str, str], new_files: dict[str, str],
                 allowed: set[str]) -> tuple[dict[str, str], dict[str, str], list[str]]:
    kept_e, kept_n, dropped = {}, {}, []
    for p, c in edits.items():
        if p in allowed:
            if len(c) > _MAX_NEW_FILE_CHARS:
                dropped.append(f"{p} (over size cap)")
                continue
            kept_e[p] = c
        else:
            dropped.append(p)
    for p, c in new_files.items():
        if len(c) > _MAX_NEW_FILE_CHARS:
            dropped.append(f"{p} (over size cap)")
            continue
        kept_n[p] = c
    return kept_e, kept_n, dropped


def _diff_summary(original: str, new: str) -> dict:
    a = original.splitlines()
    b = new.splitlines()
    return {
        "added": max(0, len(b) - len(a)),
        "removed": max(0, len(a) - len(b)),
        "lines_before": len(a),
        "lines_after": len(b),
    }


# ---------- public API ----------

def generate_edits(
    task: str,
    plan: list[str],
    relevant_files: dict[str, str],
    *,
    error_context: str = "",
) -> dict:
    user_prompt = _build_user_prompt(task, plan, relevant_files, error_context=error_context)
    raw = chat_text(_SYSTEM_PROMPT, user_prompt, max_tokens=4096)
    parsed = _parse_response(raw)
    if not parsed:
        raise ValueError("LLM response did not contain valid JSON edits")
    allowed = set(relevant_files.keys())
    edits, new_files, dropped = _scope_check(parsed["edits"], parsed["new_files"], allowed)
    return {
        "edits": edits,
        "new_files": new_files,
        "dropped": dropped,
        "reason": parsed.get("reason"),
    }


# ---------- file writing ----------

def apply_edits(repo_path: str | Path, edits: dict[str, str],
                new_files: dict[str, str]) -> tuple[dict[str, str], list[dict]]:
    repo = Path(repo_path).resolve()
    originals: dict[str, str] = {}
    diffs: list[dict] = []

    def _write(rel: str, content: str, is_new: bool) -> None:
        target = repo / rel
        if not str(target.resolve()).startswith(str(repo)):
            diffs.append({"path": rel, "error": "path escapes repo root"})
            return
        try:
            if target.exists():
                originals[rel] = target.read_text(encoding="utf-8", errors="ignore")
            else:
                originals[rel] = ""
        except OSError as e:
            diffs.append({"path": rel, "error": f"read failed: {e}"})
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as e:
            diffs.append({"path": rel, "error": f"write failed: {e}"})
            return
        diffs.append({"path": rel, **(_diff_summary(originals[rel], content)),
                      "created": is_new and not originals[rel]})

    for rel, content in edits.items():
        _write(rel, content, is_new=False)
    for rel, content in new_files.items():
        _write(rel, content, is_new=True)

    return originals, diffs


# ---------- LangGraph node ----------

def coding_node(state: SessionState) -> dict:
    repo_path = state.get("repo_path", "")
    task = state.get("task", "")
    plan = state.get("plan") or []
    relevant = state.get("relevant_files") or {}
    error_ctx = state.get("build_output", "") if not state.get("build_passed") else ""

    if not repo_path or not relevant:
        return {
            "edits": state.get("edits", {}),
            "diff_summary": state.get("diff_summary", []),
            "status": "coding_no_inputs",
        }

    try:
        gen = generate_edits(task, plan, relevant, error_context=error_ctx)
    except Exception as e:
        return {
            "edits": state.get("edits", {}),
            "diff_summary": state.get("diff_summary", []) + [{"error": f"llm: {e}"}],
            "status": "coding_llm_failed",
        }

    dropped = gen.get("dropped", [])
    try:
        originals, diffs = apply_edits(repo_path, gen["edits"], gen["new_files"])
    except Exception as e:
        return {
            "edits": state.get("edits", {}),
            "diff_summary": state.get("diff_summary", []) + [{"error": f"apply: {e}"}],
            "status": "coding_write_failed",
        }

    all_written = {**gen["edits"], **gen["new_files"]}
    return {
        "edits": all_written,
        "diff_summary": state.get("diff_summary", []) + diffs,
        "status": "coding_done" if all_written else "coding_no_changes",
        "_originals": originals,
        "_dropped_by_scope": dropped,
        "_llm_reason": gen.get("reason"),
    }

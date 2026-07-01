# Forge — Autonomous AI Software Engineer

## What This Is

Forge is an AI agent that takes a natural language feature request, understands a Next.js codebase, plans the implementation, edits the code, verifies it works (build + lint + automated browser testing), and opens a GitHub Pull Request. It behaves like a junior engineer working inside a real dev team, not a code-snippet generator.

Scope for v1: Next.js + TypeScript + Tailwind + shadcn/ui repos only. No multi-framework support, no Qdrant, no token/cost tracking. Personal demo project, not a deployed product — single user (me), no auth security hardening needed beyond basic GitHub OAuth for PR creation.

## Core Principle

Every agent has one job. No agent does another agent's work. State is passed explicitly between agents through a shared session object, not hidden in prompts.

## Agents

1. **Planner Agent** — takes the user's task, breaks it into ordered steps, decides what needs to happen and in what order. Output: a step list.
2. **Repository Agent** — given a step, searches the cloned repo (file tree + grep/keyword search, no vector DB) and returns the relevant files and their content.
3. **Coding Agent** — given relevant files + the step, edits or creates files. Touches only what's necessary.
4. **Terminal Agent** — runs `npm install`, `npm run lint`, `npm run build` inside the Docker container. Captures stdout/stderr.
5. **QA Agent** — uses Playwright to launch the app, perform the relevant UI check (e.g. click a toggle, confirm visual state, check console errors), and report pass/fail.
6. **Git Agent** — creates a branch, commits, pushes, opens a PR with a generated description, once the user approves.

## High-Level Flow

```
User logs in with GitHub
        │
Select + clone repository (into Docker volume)
        │
Analyze codebase (framework, package manager, folder structure — quick static scan)
        │
User gives a task ("Add dark mode with a toggle in the navbar")
        │
Planner Agent → step list
        │
Repository Agent → relevant files per step
        │
Coding Agent → edits files
        │
Terminal Agent → npm install / lint / build (inside Docker)
        │
   Build failed? → analyze error → Coding Agent fixes → retry (max N attempts)
   Build passed ↓
        │
QA Agent → Playwright verification of the actual feature
        │
   QA failed? → analyze → Coding Agent fixes → re-run Terminal + QA → retry (max N attempts)
   QA passed ↓
        │
Show live preview + diff to user
        │
User can continue the conversation ("move the toggle to Settings instead")
   → Repository Agent + Coding Agent update existing implementation (not from scratch)
   → Terminal + QA re-verify
        │
User approves
        │
Git Agent → branch → commit → push → PR with generated description
```

## Multi-Turn Session Behavior

Forge keeps a persistent session per repo + task thread. Every new user message is treated as an incremental instruction against the current state of the code, not a fresh task. Only when the user explicitly approves does the Git Agent run.

## Hard Rules (non-negotiable, prevents broken demos)

- All `npm install` / `build` / `test` execution happens **inside a Docker container**, never on host.
- Retry loops have a **hard max attempt limit** (suggest 3). On exceeding it, the agent must explicitly tell the user "I couldn't fix this after N attempts, here's what I tried and where it's stuck" — never loop silently or claim success without QA passing.
- Coding Agent must only touch files identified as relevant by the Repository Agent. No unrelated file edits.
- No PR is created without explicit user approval.

## Tech Stack

**Frontend:** Next.js, TypeScript, Tailwind CSS, shadcn/ui
**Backend:** FastAPI, LangGraph (agent orchestration), GitPython, Playwright
**AI:** Groq API
**Storage:** In-memory session store (Python dict keyed by session_id) for active sessions; session history persisted as JSON files on disk under `backend/sessions/{session_id}.json` for resuming past sessions after restart
**Execution:** Docker (sandboxed repo clone, install, build, test)
**Integration:** GitHub OAuth, GitHub REST API

## UI Design — Single Page Application

Three-panel layout, all visible at once on desktop (collapsible to tabs on smaller screens). No page navigation — everything happens in this one view.

**Left Panel — Sessions**
- GitHub repo picker (list of repos from OAuth, searchable)
- Current session indicator (repo name + branch + status: planning / editing / building / testing / awaiting approval)
- "New Session" button — starts a fresh task on the selected repo
- Session history list (past sessions, click to resume/reopen)

**Center Panel — Chat + Agent Activity**
- Chat thread: user messages + agent responses, in order
- Live agent progress indicator — shows which agent is currently active (Planner → Repository → Coding → Terminal → QA → Git), with a simple status icon per agent (pending / running / done / failed)
- Collapsible "Plan" block — shows the Planner Agent's step list for the current task, checkmarks as steps complete
- Collapsible "Logs" block — raw terminal/build/Playwright output, for debugging and for the "trust me, it's really doing this" factor in a demo
- Prompt input at the bottom — always available, sends a new instruction into the current session (this is what powers the multi-turn flow). Supports attaching images (drag-drop, paste, or file picker) alongside text — e.g. a screenshot of a bug, a design reference, or a mockup of the desired UI. Attached images show as thumbnails above the input before sending, removable before submit.
- "Approve & Create PR" button — appears only once a task has passed QA, sits near the prompt input

**Right Panel — Live Preview**
- Embedded preview of the running app (iframe pointing at the Docker container's exposed port, or a Playwright screenshot if iframe isn't feasible)
- Refresh button — re-pulls the latest preview state after an edit
- "Open in new tab" — opens the live preview directly for full interaction, not just the iframe view
- Small status strip above the preview: build status (passing/failing) + QA status (passed/failed/running)

**Why this layout works for a demo:** the center panel is where the "wow" happens — watching the agent think, plan, and execute in real time, with logs available if someone wants proof it's not faked. The right panel makes the result tangible immediately instead of requiring the viewer to take your word for it. The left panel is minimal on purpose — it's not the focus, just navigation.

**Suggested implementation notes:**
- Use Server-Sent Events or WebSockets to stream agent status + logs into the center panel in real time, not polling
- Keep the Plan and Logs sections collapsed by default, expandable — don't overwhelm the chat view
- The "Approve & Create PR" button should be disabled/hidden until QA Agent reports a pass, to enforce the hard rule that nothing ships without verification

## Technical Implementation Details

These fill in the "how exactly" gaps so the build doesn't stall on undefined decisions.

### LangGraph State Graph Structure

Single shared state object passed through all nodes. Suggested shape:

```python
class SessionState(TypedDict):
    repo_path: str              # local path to cloned repo inside Docker
    task: str                   # current user instruction
    attachments: list[str]      # paths/URLs to uploaded images for the current message, if any
    conversation: list          # full message history for this session, each message may include attachments
    plan: list[str]             # Planner Agent output
    relevant_files: dict        # filename -> content, from Repository Agent
    edits: dict                 # filename -> new content, from Coding Agent
    build_output: str           # last Terminal Agent run output
    build_passed: bool
    qa_result: dict             # pass/fail + details from Playwright checks
    retry_count: int
    status: str                 # planning | editing | building | testing | awaiting_approval | failed
```

Nodes: `planner -> repository -> coding -> terminal -> [conditional: retry to coding OR continue to qa] -> qa -> [conditional: retry to coding OR continue to preview] -> await_user`. Use LangGraph's conditional edges for the retry branches, keyed off `build_passed` / `qa_result` and `retry_count < MAX_RETRIES`.

### Image/Media Input Handling

Users can attach images to any prompt — a screenshot of a bug, a design mockup, a reference for "make it look like this." Handling:

- Frontend uploads the image to backend storage (local disk or S3-compatible bucket is fine for a demo project — local disk under `backend/uploads/{session_id}/` is simplest)
- Backend stores the file path/URL in the message record and passes it into `SessionState.attachments` for that turn
- **Planner Agent must use a vision-capable model call** when attachments are present (Groq supports vision models like Llama 4 Scout/Maverick — confirm current model availability at build time) to interpret the image alongside the text instruction before producing the step list
- Common use cases to design for: (1) bug screenshot → Planner extracts what's visually wrong and includes it as context for Repository/Coding agents, (2) design reference image → Planner describes the target visual outcome in the plan so Coding Agent has something concrete to implement against
- QA Agent can optionally compare its own Playwright screenshot against a user-provided reference image for visual tasks, though pixel-perfect comparison is out of scope for v1 — a text description comparison (via vision model) is good enough
- Attachments persist in conversation history so they remain visible/referenceable in later turns of the same session

### Docker Setup

- Base image: `node:20-bullseye` (or match the target repo's Node version if specified in `package.json`/`.nvmrc`)
- One container per session, repo cloned into a mounted volume so file edits from the host-side Coding Agent are immediately visible inside the container for builds
- Container exposes a port (e.g. 3000) for `npm run dev`, mapped to a host port per session so the preview iframe/Playwright can reach it
- Tear down container when session ends or after an idle timeout, to avoid orphaned containers piling up during dev

### File Editing Mechanism

Coding Agent should generate **full file contents for the files it touches**, not diffs/patches — simpler to implement reliably and easier to verify against the original. Before writing, snapshot the original file content in session state so the diff can still be shown to the user in the UI (compute the diff for *display* purposes only, not as the edit mechanism itself).

### API Contract (FastAPI backend, consumed by Next.js frontend)

- `POST /sessions` — create new session (repo, initial task) → returns session_id
- `POST /sessions/{id}/message` — send a new instruction into an existing session, accepts multipart form data so text + image attachments can be sent together (or `attachment_urls` if uploaded separately first)
- `POST /sessions/{id}/upload` — upload an image, returns a URL/path to reference in the next `/message` call (use this if uploading before composing the message, e.g. paste-to-upload in the UI)
- `GET /sessions/{id}/stream` — SSE/WebSocket endpoint streaming agent status, logs, and plan updates in real time
- `GET /sessions/{id}` — fetch full session state (for resuming/reloading)
- `POST /sessions/{id}/approve` — triggers Git Agent to create the PR
- `GET /sessions` — list past sessions for the left panel

### Secrets/Config Needed

- `GROQ_API_KEY`
- `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` (OAuth app)
- Keep these in a `.env`, never commit — standard practice but worth stating explicitly since this is a from-scratch build

### Repo Folder Structure (suggested)

```
forge/
  backend/
    agents/        # planner.py, repository.py, coding.py, terminal.py, qa.py, git.py
    graph.py        # LangGraph wiring
    docker/          # Dockerfile + container management logic
    api/             # FastAPI routes
    models.py        # session/message data classes (in-memory + JSON file persistence)
  frontend/
    app/             # Next.js app router pages
    components/      # ChatPanel, SessionPanel, PreviewPanel
  FORGE_BRAIN.md
```

## Build Phases

### Phase 1 — Core Agent Loop (no Docker, no UI, no GitHub)
Goal: prove the riskiest part works — can agents reliably understand a repo and edit it correctly.

- [ ] Set up LangGraph project skeleton using the SessionState shape and node structure defined in Technical Implementation Details
- [ ] Repository Agent: implement file-tree walk + keyword/grep search (no vector DB)
- [ ] Planner Agent: prompt + logic to break a feature request into ordered steps
- [ ] Coding Agent: prompt + file write logic, scoped to only files Repository Agent flagged
- [ ] Test end-to-end on a local clone of your own repo (basis or Athlocode) with 2-3 tasks (e.g. "add dark mode", "add a loading spinner to X page")
- [ ] Print/log every step's output for manual inspection — no automated verification yet

Exit criteria: agent can take a plain-English task and correctly edit the right files in a real repo, observed manually, for at least 2 different task types.

### Phase 2 — Verification Loop (Terminal Agent + Docker + Retry)
Goal: agent verifies its own work and self-corrects instead of just guessing.

- [ ] Build a Docker image with Node, npm, and the target repo's dependencies installable inside it
- [ ] Terminal Agent: run `npm install`, `npm run lint`, `npm run build` inside the container, capture output
- [ ] Implement retry loop: on build/lint failure, feed error back to Coding Agent, re-attempt, max 3 tries
- [ ] Implement graceful failure message when retries are exhausted
- [ ] Re-run Phase 1 test tasks through this full loop

Exit criteria: agent reliably gets a build passing (or correctly reports failure) for at least 3 different tasks, fully inside Docker.

### Phase 3 — QA Agent (Playwright)
Goal: agent confirms the feature actually works in the browser, not just that it compiles.

- [ ] Set up Playwright to launch the built app inside/alongside the Docker container
- [ ] QA Agent: write task-specific checks (e.g. for dark mode — toggle exists, click changes theme, refresh persists theme, no console errors)
- [ ] Wire QA failure back into the same retry loop as Phase 2 (fix → rebuild → re-test)
- [ ] Test on dark-mode-toggle task end-to-end: prompt in, working verified feature out

Exit criteria: at least one full task (e.g. dark mode) goes from prompt to Playwright-verified working feature, fully automated.

### Phase 4 — Multi-Turn Conversation + Live Preview
Goal: Forge behaves like a real session, not a one-shot script.

- [ ] Persist session state (task history, current code state) to disk as JSON, keyed by session_id
- [ ] Allow follow-up instructions to modify the existing implementation instead of restarting
- [ ] Add image attachment support: upload endpoint, attachment storage, and Planner Agent vision-model call when an attachment is present (test with a real bug screenshot or design reference)
- [ ] Build minimal preview output (screenshot from Playwright or simple local server link) to show the user current state
- [ ] Test a multi-turn flow: "add dark mode" → "move toggle to navbar" → "persist theme" → "looks good"

Exit criteria: a 3+ turn conversation correctly and incrementally modifies the same feature without re-doing prior work.

### Phase 5 — GitHub Integration
Goal: turn an approved session into a real PR.

- [ ] GitHub OAuth login
- [ ] Repository selection + clone via GitHub API
- [ ] Git Agent: create branch, commit changes, push, open PR
- [ ] Auto-generate PR description summarizing what changed and why
- [ ] Wire "user approves" trigger from the conversation into this flow

Exit criteria: full loop works — login, pick repo, give task, iterate, approve, get a real PR opened on GitHub.

### Phase 6 — UI + Demo Polish
Goal: make it presentable for interviews/recordings, not just functional in a terminal.

- [ ] Build the three-panel SPA per the UI Design section above (Sessions left, Chat + Agent Activity center, Live Preview right)
- [ ] Record 2-3 clean Loom demos (e.g. dark mode end-to-end, a bug fix end-to-end)
- [ ] Write up the resume-worthy summary + architecture diagram for portfolio/GitHub README

Exit criteria: a stranger watching the Loom demo understands what Forge does and is impressed within 60 seconds.

## Resume-Worthy Summary (use as-is or adapt)

Forge is an autonomous AI software engineering agent that turns natural language feature requests into production-ready GitHub Pull Requests. It clones and analyzes a repository, plans the implementation across a 6-agent pipeline (Planner, Repository, Coding, Terminal, QA, Git), executes all code changes and builds inside a sandboxed Docker environment, verifies the feature works using Playwright-driven automated browser testing, supports multi-turn iterative sessions, and opens a reviewed, ready-to-merge Pull Request on approval. Built with LangGraph for agent orchestration, FastAPI for backend services, Docker for isolated execution, and the GitHub API for repository automation.

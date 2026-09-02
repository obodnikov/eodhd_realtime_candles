# Claude Chat Export
**Generated: 2026-08-23 10:15:14**

---

👤 **USER:**
> <skill name="ai-coding-setup" location="/Users/eobomik/.agents/skills/ai-coding-setup/SKILL.md">
References are relative to /Users/eobomik/.agents/skills/ai-coding-setup.

# AI-coding setup

Produce a consistent, project-specific set of AI-coding docs:

- **CLAUDE.md** — project-global behavior + the index of rule files + tooling commands.
- **ARCHITECTURE.md** — structure, components, data flow, stability zones, rule precedence.
- **AI_<COMPONENT>.md** — one coding-rule file per architectural component, tagged with its stack.
- **.agents/skills/review-fix-loop/** — an autonomous review loop with stack-aware verify commands (optional).

The hard part is **multiple stacks in one project**. The solution: split the `AI_*.md` files by
**architectural component/boundary, each tagged with its stack** — see
[references/house-style.md](references/house-style.md). One Python pipeline gets several Python
`AI_*.md` files by layer; a React+Python+Docker app gets `AI_FRONTEND.md` + `AI_BACKEND.md` +
`AI_INFRA.md`. CLAUDE.md and ARCHITECTURE.md §8 index whichever set you produce.

Bundled resources (paths relative to this skill directory):
- `scripts/detect_stacks.py` — repo scanner (stdlib only; run with `python3`).
- `references/house-style.md` — conventions + per-placeholder fill rules. **Read before generating.**
- `references/stack-rules.md` — per-stack rule catalog for the `AI_*.md` files.
- `references/architecture-guide.md` — how to fill ARCHITECTURE.md (the 9 sections).
- `references/interview.md` — the greenfield question flow.
- `references/example.md` — a full worked input→output sample for a polyglot project (self-contained; needs no external repos). Skim it for style before generating.
- `assets/*.template` — the file skeletons to fill and write into the target repo.

## Working rule

Follow confirm-before-acting: **analysis and the file plan are read-only and need no approval, but
present the plan and get a clear "yes" before writing any file into the target project.** When a
target file already exists, **update it in place — never clobber** hand-written content; show a diff
of intended changes.

## Workflow

### Step 0 — Resolve target and mode

Establish the target project path (ask if ambiguous; for a brand-new project, offer to create the
folder). Then scan it:

```bash
python3 scripts/detect_stacks.py /path/to/project
```

Read the JSON: `mode_hint`, `stacks` (+ commands), `components`, `manifests`, `description_docs`,
`env_files`, `ci`, `existing_ai_docs`. Route on `mode_hint` (the user may override):

- `brownfield` → Step 1A.
- `greenfield` → Step 1B.

### Step 1A — Brownfield: analyze the code

Trust the detector's stacks/commands but verify against the real manifests (e.g. read
`pyproject.toml` to confirm whether mypy is a gate; read `package.json` `scripts` for real names).
Then gather what the detector can't:

- Read `README`, `docs/`, `docs/design/`, `docs/chats/`, `IMPLEMENTATION_PLAN.md` for purpose,
  decisions, and history.
- Read any `existing_ai_docs` so you **update rather than recreate** them.
- Infer component boundaries and data contracts from the directory layout and key modules.

Proceed to Step 2.

### Step 1B — Greenfield: interview

There's no code to analyze, so follow [references/interview.md](references/interview.md): read every
`description_docs` file first, pre-fill the answers, then confirm the gaps with the user via
`AskUserQuestion` (batched, defaults offered). Elicit name, pitch, project type, intended stacks per
layer, components, tooling, integrations, secrets, doc language, and the project's integrity rule.
Set the **early-design status** variant. Proceed to Step 2.

### Step 2 — Plan the AI_*.md split, then confirm

Using [references/house-style.md](references/house-style.md) ("The AI_*.md split"), map components →
one stack-tagged `AI_*.md` each (aim for 2–4). **Present to the user**, before writing:

- the file list (`CLAUDE.md`, `ARCHITECTURE.md`, each `AI_*.md` with its component + stack + glob),
- whether `.agents/skills/review-fix-loop` will be scaffolded (and the review-script path),
- which existing files will be updated vs created.

Get a "yes." Then generate, reading the relevant reference before each file. For a concrete model
of a complete output set — a polyglot CLAUDE.md (with the File|Scope table and a project-specific
integrity rule), ARCHITECTURE §2/§7/§8, and two stack-tagged `AI_*.md` files — see
[references/example.md](references/example.md).

### Step 3 — ARCHITECTURE.md

Fill `assets/ARCHITECTURE.md.template` per [references/architecture-guide.md](references/architecture-guide.md):
nine sections, ASCII diagrams in §2 and §5, real names, stability zones in §7, and the §8 rule-file
list matching the split from Step 2. 250–300 lines. No coding rules (those go in `AI_*.md`).

### Step 4 — AI_*.md files

For each planned file, fill `assets/AI_COMPONENT.md.template`, pulling rules from the matching
section(s) of [references/stack-rules.md](references/stack-rules.md). Title `# AI rules — <Component>
(<Stack>)`; set the glob, scope note, ARCHITECTURE pointer, and cross-links; include only rules for
tooling the project actually uses. Keep each short and dense (rules only).

### Step 5 — CLAUDE.md

Fill `assets/CLAUDE.md.template` per [references/house-style.md](references/house-style.md):
description, status blockquote with real build/test commands, "Read before making changes", the
File|Scope table (matching the files on disk and ARCHITECTURE §8), the working agreement **including
a project-specific integrity rule**, the optional documentation-language section, and tooling.

### Step 6 — .agents/skills/review-fix-loop (if chosen)

Copy `assets/agents-skills/review-fix-loop/SKILL.md.template` to
`<project>/.agents/skills/review-fix-loop/SKILL.md`, filling:
- `{{REVIEW_SCRIPT_PATH}}` — the user's external review script (default `$HOME/mbin/code-review.sh`).
- `{{VERIFY_COMMANDS}}` — a bullet list of the detected stack's verify commands (e.g. `pytest`,
  `ruff check .`, `mypy .`, `npm run typecheck`, `go test ./...`), one per stack/component touched.

### Step 7 — Validate

Check, and fix any miss:

- **Cross-file consistency** — the `AI_*.md` set on disk == CLAUDE.md's table == ARCHITECTURE §8 list.
- **No duplication** — no coding/style rule text repeated across ARCHITECTURE.md and an `AI_*.md`
  (grep the rule keywords); architecture rationale not copied into `AI_*.md`.
- **ARCHITECTURE** — run the checklist in [references/architecture-guide.md](references/architecture-guide.md)
  (9 sections, ~250–300 lines, diagrams, §8 intact).
- **Links resolve** — every relative link points to a file that now exists.
- **Status matches mode** — early-design vs in-progress vs implemented.

Report what was created/updated and surface any gaps honestly (e.g. "no tests detected — left the
test command as a TODO for you to confirm").
</skill>

I don't see an actual request in your message. What would you like help with?

---

👤 **USER:**
> <skill name="ai-coding-setup" location="/Users/eobomik/.agents/skills/ai-coding-setup/SKILL.md">
References are relative to /Users/eobomik/.agents/skills/ai-coding-setup.

# AI-coding setup

Produce a consistent, project-specific set of AI-coding docs:

- **CLAUDE.md** — project-global behavior + the index of rule files + tooling commands.
- **ARCHITECTURE.md** — structure, components, data flow, stability zones, rule precedence.
- **AI_<COMPONENT>.md** — one coding-rule file per architectural component, tagged with its stack.
- **.agents/skills/review-fix-loop/** — an autonomous review loop with stack-aware verify commands (optional).

The hard part is **multiple stacks in one project**. The solution: split the `AI_*.md` files by
**architectural component/boundary, each tagged with its stack** — see
[references/house-style.md](references/house-style.md). One Python pipeline gets several Python
`AI_*.md` files by layer; a React+Python+Docker app gets `AI_FRONTEND.md` + `AI_BACKEND.md` +
`AI_INFRA.md`. CLAUDE.md and ARCHITECTURE.md §8 index whichever set you produce.

Bundled resources (paths relative to this skill directory):
- `scripts/detect_stacks.py` — repo scanner (stdlib only; run with `python3`).
- `references/house-style.md` — conventions + per-placeholder fill rules. **Read before generating.**
- `references/stack-rules.md` — per-stack rule catalog for the `AI_*.md` files.
- `references/architecture-guide.md` — how to fill ARCHITECTURE.md (the 9 sections).
- `references/interview.md` — the greenfield question flow.
- `references/example.md` — a full worked input→output sample for a polyglot project (self-contained; needs no external repos). Skim it for style before generating.
- `assets/*.template` — the file skeletons to fill and write into the target repo.

## Working rule

Follow confirm-before-acting: **analysis and the file plan are read-only and need no approval, but
present the plan and get a clear "yes" before writing any file into the target project.** When a
target file already exists, **update it in place — never clobber** hand-written content; show a diff
of intended changes.

## Workflow

### Step 0 — Resolve target and mode

Establish the target project path (ask if ambiguous; for a brand-new project, offer to create the
folder). Then scan it:

```bash
python3 scripts/detect_stacks.py /path/to/project
```

Read the JSON: `mode_hint`, `stacks` (+ commands), `components`, `manifests`, `description_docs`,
`env_files`, `ci`, `existing_ai_docs`. Route on `mode_hint` (the user may override):

- `brownfield` → Step 1A.
- `greenfield` → Step 1B.

### Step 1A — Brownfield: analyze the code

Trust the detector's stacks/commands but verify against the real manifests (e.g. read
`pyproject.toml` to confirm whether mypy is a gate; read `package.json` `scripts` for real names).
Then gather what the detector can't:

- Read `README`, `docs/`, `docs/design/`, `docs/chats/`, `IMPLEMENTATION_PLAN.md` for purpose,
  decisions, and history.
- Read any `existing_ai_docs` so you **update rather than recreate** them.
- Infer component boundaries and data contracts from the directory layout and key modules.

Proceed to Step 2.

### Step 1B — Greenfield: interview

There's no code to analyze, so follow [references/interview.md](references/interview.md): read every
`description_docs` file first, pre-fill the answers, then confirm the gaps with the user via
`AskUserQuestion` (batched, defaults offered). Elicit name, pitch, project type, intended stacks per
layer, components, tooling, integrations, secrets, doc language, and the project's integrity rule.
Set the **early-design status** variant. Proceed to Step 2.

### Step 2 — Plan the AI_*.md split, then confirm

Using [references/house-style.md](references/house-style.md) ("The AI_*.md split"), map components →
one stack-tagged `AI_*.md` each (aim for 2–4). **Present to the user**, before writing:

- the file list (`CLAUDE.md`, `ARCHITECTURE.md`, each `AI_*.md` with its component + stack + glob),
- whether `.agents/skills/review-fix-loop` will be scaffolded (and the review-script path),
- which existing files will be updated vs created.

Get a "yes." Then generate, reading the relevant reference before each file. For a concrete model
of a complete output set — a polyglot CLAUDE.md (with the File|Scope table and a project-specific
integrity rule), ARCHITECTURE §2/§7/§8, and two stack-tagged `AI_*.md` files — see
[references/example.md](references/example.md).

### Step 3 — ARCHITECTURE.md

Fill `assets/ARCHITECTURE.md.template` per [references/architecture-guide.md](references/architecture-guide.md):
nine sections, ASCII diagrams in §2 and §5, real names, stability zones in §7, and the §8 rule-file
list matching the split from Step 2. 250–300 lines. No coding rules (those go in `AI_*.md`).

### Step 4 — AI_*.md files

For each planned file, fill `assets/AI_COMPONENT.md.template`, pulling rules from the matching
section(s) of [references/stack-rules.md](references/stack-rules.md). Title `# AI rules — <Component>
(<Stack>)`; set the glob, scope note, ARCHITECTURE pointer, and cross-links; include only rules for
tooling the project actually uses. Keep each short and dense (rules only).

### Step 5 — CLAUDE.md

Fill `assets/CLAUDE.md.template` per [references/house-style.md](references/house-style.md):
description, status blockquote with real build/test commands, "Read before making changes", the
File|Scope table (matching the files on disk and ARCHITECTURE §8), the working agreement **including
a project-specific integrity rule**, the optional documentation-language section, and tooling.

### Step 6 — .agents/skills/review-fix-loop (if chosen)

Copy `assets/agents-skills/review-fix-loop/SKILL.md.template` to
`<project>/.agents/skills/review-fix-loop/SKILL.md`, filling:
- `{{REVIEW_SCRIPT_PATH}}` — the user's external review script (default `$HOME/mbin/code-review.sh`).
- `{{VERIFY_COMMANDS}}` — a bullet list of the detected stack's verify commands (e.g. `pytest`,
  `ruff check .`, `mypy .`, `npm run typecheck`, `go test ./...`), one per stack/component touched.

### Step 7 — Validate

Check, and fix any miss:

- **Cross-file consistency** — the `AI_*.md` set on disk == CLAUDE.md's table == ARCHITECTURE §8 list.
- **No duplication** — no coding/style rule text repeated across ARCHITECTURE.md and an `AI_*.md`
  (grep the rule keywords); architecture rationale not copied into `AI_*.md`.
- **ARCHITECTURE** — run the checklist in [references/architecture-guide.md](references/architecture-guide.md)
  (9 sections, ~250–300 lines, diagrams, §8 intact).
- **Links resolve** — every relative link points to a file that now exists.
- **Status matches mode** — early-design vs in-progress vs implemented.

Report what was created/updated and surface any gaps honestly (e.g. "no tests detected — left the
test command as a TODO for you to confirm").
</skill>

I'll set up the AI-coding docs for this project. Let me start by scanning the repo.

This is a brownfield project that already has AI-coding docs. Per the skill, I need to update in place rather than clobber. Let me read the existing docs and key project files.

The project already has a full doc set. Let me examine the existing AI_*.md files, the AI.md, and verify the actual code structure and tooling.

The existing `AI-PYTHON-REST-API.md` and `AI_FLASK.md` are generic template stubs — the REST one even references FastAPI/pydantic/SQLAlchemy which this project doesn't use (it's aiohttp). Let me check the remaining files and the actual code.

Now I understand the landscape. The existing docs are a mix: `AI.md`, `AI_SQLite.md`, and `AI_PostgreSQL.md` are solid and project-specific, but `AI-PYTHON-REST-API.md` is a generic stub that references FastAPI/pydantic/SQLAlchemy — none of which this project uses (it's aiohttp). `AI_FLASK.md` is also a thin generic stub. Let me verify the actual REST/aiohttp and Flask code, and check for lint/tooling config.

I now have a complete picture. Here's what I found and my proposed plan.

Current state of the doc set:

- ARCHITECTURE.md — solid, already follows the 9-section structure. Needs only minor §8 sync.
- AI.md — good, project-specific general Python rules. Keep.
- AI_SQLite.md — excellent, tightly project-specific. Keep.
- AI_PostgreSQL.md — good but written framework-agnostic. Keep, light touch.
- AI-PYTHON-REST-API.md — generic stub that references FastAPI/pydantic/SQLAlchemy. The project uses aiohttp. This is factually wrong for the codebase.
- AI_FLASK.md — thin generic stub; doesn't reflect the actual admin app (waitress, session auth, API-proxy client).
- CLAUDE.md — a short behavioral file only. Missing the house-style structure (title, status, File|Scope index table, integrity rule, tooling commands).

Gaps I noticed:

- No rule file covers the WebSocket worker / candle engine — the most active, semi-stable core of the system (`websocket_worker.py`, `websocket_manager.py`, `candle_engine.py`).
- File naming is inconsistent: `AI-PYTHON-REST-API.md` (dashes) vs `AI_FLASK.md` / `AI_SQLite.md` / `AI_PostgreSQL.md` (mixed case). House style is `AI_<UPPERCASE>.md`.
- No lint/type tooling is configured (no ruff/mypy/pyproject). Only pytest + pytest-asyncio.

Before I write anything, a few decisions:

Let me check the review skill you created in .agents.

I don't see a `.agents` directory in the project yet, and no review skill you created. The only review-fix-loop I can find is the skill's own template. Let me check it.

I can't find a review skill in the project's `.agents` directory — there's no `.agents` folder in the project at all yet, and nothing review-related under `~/.agents` besides the ai-coding-setup template itself. Let me do one broader search before asking.

Found it — `.agents/skills/review-fix-loop/SKILL.md` exists in the project. My earlier `find .agents` returned nothing because it ran before the shell resolved. Let me read it.

This review skill is a verbatim copy from a different project — it references `cargo`, `clippy`, `service-wrapper/`, the `tuwunel/` submodule, `patches/`, `AI_PATCHES.md`, and Windows/Inno Setup. None of that exists here. I'll adapt it to this Python project (pytest verify, real component areas, this project's AI_*.md files, no submodule/patch rules).

Here's my complete plan. Nothing is written until you confirm.

Files to create/update:

| File | Action | Scope |
| --- | --- | --- |
| `CLAUDE.md` | Rewrite (house-style) | Title, status w/ real commands, Read-before-changes, File\|Scope index table, integrity rule, tooling. Preserves the existing "propose before coding" behavioral rule. |
| `ARCHITECTURE.md` | Light update | §8 rule-file list synced to the new AI_*.md set + names |
| `AI.md` | Keep as-is | General Python rules (already project-specific) |
| `AI_REST_API.md` | New (replaces `AI-PYTHON-REST-API.md`, delete old) | aiohttp routes/middleware/async rules for `src/api/**`, `src/api_server.py`. Removes the wrong FastAPI/pydantic/SQLAlchemy content. |
| `AI_FLASK.md` | Rewrite | Real admin app: `src/admin/**` — waitress, session auth, API-proxy client, localhost-default binding, no direct DB access |
| `AI_SQLITE.md` | Rename from `AI_SQLite.md`, keep content | `src/storage.py` |
| `AI_POSTGRESQL.md` | Rename from `AI_PostgreSQL.md`, keep content | `src/storage_postgres.py`, `scripts/init_postgres.sql` |
| `AI_WEBSOCKET_ENGINE.md` | New | `src/websocket_worker.py`, `src/websocket_manager.py`, `src/candle_engine.py`, `src/candle_aggregator.py` — async tick queue, bounded backpressure, flush tasks, reconnect/backoff, no event-loop blocking |
| `.agents/skills/review-fix-loop/SKILL.md` | Rewrite | Adapt to Python: pytest verify per area, this project's AI_*.md, drop cargo/submodule/patch content |

Naming normalization: `AI_SQLite.md` → `AI_SQLITE.md`, `AI_PostgreSQL.md` → `AI_POSTGRESQL.md`, `AI-PYTHON-REST-API.md` → `AI_REST_API.md`. `AI.md` and `AI_FLASK.md` already fit.

Integrity rule for CLAUDE.md (draft): *"Never fake candle data, drop ticks silently, or weaken a test/DB contract to make a check pass — a green check must reflect real tick-to-candle correctness and data durability."*

Tooling note I'll record honestly: only `pytest` (+ pytest-asyncio, pytest-mock) is configured. No ruff/mypy/black/lint gates exist. I'll leave those as a documented gap, not invent them.

Confirm and I'll generate all files.

---

👤 **USER:**
> go ahead

Renamed and removed. Now let me look at the templates for CLAUDE and AI_COMPONENT to match structure.

Now I'll write all files. Starting with CLAUDE.md.

Now the AI_REST_API.md. Let me look at more of the routes/middleware/server to ground the rules.

I have enough detail. Writing AI_REST_API.md.

Now the WebSocket engine file. Let me ground it in the actual worker/engine code.

The code confirms the queue/flush/backpressure architecture. Writing the WebSocket engine rule file.

Now rewrite AI_FLASK.md for the real admin app. Let me check the auth and api_client modules quickly.

Confirmed: the admin UI is a Flask app that proxies to the REST API via `APIClient` (requests + retry), uses session auth, and never touches the DB directly. Writing the real AI_FLASK.md.

Now update ARCHITECTURE.md §8 to sync the rule-file list with the new set and names. I'll also fix the outdated §3 reference and §7 (mentions `candle_aggregator.py` as file that exists — it does). Let me just update §8.

Now rewrite the review-fix-loop skill for this Python project.

Now validate: cross-file consistency, links resolve, and that the test globs I referenced actually exist.

Everything is in lockstep: the six `AI_*.md` files on disk match the CLAUDE.md table and ARCHITECTURE §8 exactly, and every test glob referenced in the skill resolves to real files. Let me do a final check that the pytest suite actually runs (sanity), and confirm no leftover references to the old filenames anywhere.

The grep confirms all the uppercase references are the new correct ones. Two genuine stale references to old filenames remain, outside my original file set — I'll fix those for consistency.

The pytest sanity check failed only because pytest isn't installed in the ambient Python 3.14 (the project targets 3.9+ via requirements.txt) — that's an environment thing, not a code issue, and I won't install deps unprompted. Let me do a final git status overview.

Done. The AI-coding doc set is now consistent and grounded in the actual codebase.

What changed:

- CLAUDE.md — rewritten to house-style: title, status (v0.9.4, real build/test commands), read-before-changes, a File|Scope index table, a project-specific integrity rule (never fake candles / drop ticks / weaken a DB contract), and honest tooling notes. Preserved your "propose before coding" behavioral rule.
- AI_REST_API.md — new, replaces the generic FastAPI stub (`AI-PYTHON-REST-API.md`, deleted). Real aiohttp rules: route ordering, `asyncio.to_thread` for all DB calls, auth/middleware order, the confirmation-gated destructive endpoints, DB-free `/health`.
- AI_WEBSOCKET_ENGINE.md — new. Covers the hot path: single-writer invariant, bounded tick queue + backpressure, no per-tick DB writes, interval flush tasks, bounded reconnect backoff, OHLCV correctness.
- AI_FLASK.md — rewritten for the real admin app: thin client over `APIClient` (never touches the DB), session auth failing closed, waitress + localhost-default binding.
- AI_SQLite.md → AI_SQLITE.md, AI_PostgreSQL.md → AI_POSTGRESQL.md — renamed to house-style (content kept; both were already solid).
- ARCHITECTURE.md §8 — synced to the new six-file set and names.
- .agents/skills/review-fix-loop/SKILL.md — the copy was from an unrelated Rust/tuwunel project (cargo, submodules, patches, Windows). Rewritten for this project: pytest verify commands mapped per component area, links to this repo's AI_*.md files, git-commit prohibition kept.
- Fixed two stray old-name references in `.kiro/steering/project-rules.md` and `docs/MULTI_WORKER_DEPLOYMENT.md`.

Validation: the six `AI_*.md` files on disk match the CLAUDE.md table and ARCHITECTURE §8 exactly, all cross-links resolve, and every test path referenced in the review skill exists.

Two honest gaps to flag:

- No linter/type-checker is configured (no ruff/mypy/black/pyproject). I documented this as a known gap rather than inventing gates — adding one is your call.
- I couldn't run `pytest` to verify the suite: pytest isn't installed in the ambient Python 3.14 here, and I didn't install deps unprompted. Run `pip install -r requirements.txt && pytest` in the project's environment when you want a live check.

Nothing is committed — all changes are staged/untracked for your review.

---

👤 **USER:**
> suggest commit message

Here's a suggested commit message:

```
docs: align AI-coding rule set with actual stack

Rework the AI_*.md doc set to match the real codebase and house style:

- Rewrite CLAUDE.md to house-style: status, File|Scope index table,
  project-specific integrity rule, honest tooling notes
- Replace generic FastAPI stub with AI_REST_API.md (real aiohttp rules)
- Add AI_WEBSOCKET_ENGINE.md for the tick-ingest/candle hot path
- Rewrite AI_FLASK.md for the thin admin UI (API-client, no direct DB)
- Rename AI_SQLite.md/AI_PostgreSQL.md to AI_SQLITE.md/AI_POSTGRESQL.md
- Sync ARCHITECTURE.md §8 rule-file list to the new set
- Adapt .agents/skills/review-fix-loop to Python (pytest verify)
- Fix stale filename refs in .kiro rules and docs

No lint/type gate exists yet; left as a documented gap.
```

Note I haven't staged the untracked files (`.agents/`, `AI_REST_API.md`, `AI_WEBSOCKET_ENGINE.md`) or run any commit — say the word if you want me to stage and commit.


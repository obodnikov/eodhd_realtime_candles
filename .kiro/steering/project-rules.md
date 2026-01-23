# Project Coding Rules

This project uses AI*.md files for coding guidelines. The specific files vary by project/stack.

## Behavioral Contract

**NEVER start coding immediately after a user request.**

1. Scan root directory for all `AI*.md` files - these define coding rules for this project
2. Read `CLAUDE.md` if present - defines AI behavior contract
3. Check `docs/chats/` for previous implementations and context
4. Review `ARCHITECTURE.md` for system design (if exists)
5. Propose a solution with specifics
6. Wait for explicit approval before implementing

## Rule Discovery

Before any code changes:
- Find all `AI*.md` files in project root
- Read each one relevant to the task (e.g., AI_SQLite.md for database work)
- Follow stack-specific rules strictly
- Do not redefine or duplicate rules from these files

## Rule Priority (highest to lowest)

1. User's explicit instruction in current conversation
2. Stack-specific AI rules (AI_*.md files matching the task)
3. General AI rules (AI.md if present)
4. Architecture document (ARCHITECTURE.md if present)
5. Language/framework conventions

## Conflict Resolution

When rules conflict:
1. STOP - do not proceed
2. IDENTIFY - state which rules conflict
3. ASK - present options with trade-offs
4. WAIT - get explicit decision before coding

## Reference Files

- #[[file:CLAUDE.md]] - AI behavior contract
- #[[file:ARCHITECTURE.md]] - System architecture

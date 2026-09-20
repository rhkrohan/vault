# Agent operating contract

Read PRD.md first. These rules apply to the orchestrator, every subagent, and the deployer.

1. The contracts in PRD.md sections 4 to 9 are fixed. Propose changes in PROGRESS.md; never make them silently.
2. Edit only the paths you own (PRD.md section 4). Read anything.
3. Small commits, prefixed with your role: `librarian: parse chatgpt export`.
4. Every module ends with its tests green and one line appended to PROGRESS.md: time, role, what changed, what is next.
5. Dependencies are fixed: anthropic, pydantic, typer, mcp, fastapi, uvicorn, httpx, pytest, ruff. Anything else requires asking the human.
6. Never commit .env, *.db, or anything under inbox/real/. Never write a key into a file, a log, or a screenshot.
7. No destructive git or shell commands: no `rm -rf`, no force push, no `git reset --hard`. No network calls except the configured model provider.
8. When a contract is ambiguous, stop and ask. Do not guess.
9. No transcript text goes into memory/. Only extracted facts, summaries, and evidence fragments under 120 characters.
10. Main must pass `pytest` and `ruff check .` at every merge, and `vault eval` runs before every merge.
11. Synthetic transcripts cover two projects, one person, one finance mention, one health mention, and a decision that changes between transcript 2 and transcript 4.
12. The deployer works on the branch `hosted` and touches only providers/runpod.py, web.py and deploy/. The orchestrator merges it in Phase 3.

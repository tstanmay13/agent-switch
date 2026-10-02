---
name: updated-from-claude
description: Catch an already open Codex session up on work done in its paired Claude Code session. Use when the user says Claude updated the task, asks what Claude did, or says this Codex session has been switched back from Claude.
---

# Catch up from Claude

This skill is for an **already open Codex session**. The normal terminal switch `agent-switch codex` already injects its own handoff prompt; `agent-switch codex --queue` can queue one to an active paired Codex thread from another terminal.

1. Identify the current coding repository. Run `agent-switch status` there and confirm that its saved Codex session is the one being continued. If the repository or pairing is ambiguous, use `agent-switch list` and ask the user to identify it.
2. Run `agent-switch codex --dry-run` from the repository root. This synchronizes new local transcript records and Git state and writes `.agent-switch/handoff.md` without launching another Codex process or contacting the API.
3. Read `.agent-switch/handoff.md`; inspect `git status`, `git diff`, and relevant files. Tell the user the concrete changes, decisions, test results, and outstanding work, then continue the task they requested.
4. Treat the repository as the source of truth. The handoff is a concise delta, and its `--dry-run` does not mark it delivered. If the user later switches with `agent-switch codex`, some context may repeat. Never edit native transcript files.

If this session already received a queued or resumed handoff, use it directly; refresh only when the user says Claude did additional work afterward.


## Command setup (macOS, Linux, and Windows)

Run commands in the coding repository's terminal, using its actual shell. These
commands work in PowerShell too; do not use Unix-only `/dev/null` redirection.
If `agent-switch` is missing from PATH, use the installed Python entry point:

- PowerShell: `py -3 "$HOME/.local/share/agent-switch/agent_switch.py" status`
- macOS/Linux: `python3 "$HOME/.local/share/agent-switch/agent_switch.py" status`

Replace `status` with the needed command and arguments. If Python was installed
without `py`, use `python` in PowerShell. If that file is missing, install from an
agent-switch clone (`python install.py` or `./install.ps1` on Windows,
`./install.sh` on macOS/Linux). Restart the agent session after installing skills.
Do not guess a clone path or copy session state from another machine. Windows
and WSL have separate homes, CLI installations, and transcripts; run this tool
in the same environment as both agents and pair sessions locally.

# agent-switch

Local Claude Code ↔ Codex handoffs. Python 3 standard library only; native transcripts stay read only. Each coding repository holds private `.agent-switch/state.json`, `ledger.jsonl`, and `handoff.md` (add `.agent-switch/` to that repository's `.gitignore`).

For setup, everyday switches, already open sessions, previews, and troubleshooting, see [the usage guide](USAGE.md).

Clone this repository and run `./install.sh` to link the command into `~/.local/bin` (which must be on your `PATH`). In a coding repository, run `agent-switch pair --claude-id UUID --codex-id UUID` to associate existing sessions, then `agent-switch sync`, `agent-switch status`, and `agent-switch codex` or `agent-switch claude`. `--dry-run` generates and prints the handoff without contacting an API. `agent-switch doctor` lists local candidates, and `agent-switch list` shows paired repositories. From a workspace parent with one pairing, status and switch commands select it automatically; with multiple pairings, pass `--repo PATH` before the command.

The launcher resumes the same native session by ID, passing the new delta via the CLI's supported prompt argument. It does not edit native JSONL. Sessions are selected by metadata and exact repository path. Pairing an explicitly named Codex session whose cwd is a parent of the repository is supported for a session started before entering that repo. Unpaired sessions require an unambiguous discovered candidate. The ledger is append only and uses source transcript byte offsets and stable event IDs to avoid duplicates. A partial final line remains unread until completed.

The local handoff includes recent normalized activity, current status, diff summary, recent commits, and changed files. It limits long transcript text and omits full tool outputs. Inspect the working tree for exact details. State files may contain private task context; never commit them.

For a currently active Codex thread, `agent-switch codex --queue` sends the generated prompt with Codex's supported queue command. `agent-switch claude --print` is a noninteractive live resume check; it contacts Claude and leaves the delta pending if authentication or usage is unavailable. The normal `claude` and `codex` commands work at any time, independent of a usage-limit signal. `agent-switch test` runs offline tests without contacting either service.

If you use a nondefault Claude profile, set `CLAUDE_CONFIG_DIR` when running both Claude and `agent-switch`; the CLI reads transcripts from that profile and passes the variable to Claude when resuming a session. Keep `.agent-switch/` ignored in each coding repository because it contains local session state.

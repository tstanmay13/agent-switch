# Using agent-switch

The command names the **agent you want to continue in**. Switching is manual and works at any time; no usage-limit signal is required.

## Setup once per project

Install from the agent-switch repository with `./install.sh`. In a coding repository, run `agent-switch doctor` to see local sessions and `agent-switch pair --claude-id CLAUDE_ID --codex-id CODEX_ID` to bind the exact two sessions. If only one session matches, a first switch can discover it automatically. If several match, the CLI lists candidates instead of choosing the latest one. Once paired, the saved IDs stay fixed until you explicitly pair different IDs.

## Day-to-day flows

| You want to... | Run from the coding repository |
| --- | --- |
| Leave Claude, continue in Codex | `agent-switch codex` |
| Leave Codex, continue in Claude | `agent-switch claude` |
| Check saved sessions and Git state | `agent-switch status` |
| See all known pairings | `agent-switch list` |
| Preview what Claude would get without an API call | `agent-switch claude --dry-run` |
| Preview what Codex would get without an API call | `agent-switch codex --dry-run` |
| Update the local ledger without delivering anything | `agent-switch sync` |
| Send to an already open Codex thread | `agent-switch codex --queue` from a second terminal |
| Check setup or run local tests | `agent-switch doctor` or `agent-switch test` |

For a Claude session that is already open, tell it: “Use the updated-from-codex skill and catch up on Codex's work.” It will build and read a current handoff inside that session. For an open Codex session, say: “Use updated-from-claude and catch up on Claude's work.” These skill flows use a dry run; they do not mark the handoff delivered, so a later CLI switch may repeat it. To get native prompt delivery, exit the receiving agent's interactive process and run the corresponding `agent-switch` command in a regular terminal. Avoid two interactive processes writing to the same native session concurrently.

A normal switch syncs both transcripts, reads Git status/diff/recent commits, writes `.agent-switch/handoff.md`, and resumes the saved destination session with that handoff as a prompt. It does not ask the exhausted source agent to summarize. The destination gets the activity since its previous successful handoff, not a copy of both full transcripts. A failed Claude authentication or usage-limited response leaves its handoff pending.

## Which session and repository?

Session IDs come from native transcript metadata. `agent-switch` reads the paired transcript paths and incremental byte offsets in `.agent-switch/state.json`. New unrelated sessions do not change the pairing. If a transcript disappears or a repository path differs, it errors rather than guessing.

Run from the repository root. From a workspace directory containing one paired repository, that pairing is selected automatically; with multiple pairings, use the global option **before** the command:

~~~~sh
agent-switch --repo ~/code/my-project status
agent-switch --repo ~/code/my-project claude
~~~~

`agent-switch sync` updates the ledger but does not inject context. `agent-switch status` does not sync. A dry run generates the latest handoff but does not mark it received.

## First paired session

~~~~sh
cd ~/code/my-project
agent-switch list
agent-switch claude --dry-run
agent-switch claude
# After Claude makes new changes:
agent-switch codex
~~~~

For a nondefault Claude profile, set `CLAUDE_CONFIG_DIR` for both Claude and `agent-switch`. The profile must be logged in for a live Claude resume:

~~~~sh
export CLAUDE_CONFIG_DIR="$HOME/.claude-work"
claude auth login
~~~~

`agent-switch claude --print` is a noninteractive live test that contacts Claude. `agent-switch test` runs offline and contacts neither service.

Runtime state and handoff text can include private task details; keep `.agent-switch/` ignored by Git. Native Claude and Codex transcript files are read only.

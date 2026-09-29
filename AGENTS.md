# Working in agent-switch

Run `./agent-switch test` after changing the CLI. Keep `.agent-switch/` runtime files, session transcripts, and credentials out of Git. Each coding repository using the tool should ignore `.agent-switch/`.

This checkout is not yet managed by `dev-setup` because it has no remote or upstream. Once those exist, register `agent-switch` with `./install.sh` in the sibling `dev-setup/surfaces.conf`, update both READMEs, and verify with `dev list` and `dev status --fetch`. Do not assume that placing a checkout beside `dev-setup` registers it automatically.

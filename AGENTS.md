# Working in agent-switch

Run `./agent-switch test` after changing the CLI. Keep `.agent-switch/` runtime files, session transcripts, and credentials out of Git. Each coding repository using the tool should ignore `.agent-switch/`.

This repository is registered in the sibling `dev-setup/surfaces.conf` with `./install.sh`. If its install command, remote, or setup steps change, update both READMEs and check `dev list` and `dev status --fetch`. A sibling checkout by itself is not enough for `dev` to manage it.

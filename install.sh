#!/bin/sh
set -eu
mkdir -p "$HOME/.local/bin"
ln -sfn "$(cd "$(dirname "$0")" && pwd)/agent-switch" "$HOME/.local/bin/agent-switch"
echo "Installed agent-switch to $HOME/.local/bin/agent-switch"

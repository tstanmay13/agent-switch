"""Install the command and its handoff skills without symlinks or admin rights."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import sys


def install(home=None):
    home = Path(home) if home is not None else Path.home()
    source = Path(__file__).resolve().parent
    runtime = home / '.local' / 'share' / 'agent-switch'
    bin_dir = home / '.local' / 'bin'
    runtime.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / 'agent_switch.py', runtime / 'agent_switch.py')
    shutil.copy2(source / 'install.py', runtime / 'install.py')
    shutil.copytree(source / 'skills', runtime / 'skills', dirs_exist_ok=True)
    shutil.copytree(source / 'tests', runtime / 'tests', dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    if os.name == 'nt':
        command = bin_dir / 'agent-switch.cmd'
        content = '@echo off\r\n"{}" "{}" %*\r\n'.format(
            sys.executable.replace('%', '%%'), str(runtime / 'agent_switch.py').replace('%', '%%'))
    else:
        command = bin_dir / 'agent-switch'
        content = '#!/bin/sh\nexec {} {} "$@"\n'.format(
            shlex.quote(sys.executable), shlex.quote(str(runtime / 'agent_switch.py')))
    if command.is_symlink():
        command.unlink()
    command.write_text(content, encoding='utf-8', newline='')
    command.chmod(0o755)
    destinations = [
        ('updated-from-claude', home / '.agents' / 'skills'),
        ('updated-from-codex', Path(os.environ.get('CLAUDE_CONFIG_DIR', home / '.claude')) / 'skills'),
    ]
    for name, base in destinations:
        destination = base / name / 'SKILL.md'
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / 'skills' / name / 'SKILL.md', destination)
        print(f'Installed skill: {destination}')
    print(f'Installed command: {command}')
    print(f'Add {bin_dir} to your user PATH, then restart terminals and agent sessions.')
    print('Run agent-switch test and agent-switch doctor from your coding repository.')
    return command


if __name__ == '__main__':
    argparse.ArgumentParser(description=__doc__).parse_args()
    install()

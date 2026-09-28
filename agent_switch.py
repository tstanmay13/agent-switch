"""Local, read-only transcript bridge for Claude Code and Codex."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

MAX_TEXT = 1200


def now():
    return datetime.now(timezone.utc).isoformat()


def atomic(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def json_lines(path, offset=0):
    """Yield complete valid records and their byte offsets. Leave partial tails unread."""
    with open(path, 'rb') as f:
        f.seek(offset)
        while True:
            start = f.tell()
            line = f.readline()
            if not line or not line.endswith(b'\n'):
                break
            try:
                yield start, f.tell(), json.loads(line)
            except (ValueError, UnicodeDecodeError):
                # A complete corrupt record is skipped; the offset still advances.
                yield start, f.tell(), None


def text_blocks(blocks):
    if isinstance(blocks, str):
        return blocks
    if not isinstance(blocks, list):
        return ''
    return '\n'.join(x.get('text', '') for x in blocks if isinstance(x, dict) and x.get('type') in ('text', 'input_text', 'output_text'))


BOOTSTRAP = 'You are continuing work handled by another coding agent while this native session was inactive.'

def compact(s, limit=MAX_TEXT):
    return re.sub(r'\s+', ' ', str(s)).strip()[:limit]


def event(source, kind, text, timestamp=None, **extra):
    limit = {'command': 220, 'request': 800, 'message': 650, 'result': 300}.get(kind, 350)
    return dict(source=source, kind=kind, text=compact(text, limit), timestamp=timestamp or now(), **extra)


def parse_claude(o):
    if not o or o.get('isSidechain') or o.get('isMeta'):
        return []
    typ = o.get('type')
    if typ not in ('user', 'assistant'):
        return []
    msg = o.get('message') or {}
    blocks = msg.get('content', [])
    if isinstance(blocks, str):
        blocks = [{'type': 'text', 'text': blocks}]
    result = []
    if typ == 'user' and any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in blocks):
        for b in blocks:
            if not isinstance(b, dict) or b.get('type') != 'tool_result':
                continue
            value = b.get('content', '')
            value = text_blocks(value) if isinstance(value, list) else str(value)
            if b.get('is_error') or re.search(r'(?:test files|tests? passed|tests? failed|error:|failed|tsc_ok|compiled)', value, re.I):
                result.append(event('claude', 'error' if b.get('is_error') else 'result', value, o.get('timestamp')))
    if typ == 'user' and not any(isinstance(b, dict) and b.get('type') == 'tool_result' for b in blocks):
        t = text_blocks(blocks)
        if t and not t.startswith(BOOTSTRAP):
            result.append(event('claude', 'request', t, o.get('timestamp')))
    if typ == 'assistant':
        t = text_blocks(blocks)
        if t:
            result.append(event('claude', 'message', t, o.get('timestamp')))
        for b in blocks:
            if not isinstance(b, dict) or b.get('type') != 'tool_use':
                continue
            name, inp = b.get('name', ''), b.get('input') or {}
            if name in ('Bash', 'bash'):
                result.append(event('claude', 'command', inp.get('command', ''), o.get('timestamp')))
            elif name in ('Edit', 'Write', 'MultiEdit', 'NotebookEdit'):
                result.append(event('claude', 'file', inp.get('file_path') or inp.get('notebook_path', ''), o.get('timestamp')))
    return [x for x in result if x['text']]


def parse_codex(o):
    if not o or o.get('type') != 'response_item':
        return []
    p = o.get('payload') or {}
    typ = p.get('type')
    result = []
    if typ == 'message':
        role = p.get('role')
        if role in ('user', 'assistant'):
            t = text_blocks(p.get('content'))
            if t and not (role == 'user' and (t.startswith(BOOTSTRAP) or t.startswith('<environment_context>'))):
                result.append(event('codex', 'request' if role == 'user' else 'message', t, o.get('timestamp')))
    elif typ in ('function_call', 'custom_tool_call'):
        name = p.get('name', '')
        raw = p.get('arguments', p.get('input', ''))
        try:
            inp = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            inp = raw
        if name in ('exec_command', 'shell_command') and isinstance(inp, dict):
            result.append(event('codex', 'command', inp.get('cmd', ''), o.get('timestamp')))
        elif name in ('apply_patch',) and isinstance(inp, str):
            result.append(event('codex', 'file', '\n'.join(re.findall(r'^\*\*\* (?:Update|Add|Delete) File: (.+)$', inp, re.M)), o.get('timestamp')))
        # Orchestrated tool calls are opaque; keep a short command hint, never outputs.
        elif name == 'exec':
            hints = re.findall(r'(?:cmd:|cmd["\']\s*:)\s*["\']([^"\']+)', str(inp))
            for hint in hints[:5]:
                result.append(event('codex', 'command', hint, o.get('timestamp')))
    elif typ in ('function_call_output', 'custom_tool_call_output'):
        raw = p.get('output', '')
        if isinstance(raw, list):
            raw = text_blocks(raw)
        if isinstance(raw, str) and re.search(r'(?:FAIL|error:|failed|passed|tests? passed)', raw, re.I):
            result.append(event('codex', 'result', raw[:500], o.get('timestamp')))
    return [x for x in result if x['text']]


def transcript_meta(path, source):
    try:
        for _, _, o in json_lines(path):
            if not o:
                continue
            if source == 'codex' and o.get('type') == 'session_meta':
                p = o.get('payload') or {}
                if p.get('parent_thread_id'):
                    return None
                return dict(id=p.get('session_id') or p.get('id'), cwd=p.get('cwd'), path=str(path), mtime=path.stat().st_mtime)
            if source == 'claude' and o.get('sessionId') and o.get('cwd'):
                return dict(id=o['sessionId'], cwd=o['cwd'], path=str(path), mtime=path.stat().st_mtime)
    except OSError:
        return None
    return None


def roots(source):
    home = Path.home()
    if source == 'codex':
        return [Path(os.environ.get('CODEX_HOME', home / '.codex')) / 'sessions']
    return [Path(os.environ.get('CLAUDE_CONFIG_DIR', home / '.claude')) / 'projects', home / '.claude-personal/projects', home / '.claude/projects']


def discover(source, repo, session_id=None, allow_parent=False):
    repo = Path(repo).resolve()
    found = []
    for root in dict.fromkeys(roots(source)):
        if not root.exists():
            continue
        for p in root.rglob('*.jsonl'):
            if 'subagents' in p.parts:
                continue
            if session_id and session_id not in p.name:
                continue
            m = transcript_meta(p, source)
            if not m or (session_id and m['id'] != session_id):
                continue
            cwd = Path(m['cwd']).resolve()
            if cwd == repo or (allow_parent and cwd in repo.parents):
                found.append(m)
    found.sort(key=lambda x: x['mtime'], reverse=True)
    return found


def choose(source, repo, session_id=None, allow_parent=False):
    candidates = discover(source, repo, session_id, allow_parent)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise RuntimeError(f'No {source} session for {repo}; specify --{source}-id if its cwd is a parent.')
    lines = '\n'.join(f"  {c['id']}  {c['cwd']}  {c['path']}" for c in candidates[:12])
    raise RuntimeError(f'Multiple {source} sessions match. Specify --{source}-id:\n{lines}')


def git(repo, *args):
    r = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ''


def git_snapshot(repo):
    return dict(branch=git(repo, 'branch', '--show-current'), head=git(repo, 'rev-parse', 'HEAD'),
                status=git(repo, 'status', '--short'), diff=git(repo, 'diff', '--no-ext-diff', '--stat') + '\n' + git(repo, 'diff', '--cached', '--no-ext-diff', '--stat'), patch=(git(repo, 'diff', '--no-ext-diff') + '\n' + git(repo, 'diff', '--cached', '--no-ext-diff'))[:6000],
                commits=git(repo, 'log', '-5', '--pretty=format:%h %s'),
                changed='\n'.join(dict.fromkeys((git(repo, 'diff', '--name-only') + '\n' + git(repo, 'diff', '--cached', '--name-only')).splitlines())))


def read_ledger(path):
    if not path.exists():
        return []
    return [o for _, _, o in json_lines(path) if o]


def append_events(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a') as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())


def state_dir(repo):
    return Path(repo) / '.agent-switch'


def save_state(repo, state):
    state['updated_at'] = now()
    atomic(state_dir(repo) / 'state.json', json.dumps(state, indent=2) + '\n')


def initialize(repo, claude_id=None, codex_id=None):
    repo = Path(repo).resolve()
    if not (repo / '.git').exists():
        raise RuntimeError(f'{repo} is not a git repository')
    state = load(state_dir(repo) / 'state.json', {'version': 1, 'repo': str(repo), 'agents': {}, 'seen_by': {'claude': 0, 'codex': 0}})
    if state['repo'] != str(repo):
        raise RuntimeError('State belongs to another repository')
    for source, sid in [('claude', claude_id), ('codex', codex_id)]:
        if sid:
            match = choose(source, repo, sid, allow_parent=True)
            if source == 'claude' and Path(match['cwd']).resolve() != repo:
                raise RuntimeError('Claude session cwd does not match repository')
            state['agents'][source] = {'id': match['id'], 'path': match['path'], 'cwd': match['cwd'], 'offset': 0, 'last_activity': None}
    state['git'] = git_snapshot(repo)
    save_state(repo, state)
    return state


def sync(repo):
    repo = Path(repo).resolve()
    state = load(state_dir(repo) / 'state.json')
    if not state:
        state = initialize(repo)
    ledger_path = state_dir(repo) / 'ledger.jsonl'
    known = {e['id'] for e in read_ledger(ledger_path)}
    new = []
    for source in ('claude', 'codex'):
        entry = state['agents'].get(source)
        if not entry:
            continue
        candidates = discover(source, repo, entry['id'], allow_parent=True)
        if not candidates:
            raise RuntimeError(f'{source} transcript missing or repo path changed: {entry["id"]}')
        meta = candidates[0]
        path = Path(meta['path'])
        if source == 'claude' and Path(meta['cwd']).resolve() != repo:
            raise RuntimeError('Claude transcript repo path changed')
        offset = entry.get('offset', 0) if entry.get('path') == str(path) else 0
        if path.stat().st_size < offset:
            offset = 0
        count = 0
        for start, end, record in json_lines(path, offset):
            offset = end
            if record is None:
                continue
            for n, e in enumerate(parse_claude(record) if source == 'claude' else parse_codex(record)):
                e['id'] = hashlib.sha256(f'{source}:{entry["id"]}:{start}:{n}'.encode()).hexdigest()[:20]
                if e['id'] not in known:
                    new.append(e)
                    known.add(e['id'])
                    count += 1
                    entry['last_activity'] = e['timestamp']
        entry['path'] = str(path)
        entry['offset'] = offset
        entry['size'] = path.stat().st_size
        entry['new_events'] = count
    current_git = git_snapshot(repo)
    previous = state.get('git', {})
    if previous and (previous.get('head'), previous.get('status'), previous.get('diff')) != (current_git['head'], current_git['status'], current_git['diff']):
        e = event('git', 'state', f"branch {current_git['branch']}; HEAD {current_git['head'][:12]}; changed: {current_git['status']}; diff: {current_git['diff']}")
        e['id'] = hashlib.sha256(json.dumps(current_git, sort_keys=True).encode()).hexdigest()[:20]
        if e['id'] not in known:
            new.append(e)
    append_events(ledger_path, new)
    state['git'] = current_git
    save_state(repo, state)
    return state, new


def handoff(repo, target):
    state, _ = sync(repo)
    all_events = read_ledger(state_dir(repo) / 'ledger.jsonl')
    start = min(state['seen_by'].get(target, 0), len(all_events))
    delta = [e for e in all_events[start:] if e['source'] != target]
    # Keep the actionable end of a long initial transcript, with the latest request separately.
    latest_request = next((e for e in reversed(delta) if e['kind'] == 'request'), None)
    other = 'claude' if target == 'codex' else 'codex'
    other_cwd = (state['agents'].get(other) or {}).get('cwd')
    cross_repo = bool(other_cwd and Path(other_cwd).resolve() != Path(repo).resolve())
    eligible = [e for e in delta if not (cross_repo and e['source'] == other and e['kind'] == 'request')]
    selected_ids = {e['id'] for e in [x for x in eligible if x['kind'] != 'command'][-18:] + [x for x in eligible if x['kind'] == 'command' and len(x['text']) > 12][-4:]}
    selected = [e for e in eligible if e['id'] in selected_ids]
    gitstate = state['git']
    lines = [f'# Handoff to {target}', '', f'Repository: {repo}', f'Events since this session last received a handoff: {len(delta)}', '']
    if other_cwd and Path(other_cwd).resolve() != Path(repo).resolve():
        lines += [f'Note: the {other} session started in {other_cwd}. Its transcript may include work in sibling repositories. Continue this native session’s existing task; use the repository path and git state to judge which new events apply.', '']
    if latest_request and latest_request not in selected and other_cwd == str(Path(repo).resolve()):
        lines += ['## Most recent request', f"- [{latest_request['source']}] {latest_request['text']}", '']
    lines += ['## New activity']
    lines += [f"- [{e['source']} / {e['kind']}] {compact(e['text'], 500 if e['kind'] != 'command' else 180)}" for e in selected] or ['- No new transcript events.']
    unresolved = [e for e in selected if e['kind'] in ('error',) or (e['kind'] == 'message' and re.search(r'\b(?:TODO|remaining|blocked|next step|still need)\b', e['text'], re.I))][-4:]
    if unresolved:
        lines += ['', '## Possible unresolved work or errors'] + [f"- [{e['source']}] {compact(e['text'], 500)}" for e in unresolved]
    lines += ['', '## Current repository', f"- Branch: {gitstate['branch']}", f"- HEAD: {gitstate['head'][:12]}", '- Status:', '```', gitstate['status'] or 'clean', '```', '- Diff summary:', '```', gitstate['diff'] or 'no unstaged diff', '```', '- Recent commits:', '```', gitstate['commits'] or 'none', '```', '- Changed files:', '```', gitstate['changed'] or 'none', '```', '- Current diff (truncated):', '```diff', gitstate['patch'] or 'no unstaged diff', '```', '']
    lines += ['## Next step', 'Inspect the working tree and git diff, then continue the latest unresolved task. Treat the repository as the source of truth.']
    body = '\n'.join(lines) + '\n'
    atomic(state_dir(repo) / 'handoff.md', body)
    return state, body, len(all_events)


def launch(repo, target, dry_run=False, print_mode=False, queue=False):
    state = load(state_dir(repo) / 'state.json') or initialize(repo)
    source = 'claude' if target == 'codex' else 'codex'
    for agent in (source, target):
        if agent not in state['agents']:
            candidates = discover(agent, repo)
            if len(candidates) > 1:
                choose(agent, repo)  # raises with candidate list
            if len(candidates) == 1:
                m = candidates[0]
                state['agents'][agent] = {'id': m['id'], 'path': m['path'], 'cwd': m['cwd'], 'offset': 0}
    if source not in state['agents']:
        raise RuntimeError(f'No {source} session found for {repo}; use pair --{source}-id ID')
    save_state(repo, state)
    state, body, end = handoff(repo, target)
    entry = state['agents'].get(target)
    exe = shutil.which(target)
    if not exe:
        raise RuntimeError(f'{target} CLI is not installed or is missing from PATH')
    prompt = f'You are continuing work handled by another coding agent while this native session was inactive. Read {state_dir(repo) / "handoff.md"}, inspect the current working tree and git diff, and continue the existing task. The handoff contains only new activity since this session last received context. Do not redo completed work unless inspection shows a problem.\n\n{body}'
    if target == 'claude':
        cmd = [exe] + (['--resume', entry['id']] if entry else []) + (['--print'] if print_mode else []) + [prompt]
        env = os.environ.copy()
        if not env.get('CLAUDE_CONFIG_DIR') and str(Path(repo).resolve()).startswith(str(Path.home() / 'Documents/personal')):
            env['CLAUDE_CONFIG_DIR'] = str(Path.home() / '.claude-personal')
    else:
        cmd = [exe] + (['resume', entry['id']] if entry else []) + [prompt]
        env = os.environ.copy()
    if queue:
        if target != 'codex' or not entry:
            raise RuntimeError('--queue requires a paired Codex session')
        cmd = [exe, 'queue', '--thread', entry['id'], '--message', prompt, '-C', str(repo)]
    if dry_run:
        print('Command:', ' '.join(cmd[:-1]), '<generated prompt>')
        print('Prompt:\n' + prompt)
        return 0
    old_size = Path(entry['path']).stat().st_size if entry and Path(entry['path']).exists() else 0
    try:
        rc = subprocess.call(cmd, cwd=repo, env=env)
        # Keep the delta pending after a usage-limit or launch failure.
        delivered = queue and rc == 0
        if rc == 0 and entry and Path(entry['path']).exists():
            for _, _, record in json_lines(entry['path'], old_size):
                if record and (parse_claude(record) if target == 'claude' else parse_codex(record)):
                    events = parse_claude(record) if target == 'claude' else parse_codex(record)
                    if any(e['kind'] == 'message' for e in events):
                        delivered = True
        if delivered:
            state['seen_by'][target] = end
            state['last_active'] = target
            save_state(repo, state)
        return rc
    finally:
        # A new native session receives its ID from its own persisted metadata.
        if not entry:
            matches = discover(target, repo)
            if len(matches) == 1:
                state['agents'][target] = {'id': matches[0]['id'], 'path': matches[0]['path'], 'cwd': matches[0]['cwd'], 'offset': 0}
                save_state(repo, state)


def find_pairings(directory):
    """Find local pairings in a workspace's immediate child repositories."""
    directory = Path(directory).resolve()
    candidates = [directory]
    if directory.is_dir():
        candidates.extend(p for p in directory.iterdir() if p.is_dir() and not p.name.startswith('.'))
    found = []
    for repo in candidates:
        state = load(state_dir(repo) / 'state.json')
        if state and state.get('repo') == str(repo):
            found.append((repo, state))
    return found


def known_pairings(directory):
    personal = Path.home() / 'Documents/personal'
    locations = [Path(directory).resolve()]
    if personal not in locations:
        locations.append(personal)
    found = {}
    for location in locations:
        for repo, state in find_pairings(location):
            found[repo] = state
    return sorted(found.items(), key=lambda item: str(item[0]))


def resolve_repo(directory, explicit=False):
    directory = Path(directory).resolve()
    if explicit or (state_dir(directory) / 'state.json').exists() or (directory / '.git').exists():
        return directory
    pairings = known_pairings(directory)
    if len(pairings) == 1:
        print(f'Using pairing in {pairings[0][0]}')
        return pairings[0][0]
    if len(pairings) > 1:
        choices = '\n'.join(f'  {repo}' for repo, _ in pairings)
        raise RuntimeError(f'Multiple paired repositories found. Choose one with --repo PATH:\n{choices}')
    return directory


def list_pairings(directory):
    pairings = known_pairings(directory)
    if not pairings:
        print('No paired repositories found. Run agent-switch pair inside a project repository.')
        return
    for repo, state in pairings:
        agents = state.get('agents', {})
        claude = agents.get('claude', {}).get('id', '-')
        codex = agents.get('codex', {}).get('id', '-')
        print(f'{repo}\n  Claude: {claude}\n  Codex:  {codex}\n  Last:   {state.get("last_active", "unknown")}')


def status(repo):
    state = load(state_dir(repo) / 'state.json')
    if not state:
        print(f'No pairing in {repo}. Run agent-switch list to see known pairings, or pair --claude-id ID --codex-id ID in this repository.')
        return
    for source in ('claude', 'codex'):
        e = state['agents'].get(source, {})
        print(f'{source.title()}:')
        print(f"  session: {e.get('id', 'unpaired')}\n  transcript: {e.get('path', '-')}\n  last activity: {e.get('last_activity', '-')}\n  synced through: {e.get('offset', 0)} bytes")
    g = git_snapshot(repo)
    print(f"Repo:\n  branch: {g['branch']}\n  HEAD: {g['head'][:12]}\n  dirty files:\n{g['status'] or '    none'}")
    print(f"Active/last agent: {state.get('last_active', 'unknown')}")


def main(argv=None):
    p = argparse.ArgumentParser(prog='agent-switch')
    p.add_argument('--repo', type=Path, default=Path.cwd(), help='Git repository to hand off (default: cwd)')
    sub = p.add_subparsers(dest='command', required=True)
    pair = sub.add_parser('pair', help='associate existing native sessions')
    pair.add_argument('--claude-id')
    pair.add_argument('--codex-id')
    sub.add_parser('sync')
    sub.add_parser('status')
    sub.add_parser('list', help='show paired repositories')
    sub.add_parser('doctor')
    sub.add_parser('test', help='run offline integration tests (no API calls)')
    for name in ('claude', 'codex'):
        s = sub.add_parser(name)
        s.add_argument('--dry-run', action='store_true')
        if name == 'codex':
            s.add_argument('--queue', action='store_true', help='send to an already active paired Codex thread')
        if name == 'claude':
            s.add_argument('--print', dest='print_mode', action='store_true', help='noninteractive integration test; contacts Claude')
    args = p.parse_args(argv)
    repo = args.repo.resolve()
    try:
        if args.command == 'list':
            list_pairings(repo)
            return 0
        if args.command in ('status', 'sync', 'claude', 'codex'):
            repo = resolve_repo(repo, explicit='--repo' in (argv if argv is not None else sys.argv[1:]))
        if args.command == 'pair':
            print(json.dumps(initialize(repo, args.claude_id, args.codex_id), indent=2))
        elif args.command == 'sync':
            _, events = sync(repo)
            print(f'Synced {len(events)} new events.')
        elif args.command == 'status':
            status(repo)
        elif args.command == 'test':
            return subprocess.call([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'], cwd=Path(__file__).parent)
        elif args.command == 'doctor':
            print(f'Repository: {repo} ({"git" if (repo / ".git").exists() else "not a git root"})')
            for source in ('claude', 'codex'):
                print(f'{source}: {shutil.which(source) or "not installed"}')
                for c in discover(source, repo)[:5]:
                    print(f"  {c['id']} {c['path']}")
        else:
            return launch(repo, args.command, args.dry_run, getattr(args, 'print_mode', False), getattr(args, 'queue', False))
        return 0
    except (RuntimeError, OSError, ValueError) as e:
        print(f'agent-switch: {e}', file=sys.stderr)
        return 2

if __name__ == '__main__':
    sys.exit(main())

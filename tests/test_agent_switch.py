import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import agent_switch as a
original_roots = a.roots


class SwitchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        subprocess.run(['git', '-C', str(self.repo), 'init', '-q'], check=True)
        self.claude_root = self.root / 'claude' / 'projects'
        self.codex_root = self.root / 'codex' / 'sessions'
        self.claude_root.mkdir(parents=True)
        self.codex_root.mkdir(parents=True)
        self.cid = '11111111-1111-4111-8111-111111111111'
        self.xid = '22222222-2222-4222-8222-222222222222'
        self.cp = self.claude_root / f'{self.cid}.jsonl'
        self.xp = self.codex_root / f'rollout-{self.xid}.jsonl'
        self.write(self.cp, [{'type':'user','sessionId':self.cid,'cwd':str(self.repo),'timestamp':'t1','message':{'role':'user','content':'Build a thing'}},
                             {'type':'assistant','sessionId':self.cid,'cwd':str(self.repo),'timestamp':'t2','message':{'role':'assistant','content':[{'type':'text','text':'Decision: use Python'},{'type':'tool_use','name':'Write','input':{'file_path':'main.py'}}]}}])
        self.write(self.xp, [{'type':'session_meta','payload':{'id':self.xid,'session_id':self.xid,'cwd':str(self.repo)}},
                             {'type':'response_item','timestamp':'t3','payload':{'type':'message','role':'assistant','content':[{'type':'output_text','text':'Added tests'}]}}])
        self.roots = patch.object(a, 'roots', side_effect=lambda source:[self.claude_root if source=='claude' else self.codex_root])
        self.roots.start(); self.addCleanup(self.roots.stop)

    def write(self, path, records):
        with open(path, 'ab') as f:
            for x in records: f.write((json.dumps(x)+'\n').encode())

    def test_real_record_shapes_and_discovery(self):
        self.assertEqual(a.choose('claude', self.repo, self.cid)['id'], self.cid)
        self.assertEqual(a.choose('codex', self.repo, self.xid)['id'], self.xid)
        self.assertEqual(a.parse_claude(json.loads(self.cp.read_text().splitlines()[1]))[-1]['text'], 'main.py')
        self.assertEqual(a.parse_codex(json.loads(self.xp.read_text().splitlines()[1]))[0]['text'], 'Added tests')

    def test_partial_tail_incremental_and_no_source_mutation(self):
        before = self.cp.read_bytes()
        with self.cp.open('ab') as f: f.write(b'{"type":"user"')
        a.initialize(self.repo, self.cid, self.xid)
        state, events = a.sync(self.repo)
        self.assertEqual(state['agents']['claude']['offset'], len(before))
        self.assertEqual(self.cp.read_bytes(), before+b'{"type":"user"')
        self.assertEqual(a.sync(self.repo)[1], [])
        with self.cp.open('ab') as f: f.write(b',"sessionId":"x","message":{"content":"next"}}\n')
        self.assertEqual(len(a.sync(self.repo)[1]), 1)

    def test_delta_and_persistence(self):
        a.initialize(self.repo, self.cid, self.xid)
        a.sync(self.repo)
        state, body, end = a.handoff(self.repo, 'codex')
        self.assertIn('Build a thing', body)
        self.assertNotIn('Added tests', body)
        state['seen_by']['codex'] = end
        a.save_state(self.repo, state)
        self.write(self.cp, [{'type':'assistant','sessionId':self.cid,'cwd':str(self.repo),'message':{'content':[{'type':'text','text':'New Claude decision'}]}}])
        _, body, _ = a.handoff(self.repo, 'codex')
        self.assertIn('New Claude decision', body)
        self.assertNotIn('Build a thing', body)
        _, reverse, _ = a.handoff(self.repo, 'claude')
        self.assertIn('Added tests', reverse)
        self.assertNotIn('New Claude decision', reverse)
        self.assertEqual(a.load(a.state_dir(self.repo)/'state.json')['seen_by']['codex'], end)

    def test_missing_corrupt_and_ambiguous(self):
        a.initialize(self.repo, self.cid, self.xid)
        with self.cp.open('ab') as f: f.write(b'not json\n')
        a.sync(self.repo)
        self.cp.unlink()
        with self.assertRaisesRegex(RuntimeError, 'missing'):
            a.sync(self.repo)
        self.write(self.cp, [{'type':'user','sessionId':self.cid,'cwd':str(self.repo),'message':{'content':'hi'}}])
        other = self.claude_root / 'other.jsonl'
        self.write(other, [{'type':'user','sessionId':'other','cwd':str(self.repo),'message':{'content':'hi'}}])
        with self.assertRaisesRegex(RuntimeError, 'Multiple'):
            a.choose('claude', self.repo)

    def test_repo_match_and_git_dirty(self):
        other = self.root / 'other'; other.mkdir(); subprocess.run(['git','-C',str(other),'init','-q'],check=True)
        self.assertEqual(a.discover('claude', other), [])
        (self.repo/'new.txt').write_text('dirty')
        self.assertIn('new.txt', a.git_snapshot(self.repo)['status'])

    def test_bootstrap_echo_and_tool_results(self):
        prompt = a.BOOTSTRAP + ' Read handoff'
        self.assertEqual(a.parse_codex({'type':'response_item','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':prompt}]}}), [])
        self.assertEqual(a.parse_claude({'type':'user','message':{'content':prompt}}), [])
        result = a.parse_claude({'type':'user','message':{'content':[{'type':'tool_result','is_error':True,'content':'error: tests failed'}]}})
        self.assertEqual(result[0]['kind'], 'error')
        result = a.parse_codex({'type':'response_item','payload':{'type':'custom_tool_call_output','output':[{'type':'text','text':'6 tests passed'}]}})
        self.assertEqual(result[0]['kind'], 'result')

    def test_git_diff_capture(self):
        (self.repo/'tracked.txt').write_text('before\n')
        subprocess.run(['git','-C',str(self.repo),'add','tracked.txt'],check=True)
        (self.repo/'tracked.txt').write_text('after\n')
        snapshot = a.git_snapshot(self.repo)
        self.assertIn('tracked.txt', snapshot['changed'])
        self.assertIn('+after', snapshot['patch'])
        self.assertIn('tracked.txt', snapshot['diff'])

    def test_parent_directory_lists_and_resolves_one_pairing(self):
        a.initialize(self.repo, self.cid, self.xid)
        self.assertEqual([repo for repo, _ in a.find_pairings(self.root)], [self.repo.resolve()])
        with patch.object(a, 'known_pairings', return_value=a.find_pairings(self.root)):
            self.assertEqual(a.resolve_repo(self.root), self.repo.resolve())
            self.assertEqual(a.resolve_repo(self.root, explicit=True), self.root.resolve())
            with patch('builtins.print') as printed:
                a.list_pairings(self.root)
            self.assertTrue(printed.called)

    def test_pairings_are_scoped_to_current_workspace(self):
        a.initialize(self.repo, self.cid, self.xid)
        other_workspace = self.root / 'other-workspace'
        other_workspace.mkdir()
        self.assertEqual(a.known_pairings(self.root)[0][0], self.repo.resolve())
        self.assertEqual(a.known_pairings(other_workspace), [])

    def test_claude_transcripts_follow_config_dir(self):
        custom = self.root / 'custom-claude'
        with patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': str(custom)}):
            self.assertEqual(original_roots('claude'), [custom / 'projects'])

    def test_parent_directory_requires_selection_for_multiple_pairings(self):
        second = self.root / 'second'; second.mkdir()
        other = (second, {'repo': str(second), 'agents': {}})
        with patch.object(a, 'known_pairings', return_value=[(self.repo, {}), other]):
            with self.assertRaisesRegex(RuntimeError, 'Multiple paired repositories'):
                a.resolve_repo(self.root)

    def test_codex_tool_shape(self):
        e = a.parse_codex({'type':'response_item','payload':{'type':'function_call','name':'exec_command','arguments':'{"cmd":"pytest -q"}'}})
        self.assertEqual(e[0]['text'], 'pytest -q')
        self.assertEqual(a.parse_claude({'type':'user','message':{'content':[{'type':'tool_result','content':'huge output'}]}}), [])

if __name__ == '__main__':
    unittest.main()

import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import qa_common as common
import release


def load_pty():
    spec = importlib.util.spec_from_file_location('qa_pty_tests', Path(__file__).resolve().parents[1]/'qa-pty.py')
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class GateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = self.root/'repo'; self.repo.mkdir()
        (self.repo/'init.zsh').write_text(':\n')
        self.work = self.root/'run'; self.work.mkdir()
        (self.work/'scratch').mkdir()
        common.atomic_json(self.work/common.MARKER, dict(id='selftest',work=str(self.work),repo=str(self.repo)))
        self.env = patch.dict(os.environ, {'QA_WORK_DIR':str(self.work), 'ZSH_CONFIG_DIR':str(self.repo),
                                          'QA_RESULTS_FILE':str(self.work/'results.jsonl')})
        self.env.start(); self.addCleanup(self.env.stop)

    def test_false_command_is_failure(self):
        self.assertEqual(common.run_case('intentional-false', 'false'), 1)
        self.assertEqual(json.loads((self.work/'results.jsonl').read_text())['exit_code'], 1)

    def test_pipeline_failure_is_not_masked(self):
        self.assertEqual(common.run_case('pipeline', 'false | cat'), 1)

    def test_empty_output_rejected(self):
        self.assertEqual(common.run_case('empty', ':', nonempty=True), 1)

    def test_stderr_is_not_success_output(self):
        self.assertEqual(common.run_case('stderr', 'print -u2 diagnostic', nonempty=True), 1)

    def test_startup_diagnostic_rejected_even_exit_zero(self):
        (self.repo/'init.zsh').write_text('print -u2 startup-broke\ntrue\n')
        self.assertEqual(common.run_case('startup-error', 'true'), 1)

    def test_startup_nonzero_rejected(self):
        (self.repo/'init.zsh').write_text('return 7\n')
        self.assertEqual(common.run_case('startup-code', 'true'), 1)

    def test_case_timeout_is_failure(self):
        with patch.dict(os.environ, {'QA_CASE_TIMEOUT':'1'}):
            self.assertEqual(common.run_case('timeout', 'sleep 30'), 1)
        self.assertEqual(json.loads((self.work/'results.jsonl').read_text())['exit_code'],124)

    def test_bounded_kills_grandchildren(self):
        pidfile=self.root/'pid'
        p=common.bounded(['sh','-c',f'sleep 30 & echo $! > "{pidfile}"; wait'],timeout=.3)
        self.assertEqual(p.returncode,124)
        self.assertIsNone(common.process_identity(int(pidfile.read_text())))

    def test_work_marker_required(self):
        (self.work/common.MARKER).unlink()
        with self.assertRaises(ValueError):common.verify_work(self.work,self.repo)

    def test_work_marker_target_must_match(self):
        with self.assertRaises(ValueError):common.verify_work(self.work,self.root/'other')

    def test_work_symlink_rejected(self):
        link=self.root/'linked';link.symlink_to(self.work)
        with self.assertRaises(ValueError):common.verify_work(link,self.repo)

    def test_target_cannot_be_run_state(self):
        common.atomic_json(self.repo/common.MARKER,dict(work=str(self.repo),repo=str(self.repo)))
        with self.assertRaises(ValueError):common.verify_work(self.repo,self.repo)

    def test_isolation_replaces_inherited_settings(self):
        env=common.clean_env(self.work/'home',self.repo,dict(HOME='/real',XDG_DATA_HOME='/real',NO_COLOR='1',
            TERM='dumb',FZF_DEFAULT_OPTS='--bad',ZSH_UI_THEME='bad',DBUS_SESSION_BUS_ADDRESS='keep',PYTHONOPTIMIZE='1'))
        self.assertNotIn('NO_COLOR',env);self.assertNotIn('FZF_DEFAULT_OPTS',env)
        self.assertNotIn('PYTHONOPTIMIZE',env)
        self.assertEqual(env['HOME'],str(self.work/'home'))
        self.assertEqual(env['TERM'],'xterm-256color')
        self.assertEqual(env['DBUS_SESSION_BUS_ADDRESS'],'keep')

    def test_wrong_config_symlink_rejected(self):
        home=self.work/'home';common.make_home(home,self.repo)
        with self.assertRaises(ValueError):common.make_home(home,self.root/'wrong')

    def test_missing_case_cannot_pass(self):
        p=self.root/'r';p.write_text('{"name":"one","status":"pass"}\n')
        with self.assertRaises(ValueError):release.read_results(p,['one','two'])

    def test_duplicate_case_cannot_pass(self):
        p=self.root/'r';p.write_text('{"name":"one","status":"pass"}\n'*2)
        with self.assertRaises(ValueError):release.read_results(p,['one'])

    def test_malformed_case_cannot_pass(self):
        p=self.root/'r';p.write_text('not json')
        with self.assertRaises(ValueError):release.read_results(p,['one'])

    def test_invalid_status_cannot_pass(self):
        p=self.root/'r';p.write_text('{"name":"one","status":"probably"}\n')
        with self.assertRaises(ValueError):release.read_results(p,['one'])

    def test_verdict_fails_closed(self):
        good=[dict(status='pass')]
        self.assertEqual(release.decide(good,True,True,True,True),('YES',0))
        for full,unchanged,clean,cleanup in [(False,True,True,True),(True,True,False,True)]:
            self.assertEqual(release.decide(good,full,unchanged,clean,cleanup)[0],'INCOMPLETE')
        for full,unchanged,clean,cleanup in [(True,False,True,True),(True,True,True,False)]:
            self.assertEqual(release.decide(good,full,unchanged,clean,cleanup)[0],'NO')
        self.assertEqual(release.decide([dict(status='incomplete')],True,True,True,True)[0],'INCOMPLETE')
        self.assertEqual(release.decide([dict(status='fail')],True,True,True,True)[0],'NO')

    def test_profile_errors_are_not_empty_profiles(self):
        pty=load_pty()
        with patch.object(pty,'nix_run',return_value=subprocess.CompletedProcess([],1,'','daemon down')):
            with self.assertRaises(AssertionError):pty.profile_elements()
        for value in ['bad json','{}','{"elements":[]}']:
            with patch.object(pty,'nix_run',return_value=subprocess.CompletedProcess([],0,value,'')):
                with self.assertRaises((AssertionError,ValueError)):pty.profile_elements()

    def test_pty_assertion_cannot_pass_from_echo(self):
        pty=load_pty();home=self.work/'home'
        common.make_home(home,self.repo,'PROMPT="QA> "\nbindkey -e\n')
        with patch.object(pty,'WORK',self.work),patch.object(pty,'REPO',self.repo):
            session=pty.Session(self.work/'scratch')
            self.addCleanup(session.close)
            session.sync()
            with self.assertRaises(AssertionError):session.check('false')
            self.assertEqual(session.last_status,1)
            session.check('true')

    def test_fingerprint_changes_on_uncommitted_edit(self):
        subprocess.run(['git','init','-q',str(self.repo)],check=True)
        subprocess.run(['git','-C',str(self.repo),'add','.'],check=True)
        subprocess.run(['git','-C',str(self.repo),'-c','user.name=QA','-c','user.email=qa@example.invalid',
                        '-c','commit.gpgsign=false','commit','-qm','fixture'],check=True)
        before=release.snapshot(self.repo)
        (self.repo/'init.zsh').write_text('false\n')
        after=release.snapshot(self.repo)
        self.assertFalse(before['dirty']);self.assertTrue(after['dirty'])
        self.assertNotEqual(before['sha256'],after['sha256'])

    def test_generated_credentials_are_valid_and_unique(self):
        import re
        a=common.credential_name('a1b2c3');b=common.credential_name('a1b2c3')
        self.assertRegex(a,r'^[A-Z_][A-Z0-9_]*$')
        self.assertTrue(a.startswith('QA_A1B2C3_'))
        self.assertNotEqual(a,b)

    def test_cleanup_refuses_unrelated_credential(self):
        (self.work/'credentials.jsonl').write_text('{"name":"USER_SECRET"}\n')
        with patch.object(release,'bounded') as backend:
            self.assertTrue(release.cleanup(self.work))
            backend.assert_not_called()

    def test_cleanup_backend_failure_is_not_success(self):
        (self.work/'credentials.jsonl').write_text('{"name":"QA_SELFTEST_ABC"}\n')
        with patch.object(release,'bounded',return_value=subprocess.CompletedProcess([],1,'','service down')):
            self.assertTrue(release.cleanup(self.work))

    def test_cleanup_does_not_signal_reused_pid(self):
        (self.work/'processes.jsonl').write_text(json.dumps({'pid':12345,'start':'old'})+'\n')
        with patch.object(release,'process_identity',return_value='different'),patch.object(release.os,'killpg') as kill:
            self.assertEqual(release.cleanup(self.work),[])
            kill.assert_not_called()

    def test_cleanup_tolerates_malformed_registry_entries(self):
        (self.work/'processes.jsonl').write_text('[]\n{"pid": 1}\n"junk"\n42\n')
        self.assertEqual(len(release.cleanup(self.work)), 4)

    def test_cleanup_reports_unverified_leaderless_group(self):
        leader = subprocess.Popen(['sh','-c','sleep 300 >/dev/null 2>&1 & echo $! > survivor.pid; exit 0'],
                                  cwd=self.root, preexec_fn=os.setsid)
        survivor = None
        for _ in range(100):
            try:
                survivor = int((self.root/'survivor.pid').read_text())
                break
            except (OSError, ValueError):
                time.sleep(0.05)
        self.assertIsNotNone(survivor, 'survivor did not start')
        leader.wait()
        pgid = os.getpgid(survivor)
        (self.work/'processes.jsonl').write_text(json.dumps({'pid': pgid, 'start': 'unrecordable'})+'\n')
        try:
            errors = release.cleanup(self.work)
            self.assertTrue(any('unverified' in e and str(pgid) in e for e in errors), errors)
            self.assertTrue(os.path.exists(f'/proc/{survivor}'), 'survivor must not be killed on unproven identity')
        finally:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.kill(survivor, signal.SIGKILL)
            except ProcessLookupError:
                pass

    def test_cleanup_rejects_unusable_path_cleanly(self):
        with self.assertRaises(OSError):
            release.validate_cleanup_target(self.root/'missing-run')
        (self.work/common.MARKER).write_text('not json')
        with self.assertRaises(ValueError):
            release.validate_cleanup_target(self.work)

    def test_atomic_json_is_durable_and_honors_mode(self):
        import qa_common as common_module
        target = self.root/'report.json'
        calls = []
        real_fsync = os.fsync
        def counting_fsync(fd):
            calls.append(fd)
            return real_fsync(fd)
        with patch('os.fsync', counting_fsync):
            common_module.atomic_json(target, {'verdict': 'INCOMPLETE'}, mode=0o600)
        self.assertGreaterEqual(len(calls), 2, 'file and directory must be fsynced')
        self.assertEqual(oct(target.stat().st_mode & 0o777), oct(0o600))
        self.assertEqual(json.loads(target.read_text()), {'verdict': 'INCOMPLETE'})
        self.assertFalse((self.root/'report.json.tmp').exists())

    def test_results_root_must_be_private(self):
        broad = self.root/'broad'; broad.mkdir(); broad.chmod(0o755)
        with self.assertRaises(ValueError):
            release.prepare_results_root(broad)
        private = self.root/'private'
        release.prepare_results_root(private)
        self.assertEqual(oct(private.stat().st_mode & 0o777), oct(0o700))

    def test_zero_assertion_regression_cannot_pass(self):
        item=release.run_stage('regression',['sh','-c','true'],self.work,dict(os.environ),5)
        self.assertEqual(item['status'],'fail')
        self.assertIn('no assertion',item['detail'])

    def test_stage_timeout_fails(self):
        item=release.run_stage('deliberate-timeout',['sh','-c','sleep 30'],self.work,dict(os.environ),.2)
        self.assertEqual(item['status'],'fail')
        self.assertEqual(item['detail'],'timeout')

    def test_stage_exit_zero_cannot_hide_missing_cases(self):
        item=release.run_stage('missing-ledger',['sh','-c','true'],self.work,dict(os.environ),5,['required'])
        self.assertEqual(item['status'],'fail')
        self.assertIn('evidence',item['detail'])

    def test_empty_stage_list_cannot_approve(self):
        self.assertEqual(release.decide([],True,True,True,True)[0],'INCOMPLETE')

    def test_missing_coverage_key_cannot_pass(self):
        with self.assertRaises(ValueError):
            release.validate_coverage({'safe': ['one'], 'env': [], 'fzf': ['x'], 'pty': ['y'], 'selftest': ['z']}, ['env'])
        with self.assertRaises(ValueError):
            release.validate_coverage({'safe': ['one', 'one']}, ['safe'])
        with self.assertRaises(ValueError):
            release.validate_coverage({}, ['safe'])
        self.assertEqual(release.validate_coverage({'safe': ['one', 'two']}, ['safe']), {'safe': ['one', 'two']})

    def test_empty_coverage_list_cannot_pass(self):
        with self.assertRaises(ValueError):
            release.validate_coverage({'safe': []}, ['safe'])
        with self.assertRaises(ValueError):
            release.validate_coverage({'safe': None}, ['safe'])

    def test_startup_exit_cannot_pass_case(self):
        (self.repo/'init.zsh').write_text('exit 0\n')
        self.assertEqual(common.run_case('exit-startup-trap', 'false'), 1)
        row = json.loads((self.work/'results.jsonl').read_text().splitlines()[-1])
        self.assertEqual(row['status'], 'fail')
        self.assertIn('before the case body', row['detail'])

    def test_non_dict_evidence_row_is_malformed(self):
        p=self.root/'r'; p.write_text('123\n')
        with self.assertRaises(ValueError):release.read_results(p,['one'])
        p.write_text('"x"\n[1,2]\n')
        with self.assertRaises(ValueError):release.read_results(p,['one'])
        p.write_text('{"name":["a"],"status":"pass"}\n{"name":"b","status":["pass"]}\n')
        with self.assertRaises(ValueError):release.read_results(p,['a','b'])
        with self.assertRaises(ValueError):release.read_results(p,[])
        with self.assertRaises(ValueError):release.read_results(p,['one','one'])

    def test_duplicate_expected_cannot_pass(self):
        p=self.root/'r'; p.write_text('{"name":"one","status":"pass"}\n')
        with self.assertRaises(ValueError):release.read_results(p,['one','one'])

    def test_failed_selftest_outcome_lands_in_stage_ledger(self):
        # unittest calls addFailure while the test's env patch is active; the
        # evidence row must still land in the launcher-provided stage ledger.
        import selftest as st
        class Deliberate(unittest.TestCase):
            def test_always_fails(self):
                self.fail('deliberate failure for ledger routing')
        with patch.dict(os.environ, {'QA_RESULTS_FILE': str(self.root/'bogus-other-file.jsonl')}):
            result = unittest.TextTestRunner(resultclass=st.EvidenceResult).run(
                unittest.TestSuite([Deliberate('test_always_fails')]))
        self.assertEqual(len(result.failures), 1)
        rows = [json.loads(x) for x in (self.work/'results.jsonl').read_text().splitlines()]
        row = rows[-1]
        self.assertTrue(row['name'].endswith('Deliberate.test_always_fails'), row['name'])
        self.assertEqual(row['status'], 'fail')
        self.assertIn('deliberate failure', row['detail'])
        self.assertFalse((self.root/'bogus-other-file.jsonl').exists())

    def test_snapshot_ignores_inherited_git_env(self):
        other = self.root/'other'; other.mkdir(); (other/'f.txt').write_text('x\n')
        for repo_dir in (self.repo, other):
            subprocess.run(['git','init','-q',str(repo_dir)],check=True)
            subprocess.run(['git','-C',str(repo_dir),'add','.'],check=True)
            subprocess.run(['git','-C',str(repo_dir),'-c','user.name=QA','-c','user.email=qa@example.invalid',
                            '-c','commit.gpgsign=false','commit','-qm','fixture'],check=True)
        clean = release.snapshot(self.repo)
        with patch.dict(os.environ, {'GIT_DIR': str(self.root/'other'/'.git')}):
            self.assertEqual(release.snapshot(self.repo), clean)
        with patch.dict(os.environ, {'GIT_WORK_TREE': str(self.root/'other')}):
            self.assertEqual(release.snapshot(self.repo)['sha256'], clean['sha256'])

    def test_isolation_strips_git_and_nix_redirects(self):
        env = common.clean_env(self.work/'home', self.repo, dict(
            GIT_OBJECT_DIRECTORY='/outside', GIT_ALTERNATE_OBJECT_DIRECTORIES='/outside',
            GIT_COMMON_DIR='/outside', GIT_CONFIG_KEY_0='k', GIT_CONFIG_VALUE_0='v',
            GIT_CEILING_DIRECTORIES='/x', NIX_STATE_DIR='/outside', NIX_CONFIG='x',
            NIX_PATH='nixpkgs=/outside', GIT_CONFIG_GLOBAL='/real'))
        for key in ('GIT_OBJECT_DIRECTORY','GIT_ALTERNATE_OBJECT_DIRECTORIES','GIT_COMMON_DIR',
                    'GIT_CONFIG_KEY_0','GIT_CONFIG_VALUE_0','GIT_CEILING_DIRECTORIES',
                    'NIX_STATE_DIR','NIX_CONFIG','NIX_PATH'):
            self.assertNotIn(key, env)
        self.assertEqual(env['GIT_CONFIG_GLOBAL'], '/dev/null')
        self.assertEqual(env['GIT_CONFIG_NOSYSTEM'], '1')
        self.assertNotIn('/real', env.values())

    def test_non_git_target_is_rejected_before_run_state(self):
        plain = self.root/'plain'; plain.mkdir(); (plain/'init.zsh').write_text(':\n')
        for target in (plain, self.root/'other'):
            with self.assertRaises(ValueError) as caught:
                release.require_git_checkout(target)
            self.assertIn('not a usable Git checkout', str(caught.exception))
        subprocess.run(['git','init','-q',str(plain)],check=True)
        subprocess.run(['git','-C',str(plain),'-c','user.name=QA','-c','user.email=qa@example.invalid',
                        '-c','commit.gpgsign=false','commit','-qm','x','--allow-empty'],check=True)
        self.assertIsNone(release.require_git_checkout(plain))

    def test_tool_metadata_reports_tool_errors_instead_of_raising(self):
        env = common.clean_env(self.work/'home', self.repo)
        with patch.object(release.shutil, 'which', return_value=str(self.root/'missing-tool')):
            meta = release.tool_metadata(env)
        self.assertIn('error', meta['fzf'])
        self.assertTrue(meta['fzf']['available'])

    def test_tool_metadata_records_first_nonempty_version_line(self):
        env = common.clean_env(self.work/'home', self.repo)
        blank = self.root/'blank-tool'; blank.write_text('#!/bin/sh\necho\nexit 0\n'); blank.chmod(0o755)
        good = self.root/'good-tool'; good.write_text('#!/bin/sh\necho; echo "VERSION 1"\nexit 0\n'); good.chmod(0o755)
        with patch.object(release.shutil, 'which', return_value=str(blank)):
            meta = release.tool_metadata(env)
        self.assertIsNone(meta['zsh']['version'])
        self.assertEqual(meta['zsh']['version_exit'], 0)
        with patch.object(release.shutil, 'which', return_value=str(good)):
            meta = release.tool_metadata(env)
        self.assertEqual(meta['zsh']['version'], ['VERSION 1'])

    def test_sighup_and_sigquit_route_to_interrupt(self):
        saved = {number: signal.getsignal(number) for number in release.INTERRUPT_SIGNALS}
        def restore():
            for number, handler in saved.items():
                signal.signal(number, handler)
        self.addCleanup(restore)
        self.assertIn(signal.SIGHUP, release.INTERRUPT_SIGNALS)
        self.assertIn(signal.SIGQUIT, release.INTERRUPT_SIGNALS)
        release.install_interrupt_handlers()
        for number in release.INTERRUPT_SIGNALS:
            self.assertIs(signal.getsignal(number), release.interrupt)
            with self.assertRaises(KeyboardInterrupt):
                release.interrupt(number, None)
        release.ignore_interrupt_signals()
        for number in release.INTERRUPT_SIGNALS:
            self.assertEqual(signal.getsignal(number), signal.SIG_IGN)

    def test_stage_interrupt_stops_owned_group(self):
        with patch.object(release.time,'sleep',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                release.run_stage('interrupted',['sh','-c','sleep 30'],self.work,dict(os.environ),5)
        rows=[json.loads(x) for x in (self.work/'processes.jsonl').read_text().splitlines()]
        self.assertTrue(rows)
        self.assertTrue(all(common.process_identity(row['pid']) is None for row in rows))

    def test_buffer_probe_reads_zle_not_prior_terminal_text(self):
        pty=load_pty();home=self.work/'home'
        common.make_home(home,self.repo,'PROMPT="QA> "\nbindkey -e\n')
        with patch.object(pty,'WORK',self.work),patch.object(pty,'REPO',self.repo):
            session=pty.Session(self.work/'scratch')
            self.addCleanup(session.close)
            session.sync()
            session.prepare_buffer_probe()
            session.check('print misleading-old-text')
            session.send('pending text')
            self.assertEqual(session.capture_buffer(),'pending text')

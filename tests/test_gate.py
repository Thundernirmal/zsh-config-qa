import importlib.util
import json
import os
from pathlib import Path
import select
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

    def test_session_check_does_not_leak_pipefail(self):
        import shlex
        pty=load_pty();home=self.work/'home'
        probe = self.work/'pipe-state'
        rc = ('PROMPT="QA> "\nbindkey -e\n'
              '__qa_pipe_probe() { print -rn -- "$options[pipefail]" > ' + shlex.quote(str(probe)) + '; }\n')
        common.make_home(home,self.repo,rc)
        with patch.object(pty,'WORK',self.work),patch.object(pty,'REPO',self.repo):
            session=pty.Session(self.work/'scratch')
            self.addCleanup(session.close)
            session.sync()
            # Baseline is captured at the shell's top level BEFORE any check()
            # runs: on a revert, the first check itself would leak the option
            # and poison any later baseline. The value itself is not asserted.
            def run_probe():
                probe.unlink(missing_ok=True)
                session.sendline('__qa_pipe_probe')
                deadline = time.monotonic() + 10
                while not probe.exists():
                    assert time.monotonic() < deadline, 'pipe probe did not run'
                    ready, _, _ = select.select([session.master], [], [], 0.05)
                    if ready:
                        session.read_available()
                return probe.read_text()
            before = run_probe()
            # The check body itself still runs under PIPE_FAIL by design.
            session.check('[[ -o pipefail ]]')
            session.check('true')
            self.assertEqual(before, run_probe(),
                             'Session.check() changed the shell top-level options')

    def test_session_diagnostics_redact_registered_values(self):
        pty=load_pty();home=self.work/'home'
        common.make_home(home,self.repo,'PROMPT="QA> "\nbindkey -e\n')
        with patch.object(pty,'WORK',self.work),patch.object(pty,'REPO',self.repo):
            session=pty.Session(self.work/'scratch')
            self.addCleanup(session.close)
            session.sync()
            session.redactions.append('qa-inert-secret-0123456789abcdef')
            session.check('print "leak attempt: qa-inert-secret-0123456789abcdef"')
            with self.assertRaises(AssertionError) as caught:
                session.wait_for('definitely-missing-marker', timeout=0.5)
            message = str(caught.exception)
            self.assertNotIn('qa-inert-secret-0123456789abcdef', message)
            self.assertIn('<redacted synthetic credential>', message)

    def test_stage_commands_run_in_the_owned_run_directory(self):
        import inspect
        source = inspect.getsource(release.run_stage)
        self.assertIn('cwd=work', source)
        self.assertNotIn('cwd=PROJECT', source)

    def test_fbr_fixture_git_setup_is_bounded(self):
        source = (Path(__file__).resolve().parents[1]/'qa-pty.py').read_text()
        start = source.index('def fbr_select')
        body = source[start:source.index('def fkill_picker')]
        self.assertIn('bounded(argv', body)
        self.assertNotIn('subprocess.run(["git"', body)

    def test_confirm_query_fails_closed_before_enter(self):
        # Real fzf in a PTY: an anchored query matching zero rows must raise
        # while the picker is still open (no Enter was ever sent).
        pty=load_pty();home=self.work/'home'
        common.make_home(home,self.repo,'PROMPT="QA> "\nbindkey -e\n')
        with patch.object(pty,'WORK',self.work),patch.object(pty,'REPO',self.repo):
            session=pty.Session(self.work/'scratch')
            self.addCleanup(session.close)
            session.sync()
            session.sendline("printf 'alpha\\nbeta\\n' | fzf")
            session.wait_for('alpha', timeout=15)
            with self.assertRaises(AssertionError) as caught:
                pty.confirm_query(session, '^zzz$', timeout=2)
            self.assertIn('never reached exactly one match', str(caught.exception))
            children = session._children()
            self.assertTrue(children, 'picker must still be running: no Enter was sent')
            session.send(b'\x1b')

    def test_qa_pty_rejects_symlinked_scratch(self):
        # Behavioral: qa-pty must refuse a symlinked scratch before any
        # session starts (a bare source-string check was evadable; 0030).
        project = Path(__file__).resolve().parents[1]
        qarepo = self.root/'qarepo'; qarepo.mkdir(); (qarepo/'init.zsh').write_text(':\n')
        qawork = self.root/'qawork'; qawork.mkdir()
        victim = self.root/'victim'; victim.mkdir()
        (qawork/'scratch').symlink_to(victim, target_is_directory=True)
        proc = subprocess.run(
            [sys.executable, str(project/'qa-pty.py')],
            capture_output=True, text=True,
            env={**os.environ, 'QA_WORK_DIR': str(qawork), 'ZSH_CONFIG_DIR': str(qarepo)})
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn('or scratch is a symlink', proc.stderr)
        self.assertEqual(list(victim.iterdir()), [], 'victim directory must be untouched')

    def test_snapshot_uses_config_neutral_git_env(self):
        # The operator's global Git configuration (e.g. core.excludesFile)
        # must not blind the target-clean check or the digest (0036).
        other = self.root/'ignore-repo'; other.mkdir(); (other/'tracked.txt').write_text('x\n')
        subprocess.run(['git','init','-q',str(other)],check=True)
        subprocess.run(['git','-C',str(other),'add','.'],check=True)
        subprocess.run(['git','-C',str(other),'-c','user.name=QA','-c','user.email=qa@example.invalid',
                        '-c','commit.gpgsign=false','commit','-qm','init'],check=True)
        (other/'untracked.tmp').write_text('x\n')
        neutral = self.root/'home-neutral'; neutral.mkdir()
        blinded = self.root/'home-blinded'; blinded.mkdir()
        (blinded/'.gitconfig').write_text(
            f'[core]\n\texcludesFile = {self.root}/global-ignore\n')
        (self.root/'global-ignore').write_text('untracked.tmp\n')
        baseline = release.snapshot(other)
        self.assertTrue(baseline['dirty'])
        for home in (neutral, blinded):
            with patch.dict(os.environ, {'HOME': str(home), 'XDG_CONFIG_HOME': str(home/'.config')}):
                state = release.snapshot(other)
            self.assertTrue(state['dirty'], f'global config blinded the dirty check (HOME={home})')
            self.assertEqual(state['sha256'], baseline['sha256'], f'digest drifted (HOME={home})')

    def test_session_init_tears_down_on_post_spawn_failure(self):
        pty=load_pty();home=self.work/'home'
        common.make_home(home,self.repo,'PROMPT="QA> "\nbindkey -e\n')
        with patch.object(pty,'WORK',self.work),patch.object(pty,'REPO',self.repo):
            seen = []
            def failing_register(pid):
                seen.append(pid)
                raise OSError('injected register_process failure')
            with patch.object(pty, 'register_process', failing_register):
                with self.assertRaises(OSError):
                    pty.Session(self.work/'scratch')
            self.assertTrue(seen, 'register_process must be reached before the failure')
            time.sleep(0.2)
            self.assertFalse(Path(f'/proc/{seen[0]}').exists(),
                             f'leaked interactive zsh {seen[0]} after constructor failure')

    def test_clean_env_owns_tmpdir(self):
        expected = f'/tmp/zsh-config-qa-{os.getuid()}-tmp'
        env = common.clean_env(self.work/'home', self.repo, dict(
            TMPDIR='/tmp/outside-tmp', TEMP='/tmp/outside-temp', TMP='/tmp/outside-tmp2'))
        for key in ('TMPDIR', 'TEMP', 'TMP'):
            self.assertEqual(env[key], expected, key)
        self.assertTrue(Path(expected).is_dir())
        self.assertEqual(oct(Path(expected).stat().st_mode & 0o777), oct(0o700))
        # the shared root lives outside any repository tree so git discovery
        # from child temp dirs cannot find the harness or target checkout
        harness = Path(__file__).resolve().parents[1]
        self.assertNotIn(harness, Path(expected).resolve().parents)

    def test_prepare_results_root_secures_created_parents(self):
        nested = self.root/'a'/'b'/'leaf'
        release.prepare_results_root(nested)
        for directory in (self.root/'a', self.root/'a'/'b', nested):
            self.assertEqual(oct(directory.stat().st_mode & 0o777), oct(0o700), str(directory))
        # a private leaf inside a caller-owned broad parent is legitimate: the
        # created leaf is 0700 and the existing parent is never chmodded
        broad = self.root/'broad'; broad.mkdir(); broad.chmod(0o755)
        release.prepare_results_root(broad/'x')
        self.assertEqual(oct((broad/'x').stat().st_mode & 0o777), oct(0o700))
        self.assertEqual(oct(broad.stat().st_mode & 0o777), oct(0o755))
        unwritable = self.root/'unwritable'; unwritable.mkdir(mode=0o500)
        with self.assertRaises(ValueError):
            release.prepare_results_root(unwritable)
        unwritable.chmod(0o700)

    def test_evidence_stage_without_inventory_fails_loudly(self):
        item = release.run_stage('safe', ['sh','-c','true'], self.work, dict(os.environ), 5, None)
        self.assertEqual(item['status'], 'fail')
        self.assertIn('missing case inventory', item['detail'])
        ledger = self.work/'safe.jsonl'
        ledger.write_text('{"name":"probe","status":"pass"}\n')
        item = release.run_stage('safe', ['sh','-c','true'], self.work, dict(os.environ), 5, ['probe'])
        self.assertEqual(item['status'], 'pass')

    def test_cleanup_command_rejects_unusable_marker_repo(self):
        def fail(message):
            raise ValueError(message)
        # A self-consistent marker naming a nonexistent repository must not
        # pass (ISSUE-0044); the wrong-repo variant of 0016 becomes exercisable.
        (self.work/common.MARKER).write_text(json.dumps(
            {'id':'x','work':str(self.work),'repo':'/nonexistent/target'}))
        with self.assertRaises(ValueError) as caught:
            release.cleanup_command(self.work, fail)
        self.assertIn('cannot clean up', str(caught.exception))
        self.assertIn('not a usable Git checkout', str(caught.exception))
        # a work directory inside a real repository is rejected by containment
        nested = self.root/'nested'; nested.mkdir()
        subprocess.run(['git','init','-q',str(nested)],check=True)
        subprocess.run(['git','-C',str(nested),'-c','user.name=QA','-c','user.email=qa@example.invalid',
                        '-c','commit.gpgsign=false','commit','-qm','x','--allow-empty'],check=True)
        nested_run = nested/'run'; nested_run.mkdir()
        (nested_run/common.MARKER).write_text(json.dumps(
            {'id':'x','work':str(nested_run),'repo':str(nested)}))
        with self.assertRaises(ValueError):
            release.cleanup_command(nested_run, fail)
        # a valid marker whose repo is a real checkout passes and cleans up
        subprocess.run(['git','init','-q',str(self.repo)],check=True)
        subprocess.run(['git','-C',str(self.repo),'-c','user.name=QA','-c','user.email=qa@example.invalid',
                        '-c','commit.gpgsign=false','commit','-qm','x','--allow-empty'],check=True)
        (self.work/common.MARKER).write_text(json.dumps(
            {'id':'x','work':str(self.work),'repo':str(self.repo)}))
        self.assertEqual(release.cleanup_command(self.work, fail), 0)

    def test_allocation_failure_leaves_a_report_or_nothing(self):
        # ISSUE-0027 reopen: a marker-write failure must not strand an
        # unmarked, reportless run directory. Drives release.main() in a
        # subprocess with the per-uid lock neutralized and a sandbox results
        # root, injecting the failure at the marker write.
        project = Path(__file__).resolve().parents[1]
        repo = self.root/'env-repo'; repo.mkdir(); (repo/'init.zsh').write_text(':\n')
        subprocess.run(['git','init','-q',str(repo)],check=True)
        subprocess.run(['git','-C',str(repo),'-c','user.name=QA','-c','user.email=qa@example.invalid',
                        '-c','commit.gpgsign=false','commit','-qm','x','--allow-empty'],check=True)
        sandbox = self.root/'results'; sandbox.mkdir()
        script = self.root/'drive.py'
        script.write_text(
            "import sys\n"
            f"sys.path.insert(0, {str(project)!r})\n"
            "import release, qa_common\n"
            "release.fcntl.flock = lambda *a, **k: None\n"
            "real = release.atomic_json\n"
            "def failing(path, value, mode=None):\n"
            "    if str(path).endswith('.qa-owned.json'):\n"
            "        raise OSError('injected marker-write failure')\n"
            "    return real(path, value, mode=mode)\n"
            "release.atomic_json = failing\n"
            "sys.argv = ['release.py', '--repo', " + repr(str(repo)) + ",\n"
            "            '--results-dir', " + repr(str(sandbox)) + "]\n"
            "try:\n"
            "    code = release.main()\n"
            "except SystemExit as exit_error:\n"
            "    code = exit_error.code\n"
            "print('ENVELOPE_RC', code)\n")
        proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                              cwd=str(project), timeout=120,
                              env={**os.environ, 'ZSH_CONFIG_DIR': str(repo)})
        self.assertIn('ENVELOPE_RC', proc.stdout, proc.stderr[-800:])
        rc = int(proc.stdout.split('ENVELOPE_RC')[1].split()[0])
        self.assertNotEqual(rc, 0, 'the allocation failure must not approve anything')
        self.assertNotIn('Traceback', proc.stderr, 'allocation failures must not traceback')
        self.assertEqual(list(sandbox.iterdir()), [],
                         'no unmarked run directory or pointer may remain')

    def test_snapshot_git_deadline_fails_fast(self):
        stub = self.root/'stub'; stub.mkdir()
        (stub/'git').write_text('#!/bin/sh\nsleep 30\n'); (stub/'git').chmod(0o755)
        with patch.object(release, 'SNAPSHOT_TIMEOUT', 2), \
                patch.dict(os.environ, {'PATH': str(stub) + os.pathsep + os.environ['PATH']}):
            start = time.monotonic()
            with self.assertRaises(RuntimeError) as caught:
                release.snapshot(self.root/'plain' if (self.root/'plain').exists() else self.work)
            elapsed = time.monotonic() - start
        self.assertLess(elapsed, 20, 'snapshot must honor its per-command deadline')
        self.assertIn('timed out', str(caught.exception))

    def test_fixture_git_steps_are_bounded(self):
        project = Path(__file__).resolve().parents[1]
        stub = self.root/'gitstub'; stub.mkdir()
        (stub/'git').write_text('#!/bin/sh\nsleep 30\n'); (stub/'git').chmod(0o755)
        fxwork = self.root/'fxwork'; fxwork.mkdir()
        (fxwork/'scratch').mkdir()
        common.atomic_json(fxwork/common.MARKER, dict(id='fx', work=str(fxwork), repo=str(self.repo)))
        start = time.monotonic()
        proc = subprocess.run(
            ['zsh', str(project/'setup-fixtures.zsh')],
            capture_output=True, text=True, cwd=str(fxwork),
            env={**os.environ, 'QA_WORK_DIR': str(fxwork), 'ZSH_CONFIG_DIR': str(self.repo),
                 'QA_FIXTURE_TIMEOUT': '2', 'PATH': str(stub) + os.pathsep + os.environ['PATH']})
        elapsed = time.monotonic() - start
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertLess(elapsed, 15, 'fixture git step must honor its deadline')
        self.assertIn('fixture step', proc.stderr)
        self.assertIn('deadline', proc.stderr)

    def test_cgm_value_check_uses_a_hash_not_the_plaintext(self):
        import ast
        source = (Path(__file__).resolve().parents[1]/'qa-pty.py').read_text()
        self.assertIn('sha256sum', source)
        tree = ast.parse(source)
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'cgm_roundtrip')
        check_args = [ast.unparse(node.args[0]) for node in ast.walk(fn)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                      and node.func.attr == 'check']
        self.assertTrue(check_args, 'cgm_roundtrip must run checks')
        for argument in check_args:
            if 'secret' in argument:
                self.assertIn('sha256sum', argument,
                              f'check body embeds the secret outside the hash comparison: {argument}')

    def test_run_case_rejects_unsafe_scratch(self):
        scratch = self.work/'scratch'
        scratch.rmdir()
        scratch.symlink_to(self.root/'victim')
        (self.root/'victim').mkdir()
        with self.assertRaises(ValueError):
            common.run_case('unsafe-scratch', 'true')

    def test_snapshot_git_calls_are_bounded(self):
        import inspect
        source = inspect.getsource(release.snapshot)
        self.assertIn("bounded(['git'", source)
        self.assertIn('timeout=SNAPSHOT_TIMEOUT', source)

    def test_coverage_is_parsed_and_validated_once_before_run_state(self):
        import inspect
        source = inspect.getsource(release.load_validated_coverage)
        self.assertIn('validate_coverage', source)
        main_source = inspect.getsource(release.main)
        self.assertIn('load_validated_coverage(PROJECT/', main_source)
        self.assertNotIn('json.loads((PROJECT', main_source.replace(
            'load_validated_coverage(PROJECT/', 'READ_ELSEWHERE(PROJECT/'))
        with self.assertRaises(ValueError):
            release.load_validated_coverage(self.root/'missing.json', ['safe'])
        (self.root/'bad.json').write_text('not json')
        with self.assertRaises(ValueError):
            release.load_validated_coverage(self.root/'bad.json', ['safe'])

    def test_scenario_session_is_closed_when_startup_fails(self):
        pty=load_pty()
        with patch.object(pty,'WORK',self.work),patch.object(pty,'REPO',self.repo):
            closed=[]
            class FailingStartup:
                def __init__(self,*args,**kwargs):pass
                def sync(self,*args,**kwargs):raise AssertionError('startup hung')
                def close(self):closed.append(True)
            for scenario in (pty.nounset_startup, pty.env_no_color):
                closed.clear()
                with patch.object(pty,'Session',FailingStartup):
                    with self.assertRaises(AssertionError):
                        scenario()
                self.assertTrue(closed, f'{scenario.__name__} leaked its session')

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

    def test_cleanup_accepts_uncaptured_start_identity(self):
        # A leader registered after it exited records `start: null`; that is a
        # known state, not a malformed entry, and must not produce a false NO.
        # Choose a provably dead pid that also owns no live process group.
        dead = None
        for candidate in range(500, 4000):
            if not Path(f'/proc/{candidate}').exists() and not release.group_members(candidate):
                dead = candidate
                break
        self.assertIsNotNone(dead, 'no dead pid without a live group found')
        (self.work/'processes.jsonl').write_text(json.dumps({'pid': dead, 'start': None})+'\n')
        self.assertEqual(release.cleanup(self.work), [])

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
        # Ceiling discovery at the sandbox root: the stage TMPDIR can legally
        # sit inside the harness repository, which would otherwise be
        # discovered as the enclosing checkout.
        with patch.object(release, 'git_env', lambda: {'GIT_CEILING_DIRECTORIES': str(self.root)}):
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

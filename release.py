#!/usr/bin/env python3
"""Local release gate. No CI or remote write operations."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import shutil
import platform
import ctypes
import subprocess
import sys
import time
import uuid

from qa_common import MARKER, atomic_json, bounded, clean_env, make_home, process_identity, verify_work

PROJECT = Path(__file__).resolve().parent
ALL_STAGES = ['selftest', 'regression', 'fixtures', 'safe', 'env', 'fzf', 'pty']


def git_env():
    """Runner-side Git environment: repository discovery cannot be redirected."""
    return {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}


def require_git_checkout(repo):
    """Validate the target is a usable Git checkout before any run state exists."""
    try:
        probe = bounded(['git', '-C', str(repo), 'rev-parse', '--verify', 'HEAD'],
                        env=git_env(), timeout=30)
    except OSError as error:
        raise ValueError(f'git is not available: {error}') from error
    if probe.returncode != 0:
        raise ValueError('not a usable Git checkout: '
                         f'{probe.stderr.strip() or f"git exit {probe.returncode}"}')


def snapshot(repo):
    def git(*args):
        p = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, check=True,
                           env=git_env())
        return p.stdout
    names = git('ls-files', '-z', '--cached', '--others', '--exclude-standard').split(b'\0')
    digest = hashlib.sha256()
    for name in sorted(set(filter(None, names))):
        path = repo / os.fsdecode(name)
        digest.update(name + b'\0')
        if path.is_symlink():
            digest.update(b'L' + os.fsencode(os.readlink(path)))
        elif path.is_file():
            digest.update(str(path.stat().st_mode).encode() + path.read_bytes())
        else:
            digest.update(b'MISSING')
    return dict(commit=git('rev-parse', 'HEAD').decode().strip(),
                dirty=bool(git('status', '--porcelain')), sha256=digest.hexdigest())


def harness_identity():
    if (PROJECT / '.git').exists():
        return snapshot(PROJECT)
    digest=hashlib.sha256()
    for path in sorted(PROJECT.rglob('*')):
        if path.is_file() and path.suffix in {'.py','.zsh','.json','.md'} and not any(part.startswith('.') for part in path.relative_to(PROJECT).parts):
            digest.update(str(path.relative_to(PROJECT)).encode()+path.read_bytes())
    return {'sha256': digest.hexdigest()}


def tool_metadata(env):
    """Probe installed tools; never fail the run over one broken binary."""
    result={}
    for tool in ['zsh','fzf','git','python3','nix','jq','secret-tool','zoxide','pacman']:
        path=shutil.which(tool, path=env.get('PATH'))
        if not path:
            result[tool]={'available':False}
            continue
        entry={'path':path,'available':True}
        try:
            file=Path(path).resolve()
            entry.update(path=str(file), sha256=hashlib.sha256(file.read_bytes()).hexdigest())
            if tool != 'secret-tool':
                probe=bounded([path,'--version'],env=env,timeout=5)
                lines=[line for line in (probe.stdout or probe.stderr).splitlines() if line.strip()]
                entry.update(version=lines[:1] or None, version_exit=probe.returncode)
        except Exception as error:
            entry['error']=f'{type(error).__name__}: {error}'
        result[tool]=entry
    return result


EVIDENCE_STAGES = {'selftest', 'safe', 'env', 'fzf', 'pty'}


def validate_coverage(coverage, selected):
    """Fail closed when a selected evidence stage has no usable inventory."""
    if not isinstance(coverage, dict):
        raise ValueError('coverage.json must contain a JSON object')
    problems = []
    for stage in selected:
        if stage not in EVIDENCE_STAGES:
            continue
        names = coverage.get(stage)
        if not isinstance(names, list) or not names:
            problems.append(f'{stage}: missing or empty inventory')
            continue
        if any(not isinstance(name, str) or not name for name in names):
            problems.append(f'{stage}: inventory contains empty or non-string names')
        if len(set(names)) != len(names):
            problems.append(f'{stage}: duplicate required case names')
    if problems:
        raise ValueError('; '.join(problems))
    return coverage


def read_results(path, expected):
    if not isinstance(expected, list) or not expected:
        raise ValueError('empty expected case inventory')
    if len(set(expected)) != len(expected):
        raise ValueError('duplicate names in expected case inventory')
    try:
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    except (OSError, ValueError) as error:
        raise ValueError(f'missing or malformed case evidence: {error}') from error
    if any(not isinstance(row, dict) for row in rows):
        raise ValueError('malformed case evidence: row is not a JSON object')
    if any(not isinstance(r.get('name'), str) or not isinstance(r.get('status'), str) for r in rows):
        raise ValueError('malformed case evidence: name and status must be strings')
    names = [r.get('name') for r in rows]
    if len(set(names)) != len(names) or set(names) != set(expected):
        raise ValueError(f'case inventory mismatch: missing={sorted(set(expected)-set(names))}, '
                         f'unexpected={sorted(set(names)-set(expected))}; duplicate={len(set(names)) != len(names)}')
    if any(r.get('status') not in {'pass', 'fail', 'skip'} for r in rows):
        raise ValueError('invalid case status')
    return rows


def decide(stages, full, unchanged, clean, cleanup_ok):
    if not cleanup_ok or not unchanged or any(s['status'] == 'fail' for s in stages):
        return 'NO', 1
    if not stages or not full or not clean or any(s['status'] != 'pass' for s in stages):
        return 'INCOMPLETE', 2
    return 'YES', 0


INTERRUPT_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT)


def interrupt(signum, frame):
    raise KeyboardInterrupt


def install_interrupt_handlers():
    """Route ordinary termination signals (including hangup) through cleanup."""
    return {number: signal.signal(number, interrupt) for number in INTERRUPT_SIGNALS}


def ignore_interrupt_signals():
    for number in INTERRUPT_SIGNALS:
        signal.signal(number, signal.SIG_IGN)


def own_descendants():
    found = []
    pending = [os.getpid()]
    while pending:
        pid = pending.pop()
        try:
            children = [int(x) for x in Path(f'/proc/{pid}/task/{pid}/children').read_text().split()]
        except OSError:
            continue
        found.extend(children)
        pending.extend(children)
    return found


def enable_subreaper():
    # Orphaned grandchildren stay owned by the gate if a shell dies early.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # Linux PR_SET_CHILD_SUBREAPER
        raise OSError(ctypes.get_errno(), 'cannot enable child subreaper')


def group_members(pgid):
    """Live same-uid processes whose process group is `pgid` (leader-agnostic)."""
    members = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry/'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) == pgid and entry.stat().st_uid == os.getuid() and fields[0] != 'Z':
                members.append(int(entry.name))
        except (OSError, IndexError, ValueError):
            continue
    return members


def cleanup(work):
    errors = []
    # Never target unrelated sessions: these are actual descendants of this runner.
    descendants = set(own_descendants())
    for pid in reversed(sorted(descendants)):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    registry = work / 'processes.jsonl'
    if registry.exists():
        for line in registry.read_text().splitlines():
            try:
                entry = json.loads(line)
                if not isinstance(entry, dict) or not isinstance(entry.get('pid'), int) \
                        or isinstance(entry.get('pid'), bool) or not isinstance(entry.get('start'), str):
                    raise ValueError(f'invalid registry entry: {line!r}')
                pid, start = entry['pid'], entry['start']
                if start and process_identity(pid) == start:
                    os.killpg(pid, signal.SIGKILL)
                elif start:
                    # The leader is gone (crash recovery); the group may still
                    # hold live members. Their identity cannot be proven from
                    # the recorded leader alone, so never signal blindly
                    # (PID-reuse protection): report the run's cleanup as
                    # unverified instead. Members that are this process's own
                    # descendants are handled by the descendant pass above.
                    members = [m for m in group_members(pid) if m not in descendants]
                    if members:
                        errors.append(f'process cleanup: recorded group {pid} has '
                                      f'unverified surviving members {members}; cleanup unverified')
            except ProcessLookupError:
                pass
            except (TypeError, ValueError, KeyError, PermissionError) as error:
                errors.append(f'process cleanup: {error}')
    credentials = work / 'credentials.jsonl'
    if credentials.exists():
        for line in credentials.read_text().splitlines():
            try:
                entry = json.loads(line)
                name = entry['name']
                if not name.startswith('QA_' + json.loads((work / MARKER).read_text())['id'].upper() + '_'):
                    raise ValueError('credential is outside this run namespace')
                # Never log values. Only the run's synthetic credential is queried.
                bounded(['secret-tool', 'clear', 'application', 'cgm', 'variable', name], timeout=20)
                check = bounded(['secret-tool', 'lookup', 'application', 'cgm', 'variable', name], timeout=20)
                if check.returncode != 1 or check.stderr.strip():
                    errors.append(f'credential cleanup could not verify absence: {name}')
            except Exception as error:
                errors.append(f'credential cleanup: {error}')
    # Reap adopted descendants after the owned groups have been stopped.
    deadline=time.monotonic()+3
    while own_descendants() and time.monotonic()<deadline:
        try:
            while os.waitpid(-1, os.WNOHANG)[0]:
                pass
        except ChildProcessError:
            pass
        time.sleep(.02)
    if own_descendants():
        errors.append('owned child processes remained after cleanup')
    return errors


def run_stage(name, argv, work, env, timeout, expected=None):
    log = work / f'{name}.log'
    results = work / f'{name}.jsonl'
    stage_env = dict(env, QA_RESULTS_FILE=str(results))
    start = time.monotonic()
    print(f'RUN  {name} (log: {log})', flush=True)
    with log.open('w') as stream:
        p = subprocess.Popen(argv, cwd=PROJECT, env=stage_env, stdin=subprocess.DEVNULL,
                             stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        # Stage groups and separately grouped PTY/case children are all registered.
        with (work / 'processes.jsonl').open('a') as registry:
            registry.write(json.dumps({'pid': p.pid, 'start': process_identity(p.pid)}) + '\n')
        timed_out = False
        next_update = start + 30
        try:
            while p.poll() is None:
                now = time.monotonic()
                if now-start > timeout:
                    timed_out = True
                    os.killpg(p.pid, signal.SIGKILL)
                    p.wait()
                    break
                if now >= next_update:
                    print(f'... {name}: {int(now-start)}s elapsed', flush=True)
                    next_update = now+30
                time.sleep(.15)
        finally:
            if p.poll() is None:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()
    status = 'pass' if p.returncode == 0 and not timed_out else 'fail'
    detail = 'timeout' if timed_out else f'exit {p.returncode}'
    rows = []
    if name == 'regression':
        assertion_count=sum(line.startswith('ok: ') for line in log.read_text(errors='replace').splitlines())
        if assertion_count == 0:
            status, detail = 'fail', 'regression runner produced no assertion evidence'
        else:
            detail += f'; {assertion_count} assertions'
    if name == 'fzf' and expected is not None:
        entries=[dict(name=line[4:],status='pass') for line in log.read_text(errors='replace').splitlines() if line.startswith('ok: ')]
        results.write_text(''.join(json.dumps(row)+'\n' for row in entries))
    if expected is not None:
        try:
            rows = read_results(results, expected)
            if any(row['status'] == 'fail' for row in rows):
                status = 'fail'
            elif status == 'pass' and any(row['status'] == 'skip' for row in rows):
                status = 'incomplete'
        except ValueError as error:
            status, detail = 'fail', str(error)
    item = dict(name=name, status=status, exit_code=p.returncode, detail=detail,
                seconds=round(time.monotonic()-start, 2), log=log.name, cases=rows)
    print(f'{status.upper():10} {name}: {detail}', flush=True)
    return item


def prepare_results_root(root: Path) -> None:
    """Create (or validate) a private results root; never broaden an existing one."""
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.stat().st_mode & 0o077:
        raise ValueError(f'results root {root} must be private to the user (chmod 700)')


def validate_cleanup_target(work: Path) -> dict:
    """Parse and verify an owned run marker for --cleanup; raise for unusable paths."""
    marker = json.loads((work/MARKER).read_text())
    verify_work(work, Path(marker['repo']))
    return marker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stages', nargs='*', choices=ALL_STAGES)
    parser.add_argument('--repo', type=Path, default=Path(os.environ.get('ZSH_CONFIG_DIR', str(Path.home()/'.config/zsh'))))
    parser.add_argument('--results-dir', type=Path, default=PROJECT/'.runs')
    parser.add_argument('--repeat', type=int, default=2, help='repeat the entire PTY matrix (default: 2)')
    parser.add_argument('--stage-timeout', type=int, default=1800)
    parser.add_argument('--offline', action='store_true', help='skip network checks; verdict cannot be YES')
    parser.add_argument('--cleanup', type=Path, help='retry cleanup for an interrupted owned run')
    args = parser.parse_args()
    lock_path = Path('/tmp') / f'zsh-config-qa-{os.getuid()}.lock'
    lock_fd = os.open(lock_path, os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW, 0o600)
    lock = os.fdopen(lock_fd, 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        parser.error('another local QA gate is running')
    enable_subreaper()
    if args.cleanup:
        work = args.cleanup.absolute()
        try:
            validate_cleanup_target(work)
        except (OSError, ValueError, KeyError, TypeError) as error:
            parser.error(f'cannot clean up {work}: {error}')
        errors = cleanup(work)
        print('\n'.join(errors) if errors else 'Cleanup verified.')
        return 1 if errors else 0
    if args.repeat < 1 or args.stage_timeout < 1:
        parser.error('repeat and timeout must be positive')
    repo = args.repo.resolve()
    if not (repo/'init.zsh').is_file():
        parser.error('target does not contain init.zsh')
    try:
        require_git_checkout(repo)
    except ValueError as error:
        parser.error(str(error))
    selected = args.stages or ALL_STAGES
    if len(set(selected)) != len(selected):
        parser.error('duplicate stages are not allowed')
    if set(selected) & {'safe','env','pty'} and 'fixtures' not in selected:
        selected = ['fixtures', *selected]
    selected = [s for s in ALL_STAGES if s in selected]
    # Validate the inventory before any run state (dir/marker) can be created.
    try:
        coverage = json.loads((PROJECT/'coverage.json').read_text())
        validate_coverage(coverage, selected)
    except (OSError, ValueError) as error:
        parser.error(f'coverage inventory invalid: {error}')
    # A leftover QA_WORK_DIR would make bounded() register probes into a
    # foreign run's ledger before this gate allocates its own.
    os.environ.pop('QA_WORK_DIR', None)
    before = snapshot(repo)
    # One local gate at a time, even when separate results roots are requested.
    root = args.results_dir.resolve()
    if root == repo or root in repo.parents or repo in root.parents:
        parser.error('results must live outside the target repository')
    try:
        prepare_results_root(root)
    except OSError as error:
        parser.error(f'cannot create results root {root}: {error}')
    except ValueError as error:
        parser.error(str(error))
    ident = uuid.uuid4().hex
    work = root / (time.strftime('%Y%m%d-%H%M%S')+'-'+ident[:8])
    work.mkdir(mode=0o700)
    atomic_json(work/MARKER, dict(id=ident, work=str(work), repo=str(repo)))
    verify_work(work, repo)
    os.environ['QA_WORK_DIR'] = str(work)
    home = work/'home'; make_home(home, repo)
    env = clean_env(home, repo)
    env.update(QA_WORK_DIR=str(work), QA_RUN_ID=ident)
    env.pop('QA_LIBRARY_ONLY', None)
    if args.offline:
        env['QA_SKIP_NETWORK'] = '1'
    else:
        env.pop('QA_SKIP_NETWORK', None)
    # Inventory already validated before run state existed; keep the parse here
    # only for the shared coverage object.
    coverage = json.loads((PROJECT/'coverage.json').read_text())
    stages = []
    interrupted = False
    report = dict(schema=1, verdict='INCOMPLETE', repo=str(repo), target=before,
                  harness=harness_identity(), machine=platform.platform(), tools=tool_metadata(env),
                  repeat=args.repeat, stages=stages, cleanup_errors=[])
    atomic_json(work/'report.json', report)
    install_interrupt_handlers()
    commands = {
        'selftest': [sys.executable, str(PROJECT/'selftest.py')],
        'regression': ['zsh', str(repo/'scripts/run-tests.zsh')],
        'fixtures': ['zsh', str(PROJECT/'setup-fixtures.zsh')],
        'safe': ['zsh', str(PROJECT/'run-safe.zsh')],
        'env': ['zsh', str(PROJECT/'run-env.zsh')],
        'fzf': [sys.executable, str(repo/'scripts/test-fzf-pty.py')],
        'pty': [sys.executable, str(PROJECT/'qa-pty.py')],
    }
    print(f'Target: {repo} @ {before["commit"]}\nEvidence: {work}', flush=True)
    try:
        for name in selected:
            count = args.repeat if name == 'pty' else 1
            for iteration in range(count):
                label = f'pty-{iteration+1}' if name == 'pty' else name
                result = run_stage(label, commands[name], work, env, args.stage_timeout, coverage.get(name))
                stages.append(result)
                atomic_json(work/'report.json', report)
                if result['status'] == 'fail':
                    # Do not run dependent stages on failed fixtures or a broken harness.
                    if name in {'selftest','fixtures'}:
                        raise RuntimeError(f'{name} failed; dependent stages blocked')
                    errors = cleanup(work)
                    if errors:
                        raise RuntimeError('; '.join(errors))
    except KeyboardInterrupt:
        interrupted = True
        stages.append(dict(name='interrupted', status='incomplete', detail='signal received'))
    except Exception as error:
        stages.append(dict(name='runner', status='fail', detail=str(error)))
    finally:
        ignore_interrupt_signals()
        errors = cleanup(work)
        report['cleanup_errors'] = errors
        try:
            after = snapshot(repo)
            unchanged = after == before
            report['target_after'] = after
        except Exception as error:
            unchanged = False
            report['snapshot_error'] = str(error)
        full = selected == ALL_STAGES and args.repeat >= 2 and not args.offline and not interrupted and len(stages) == len(ALL_STAGES)-1+args.repeat
        verdict, rc = decide(stages, full, unchanged, not before['dirty'], not errors)
        report['harness_after'] = harness_identity()
        if report['harness_after'] != report['harness']:
            verdict, rc = 'NO', 1
            report['harness_changed'] = True
        report.update(verdict=verdict, full_coverage=full, target_unchanged=unchanged)
        atomic_json(work/'report.json', report)
        lines = [f'# Release verdict: {verdict}', '', f'Target: `{before["commit"]}`', '',
                 '| Stage | Result | Evidence |', '|---|---|---|']
        for stage in stages:
            lines.append(f'| {stage["name"]} | {stage["status"]} | [{stage.get("log", "details")}]({stage.get("log", "report.json")}) |')
        lines += ['', 'YES requires a clean, unchanged target, all required cases, no skips, and verified cleanup.',
                  'Scope: local GNU/Linux and installed tools. System-wide package mutations and human font/contrast judgment are excluded.',
                  '', 'Cleanup: '+ ('; '.join(errors) if errors else 'verified'), '',
                  'See report.json for case-level evidence, target fingerprint, harness revision, and exact failures.']
        (work/'report.md').write_text('\n'.join(lines)+'\n')
        atomic_json(root/'latest.json', {'run':str(work),'verdict':verdict}, mode=0o600)
        print(f'\nRELEASE: {verdict}\nReport: {work / "report.md"}', flush=True)
    return rc

if __name__ == '__main__':
    sys.exit(main())

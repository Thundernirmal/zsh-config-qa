#!/usr/bin/env python3
"""Shared isolation, bounded processes, and machine-readable case evidence."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid

MARKER = '.qa-owned.json'


def atomic_json(path: Path, value, mode: int | None = None) -> None:
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'w') as stream:
        stream.write(json.dumps(value, indent=2) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    if mode is not None:
        os.chmod(tmp, mode)
    tmp.replace(path)
    dir_fd = os.open(path.parent, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def verify_work(work: Path, repo: Path) -> dict:
    if not work.is_absolute() or work.is_symlink() or work.resolve() != work:
        raise ValueError('QA_WORK_DIR must be an absolute, non-symlink owned run directory')
    for ancestor in (work, *work.parents):
        if ancestor.is_symlink():
            raise ValueError('symlink ancestor in QA_WORK_DIR')
    marker = work / MARKER
    if marker.is_symlink() or not marker.is_file() or marker.stat().st_uid != os.getuid():
        raise ValueError('missing owned run marker; use ./run-all.zsh')
    data = json.loads(marker.read_text())
    if data.get('work') != str(work) or data.get('repo') != str(repo.resolve()):
        raise ValueError('run marker does not match work directory and target')
    if work == repo.resolve() or work in repo.resolve().parents or repo.resolve() in work.parents:
        raise ValueError('run state must be outside the target repository')
    return data


def make_home(home: Path, repo: Path, rc: str | None = None) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / '.config').mkdir(exist_ok=True)
    link = home / '.config/zsh'
    if link.is_symlink():
        if link.resolve() != repo.resolve():
            raise ValueError('isolated HOME points at a different target')
    elif link.exists():
        raise ValueError('refusing to replace an existing config directory')
    else:
        link.symlink_to(repo.resolve())
    if rc is None:
        rc = ('autoload -Uz compinit; compinit -i -d "$HOME/.zcompdump"\n'
              'source "$HOME/.config/zsh/init.zsh"\n'
              'PROMPT="QA> "\nRPROMPT=""\nbindkey -e\n')
    (home / '.zshrc').write_text(rc)


def clean_env(home: Path, repo: Path, base=None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    for key in list(env):
        # Strip the whole Git/Nix redirect families, not just the common names:
        # GIT_OBJECT_DIRECTORY, GIT_COMMON_DIR, GIT_CONFIG_*, NIX_STATE_DIR, etc.
        if key.startswith(('FZF_', 'ZSH_UI_', 'ZSH_FZF_', 'CGM_', '_ZO_', '_ZSH_', 'QA_TEST_',
                           'ZSH_HTTP_', 'GIT_', 'NIX_')) or key in {
            'NO_COLOR', 'NO_NERD_FONT', 'ZSH_GLOBAL_ALIASES', 'ZDOTDIR', 'ENV', 'BASH_ENV',
            'PYTHONOPTIMIZE', 'PYTHONPATH', 'PYTHONHOME',
        }:
            env.pop(key, None)
    env.update(HOME=str(home), ZDOTDIR=str(home), XDG_CONFIG_HOME=str(home / '.config'),
               XDG_CACHE_HOME=str(home / '.cache'), XDG_DATA_HOME=str(home / '.local/share'),
               XDG_STATE_HOME=str(home / '.local/state'), TERM='xterm-256color',
               COLORTERM='truecolor', LANG='C.UTF-8', LC_ALL='C.UTF-8',
               ZSH_CONFIG_DIR=str(repo.resolve()), GIT_CONFIG_GLOBAL='/dev/null',
               GIT_CONFIG_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0', PYTHONUNBUFFERED='1',
               PYTHONDONTWRITEBYTECODE='1')
    # Retain the session bus and XDG_RUNTIME_DIR for the real Secret Service.
    return env


def credential_name(run_id):
    return 'QA_' + run_id.upper() + '_' + uuid.uuid4().hex[:12].upper()


def process_identity(pid):
    try:
        fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        return fields[19] if fields[0] != 'Z' else None
    except (OSError, IndexError):
        return None


def register_process(pid):
    work = os.environ.get('QA_WORK_DIR')
    if work:
        with open(Path(work) / 'processes.jsonl', 'a') as stream:
            stream.write(json.dumps({'pid': pid, 'start': process_identity(pid)}) + '\n')


def bounded(argv, *, env=None, cwd=None, timeout=120, input=None, text=True):
    """Kill the whole owned process group on timeout, including grandchildren."""
    process = subprocess.Popen(argv, env=env, cwd=cwd,
                               stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=text,
                               start_new_session=True)
    register_process(process.pid)
    try:
        stdout, stderr = process.communicate(input, timeout=timeout)
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        stdout, stderr = process.communicate()
        marker = '\nQA: process timed out\n' if text else b'\nQA: process timed out\n'
        return subprocess.CompletedProcess(argv, 124, stdout, stderr + marker)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise


def record(name, status, *, detail='', duration=0, destination=None, **extra):
    item = dict(name=name, status=status, detail=detail, duration_seconds=round(duration, 3), **extra)
    resolved = destination or os.environ.get('QA_RESULTS_FILE')
    if not resolved:
        raise RuntimeError('QA_RESULTS_FILE missing; use ./run-all.zsh')
    with open(resolved, 'a') as stream:
        stream.write(json.dumps(item) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    print(f'{status.upper():10} {name}' + (f': {detail}' if detail else ''), flush=True)
    return item


def run_case(name, code, envs='', nonempty=False):
    repo = Path(os.environ['ZSH_CONFIG_DIR']).resolve()
    work = Path(os.environ['QA_WORK_DIR'])
    verify_work(work, repo)
    scratch = work / 'scratch'
    if scratch.is_symlink() or not scratch.is_dir():
        raise ValueError('owned scratch fixture directory is missing or is a symlink')
    home = work / 'home'
    make_home(home, repo)
    env = clean_env(home, repo)
    for assignment in shlex.split(envs):
        key, value = assignment.split('=', 1)
        env[key] = value
    token = re.sub(r'[^a-zA-Z0-9_-]+', '-', name).strip('-') + '-' + uuid.uuid4().hex[:8]
    cases = work / 'cases'; cases.mkdir(exist_ok=True)
    script = cases / (token + '.zsh')
    startup_stderr = cases / (token + '.startup.stderr')
    body_started = cases / (token + '.body-started')
    script.write_text(f'source {shlex.quote(str(repo / "init.zsh"))} 2>{shlex.quote(str(startup_stderr))}\n'
                      f'qa_source_rc=$?\nif (( qa_source_rc != 0 )) || [[ -s {shlex.quote(str(startup_stderr))} ]]; then\n'
                      f' command cat -- {shlex.quote(str(startup_stderr))} >&2\n exit 121\nfi\n'
                      f': > {shlex.quote(str(body_started))}\n'
                      'setopt PIPE_FAIL\n' + code + '\n')
    start = time.monotonic()
    result = bounded(['zsh', '-d', '-f', str(script)], env=env, cwd=scratch,
                     timeout=int(os.environ.get('QA_CASE_TIMEOUT', '180')))
    (cases / (token + '.stdout')).write_text(result.stdout)
    (cases / (token + '.stderr')).write_text(result.stderr)
    if not body_started.is_file():
        passed = False
        detail = 'startup exited before the case body ran (possible exit in init.zsh)'
    else:
        passed = result.returncode == 0 and (not nonempty or bool(result.stdout.strip()))
        detail = '' if passed else f'exit={result.returncode}; see cases/{token}.*'
    record(name, 'pass' if passed else 'fail', duration=time.monotonic()-start,
           detail=detail,
           exit_code=result.returncode, evidence=f'cases/{token}')
    return 0 if passed else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', choices=['verify', 'case', 'skip'])
    parser.add_argument('name', nargs='?'); parser.add_argument('code', nargs='?', default='')
    parser.add_argument('envs', nargs='?', default=''); parser.add_argument('--nonempty', action='store_true')
    args = parser.parse_args()
    if args.operation == 'verify':
        verify_work(Path(os.environ['QA_WORK_DIR']), Path(os.environ['ZSH_CONFIG_DIR']))
        return 0
    if args.operation == 'skip':
        record(args.name, 'skip', detail=args.code)
        return 0
    return run_case(args.name, args.code, args.envs, args.nonempty)

if __name__ == '__main__':
    sys.exit(main())

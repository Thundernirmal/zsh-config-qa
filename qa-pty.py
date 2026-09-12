#!/usr/bin/env python3
"""Interactive QA driver for the shared Zsh config.

Runs a real interactive zsh inside a PTY, triggers the themed fzf widgets and
commands, and asserts that the expected UI opened and the terminal returned to
a usable prompt.  This is a live QA harness: it exercises the same interactive
boundary a person would, but scripted and repeatable.

The harness is read-only with respect to the Zsh config repository.  Mutating
scenarios are confined to throwaway paths:

  * ``npkg`` scenarios use an isolated HOME with its own Nix profile.
  * ``cgm`` stores one clearly named test credential and deletes it again.
  * ``fkill-signal`` signals only a ``sleep`` process it started itself.

Usage:
    ./run-all.zsh pty              # managed fixtures, isolation, evidence, cleanup
    python3 qa-pty.py --list       # read-only scenario inventory

The runner supplies ZSH_CONFIG_DIR, QA_WORK_DIR, QA_RUN_ID, and
QA_RESULTS_FILE. Direct execution of scenarios without an owned run is rejected.
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
from pathlib import Path
import pty
import re
import select
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import termios
import time
import uuid

from qa_common import (bounded, clean_env, credential_name, make_home as create_home, record, register_process, verify_work)
from dataclasses import dataclass


PROJECT = Path(__file__).resolve().parent
REPO = Path(os.environ.get("ZSH_CONFIG_DIR") or (Path.home() / ".config" / "zsh")).resolve()
WORK = Path(os.environ.get("QA_WORK_DIR") or (PROJECT / ".work")).resolve()
SCRATCH = WORK / "scratch"
GITREPO = SCRATCH / "gitrepo"
QAHOME = WORK / "nixhome"
FAKEBIN = WORK / "fakebin"
PROBE_FILE = WORK / "env-probe.txt"

ANSI_OSC = re.compile(rb"\x1b\][^\x07]*?(?:\x07|\x1b\\)")
ANSI_CSI = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")


def strip_terminal_controls(value: bytes | bytearray) -> bytes:
    """Remove terminal drawing controls while preserving rendered text."""
    plain = ANSI_OSC.sub(b"", bytes(value))
    plain = ANSI_CSI.sub(b"", plain)
    return plain.replace(b"\x1b", b"")


def decode(value: bytes | bytearray) -> str:
    return bytes(value).decode("utf-8", errors="replace")


def set_window(fd: int, width: int, height: int = 32) -> None:
    size = struct.pack("HHHH", height, width, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, size)


class Session:
    """One interactive zsh inside a PTY."""

    def __init__(
        self,
        cwd: Path,
        home: str | None = None,
        zdotdir: str | None = None,
        width: int = 120,
        height: int = 32,
        extra_env: dict[str, str] | None = None,
    ):
        selected_home = Path(home) if home else WORK / 'home'
        if not selected_home.resolve().is_relative_to(WORK):
            raise ValueError('PTY HOME must be inside the owned run')
        if not (selected_home / '.zshrc').exists():
            create_home(selected_home, REPO)
        env = clean_env(selected_home, REPO)
        if zdotdir:
            env['ZDOTDIR'] = zdotdir
        if extra_env:
            env.update(extra_env)
        self.master, slave = pty.openpty()
        set_window(self.master, width, height)

        def child_setup() -> None:
            # Make the PTY slave the controlling terminal so zle/fzf can open
            # /dev/tty (needed by the generated fzf widgets).
            os.setsid()
            fcntl.ioctl(0, termios.TIOCSCTTY, 0)

        self.proc = subprocess.Popen(
            ["zsh", "-d", "-i"],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            env=env,
            cwd=str(cwd),
            close_fds=True,
            preexec_fn=child_setup,
        )
        register_process(self.proc.pid)
        os.close(slave)
        self.output = bytearray()
        self.counter = 0
        self.closed = False
        self.redactions = []
        self.last_status = None

    # -- low level -------------------------------------------------------
    def read_available(self) -> bool:
        try:
            chunk = os.read(self.master, 65536)
        except OSError as error:
            if error.errno == errno.EIO:
                return False
            raise
        if not chunk:
            return False
        self.output.extend(chunk)
        # fzf queries the terminal for its cursor position and bracketed-paste
        # state while entering raw mode; answer so the interaction is
        # deterministic without a real terminal emulator.
        for _ in range(chunk.count(b"\x1b[6n")):
            os.write(self.master, b"\x1b[1;1R")
        for _ in range(chunk.count(b"\x1b[?2004$p")):
            os.write(self.master, b"\x1b[?2004;1$y")
        return True

    def wait_for(self, marker: str | bytes, timeout: float = 15.0, plain: bool = True) -> None:
        if isinstance(marker, str):
            marker = marker.encode()
        deadline = time.monotonic() + timeout
        while True:
            haystack = strip_terminal_controls(self.output) if plain else bytes(self.output)
            if marker in haystack:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"timed out waiting for {marker!r}; output tail:\n"
                    f"{decode(strip_terminal_controls(self.output))[-3000:]}"
                )
            ready, _, _ = select.select([self.master], [], [], min(remaining, 0.2))
            if ready and not self.read_available():
                break
            if self.proc.poll() is not None and not ready:
                break
        raise AssertionError(
            f"process exited before {marker!r}; output tail:\n"
            f"{decode(strip_terminal_controls(self.output))[-3000:]}"
        )

    def wait_for_since(self, marker: str, offset: int, timeout: float = 10.0) -> None:
        """Wait for a marker appearing only in output written after offset."""
        marker_bytes = marker.encode()
        deadline = time.monotonic() + timeout
        while True:
            if marker_bytes in strip_terminal_controls(self.output[offset:]):
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(
                    f"timed out waiting for {marker!r} after output offset {offset}; tail:\n"
                    f"{decode(strip_terminal_controls(self.output))[-1500:]}"
                )
            ready, _, _ = select.select([self.master], [], [], min(remaining, 0.2))
            if ready and not self.read_available():
                break
            if self.proc.poll() is not None and not ready:
                break
        raise AssertionError(
            f"process exited before {marker!r} after output offset {offset}; tail:\n"
            f"{decode(strip_terminal_controls(self.output))[-1500:]}"
        )

    def wait_quiet(self, quiet: float = 0.35, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        last = time.monotonic()
        while time.monotonic() < deadline:
            ready, _, _ = select.select([self.master], [], [], 0.1)
            if ready:
                if not self.read_available():
                    return
                last = time.monotonic()
            elif time.monotonic() - last >= quiet:
                return

    def _children(self) -> list[str]:
        found = []
        pending = [str(self.proc.pid)]
        while pending:
            pid = pending.pop()
            try:
                children = Path(f'/proc/{pid}/task/{pid}/children').read_text().split()
            except OSError:
                continue
            found.extend(children)
            pending.extend(children)
        return found

    def wait_children_empty(self, timeout: float = 8.0) -> None:
        """Wait until the interactive shell has no running child (e.g. fzf)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._children():
                return
            time.sleep(0.05)
        raise AssertionError("interactive shell still has a running child process")

    def wait_no_fzf(self, timeout: float = 15.0) -> None:
        """Wait until no fzf child remains (other children may still run)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            fzf_running = False
            for pid in self._children():
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as handle:
                        if b"fzf" in handle.read():
                            fzf_running = True
                            break
                except OSError:
                    continue
            if not fzf_running:
                return
            time.sleep(0.05)
        raise AssertionError("fzf is still running")

    def wait_for_zle(self, timeout: float = 10.0) -> None:
        """Wait until zle is reading keys (the pty leaves canonical mode)."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                attrs = termios.tcgetattr(self.master)
            except termios.error:
                attrs = None
            if attrs is not None and not (attrs[3] & termios.ICANON):
                return
            if time.monotonic() >= deadline:
                raise AssertionError("timed out waiting for zle to become active")
            time.sleep(0.05)

    def send(self, data: bytes | str) -> None:
        if isinstance(data, str):
            data = data.encode()
        os.write(self.master, data)

    def sendline(self, text: str) -> None:
        self.send(text + "\r")

    def sync(self, timeout: float = 15.0) -> str:
        """Send a unique print and wait until the shell is back at the prompt.

        The command echoes the literal ``${:-SYNC}`` while the executed output
        contains ``SYNC``, so a single marker occurrence proves execution.
        Then wait for zle to take over the terminal again before returning.
        """
        self.counter += 1
        marker = f"QA-SYNC-{os.getpid()}-{self.counter}"
        self.sendline(f'print "QA-${{:-SYNC}}-{os.getpid()}-{self.counter}"')
        self.wait_for(marker, timeout=timeout)
        self.wait_for_zle(timeout=timeout)
        return marker

    def check(self, code: str, expected: int = 0, timeout: float = 30) -> str:
        token = uuid.uuid4().hex
        script = WORK / ('pty-command-' + token + '.zsh')
        script.write_text('setopt LOCAL_OPTIONS PIPE_FAIL\n' + code + '\n')
        offset = len(self.output)
        self.sendline(f'source {shlex.quote(str(script))}; qa_rc=$?; print -r -- "QA-${{:-RESULT}}-{token}:$qa_rc"')
        prefix = 'QA-RESULT-' + token + ':'
        self.wait_for_since(prefix, offset, timeout)
        text = decode(strip_terminal_controls(self.output[offset:]))
        match = re.search(re.escape(prefix) + r'([0-9]+)', text)
        assert match is not None, 'missing executed command status'
        self.last_status = int(match.group(1))
        assert self.last_status == expected, f'command exited {self.last_status}, expected {expected}'
        self.wait_for_zle(timeout)
        return text

    def prepare_buffer_probe(self):
        self.buffer_probe = WORK / ('zle-buffer-' + uuid.uuid4().hex)
        self.check('__qa_buffer_probe() { print -rn -- "$BUFFER" > ' + shlex.quote(str(self.buffer_probe)+'.tmp') + ' && command mv -- ' + shlex.quote(str(self.buffer_probe)+'.tmp') + ' ' + shlex.quote(str(self.buffer_probe)) + '; }; '
                   'zle -N __qa_buffer_probe; bindkey "^X^B" __qa_buffer_probe')

    def capture_buffer(self):
        self.buffer_probe.unlink(missing_ok=True)
        self.send(b'\x18\x02')
        deadline=time.monotonic()+10
        while not self.buffer_probe.exists():
            assert time.monotonic()<deadline, 'ZLE buffer probe did not execute'
            ready, _, _ = select.select([self.master], [], [], .05)
            if ready:
                self.read_available()
        return self.buffer_probe.read_text()

    def text(self) -> str:
        text = decode(strip_terminal_controls(self.output))
        for value in self.redactions:
            text = text.replace(value, '<redacted synthetic credential>')
        return text

    def clear_output(self) -> None:
        self.output.clear()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
        os.close(self.master)

    def __enter__(self) -> "Session":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


@dataclass
class Result:
    name: str
    status: str  # pass | fail | skip
    detail: str = ""


RESULTS: list[Result] = []

SCENARIO_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "cgm": ("secret-tool",),
    "cgm-no-color": ("secret-tool",),
    "zi": ("zoxide",),
    "zi-select": ("zoxide",),
    "nounset-startup": ("zoxide",),
    "npkg-remove": ("nix", "jq"),
    "npkg-add": ("nix", "jq"),
}


def run(name: str, fn) -> None:
    start = time.monotonic()
    print(f"scenario {name} ...", flush=True)
    missing = [tool for tool in SCENARIO_REQUIREMENTS.get(name, ()) if shutil.which(tool) is None]
    if missing:
        detail = "missing dependency: " + ", ".join(missing)
        RESULTS.append(Result(name, "skip", detail))
        record(name, "skip", detail=detail, duration=time.monotonic()-start)
        return
    try:
        fn()
    except Exception as error:  # noqa: BLE001 - QA harness reports and continues
        RESULTS.append(Result(name, "fail", str(error)))
        record(name, "fail", detail=str(error), duration=time.monotonic()-start)
    else:
        RESULTS.append(Result(name, "pass"))
        record(name, "pass", duration=time.monotonic()-start)


def fresh_zsh() -> Session:
    session = Session(SCRATCH)
    try:
        session.sync(timeout=45)
        session.check('[[ $_ZSH_FUNCTIONS_MODULE_DIR == ' + shlex.quote(str(REPO)) + ' ]]')
    except BaseException:
        session.close()
        raise
    return session


def close_picker(session: Session, clear_line: bool = False) -> None:
    """Cancel an open fzf picker and wait for the shell to regain the prompt."""
    session.send(b"\x1b")
    try:
        session.wait_children_empty(timeout=5)
    except AssertionError:
        session.send(b"\x1b")
        session.wait_children_empty(timeout=5)
    session.wait_for_zle()
    if clear_line:
        session.send(b"\x03")  # zle send-break: clear the pending buffer
        session.wait_for_zle()
        time.sleep(0.3)  # let send-break settle before typing the next command
    session.sync(timeout=15)


def probe_shell(extra_env: dict[str, str], shell_code: str, name: str) -> str:
    """Run shell_code in an interactive session and return its file output."""
    PROBE_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROBE_FILE.unlink(missing_ok=True)
    session = Session(SCRATCH, extra_env=extra_env)
    session.sync(timeout=45)
    try:
        session.check(f"{{ {shell_code} }} > {shlex.quote(str(PROBE_FILE))}")
    finally:
        session.close()
    return PROBE_FILE.read_text()


# -- startup and fzf cache ---------------------------------------------

def startup_prompt() -> None:
    session = fresh_zsh()
    try:
        session.sync()
        assert "QA-SYNC" in session.text(), "first sync marker missing"
        text = session.text()
        assert "config: fzf" not in text, "fzf startup diagnostic appeared"
        assert "command not found" not in text, "command-not-found noise during startup"
        assert "parameter not set" not in text, "unset parameter during startup"
        session.check("(( $+functions[ztheme] && $+functions[zhelp] && $+functions[upkg] ))")
    finally:
        session.close()


def nounset_startup() -> None:
    """NO_UNSET before sourcing init must not break lazy integrations."""
    home = WORK / "nounset-home"
    make_home(home, 'setopt NO_UNSET\nsource "$HOME/.config/zsh/init.zsh"\n')
    session = Session(SCRATCH, home=str(home), zdotdir=str(home))
    session.sync(timeout=45)
    try:
        PROBE_FILE.parent.mkdir(parents=True, exist_ok=True)
        PROBE_FILE.unlink(missing_ok=True)
        session.check(
            '{ print "z=$+functions[z] zi=$+functions[zi]"; } > '
            + shlex.quote(str(PROBE_FILE))
        )
        session.sync()
        assert "parameter not set" not in session.text(), "NO_UNSET startup emitted a parameter error"
        state = PROBE_FILE.read_text().strip()
        assert "z=1" in state and "zi=1" in state, f"NO_UNSET startup lost zoxide: {state}"
    finally:
        session.close()


def fzf_cold_start() -> None:
    """An empty cache must be rebuilt and a prompt must appear cleanly."""
    cache = QAHOME / ".cache" / "zsh" / "fzf"
    shutil.rmtree(cache, ignore_errors=True)
    session = Session(SCRATCH, home=str(QAHOME), zdotdir=str(QAHOME))
    session.sync(timeout=45)
    try:
        files = sorted(cache.glob("integration-*.zsh"))
        assert files, "cold start did not create an fzf integration cache"
        text = session.text()
        assert "config: fzf" not in text, "unexpected fzf diagnostic on cold start"
    finally:
        session.close()


def fzf_warm_start() -> None:
    """A warm cache must be reused without regeneration."""
    cache = QAHOME / ".cache" / "zsh" / "fzf"
    before = {path.name: path.stat().st_mtime_ns for path in cache.glob("integration-*.zsh")}
    assert before, "warm-start check needs a cold start first"
    session = Session(SCRATCH, home=str(QAHOME), zdotdir=str(QAHOME))
    session.sync(timeout=45)
    try:
        after = {path.name: path.stat().st_mtime_ns for path in cache.glob("integration-*.zsh")}
        assert after == before, "warm start regenerated the fzf integration cache"
    finally:
        session.close()


def fzf_blocked() -> None:
    """An unsupported fzf must block pickers but not the rest of the shell."""
    FAKEBIN.mkdir(parents=True, exist_ok=True)
    fake = FAKEBIN / "fzf"
    fake.write_text('#!/bin/sh\nif [ "$1" = "--version" ]; then echo "0.67.0 (fake)"; exit 0; fi\nexit 1\n')
    fake.chmod(0o755)
    env = {"PATH": str(FAKEBIN) + os.pathsep + os.environ["PATH"]}
    session = Session(SCRATCH, extra_env=env)
    session.sync(timeout=45)
    try:
        text = session.text()
        assert "fzf 0.68.0 or newer is required" in text, "minimum-version diagnostic missing"
        session.clear_output()
        session.check("zhelp --plain package | command grep upkg")
        session.clear_output()
        session.send(b"\x14")
        time.sleep(1.0)
        assert not session._children(), "Ctrl+T opened a picker despite blocked fzf"
        session.send(b"\x03")
        session.wait_for_zle()
    finally:
        session.close()


# -- environment probes -------------------------------------------------

def env_no_color() -> None:
    text = probe_shell(
        {"NO_COLOR": "1"},
        'print "D=$FZF_DEFAULT_OPTS"; print "T=$FZF_CTRL_T_OPTS"',
        "env-no-color",
    )
    assert "--no-color" in text and "--color=never" in text, "NO_COLOR did not reach the pickers"


def env_extra_opts() -> None:
    text = probe_shell(
        {"ZSH_FZF_EXTRA_OPTS": "--no-mouse"},
        'print "D=$FZF_DEFAULT_OPTS"',
        "env-extra-opts",
    )
    assert "--no-mouse" in text, "ZSH_FZF_EXTRA_OPTS was not appended"


def env_inherited_opts() -> None:
    text = probe_shell(
        {"FZF_DEFAULT_OPTS": "--height=~60% --tabstop=4"},
        'print "D=$FZF_DEFAULT_OPTS"',
        "env-inherited-opts",
    )
    assert "--height=~60%" in text and "--tabstop=4" in text, "inherited options were dropped"
    assert "--border=rounded" in text, "managed presentation is missing"


def _env_layout(layout: str, needles: tuple[str, ...]) -> None:
    text = probe_shell(
        {"ZSH_FZF_LAYOUT": layout},
        'print "T=$FZF_CTRL_T_OPTS"; print "D=$FZF_DEFAULT_OPTS"',
        f"env-layout-{layout}",
    )
    missing = [needle for needle in needles if needle not in text]
    assert not missing, f"{layout} layout missing {missing}"


def env_layout_compact() -> None:
    _env_layout("compact", ("--height=~60%", "--padding=0,1", "50%"))


def env_layout_roomy() -> None:
    _env_layout("roomy", ("--height=80%", "--padding=1,2", "55%"))


def env_layout_minimal() -> None:
    _env_layout("minimal", ("--height=~45%", "--style=minimal", "45%"))


# -- widgets and pickers ------------------------------------------------

def ctrl_t_picker() -> None:
    session = fresh_zsh()
    try:
        session.clear_output()
        session.send(b"\x14")  # Ctrl+T
        session.wait_for("Files", timeout=15)
        session.wait_for("Type to filter files", timeout=5)
        session.send(b"\x10")  # Ctrl+P preview toggle
        session.send(b"\x1f")  # Ctrl+/ wrap toggle
        close_picker(session)
        assert "Files" in session.text(), "Files frame never rendered"
    finally:
        session.close()


def ctrl_t_insert() -> None:
    session = fresh_zsh()
    try:
        session.prepare_buffer_probe()
        session.clear_output()
        session.send(b"\x14")
        session.wait_for("Files", timeout=15)
        session.send("a.txt")
        time.sleep(0.5)
        session.send(b"\r")
        session.wait_no_fzf(timeout=8)
        session.wait_for_zle()
        words = shlex.split(session.capture_buffer())
        assert len(words) == 1 and (SCRATCH / words[0]).resolve() == (SCRATCH / 'files/a.txt').resolve(), f'wrong inserted buffer: {words}'
        session.send(b"\x03")  # clear the inserted buffer without running it
        session.wait_for_zle()
        time.sleep(0.3)
        session.sync()
    finally:
        session.close()


def ctrl_r_picker() -> None:
    session = fresh_zsh()
    try:
        session.sendline("print history-seed-command")
        session.sync()
        session.clear_output()
        session.send(b"\x12")  # Ctrl+R
        session.wait_for("History", timeout=15)
        session.send(b"\x10")  # Ctrl+P preview toggle
        session.send(b"\x1f")  # Ctrl+/ wrap toggle
        close_picker(session)
        assert "History" in session.text(), "History frame never rendered"
    finally:
        session.close()


def alt_c_picker() -> None:
    session = fresh_zsh()
    try:
        session.clear_output()
        session.send(b"\x1bc")  # Alt+C
        session.wait_for("Directories", timeout=15)
        close_picker(session)
        assert "Directories" in session.text(), "Directories frame never rendered"
    finally:
        session.close()


def completion_paths() -> None:
    session = fresh_zsh()
    try:
        session.clear_output()
        session.send("ls **")
        time.sleep(0.2)
        session.send(b"\t")
        session.wait_for("Type to filter", timeout=15)
        close_picker(session, clear_line=True)
        text = session.text()
        assert ("Paths" in text or "Files" in text or "Completions" in text), "completion frame never rendered"
    finally:
        session.close()


def zhelp_palette() -> None:
    session = fresh_zsh()
    try:
        session.clear_output()
        session.sendline("zhelp package")
        session.wait_for("Commands", timeout=15)
        session.wait_for("Usage", timeout=5)
        close_picker(session)
        assert "Commands" in session.text(), "zhelp palette never rendered"
    finally:
        session.close()


def zhelp_queue() -> None:
    session = fresh_zsh()
    try:
        session.prepare_buffer_probe()
        session.clear_output()
        session.sendline("zhelp")  # no seed query: type the target inside the palette
        session.wait_for("Commands", timeout=15)
        time.sleep(0.3)
        session.send("upkg-plan")
        time.sleep(0.5)
        session.send(b"\r")  # queue the focused example
        session.wait_no_fzf(timeout=10)
        session.wait_for_zle()
        words = shlex.split(session.capture_buffer())
        assert words[:2] == ['upkg', 'plan'], f'wrong queued buffer: {words}'
        session.send(b"\x03")  # clear the queued buffer without running it
        session.wait_for_zle()
        time.sleep(0.3)
        session.sync()
    finally:
        session.close()


def fbr_picker() -> None:
    session = Session(GITREPO)
    session.sync(timeout=45)
    try:
        session.clear_output()
        session.sendline("fbr")
        session.wait_for("Branches", timeout=20)
        close_picker(session)
        assert "Branches" in session.text(), "Branches frame never rendered"
    finally:
        session.close()


def fbr_select() -> None:
    # Detach first so qa-feature can be force-created even if a previous run
    # left it checked out.
    subprocess.run(["git", "-C", str(GITREPO), "checkout", "-q", "--detach"], check=True)
    subprocess.run(["git", "-C", str(GITREPO), "branch", "-f", "qa-feature"], check=True)

    session = Session(GITREPO)
    session.sync(timeout=45)
    try:
        session.clear_output()
        session.sendline("fbr")
        session.wait_for("Branches", timeout=20)
        session.send("qa-feature")
        time.sleep(0.5)
        session.send(b"\r")
        # Verify HEAD directly; typed follow-up commands race the widget.
        head = GITREPO / ".git" / "HEAD"
        deadline = time.monotonic() + 15
        checked_out = False
        while time.monotonic() < deadline:
            if "qa-feature" in head.read_text():
                checked_out = True
                break
            time.sleep(0.1)
        assert checked_out, "fbr did not check out the branch"
    finally:
        session.close()


def fkill_picker() -> None:
    session = fresh_zsh()
    try:
        session.clear_output()
        session.sendline("fkill")
        session.wait_for("Processes", timeout=20)
        close_picker(session)  # cancel: never send a signal
        assert "Processes" in session.text(), "Processes frame never rendered"
    finally:
        session.close()


def fkill_signal() -> None:
    """Verify the Enter path sends SIGTERM to exactly the selected process."""
    dummy = subprocess.Popen(["sleep", "600"], start_new_session=True)
    register_process(dummy.pid)
    session = fresh_zsh()
    session.check('FZF_DEFAULT_OPTS+=" --nth=1"')
    try:
        session.clear_output()
        session.sendline("fkill")
        session.wait_for("Processes", timeout=20)
        session.send("^" + str(dummy.pid) + "$")
        time.sleep(0.5)
        session.send(b"\r")
        # Wait for fkill's own confirmation line, then verify the process died.
        session.wait_for(f"sent SIGTERM to {dummy.pid}", timeout=15)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and dummy.poll() is None:
            time.sleep(0.1)
        assert dummy.poll() == -signal.SIGTERM, "selected process did not exit from SIGTERM"
    finally:
        session.close()
        if dummy.poll() is None:
            dummy.kill()
            dummy.wait()


def zi_picker() -> None:
    session = fresh_zsh()
    try:
        session.sendline(f"zoxide add {shlex.quote(str(SCRATCH))}")
        session.sync()
        session.clear_output()
        session.sendline(f"zi {shlex.quote(str(SCRATCH))}")
        session.wait_for("Directories", timeout=20)
        close_picker(session)
        assert "Directories" in session.text(), "zi frame never rendered"
    finally:
        session.close()


def zi_select() -> None:
    session = Session(WORK)
    session.sync(timeout=45)
    try:
        session.sendline(f"zoxide add {shlex.quote(str(SCRATCH))}")
        session.sync()
        session.clear_output()
        session.sendline(f"zi {shlex.quote(str(SCRATCH))}")
        session.wait_for("Directories", timeout=20)
        session.send(b"\r")
        # Verify the shell's working directory directly; typed follow-up
        # commands race the widget's foreground work.
        deadline = time.monotonic() + 15
        changed = False
        while time.monotonic() < deadline:
            try:
                if os.readlink(f"/proc/{session.proc.pid}/cwd") == str(SCRATCH):
                    changed = True
                    break
            except OSError:
                pass
            time.sleep(0.1)
        assert changed, "zi did not change directory"
    finally:
        session.close()


def cgm_roundtrip(no_color=False) -> None:
    name = credential_name(os.environ['QA_RUN_ID'])
    secret = 'qa-' + uuid.uuid4().hex
    # Register before any storage, so the outer runner can clean up after signals.
    with (WORK / 'credentials.jsonl').open('a') as stream:
        stream.write(json.dumps({'name': name}) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    session = Session(SCRATCH, extra_env={'NO_COLOR': '1'} if no_color else {})
    session.redactions.append(secret)
    try:
        session.sync(timeout=45)
        session.clear_output()
        session.sendline('cgm set ' + name)
        # Secret Service's actual input prompt, common to rich and plain modes.
        session.wait_for('Password:', timeout=20)
        deadline = time.monotonic()+5
        while termios.tcgetattr(session.master)[3] & termios.ECHO:
            assert time.monotonic() < deadline, 'credential input echo remained enabled'
            time.sleep(.02)
        session.send(secret + '\r')
        session.wait_for('Saved ' + name if no_color else 'Credential Saved', timeout=20)
        session.sync()
        session.check('cgm list | command grep -q ' + shlex.quote(name))
        session.check(f'cgm env {name} && [[ ${name} == {shlex.quote(secret)} ]]')
        session.check(f'cgm unset {name} && (( ! $+parameters[{name}] ))')
        session.clear_output()
        session.sendline('cgm delete ' + name)
        session.wait_for('[y/N]', timeout=15)
        session.send('y\r')
        session.sync()
        session.check('cgm list > "$HOME/cgm-list" && ! command grep -q ' + name + ' "$HOME/cgm-list"')
        check = bounded(['secret-tool', 'lookup', 'application', 'cgm', 'variable', name], timeout=20)
        assert check.returncode == 1 and not check.stderr.strip(), 'credential still stored or backend unavailable'
    finally:
        session.close()
        # Only this unique synthetic name is touched; values never enter logs.
        bounded(['secret-tool', 'clear', 'application', 'cgm', 'variable', name], timeout=20)
        check = bounded(['secret-tool', 'lookup', 'application', 'cgm', 'variable', name], timeout=20)
        assert check.returncode == 1 and not check.stderr.strip(), 'credential cleanup could not verify absence'



NIX_FEATURES = ("--extra-experimental-features", "nix-command flakes")


def nix_env() -> dict[str, str]:
    return clean_env(QAHOME, REPO)


def nix_run(*args: str, timeout: float = 300) -> subprocess.CompletedProcess:
    return bounded(["nix", *NIX_FEATURES, *args], env=nix_env(), timeout=timeout)



def profile_elements() -> dict:
    """Return the isolated profile's element map (empty when absent)."""
    result = nix_run("profile", "list", "--json", timeout=60)
    assert result.returncode == 0, f'profile read failed: {result.stderr.strip()}'
    data = json.loads(result.stdout)
    assert isinstance(data, dict) and isinstance(data.get('elements'), dict), 'invalid Nix profile schema'
    return data['elements']



def wait_profile(predicate, timeout: float, description: str) -> None:
    """Poll the isolated Nix profile until predicate(elements) is true."""
    deadline = time.monotonic() + timeout
    last: object = None
    while time.monotonic() < deadline:
        last = profile_elements()
        if predicate(last):
            return
        time.sleep(1.0)
    raise AssertionError(f"timed out waiting for {description}; profile: {last}")


def empty_profile() -> None:
    manifest = QAHOME / '.local/state/nix/profiles/profile/manifest.json'
    if not manifest.exists():
        seeded = nix_run('profile', 'add', 'nixpkgs#hello')
        assert seeded.returncode == 0, f'profile initialization failed: {seeded.stderr.strip()}'
    for name in list(profile_elements()):
        result = nix_run("profile", "remove", name, timeout=120)
        assert result.returncode == 0, f"profile cleanup failed: {result.stderr.strip()}"


def npkg_remove_picker() -> None:
    empty_profile()
    seeded = nix_run("profile", "add", "nixpkgs#hello")
    assert seeded.returncode == 0, f"seeding hello failed: {seeded.stderr.strip()}"
    seeded_elements = profile_elements()
    assert "hello" in seeded_elements, "hello missing after seeding"
    (WORK / "nix-remove-before.json").write_text(json.dumps(seeded_elements, indent=2))

    session = Session(SCRATCH, home=str(QAHOME), zdotdir=str(QAHOME))
    session.sync(timeout=45)
    try:
        session.clear_output()
        session.sendline("npkg remove")
        session.wait_for("Installed packages", timeout=20)
        session.send(b"\r")  # remove focused entry
        # Verify the mutation by reading the isolated profile directly; typed
        # follow-up commands race the widget's foreground nix process.
        wait_profile(lambda elements: "hello" not in elements, timeout=120, description="hello removal")
    finally:
        session.close()


def npkg_add_picker() -> None:
    empty_profile()  # so a pre-existing cowsay cannot make the check pass vacuously
    session = Session(SCRATCH, home=str(QAHOME), zdotdir=str(QAHOME))
    session.sync(timeout=45)
    try:
        session.clear_output()
        session.sendline("npkg add")
        # The first run builds the nixpkgs attribute cache; this can take a while.
        session.wait_for("Packages", timeout=300)
        session.send("cowsay")
        time.sleep(0.5)
        session.send(b"\r")
        wait_profile(lambda elements: "cowsay" in elements, timeout=300, description="cowsay install")
        (WORK / "nix-add-after.json").write_text(json.dumps(profile_elements(), indent=2))
    finally:
        session.close()
        empty_profile()


SCENARIOS: dict[str, tuple] = {
    "startup": (startup_prompt, "cold interactive startup stays clean"),
    "nounset-startup": (nounset_startup, "NO_UNSET startup keeps zoxide and other integrations"),
    "fzf-cold-start": (fzf_cold_start, "empty fzf cache is rebuilt"),
    "fzf-warm-start": (fzf_warm_start, "warm fzf cache is reused"),
    "fzf-blocked": (fzf_blocked, "fzf below 0.68 blocks pickers only"),
    "env-no-color": (env_no_color, "NO_COLOR reaches pickers"),
    "env-extra-opts": (env_extra_opts, "ZSH_FZF_EXTRA_OPTS is appended"),
    "env-inherited-opts": (env_inherited_opts, "inherited fzf options are preserved"),
    "env-layout-compact": (env_layout_compact, "compact layout frame and preview"),
    "env-layout-roomy": (env_layout_roomy, "roomy layout frame and preview"),
    "env-layout-minimal": (env_layout_minimal, "minimal layout frame and preview"),
    "ctrl-t": (ctrl_t_picker, "Ctrl+T opens the Files picker and toggles previews"),
    "ctrl-t-insert": (ctrl_t_insert, "Ctrl+T inserts the selected path"),
    "ctrl-r": (ctrl_r_picker, "Ctrl+R opens the History picker"),
    "alt-c": (alt_c_picker, "Alt+C opens the Directories picker"),
    "completion": (completion_paths, "**<Tab> opens the fzf completion overlay"),
    "zhelp": (zhelp_palette, "zhelp opens the Commands palette"),
    "zhelp-queue": (zhelp_queue, "zhelp queues the selected example"),
    "fbr": (fbr_picker, "fbr opens the Branches picker"),
    "fbr-select": (fbr_select, "fbr checks out the selected branch"),
    "fkill": (fkill_picker, "fkill opens the Processes picker and cancels safely"),
    "fkill-signal": (fkill_signal, "fkill sends SIGTERM to the selected PID"),
    "zi": (zi_picker, "zi opens the zoxide Directories picker"),
    "zi-select": (zi_select, "zi changes directory after selection"),
    "cgm-no-color": (lambda: cgm_roundtrip(True), "plain credential round trip"),
    "cgm": (cgm_roundtrip, "cgm stores, loads, unsets, and deletes a credential"),
    "npkg-remove": (npkg_remove_picker, "npkg remove picker removes the selection"),
    "npkg-add": (npkg_add_picker, "npkg add picker installs the selection"),
}


def make_home(home: Path, zshrc: str) -> None:
    """Create an isolated HOME whose config lives in the selected repository."""
    create_home(home, REPO, 'autoload -Uz compinit; compinit -i -d "$HOME/.zcompdump"\n' + zshrc + '\nPROMPT="QA> "\nRPROMPT=""\nbindkey -e\n')


def ensure_isolated_home() -> None:
    """Prepare the throwaway HOME used by the fzf-cache and Nix scenarios."""
    make_home(QAHOME, 'source "$HOME/.config/zsh/init.zsh"\n')


def list_scenarios() -> None:
    print(f"repository: {REPO}")
    print(f"work dir:   {WORK}")
    print()
    for name, (_, description) in SCENARIOS.items():
        requires = SCENARIO_REQUIREMENTS.get(name, ())
        suffix = f"  [needs: {', '.join(requires)}]" if requires else ""
        print(f"{name:18} {description}{suffix}")


def main() -> int:
    if "--list" in sys.argv[1:]:
        list_scenarios()
        return 0

    if not REPO.joinpath("init.zsh").is_file():
        print(f"fatal: no init.zsh under {REPO}; set ZSH_CONFIG_DIR", file=sys.stderr)
        return 2
    if not SCRATCH.is_dir():
        print(f"fatal: fixtures missing under {SCRATCH}; run ./setup-fixtures.zsh first", file=sys.stderr)
        return 2
    if shutil.which("fzf") is None:
        print("fatal: fzf is required on PATH", file=sys.stderr)
        return 2

    if not __debug__:
        raise RuntimeError("Python optimization disables assertions; do not use -O")
    verify_work(WORK, REPO)
    ensure_isolated_home()
    names = [argument for argument in sys.argv[1:] if not argument.startswith("-")]
    names = names or list(SCENARIOS)
    for name in names:
        entry = SCENARIOS.get(name)
        if entry is None:
            print(f"unknown scenario: {name}", file=sys.stderr)
            return 2
        run(name, entry[0])

    passed = [result for result in RESULTS if result.status == "pass"]
    failed = [result for result in RESULTS if result.status == "fail"]
    skipped = [result for result in RESULTS if result.status == "skip"]
    print()
    print(
        f"interactive sweep: {len(passed)} passed, "
        f"{len(failed)} failed, {len(skipped)} skipped"
    )
    for result in failed:
        print(f"  FAIL {result.name}: {result.detail}")
    for result in skipped:
        print(f"  SKIP {result.name}: {result.detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

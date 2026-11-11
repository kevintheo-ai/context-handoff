"""Shared test setup: a private state folder, a fake home, and no real window or kill in any test."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import handoff_spawn as hs
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _no_window(argv):
    raise AssertionError(f"a test tried to open a real window: {argv}")


def _no_detach(cmd):
    raise AssertionError(f"a test tried to end a real session: {cmd}")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every test: state folder and HOME in tmp, no session id, no terminal app, no detached kill."""
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    home = tmp_path / "home"
    home.mkdir()
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setattr(hs, "STATE_DIR", state)
    monkeypatch.setattr(hs, "RUN_PARENT", str(runs))
    monkeypatch.setattr(hs, "_run", _no_window)
    monkeypatch.setattr(hs, "_detach", _no_detach)
    monkeypatch.setattr(hs, "ALIVE_S", 0.3)
    monkeypatch.setenv("HOME", str(home))
    # No TERM_PROGRAM either: a test run inside Ghostty must not reach the real Ghostty.
    for var in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CONFIG_DIR", "CONTEXT_HANDOFF_TERMINAL", "TERM_PROGRAM"):
        monkeypatch.delenv(var, raising=False)
    return state


@pytest.fixture
def fake_claude(tmp_path):
    """A stand-in for claude: records folder, arguments (NUL-separated), pid and config dir, then exits
    with FAKE_EXIT after FAKE_SLEEP seconds."""
    out = tmp_path / "fake-out"
    exe = tmp_path / "bin" / "claude"
    exe.parent.mkdir()
    exe.write_text(
        "#!/bin/sh\n"
        f"o='{out}'\n"
        'pwd > "$o.cwd"\n'
        'printf \'%s\\0\' "$@" > "$o.args"\n'
        'echo $$ > "$o.pid"\n'
        'printf %s "${CLAUDE_CONFIG_DIR-}" > "$o.config"\n'
        'sleep "${FAKE_SLEEP:-0}"\n'
        'exit "${FAKE_EXIT:-0}"\n'
    )
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    return exe, out


def read_args(out: Path) -> list[str]:
    return Path(f"{out}.args").read_text().split("\0")[:-1]


def env_without_session() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    return env

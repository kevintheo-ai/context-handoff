"""handoff_spawn.py: start script, terminal openers, checks, caller detection, the whole flow without a window."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import handoff_spawn as hs
import pytest
from conftest import ROOT, read_args

ODD = "folder with space, 'quote', \"double\", $HOME and `tick`"


def run_script(script: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["/bin/sh", str(script)], input="\n", capture_output=True, text=True, env=env, timeout=30)


def write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def make_script(tmp_path: Path, cwd: Path, argv: list[str], go: bool = True) -> tuple[Path, Path]:
    """A start script as the spawn writes it, with its go token (unless go=False); returns script and marker."""
    marker, token = tmp_path / "m", tmp_path / "go"
    if go:
        token.touch()
    return write(tmp_path / "s.command", hs.start_script(cwd, marker, token, argv)), marker


def test_start_script_starts_claude_in_the_folder_with_exact_arguments(tmp_path, fake_claude):
    exe, out = fake_claude
    cwd = tmp_path / ODD
    cwd.mkdir()
    argv = hs.session_argv(str(exe), "Read 'this'\nand \"that\" $(nothing)", "opus", "max", "Shop · Fix")
    script, marker = make_script(tmp_path, cwd, argv)
    assert run_script(script).returncode == 0
    assert Path(f"{out}.cwd").read_text().strip() == str(cwd.resolve())
    assert read_args(out) == argv[1:]
    # the marker holds the pid that became claude (exec keeps it)
    assert marker.read_text().strip() == Path(f"{out}.pid").read_text().strip()
    assert not script.exists(), "the start script deletes itself"


@pytest.mark.parametrize(("code", "expected"), [(0, 0), (129, 0), (3, 3)])
def test_start_script_exit_codes(tmp_path, fake_claude, code, expected):
    exe, _ = fake_claude
    script, _ = make_script(tmp_path, tmp_path, [str(exe), "go"])
    result = run_script(script, {**os.environ, "FAKE_EXIT": str(code)})
    assert result.returncode == expected
    assert ("Press Enter" in result.stdout) == (expected != 0)


def test_start_script_keeps_the_config_dir(tmp_path, fake_claude, monkeypatch):
    exe, out = fake_claude
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "other config"))
    script, _ = make_script(tmp_path, tmp_path, [str(exe), "go"])
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CONFIG_DIR"}
    assert run_script(script, env).returncode == 0
    assert Path(f"{out}.config").read_text() == str(tmp_path / "other config")


def test_start_script_stops_on_a_missing_folder(tmp_path, fake_claude):
    exe, out = fake_claude
    script, _ = make_script(tmp_path, tmp_path / "gone", [str(exe), "go"])
    result = run_script(script)
    assert result.returncode == 1 and "folder not found" in result.stdout
    assert not Path(f"{out}.args").exists()


def test_start_script_keeps_placeholder_text_in_a_folder_name(tmp_path, fake_claude):
    exe, out = fake_claude
    cwd = tmp_path / "@ARGV@ @MARKER@"
    cwd.mkdir()
    script, _ = make_script(tmp_path, cwd, [str(exe), "go"])
    assert run_script(script).returncode == 0
    assert Path(f"{out}.cwd").read_text().strip() == str(cwd.resolve())


def test_ghostty_runs_through_the_login_shell_and_closes_its_window(tmp_path, fake_claude):
    exe, out = fake_claude
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    called = tmp_path / "osascript.args"
    osa = write(fake_bin / "osascript", f"#!/bin/sh\nprintf '%s\\n' \"$@\" > '{called}'\n")
    osa.chmod(0o755)
    script, marker = make_script(tmp_path, tmp_path, [str(exe), "go"])
    env = {**os.environ, "TERM_PROGRAM": "ghostty", "SHELL": "/bin/bash", "PATH": f"{fake_bin}:{os.environ['PATH']}"}
    result = run_script(script, env)
    assert result.returncode == 0
    assert marker.read_text().strip() == Path(f"{out}.pid").read_text().strip()
    assert read_args(out) == ["go"]
    assert "\033]2;context-handoff-" in result.stdout, "the window gets a name to be found by"
    args = called.read_text()
    assert 'tell application "Ghostty"' in args and "context-handoff-" in args
    assert "if (count of t) is 1 and name of (item 1 of t) is (item 1 of argv) then close window" in args, (
        "only a window that holds nothing but this terminal, found by its exact name"
    )


@pytest.mark.parametrize("listed", [True, False])
def test_ghostty_uses_only_a_login_shell_listed_in_etc_shells(tmp_path, fake_claude, listed):
    exe, out = fake_claude
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    write(fake_bin / "osascript", "#!/bin/sh\n").chmod(0o755)
    ran = tmp_path / "profile-ran"
    write(Path(os.environ["HOME"]) / ".bash_profile", f"touch '{ran}'\n")
    shell = "/bin/bash"
    if not listed:  # a shell of the same name somewhere else, not in /etc/shells
        shell = str(write(tmp_path / "bash", '#!/bin/sh\nexec /bin/bash "$@"\n'))
        Path(shell).chmod(0o755)
    script, _ = make_script(tmp_path, tmp_path, [str(exe), "go"])
    env = {**os.environ, "TERM_PROGRAM": "ghostty", "SHELL": shell, "PATH": f"{fake_bin}:{os.environ['PATH']}"}
    assert run_script(script, env).returncode == 0
    assert read_args(out) == ["go"]
    assert ran.exists() == listed, "the login profile runs only through a shell from /etc/shells"


def test_ghostty_start_ignores_aliases_and_functions_of_the_login_shell(tmp_path, fake_claude):
    exe, out = fake_claude
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    write(fake_bin / "osascript", "#!/bin/sh\n").chmod(0o755)
    profile = "alias rm='rm -i'\nprintf() { :; }\nexec() { :; }\n"  # what a dotfile might bring
    write(Path(os.environ["HOME"]) / ".bash_profile", profile)
    script, marker = make_script(tmp_path, tmp_path, [str(exe), "go"])
    env = {**os.environ, "TERM_PROGRAM": "ghostty", "SHELL": "/bin/bash", "PATH": f"{fake_bin}:{os.environ['PATH']}"}
    assert run_script(script, env).returncode == 0
    assert read_args(out) == ["go"], "claude started"
    assert marker.read_text().strip() == Path(f"{out}.pid").read_text().strip(), "the marker holds its pid"
    assert not (tmp_path / "go").exists(), "the go token was taken"


def test_a_start_without_its_go_token_starts_nothing(tmp_path, fake_claude):
    exe, out = fake_claude
    script, marker = make_script(tmp_path, tmp_path, [str(exe), "go"], go=False)
    result = run_script(script)
    assert "start cancelled" in result.stdout and result.returncode == 3
    assert not marker.exists() and not Path(f"{out}.args").exists()


def test_session_argv_leaves_out_what_is_not_given():
    assert hs.session_argv("claude", "p", None, None, None) == ["claude", "p"]
    assert hs.session_argv("claude", "p", "opus", "high", "T") == [
        "claude", "--model", "opus", "--effort", "high", "--name", "T", "p",
    ]  # fmt: skip


def test_warp_config_is_found_by_its_name_and_runs_the_script(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(hs, "_run", calls.append)
    run = tmp_path / "context-handoff-abc123"
    run.mkdir()
    script = run / "start.command"
    hs.open_window("warp", script)
    config = hs.warp_config(script)
    lines = config.read_text().splitlines()
    # named like the fresh folder: Warp did not open a launch configuration with a reused name (measured)
    assert calls == [["open", "warp://launch/context-handoff-abc123"]]
    assert lines[1] == 'name: "context-handoff-abc123"' and config.name == "context-handoff-abc123.yaml"
    tmp_path = run  # the folder Warp opens is the script's own
    # Warp rejects a tab layout without a folder (measured: "did not match any variant of PaneTemplateType")
    assert f"cwd: {json.dumps(str(tmp_path))}" in [line.strip() for line in lines]
    assert lines[-1].strip() == f"- exec: {json.dumps(f'{script} && exit')}"
    assert config.stat().st_mode & 0o777 == 0o600


def test_ghostty_and_terminal_get_the_script(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(hs, "_run", calls.append)
    script = tmp_path / "s.command"
    hs.open_window("ghostty", script)
    hs.open_window("terminal", script)
    assert calls[0][:2] == ["osascript", "-e"] and calls[0][3] == str(script)
    assert "set command of cfg to item 1 of argv" in calls[0][2]
    assert calls[1] == ["open", "-a", "Terminal", str(script)]


@pytest.mark.parametrize(
    ("term", "override", "expected"),
    [("WarpTerminal", None, "warp"), ("ghostty", None, "ghostty"), ("Apple_Terminal", None, "terminal"),
     ("vscode", None, None), ("WarpTerminal", "ghostty", "ghostty")],
)  # fmt: skip
def test_terminal_default(monkeypatch, term, override, expected):
    monkeypatch.setenv("TERM_PROGRAM", term)
    if override:
        monkeypatch.setenv("CONTEXT_HANDOFF_TERMINAL", override)
    assert hs.parse_args(["--brief", "/x"]).terminal == expected


def test_main_refuses_unknown_terminal_and_missing_brief(tmp_path, capsys, monkeypatch):
    brief = write(tmp_path / "b.md", "x")
    monkeypatch.setenv("TERM_PROGRAM", "vscode")
    assert hs.main(["--brief", str(brief), "--cwd", str(tmp_path)]) == 1
    assert "unknown terminal" in capsys.readouterr().err
    assert hs.main(["--brief", "relative.md", "--terminal", "warp"]) == 1
    assert "brief not found" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["with space", "line\nbreak", "ends-in-a-line-break\n"])
def test_a_start_folder_with_odd_characters_is_refused(tmp_path, isolated, capsys, monkeypatch, fake_claude, name):
    exe, _ = fake_claude
    monkeypatch.setattr(hs, "CLAUDE", str(exe))  # so the check is reached on a machine without claude too
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s3")
    parent = Path(f"{tmp_path}/{name}")
    parent.mkdir()
    monkeypatch.setattr(hs, "RUN_PARENT", str(parent))
    brief = write(tmp_path / "b.md", "x")
    assert hs.main(["--brief", str(brief), "--cwd", str(tmp_path), "--terminal", "warp", "--close-caller"]) == 1
    assert "odd characters" in capsys.readouterr().err
    assert list(parent.iterdir()) == [], "the refused folder is gone again"
    assert not (isolated / "s3.handed-over").exists(), "nothing started, the session may try again"


def test_start_files_get_a_fresh_private_folder():
    run = hs.new_run_dir()
    try:
        assert run.name.startswith("context-handoff-") and run.stat().st_mode & 0o777 == 0o700
        assert hs.new_run_dir() != run, "a fresh one per spawn"
    finally:
        for d in Path(hs.RUN_PARENT).iterdir():
            d.rmdir()


def test_old_state_files_are_pruned(isolated, tmp_path):
    old, new = isolated / "old.json", isolated / "new.json"
    old.write_text("{}")
    new.write_text("{}")
    os.utime(old, (0, 0))
    elsewhere = tmp_path / "keep-me"
    elsewhere.write_text("x")
    os.utime(elsewhere, (0, 0))
    (isolated / "link").symlink_to(elsewhere)
    hs.prune_state()
    assert not old.exists() and new.exists()
    assert elsewhere.exists(), "a link is never followed"


def fake_ps(table: dict[int, tuple[int, str]]):
    """table: pid -> (parent pid, name of pid), like `ps -o ppid=,comm=`."""
    return lambda pid: table.get(pid, (0, ""))


@pytest.mark.parametrize(
    ("table", "expected"),
    [
        # started by hand in a window: claude under the login shell -> both, the window closes
        ({100: (80, "/bin/zsh"), 80: (70, "/usr/local/bin/claude"), 70: (60, "-zsh"), 60: (1, "Warp")}, [80, 70]),
        # started by a start script: claude under /bin/sh -> claude only, the script ends the window
        ({100: (80, "zsh"), 80: (75, "claude"), 75: (70, "/bin/sh"), 70: (60, "-zsh"), 60: (1, "Warp")}, [80]),
        # nested: an inner claude inside an outer claude's tool shell -> only the inner one
        ({100: (80, "zsh"), 80: (65, "claude"), 65: (50, "zsh"), 50: (40, "claude"), 40: (30, "-zsh")}, [80]),
        # no claude above us
        ({100: (90, "zsh"), 90: (1, "launchd")}, []),
    ],
)
def test_caller_session(monkeypatch, table, expected):
    monkeypatch.setattr(hs, "_parent_and_name", fake_ps(table))
    assert hs.caller_session(100) == expected


def run_close(cmd: str) -> None:
    """Run a close script without its 10 s wait."""
    subprocess.run(["/bin/sh", "-c", cmd.replace(f"sleep {hs.CLOSE_DELAY_S}", ":", 1)], timeout=10)


def test_a_handoff_that_hangs_up_nobody_frees_the_session_again(tmp_path):
    note = tmp_path / "s1.handed-over"
    note.touch()
    gone = subprocess.run(["/bin/sh", "-c", "echo $$"], capture_output=True, text=True).stdout.strip()
    run_close(hs.close_command(int(gone), "Wed Nov 11 04:48:16 2026", [80], "", note))
    assert not note.exists(), "the new session died, so the old one lives on and may hand over again"


def test_without_a_caller_the_note_stays(tmp_path, monkeypatch, capsys):
    note = tmp_path / "s1.handed-over"
    note.touch()
    monkeypatch.setattr(hs, "caller_session", lambda: [])
    hs.close_caller(4242, note)
    assert note.exists(), "the new session runs: this one must not hand over again, it only cannot close itself"
    assert "close its window yourself" in capsys.readouterr().err


def test_close_command_checks_the_new_session_first():
    lines = hs.close_command(4242, "Wed Nov 11 04:48:16 2026", [80, 70], "").splitlines()
    assert lines[0] == f"sleep {hs.CLOSE_DELAY_S}"
    assert lines[1] == "set -- $(ps -o lstart= -p 4242); [ \"$*\" = 'Wed Nov 11 04:48:16 2026' ] || exit 0"
    assert "kill -HUP 80" in lines[3] and "kill -HUP 70" in lines[4]
    assert not any("osascript" in line for line in lines)
    with_terminal = hs.close_command(4242, "x", [80], "ttys011")
    assert "ps -t ttys011" in with_terminal and with_terminal.endswith("/dev/ttys011")


def test_close_command_hangs_up_only_what_is_still_the_same_process(tmp_path):
    """Run the real script against live stand-ins. Only processes this test started itself get a signal."""
    stand_in = subprocess.Popen(["/bin/sleep", "30"])  # named sleep, not claude: a reused pid
    new = subprocess.Popen(["/bin/sleep", "30"])
    claude = subprocess.Popen(["/bin/bash", "-c", "exec -a claude-x /bin/sleep 30"])  # bash: exec -a
    try:
        run_close(hs.close_command(new.pid, hs.started_at(new.pid), [stand_in.pid], ""))
        assert stand_in.poll() is None, "a process that is not claude must not be hung up"
        run_close(hs.close_command(new.pid, "Thu Jan  1 01:00:00 1970", [claude.pid], ""))
        assert claude.poll() is None, "a new pid with another start time is not the new session"
        run_close(hs.close_command(new.pid, hs.started_at(new.pid), [claude.pid], ""))
        assert claude.wait(timeout=5) == -1, "the session is hung up (SIGHUP)"
    finally:
        for p in (stand_in, new, claude):
            if p.poll() is None:
                p.kill()
            p.wait()


def test_caller_context_pct_turns_close_caller_on(tmp_path, isolated, capsys, monkeypatch, fake_claude):
    exe, _ = fake_claude
    monkeypatch.setattr(hs, "CLAUDE", str(exe))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "abc-123")
    status = isolated / "abc-123.json"
    brief = write(tmp_path / "b.md", "x")
    args = ["--brief", str(brief), "--cwd", str(tmp_path), "--terminal", "warp", "--dry-run"]
    status.write_text(json.dumps({"context_window": {"used_percentage": 86.4}}))
    assert hs.main(args) == 0
    assert "NOTE: this session is at 86 % context" in capsys.readouterr().out
    status.write_text(json.dumps({"context_window": {"used_percentage": 40}}))
    assert hs.main(args) == 0
    assert "NOTE" not in capsys.readouterr().out
    status.write_text("not json")
    assert hs.caller_context_pct() == 0


def test_private_dir(tmp_path):
    good = tmp_path / "good"
    good.mkdir(mode=0o700)
    open_one = tmp_path / "open"
    open_one.mkdir()
    open_one.chmod(0o777)
    link = tmp_path / "link"
    link.symlink_to(good)
    assert hs.private_dir(good)
    assert not hs.private_dir(open_one)
    assert not hs.private_dir(link)
    assert not hs.private_dir(tmp_path / "missing")


def test_a_foreign_state_folder_gives_no_number(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    (shared / "s1.json").write_text(json.dumps({"context_window": {"used_percentage": 99}}))
    monkeypatch.setattr(hs, "STATE_DIR", shared)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1")
    assert hs.caller_context_pct() == 0


def test_wait_for_start(tmp_path):
    marker, go = tmp_path / "m", tmp_path / "go"
    marker.write_text(f"{os.getpid()}\n")
    assert hs.wait_for_start(marker, go, timeout=2) == os.getpid()
    done = subprocess.run(["/bin/sh", "-c", "echo $$"], capture_output=True, text=True).stdout.strip()
    marker.write_text(done)
    assert hs.wait_for_start(marker, go, timeout=2) == 0, "a session that is already gone did not start"


def test_at_the_deadline_the_go_token_is_withdrawn(tmp_path):
    marker, go = tmp_path / "m", tmp_path / "go"
    go.touch()
    assert hs.wait_for_start(marker, go, timeout=0.5) == 0
    assert not go.exists(), "a start script that runs later now finds no token and starts nothing"


def test_a_token_taken_without_a_marker_is_unsure(tmp_path, monkeypatch):
    monkeypatch.setattr(hs, "GRACE_S", 0.5)
    marker, go = tmp_path / "m", tmp_path / "go"  # no token and no marker: taken, then stuck
    assert hs.wait_for_start(marker, go, timeout=0.5) == hs.UNSURE


def test_unsure_keeps_the_session_handed_over(tmp_path, isolated, monkeypatch, fake_claude, capsys):
    exe, _ = fake_claude
    monkeypatch.setattr(hs, "CLAUDE", str(exe))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s4")
    monkeypatch.setattr(hs, "spawn", lambda *a: hs.UNSURE)
    brief = write(tmp_path / "brief.md", "x")
    assert hs.main(["--brief", str(brief), "--cwd", str(tmp_path), "--terminal", "warp", "--close-caller"]) == 1
    assert "may still start" in capsys.readouterr().err
    assert (isolated / "s4.handed-over").exists(), "no second try while the first may still come"


def test_an_error_after_the_claim_frees_it(tmp_path, isolated, monkeypatch, fake_claude):
    exe, _ = fake_claude
    monkeypatch.setattr(hs, "CLAUDE", str(exe))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s5")

    def broken(*a):
        raise OSError("disk full")

    monkeypatch.setattr(hs, "spawn", broken)
    brief = write(tmp_path / "brief.md", "x")
    with pytest.raises(OSError):
        hs.main(["--brief", str(brief), "--cwd", str(tmp_path), "--terminal", "warp", "--close-caller"])
    assert not (isolated / "s5.handed-over").exists()


def test_a_token_taken_at_the_deadline_still_counts(tmp_path):
    marker, go = tmp_path / "m", tmp_path / "go"  # no token: the start script took it just now ...
    late = subprocess.Popen(["/bin/sh", "-c", f"sleep 1.5; echo {os.getpid()} > '{marker}'"])  # ... marker follows
    try:
        assert hs.wait_for_start(marker, go, timeout=0.5) == os.getpid()
    finally:
        late.wait()


def run_in_background(argv):
    """Stand-in for the terminal: run the start script from the Warp configuration or the argument."""
    target = argv[-1]
    if target.startswith("warp://launch/"):
        exec_line = (Path.home() / ".warp/launch_configurations" / f"{target[14:]}.yaml").read_text()
        target = json.loads(exec_line.rsplit("exec: ", 1)[1])[: -len(" && exit")]
    subprocess.Popen(["/bin/sh", target], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)


@pytest.mark.parametrize("terminal", ["warp", "ghostty", "terminal"])
def test_whole_flow_without_a_window(tmp_path, isolated, monkeypatch, fake_claude, capsys, terminal):
    exe, out = fake_claude
    monkeypatch.setattr(hs, "CLAUDE", str(exe))
    monkeypatch.setattr(hs, "_run", run_in_background)
    monkeypatch.setenv("FAKE_SLEEP", "3")
    detached = []
    monkeypatch.setattr(hs, "_detach", detached.append)
    monkeypatch.setattr(hs, "caller_session", lambda: [80, 70])
    brief = write(tmp_path / "brief.md", "x")
    code = hs.main(["--brief", str(brief), "--cwd", str(tmp_path), "--terminal", terminal, "--close-caller"])
    assert code == 0, capsys.readouterr().err
    pid = int(Path(f"{out}.pid").read_text())  # the pid the fake claude saw for itself
    assert f"(pid {pid})" in capsys.readouterr().out
    assert read_args(out)[-1].startswith(f"Read the file {brief} COMPLETELY")
    assert detached == [hs.close_command(pid, hs.started_at(pid), [80, 70], "")]
    assert list(isolated.iterdir()) == [], "no session id, so no handed-over note"
    assert list(Path(hs.RUN_PARENT).iterdir()) == [], "start folder with script, token and marker gone"
    assert not list((Path.home() / ".warp/launch_configurations").glob("*.yaml"))


def test_a_session_hands_over_only_once(tmp_path, isolated, monkeypatch, fake_claude, capsys):
    exe, _ = fake_claude
    monkeypatch.setattr(hs, "CLAUDE", str(exe))
    monkeypatch.setattr(hs, "_run", run_in_background)
    monkeypatch.setenv("FAKE_SLEEP", "3")
    monkeypatch.setattr(hs, "_detach", lambda cmd: None)
    monkeypatch.setattr(hs, "caller_session", lambda: [80])
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1")
    brief = write(tmp_path / "brief.md", "x")
    args = ["--brief", str(brief), "--cwd", str(tmp_path), "--terminal", "warp"]
    assert hs.main([*args, "--close-caller"]) == 0
    assert (isolated / "s1.handed-over").exists()
    monkeypatch.setattr(hs, "_run", lambda argv: pytest.fail("a second window opened"))
    assert hs.main([*args, "--close-caller"]) == 1
    assert "already handed over" in capsys.readouterr().err


def test_a_failed_handoff_can_be_tried_again(tmp_path, isolated, monkeypatch, fake_claude, capsys):
    exe, _ = fake_claude
    monkeypatch.setattr(hs, "CLAUDE", str(exe))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s2")

    def fail(argv):
        raise subprocess.CalledProcessError(1, argv, stderr="no window")

    monkeypatch.setattr(hs, "_run", fail)
    brief = write(tmp_path / "brief.md", "x")
    assert hs.main(["--brief", str(brief), "--cwd", str(tmp_path), "--terminal", "warp", "--close-caller"]) == 1
    assert not (isolated / "s2.handed-over").exists(), "nothing started, so the session may try once more"


def test_the_marker_lives_in_the_private_folder(tmp_path, isolated, fake_claude, capsys, monkeypatch):
    exe, _ = fake_claude
    monkeypatch.setattr(hs, "CLAUDE", str(exe))
    brief = write(tmp_path / "brief.md", "x")
    assert hs.main(["--brief", str(brief), "--cwd", str(tmp_path), "--terminal", "warp", "--dry-run"]) == 0
    script = capsys.readouterr().out
    marker_and_token = re.search(r"^set -- (\S+\.pid) (\S+\.go) ", script, re.M)
    assert marker_and_token, "marker and go token are the start script's first arguments"
    folders = {Path(p).parent for p in marker_and_token.groups()}
    assert all(f.name.startswith("context-handoff-") for f in folders), "in the start folder, not by the brief"


def test_a_session_that_dies_at_start_is_no_start(tmp_path, monkeypatch, fake_claude, capsys):
    exe, _ = fake_claude
    monkeypatch.setattr(hs, "CLAUDE", str(exe))
    monkeypatch.setattr(hs, "_run", run_in_background)
    monkeypatch.setenv("FAKE_EXIT", "0")  # exits at once, before ALIVE_S
    brief = write(tmp_path / "brief.md", "x")
    assert hs.main(["--brief", str(brief), "--cwd", str(tmp_path), "--terminal", "terminal"]) == 1
    assert "did not start" in capsys.readouterr().err


def test_open_failure_is_reported_and_cleans_up(tmp_path, isolated, monkeypatch, fake_claude, capsys):
    exe, _ = fake_claude
    monkeypatch.setattr(hs, "CLAUDE", str(exe))

    def fail(argv):
        raise subprocess.CalledProcessError(1, argv, stderr="execution error: Ghostty got an error")

    monkeypatch.setattr(hs, "_run", fail)
    brief = write(tmp_path / "brief.md", "x")
    assert hs.main(["--brief", str(brief), "--cwd", str(tmp_path), "--terminal", "ghostty"]) == 1
    assert "Ghostty got an error" in capsys.readouterr().err
    assert list(isolated.iterdir()) == []


def test_dry_run_opens_nothing(tmp_path, monkeypatch, fake_claude, capsys):
    exe, _ = fake_claude
    monkeypatch.setattr(hs, "CLAUDE", str(exe))
    brief = write(tmp_path / "brief.md", "x")
    assert hs.main(["--brief", str(brief), "--cwd", str(tmp_path), "--terminal", "warp", "--dry-run"]) == 0
    assert capsys.readouterr().out.startswith("#!/bin/sh\n")


def test_help_runs_on_this_python():
    result = subprocess.run(
        [sys.executable, str(ROOT / "skills/handoff/scripts/handoff_spawn.py"), "--help"],
        capture_output=True, text=True,
    )  # fmt: skip
    assert result.returncode == 0 and "--close-caller" in result.stdout

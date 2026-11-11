#!/usr/bin/env python3
"""Open a new terminal window with a fresh Claude Code session that starts on a handoff brief.

Terminals (macOS): Warp, Ghostty 1.3+ and Terminal. The window runs a one-time start script: it changes
to the working folder, writes the pid that becomes `claude` into a marker file next to the brief and
starts the session. The spawn counts as done once the marker is there and that process still runs.

--close-caller ends the CALLING session once the new one runs, so its window closes. It switches itself
on when the calling session's context is at or above CONTEXT_HANDOFF_PREPARE (85 %): from there on, every
handoff is a context handoff.

Exit 0 = the new session runs. Exit 1 = no session runs: read the error, fix it, try once more.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Status line, watcher and this script share one folder, in the user's home so nobody else can claim its name.
STATE_DIR = Path(os.environ.get("CONTEXT_HANDOFF_DIR") or Path.home() / ".claude" / "context-handoff")
KEEP_DAYS = 14  # status files of older sessions are removed at the next spawn
RUN_PARENT = "/tmp"  # each spawn gets a fresh private folder here (random name, plain characters for Ghostty)
_PREPARE = os.environ.get("CONTEXT_HANDOFF_PREPARE", "")
PREPARE_PCT = int(_PREPARE) if _PREPARE.isdigit() else 85  # a setting that is not a whole number counts as 85
CLAUDE = os.environ.get("CONTEXT_HANDOFF_CLAUDE") or "claude"
TERMINALS = {"WarpTerminal": "warp", "ghostty": "ghostty", "Apple_Terminal": "terminal"}  # TERM_PROGRAM -> name
EFFORTS = ("low", "medium", "high", "xhigh", "max")
SAFE_PATH = re.compile(r"[A-Za-z0-9/._-]+")  # fullmatch: the start script's path goes as it is into Ghostty
START_TIMEOUT_S = 60  # covers a terminal that has to start first
ALIVE_S = 3.0  # the new session must still run this long after its marker appeared
GRACE_S = 30.0  # a start script that took its go token at the deadline gets this long for its marker
UNSURE = -1  # wait_for_start: the start was taken but not confirmed, it may still come
CLOSE_DELAY_S = 10  # the tool call returns first; a new session that dies at start is gone by then
# `open` and `osascript` run as if from the Dock: a terminal that starts now would otherwise inherit this
# session's environment (CLAUDECODE, session ids) and hand it to every window it opens.
CLEAN_ENV_KEEP = ("HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "SSH_AUTH_SOCK", "__CF_USER_TEXT_ENCODING")

# Cold start without Ghostty's own empty first window; `command` instead of typed input, so the start does
# not depend on when the shell is ready to read.
GHOSTTY_SCRIPT = """on run argv
	if not (application "Ghostty" is running) then
		do shell script "open -a Ghostty --args --initial-window=false"
		repeat 100 times
			if application "Ghostty" is running then exit repeat
			delay 0.1
		end repeat
	end if
	tell application "Ghostty"
		set cfg to new surface configuration
		set command of cfg to item 1 of argv
		new window with configuration cfg
		activate
	end tell
end run"""
# Terminal keeps a window open after its shell ended unless the profile says otherwise. Close the caller's
# window, but only a one-tab window whose shell is already gone, so Terminal never asks.
TERMINAL_CLOSE_SCRIPT = """on run argv
	tell application "Terminal"
		repeat with w in windows
			if (count of tabs of w) is 1 and tty of tab 1 of w is (item 1 of argv) then close w
		end repeat
	end tell
end run"""


def clean_env() -> dict[str, str]:
    """The few variables a terminal needs, as it would get them from the Dock."""
    env = {k: os.environ[k] for k in CLEAN_ENV_KEEP if k in os.environ}
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    return env


def _run(argv: list[str]) -> None:
    """The only way this script reaches a terminal app: open or osascript."""
    subprocess.run(argv, check=True, timeout=START_TIMEOUT_S, env=clean_env(), capture_output=True, text=True)


def private_dir(path: Path) -> bool:
    """True if path is a real folder owned by this user that nobody else can write to."""
    try:
        st = path.lstat()
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode) and st.st_uid == os.getuid() and not st.st_mode & 0o022


def session_id() -> str:
    """Id of the calling Claude Code session (Claude Code sets it for its tools); "" if none or odd."""
    sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
    return sid if re.fullmatch(r"[A-Za-z0-9_-]+", sid) else ""


def caller_context_pct() -> int:
    """Context fill of the calling session as its status line saved it; 0 = unknown."""
    sid = session_id()
    if not sid or not private_dir(STATE_DIR):
        return 0
    try:
        return int(json.loads((STATE_DIR / f"{sid}.json").read_text())["context_window"]["used_percentage"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def session_argv(claude: str, prompt: str, model: str | None, effort: str | None, title: str | None) -> list[str]:
    """The claude command line of the new session."""
    argv = [claude]
    if model:
        argv += ["--model", model]
    if effort:
        argv += ["--effort", effort]
    if title:
        argv += ["--name", title]
    return [*argv, prompt]


START_SCRIPT = r"""#!/bin/sh
# context-handoff: one-time start script, deletes itself.
rm -f -- "$0"
@CONFIG@cd @CWD@ || { echo 'context-handoff: folder not found'; read -r _; exit 1; }
set -- @MARKER@ @GO@ @ARGV@
# Take the go token first: if the spawn script gave up waiting and withdrew it, nothing starts here. Then the
# pid goes into the marker and this shell becomes claude. Plain /bin/sh, so no alias or function can interfere.
inner='/bin/rm -- "$1" 2>/dev/null || { echo "context-handoff: start cancelled, the spawn gave up waiting"; exit 3; }
printf "%s\n" "$$" > "$0" && shift && exec "$@"'
# Ghostty runs this script without your shell profile: there your login shell sets up the environment first.
login=
if [ "${TERM_PROGRAM-}" = ghostty ] && grep -qx -- "${SHELL-}" /etc/shells; then
  case "${SHELL##*/}" in zsh | bash | ksh) login=$SHELL ;; esac
fi
if [ -n "$login" ]; then
  "$login" -l -i -c 'builtin exec /bin/sh -c "$0" "$@"' "$inner" "$@"
else
  /bin/sh -c "$inner" "$@"
fi
s=$?
[ "$s" -eq 129 ] && s=0  # SIGHUP is how --close-caller ends a session: a clean end
if [ "$s" -ne 0 ]; then
  printf '\ncontext-handoff: the session ended with code %s. Press Enter to close.\n' "$s"
  read -r _
elif [ "${TERM_PROGRAM-}" = ghostty ]; then
  # Ghostty keeps a window open after its command ended ("Press any key"), and it ends what this script
  # leaves running: so name this terminal and close its window from here, as the script's last step. Only
  # a window that holds nothing else: with a tab or split the user opened next to it, the window stays.
  printf '\033]2;context-handoff-%s\007' "$$"
  exec osascript -e 'on run argv' -e 'delay 0.5' -e 'tell application "Ghostty"' \
    -e 'repeat with w in (get every window)' -e 'set b to (get every tab of w)' -e 'if (count of b) is 1 then' \
    -e 'set t to (get every terminal of (item 1 of b))' \
    -e 'if (count of t) is 1 and name of (item 1 of t) is (item 1 of argv) then close window (contents of w)' \
    -e 'end if' -e 'end repeat' -e 'end tell' -e 'end run' "context-handoff-$$"
fi
exit "$s"
"""


def start_script(cwd: Path, marker: Path, go: Path, argv: list[str]) -> str:
    """The one-time sh script the new window runs; it deletes itself. The inner shell takes the go token,
    writes its pid and then becomes claude, so the marker holds the session's pid. Exit 129 (SIGHUP, how
    --close-caller ends a session) counts as a clean end; any other failure waits for Enter, so the error
    stays readable."""
    q = shlex.quote
    config = os.environ.get("CLAUDE_CONFIG_DIR")
    values = {
        "CONFIG": f"export CLAUDE_CONFIG_DIR={q(config)}\n" if config else "",
        "CWD": q(str(cwd)),
        "MARKER": q(str(marker)),
        "GO": q(str(go)),
        "ARGV": " ".join(q(a) for a in argv),
    }
    # One pass, so a folder or prompt that happens to contain "@ARGV@" stays as it is.
    return re.sub(r"@(CONFIG|CWD|MARKER|GO|ARGV)@", lambda m: values[m.group(1)], START_SCRIPT)


def warp_config(script: Path) -> Path:
    """Where Warp's one-time launch configuration for this start script lives."""
    return Path.home() / ".warp" / "launch_configurations" / f"{script.parent.name}.yaml"


def open_warp(script: Path) -> None:
    """Warp: a one-time launch configuration with one window and one tab, named like the spawn's fresh folder
    (a fixed name is not opened again). Warp looks it up by its `name` field, so that must equal the file
    name. Warp needs a folder (the script's own, plain characters; the script then changes to the real one)
    and no tab title, which would freeze the one the session sets."""
    config, name = warp_config(script), script.parent.name
    config.parent.mkdir(parents=True, exist_ok=True)
    config.touch(mode=0o600)
    config.write_text(
        f"---\nname: {json.dumps(name)}\nactive_window_index: 0\nwindows:\n  - active_tab_index: 0\n"
        f"    tabs:\n      - layout:\n          cwd: {json.dumps(str(script.parent))}\n          commands:\n"
        f"            - exec: {json.dumps(f'{shlex.quote(str(script))} && exit')}\n"
    )
    _run(["open", f"warp://launch/{name}"])


def open_window(terminal: str, script: Path) -> None:
    """Open the new window that runs script."""
    if terminal == "warp":
        open_warp(script)
    elif terminal == "ghostty":
        _run(["osascript", "-e", GHOSTTY_SCRIPT, str(script)])
    else:
        _run(["open", "-a", "Terminal", str(script)])


def alive(pid: int) -> bool:
    """True if a process with this pid exists."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def read_pid(marker: Path) -> int:
    """The pid in the marker; 0 while it is missing, half written or not a real process id."""
    try:
        pid = int(marker.read_text().strip())
    except (OSError, ValueError):
        return 0
    return pid if pid > 1 else 0  # 0, 1 and negative numbers would address many processes at once


def wait_for_start(marker: Path, go: Path, timeout: float = START_TIMEOUT_S) -> int:
    """pid of the new session once its marker is there and it still runs ALIVE_S later; 0 = it did not start
    and never will: at the deadline the go token is withdrawn, so a start script that runs later stops before
    claude starts. If the token is already taken, that script is starting right now; its marker gets
    GRACE_S more, and if it still is not there the start is UNSURE: it may yet come, so nobody may retry."""
    deadline, taken = time.monotonic() + timeout, False
    while not (pid := read_pid(marker)):
        if time.monotonic() >= deadline:
            if taken:
                return UNSURE
            try:
                go.unlink()
                return 0
            except FileNotFoundError:
                deadline, taken = time.monotonic() + GRACE_S, True
        time.sleep(0.5)
    time.sleep(ALIVE_S)
    return pid if alive(pid) else 0


def _parent_and_name(pid: int) -> tuple[int, str]:
    """Parent pid and process name (argv[0] on macOS) of pid; (0, "") if it is gone."""
    out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True, text=True).stdout
    parts = out.split(None, 1)
    return (int(parts[0]), parts[1].strip()) if len(parts) == 2 else (0, "")


def caller_session(pid: int | None = None) -> list[int]:
    """PIDs to hang up so the calling session ends: the topmost `claude` above this script, plus the shell
    it runs in if that is a window's login shell (name starts with "-"), which closes the window. Never an
    outer Claude's tool shell. Empty = no Claude above us."""
    pid = pid or os.getppid()
    claude = 0
    while pid > 1:
        ppid, name = _parent_and_name(pid)
        if os.path.basename(name).startswith("claude"):
            claude = pid
        elif claude:
            return [claude, pid] if name.startswith("-") else [claude]
        pid = ppid
    return [claude] if claude else []


def _tty(pid: int) -> str:
    """Terminal device of pid, e.g. ttys011; "" if it has none."""
    out = subprocess.run(["ps", "-o", "tty=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return out if re.fullmatch(r"ttys\d+", out) else ""


def started_at(pid: int) -> str:
    """When the process started, as ps shows it (spaces normalised); "" if it is gone. A pid that is given to
    a new process later carries another start time."""
    out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True).stdout
    return " ".join(out.split())


def close_command(new_pid: int, new_start: str, pids: list[int], tty: str, release: Path | None = None) -> str:
    """Shell script that waits, then hangs up the calling session if the new one still runs; in Terminal it
    then closes the caller's window once its shell is gone. A pid can be given to a new process once its
    own is gone, so the new session counts only while its pid still has the start time it had (`new_start`),
    and each old process is hung up only while it still carries its name. If it hangs up nobody, it removes
    `release` (the handed-over note): the old session lives on and may hand over again."""
    stay = f"{{ rm -f {shlex.quote(str(release))}; exit 0; }}" if release else "exit 0"
    lines = [
        f"sleep {CLOSE_DELAY_S}",
        f'set -- $(ps -o lstart= -p {new_pid}); [ "$*" = {shlex.quote(new_start)} ] || {stay}',
        f"ps -o comm= -p {pids[0]} | grep -Eq '(^|/)claude[^/]*$' || {stay}",
        f"kill -HUP {pids[0]}",
    ]
    if len(pids) > 1:
        lines.append(f"ps -o comm= -p {pids[1]} | grep -q '^-' && kill -HUP {pids[1]}")
    if tty:
        lines += [
            f"i=0; while [ $i -lt 10 ] && ps -t {tty} -o pid= | grep -q .; do sleep 0.5; i=$((i + 1)); done",
            f"ps -t {tty} -o pid= | grep -q . || osascript -e {shlex.quote(TERMINAL_CLOSE_SCRIPT)} /dev/{tty}",
        ]
    return "\n".join(lines)


def close_caller(new_pid: int, release: Path | None = None) -> None:
    """End the calling session, detached and delayed, so this call still returns its result first. Without a
    calling claude process to find, the handed-over note stays: the new session runs, so this one must not
    hand over again; it only cannot close itself."""
    pids = caller_session()
    if not pids:
        print("WARNING: no calling claude session found; it stays open, close its window yourself.", file=sys.stderr)
        return
    tty = _tty(pids[0]) if os.environ.get("TERM_PROGRAM") == "Apple_Terminal" else ""
    _detach(close_command(new_pid, started_at(new_pid), pids, tty, release))
    print(f"OK: in {CLOSE_DELAY_S} s this session ends and its window closes, if the new one still runs.")


def _detach(cmd: str) -> None:
    """Run a shell line in a session of its own, so it outlives this script and the calling session."""
    subprocess.Popen(
        ["/bin/sh", "-c", cmd],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Command line; the terminal defaults to CONTEXT_HANDOFF_TERMINAL, else the one this session runs in."""
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--brief", required=True, help="absolute path of the handoff brief")
    ap.add_argument("--cwd", default=os.getcwd(), help="working folder of the new session (default: this one)")
    ap.add_argument("--model", help="model of the new session, e.g. opus or claude-opus-5-5 (default: Claude Code's)")
    ap.add_argument("--effort", choices=EFFORTS, help="effort of the new session (default: Claude Code's)")
    ap.add_argument("--title", help="session name a human understands at once, e.g. 'Shop · Fix checkout'")
    ap.add_argument(
        "--terminal",
        default=os.environ.get("CONTEXT_HANDOFF_TERMINAL") or TERMINALS.get(os.environ.get("TERM_PROGRAM", "")),
        help="warp, ghostty or terminal (default: CONTEXT_HANDOFF_TERMINAL, else the terminal you are in)",
    )
    ap.add_argument(
        "--close-caller",
        action="store_true",
        help=f"end this session once the new one runs (context or role handoff); on by itself from {PREPARE_PCT} %%",
    )
    ap.add_argument("--dry-run", action="store_true", help="print the start script, open nothing")
    return ap.parse_args(argv)


def problem(a: argparse.Namespace, brief: Path, cwd: Path, claude: str | None) -> str:
    """What stops the spawn before any window opens; "" = nothing."""
    if not (brief.is_absolute() and brief.is_file()):
        return "brief not found or path not absolute"
    if not (cwd.is_absolute() and cwd.is_dir()):
        return "working folder not found or path not absolute"
    if a.terminal not in TERMINALS.values():
        return f"unknown terminal {a.terminal!r}: pass --terminal warp, ghostty or terminal"
    if not claude:
        return f"{CLAUDE!r} not found: install Claude Code or set CONTEXT_HANDOFF_CLAUDE"
    return ""


def new_run_dir() -> Path | None:
    """A fresh private folder for one spawn's start script, go token and marker: random name, owner only.
    None if its path has other than plain characters (Ghostty gets the script's path as it is)."""
    run = Path(tempfile.mkdtemp(prefix="context-handoff-", dir=RUN_PARENT))
    if SAFE_PATH.fullmatch(str(run)):
        return run
    run.rmdir()
    return None


def prune_state() -> None:
    """Remove the files status line and watcher left for sessions older than KEEP_DAYS."""
    cutoff = time.time() - KEEP_DAYS * 86400
    for p in STATE_DIR.iterdir():
        with contextlib.suppress(OSError):
            if p.is_file() and not p.is_symlink() and p.stat().st_mtime < cutoff:
                p.unlink()


def write_script(script: Path, text: str) -> None:
    """Write the start script and its go token into the private folder (owner-only, never over an existing
    file)."""
    with os.fdopen(os.open(script, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o700), "w") as f:
        f.write(text)
    os.close(os.open(script.with_suffix(".go"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))


def ensure_state_dir() -> bool:
    """Create the private folder if needed; False if it is not private (owner, rights or a link)."""
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    return private_dir(STATE_DIR)


def claim_handover(sid: str) -> bool:
    """Note at once, and only once, that this session hands over now; False if it already has."""
    try:
        os.close(os.open(handed_over(sid), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except FileExistsError:
        return False
    return True


def launch(terminal: str, script: Path) -> int:
    """Open the window and wait for the session; pid of the new session, 0 = it did not start. The start
    script, its go token, its marker and Warp's configuration are gone afterwards in every case: a window
    that comes late finds nothing to start."""
    marker, go = script.with_suffix(".pid"), script.with_suffix(".go")
    try:
        open_window(terminal, script)
        return wait_for_start(marker, go)
    except subprocess.TimeoutExpired:  # `open` hung, maybe after handing the window over: the marker decides
        return wait_for_start(marker, go)
    except (subprocess.CalledProcessError, OSError) as e:
        detail = getattr(e, "stderr", None) or e
        print(f"ERROR: could not open a {terminal} window: {str(detail).strip()}", file=sys.stderr)
        return 0
    finally:
        for leftover in (script, go, marker, warp_config(script)):
            leftover.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            script.parent.rmdir()


def handed_over(sid: str) -> Path:
    """Note that this session has handed over: the watcher goes quiet, and a second handoff is refused."""
    return STATE_DIR / f"{sid}.handed-over"


def main(argv: list[str] | None = None) -> int:
    """Check, write the start script, open the window, verify; with --close-caller end this session."""
    a = parse_args(argv)
    brief, cwd, claude = Path(a.brief).expanduser(), Path(a.cwd).expanduser(), shutil.which(CLAUDE)
    stop = problem(a, brief, cwd, claude)
    if stop or not claude:
        print(f"ERROR: {stop}", file=sys.stderr)
        return 1
    sid, pct = session_id(), caller_context_pct()
    if pct >= PREPARE_PCT and not a.close_caller:
        a.close_caller = True
        print(f"NOTE: this session is at {pct} % context, so this is a context handoff: it ends after the spawn.")
    prompt = f"Read the file {brief} COMPLETELY and carry out the task in it. The brief is your full assignment."
    argv_new = session_argv(claude, prompt, a.model, a.effort, a.title)
    if a.dry_run:  # shown with a sample folder; a real spawn makes a fresh one
        sample = Path(RUN_PARENT) / "context-handoff-XXXXXXXX" / "start.command"
        print(start_script(cwd, sample.with_suffix(".pid"), sample.with_suffix(".go"), argv_new), end="")
        return 0
    if not ensure_state_dir():
        print(f"ERROR: {STATE_DIR} is not a private folder of yours (owner, rights or a link)", file=sys.stderr)
        return 1
    prune_state()
    claimed = bool(a.close_caller and sid)
    if claimed and not claim_handover(sid):
        print(
            "ERROR: this session has already handed over and ends in a few seconds; do nothing more.", file=sys.stderr
        )
        return 1
    release = handed_over(sid) if claimed else None
    pid = 0
    try:
        pid = spawn(a.terminal, cwd, argv_new)
    finally:
        if release and pid == 0:  # nothing started, also after an error: this session may try once more
            release.unlink(missing_ok=True)
    if pid == UNSURE:
        print(
            "ERROR: a start script took its go token but did not confirm in time. It may still start: look for",
            file=sys.stderr,
        )
        print("the new window before you try again. This session counts as handed over.", file=sys.stderr)
        return 1
    if not pid:
        print("ERROR: the new session did not start (no marker, or its process was gone right after).", file=sys.stderr)
        return 1
    print(f"OK: new session runs in a {a.terminal} window (pid {pid}). Brief: {json.dumps(str(brief))}")
    if a.close_caller:
        close_caller(pid, release)
    return 0


def spawn(terminal: str, cwd: Path, argv_new: list[str]) -> int:
    """Write the start script into a fresh private folder, open the window, wait; pid of the new session."""
    run = new_run_dir()
    if not run:
        print(f"ERROR: the folder for the start script under {RUN_PARENT} has odd characters", file=sys.stderr)
        return 0
    script = run / "start.command"
    write_script(script, start_script(cwd, script.with_suffix(".pid"), script.with_suffix(".go"), argv_new))
    return launch(terminal, script)


if __name__ == "__main__":
    sys.exit(main())

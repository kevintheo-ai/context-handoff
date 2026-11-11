# context-handoff

context-handoff is a free, open-source Claude Code skill (`/handoff`) with a hook and a small status line helper. When a session's context reaches 85 %, it prepares a handoff. At the latest at 89 % it hands over to a fresh Claude Code session in a new terminal window. This runs by itself. The old session then ends itself and its window closes.

## What it does

The rule: from 85 % context a session prepares the handoff, at the latest at 89 % it hands over, with the same model and effort.

There are three kinds of handoff.

- A task handoff is for when you want a task done in a session of its own. Your current session keeps working.
- A context handoff runs when the watcher reports 85 % or 89 %. It asks you nothing, and the old session ends itself after the spawn.
- A role handoff makes the new session take over this session's job, for example a fresh lead. It runs like a context handoff, at any context level.

Every handoff starts the new session on a brief, a Markdown file. A task brief needs one or two sentences and one or two pointers, and the new session asks you for the rest. A context or role brief holds the full state: what is done, decisions and why, what is open, the next step, your rules, and the handoff rule itself.

## How I use it: a Team-Lead and a Key account

I run several Claude Code windows per project at the same time. One is the Team-Lead: it runs the project, keeps the decision log and hands tasks to worker windows with /handoff. I run it on Claude Fable 5.2 with effort max.

Some windows run for a long time, for example a lab that runs model tests. They do not talk to me in their own window. Their questions, approvals and requests (a login, a click) go to one window, the Key account. It bundles them and asks me in one question dialog, passes my answer back and reports it to the Team-Lead, which writes it into the decision log. I run the Key account on Claude Sonnet 5.5 with effort xhigh.

Worker windows for a short task talk to me directly. When the task is done, they save their work and close their own window. Every window follows the same context rule. When one hands over, the fresh window takes over the same job and the old one closes.

This repo is built from the handoff skill I run every day on my Mac. The numbers below come from my private version, not from this repo. The private skill has run since 31 August 2026. On 11 November 2026 I counted 534 brief files and 528 start markers; the oldest is a self-test, so more than 500 sessions started this way. The context watcher has run since 6 October 2026, and the old window has closed itself since 21 October 2026. Since 7 November 2026 the spawn script switches the closing on by itself at 85 %. The reason: in a check of four days, 13 of 15 context handoffs were clean; in the other 2 the session had left out the closing, and I had to close the old window myself.

My open-source tool Lookout sorts these windows on my screen: https://github.com/kevintheo-ai/lookout

## What's inside

| File | What it is |
| - | - |
| `skills/handoff/SKILL.md` | The skill |
| `skills/handoff/brief-template.md` | The brief template |
| `skills/handoff/scripts/handoff_spawn.py` | Opens the new window and checks the start. Standard library only |
| `hooks/context-handoff-watch.sh` | The watcher, a PostToolUse hook |
| `statusline/save-status.sh` | A status line that saves the session's status for the watcher |
| `tests/` | 67 offline tests (pytest) |

## How it works

### Status line

Claude Code passes a JSON status to the status line command, with the context fill in percent, the model and the effort. `save-status.sh` saves it as `~/.claude/context-handoff/<session id>.json`, in a folder only you can write to and a file only you can read, and prints one line. If you already have a status line, copy its marked lines into yours.

### Watcher

The watcher reads that file after every tool call. From 85 % it tells the agent once to prepare: start no new step, finish the running one, wait for its own background jobs, save the work (commit, notes). From 89 % it says to hand over now, again at every new percent point. Each message carries the exact flags for the spawn: `--model`, `--effort` and `--close-caller`. The watcher never blocks a tool call and stays silent on any error.

### Brief and spawn

The agent writes the brief and runs the spawn script. For each spawn the script makes a fresh private folder under `/tmp` with a random name and puts a one-time start script and a go token into it. The start script deletes itself when it runs, takes the go token, writes the new session's pid into a marker file and starts `claude` in the working folder with the model, the effort, a session name and the prompt to read the brief. The window opens in the terminal the session runs in.

### Start check

The spawn counts as done (exit 0) when the marker is there within 60 seconds and the process still runs 3 seconds later. If no marker came in time, the script withdraws the go token, so a window that comes late finds nothing to start: exit 1, nothing runs, one more try is safe. If the start script had already taken the token, the script waits 30 seconds more. If the marker still does not come, it says the start may still come, so look for the new window before trying again.

### Closing the old session

With the close-caller flag the script waits 10 seconds and then ends the calling session with a hangup signal (SIGHUP), but only if the new session still runs with the same process id and start time as at the start. If the old session ran directly in the window's login shell, that shell ends too, and the window closes. The script switches `--close-caller` on by itself when the calling session is at 85 % or more.

A session hands over once. Before a handoff with the close-caller flag the script notes it, in one step that only one spawn can win, so a second such spawn from the same session is refused and the watcher goes quiet for that session. If nothing started, or the new session ended within the 10 seconds, the note is removed again and the old session works on.

### Warp

The script writes a one-time Warp launch configuration (one window, one tab), opens it and deletes it after the start.

### Ghostty

The script opens a window through Ghostty's AppleScript, with the start script as the window's command. Ghostty keeps such a window open after its command ended, so the start script names its terminal and closes the window through AppleScript as its last step, but only a window that holds nothing else. With a tab or split you opened next to it, the window stays.

### Terminal

The script opens the start script as a `.command` file. Terminal keeps a window open after its shell ended unless the profile says otherwise. After a handoff the script closes the old window, but only a window with one tab whose shell has already ended.

## Install

You need macOS, Claude Code (tested with 2.1.322), Python 3.9 or newer, bash, jq, and a terminal: Warp, Ghostty 1.3 or newer, or Terminal.

```bash
git clone https://github.com/kevintheo-ai/context-handoff.git
cd context-handoff
mkdir -p ~/.claude/skills ~/.claude/hooks
cp -R skills/handoff ~/.claude/skills/
cp hooks/context-handoff-watch.sh statusline/save-status.sh ~/.claude/hooks/
```

Then merge these settings into `~/.claude/settings.json`. The env block is optional.

```json
{
  "statusLine": { "type": "command", "command": "~/.claude/hooks/save-status.sh" },
  "hooks": {
    "PostToolUse": [
      { "matcher": "*", "hooks": [{ "type": "command", "command": "~/.claude/hooks/context-handoff-watch.sh", "timeout": 5 }] }
    ]
  },
  "env": { "CONTEXT_HANDOFF_PREPARE": "85", "CONTEXT_HANDOFF_LIMIT": "89" }
}
```

Settings are environment variables, best put in the env block so every session sees them: `CONTEXT_HANDOFF_PREPARE` (default 85), `CONTEXT_HANDOFF_LIMIT` (default 89), `CONTEXT_HANDOFF_TERMINAL` (warp, ghostty or terminal), `CONTEXT_HANDOFF_CLAUDE` (default `claude`, for your own launcher) and `CONTEXT_HANDOFF_DIR` (default `~/.claude/context-handoff`).

macOS may ask once whether your terminal may control Ghostty or Terminal (Automation).

## Run the tests

The 67 offline tests need pytest. They open no window and end only processes they started themselves.

```bash
python3 -m pytest -q
```

## What was tested

On 11 November 2026, on macOS 27.0.1 with Claude Code 2.1.322, Warp 0.2026.11.04.08.17.01, Ghostty 1.3.1 and Terminal 2.15:

- In each of the three terminals, a session A in a window started a session B with the close-caller flag. B started, A ended about 10 seconds later and its window closed. In Ghostty this also worked from a cold start and with `confirm-close-surface = always` in the Ghostty config.
- A real Claude Code session (Sonnet 5.5) started in a new Warp window from a brief, with model, effort and session name, and answered. Its window closed after the session ended.
- The 67 offline tests pass. In the end-to-end tests the spawn script ran with the system Python 3.9 of macOS.
- While the Mac was under very heavy load (load average above 270), Warp sometimes failed to start the shell in a new window. The spawn then reported that nothing started, withdrew the go token, and nothing started twice.

## Known limitations

- macOS only, three terminals. For other terminals, the window part is one function in the spawn script (`open_window`).
- The context number comes from the status line. Without the helper there is no watcher message and no automatic closing at 85 %.
- The new session does not report back to the old one. The only start check is the marker and the process.
- A session you end yourself in Terminal leaves its window open if your Terminal profile says so (Settings, Profiles, Shell, When the shell exits).
- A session that runs in a sub-shell of its window ends after a handoff, but its window stays open.
- Only `CLAUDE_CONFIG_DIR` is passed on. Everything else the new session gets from its terminal.
- The script finds the calling session by its process name, which starts with `claude`. If it finds none, the new session runs, the old one stays open with a note to close its window by hand, and it does not hand over again.
- Under very heavy load a terminal can take longer than 60 seconds to open a window. The spawn then reports that nothing started, and nothing starts later.

## How it was built

I designed it and set the rules, and I decide and accept the result. Claude Code wrote the code, and the tests in the three terminals ran on my Mac. Four models from other labs reviewed the code: Codex, GLM, Muse and Grok. In two rounds they reported 32 real problems and 3 false alarms. All 32 are fixed, each with a test where a test can show it.

## License

MIT, Copyright 2026 Kevin Theobald.

Kevin Theobald ([LinkedIn](https://www.linkedin.com/in/kevintheo) · [kevintheo.com](https://kevintheo.com))

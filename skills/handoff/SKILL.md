---
name: handoff
description: Hands work to a fresh, interactive Claude Code session in a new terminal window (Warp, Ghostty or Terminal on macOS) that starts on a written brief. Use when the user says "/handoff" or wants a task done in its own session or window, and when the context-handoff watcher reports 85 % or 89 % context. Not for background subagents.
---

# /handoff

Write a brief, open a new window that starts `claude` on it, verify that it runs. From then on the new
session belongs to the user, who talks to it in its own window. The two sessions exchange no messages.

## Which handoff

- **Task handoff:** the user wants a task done in a session of its own. This session keeps working:
  no `--close-caller`.
- **Context handoff:** the watcher says 85 % (prepare) or 89 % (hand over now). Same model and effort as
  this session, no question to the user, `--close-caller`.
- **Role handoff:** the new session takes over this session's job, for example a fresh lead. Run it like
  a context handoff, at any context level.

With `--close-caller` this session ends about 10 s after the new one runs, and its window closes. The
script turns it on by itself when this session is at 85 % or more.

## Context handoff

1. From 85 %: start no new step. Finish the running one. Wait for your own background jobs (subagents,
   checks) or hand them over in the brief.
2. Save the work: commit, update notes and logs. After this, change nothing but the brief.
3. Hand over at the next verified point, at the latest at 89 %. If the running step does not fit any
   more, write its exact state into the brief and hand over anyway.
4. Model and effort: copy the flags from the watcher's message as they are. Without one, read them and put
   the model in single quotes (`opus[1m]` is a glob to zsh):
   `jq -r '.model.id, .effort.level' "${CONTEXT_HANDOFF_DIR:-$HOME/.claude/context-handoff}/$CLAUDE_CODE_SESSION_ID.json"`
5. Spawn with `--close-caller`, then do nothing more: this session is over. A second spawn of a session that
   has handed over is refused, and the watcher goes quiet.

## 1. Write the brief

Start from `brief-template.md` next to this file and save it with the Write tool as
`~/.claude/handoffs/YYYY-MM-DD_HHMM_<slug>.md` (absolute path).

- Task handoff: the task in one or two sentences and one or two pointers. The new session asks the user
  for the rest, so collect nothing for it.
- Context or role handoff: complete, because this session's knowledge ends with it. State, decisions
  with their reasons, open points, the next concrete step, the user's rules, and this handoff rule.

## 2. Spawn

```bash
python3 ~/.claude/skills/handoff/scripts/handoff_spawn.py \
  --brief '<absolute path of the brief>' --cwd '<absolute working folder>' --title '<Project · Task>' \
  [--model '<model>' --effort <effort>] [--close-caller]
```

- Every value in single quotes, as above: a folder, title or model with spaces, `$`, `;` or `[` then reaches
  the script as it is. A single quote inside a value becomes `'\''`.
- `--title`: a name a human understands at once, 2 to 5 words, no date. It becomes the session name.
- The window opens in the terminal this session runs in. `--terminal warp|ghostty|terminal` or the
  variable `CONTEXT_HANDOFF_TERMINAL` choose another.
- `--dry-run` prints the start script and opens nothing.

## 3. Verify

Exit 0 = the new session runs; the script checked the marker and the process. That is the whole check:
the new session does not report back here. Exit 1 = no session runs: read the error, fix the cause and
try once more. Every spawn opens a real window, so one retry at most.

## 4. Tell the user

One line: the window is open, what the new session does, and that they talk to it there. Then go on
with your own work (task handoff), or stop (context and role handoff).

#!/bin/bash
# context-handoff watcher: a Claude Code PostToolUse hook (matcher "*").
#
# Reads the context fill that the status line saved for this session and tells the agent:
#   from 85 %: once, prepare the handoff
#   from 89 %: hand over now, again at every new percent point
# with the session's model and effort, so /handoff starts the new session the same way.
# Subagents stay silent: the number belongs to the main session.
# Every error ends silently with exit 0, so the hook never gets in the way of a tool call.
#
# Settings (best in the "env" block of ~/.claude/settings.json, so every session sees them):
#   CONTEXT_HANDOFF_PREPARE (85), CONTEXT_HANDOFF_LIMIT (89), CONTEXT_HANDOFF_DIR (~/.claude/context-handoff)

dir=${CONTEXT_HANDOFF_DIR:-$HOME/.claude/context-handoff}
prepare=${CONTEXT_HANDOFF_PREPARE:-85}
limit=${CONTEXT_HANDOFF_LIMIT:-89}
[[ "$prepare" =~ ^[0-9]+$ && "$limit" =~ ^[0-9]+$ ]] || exit 0

input=$(cat)
# Separator \x1f instead of a tab: a tab counts as white space for read and swallows empty fields.
IFS=$'\x1f' read -r sid agent < <(jq -r '[.session_id // "", .agent_id // ""] | join("\u001f")' <<<"$input" 2>/dev/null)
[[ "$sid" =~ ^[A-Za-z0-9_-]+$ && -z "$agent" ]] || exit 0
# Only a real folder of this user that nobody else can write to: nobody else may plant a number here.
[[ -d "$dir" && ! -L "$dir" && -O "$dir" ]] || exit 0
mode=$(stat -f %Lp "$dir" 2>/dev/null)
[[ "$mode" =~ ^[0-7]+$ ]] && (( (8#$mode & 8#022) == 0 )) || exit 0
status="$dir/$sid.json"
[ -f "$status" ] || exit 0
# Already handed over (the spawn script notes it): the session ends in a few seconds, nothing more to say.
[ -e "$dir/$sid.handed-over" ] && exit 0

IFS=$'\x1f' read -r pct model effort < <(jq -r '[(.context_window.used_percentage // 0 | floor | tostring), (.model.id // ""), (.effort.level // "")] | join("\u001f")' "$status" 2>/dev/null)
[[ "$pct" =~ ^[0-9]+$ ]] || exit 0
[ "$pct" -ge "$prepare" ] || exit 0

flags=""
model_re='^[]A-Za-z0-9._:@/[-]+$'  # e.g. claude-opus-5-5, opus[1m] or a provider path; nothing else goes in
[[ "$model" =~ $model_re ]] && flags+="--model '$model' "  # quoted: zsh would read opus[1m] as a glob
[[ "$effort" =~ ^(low|medium|high|xhigh|max)$ ]] && flags+="--effort $effort "
flags+="--close-caller"

if [ "$pct" -ge "$limit" ]; then
  mark="$dir/$sid.limit-$pct"
  msg="CONTEXT ${pct} %: the limit of ${limit} % is reached, hand over NOW. Start no new step. Wait for your own background jobs (subagents, checks) or put them into the brief. Save the work if you have not yet (commit, notes). Write the brief with the current state, then hand over with /handoff: ${flags}. This window then closes itself; do nothing after the spawn."
else
  mark="$dir/$sid.prepared"
  msg="CONTEXT ${pct} %: prepare the handoff. Start no new step; finish the running one, wait for your own background jobs (subagents, checks) and save the work (commit, notes). After that, change nothing but the brief. Hand over with /handoff at the next verified point, at the latest at ${limit} %: ${flags}. This window then closes itself; do nothing after the spawn."
fi

# The message is ready before its mark is set, so a failure in between cannot swallow it. Each message goes
# out once: creating the mark fails if it exists (noclobber), so two hooks at the same moment cannot both send.
out=$(jq -nc --arg m "$msg" '{hookSpecificOutput: {hookEventName: "PostToolUse", additionalContext: $m}}') || exit 0
(set -C; : >"$mark") 2>/dev/null || exit 0
printf '%s\n' "$out"
exit 0

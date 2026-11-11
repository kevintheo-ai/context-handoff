#!/bin/bash
# Claude Code status line for context-handoff. Saves the status JSON that Claude Code passes in (context
# fill, model, effort) where the watcher hook and /handoff read it, then prints one short line.
# Already have a status line? Keep it and copy the lines marked SAVE into it, after `input=$(cat)`.

input=$(cat)
dir=${CONTEXT_HANDOFF_DIR:-$HOME/.claude/context-handoff}                                 # SAVE
sid=$(jq -r '.session_id // empty' <<<"$input" 2>/dev/null)                               # SAVE
# The folder is made private in one step (never chmod: that would follow a planted link); files only for you.
(umask 077; mkdir -p "$(dirname "$dir")" && mkdir -m 700 "$dir") 2>/dev/null             # SAVE
[[ "$sid" =~ ^[A-Za-z0-9_-]+$ && -d "$dir" && ! -L "$dir" && -O "$dir" && $(stat -f %Lp "$dir") =~ ^[0-7]*[0145][0145]$ ]] && (umask 077; printf '%s' "$input" >"$dir/$sid.json.$$") && mv -f "$dir/$sid.json.$$" "$dir/$sid.json"  # SAVE

jq -r '"\(.model.display_name // "?") · context \(.context_window.used_percentage // 0 | floor) %"' <<<"$input" 2>/dev/null

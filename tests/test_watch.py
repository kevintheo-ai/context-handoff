"""The watcher hook and the status line: thresholds, once-only messages, silence where it belongs."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from conftest import ROOT

HOOK = ROOT / "hooks" / "context-handoff-watch.sh"
STATUSLINE = ROOT / "statusline" / "save-status.sh"


def status(state: Path, sid: str, pct: float, model: str = "claude-opus-5-5", effort: str = "max") -> None:
    data = {"session_id": sid, "context_window": {"used_percentage": pct}, "model": {"id": model}}
    if effort:
        data["effort"] = {"level": effort}
    (state / f"{sid}.json").write_text(json.dumps(data))


def hook(state: Path, sid: str = "s1", agent: str = "", **env: str) -> str:
    payload = {"session_id": sid, "tool_name": "Bash"}
    if agent:
        payload["agent_id"] = agent
    result = subprocess.run(
        ["bash", str(HOOK)], input=json.dumps(payload), capture_output=True, text=True, timeout=10,
        env={**os.environ, "CONTEXT_HANDOFF_DIR": str(state), **env},
    )  # fmt: skip
    assert result.returncode == 0
    if not result.stdout.strip():
        return ""
    return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


def test_silent_below_the_prepare_threshold(isolated):
    status(isolated, "s1", 84.9)
    assert hook(isolated) == ""


def test_prepare_message_comes_once_with_model_and_effort(isolated):
    status(isolated, "s1", 85.2)
    msg = hook(isolated)
    assert msg.startswith("CONTEXT 85 %: prepare the handoff")
    assert "--model 'claude-opus-5-5' --effort max --close-caller" in msg
    status(isolated, "s1", 87)
    assert hook(isolated) == ""


def test_limit_message_at_every_new_point(isolated):
    status(isolated, "s1", 89)
    assert "hand over NOW" in hook(isolated)
    assert hook(isolated) == ""
    status(isolated, "s1", 90.7)
    assert hook(isolated).startswith("CONTEXT 90 %")


def test_subagents_and_unknown_sessions_stay_silent(isolated):
    status(isolated, "s1", 95)
    assert hook(isolated, agent="a-1") == ""
    assert hook(isolated, sid="no-file") == ""
    assert hook(isolated, sid="../s1") == ""


def test_own_thresholds(isolated):
    status(isolated, "s1", 81)
    assert hook(isolated, CONTEXT_HANDOFF_PREPARE="80", CONTEXT_HANDOFF_LIMIT="88").startswith("CONTEXT 81 %: prepare")
    status(isolated, "s1", 88)
    assert "limit of 88 %" in hook(isolated, CONTEXT_HANDOFF_PREPARE="80", CONTEXT_HANDOFF_LIMIT="88")


def test_odd_model_and_missing_effort_are_left_out(isolated):
    status(isolated, "s1", 86, model="opus; rm -rf ~", effort="")
    msg = hook(isolated)
    assert "--model" not in msg and "--effort" not in msg and "--close-caller" in msg


def test_a_linked_or_shared_folder_is_ignored(tmp_path, isolated):
    status(isolated, "s1", 95)
    link = tmp_path / "link"
    link.symlink_to(isolated)
    assert hook(link) == ""
    isolated.chmod(0o777)
    try:
        assert hook(isolated) == "", "a folder others can write to may hold a planted number"
    finally:
        isolated.chmod(0o700)


def test_a_model_with_a_provider_path_keeps_its_flag(isolated):
    status(isolated, "s1", 86, model="us.anthropic.claude-opus/v1:0")
    assert "--model 'us.anthropic.claude-opus/v1:0' " in hook(isolated)


def test_a_model_with_brackets_is_quoted_for_zsh(isolated):
    status(isolated, "s1", 86, model="opus[1m]")
    assert "--model 'opus[1m]' " in hook(isolated)


def test_silent_once_the_session_has_handed_over(isolated):
    status(isolated, "s1", 92)
    (isolated / "s1.handed-over").touch()
    assert hook(isolated) == ""


def test_statusline_saves_the_json_and_prints_a_line(isolated):
    payload = {"session_id": "s9", "model": {"display_name": "Opus 5.5"}, "context_window": {"used_percentage": 42.6}}
    result = subprocess.run(
        ["bash", str(STATUSLINE)], input=json.dumps(payload), capture_output=True, text=True, timeout=10,
        env={**os.environ, "CONTEXT_HANDOFF_DIR": str(isolated)},
    )  # fmt: skip
    assert result.stdout.strip() == "Opus 5.5 · context 42 %"
    assert json.loads((isolated / "s9.json").read_text()) == payload
    assert sorted(p.name for p in isolated.iterdir()) == ["s9.json"], "no temp file left"


def test_statusline_creates_a_nested_private_folder(tmp_path):
    nested = tmp_path / "a" / "b"
    subprocess.run(
        ["bash", str(STATUSLINE)], input=json.dumps({"session_id": "s5"}), capture_output=True, text=True,
        env={**os.environ, "CONTEXT_HANDOFF_DIR": str(nested)}, timeout=10,
    )  # fmt: skip
    assert (nested / "s5.json").is_file()
    assert nested.stat().st_mode & 0o777 == 0o700
    assert (nested / "s5.json").stat().st_mode & 0o777 == 0o600, "the status is for its owner only"


def test_statusline_never_changes_a_folder_behind_a_planted_link(tmp_path):
    target = tmp_path / "someone-elses-idea"
    target.mkdir()
    target.chmod(0o755)
    link = tmp_path / "planted"
    link.symlink_to(target)
    subprocess.run(
        ["bash", str(STATUSLINE)], input=json.dumps({"session_id": "s6"}), capture_output=True, text=True,
        env={**os.environ, "CONTEXT_HANDOFF_DIR": str(link)}, timeout=10,
    )  # fmt: skip
    assert target.stat().st_mode & 0o777 == 0o755, "no chmod through the link"
    assert list(target.iterdir()) == [], "nothing written through the link"


def test_statusline_ignores_an_odd_session_id(isolated):
    subprocess.run(
        ["bash", str(STATUSLINE)], input=json.dumps({"session_id": "../x"}), capture_output=True, text=True,
        env={**os.environ, "CONTEXT_HANDOFF_DIR": str(isolated)}, timeout=10,
    )  # fmt: skip
    assert list(isolated.iterdir()) == []

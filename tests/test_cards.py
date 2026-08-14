"""The cards' command-line door: the same savers the app uses, minus the need for the app."""

import pytest

from excephalon.cards import main, without_rows


def test_rows_named_by_unique_fragment_are_dropped_and_the_rest_stand():
    kept = without_rows("- keep me" + chr(10) + "- drop me please" + chr(10) + "- also keep",
                        ["drop me"])
    assert kept == "- keep me" + chr(10) + "- also keep"


def test_a_fragment_matching_zero_or_many_rows_refuses_the_whole_edit():
    rows = "- alpha one" + chr(10) + "- alpha two"
    with pytest.raises(SystemExit):
        without_rows(rows, ["alpha"])  # two hits: refuse rather than guess
    with pytest.raises(SystemExit):
        without_rows(rows, ["beta"])  # zero hits: same refusal
    with pytest.raises(SystemExit):
        without_rows(rows, ["alpha one", "gamma"])  # one bad fragment poisons the run


def test_tick_checks_an_enhancement_off_by_number(tmp_path, monkeypatch):
    from excephalon import cards, memory

    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements he wants (roadmap, not now)" + chr(10)
                       + "- [ ] #7 louder voice" + chr(10), encoding="utf-8")
    monkeypatch.setattr(memory, "DEFAULT_PROFILE_PATH", profile)
    monkeypatch.setattr(cards, "complete_enhancement_by_id",
                        lambda item_id: memory.complete_enhancement_by_id(item_id, path=profile))

    assert main(["tick", "7"]) == 0
    assert "- [x] #7 louder voice" in profile.read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["tick", "99"])  # no such item: say so, change nothing


def test_tick_reaches_a_project_card_when_one_is_named(tmp_path, monkeypatch):
    # "it did not check them off in the Projects tab" - the CLI door could only ever tick the
    # Enhancements card, so a finished Highdeas task had no terminal-side tick at all. The card
    # name resolves by stem through the same saver the brain's tool uses.
    from excephalon import cards, memory

    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements he wants\n- [ ] #7 louder voice\n\n"
                       "## Project: Highdeas\n- [ ] #7 the spinner holds\n", encoding="utf-8")
    monkeypatch.setattr(cards, "complete_enhancement_by_id",
                        lambda item_id, **where: memory.complete_enhancement_by_id(
                            item_id, path=profile, **where))

    assert main(["tick", "7", "Highdeas"]) == 0

    text = profile.read_text(encoding="utf-8")
    assert "- [x] #7 the spinner holds" in text
    assert "- [ ] #7 louder voice" in text  # the Enhancements card's #7 stands untouched


def test_drop_instruction_rewrites_the_card_through_its_own_saver(tmp_path, monkeypatch):
    from excephalon import cards

    card = tmp_path / "persona.md"
    card.write_text("- first rule" + chr(10) + "- second rule" + chr(10), encoding="utf-8")
    monkeypatch.setattr(cards, "load_persona_additions",
                        lambda: card.read_text(encoding="utf-8"))
    monkeypatch.setattr(cards, "save_persona_additions",
                        lambda text, path: card.write_text(text.rstrip() + chr(10),
                                                           encoding="utf-8"))

    assert main(["drop-instruction", "second rule"]) == 0
    assert card.read_text(encoding="utf-8") == "- first rule" + chr(10)


def test_retire_wraps_an_agent_up_with_no_app_running(tmp_path):
    import json

    from excephalon.cards import retire

    logs = tmp_path / "agent-logs"
    logs.mkdir()
    (logs / "fixer.log").write_text("[10:00:00] ENTITY> fix it" + chr(10), encoding="utf-8")
    wt = tmp_path / "wt"
    wt.mkdir()
    state = tmp_path / "agents.json"
    state.write_text(json.dumps([{"name": "fixer", "cwd": str(wt)},
                                 {"name": "other", "cwd": "/wt/other"}]), encoding="utf-8")
    ran = []

    done = retire("fixer", state_path=state, log_dir=logs, run=lambda cmd, **kw: ran.append(cmd))

    assert not (logs / "fixer.log").exists()
    assert (tmp_path / "agent-logs-archive" / "fixer.log").exists()
    assert [entry["name"] for entry in json.loads(state.read_text(encoding="utf-8"))] == ["other"]
    assert ran == [["git", "-C", str(wt), "worktree", "remove", str(wt)]]
    assert done == ["log archived", "dropped from the fleet record", "worktree removed"]


def test_a_worktree_that_refuses_removal_does_not_fail_the_wrap_up(tmp_path):
    import json

    from excephalon.cards import retire

    logs = tmp_path / "agent-logs"
    logs.mkdir()
    wt = tmp_path / "wt"
    wt.mkdir()
    state = tmp_path / "agents.json"
    state.write_text(json.dumps([{"name": "fixer", "cwd": str(wt)}]), encoding="utf-8")

    def refuse(cmd, **kw):
        raise RuntimeError("dirty worktree")

    done = retire("fixer", state_path=state, log_dir=logs, run=refuse)

    assert done == ["dropped from the fleet record", "worktree left for a sweep (dirty or locked)"]

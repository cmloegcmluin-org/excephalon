import asyncio
import os.path
from pathlib import Path

from excephalon.actions import _resolve, fleet_actions, take_care_spec


def test_take_care_spec_carries_the_task_its_enhancement_and_the_project():
    # A Projects-tab robot clicked: the task's own words are the task and the enhancement it ticks
    # off, and the card's project rides along so the tick lands right. The NAME is NOT slugified
    # here - a robot click has no brain in the loop, so the caller distills it through the namer.
    spec = take_care_spec("Highdeas", "smart grouping of ideas")
    assert spec == {"task": "smart grouping of ideas",
                    "enhancement": "smart grouping of ideas", "project": "Highdeas"}


def test_take_care_spec_maps_excephalons_own_card_to_the_enhancements_roadmap():
    # Excephalon's card is the Enhancements roadmap, not a "## Project:" section, so the desk names
    # it by project=None - the same None its own tasks tick against.
    for own in ("Excephalon", "entity", "yourself"):
        assert take_care_spec(own, "live captions")["project"] is None


def test_take_care_spec_has_nothing_to_start_for_an_empty_task():
    # An empty, not-yet-saved row is nothing to put an agent on.
    assert take_care_spec("Highdeas", "   ") is None
    assert take_care_spec("Highdeas", "") is None


class FakeDesk:
    def __init__(self, known=("gdoc-export",)):
        self.started = []
        self.told = []
        self.news_dropped = []
        self.handed = []
        self.chosen = []
        self.retired = []
        self.renamed = []
        self.presented = []
        self.verdicts = []
        self._known = set(known)

    def start(self, name, cwd, task, enhancement=None, project=None):
        self.started.append((name, cwd, task, enhancement, project))
        return name  # the real desk returns the name it settled on (unique); the fake keeps it as-is

    def send(self, name, message):
        self.told.append((name, message))
        return name in self._known

    def drop_news(self, name):
        self.news_dropped.append(name)

    def hand_over_news(self, name):
        self.handed.append(name)
        return name in self._known

    def choose(self, model=None, effort=None):
        self.chosen.append((model, effort))
        return "Fable on max"

    def running_on(self):
        return "Opus on high"

    def retire(self, name):
        self.retired.append(name)
        return name in self._known

    def rename(self, name, to):
        self.renamed.append((name, to))
        return True

    def present(self, name, steps):
        from excephalon.delivery import DeliveryError

        if name not in self._known:
            raise DeliveryError(f"no agent called {name} is at the desk")
        self.presented.append((name, steps))

    def verdict(self, name, approved, feedback=""):
        from excephalon.delivery import DeliveryError

        if name not in self._known:
            raise DeliveryError("no verdict can be recorded - nothing has been presented")
        self.verdicts.append((name, approved, feedback))


def _call(tool, **args):
    reply = asyncio.run(tool.handler(args))
    [content] = reply["content"]
    return content["text"]


class FakeForeman:
    def __init__(self):
        self.considered = []

    def consider(self, name, question):
        self.considered.append((name, question))


class FakeErrands:
    def __init__(self):
        self.chores = []

    def run(self, chore):
        self.chores.append(chore)


def _tools(desk, foreman=None, errands=None, **kwargs):
    server, tools = fleet_actions(desk, foreman or FakeForeman(), errands or FakeErrands(),
                                  **kwargs)
    return {tool.name: tool for tool in tools}


def test_start_agent_distills_a_name_from_the_task_not_the_worktree_folder(tmp_path):
    # "agent names shouldn't be the name of the task with hyphens... distilled to 1-3 words, with
    # the Project prefixed." The name comes from the namer reading the task and the project - the
    # worktree keeps its own (git's) name, which the label no longer copies.
    desk = FakeDesk()
    worktree = tmp_path / "wt-2f9c1a"  # git's name for the tree, deliberately unlike the label
    worktree.mkdir()
    seen = []

    def namer(task, project=None):
        seen.append((task, project))
        return "highdeas-drive-link"

    tools = _tools(desk, resolve=lambda target: [str(worktree)], prepare=lambda path: None,
                   namer=namer)

    said = _call(tools["start_agent"], path=str(worktree), task="fix the drive link",
                 project="Highdeas")

    assert seen == [("fix the drive link", "Highdeas")]  # the namer read the task AND the project
    assert desk.started == [("highdeas-drive-link", str(worktree), "fix the drive link", None,
                             "Highdeas")]
    assert "highdeas-drive-link" in said


def test_start_agent_tags_the_agent_with_the_enhancement_it_takes_on(tmp_path):
    # When the agent is taking an item off the Enhancements list, that item rides along verbatim so
    # it ticks itself off the list when the work lands (agent_desk.retire). The name is the default
    # (mechanical) distillation of the task - "wire the neural voice" -> "wire-neural-voice".
    desk = FakeDesk()
    worktree = tmp_path / "wt-neural"
    worktree.mkdir()
    tools = _tools(desk, resolve=lambda target: [str(worktree)], prepare=lambda path: None)

    _call(tools["start_agent"], path=str(worktree), task="wire the neural voice",
          enhancement="Better voice")

    assert desk.started == [("excephalon-wire-neural-voice", str(worktree), "wire the neural voice",
                             "Better voice", None)]


def test_start_agent_leaves_the_tag_empty_when_no_enhancement_is_named(tmp_path):
    # Most work is not a listed enhancement; a blank tag must become no tag, never an empty-string
    # item the wrap-up then tries to tick off nothing with.
    desk = FakeDesk()
    worktree = tmp_path / "wt-oneoff"
    worktree.mkdir()
    tools = _tools(desk, resolve=lambda target: [str(worktree)], prepare=lambda path: None)

    _call(tools["start_agent"], path=str(worktree), task="a one-off fix", enhancement="  ")

    assert desk.started == [("excephalon-one-off-fix", str(worktree), "a one-off fix", None, None)]


def test_start_agent_makes_the_worktree_when_the_path_is_new(tmp_path):
    # New work means a new worktree cut from origin/main; the tool does the cutting so the model
    # never shells out.
    desk = FakeDesk()
    fresh = tmp_path / "new-feature"
    prepared = []
    tools = _tools(desk, resolve=lambda target: [str(fresh)],
                   prepare=lambda path: prepared.append(path))

    _call(tools["start_agent"], path=str(fresh), task="build it")

    assert prepared == [str(fresh)]


def test_start_agent_with_nowhere_to_start_says_so():
    desk = FakeDesk()
    tools = _tools(desk, resolve=lambda target: [], prepare=lambda path: None)

    said = _call(tools["start_agent"], path="~/nowhere", task="?")

    assert desk.started == []
    assert "couldn't find" in said.lower()


def test_tell_agent_reaches_the_agent_by_name():
    desk = FakeDesk()
    tools = _tools(desk)

    said = _call(tools["tell_agent"], name="gdoc-export", message="also clean up the folders")

    assert desk.told == [("gdoc-export", "also clean up the folders")]
    assert "gdoc-export" in said


def test_tell_agent_says_when_there_is_no_such_agent():
    # The model must never be told a message landed when it didn't - that is how "passed that to
    # the agent" got spoken about deliveries that never happened.
    desk = FakeDesk(known=())
    tools = _tools(desk)

    said = _call(tools["tell_agent"], name="ghost", message="hello?")

    assert "no agent" in said.lower()
    assert "ghost" in said


def test_choosing_a_model_governs_the_next_agent():
    desk = FakeDesk()
    tools = _tools(desk)

    said = _call(tools["set_next_agent_model"], choice="fable on max")

    assert desk.chosen == [("claude-fable-5", "max")]
    assert "Fable on max" in said


def test_a_choice_naming_no_model_changes_nothing():
    desk = FakeDesk()
    tools = _tools(desk)

    said = _call(tools["set_next_agent_model"], choice="the good one")

    assert desk.chosen == []
    assert "Opus on high" in said  # what they are still on, so the answer is real


def test_filing_an_improvement_lands_it_in_the_profile():
    desk = FakeDesk()
    filed = []
    tools = _tools(desk, file_enhancement=lambda item, stamp=None: filed.append(item) or True)

    said = _call(tools["file_improvement"], item="louder notification chime")

    assert filed == ["louder notification chime"]
    assert "filed" in said.lower()


def test_updating_the_persona_records_a_standing_instruction_under_its_name():
    # The gap this closes: Excephalon could file an enhancement but had no lever to change how it
    # itself behaves. A typed tool, like every other - it cannot be half-written or leak into the
    # voice, and it lands in the same overlay the window edits, always under the short bolded
    # name the Instructions card draws.
    desk = FakeDesk()
    added = []
    tools = _tools(desk, save_instruction=lambda name, rule: added.append((name, rule)) or False)

    said = _call(tools["update_persona"], name="Commit hashes",
                 instruction="never read a commit hash aloud")

    assert added == [("Commit hashes", "never read a commit hash aloud")]
    assert "persona" in said.lower() or "standing" in said.lower()


def test_updating_the_persona_says_when_it_rewrote_a_standing_row_instead():
    # Restating a rule already on the card rewrites that row in place (memory owns that), and the
    # reply says WHICH happened - "Added" about a rewrite would read as a duplicate just filed,
    # which is the exact confusion the rewrite exists to end.
    desk = FakeDesk()
    tools = _tools(desk, save_instruction=lambda name, rule: True)

    said = _call(tools["update_persona"], name="Sequential verdicts",
                 instruction="present one piece of work at a time")

    assert "rewrote" in said.lower() or "updated" in said.lower()
    assert "added" not in said.lower()


def test_updating_the_persona_relays_a_refusal_rather_than_crashing():
    # The saver refuses a call that yields no name or no rule (the malformed row the card once
    # gained); the tool hands the model that sentence so it can call again correctly.
    desk = FakeDesk()

    def refuse(name, rule):
        raise ValueError("a standing instruction needs its short bolded name")

    tools = _tools(desk, save_instruction=refuse)

    said = _call(tools["update_persona"], name="", instruction="a rule with no name")

    assert "name" in said.lower()


def test_telling_an_agent_drops_its_held_news_because_he_has_moved_past_it():
    # Held news predates the message he just sent through: an agent's "Done." was still offered
    # as an update after his feedback had already put it back to work - "surely there's no update
    # for smart grouping. You just sent off the latest message to it." Delivered words make the
    # held sentence history; the agent's next report is the news.
    desk = FakeDesk()
    tools = _tools(desk)

    _call(tools["tell_agent"], name="gdoc-export", message="strip the extra suggestions")

    assert desk.news_dropped == ["gdoc-export"]


def test_delivering_an_update_hands_the_held_news_to_the_app_to_speak():
    # "give me the update on the smart grouping" went to the brain, which retold the update in
    # its own words - and the app then spoke its held copy too, 13 seconds later. The tool is the
    # brain's one honest answer: the app speaks the held copy, the brain adds nothing.
    desk = FakeDesk()
    tools = _tools(desk)

    said = _call(tools["deliver_update"], name="gdoc-export")

    assert desk.handed == ["gdoc-export"]
    assert "do not repeat" in said.lower()


def test_delivering_an_update_with_nothing_held_says_to_answer_from_the_briefing():
    desk = FakeDesk()
    tools = _tools(desk)

    said = _call(tools["deliver_update"], name="nobody-here")

    assert "nothing is waiting" in said.lower()
    assert "briefing" in said.lower()


def test_telling_a_missing_agent_drops_nothing():
    # No message was delivered, so nothing about the held news changed either.
    desk = FakeDesk()
    tools = _tools(desk)

    _call(tools["tell_agent"], name="nobody-here", message="hello?")

    assert desk.news_dropped == []


def test_dropping_an_instruction_deletes_the_row_those_words_name():
    # "I don't have a way to remove standing instructions - I can only add new ones" - said to the
    # user about a duplicate he then had to delete by hand. The card is Excephalon's to edit in
    # both directions now.
    desk = FakeDesk()
    dropped = []
    tools = _tools(desk, drop_instruction=lambda words: dropped.append(words) or 1)

    said = _call(tools["drop_instruction"], words="Sequential verdicts")

    assert dropped == ["Sequential verdicts"]
    assert "dropped" in said.lower() or "deleted" in said.lower()


def test_dropping_an_instruction_refuses_to_guess_and_says_why():
    # Zero matches and several matches are different failures, and the model can only fix its call
    # if the reply says which happened.
    desk = FakeDesk()
    tools = _tools(desk, drop_instruction=lambda words: 0)
    said = _call(tools["drop_instruction"], words="a rule never filed")
    assert "no " in said.lower()

    tools = _tools(desk, drop_instruction=lambda words: 3)
    said = _call(tools["drop_instruction"], words="keep answers")
    assert "3" in said
    assert "nothing" in said.lower() or "not" in said.lower()


def test_every_tool_the_server_builds_is_one_the_brain_is_allowed_to_call():
    # allowed_tools is TOOL_NAMES; a tool built here but missing from that tuple would exist and
    # be silently uncallable - a lever the brain reaches for and finds welded still.
    from excephalon.actions import SERVER, TOOL_NAMES

    desk = FakeDesk()
    tools = _tools(desk)

    assert {f"mcp__{SERVER}__{name}" for name in tools} == set(TOOL_NAMES)


def test_remembering_a_fact_appends_it_to_what_entity_has_learned():
    # Write access to its memory: told a durable fact, Excephalon can keep it now, not only at the
    # end-of-session consolidation. Facts arrive as a list, the way `append_learned` takes them.
    desk = FakeDesk()
    remembered = []
    tools = _tools(desk, remember_fact=lambda facts: remembered.extend(facts))

    said = _call(tools["remember"], fact="they keep their coffee mug on the left")

    assert remembered == ["they keep their coffee mug on the left"]
    assert "remember" in said.lower() or "noted" in said.lower()


def test_start_agent_with_an_empty_task_falls_back_to_the_default():
    desk = FakeDesk()
    tools = _tools(desk, resolve=lambda target: ["/wt/resume-me"], prepare=lambda path: None,
                   default_task="DEFAULT TASK")

    _call(tools["start_agent"], path="/wt/resume-me", task="")

    assert desk.started[0][2] == "DEFAULT TASK"


def test_closing_a_tab_retires_the_agent_through_the_desk():
    desk = FakeDesk()
    tools = _tools(desk)

    said = _call(tools["close_agent_tab"], name="gdoc-export")

    assert desk.retired == ["gdoc-export"]
    assert "closed" in said.lower()


def test_a_tab_that_cannot_close_says_why_not_that_it_did():
    desk = FakeDesk(known=())
    tools = _tools(desk)

    said = _call(tools["close_agent_tab"], name="fixer")

    assert "still working" in said.lower() or "no tab" in said.lower()


def test_resolve_globs_a_worktrees_container_to_its_actual_worktrees(tmp_path):
    for name in ("wt1", "wt2"):
        (tmp_path / name).mkdir()
        (tmp_path / name / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    (tmp_path / "junk").mkdir()  # no .git - not a worktree, so no agent belongs in it

    resolved = _resolve(str(tmp_path))

    assert sorted(Path(p).name for p in resolved) == ["wt1", "wt2"]


def test_resolve_never_explodes_a_single_worktree_into_its_subdirectories(tmp_path):
    # The model named ONE worktree; globbing its subdirectories started an agent in .venv, one in
    # docs, one in src... - a whole crowd working "the task" in folders that aren't worktrees at all.
    worktree = tmp_path / "hungry-neumann"
    worktree.mkdir()
    (worktree / ".git").write_text("gitdir: elsewhere", encoding="utf-8")
    for name in (".venv", "docs", "src", "tests"):
        (worktree / name).mkdir()

    assert _resolve(str(worktree)) == [str(worktree)]  # one worktree, one agent


def test_resolve_takes_explicit_comma_separated_paths():
    assert _resolve("/x/one, /x/two") == ["/x/one", "/x/two"]


def test_resolve_expands_a_home_relative_fresh_path():
    # A brand-new worktree path named for new work won't exist yet, so it falls to the
    # explicit-path branch - which must still expand ~ so the agent's cwd is real.
    assert _resolve("~/work/new-agent") == [os.path.expanduser("~/work/new-agent")]


def test_mark_ready_records_the_presentation_with_its_steps():
    desk = FakeDesk()
    tools = _tools(desk)

    said = _call(tools["mark_ready"], name="gdoc-export",
                 steps="Open localhost:5300 and click Export.")

    assert desk.presented == [("gdoc-export", "Open localhost:5300 and click Export.")]
    assert "gdoc-export" in said


def test_mark_ready_relays_the_desks_refusal():
    # The refusal sentence IS the tool's answer - the model must hear why, or it will tell the
    # user something was presented that wasn't.
    desk = FakeDesk(known=())
    tools = _tools(desk)

    said = _call(tools["mark_ready"], name="ghost", steps="steps")

    assert desk.presented == []
    assert "no agent" in said.lower()


def test_an_approving_verdict_reaches_the_desk():
    desk = FakeDesk()
    tools = _tools(desk)

    said = _call(tools["record_verdict"], name="gdoc-export", verdict="approved", feedback="")

    assert desk.verdicts == [("gdoc-export", True, "")]
    assert "land" in said.lower()


def test_a_rejecting_verdict_carries_the_feedback():
    desk = FakeDesk()
    tools = _tools(desk)

    said = _call(tools["record_verdict"], name="gdoc-export", verdict="rejected",
                 feedback="The button is on the wrong side.")

    assert desk.verdicts == [("gdoc-export", False, "The button is on the wrong side.")]
    assert "feedback" in said.lower()


def test_a_verdict_word_that_is_neither_is_refused_without_touching_the_desk():
    desk = FakeDesk()
    tools = _tools(desk)

    said = _call(tools["record_verdict"], name="gdoc-export", verdict="maybe", feedback="")

    assert desk.verdicts == []
    assert "approved" in said and "rejected" in said  # the two words it must choose between


def test_record_verdict_relays_the_desks_refusal():
    desk = FakeDesk(known=())
    tools = _tools(desk)

    said = _call(tools["record_verdict"], name="ghost", verdict="approved", feedback="")

    assert desk.verdicts == []
    assert "presented" in said.lower()


def test_ask_foreman_hands_the_stuck_agent_to_the_senior_layer():
    desk, foreman = FakeDesk(), FakeForeman()
    tools = _tools(desk, foreman=foreman)

    said = _call(tools["ask_foreman"], name="gdoc-export",
                 question="It wants to know which auth library to use.")

    assert foreman.considered == [("gdoc-export", "It wants to know which auth library to use.")]
    assert "foreman" in said.lower()


def test_a_filed_improvement_is_stamped_and_a_duplicate_is_refused():
    desk, filed = FakeDesk(), []

    def keeper(item, stamp=None):
        if filed:
            return False
        filed.append((item, stamp))
        return True

    tools = _tools(desk, file_enhancement=keeper, clock=lambda fmt: "2026-07-27 00:30")

    first = _call(tools["file_improvement"], item="warn him when credits run low")
    second = _call(tools["file_improvement"], item="warn him when credits run low")

    assert filed == [("warn him when credits run low", "2026-07-27 00:30")]
    assert "Filed" in first
    assert "already on the list" in second  # the refusal is said, never silently swallowed


def test_revising_a_ticket_goes_through_by_id_and_a_missing_id_is_said():
    desk, revised = FakeDesk(), []
    tools = _tools(desk, revise=lambda item_id, text: revised.append((item_id, text)) or item_id == 7)

    said_yes = _call(tools["revise_enhancement"], id=7, text="sharper words")
    said_no = _call(tools["revise_enhancement"], id=99, text="anything")

    assert revised == [(7, "sharper words"), (99, "anything")]
    assert "7" in said_yes
    assert "no item" in said_no.lower()


def test_a_little_chore_goes_to_the_errand_hand_not_an_agent_tab():
    desk, errands = FakeDesk(), FakeErrands()
    tools = _tools(desk, errands=errands)

    said = _call(tools["run_errand"], chore="archive the old fixer log")

    assert errands.chores == ["archive the old fixer log"]
    assert "note" in said.lower()


def test_checking_off_by_id_flips_the_tick_and_a_missing_id_is_said():
    desk, ticked = FakeDesk(), []
    tools = _tools(desk, check_off=lambda item_id: ticked.append(item_id) or item_id == 43,
                   check_off_anywhere=lambda item_id: None)

    said_yes = _call(tools["check_off_enhancement"], id=43)
    said_no = _call(tools["check_off_enhancement"], id=999)

    assert ticked == [43, 999]
    assert "checked off" in said_yes
    assert "no single open item" in said_no.lower()


def test_a_wrongly_guessed_card_still_ticks_the_item_when_one_card_holds_it():
    # "I see #132 in the briefing but the tool can't find it on the Enhancements list" - the
    # brain guessed a project for an item sitting in plain sight on the Enhancements card, the
    # named card had no such number, and the miss reached the user ("please fix whatever is
    # wrong with it so that it can't find what is in plain sight"). One card holding the number
    # IS the answer; several holding it is still a refusal.
    desk, ticked = FakeDesk(), []
    tools = _tools(desk, check_off=lambda item_id, **where: False,
                   check_off_anywhere=lambda item_id: ticked.append(item_id) or "Enhancements")

    said = _call(tools["check_off_enhancement"], id=132, project="Excephalon")

    assert ticked == [132]
    assert "#132 is checked off" in said
    assert "Enhancements" in said


def test_checking_off_a_project_card_task_reaches_that_card():
    # "for the three tasks Excephalon took care of today, it did not check them off in the
    # Projects tab. please make Excephalon behave that way moving forward." Two of the three
    # lived on the Highdeas card, which no tool could reach - every tick landed on the
    # Enhancements card or nowhere.
    desk, ticked = FakeDesk(), []
    tools = _tools(desk, check_off=lambda item_id, **where: ticked.append(
        (item_id, where.get("heading"))) or True)

    said = _call(tools["check_off_enhancement"], id=7, project="Highdeas")

    assert ticked == [(7, "Project: Highdeas")]
    assert "#7 on Highdeas is checked off" in said


def test_start_agent_carries_the_project_card_its_item_lives_on(tmp_path):
    # The card rides with the agent from the start, like the item itself, so the wrap-up's tick
    # lands where the task actually lives.
    desk = FakeDesk()
    worktree = tmp_path / "wt-spinner"
    worktree.mkdir()
    tools = _tools(desk, resolve=lambda target: [str(worktree)], prepare=lambda path: None)

    _call(tools["start_agent"], path=str(worktree), task="hold the spinner",
          enhancement="#7 the spinner holds", project="Highdeas")

    # The default namer distills "hold the spinner" and prefixes the project card it lives on.
    assert desk.started == [("highdeas-hold-spinner", str(worktree), "hold the spinner",
                             "#7 the spinner holds", "Highdeas")]


def test_a_feature_request_for_another_app_is_not_filed_on_his_own_list():
    # "No, you fucker. This is the second time you've done this. If I ask you for a feature on
    # anything other than yourself, you're supposed to go out and do it with a Claude agent. The
    # enhancements list is only for enhancements to yourself, Excephalon." Both times the remedy
    # was a written instruction; the projects are known by name, so this one is decidable.
    filed = []
    tools = _tools(FakeDesk(), other_apps=("Highdeas", "Haglio"),
                   file_enhancement=lambda item, stamp=None: filed.append(item) or True)

    said = _call(tools["file_improvement"],
                 item="there should be a checkbox in Highdeas's modal view of notes which "
                      "disables the auto play of audio")

    assert filed == []  # nothing reached the list
    assert "Highdeas" in said and "start_agent" in said


def test_an_item_naming_another_app_and_itself_is_still_his_own_list():
    # "A funnel from Highdeas so new feature ideas get automatically picked up by Excephalon" is
    # a change to Excephalon that happens to name another app - exactly what the list is for.
    filed = []
    tools = _tools(FakeDesk(), other_apps=("Highdeas",),
                   file_enhancement=lambda item, stamp=None: filed.append(item) or True)

    _call(tools["file_improvement"],
          item="a funnel from Highdeas so new feature ideas are picked up by Excephalon")

    assert len(filed) == 1


def test_an_ordinary_self_improvement_still_files():
    filed = []
    tools = _tools(FakeDesk(), other_apps=("Highdeas",),
                   file_enhancement=lambda item, stamp=None: filed.append(item) or True)

    _call(tools["file_improvement"], item="the voice should pause longer between sentences")

    assert len(filed) == 1


def test_an_agent_can_be_started_under_the_name_he_asks_for(tmp_path):
    # "I should be able to tell Excephalon initially what to name them" - so the name rides in
    # with the ask, and the worktree keeps its own (git's) name.
    desk = FakeDesk()
    worktree = tmp_path / "inbox-auto-play-toggle"
    worktree.mkdir()
    tools = _tools(desk, resolve=lambda target: [str(worktree)], prepare=lambda path: None)

    said = _call(tools["start_agent"], path=str(worktree), task="add the checkbox",
                 name="the auto-play fix")

    assert desk.started == [("the-auto-play-fix", str(worktree), "add the checkbox", None, None)]
    assert "the-auto-play-fix" in said


def test_without_an_explicit_name_the_task_is_distilled_into_one(tmp_path):
    # The old behavior - an agent named after its worktree folder - is exactly what this feature
    # replaces. With no name asked for, the task itself is distilled into the label.
    desk = FakeDesk()
    worktree = tmp_path / "wt-7c3"
    worktree.mkdir()
    tools = _tools(desk, resolve=lambda target: [str(worktree)], prepare=lambda path: None)

    _call(tools["start_agent"], path=str(worktree), task="fix the drive link")

    # From the task, not the "wt-7c3" folder - and projectless, so it is Excephalon's own work.
    assert desk.started[0][0] == "excephalon-fix-drive-link"


def test_a_fan_out_over_several_worktrees_keeps_each_ones_own_name(tmp_path):
    # Driving several existing worktrees at once (a directory that globs to many) is re-attachment,
    # not fresh work: one shared task can't distill distinct names, so each keeps its folder's name.
    desk = FakeDesk()
    one, two = tmp_path / "voice-fix", tmp_path / "spinner-fix"
    one.mkdir()
    two.mkdir()
    tools = _tools(desk, resolve=lambda target: [str(one), str(two)], prepare=lambda path: None,
                   namer=lambda task, project=None: "should-not-be-used")

    _call(tools["start_agent"], path=str(tmp_path), task="pick up where each left off")

    assert [row[0] for row in desk.started] == ["voice-fix", "spinner-fix"]


def test_renaming_an_agent_by_voice_goes_through_the_desk():
    desk = FakeDesk()
    tools = _tools(desk)

    said = _call(tools["rename_agent"], name="gdoc-export", to="the Drive export")

    assert desk.renamed == [("gdoc-export", "the Drive export")]
    assert "the-Drive-export" in said

"""What the brain can DO, as typed tools instead of code-words in its speech.

It used to act by writing marker phrases - [SUPERVISE], [TELL] - into its own conversational
reply, for a scanner to fish back out. That made its speech double as its control panel, and both
jobs suffered: a marker the scanner missed was read aloud ("I don't appreciate how you're speaking
to me in code"), a marker typed slightly wrong did nothing and told no one, and a status question
was sometimes answered by *dispatching* because writing the phrase was the only verb it had. A
typed tool call cannot be half-written, cannot leak into the voice, and returns a result the model
has to look at.

The tools run in-process (an SDK MCP server), so acting is one round trip with no subprocess, and
every one of them returns in well under a second - the desk does agent work on its own threads.
"""

import asyncio
import os.path
import re
import time
from pathlib import Path

from claude_agent_sdk import create_sdk_mcp_server, tool

from excephalon.delivery import DeliveryError
from excephalon.naming import distill_name
from excephalon.memory import (PROJECT_PREFIX, append_enhancement, append_learned,
                           complete_enhancement_anywhere, drop_persona_instruction,
                           forget_learned, save_persona_instruction,
                           complete_enhancement_by_id, revise_enhancement)
from excephalon.models import resolve as resolve_model
from excephalon.tailing import safe_name
from excephalon.worktrees import find_worktrees, is_worktree, prepare_worktree_for

SERVER = "excephalon"

# The names the model calls, and the only tools its options allow: the conversational brain has no
# Bash, no Read, no way to wander a repo mid-turn - investigation belongs to the agents it starts.
TOOL_NAMES = tuple(f"mcp__{SERVER}__{name}"
                   for name in ("start_agent", "tell_agent", "deliver_update",
                                "set_next_agent_model",
                                "file_improvement", "revise_enhancement", "check_off_enhancement",
                                "update_persona", "drop_instruction", "remember", "forget_memory",
                                "close_agent_tab", "mark_ready", "rename_agent",
                                "record_verdict", "ask_foreman", "run_errand"))

# What the app calls itself, in the words an item would use. An item naming one of these is about
# Excephalon, whatever else it also names.
SELF_NAMES = ("excephalon", "entity", "yourself")

DEFAULT_TASK = (
    "You are in a git worktree. Look at the branch name and the working tree, work out what "
    "this session is meant to be doing, and continue it. Report back in a few plain sentences: "
    "what you did, and anything you need the user to decide."
)


def _resolve(target):
    """A worktrees directory (globbed to its sub-dirs) or explicit comma/newline-separated paths.

    A path that doesn't exist yet (the usual case - a fresh worktree named for new work) lands in
    the explicit branch, so expand ~ there too or the agent's cwd would be a bogus literal.
    """
    expanded = str(Path(target).expanduser())
    if is_worktree(expanded):
        return [expanded]  # ONE worktree was named - never fan out into its subdirectories
    if Path(expanded).is_dir():
        return find_worktrees(expanded) or [expanded]
    # expanduser only (not full Path normalization) so plain paths pass through verbatim and only ~ resolves.
    return [os.path.expanduser(part.strip()) for part in re.split(r"[,\n]", target) if part.strip()]


def take_care_spec(project, task_text, *, selves=SELF_NAMES):
    """What to start for a Projects-tab robot click, or None if there is nothing to start.

    A click starts an agent on the task then and there - the deterministic half of "please take care
    of this task". The task's own words are the task and the enhancement it ticks off when the work
    lands; the card's project rides along so the tick lands on the right card - except Excephalon's
    own card (the Enhancements roadmap), which is project=None, the same None its own tasks tick
    against. The NAME is not decided here: a robot click has no brain in the loop, so the caller
    hands the task to the namer (see excephalon.naming) rather than slugifying it - "agent names
    shouldn't be the name of the task with hyphens"."""
    text = (task_text or "").strip()
    if not text:
        return None
    proj = (project or "").strip()
    proj = None if proj.lower() in selves else (proj or None)
    return {"task": text, "enhancement": text, "project": proj}


def names_another_app(item, others, selves=SELF_NAMES):
    """The other project this item is a feature request for, or None.

    The Enhancements list is for changes to Excephalon ITSELF. Twice a feature request for one of
    his other apps was filed there instead of being handed to an agent - "The enhancements list is
    only for enhancements to yourself... You were supposed to have learned your lesson from the
    first time" - and both times the remedy was a written instruction, which held until it didn't.
    The projects are known (their folder names already seed the transcriber's vocabulary), so this
    is decidable rather than remembered. An item naming another app AND itself is left alone: that
    is a change to how Excephalon handles that app, which is exactly what the list is for."""
    words = re.findall(r"[a-z0-9]+", item.lower())
    if any(name.lower() in words for name in selves):
        return None
    for name in others:
        if name.lower() in words:
            return name
    return None


def fleet_actions(desk, foreman, errands, *, file_enhancement=append_enhancement,
                  revise=revise_enhancement, check_off=complete_enhancement_by_id,
                  check_off_anywhere=complete_enhancement_anywhere,
                  save_instruction=save_persona_instruction,
                  drop_instruction=drop_persona_instruction,
                  remember_fact=append_learned, forget_fact=forget_learned,
                  resolve=_resolve, prepare=prepare_worktree_for, default_task=DEFAULT_TASK,
                  namer=distill_name, other_apps=(), clock=time.strftime):
    """The action tools, wired to this desk and foreman: (server config for the options, the
    tools themselves).

    The tools come back too so tests can drive the handlers directly - the server config is an
    opaque box once built."""

    @tool("start_agent", "Start a fresh coding agent working on a task. `path` is the absolute "
          "path of the worktree to work in (a new path gets a new worktree cut from current "
          "origin/main). `task` is the user's requirements, passed on faithfully and completely - "
          "every constraint they stated. `enhancement` is optional: when this agent is taking on "
          "an item from one of the user's lists, pass that item's exact text so it ticks itself "
          "off when the work lands - and when the item lives on a Projects-tab card rather than "
          "the Enhancements card, pass that card's name as `project` (e.g. 'Highdeas'). Leave "
          "both out for any other work. You do NOT name the agent: the app distills a short, "
          "project-prefixed label from the task itself. Pass `name` ONLY when the user asks for a "
          "specific one ('call it the auto-play fix'); otherwise leave it out.",
          {"path": str, "task": str, "enhancement": str, "project": str, "name": str})
    async def start_agent(args):
        paths = resolve(str(args["path"]))
        if not paths:
            return _say("I couldn't find any sessions to drive there.")
        task = str(args.get("task") or default_task)
        enhancement = str(args.get("enhancement") or "").strip() or None
        project = str(args.get("project") or "").strip() or None
        single = len(paths) == 1
        # The name he asked for, when he asked for one: "call it the auto-play fix". Only for a
        # single agent - one name cannot cover a fan-out - and it labels the agent, never the
        # worktree, which is git's to name.
        asked = safe_name(str(args.get("name") or "")) if single else ""
        started = []
        for path in paths:
            if not Path(path).exists():  # new work means a new worktree, cut from current origin/main
                prepare(path)
            if asked:
                name = asked
            elif single:
                # No explicit name for fresh work: the thinking namer reads the task and the project
                # and distills a short, prefixed label. Run off the event loop (it may reach a
                # model) so a name can never stall the turn.
                name = await asyncio.to_thread(namer, task, project)
            else:
                name = Path(path).name  # a fan-out re-attaches to existing worktrees, each own name
            # The item's NUMBER is resolved by the desk (agent_desk._item_number), the one
            # place every start passes through - resolved here instead, a Projects-tab robot
            # click carried no number and its finished work left the item open.
            started.append(desk.start(name, path, task, enhancement=enhancement, project=project))
        return _say(f"Started {', '.join(started)} on {desk.running_on()}.")

    @tool("rename_agent", "Call a running agent something else - the name the user gives it, used "
          "everywhere the app names that agent from then on. For when they say 'call that one the "
          "auto-play fix'.", {"name": str, "to": str})
    async def rename_agent(args):
        name, to = str(args["name"]).strip(), str(args["to"]).strip()
        if not desk.rename(name, to):
            return _say(f"Couldn't rename {name} - no agent by that name, or the new one is taken.")
        return _say(f"{name} is now {safe_name(to)}.")

    @tool("tell_agent", "Say something more to an agent already running - a correction, an answer, "
          "a follow-up. `name` is the agent's name from the fleet briefing.",
          {"name": str, "message": str})
    async def tell_agent(args):
        name = str(args["name"]).strip()
        if not desk.send(name, str(args["message"])):
            return _say(f"No agent called {name} is running - check the fleet briefing.")
        # Held news from this agent predates the words just delivered - the user has moved past
        # it, and offered later it reads as a fresh update on work he already redirected ("surely
        # there's no update for smart grouping. You just sent off the latest message to it.").
        # The agent's next report is the news now.
        drop_held = getattr(desk, "drop_news", None)
        if drop_held is not None:
            drop_held(name)
        return _say(f"Delivered to {name}.")

    @tool("deliver_update", "Hand over an agent's HELD update: the app appends it to THIS very "
          "reply, word for word, in the same breath. Call this whenever the user asks for an "
          "agent's update and the briefing says news for that agent is still waiting to be "
          "spoken - and never retell or summarize a held update in your own words; that is how "
          "the user heard two versions of the same news 13 seconds apart. After calling, say at "
          "most one short sentence answering their words, never the update's content.",
          {"name": str})
    async def deliver_update(args):
        name = str(args["name"]).strip()
        if not desk.hand_over_news(name):
            return _say(f"Nothing is waiting to be spoken about {name} - answer from the fleet "
                        "briefing, and say plainly if it holds no answer.")
        return _say(f"{name}'s update will be appended to this reply of yours, word for word - "
                    "do not repeat or summarize its content.")

    @tool("set_next_agent_model", "Set which model and effort the NEXT agent starts on, from the "
          "user's words ('fable on max', 'back to opus'). Agents already working keep the model "
          "they opened with.", {"choice": str})
    async def set_next_agent_model(args):
        choice = resolve_model(str(args["choice"]))
        if choice is None:
            return _say(f"That named no model or effort I know. Still on {desk.running_on()}.")
        return _say(f"Next agent goes on {desk.choose(*choice)}.")

    @tool("file_improvement", "File one self-improvement item on the user's Enhancements list, "
          "the moment they ask for it. One call per item - and never re-file words already on "
          "the list; the tool refuses duplicates and says so.", {"item": str})
    async def file_improvement(args):
        elsewhere = names_another_app(str(args["item"]), other_apps)
        if elsewhere:
            return _say(
                f"Not filed: that is a feature request for {elsewhere}, and this list is only for "
                f"changes to yourself. Put an agent on it in {elsewhere}'s own repo instead "
                "(start_agent), and tell the user you have done that - not that you filed it.")
        if not file_enhancement(str(args["item"]), stamp=clock("%Y-%m-%d %H:%M")):
            return _say("That one is already on the list, still open - not filing a second copy.")
        return _say("Filed.")

    @tool("run_errand", "Do a small local chore yourself - move or archive a file, tidy a "
          "folder, read something and report back - without opening a visible agent tab. For "
          "features and repo work use start_agent; this is for the little things the user asks "
          "for in passing. The outcome comes back as its own note when done.", {"chore": str})
    async def run_errand(args):
        errands.run(str(args["chore"]))
        return _say("Doing that little job now - its result will come back as its own note.")

    @tool("revise_enhancement", "Rewrite an existing Enhancements-list item's words by its #id - "
          "when the user wants a filed ticket corrected or expanded rather than duplicated. The "
          "item keeps its number and its done state.", {"id": int, "text": str})
    async def revise_item(args):
        if not revise(int(args["id"]), str(args["text"])):
            return _say(f"No item carries #{args['id']} - check the number on the tab.")
        return _say(f"Rewrote #{args['id']}.")

    @tool("check_off_enhancement", "Mark one list item DONE by its #id, the moment the thing it "
          "asks for is finished - never by rewriting its words. The tick flips; the number and "
          "the words stay. `project` is the Projects-tab card the item lives on (e.g. "
          "'Highdeas'); pass \"\" for the Enhancements card - never a guess. An afternoon's "
          "finished tasks once sat unticked on their cards because only the Enhancements card "
          "was reachable.",
          {"id": int, "project": str})
    async def check_off_item_tool(args):
        project = str(args.get("project") or "").strip()
        where = {"heading": f"{PROJECT_PREFIX}{project}"} if project else {}
        card = f" on {project}" if project else ""
        if not check_off(int(args["id"]), **where):
            # The named card was a guess, and guesses miss: told to tick #132, the brain
            # invented a project, and "the tool can't find it" reached the user about an item
            # in plain sight. When exactly one card holds the number, that card is the answer.
            landed = check_off_anywhere(int(args["id"]))
            if landed is None:
                return _say(f"No single open item carries #{args['id']}{card} - check the "
                            "number on the tab.")
            return _say(f"#{args['id']} is checked off - it was on the {landed} card"
                        + (f", not {project}" if project else "") + ".")
        return _say(f"#{args['id']}{card} is checked off.")

    @tool("update_persona", "Record a lasting change to how YOU behave - a standing instruction "
          "about how you talk or act - when the user tells you to work differently from now on (not "
          "a one-off for this turn). `name` is the short bolded label the Instructions card shows "
          "it under, three words tops; `instruction` is the rule itself. A name or rule already on "
          "the card is RESTATED - that row is rewritten in place, never duplicated. It joins your "
          "persona and takes effect next time you start. One call per instruction.",
          {"name": str, "instruction": str})
    async def update_persona(args):
        try:
            rewrote = save_instruction(str(args.get("name") or ""), str(args.get("instruction") or ""))
        except ValueError as refused:
            return _say(f"Not saved: {refused} - call again with both.")
        if rewrote:
            return _say("Rewrote that standing instruction where it stood - no duplicate row.")
        return _say("Added to your standing instructions - it's part of your persona from next start.")

    @tool("drop_instruction", "Delete one standing instruction from your persona - when the user "
          "says a rule should no longer stand, or a row is there by mistake. Give its bolded name, "
          "or enough of its words to pick out exactly one row; matching none or several, nothing "
          "is deleted and this says so.", {"words": str})
    async def drop_instruction_tool(args):
        matched = drop_instruction(str(args.get("words") or ""))
        if matched == 1:
            return _say("Dropped - that instruction is out of your standing instructions.")
        if matched == 0:
            return _say("No standing instruction matches those words - read the card back to them "
                        "if unsure.")
        return _say(f"Those words match {matched} instructions, so nothing was deleted - give more "
                    "of the exact row you mean.")

    @tool("forget_memory", "Drop one remembered fact from the memory store - the review's "
          "delete, for a memory the user judged not worth keeping (or one just converted into "
          "a standing instruction). Give the fact's words; a close paraphrase lands on the "
          "right line.", {"fact": str})
    async def forget_memory(args):
        if forget_fact(str(args["fact"])):
            return _say("Dropped from memory.")
        return _say("No memory matched those words - read the store back to them if unsure.")

    @tool("remember", "Keep one durable fact about the user that came up - a preference, a "
          "commitment, a life detail worth having next time. For lasting facts, not this turn's "
          "chatter. One call per fact.", {"fact": str})
    async def remember(args):
        remember_fact([str(args["fact"])])
        return _say("Noted - I'll remember that.")

    @tool("close_agent_tab", "Wrap up a finished agent: its tab closes (the log is archived), "
          "its session ends, and its worktree is removed. Call it unprompted once the user has "
          "signed off on that agent's work and it has landed - they never want to see a "
          "finished agent lingering. Only for agents that are done - a working agent's tab "
          "stays open.", {"name": str})
    async def close_agent_tab(args):
        name = str(args["name"]).strip()
        if not desk.retire(name):
            return _say(f"Couldn't close {name} - it is still working, still has news waiting "
                        "to be spoken, or there is no tab by that name. Try again shortly.")
        return _say(f"Closed {name}'s tab.")

    @tool("mark_ready", "Record that an agent's finished work is standing up for the user to SEE, "
          "with the exact click-by-click steps from the agent's report. Call it the moment an "
          "agent presents reviewable work; a verdict can only be recorded on work marked ready.",
          {"name": str, "steps": str})
    async def mark_ready(args):
        name = str(args["name"]).strip()
        try:
            desk.present(name, str(args["steps"]))
        except DeliveryError as refused:
            return _say(str(refused))
        return _say(f"Marked: {name}'s work is presented, awaiting their verdict.")

    @tool("record_verdict", "Record the user's verdict on presented work, the moment they give "
          "it. `verdict` is 'approved' or 'rejected'. Approval sends the agent to land the work; "
          "rejection sends it back with `feedback` - their words on what was wrong. An approval "
          "that also asks for a small change ('ship it with that tweak') is STILL an approval: "
          "record it approved with the change as `feedback`, and the agent folds it in on the "
          "way to landing. Never route a sign-off through tell_agent - that records no verdict, "
          "the landing stays blocked, and the user gets asked to approve again what they already "
          "approved in no uncertain terms.",
          {"name": str, "verdict": str, "feedback": str})
    async def record_verdict(args):
        name = str(args["name"]).strip()
        word = str(args["verdict"]).strip().lower()
        if word not in ("approved", "rejected"):
            return _say("Say a verdict of exactly approved or rejected.")
        try:
            desk.verdict(name, word == "approved", feedback=str(args.get("feedback") or ""))
        except DeliveryError as refused:
            return _say(f"{refused} - that is machinery, yours to sort: tell the user only what "
                        "happens next in their terms, never gates, verdicts-on-record, or what "
                        "'the system needs'.")
        if word == "approved":
            return _say(f"Recorded. {name} is off to land it and will report when it's in.")
        return _say(f"Recorded. {name} has their feedback and will present again.")

    @tool("ask_foreman", "Hand a stuck working agent to the foreman - a smarter model that reads "
          "the agent's log and settles technical snags itself: use it when an agent needs "
          "feedback or a technical decision you can't confidently give, or isn't finishing on "
          "its own. Decisions that belong to the user - preference, scope, sign-off - still go "
          "to the user, never the foreman. `question` is what the agent needs, in a sentence.",
          {"name": str, "question": str})
    async def ask_foreman(args):
        foreman.consider(str(args["name"]).strip(), str(args["question"]))
        return _say("The foreman has it - it will settle it with the agent, or say what's needed.")

    tools = [start_agent, tell_agent, deliver_update, set_next_agent_model, file_improvement,
             revise_item,
             check_off_item_tool, run_errand, update_persona, drop_instruction_tool, rename_agent,
             remember, forget_memory, close_agent_tab, mark_ready, record_verdict, ask_foreman]
    return create_sdk_mcp_server(name=SERVER, tools=tools), tools


def _say(text):
    return {"content": [{"type": "text", "text": text}]}

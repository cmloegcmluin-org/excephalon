"""The one author of every piece of news he hears.

News used to be worded when it ARRIVED: one brain call per agent event, off in the background, its
sentence stored as prose and spoken minutes later at a lull - sometimes with the app's own roll
call welded onto its back. Two authors in one utterance, and a sentence written with no idea of
the moment it would be said in. Every gate at that seam existed to police the splice, and the
gates began breaking each other: in one night the review gate swallowed a merge report and the
opening weld read him the same sentence twice.

Here the news is worded ONCE, at the moment it is spoken, by one author for the whole utterance:
the fact (the agent's report, as input, never output), where the work stands, what else is still
waiting - one composition. What is checked afterwards is mechanical and about the FACTS, not the
prose: a link the report handed over is in the utterance verbatim, and unlanded work is not
called shipped. A draft that fails is retracted and asked again once with the fault named; one
that fails twice gives way to the app's own whole sentence, which claims nothing and still carries
the link. Never a splice: whoever wrote the utterance wrote all of it.
"""

import inspect
import re
from dataclasses import dataclass

from excephalon.links import link_parts
from excephalon.relay import notice
from excephalon.waiting import roll_call

# How long the wording may take at the lull before the app's own sentence goes out instead. He
# is about to hear this and nothing else is happening; a few seconds is the price of one author,
# and past that the plain line beats a better one late.
WORD_DEADLINE = 30.0

# The routing word, wherever it leads the reply. "Handled - <news>" reached the user verbatim and
# he had to ask what it referred to. Alone it means the brain chose silence; it is never speech.
_HANDLED_LEAD = re.compile(r"(?i)^handled\b[\s\-–—:,.!]*")

# Where the work actually stands, stated as a fact in the prompt rather than left to a rule the
# model has to recall. "The feature should be there in Highdeas waiting" was said about work still
# being built - "never claim in heads-up statements that a feature is available or deployed in an
# app when it is still under development or awaiting merge".
STAGE_FACT = {
    "building": ("[Fact, from the app: this work is still BEING BUILT. It is not deployed, not "
                 "live, not shipped, not available anywhere he could go and use it. Saying or "
                 "implying otherwise is false.]"),
    "ready": ("[Fact, from the app: this work is BUILT AND WAITING FOR HIS VERDICT. It is not "
              "deployed, not live, not shipped, and not in the app he uses - a demo he can look "
              "at is not the thing being live. Saying or implying otherwise is false.]"),
}

# Words that can only be a claim of deployment. Kept tiny and unambiguous on purpose: presentation
# news legitimately says "the demo is live on that port", so "live" is not on this list.
CLAIMS_DEPLOYED = ("deployed", "shipped", "in production", "already in ", "should be there")

# The kinds whose report may hand over a door - a launcher, an address, a path. For those the
# door has to survive into the utterance verbatim: unparsed or paraphrased, "the launcher link"
# reached him with no link in it ("What launch link? You didn't give me one.").
WALKTHROUGH_KINDS = frozenset(("finished", "pending", "wrote", "errand"))

# What each kind of event asks of the one author. Adapted from the narrations this replaces, minus
# every tool action: the triage that kicks an agent onward or hands it to the foreman happens when
# the event ARRIVES (narrator.py); by the time a fact reaches here it is news, and the only job is
# saying it well.
FRAMING = {
    "finished": (
        "Your agent just finished a turn on this work. Say, in your own one or two short "
        "sentences, whether the thing he wanted is DONE or needs a decision or a step from him. "
        "If it is ready to look at, give him the agent's own see-it-running steps - where to "
        "click and what to watch happen. 'Run the tests' is never his verification; if the agent "
        "stood nothing up for his eyes, say the review isn't ready. Until he has signed off AND a "
        "merge is reported, never say 'fixed', 'done', or anything that reads as already landed - "
        "the only frame before sign-off is 'ready for your eyes', with the steps. If the report "
        "hands over a link or a launcher - especially a markdown [label](address) link - carry it "
        "VERBATIM: it is the door to the demo, and a walkthrough without it sends him hunting. "
        "Never relay the report's internals - no commit hashes, no test counts, no branch names, "
        "no file lists."),
    "landing": (
        "Your agent was landing work he had already approved. If the report says the work "
        "merged, tell him in one short sentence that it is in and done. If it did not land, tell "
        "him in one sentence what is stuck. Never relay internals - no commit hashes, no test "
        "counts, no branch names."),
    "landed": (
        "This work has MERGED and the app has already wrapped everything up: its list item is "
        "ticked and its tab is closed. Tell him in one short sentence that it is in and done. "
        "Never relay internals - no commit hashes, no test counts, no branch names."),
    "pending": (
        "You have just started up, and this work was presented for his eyes before the restart "
        "and never got a verdict. Remind him in one or two short sentences that it is waiting on "
        "his yes or no, and give him the steps - where to click and what to watch happen. Never "
        "relay internals."),
    "died": (
        "This work has crashed repeatedly - the app restarted it silently each time, and it died "
        "again. It is stuck. Tell him plainly in one short sentence that this piece of work keeps "
        "failing and what you propose - it is not moving until he decides."),
    "wrote": (
        "Your agent wrote this to its inbox for him. Pass on what matters in one or two short "
        "sentences: what it needs from him, or what is ready."),
    "errand": (
        "The quiet errand hand finished a small local chore. Tell him in ONE short sentence that "
        "it's done, or exactly what stopped it - no internals, no tool talk. It is not an item he "
        "chooses between."),
    "quiet": (
        "This work has gone quiet - it may be hung. Tell him in one short sentence, and what has "
        "been set in motion about it if anything has."),
    "memory": (
        "One memory from your store awaits his review. Read it back in your own short sentence "
        "and ask what he wants done with it - kept, dropped, or made a standing instruction. One "
        "memory, one short question, never a list."),
}

# The work's own name, handed over so the line calls it what HE calls it. An agent's internal name
# is a filename: read out beside his own words for the same work it reads as a different thing
# entirely - "it says 'still waiting: ... scheduled-messages'. I think this is the same as the
# 'timed-reminder feature', but it's weird and confusing that in the previous message it chose a
# different name for the feature than its agent log's name."
WORKS_NAME = (
    "The work this is about is, in HIS OWN WORDS, \"{work}\". Call it that. Never say the "
    "agent's internal name to him - it is a filename, and he has never asked for one.")

REPORT = (
    "What the agent reported - for you to READ, never to relay: every noun in it belongs to a "
    "conversation he was not part of, and one that reached him whole was answered \"what is a "
    "'fresh' demo?? what four curated scenarios? basically this whole message is useless, insane, "
    "confusing, and terrible\":\n{report}")

# Conduct that rode only in the persona and lost to habit by mid-session. A narrated line is a line
# he HEARS, so it carries the same conduct a reply does - "the desk" reached him through a narration
# the moment the conduct was only on replies.
CONDUCT = (
    "[Standing conduct for this message, same as any reply: no internal vocabulary EVER - never "
    "'the desk', 'the fleet', 'the outbox', 'the roster', 'marked ready', 'the delivery stage', "
    "and never an agent's internal name as a thing he should recognise; say 'your agents', 'the "
    "list', 'ready for you to look at'. Name the WORK in his own words - never 'the feature' or "
    "'the work', which say nothing. Short: one or two sentences per piece of news, and the steps "
    "when there are steps. This is ONE spoken message, in your own voice; write nothing that "
    "reads as a label or a status line.]")

ALSO_WAITING = (
    "[Also still waiting to be spoken about, name ONLY - say nothing of what those updates hold: "
    "{titles}. END your message by naming them, in his words for them, and asking which he wants "
    "next.]")

NOTHING_ELSE = "[Nothing else is waiting. Do not offer, mention or ask about any other work.]"

OPENING = (
    "[Agent news, from the app - not the user speaking. Word ALL of the following for him as ONE "
    "short spoken message, in your own voice, in the order given.]")

AGAIN = (
    "[Your last attempt at this {fault}, so it was not spoken and he has not heard it. Write it "
    "again, fixing exactly that.]\n\n")


@dataclass(frozen=True)
class Worded:
    """One whole utterance and who wrote it. `composed` means the brain wrote ALL of it - so it
    remembers saying it the way it remembers a reply - and False means the app did, whole, and the
    ledger must read it back."""

    text: str
    composed: bool

    def __str__(self):
        return self.text

    def __bool__(self):
        return bool(self.text.strip())


def anchors(report):
    """The doors a report hands over - addresses, launchers, paths - in the order they appear.
    Judged by the same rule the window draws links by, so what must survive into speech is exactly
    what he could click."""
    found = []
    for part in link_parts(str(report or ""), exists=lambda _path: False):
        target = part.get("link")
        if target and target not in found:
            found.append(target)
    return found


def claims_deployed(said, stage):
    """Does this line say the work is out there, when the app knows it is not? Only for work that
    has not landed - once it has, saying so is the news."""
    if stage not in STAGE_FACT:
        return False
    lowered = str(said).lower()
    return any(claim in lowered for claim in CLAIMS_DEPLOYED)


def _takes(call, keyword):
    try:
        return keyword in inspect.signature(call).parameters
    except (TypeError, ValueError):
        return False


class Speaker:
    """Words facts into the one utterance he hears. With no brain it speaks for the app alone."""

    def __init__(self, brain=None, *, deadline=WORD_DEADLINE):
        self._brain = brain
        self._deadline = deadline

    def word(self, facts, waiting=()):
        """One utterance for these facts, naming what else waits - and who wrote it.

        A fact carrying the agent's report is worded by the brain; one carrying only prose (news
        composed before this existed, or the app's own sentence) is spoken as it stands. Either way
        the whole utterance has one author: the roll call of what else waits is written INTO the
        brain's composition, or appended to the app's own - never onto the brain's."""
        facts = list(facts)
        listed = list(waiting)
        if self._brain is None or not any(getattr(fact, "report", "") for fact in facts):
            return self._plainly(facts, listed)
        prompt = self._prompt(facts, listed)
        fault = ""
        for _ in range(2):
            draft = self._ask((AGAIN.format(fault=fault) if fault else "") + prompt)
            if draft is None:
                break  # the brain could not answer at all; the app's own sentence carries it
            fault = self.unfit(draft, facts)
            if not fault:
                return Worded(draft, composed=True)
            self._retract(draft)  # written, never spoken: off its record
        return self._plainly(facts, listed)

    def unfit(self, draft, facts):
        """Why this draft may not be spoken, or "" when it may - checked against the FACTS.

        The checks are the two that history demands: a door the report handed over has to be in
        the utterance verbatim ("What launch link? You didn't give me one."), and unlanded work is
        never called shipped ("The feature should be there in Highdeas waiting")."""
        said = str(draft or "").strip()
        if not said:
            return "says nothing at all"
        if not _HANDLED_LEAD.sub("", said):
            return "is only the routing word 'handled', which is never speech"
        for fact in facts:
            report = getattr(fact, "report", "")
            if report and getattr(fact, "kind", "") in WALKTHROUGH_KINDS:
                for door in anchors(report):
                    if door not in said:
                        return f"drops the link {door}, which is the door he needs verbatim"
            if claims_deployed(said, getattr(fact, "stage", None)):
                return "says the work is deployed or shipped while the app knows it is not"
        return ""

    def _prompt(self, facts, listed):
        pieces = [OPENING]
        for place, fact in enumerate(facts, start=1):
            kind = getattr(fact, "kind", "") or "finished"
            work = getattr(fact, "work", "") or ""
            lines = [f"[Piece {place}: " + FRAMING.get(kind, FRAMING["finished"]) + "]"]
            if work:
                lines.append("[" + WORKS_NAME.format(work=work) + "]")
            stage = STAGE_FACT.get(getattr(fact, "stage", None))
            if stage:
                lines.append(stage)
            report = getattr(fact, "report", "")
            lines.append("[" + REPORT.format(report=report) + "]" if report
                         else f"[The news, as the app has it: {fact}]")
            pieces.append("\n".join(lines))
        titles = [_title(item) for item in listed]
        pieces.append(ALSO_WAITING.format(titles=", ".join(titles)) if titles else NOTHING_ELSE)
        pieces.append(CONDUCT)
        return "\n\n".join(pieces)

    def _plainly(self, facts, listed):
        """The app's own whole utterance: what it knows, in his words, with the doors intact."""
        parts = []
        for fact in facts:
            report = getattr(fact, "report", "")
            if not report:
                parts.append(str(fact))
                continue
            line = notice(getattr(fact, "kind", ""), getattr(fact, "work", ""))
            doors = anchors(report) if getattr(fact, "kind", "") in WALKTHROUGH_KINDS else []
            if doors:
                line = line.rstrip(".") + ": " + " ".join(doors)
            parts.append(line)
        if listed:
            parts.append(roll_call(listed))
        text = "\n\n".join(part for part in parts if part)
        # The brain's own sentence, and nothing else in the utterance - the one case the ledger
        # need not carry, because it remembers writing it.
        composed = (len(facts) == 1 and not listed and bool(getattr(facts[0], "composed", False))
                    and not getattr(facts[0], "report", ""))
        return Worded(text, composed=composed)

    def _ask(self, prompt):
        extras = {"deadline": self._deadline} if _takes(self._brain.respond, "deadline") else {}
        try:
            return str(self._brain.respond(prompt, remember=False, **extras) or "").strip()
        except Exception:
            return None

    def _retract(self, draft):
        take_back = getattr(self._brain, "retract", None)
        if take_back is not None and str(draft or "").strip():
            take_back(draft)


def _title(item):
    return getattr(item, "work", "") or getattr(item, "about", None) or str(item)

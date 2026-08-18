"""Agent news, spoken by the same voice the user talks to.

What an agent produced used to reach the user as a concatenation - agent name, colon, first
sentence, capped - and he called it what it was: "I don't appreciate how you're speaking to me in
code." A notice is a label; a voice is someone telling you a thing. So each event now takes one
trip through the brain - which reads the agent's report and composes the one or two sentences the
user actually hears, in its own words, remembered as part of the conversation - and THAT goes to
the outbox for the lull. The relay's plain notice survives only as the fallback when the brain
cannot answer, because news must never die with a wedged session.
"""

import re
import threading

from excephalon.relay import notice

# The routing word, wherever it leads the reply. "Handled - <news>" reached the user verbatim and
# he had to ask what it referred to ("The word 'handled' doesn't appear to refer to anything...").
# Alone it means silence; leading real news it is stripped, because it is protocol, never speech.
_HANDLED_LEAD = re.compile(r"(?i)^handled\b[\s\-–—:,.!]*")

# How long one narration may sit inside the brain before the plain notice ships instead. The brain
# serializes its turns, so this must cover a few queued narrations - but not much more: the night
# everything after 04:43 went unspoken, one hung narration held the lock and the agent's merge
# report and the quiet warning both queued behind it until the app closed and they died. News must
# never die with a wedged session - that is this module's whole reason to exist.
# Deliberately under the loop's own bound on HIS silence: a narration that can outlast his
# patience is one his turn can lose to, and three of his turns in a row died waiting on one.
NARRATE_DEADLINE = 60.0


def _yields_to_him(brain):
    """`background=True` for a brain that knows the difference - so his own turn cuts this ask
    loose instead of queueing behind it. A brain that does not is asked exactly as before."""
    import inspect

    try:
        takes = "background" in inspect.signature(brain.respond).parameters
    except (TypeError, ValueError):
        takes = False
    return {"background": True} if takes else {}

# Events whose news is not an item on his list. The desk's agents have tabs, verdicts and
# landings, and several ready at once are read out numbered so he can take them one at a time.
# The errand hand and the memory inbox are the app's own machinery - a chore he never asked to
# see, and a housekeeping question - and each is simply something to say.
UNLISTED_KINDS = frozenset(("errand", "memory"))

# What the brain is asked, by kind of event. Each is a system-originated turn: the brain answers
# it the way it answers anything - and because it composed the words, it remembers saying them.
# Every narration carries this, because a narrated line is a line he HEARS - and the standing
# conduct that governs a reply reaches only replies. "The agent reported the feature is working
# ... but it's already wrapped up at the desk" was a narration: jargon he had to ask about twice,
# in a sentence that named no feature.
# Where the work actually stands, stated as a fact in the prompt rather than left to a rule the
# model has to recall. "The feature should be there in Highdeas waiting" was said about work still
# being built - "never claim in heads-up statements that a feature is available or deployed in an
# app when it is still under development or awaiting merge".
STAGE_FACT = {
    "building": ("\n\n[Fact, from the app: this work is still BEING BUILT. It is not deployed, not "
                 "live, not shipped, not available anywhere he could go and use it. Saying or "
                 "implying otherwise is false.]"),
    "ready": ("\n\n[Fact, from the app: this work is BUILT AND WAITING FOR HIS VERDICT. It is not "
              "deployed, not live, not shipped, and not in the app he uses - a demo he can look "
              "at is not the thing being live. Saying or implying otherwise is false.]"),
}

# Words that can only be a claim of deployment. Kept tiny and unambiguous on purpose: presentation
# news legitimately says "the demo is live on that port", so "live" is not on this list.
CLAIMS_DEPLOYED = ("deployed", "shipped", "in production", "already in ", "should be there")

# The work's own name, handed over so the line calls it what HE calls it. An agent's internal
# name is a filename: read out beside his own words for the same work it reads as a different
# thing entirely - "it says 'still waiting: ... scheduled-messages'. I think this is the same as
# the 'timed-reminder feature', but it's weird and confusing that in the previous message it
# chose a different name for the feature than its agent log's name."
WORKS_NAME = (
    "\n\n[Fact, from the app: the work this is about is, in HIS OWN WORDS, "
    "\"{work}\". Call it that. Never say the agent\'s internal name to him - it is a "
    "filename, and he has never asked for one. And never relay a word of the report above: "
    "every noun in it belongs to a conversation he was not part of, and one that reached him "
    "whole was answered: what is a fresh demo? what four curated scenarios? what two clean "
    "Excephalon messages? basically this whole message is useless, insane, confusing, and "
    "terrible.]"
)

NARRATION_CONDUCT = (
    "\n\n[Standing conduct for this line, same as any reply: no internal vocabulary EVER - never "
    "'the desk', 'the fleet', 'the outbox', 'the roster', 'marked ready', 'the delivery stage', "
    "and never an agent's internal name as a thing he should recognise; say 'your agents', 'the "
    "list', 'ready for you to look at'. Name the WORK in his own words - never 'the feature' or "
    "'the work', which say nothing. One or two short sentences.]"
)

PROMPTS = {
    "finished": (
        "[Agent event, from the app - not the user speaking. Your agent {agent} just finished a "
        "turn and reported:\n{report}\n\nTell the user, in your own one or two short sentences: "
        "is the thing they wanted DONE, or does it need a decision or a step from them? If it is "
        "ready to look at, give them the agent's own see-it-running steps - where to click and "
        "what to watch happen. 'Run the tests' is never their verification; if the agent stood "
        "nothing up for their eyes, say the review isn't ready and tell the agent to stand one "
        "up. Until they have signed off AND a merge is reported, never say 'fixed', 'done', or "
        "anything that reads as already landed - the only frame before sign-off is 'ready for "
        "your eyes', with the steps ('The text selection bug is fixed' reached them about work "
        "they had not even seen, and they called it out). If the report hands over a link or a "
        "launcher - especially a markdown [label](address) link - carry it VERBATIM in your "
        "sentences: it is the door to the demo, and a walkthrough without it sends them "
        "hunting. Never relay the report's internals - "
        "no commit hashes, no test counts, no branch "
        "names, no file lists. And if the report is only the agent pausing mid-task - narrating "
        "a step, asking leave to continue, nothing done and nothing the user must decide - do "
        "not interrupt them at all: use tell_agent to tell it to continue, and answer with the "
        "single word: handled - the whole reply, never the first word of a longer one, and never "
        "a word you say TO the user. If it is stuck on something TECHNICAL - it needs feedback or "
        "a decision you can't confidently give - use ask_foreman instead of guessing or bothering "
        "the user; only their own calls (preference, scope, sign-off) go to them.]"
    ),
    # A finished turn from an agent that was landing already-approved work: the loop's last leg.
    # Everything after the user's sign-off is mechanical, so the wrap-up is commanded here, not
    # handed back to the user as a chore.
    "landing": (
        "[Agent event, from the app - not the user speaking. Your agent {agent} was landing work "
        "the user had already approved, and reported:\n{report}\n\nIf the report says the work "
        "merged, call close_agent_tab for {agent} right now - the wrap-up is yours to do, not "
        "theirs - then tell the user in one short sentence that it is in and wrapped up. If it "
        "did not land, tell them in one sentence what is stuck. Never relay the report's "
        "internals - no commit hashes, no test counts, no branch names, no file lists.]"
    ),
    # The desk verified the merge against git and did the whole wrap-up itself - log archived,
    # list item ticked, session closed - so this narration only SAYS it; nothing is left for
    # the brain to do, because the wrap-up hanging on a narration is how a failed compose left
    # a merged agent haunting the desk for fourteen hours.
    "landed": (
        "[Agent event, from the app - not the user speaking. Your agent {agent}'s approved work "
        "has MERGED, and the app has already wrapped everything up: its list item is ticked and "
        "its tab is closed. Tell the user in one short sentence that it is in and done. Never "
        "relay the report's internals - no commit hashes, no test counts, no branch names.]"
    ),
    # A restart found an agent holding work it had already presented and never got a verdict on.
    # Nothing re-engages an idle agent, so without this the review simply stopped existing: he
    # rejected a round, the agent fixed and re-presented into a closed app, and the next launch
    # said nothing - "I never heard back again."
    "pending": (
        "[Agent event, from the app - not the user speaking. You have just started up, and your "
        "agent {agent} is holding finished work it presented for the user's eyes that never got "
        "a verdict. The steps it gave for seeing it run:\n{report}\n\nRemind them in your own "
        "one or two short sentences that it is waiting on their yes or no, and give them those "
        "steps - where to click and what to watch happen. Never relay internals - no commit "
        "hashes, no test counts, no branch names, no file lists.]"
    ),
    # Reached only past the desk's own silent-restart allowance (DEATH_LIMIT): a single crash
    # is handled without a word - "I should never need to know that anything died" - so by the
    # time this fires, the task has killed its agent repeatedly and is genuinely stuck.
    "died": (
        "[Agent event, from the app - not the user speaking. Your agent {agent} has now crashed "
        "repeatedly - the app restarted it silently each time, and it died again: {report}\n\n"
        "This task is stuck. Tell the user plainly in one short sentence that this piece of work "
        "keeps failing and what you propose - their work is not moving until they decide.]"
    ),
    "wrote": (
        "[Agent event, from the app - not the user speaking. Your agent {agent} wrote this to "
        "its inbox for the user:\n{report}\n\nPass on what matters in your own one or two short "
        "sentences: what it needs, or what is ready.]"
    ),
    "errand": (
        "[Agent event, from the app - not the user speaking. The quiet errand hand finished a "
        "small local chore and reported: {report}\n\nTell the user in ONE short sentence that "
        "it's done, or exactly what stopped it - no internals, no tool talk.]"
    ),
    "quiet": (
        "[Agent event, from the app - not the user speaking. Your agent {agent} has {report} - "
        "it may be hung. Use ask_foreman to have the senior model read its log and prod it, and "
        "tell the user in one short sentence what you've set in motion - or, if you already "
        "know it needs THEM, say that instead.]"
    ),
    # The memory inbox: raised only in downtime (see review.MemoryNudger), one fact at a time,
    # because he wants the store worked to zero - "this list is an inbox, and I'm an inbox-0
    # kind of guy" - without ever being interrupted for it.
    "memory": (
        "[Quiet-moment housekeeping, from the app - not the user speaking. One memory from your "
        "store awaits his review:\n{report}\n\nRead it back in your own short sentence and ask "
        "what he wants done with it - kept, dropped (forget_memory), or made a standing "
        "instruction (update_persona, then forget_memory so it leaves the inbox). One memory, "
        "one short question, never a list.]"
    ),
}


def _claims_deployed(said, stage):
    """Does this line say the work is out there, when the app knows it is not? Only for work that
    has not landed - once it has, saying so is the news."""
    if stage not in STAGE_FACT:
        return False
    lowered = said.lower()
    return any(claim in lowered for claim in CLAIMS_DEPLOYED)


class Narrator:
    """Turns one agent event into one brain-composed interjection, off-thread, never lost."""

    def __init__(self, brain, outbox, stage_of=None, deadline=NARRATE_DEADLINE, work_of=None):
        self._brain = brain
        self._outbox = outbox
        self._deadline = deadline
        # This piece of work in HIS words. It is what the line must CALL the work, and what the
        # app's own fallback sentence is built from - the agent's internal name is a filename and
        # reached him as a second, unrelated thing beside his own words for the same work.
        self._work_of = work_of or (lambda agent: "")
        # Where the agent's work stands in the delivery loop (the desk's delivery_stage) - the
        # same finished turn is presentation news while building, wrap-up news while landing.
        self._stage_of = stage_of or (lambda agent: None)

    def _retract(self, draft):
        """Take a composed line back off the brain's own record - it will never be spoken.

        Guarded, because a brain fake need not carry the method; a retraction that cannot be made
        must never be what stops the news."""
        take_back = getattr(self._brain, "retract", None)
        if take_back is not None and str(draft or "").strip():
            take_back(draft)

    def tell(self, kind, agent, report):
        """Narrate one event. Returns at once; the composed line lands in the outbox when ready.

        Off-thread because the brain serializes its turns: an event landing mid-reply waits its
        turn on the brain's own lock, and nothing here may hold up the desk that emitted it."""
        threading.Thread(target=self._narrate, args=(kind, agent, report), daemon=True).start()

    def _narrate(self, kind, agent, report):
        # An errand and a memory nudge are the app's own machinery, not agents with tabs and
        # verdicts: their news is something to SAY, never a name he is asked to choose between.
        # Read out numbered beside a real agent, "errands" cost him five turns trying to close a
        # task that never existed ("I don't even know what errands would be").
        listed = kind not in UNLISTED_KINDS
        if kind == "finished" and self._stage_of(agent) == "landing":
            kind = "landing"
        stage = self._stage_of(agent)
        work = self._work_of(agent) or ""
        plainly = notice(kind, work)
        prompt = (PROMPTS.get(kind, PROMPTS["finished"]).format(agent=agent, report=report)
                  + STAGE_FACT.get(stage, "") + NARRATION_CONDUCT
                  + (WORKS_NAME.format(work=work) if work else ""))
        # One claim on delivering this event: whichever of the two threads takes it speaks, the
        # other stays silent. Without it, a reply landing just as the deadline runs out would be
        # spoken AND covered by the notice - the same news twice.
        claim = threading.Lock()
        claimed = []
        composed = threading.Event()

        def take():
            with claim:
                if claimed:
                    return False
                claimed.append(True)
                return True

        def compose():
            # NOT remembered as a turn of the conversation: composing is not delivering, and this
            # is the one place in the app that routinely throws a finished line away - swallowed
            # as a kick to the agent, dropped for over-claiming, beaten by the deadline. Carried
            # into the window that survives a compaction or a restart, every one of those drafts
            # came back as something the model believed it had told him. What it actually said is
            # written from the delivery instead (SdkBrain.spoke), and what it wrote and nobody
            # heard is taken back (SdkBrain.retract).
            said = ""
            try:
                drafted = self._brain.respond(prompt, remember=False,
                                              **_yields_to_him(self._brain))
            except Exception:
                drafted = ""
            else:
                stripped = _HANDLED_LEAD.sub("", drafted.strip())
                if drafted.strip() and not stripped:
                    # The brain kicked the agent onward itself; there is no news to deliver.
                    # Claimed, so a timed-out waiter doesn't ship a notice about it either way -
                    # composing finished in time or it didn't, and the brain chose silence.
                    take()
                    self._retract(drafted)
                    composed.set()
                    return
                said = stripped
            if take():
                if said.strip() and _claims_deployed(said, stage):
                    # It said the work is out there when it is not. The plain notice carries the
                    # news without the claim; a sentence he would act on must not be a guess.
                    self._retract(said)
                    self._outbox.push(plainly, about=agent, listed=listed, kind=kind, work=work)
                elif said.strip():
                    self._outbox.push(said.strip(), about=agent, composed=True, listed=listed,
                                      kind=kind, work=work)
                else:
                    # The brain could not answer; the capped plain notice still carries the news,
                    # marked app-authored so the ledger reads it back to the brain next turn.
                    self._outbox.push(plainly, about=agent, listed=listed, kind=kind, work=work)
            else:
                # The deadline shipped the plain notice while this was still being written. The
                # late answer is dropped - and taken back, or the model holds a sentence he never
                # heard beside the notice he did, which reads as having said it twice.
                self._retract(said)
            composed.set()

        threading.Thread(target=compose, daemon=True).start()
        if not composed.wait(self._deadline) and take():
            # The brain has sat on this past the deadline - wedged, or buried under a queue that
            # will outlive the user's patience. The notice ships now; the late answer is dropped.
            self._outbox.push(plainly, about=agent, listed=listed, kind=kind, work=work)

"""Agent events become FACTS for the one author - after a triage that keeps non-news off his ears.

What an agent produced used to reach the user as a concatenation - agent name, colon, first
sentence, capped - and he called it what it was: "I don't appreciate how you're speaking to me in
code." Then each event took one trip through the brain HERE, at arrival, and its sentence was stored
as prose to be spoken later. That made two authors of every delivery: a sentence written with no
idea of the moment it would be said in, and the app welding a roll call onto its back.

Now nothing is worded here. An event is judged - is this news for him at all? - and if it is, it is
recorded as a fact: which piece of work in his words, what happened to it, and the agent's report
kept as INPUT for whoever words it. The wording happens once, when the fact is actually spoken, by
`speaker.Speaker`, with everything else that utterance owes composed into it.

The judgement still has to happen at arrival, off his path. An agent's finished turn is often not
news - it is pausing mid-task, or stuck on something technical - and offering him "an update" that
turns out to be nothing is its own failure: "Well then I don't think you should Have Offered it as
an option. If there's nothing actionable for it." So a finished or quiet agent is triaged by the
brain with its tools (tell_agent to kick it onward, ask_foreman for a snag), and only what the
brain calls news becomes a fact. The triage's own words are never spoken and are taken back.
"""

import inspect
import re
import threading

from excephalon.relay import notice

# The routing word, wherever it leads the reply. Alone it means the brain settled the event itself
# and there is nothing for him; it is protocol, never speech.
_HANDLED_LEAD = re.compile(r"(?i)^handled\b[\s\-–—:,.!]*")

# How long a triage may sit inside the brain before the event is simply treated as news. Under the
# loop's own bound on HIS silence, because a background ask that can outlast his patience is one
# his turn can lose to - and three of his turns in a row died waiting on one.
NARRATE_DEADLINE = 60.0

# Events whose news is not an item on his list. The desk's agents have tabs, verdicts and
# landings, and several ready at once are read out numbered so he can take them one at a time.
# The errand hand and the memory inbox are the app's own machinery - a chore he never asked to
# see, and a housekeeping question - and each is simply something to say.
UNLISTED_KINDS = frozenset(("errand", "memory"))

# The kinds that need judging before they are news. A finished turn may be the agent pausing;
# a quiet agent may just need a prod. Everything else - a landing, a death, a written note, an
# errand's outcome, a memory nudge, a walkthrough left pending across a restart - IS news.
TRIAGED = frozenset(("finished", "quiet"))

# What the brain is asked, by kind - for a DECISION, never for words he will hear.
TRIAGE = {
    "finished": (
        "[Agent event, from the app - not the user speaking. Your agent {agent} just finished a "
        "turn and reported:\n{report}\n\nDecide; do not word anything for the user - the app "
        "words it later, if it is news. If the report is only the agent pausing mid-task - "
        "narrating a step, asking leave to continue, nothing done and nothing the user must "
        "decide - do not interrupt them: use tell_agent to tell it to continue, and answer with "
        "the single word: handled. If it is stuck on something TECHNICAL - it needs feedback or a "
        "decision you can't confidently give - use ask_foreman instead of guessing or bothering "
        "the user, and answer: handled. Otherwise - the thing is done, or ready for the user to "
        "look at, or needs their own call (preference, scope, sign-off) - answer with the single "
        "word: news.]"
    ),
    "quiet": (
        "[Agent event, from the app - not the user speaking. Your agent {agent} has {report} - "
        "it may be hung. Use ask_foreman to have the senior model read its log and prod it. If "
        "that settles it, answer with the single word: handled. If it genuinely needs the user, "
        "answer: news.]"
    ),
}


def _yields_to_him(brain):
    """`background=True` for a brain that knows the difference - so his own turn cuts this ask
    loose instead of queueing behind it. A brain that does not is asked exactly as before."""
    return {"background": True} if _takes(brain.respond, "background") else {}


def _takes(call, keyword):
    try:
        return keyword in inspect.signature(call).parameters
    except (TypeError, ValueError):
        return False


class Narrator:
    """Turns one agent event into one recorded fact, off-thread, never lost - after triage."""

    def __init__(self, brain, outbox, stage_of=None, deadline=NARRATE_DEADLINE, work_of=None):
        self._brain = brain
        self._outbox = outbox
        self._deadline = deadline
        # This piece of work in HIS words - what the fact carries, what the plain sentence is
        # built from, and the only name he is ever told for it.
        self._work_of = work_of or (lambda agent: "")
        # Where the agent's work stands in the delivery loop (the desk's delivery_stage) - the
        # same finished turn is presentation news while building, wrap-up news while landing.
        self._stage_of = stage_of or (lambda agent: None)

    def _retract(self, draft):
        """Take a composed line back off the brain's own record - it will never be spoken."""
        take_back = getattr(self._brain, "retract", None)
        if take_back is not None and str(draft or "").strip():
            take_back(draft)

    def tell(self, kind, agent, report):
        """Judge one event and record it. Returns at once; the fact lands in the outbox when ready.

        Off-thread because the brain serializes its turns: an event landing mid-reply waits its
        turn on the brain's own lock, and nothing here may hold up the desk that emitted it."""
        threading.Thread(target=self._narrate, args=(kind, agent, report), daemon=True).start()

    def _narrate(self, kind, agent, report):
        listed = kind not in UNLISTED_KINDS
        stage = self._stage_of(agent)
        if kind == "finished" and stage == "landing":
            kind = "landing"  # the loop's last leg is a conclusion, never something to triage
        if kind in TRIAGED and self._brain is not None and not self._is_news(kind, agent, report):
            return  # the brain settled it itself; there is nothing for him
        work = self._work_of(agent) or ""
        self._outbox.push(notice(kind, work), about=agent, listed=listed, kind=kind, work=work,
                          report=str(report or ""), stage=stage)

    def _is_news(self, kind, agent, report):
        """Ask the brain whether this event is his business at all. Its answer is a decision word,
        never spoken, always taken back off its record - and any failure to answer is news, because
        news must never die with a wedged session."""
        prompt = TRIAGE[kind].format(agent=agent, report=report)
        extras = dict(_yields_to_him(self._brain))
        if _takes(self._brain.respond, "deadline"):
            extras["deadline"] = self._deadline
        try:
            answer = str(self._brain.respond(prompt, remember=False, **extras) or "").strip()
        except Exception:
            return True
        self._retract(answer)
        # Alone, "handled" means it settled the event itself. Leading real words it is only the
        # routing word in front of news - and that news has to reach him.
        return bool(_HANDLED_LEAD.sub("", answer)) if answer else True

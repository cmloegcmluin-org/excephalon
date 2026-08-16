"""What Excephalon says when it comes back up.

"It shouldn't always say 'I'm ready. What can I do for you?' That should only be the default if
we weren't in the middle of something when I restarted." A restart is his only way to pick up a
fix, so most restarts happen mid-conversation - and the stock line greets him as a stranger about
a thread that is minutes old, with a question of his still unanswered on the screen behind it.

Three things decide the difference, and all three are already on disk: the tail of the transcript
(what was in flight), the commit this process boots from against the one the last process ran
(what changed while it was down), and the gap between them (whether "sorry you weren't able to
talk to me for a minute" is a true sentence). This module reads those and hands the brain the one
note it needs; the WORDS are the brain's, because a welcome-back assembled from templates is the
stock greeting again wearing more of them.
"""

import json
from pathlib import Path

from excephalon.delivery import IN_REVIEW
from excephalon.phrases import canonical

STOCK_GREETING = "I'm ready. What can I do for you?"

# What a greeting must never be. A promise of an action is the brain narrating its own future -
# "Back with you... Let me finish reading what actually landed with the drag play cursor fix so
# you know exactly what you're approving" opened a session by resurrecting a dead errand about
# the wrong work, and he had to answer "Dude, what the fuck? No. It's already been shipped."
# An invitation to approve or review is a stage claim, legal only while some thread really is
# in review. Both checks are tiny and fail toward the stock line, which is never wrong - only
# plain.
_PROMISES = ("let me ", "i'm going to ", "i am going to ", "give me a moment", "give me a sec",
             "i'll finish", "i'll read", "i'll check", "i'll look", "i'll go ", "i'll get")
_REVIEW_CLAIMS = ("approv", "review", "verdict", "ready for your eyes", "ready for you to look",
                  "take a look", "waiting on your", "waiting for your")


def unfit(greeting, fleet=""):
    """Why this composed greeting may not be spoken, or None when it may.

    The greeting is one model-written sentence with no conversation behind it yet, so the two
    failure shapes it keeps finding are both checkable: promising work instead of greeting, and
    inviting a verdict when nothing is in review. The stage phrase is delivery.IN_REVIEW, the
    same constant the briefing renders - matched anywhere in the fleet text, so the check and
    the record cannot drift apart."""
    lowered = greeting.lower()
    promise = next((phrase for phrase in _PROMISES if phrase in lowered), None)
    if promise:
        return f"promises an action ('{promise.strip()}…') instead of greeting"
    if IN_REVIEW not in fleet.lower():
        claim = next((phrase for phrase in _REVIEW_CLAIMS if phrase in lowered), None)
        if claim:
            return f"invites approval ('{claim}…') while nothing is in review"
    return None

# Past this, coming back is a fresh start rather than a restart mid-conversation - whatever the
# transcript still holds. "Sorry you weren't able to communicate with me for a minute" is a
# sentence about a minute, not about last night.
RESUMABLE_GAP = 60 * 60


def last_boot(path):
    """Where the previous process stood: {"commit", "at"}, or {} when there was none.

    Read on the way up, so an unreadable record is simply no record - a launch with no console is
    this project's oldest failure, and a torn file must never be what stops the app appearing."""
    try:
        held = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return held if isinstance(held, dict) else {}


def record_boot(path, commit, at):
    """Write down where THIS process stands, for the next one to come back from. One record, not
    a growing pile: only the boot he last had is the question."""
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"commit": commit, "at": at}), encoding="utf-8")
    except OSError:
        pass  # a lost record costs the next welcome-back its detail, never the launch


def last_seen(transcripts):
    """When the last process was last THERE for him: the newest transcript's last write.

    How long he was away is that moment to this boot, never boot to boot - measured boot to boot
    it is the LIFETIME of the session he just spent talking to it, and a restart-to-upgrade after
    a fifty-minute conversation came back with "you were out about 49 minutes" (he was out for
    seconds). Read off the file rather than written at shutdown, because a process that is killed
    or crashes writes nothing on its way out and is exactly when this matters."""
    directory = Path(transcripts)
    try:
        return max((record.stat().st_mtime for record in directory.glob("*.log")), default=0.0)
    except OSError:
        return 0.0


def changes_since(repo, commit, run=None):
    """The subjects of the commits that landed since `commit` - what changed while it was down.

    Their own words: the one honest source for "I've been upgraded to..." is the commit that did
    it. No previous boot, a git that will not answer, a commit this checkout has never heard of -
    all of them are simply nothing to report."""
    if not commit:
        return []
    if run is None:
        from excephalon.worktrees import run_hidden

        run = run_hidden
    try:
        done = run(["git", "-C", str(repo), "log", "--format=%s", f"{commit}..HEAD"],
                   capture_output=True, text=True, timeout=20)
    except Exception:
        return []
    if getattr(done, "returncode", 1) != 0:
        return []
    return [line.strip() for line in (done.stdout or "").splitlines() if line.strip()]


def homecoming_note(turns, changes, away, waiting=(), fleet=""):
    """The note that asks the brain for a welcome-back, or "" when a fresh start is the truth.

    Empty for the three cases where picking a thread up would be wrong: no thread at all, a
    thread he closed himself with a goodbye, and a gap long enough that this is a new day rather
    than a restart. Otherwise it carries everything the answer needs IN the note - the exchange
    it broke off on, what landed while it was down, how long he was without it, and where every
    piece of work STANDS (`fleet`, the desk's own briefing) - rather than trusting the session's
    seed to still hold the last turn. The fleet section exists because the greeting used to know
    only the transcript's prose, and from prose alone it denied that anything had landed and
    reopened an approval that was finished, in one sentence."""
    if not turns or away > RESUMABLE_GAP:
        return ""
    last_said, last_reply = turns[-1]
    if canonical(last_reply).startswith("be seeing you"):
        return ""  # he ended it; there is no task at hand to get back to
    landed = ("Nothing about you changed - he restarted for some other reason."
              if not changes else
              "What landed in you while you were down, in the words of the changes themselves: "
              + "; ".join(changes) + ". Most of what lands is INTERNAL machinery that makes no "
              "difference he could ever notice - if that is what these are, there is nothing to "
              "tell him about it, and you say nothing rather than inventing a meaning for him. "
              "He met one of these as \"a voice safety layer caught some things before they "
              "reached you... you're still driving\", and asked what on earth it meant. But work "
              "the briefing below says was DELIVERED is the opposite of internal: that is his "
              "own ask landing, exactly what he restarted to have.")
    standing = ""
    if fleet:
        standing = ("\n\nWhere his work stands, from the app's own records - the one truth, "
                    f"which your greeting must not contradict:\n{fleet}\nWork marked DELIVERED "
                    "is finished: never re-offer it for review or approval, and never deny it "
                    "landed. Work not marked delivered is never called shipped.")
    owed = ""
    if waiting:
        owed = (f"\n\nThere are already {len(waiting)} update(s) waiting to be spoken to him, and "
                "the app reads that list out itself the moment your greeting ends. So do NOT ask "
                "about any one of them and do not list them - two messages thirteen seconds "
                "apart, one asking about a single update and one offering all of them, is what "
                "he called unnatural.")
    return (
        "[App note, not from him: you have just restarted and this is your FIRST line of the "
        f"session - he is looking at the window now. He was without you for about {away / 60:.0f} "
        f"minute(s). {landed}"
        f"{standing}{owed}\n\n"
        f"The exchange you broke off on - he said: {last_said}\nYou answered: {last_reply}\n\n"
        "Greet him in one short spoken paragraph: welcome him back, briefly acknowledge the gap "
        "if he lost real time, say in one plain clause what changed ONLY if it is something he "
        "would notice, and then pick the conversation back up. A greeting says where things "
        "stand and hands him the floor: never promise an action of your own ('let me finish "
        "reading…' opened a session and made no sense to him), and never invite him to approve "
        "or review anything unless the records above say it is in review. Do not ask what you "
        "can do for him - you already know what you were doing.]"
    )

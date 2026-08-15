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

from excephalon.phrases import canonical

STOCK_GREETING = "I'm ready. What can I do for you?"

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


def homecoming_note(turns, changes, away):
    """The note that asks the brain for a welcome-back, or "" when a fresh start is the truth.

    Empty for the three cases where picking a thread up would be wrong: no thread at all, a
    thread he closed himself with a goodbye, and a gap long enough that this is a new day rather
    than a restart. Otherwise it carries everything the answer needs IN the note - the exchange
    it broke off on, what landed while it was down, how long he was without it - rather than
    trusting the session's seed to still hold the last turn."""
    if not turns or away > RESUMABLE_GAP:
        return ""
    last_said, last_reply = turns[-1]
    if canonical(last_reply).startswith("be seeing you"):
        return ""  # he ended it; there is no task at hand to get back to
    landed = ("Nothing about you changed - he restarted for some other reason."
              if not changes else
              "What landed in you while you were down, in the words of the changes themselves: "
              + "; ".join(changes))
    return (
        "[App note, not from him: you have just restarted and this is your FIRST line of the "
        f"session - he is looking at the window now. He was without you for about {away / 60:.0f} "
        f"minute(s). {landed}\n\n"
        f"The exchange you broke off on - he said: {last_said}\nYou answered: {last_reply}\n\n"
        "Greet him in one short spoken paragraph: welcome him back, say in one plain clause what "
        "changed in you if anything did (what it means for him, never a commit's phrasing read "
        "out), briefly acknowledge the gap if he lost time, and then pick the conversation back "
        "up by putting your own last question to him again. Do not ask what you can do for him - "
        "you already know what you were doing.]"
    )

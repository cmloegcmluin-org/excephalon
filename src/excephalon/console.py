"""What Excephalon shows, kept separate from what it speaks.

The spoken word is transient - it's gone the moment it's said. A surface the user can read is where
they catch up on the reply, see it's thinking rather than hung, and notice an unprompted heads-up.
One seam for all of it keeps the conversation loop about flow rather than formatting, and lets the
same session drive a terminal, a window, or nothing at all (tests, a typed run that shouldn't echo
their own words back at them).

Three outputs, because they answer different questions: `echo`/`overwrite` paint a terminal,
`record` keeps the durable session file, and `messages` reports WHO said each line for a surface
that renders a conversation instead of a log - so the window never re-parses the prefixes written
right here.
"""

import sys

from excephalon.links import as_written
from excephalon.transcript import SELF, SELF_HEADS_UP, SELF_SAID


def _print_flushed(line):
    # Flush so the "(thinking…)" indicator actually appears while it thinks, not after.
    print(line, flush=True)


def _overwrite_flushed(text):
    # Written as-is, with no trailing newline - so text that starts with a carriage return lands
    # back on top of the line just written. That's what collapses a run of ignores onto one line.
    sys.stdout.write(text)
    sys.stdout.flush()


class Console:
    def __init__(self, *, echo=_print_flushed, overwrite=_overwrite_flushed, record=None,
                 messages=None, voice=True, thinking_notice="(thinking…)",
                 listening_notice="(listening… say 'over' when you're done)"):
        self._echo = echo
        self._overwrite = overwrite
        # Where the same lines go to be kept - the terminal scrolls away, and it was the only record
        # of what they actually saw when something went wrong.
        self._record = record or (lambda line: None)
        # Who said each line, for a conversation view. Empty for a terminal, which shows prefixes.
        self._messages = messages or (lambda role, text: None)
        # A voice run narrates the mic - "listening", and what it heard. A typed run needs neither:
        # they have their own prompt and their own words on screen already.
        self._voice = voice
        self._thinking_notice = thinking_notice
        self._listening_notice = listening_notice
        self._ignored = 0  # length of the current run of ignored utterances, collapsed onto one line

    def listening(self):
        # An empty notice says nothing: the window has a mic button and a level meter, so
        # "(listening… say 'over' when you're done)" would be both wrong there and noise.
        if self._voice and self._listening_notice:
            self._line(self._listening_notice)

    def ignored(self):
        """It heard something while asleep and dropped it. A TV in the room can produce these all
        evening, so the run collapses onto a single line whose count ticks up, rather than scrolling
        their terminal away."""
        self._ignored += 1
        tally = f" {self._ignored}x" if self._ignored > 1 else ""
        self._overwrite(f"\r(ignoring…{tally})")

    def _line(self, text, *, show=True):
        """Every ordinary line goes through here so it can first close an open ignore run - without
        that newline it would be written on top of the counter, which is still sitting unterminated.
        A line that isn't shown is still kept: the record is of the session, not of the screen."""
        if self._ignored:
            self._record(f"(ignored {self._ignored} while asleep)")  # the tally, not every scrap
            self._ignored = 0
            self._overwrite("\n")
        if show:
            self._echo(text)
        self._record(text)

    def heard(self, text):
        self._line(f"you said: {text}", show=self._voice)
        self._messages("you", text)

    def thinking(self):
        self._line(self._thinking_notice)
        self._messages("status", self._thinking_notice)

    def reply(self, text):
        # An address it spelled out in words is written back as an address, here where its words
        # become the record: "click through at localhost port 8752" was not something he could
        # click, and the voice had already said those words anyway.
        text = as_written(text)
        self._line(f"{SELF_SAID}{text}\n")  # trailing blank line separates turns in the transcript
        self._messages(SELF, text)

    def spoke(self, text):
        """Something they HEARD that the terminal deliberately doesn't show - the acknowledgement, the
        still-working check-ins. It still belongs in the record: reading a session back and seeing no
        check-ins made it look like none had fired, when they had actually heard every one."""
        text = as_written(text)  # said in words, written as the address - same as a reply
        self._line(text, show=False)
        self._messages(SELF, text)  # they heard it, so a conversation view shows it

    def aside(self, text):
        """Something the APP noticed, in its own voice - "(cut off mid-utterance)", a voice
        error. He hears none of it; it is a note about the turn. Kept in the record and shown
        in the conversation as the quiet grey line it is: ""(cut off mid-utterance)" should not
        appear in a blue word bubble, because it\'s not something Excephalon says"."""
        self._line(text, show=False)
        self._messages("status", text)

    def heads_up(self, text):
        text = as_written(text)  # an unprompted line names places to look too, and they must open
        self._line(f"{SELF_HEADS_UP}{text}\n")  # marked so an unprompted line isn't mistaken for a reply
        self._messages("heads-up", text)

    def evidence(self, text):
        """A technical detail kept for diagnosis: the durable record only - never the screen, the
        window, or the voice. The insulation is the point: a brain failure's cause once rode in
        the spoken reply, and "_AskWedged" was read to the user aloud - a code identifier through
        the one shield, and its audio then landed in their own draft. The record is where the
        cause is USEFUL: stderr under pythonw goes nowhere, and an unexplained failure "has never
        said that and recovered"."""
        self._line(text, show=False)

    def timing(self, *, think, speak):
        self._line(f"  [think {think:.1f}s · speak {speak:.1f}s]")  # the --timings per-turn readout

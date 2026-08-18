"""Several agents ready at once: which are waiting, and which one a reply just named.

One agent finishing is spoken as it lands. Several finishing together used to be run into a single
utterance - a wall with no way to take them one at a time, and long enough that it arrived as
"I've got a longer answer, ready for it?" rather than as news. What was asked for is the other
shape: when several are ready, say which, and let the order be chosen.

So they are read out numbered, and nothing more is said until one of them is named. Numbered
because an agent is named after its worktree - `export-report-as-csv` - and no speech-to-text
spells that back; a number always survives the trip, and any distinctive word of a name works too.
Numbering was chosen over asking the model to interpret the reply, because that needs a marker in
what it writes, and marker text has reached them verbatim before.
"""

import re

NUMBERS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")

_WORD = re.compile(r"[a-z0-9]+")

# Picking one off a read-out list is terse - "two", "the drive one". Anything longer is them
# talking, and an agent's name can easily fall inside an ordinary sentence ("what does the sidebar
# look like now"). Reading a notice at them instead of answering that would lose the turn.
MOST_WORDS = 6

# Picking is affirmative. A short sentence that DENIES something is doing another job - correcting
# a mishearing, most often, since the app reads a name back and the transcriber mangles it: "I
# said errands, not Aaron's" was read as picking that agent and answered with its held news, which
# was a question he had already answered ("What the fuck I already told you, it's Spanish").
_DENIALS = frozenset(("no", "not", "nope", "isnt", "wasnt", "didnt", "dont", "never"))


def roll_call(news):
    """The one line that says who is waiting, numbered so that one of them can be named."""
    if len(news) == 1:
        # Numbering a list of one and then asking which of it they want reads as a form being
        # recited. It is still said: news that goes quiet is news they never get.
        return f"Still waiting: {_label(news[0])}."
    listed = " ".join(f"{_spoken(place).capitalize()}, {_label(item)}."
                      for place, item in enumerate(news, start=1))
    # "updates", because "Four waiting." landed as "four WHATS?" - the count needs its noun.
    return f"{_spoken(len(news)).capitalize()} updates waiting. {listed} Which first?"


def chosen(heard, news):
    """Which of the waiting agents they just named, or None if they were not naming one."""
    spoken = _WORD.findall(heard.lower().replace("'", ""))
    if len(spoken) > MOST_WORDS:
        return None
    said = set(spoken)
    if said & _DENIALS:
        return None  # a correction, not a choice
    # A name first, then a number. "the drive one" carries the word "one", and reading that as the
    # number would answer about a different agent while sounding exactly as though it understood.
    named = [place for place, item in enumerate(news) if said & _name_words(item)]
    if len(named) == 1:
        return named[0]
    if named:
        return None  # a word two of them share; asking again beats answering about the wrong one
    for place, word in enumerate(NUMBERS, start=1):
        if word in said or str(place) in said:
            # Past the end of the list it names nothing: they misspoke, or the list has moved on.
            return place - 1 if place <= len(news) else None
    return None


def _label(item):
    """What to CALL this piece of work out loud: his own words for it.

    It used to be the agent's internal name, so the list read out "scheduled-messages" about work
    he and Excephalon had been calling the timed-reminder feature: "it's weird and confusing that
    in the previous message it chose a different name for the feature than its agent log's name."
    One thread, one name, and it is his."""
    return getattr(item, "work", "") or getattr(item, "about", None) or str(item)


def _name_words(item):
    return set(_WORD.findall(_label(item).lower()))


def _spoken(count):
    """A number as it is said. Past what has a short word of its own, the digit reads the same."""
    return NUMBERS[count - 1] if 1 <= count <= len(NUMBERS) else str(count)

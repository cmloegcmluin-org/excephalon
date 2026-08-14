"""What a message names that can be opened: a web address, or a path on this machine - what counts
as one, what opens it, and how it is said aloud.

They are not technical outside code, so the useful behaviour is not that a path is coloured - it is
that it OPENS. Excephalon writes real Windows paths into the conversation constantly, and reading one
off the screen to type somewhere else is exactly the friction the window exists to remove.

Spoken is the other half of the same question, and the same answer serves both: a thing worth
turning into a link is a thing nobody reads out. What is written stays whole on the page, and
`as_spoken` is what the voice gets instead.

All of it is decided here, purely, so it is settled without a display; the page only paints what
this finds.
"""

import os
import re
import subprocess
import webbrowser
from pathlib import Path, PureWindowsPath

from excephalon import machine

# A drive-letter path, a UNC share, a rooted POSIX path, an http(s) address - or a LOCAL app's
# address the way a person writes it: "localhost:5200", scheme and all dropped. That last one is
# scoped to the two local hosts with an explicit port, because anything looser ("note:", "10:30")
# is ordinary prose. Nothing else: a bare `src/excephalon` is indistinguishable from "and/or" or
# "they/she", and a wrong thing offered as openable is worse than a right one left plain.
#
# Both path shapes count on both desks, because a path is offered by its shape and neither shape
# is ambiguous prose anywhere: English has no leading-slash word, and "C:\..." is nobody's
# sentence. Asking which machine this is would only make `C:\...` on the Mac - and the whole
# suite's paths with it - stop being what it plainly is. A POSIX path needs TWO segments, so a
# lone "/shrug" stays prose.
# A web address stops at an em- or en-dash: no URL carries one (it would be percent-encoded), and
# the brain glues them straight onto addresses - "at http://localhost:5210/—drag the notes" - so
# a dash swallowed into the match took the following word into the link with it ("because no
# space was placed after the URL before the dash, the dash and the following word got grouped
# into the URL link"). Path shapes keep their dashes: a folder really can be named with one.
_URL_BODY = r"[^\s–—]"
_BARE_LOCAL = rf"(?:localhost|127\.0\.0\.1):\d{{2,5}}(?:/{_URL_BODY}*)?"
_POSIX_PATH = r"/[^/\s]+/\S*"
_LINK = re.compile(
    rf"https?://{_URL_BODY}+|{_BARE_LOCAL}|[A-Za-z]:[\\/]\S+|\\\\[^\s\\]+\\\S+|{_POSIX_PATH}")
_IS_BARE_LOCAL = re.compile(_BARE_LOCAL)

# Excephalon writes these inside sentences, so the full stop after a filename is the sentence's and
# the bracket around an address is the sentence's too.
_LEADING, _TRAILING = "\"'<([{", ".,;:!?\"'>)]}"


def link_in(word):
    """What this one word opens, or None. A word that OPENS with an address and runs on into an
    em- or en-dash ("http://localhost:5210/—drag") is that address with the sentence's own
    punctuation glued on: the address is the link, and the dash with whatever follows stays
    prose - `link_parts` already draws any leftover after a link as plain text."""
    target = word.strip().lstrip(_LEADING).rstrip(_TRAILING)
    if _LINK.fullmatch(target):
        return target
    found = _LINK.match(target)
    if found and target[found.end():].startswith(("–", "—")):
        return found.group(0)
    return None


MAX_PATH_WORDS = 8  # a path with more spaces than this is not worth probing the disk over


def link_parts(text, *, exists=os.path.exists):
    """`text` split into what can be opened and what cannot, as [{"text", "link"}].

    The hard case is a space: "C:\\Users\\ada\\Field Notes\\inbox" cannot be told from a
    path followed by another word by looking at the text alone - which is why a single broken link
    is what they saw. So the filesystem is asked. A drive-letter or UNC match is extended across the
    following words to the longest run that actually exists on disk; a run that exists nowhere
    stays the one word it was, exactly as before, and a URL (which can hold no space) is always the
    one word. The page draws only what this returns, so the rule lives here, where it is tested."""
    words = text.split(" ")
    parts, plain, index = [], [], 0
    while index < len(words):
        if link_in(words[index]) is None:
            plain.append(words[index])
            index += 1
            continue
        if plain:
            parts.append({"text": " ".join(plain) + " ", "link": ""})
            plain = []
        span, target = _widest(words, index, exists)
        raw = " ".join(words[index:index + span])
        lead = raw[:len(raw) - len(raw.lstrip(_LEADING))]  # the sentence's own "(" stays outside
        trail = raw[len(lead) + len(target):]              # and its own trailing "." does too
        for piece in ({"text": lead, "link": ""}, {"text": target, "link": target},
                      {"text": trail + " ", "link": ""}):
            if piece["text"]:
                parts.append(piece)
        index += span
    if plain:
        parts.append({"text": " ".join(plain), "link": ""})
    return parts


def offers(target, *, exists=os.path.exists):
    """Would this module, shown exactly this string, turn the whole of it into one link?

    What `/open` asks before opening anything: a POST that opens whatever it is handed is a way to
    run things by talking to the port, so it opens only what the page was actually offered - and
    "offered" is defined by the very function that offered it, spaces and all."""
    return [part["link"] for part in link_parts(target, exists=exists) if part["link"]] == [target]


def _widest(words, index, exists):
    """How many words the path at `index` really spans, and the path itself. The one-word match is
    the floor - offered whether or not it exists, since Excephalon names files a moment before making
    them. A wider run only wins when the disk confirms it, so a real word after a real path is
    never swallowed into it."""
    base = link_in(words[index])
    if base.startswith(("http://", "https://")) or _IS_BARE_LOCAL.fullmatch(base):
        return 1, base  # a web address holds no space, so it is never widened across the disk
    widest_span, widest = 1, base
    for span in range(2, min(MAX_PATH_WORDS, len(words) - index) + 1):
        # `base` already proved this is a drive/UNC path; an extension across a space cannot match
        # `_LINK` (it forbids whitespace), so existence on disk is the whole test for one.
        candidate = " ".join(words[index:index + span]).lstrip(_LEADING).rstrip(_TRAILING)
        if exists(candidate):
            widest_span, widest = span, candidate
    return widest_span, widest


# What a person says instead of reading an address out. A stand-in rather than nothing: dropping
# it would leave a sentence that no longer says there IS anything to open, and they have already
# objected to hearing less than what was written.
SPOKEN_ADDRESS = "the link"

# Identifiers nobody should hear read out: transcription mangles them, and the user's standing
# instruction is to put them on screen instead - which the screen already does; only the AUDIO
# takes the stand-in. A hash is hex with at least one digit (pure words like "deadline" are
# words), seven characters up (git's short form); an id is a UUID or a long run of digits.
_COMMIT_HASH = re.compile(r"(?=[0-9a-f]*\d)[0-9a-f]{7,40}", re.IGNORECASE)
_LONG_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d{7,}",
                      re.IGNORECASE)


# An address SPELLED OUT in words - "click through at localhost port 8752" - which is what the
# brain writes when it tries to do the voice's job for it. Everything it writes is spoken, so it
# has no way to write one thing and say another; asked to say an address naturally, it says it in
# the only channel it has, and what reaches the screen is words nobody can click. The digits may
# come apart too ("port 8-7-5-2"), because that is how the instruction spelled its example.
_SPELLED_LOCAL = re.compile(r"\b(localhost|127\.0\.0\.1)[ ,]+port[ ]+(\d(?:[ -]?\d){1,4})\b",
                            re.IGNORECASE)

# The loopback host by its number - what agents write when they hand a test instance over. It is
# this same machine wearing an unreadable name, and he asked for the word: "it types 'localhost'
# instead of '127.0.0.1'". The boundary keeps 127.0.0.10 - a genuinely different address - as it
# is.
_NUMERIC_LOCALHOST = re.compile(r"\b127\.0\.0\.1\b")


def as_written(text):
    """`text` as the screen keeps it: a spoken-out local address put back as an address, and the
    numeric loopback written as the localhost it is.

    The mirror of `as_spoken`, and the reason both exist in one file: the split between what is
    said and what is written is this module's whole job, so a message that spells an address out
    in words is repaired HERE rather than begged for in a persona. The voice is unaffected - it
    speaks the record's words through `as_spoken` - and the page can offer a link either way;
    localhost and 127.0.0.1 open the same server."""
    text = _SPELLED_LOCAL.sub(lambda found: f"{found.group(1)}:{re.sub(r'[ -]', '', found.group(2))}",
                              text)
    return _NUMERIC_LOCALHOST.sub("localhost", text)


def as_spoken(text):
    """`text` as it should be SAID - the written form stays on screen untouched.

    Nobody reads an address out character by character, and a Windows path read aloud is a minute
    of "backslash". Exact identifiers get the same treatment - "859e704" spoken is a mangled
    transcription waiting to happen, and the standing instruction is screen, not voice. What is
    on screen is still the real thing, so it can be read and clicked."""
    # An em- or en-dash glued to an address ("...5210/—drag the notes") rides inside the matched
    # word, and the voice read the raw address out with "drag" welded on. The dashes are the
    # sentence's, never the address's, so they get their own space before words are judged - the
    # voice reads "a — b" and "a—b" alike, so only the judging changes.
    text = re.sub(r"[–—]", lambda dash: f" {dash.group(0)} ", text)
    return " ".join(_said_aloud(word) for word in text.split())


def _said_aloud(word):
    """One word, with the sentence's own punctuation left around whatever stands in for it."""
    core = word.lstrip(_LEADING)
    lead = word[:len(word) - len(core)]
    kept = len(core.rstrip(_TRAILING))
    core, trail = core[:kept], core[kept:]
    if link_in(core) is not None:
        return lead + _stand_in(core) + trail
    if _LONG_ID.fullmatch(core):
        return lead + "an id" + trail
    if _COMMIT_HASH.fullmatch(core):
        return lead + "a commit id" + trail
    return word


# A local address in any of its written costumes - scheme or not, localhost or the numeric
# loopback, path or not - reduced to the two parts a person says: the host and the port.
_LOCAL_ADDRESS = re.compile(r"(?:https?://)?(?:localhost|127\.0\.0\.1):(?P<port>\d{2,5})(?:/\S*)?")


def _stand_in(target):
    """An address is "the link"; a path is its last part, which is the part a person would say -
    "it's in profile.md", never the eight folders above it. A LOCAL address is said the way he
    asked to hear it - "http://localhost:5210" as "localhost port 5210" - because spoken any more
    literally it came out as letters and punctuation points read one at a time ("quite
    unnatural"), with the numeric loopback the worst costume of all. The path stays on screen,
    where it can be clicked."""
    local = _LOCAL_ADDRESS.fullmatch(target)
    if local:
        return f"localhost port {local['port']}"
    if target.startswith(("http://", "https://")):
        return SPOKEN_ADDRESS
    return PureWindowsPath(target).name or SPOKEN_ADDRESS


def _on_this_machine(where):
    """This desk's own "open this": a folder in its file manager, a file in whatever owns that
    kind. Windows has a call for it and no command; macOS has a command and no call, and asking
    for `os.startfile` there is an AttributeError at the moment of the click."""
    if machine.WINDOWS:
        os.startfile(where)
    else:
        subprocess.run(["open", where], check=False)


def open_link(target, *, browser=webbrowser.open, shell=_on_this_machine):
    """Open what was clicked - an address in the browser, anything else on this machine.

    A path Excephalon has named but not written yet opens the nearest folder above it that IS there.
    It names a file in the same breath as making it, so a click can land a moment early, and a
    click that opens nothing at all reads as the window being broken rather than as being early.
    If nothing in the path exists, nothing opens - there is no such place to show."""
    if target.startswith(("http://", "https://")):
        browser(target)
        return
    if _IS_BARE_LOCAL.fullmatch(target):
        browser(f"http://{target}")  # written the human way; the browser still needs its scheme
        return
    where = Path(target)
    while not where.exists() and where.parent != where:
        where = where.parent
    if where.exists():
        shell(str(where))

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
from urllib.parse import unquote

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
# EVERY shape stops at an em- or en-dash. The brain glues them straight onto whatever it just
# named - "at http://localhost:5210/—drag the notes", "...aug14-20.html—and send your routine" -
# and a dash swallowed into the match takes the following word into the link with it ("the ' —
# and' is part of the link still"). An ordinary hyphen is a name's own character and stays; an
# em- or en-dash is the sentence's punctuation everywhere, in a URL (which would percent-encode
# one) and in a path alike. Scoping this to web addresses alone is what left the path case
# broken after the first fix.
#
# `file:///C:/...` is a path wearing a scheme - what an agent writes, because a link in the
# desktop Claude app needs one. Here it is neither clickable nor readable: unrecognized, it was
# drawn as plain text and read out character by character.
_BODY = r"[^\s–—]"
_BARE_LOCAL = rf"(?:localhost|127\.0\.0\.1):\d{{2,5}}(?:/{_BODY}*)?"
_POSIX_PATH = rf"/[^/\s–—]+/{_BODY}*"
_FILE_URL = rf"file://{_BODY}+"
_LINK = re.compile(
    rf"https?://{_BODY}+|{_FILE_URL}|{_BARE_LOCAL}|[A-Za-z]:[\\/]{_BODY}+|\\\\[^\s\\–—]+\\{_BODY}+"
    rf"|{_POSIX_PATH}")
_IS_BARE_LOCAL = re.compile(_BARE_LOCAL)
_IS_FILE_URL = re.compile(_FILE_URL)


# A markdown link, the shape every coding agent writes: the label is what a person reads and
# says, the address is what a click opens. Unparsed, the whole construct fell through the
# word-scanner - "[▶ Launch the Auto-play demo](http://localhost:41777/launch?...)" drew as
# plain text and the voice read the address out character by character ("it's still sending
# links that aren't clickable, and still trying to read them aloud"). The label is the part
# with meaning; the address is machinery for the click.
_MARKDOWN = re.compile(r"\[(?P<label>[^\]\n]+)\]\((?P<url>[^)\s]+)\)")


def bare_path(target):
    """A `file://` URL as the plain path it names, or `target` unchanged.

    `file:///C:/My%20Notes/x.html` -> `C:/My Notes/x.html`: the scheme dropped, the slash before a
    drive letter dropped with it, and the percent-escapes read back as the characters they stand
    for. That plain form is what this module already draws as a link and already opens."""
    if not _IS_FILE_URL.fullmatch(target):
        return target
    path = unquote(target[len("file://"):])
    return path[1:] if re.match(r"/[A-Za-z]:[\\/]", path) else path

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

    Each LINE is judged on its own. Split on spaces alone, a newline stays inside a word, and an
    address ending a list line arrives welded to the next line's first word -
    "http://localhost:8770/projects\\n2." - which matches nothing, so the one address the message
    existed to hand over was drawn as plain text ("it's not a clickable link"). A path never spans
    a line break, so per-line loses nothing.

    The hard case is a space: "C:\\Users\\ada\\Field Notes\\inbox" cannot be told from a
    path followed by another word by looking at the text alone - which is why a single broken link
    is what they saw. So the filesystem is asked. A drive-letter or UNC match is extended across the
    following words to the longest run that actually exists on disk; a run that exists nowhere
    stays the one word it was, exactly as before, and a URL (which can hold no space) is always the
    one word. The page draws only what this returns, so the rule lives here, where it is tested."""
    parts = []
    for line in text.split("\n"):
        if parts:  # the break between lines is the prose's own character, never a link's
            if parts[-1]["link"]:
                parts.append({"text": "\n", "link": ""})
            else:
                parts[-1]["text"] += "\n"
        for piece in _line_parts(line, exists):
            if not piece["link"] and parts and not parts[-1]["link"]:
                parts[-1]["text"] += piece["text"]
            else:
                parts.append(piece)
    return parts


def _line_parts(line, exists):
    """One newline-free line: markdown links first - their labels hold spaces, so they must be
    lifted out whole before any word-splitting - then the word-scanner over what remains."""
    parts, at = [], 0
    for found in _MARKDOWN.finditer(line):
        parts += _word_parts(line[at:found.start()], exists)
        parts.append({"text": found["label"], "link": found["url"]})
        at = found.end()
    parts += _word_parts(line[at:], exists)
    return parts


def _word_parts(line, exists):
    """A stretch with no markdown in it, split exactly as `link_parts` always split whole texts."""
    if not line:
        return []
    words = line.split(" ")
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
SPOKEN_FILE = "the file"

# A file name a person would say aloud: plain words, no digits, short. "profile.md" is how he says
# that one; "weekly-schedule-aug14-20.html" read out is a string of letters and numbers, and he
# asked for "the file" instead ("or something natural like a human would have said here").
_SAYABLE_NAME = re.compile(r"[A-Za-z][A-Za-z-]{0,23}(?:\.[A-Za-z]{1,4})?")

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

# A path wearing the `file://` scheme, anywhere in a sentence - written back as the plain path.
_WRITTEN_FILE_URL = re.compile(_FILE_URL)


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
    text = _WRITTEN_FILE_URL.sub(lambda found: bare_path(found.group(0)), text)
    return _NUMERIC_LOCALHOST.sub("localhost", text)


def as_spoken(text):
    """`text` as it should be SAID - the written form stays on screen untouched.

    Nobody reads an address out character by character, and a Windows path read aloud is a minute
    of "backslash". Exact identifiers get the same treatment - "859e704" spoken is a mangled
    transcription waiting to happen, and the standing instruction is screen, not voice. What is
    on screen is still the real thing, so it can be read and clicked."""
    # A markdown link is spoken as its LABEL - that is what the label is for - with the symbols
    # dropped ("▶" is a button glyph, not a word). Before any splitting, because the label holds
    # spaces and the construct read piecewise is the address read raw.
    text = _MARKDOWN.sub(lambda found: _spoken_label(found["label"]), text)
    # An em- or en-dash glued to an address ("...5210/—drag the notes") rides inside the matched
    # word, and the voice read the raw address out with "drag" welded on. The dashes are the
    # sentence's, never the address's, so they get their own space before words are judged - the
    # voice reads "a — b" and "a—b" alike, so only the judging changes.
    text = re.sub(r"[–—]", lambda dash: f" {dash.group(0)} ", text)
    return " ".join(_said_aloud(word) for word in text.split())


def _spoken_label(label):
    """A markdown label as speech: its words, or the stand-in when nothing survives."""
    words = re.sub(r"[^A-Za-z0-9' -]+", " ", label).split()
    return " ".join(words) if words else SPOKEN_ADDRESS


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
    name = PureWindowsPath(bare_path(target)).name
    if not name:
        return SPOKEN_ADDRESS
    # A name a person would actually say gets said - "it's in profile.md" is exactly how he says
    # that one. A name carrying digits or running long is not speech: read out,
    # "weekly-schedule-aug14-20.html" is a string of letters and numbers, and he asked for "the
    # file" instead. The screen still shows the real name, which is where a name is read anyway.
    return name if _SAYABLE_NAME.fullmatch(name) else SPOKEN_FILE


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
    # A `file://` URL is a path in a costume: opened on this machine like any other path, never
    # handed to a browser, which would show the file instead of the app that owns it.
    where = Path(bare_path(target))
    while not where.exists() and where.parent != where:
        where = where.parent
    if where.exists():
        shell(str(where))

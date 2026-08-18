"""What an agent is allowed to say to the user: a notice, never its own words.

They were handed commit hashes, test counts and "I reran the suite myself" verbatim, and could not
tell whether they were talking to Excephalon or to the agent it was driving. Telling the model not to
relay was not enough - they asked for the code to prevent it - so nothing an agent writes reaches the
outbox except this: the gist, capped, with the rest left where it is.

This is the FALLBACK, reached only when the brain could not word the event itself - and it is still
spoken in Excephalon's voice, so it may not read as a label. It used to open with the agent's
internal name and close by sending him to that agent's tab: "errands: The agent that fixed the
proactive-notice bug is registered as `excephalon-139-bug-excephalon-occasionally`... (the rest is
in errands's tab)". His answer: "Does a human walk up to their coworker in an office space and just
begin a conversation with the word 'errands'? No, of course not. This is not natural human
behavior." Both the name-tag and the tab pointer are gone - the logs are Excephalon's to read, not
his - and what is left is one sentence of the news itself.
"""

import re

NOTICE_CHARS = 160  # a sentence's worth; past this it is the agent talking, not a notice

_SENTENCE_END = re.compile(r"(?<=[.!?])\s")

# The agent's own name at the head of its report, which its log lines carry and its reports copy.
# Stripped rather than trusted: it is the same label by another road.
_TAGGED = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\s*:\s+")


def notice(agent, report):
    """One line of the news he can act on, in plain words - no name-tag, no pointer to a tab."""
    said = " ".join(str(report).split())
    said = _TAGGED.sub("", said)
    if not said:
        return "There's word from your work that I couldn't put into words."
    first = _SENTENCE_END.split(said, maxsplit=1)[0]
    if len(first) > NOTICE_CHARS:
        first = first[:NOTICE_CHARS].rstrip() + "…"
    return first

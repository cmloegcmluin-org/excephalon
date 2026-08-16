"""One gate every stored line passes before it is spoken: is this still true to say?

The failure this ends is a category, not an incident. Anything the app has QUEUED - an agent's
report, an errand's answer, a revival reminder - was composed at one moment and spoken at
another, and in between the conversation moves. The queue cannot know that: a piece of news
carries its words, which agent it is about, and who wrote it, and nothing else. So a recorded
question was played back four minutes after he had answered it ("What the fuck I already told
you, it's Spanish"), and the fix each time was to name one more route by which a stale recording
could reach him - retire, tell_agent, superseded, drained-in-hand, errands. Six patches, each
about a path, none about the disease: "a thin skin around a bunch of stupid little idiots who
don't talk to each other".

The one part that does know the conversation is the brain, because it has been having it. So the
check is one question TO the brain, carrying the exact words about to be spoken. It answers SAY
or SKIP and nothing else: it may never hand back a better version of the line, because the app
speaks stored news word for word on purpose - the brain retelling held news in its own words is
what put two versions of the same update in front of him thirteen seconds apart.

Every uncertainty falls toward saying it. A brain that is wedged, absent, or unclear lets the
line through, because news that is never spoken is the worse failure by far: a merged feature
once died unheard in a queue and read to him as work thrown into a black hole.
"""

SKIP = "skip"

GATE_PROMPT = (
    "[App check, not from him and not a turn of the conversation. You are about to say this to "
    "him, word for word, from something composed earlier:\n\n{line}\n\n"
    "Given the conversation you have just been having with him, is that still worth saying? "
    "It is NOT if he has already answered it, if you have already told him the same thing, or "
    "if what has happened since has made it wrong or pointless.\n\n"
    "Reply with exactly one word: SAY if it should go out as it stands, SKIP if it has been "
    "overtaken. Do not reword it, do not explain, do not write a replacement - the words above "
    "are what he will hear if you say SAY.]"
)


def overtaken(brain, line, *, prompt=GATE_PROMPT):
    """True when this stored line has been overtaken by the conversation and must not be spoken.

    False for everything else - no brain, a wedged brain, an unclear answer - because the gate
    may only ever hold back a line the brain plainly says is finished with."""
    if brain is None or not str(line).strip():
        return False
    try:
        answered = brain.respond(prompt.format(line=line), remember=False)
    except Exception:
        return False  # the check is not worth the news
    return str(answered or "").strip().lower().startswith(SKIP)

"""What the app may say about an agent when it has to speak for itself.

An agent's own words never reach the user. They were handed commit hashes, test counts and "I
reran the suite myself" verbatim, and could not tell whether they were talking to Excephalon or to
the agent it was driving. The fallback here used to relay the agent's FIRST SENTENCE, which is the
same failure wearing a cap: "The fresh demo is clean: exactly the four curated scenarios, two
clean Excephalon messages, no raw 'Red', no 'tab' pointer" reached him whole, and every noun
in it belonged to a conversation he was never part of - "what is a 'fresh' demo?? what four
curated scenarios? what two clean Excephalon messages? basically this whole message is useless,
insane, confusing, and terrible."

So this says only what the APP knows: which piece of work, in HIS words, and what happened to it.
No agent prose, no internal name, no jargon, and never a pointer to a log ("the purpose of
Excephalon is to insulate me from these agent logs; I do not want to check them"). It is the
fallback for when the brain cannot word an event itself, so it must be sayable with nothing but
the two facts the app is certain of.
"""

# What each kind of event MEANS to him, as a sentence about his own work. Deliberately incurious
# about the agent's report: a fallback that quoted it is what made this file a problem.
_SAID = {
    "finished": "There's an update on {work}.",
    "wrote": "There's an update on {work}.",
    "pending": "{work} is still waiting on your yes or no.",
    "landing": "{work} is being landed now.",
    "landed": "{work} is done and in.",
    "died": "{work} has run into trouble and needs you.",
    "quiet": "{work} has gone quiet.",
    "errand": "I finished that errand for you.",
    "memory": "There's one thing from what I remember that I'd like your call on.",
}

FALLBACK = "There's an update on your work."


def notice(kind, work=""):
    """One plain sentence about his work, in his words - the app speaking for itself."""
    work = " ".join(str(work or "").split())
    said = _SAID.get(str(kind))
    if said is None:
        return FALLBACK
    if "{work}" not in said:
        return said
    return said.format(work=work) if work else FALLBACK

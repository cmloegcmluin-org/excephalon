"""Which agent is on which task, both ways round.

The Projects tab shows a task's agent as an indicator beside it - click through to that agent's
log; an agent's log links back to the task it is on. The tie between the two is the agent's
`enhancement`: the verbatim item text it was started on, which the desk records to
runtime/agents.json (agent_desk.AgentDesk._write_state). A task is matched to its agent the loose
way the desk itself ticks by (memory.complete_enhancement) - the item whose words contain the
enhancement, give or take a #id prefix, a filing stamp, and casing.
"""

import json
import re
from pathlib import Path

# What separates the words to match on from the bookkeeping around them: a leading #id and a
# trailing "(filed …)" stamp are the item's, not the ask's, so neither should decide a match.
_ID = re.compile(r"^\s*#\d+\s+")
_FILED = re.compile(r"\s*\(filed [^)]*\)\s*$")


def fleet_enhancements(state_path):
    """{agent name: the task text it is on}, from the fleet's survival record.

    Only the agents started on an Enhancements item carry one; the rest are skipped. No path (a
    checkout with no fleet behind it), or a missing or unreadable record, is simply an empty fleet
    - the tab draws without links rather than failing.
    """
    if state_path is None:
        return {}
    try:
        record = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    fleet = {}
    for entry in record:
        name = entry.get("name")
        enhancement = (entry.get("enhancement") or "").strip()
        if name and enhancement:
            fleet[name] = enhancement
    return fleet


def slug(title):
    """A card's title as the DOM id its section carries - `card-<slug>` - matching the template's
    own `title | lower | replace(' ', '-')`, so an anchor built here lands on the card drawn there."""
    return title.lower().replace(" ", "-")


def anchor_for(title, item_id):
    """The id a linked task row carries, unique across the page: its card, then its number."""
    return f"task-{slug(title)}-{item_id}"


def _fold(text):
    """The words a match is made on: a #id prefix and a filing stamp dropped, case and spacing
    normalised - so a fragment the brain quoted still lands on its item, the way the tick does."""
    text = _FILED.sub("", _ID.sub("", str(text or "")))
    return " ".join(text.casefold().split())


def assign(cards, fleet):
    """Mark each task an agent is on, and hand back the reverse map for the Agents tab.

    In place: every item whose words contain an agent's enhancement gains `agent` (the name, drawn
    as the indicator) and `anchor` (its own row id, what the back-link targets). Returned:
    {agent name: {anchor, title, text}} - all the Agents tab needs to link the other way.

    An agent lands on its FIRST matching item only - across the cards in order, open items before
    done - exactly as the desk ticks the first hit. An item with no #id is not a thing a link can
    point at, so it is passed over."""
    waiting = {name: _fold(enhancement) for name, enhancement in fleet.items()
               if _fold(enhancement)}
    by_agent = {}
    for card in cards:
        for item in card["active"] + card["done"]:
            if item.get("id") is None:
                continue
            words = _fold(item["text"])
            name = next((who for who, enhancement in waiting.items() if enhancement in words), None)
            if name is None:
                continue
            waiting.pop(name)  # one agent, one task - it never marks a second
            anchor = anchor_for(card["title"], item["id"])
            item["agent"], item["anchor"] = name, anchor
            by_agent[name] = {"anchor": anchor, "id": item["id"],
                              "title": card["title"], "text": item["text"]}
    return by_agent

import json

from excephalon.agent_tasks import anchor_for, assign, fleet_enhancements, slug


def _card(title, *items, movable=True):
    """A project card as the web route hands it over: a title, its items split open-before-done.
    Each item is (id, text, done)."""
    return {"title": title, "movable": movable,
            "active": [{"id": i, "text": t, "done": False} for i, t, d in items if not d],
            "done": [{"id": i, "text": t, "done": True} for i, t, d in items if d]}


def test_fleet_enhancements_reads_each_agents_task_text(tmp_path):
    # The desk records every live agent to runtime/agents.json, an `enhancement` beside each that
    # is the verbatim item it is completing (agent_desk._write_state). That field is the whole tie
    # between an agent and its task; the agents started on nothing carry none and are skipped.
    state = tmp_path / "agents.json"
    state.write_text(json.dumps([
        {"name": "credits-warn", "enhancement": "warn about credits"},
        {"name": "no-task", "enhancement": None},
        {"name": "blank", "enhancement": "   "},
    ]), encoding="utf-8")

    assert fleet_enhancements(state) == {"credits-warn": "warn about credits"}


def test_a_missing_or_broken_record_is_an_empty_fleet(tmp_path):
    # A tab with no fleet file behind it (a fresh machine, a test) draws its tasks without links
    # rather than throwing; a half-written record reads the same way.
    assert fleet_enhancements(tmp_path / "nope.json") == {}
    broken = tmp_path / "agents.json"
    broken.write_text("{ not json", encoding="utf-8")
    assert fleet_enhancements(broken) == {}


def test_assign_marks_the_task_and_maps_the_agent_back():
    # The task an agent is on gains the agent's name (the indicator) and its own row id (what the
    # back-link targets); the reverse map is what the Agents tab draws its link from.
    cards = [_card("Excephalon", (3, "warn about credits", False),
                   (4, "live captions", False), movable=False)]

    by_agent = assign(cards, {"credits-warn": "warn about credits"})

    item = cards[0]["active"][0]
    assert item["agent"] == "credits-warn"
    assert item["anchor"] == "task-excephalon-3"
    assert cards[0]["active"][1].get("agent") is None  # the untouched task stays plain
    # The reverse map carries all the Agents tab draws its link from: the card (project) name, the
    # task's number and its words, and the anchor to jump to.
    assert by_agent == {"credits-warn": {"anchor": "task-excephalon-3", "id": 3,
                                         "title": "Excephalon", "text": "warn about credits"}}


def test_matching_ignores_case_an_id_prefix_and_a_filed_stamp():
    # The brain quotes the item loosely - sometimes with its #id, sometimes a fragment - so the
    # match folds the same bookkeeping away the desk's own tick does.
    cards = [_card("RTT app", (1, "Tune the mapping curve", False))]

    by_agent = assign(cards, {"tuner": "#1  tune the MAPPING curve"})

    assert cards[0]["active"][0]["anchor"] == "task-rtt-app-1"
    assert by_agent["tuner"]["anchor"] == "task-rtt-app-1"


def test_an_agent_lands_on_only_its_first_matching_task():
    # Two open items both contain the words; the agent marks the first and is spent, so the second
    # stays plain - one agent cannot be in two places.
    cards = [_card("Excephalon", (1, "fix the thing", False), (2, "fix the thing twice", False))]

    by_agent = assign(cards, {"fixer": "fix the thing"})

    assert cards[0]["active"][0]["agent"] == "fixer"
    assert cards[0]["active"][1].get("agent") is None
    assert by_agent["fixer"]["anchor"] == "task-excephalon-1"


def test_an_item_with_no_id_is_not_something_a_link_can_reach():
    cards = [{"title": "Excephalon", "movable": False,
              "active": [{"id": None, "text": "warn about credits", "done": False}], "done": []}]

    by_agent = assign(cards, {"x": "warn about credits"})

    assert cards[0]["active"][0].get("agent") is None  # nothing to anchor, so no link either way
    assert by_agent == {}


def test_slug_and_anchor_match_the_ids_the_template_draws():
    assert slug("RTT app") == "rtt-app"
    assert anchor_for("Excephalon", 3) == "task-excephalon-3"

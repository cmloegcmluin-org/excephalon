from excephalon.memory import profile_sections
from excephalon.mirror import Mirror, TranscriptFeed, TranscriptModel
from excephalon.web import create_app


def _model(*said):
    """A conversation drawn from recorded MESSAGES - what the window replays. Each is
    ("message", role, stamp, text), or ("day", date) / ("session",) for a break."""
    model = TranscriptModel(clock=lambda: "12:00:00")
    for message in said:
        model.apply("history", message)
    return model


def _logged(*lines):
    """An AGENT's log, which is still lines with prefixes - a different archive, its own door."""
    model = TranscriptModel(clock=lambda: "12:00:00")
    for line in lines:
        model.apply("log", line)
    return model


def _client(model=None, **wiring):
    wiring.setdefault("on_submit", lambda text: None)
    return create_app(model if model is not None else _model(), **wiring).test_client()


def _rule_for(css, selector):
    """The declaration block served for exactly this selector, so an assertion names the rule it
    means rather than fishing for a substring anywhere in the stylesheet."""
    start = css.index(selector + " {")
    body = css.index("{", start) + 1
    return css[body:css.index("}", body)]


def test_the_page_hands_over_who_said_what_rather_than_transcript_lines():
    model = _model(("day", "2026-07-18"),
                   ("message", "you", "02:41:38", "morning"),
                   ("message", "excephalon", "02:42:10", "Morning."))

    shown = _client(model).get("/messages").get_json()

    assert [entry["role"] for entry in shown["entries"]] == ["day", "you", "excephalon"]
    assert shown["entries"][1]["name"] == "You"  # who said it, resolved once, on the server
    assert shown["entries"][2]["name"] == "Excephalon"  # the display name; the role key is its own thing
    assert shown["sessions"] == [{"label": "2026-07-18 02:41", "at": 0}]


def test_a_message_carries_a_full_dated_reference_the_copy_link_can_locate():
    # The copy-link pointer he pastes back at Excephalon was only "You · 05:01:59" - a time with no
    # day, which "could be any fucking day". Each message now carries a reference with the date of
    # the break above it, and it is worked out from the whole thread so it stays right even when
    # the poll starts past that break. A break is a place, not a moment, so it carries none.
    model = _model(("day", "2026-07-18"),
                   ("message", "you", "02:41:38", "morning"),
                   ("message", "excephalon", "02:42:10", "Morning."))

    shown = _client(model).get("/messages?since=2").get_json()

    assert shown["entries"][0]["reference"] == "Excephalon · 2026-07-18 02:42:10"

    whole = _client(model).get("/messages").get_json()["entries"]
    assert whole[1]["reference"] == "You · 2026-07-18 02:41:38"
    assert whole[0]["reference"] == ""  # the day break is a place, not a turn to point at


def test_a_message_carries_the_bare_moment_the_link_button_builds_a_url_from():
    # The link button copies an actual URL - http://<host>/#at=<moment> - that reopens the
    # conversation at that turn, not the readable "Name · …" text. The server hands over the bare
    # moment (date and time, no name) for the page to encode into that hash.
    model = _model(("day", "2026-07-18"), ("message", "excephalon", "02:42:10", "Morning."))

    entries = _client(model).get("/messages").get_json()["entries"]

    assert entries[1]["moment"] == "2026-07-18 02:42:10"
    assert entries[0]["moment"] == ""  # a break heads a place, not a moment to link to


def test_a_poll_carries_only_what_the_page_has_not_drawn():
    # Four times a second against every session ever recorded, so it cannot hand back the lot.
    model = _model(("day", "2026-07-18"),
                   ("message", "you", "02:41:38", "morning"),
                   ("message", "excephalon", "02:42:10", "Morning."))
    client = _client(model)

    shown = client.get("/messages?since=2").get_json()

    assert [entry["text"] for entry in shown["entries"]] == ["Morning."]
    assert (shown["at"], shown["total"]) == (2, 3)  # where it starts, and how much there now is
    assert client.get("/messages?since=99").get_json()["entries"] == []  # never past the end


def test_every_session_break_is_named_where_it_stands():
    # The breaks are identical dicts, so anything locating one by value found the first of them
    # and sent every row of the contents to the same place.
    model = _model(("day", "2026-07-18"), ("message", "you", "02:41:38", "morning"),
                   ("session",), ("message", "you", "16:30:34", "back"),
                   ("session",), ("message", "you", "18:00:00", "evening"))

    shown = _client(model).get("/messages").get_json()

    assert [session["at"] for session in shown["sessions"]] == [0, 2, 4]
    # And a break carries its own name, so it reads as the row that points at it.
    assert [entry["label"] for entry in shown["entries"] if entry["role"] == "session"] == [
        "2026-07-18 16:30", "2026-07-18 18:00",
    ]


def test_the_bar_reaches_every_page_and_carries_the_restart_button(tmp_path):
    profile = tmp_path / "profile.md"
    profile.write_text("## Goals\n- swim\n\n## Projects\n- entity\n", encoding="utf-8")
    client = _client(profile_path=profile)

    pages = ("/", "/config", "/projects", "/agents")
    for path in pages:
        page = client.get(path).get_data(as_text=True)
        for other in pages:
            assert f'href="{other}"' in page  # every page reaches every other one
        # One click from a landed fix to running it, wherever he happens to be looking.
        assert 'id="restart"' in page


def test_the_restart_says_it_is_updating_instead_of_leaving_him_guessing():
    # "It does sit there for a long time leaving me in suspense whether it's going to work or
    # not." The window goes down, a helper waits for the process to die, a fresh one comes up -
    # all of it silent. The veil now says so, and cannot be waved away: nothing is being asked.
    page = _client().get("/").get_data(as_text=True)
    assert 'id="updating"' in page and "Updating" in page

    js = _client().get("/static/closing.js").get_data(as_text=True)
    restart = js[js.index('getElementById("restart")'):]
    assert "updating.hidden = false" in restart
    assert restart.index("veil.hidden = false") < restart.index('fetch("/restart"')  # said first
    assert "leaving" in js and "if (!leaving) veil.hidden = true" in js  # and not dismissable


def test_the_tabs_this_page_replaced_still_answer(tmp_path):
    # A window standing open across the update lands on the new page, not on a 404.
    client = _client()

    for old in ("/profile", "/persona", "/memory", "/translations"):
        answer = client.get(old)
        assert answer.status_code == 302
        assert answer.headers["Location"].endswith("/config")


def test_the_bar_stays_frozen_with_the_same_air_above_and_below_the_pills():
    # The reading pages scroll at the document level, so the bar is pinned over its own opaque
    # background. Its air is INSIDE it (padding, symmetric): as an outside margin it scrolled
    # away - "it also strangely scrolls down to remove the margin above the row of pills" - and
    # below the pills there was never any, the border sitting flush on their bottoms.
    css = _client().get("/static/app.css").get_data(as_text=True)

    frozen = _rule_for(css, "body.page .topbar, body.config .topbar")
    assert "position: sticky" in frozen
    assert "top: 0" in frozen
    assert "background:" in frozen  # opaque, or the scrolled content bleeds through the tabs
    assert "padding: 10px 0" in frozen  # the same air above the pills and below them


def test_the_copy_buttons_sit_above_the_full_width_break_rows():
    # A day or session break is a full-width row, and it is painted AFTER the copy buttons: the
    # buttons are the thread's first children, every entry is appended after them, and .day /
    # .session / .said are all position:relative. With equal z-index the later row wins the paint
    # order, so the full-width break sat ON TOP of the copy button that hovering it had just
    # placed - the button showed but the click landed on the transparent row over it, which is why
    # copying a date heading did nothing while copying a bubble (whose gutter is clear) worked. A
    # z-index lifts the button back above the rows.
    css = _client().get("/static/app.css").get_data(as_text=True)

    copy = _rule_for(css, ".copy")
    assert "z-index:" in copy, "the copy button needs a stacking order above the break rows"
    zindex = int(copy.split("z-index:")[1].split(";")[0].strip())
    assert zindex >= 1


def test_the_config_page_shows_its_life_context_and_saves_it_back(tmp_path):
    profile = tmp_path / "profile.md"
    profile.write_text("## Life context (for awareness)\n- new to the city\n", encoding="utf-8")
    client = _client(profile_path=profile)

    page = client.get("/config").get_data(as_text=True)
    assert "new to the city" in page
    # Matched by stem, since a profile glosses its own headings however it likes.
    assert 'data-heading="Life context (for awareness)"' in page

    # Life context is background, not work, so it saves back as plain bullets - no boxes in the
    # file - and the save resolves the stem the way the read does, never forking a rival section.
    client.post("/profile", json={"heading": "Life context", "drawn": ["new to the city"],
                                  "items": [{"done": False, "text": "new to the city, since June"}]})

    saved = profile.read_text(encoding="utf-8")
    assert "- new to the city, since June" in saved
    assert "- [ ]" not in saved                    # a bullet list, never a checklist
    assert saved.count("## Life context") == 1     # into the glossed section, never a rival one


def test_the_excephalon_list_is_a_checklist_that_ticks_rather_than_deletes(tmp_path):
    # The Excephalon card on the Projects tab is the Enhancements roadmap - the same checklist it
    # always was, just shown as Excephalon's own project (#128).
    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements they want for you (roadmap, not now)\n"
                       "- [x] hear only their voice\n- live captions\nplain line\n", encoding="utf-8")
    client = _client(profile_path=profile)

    page = client.get("/projects").get_data(as_text=True)
    # A box to click, not `- [x]` spelled out for the reader to decode - and any line with words
    # on it is an item, since they are typed in plain.
    assert page.count("<input type=\"checkbox\"") == 3
    assert page.count('<li class="done">') == 1

    # Ticking one writes the whole list back as markdown, which is the form the brain reads. Saving
    # the enhancements list also numbers it, so every item comes back with a stable id.
    client.post("/profile", json={
        "heading": "Enhancements they want for you (roadmap, not now)",
        "drawn": ["hear only their voice", "live captions", "plain line"],
        "items": [{"id": None, "done": True, "text": "hear only their voice"},
                  {"id": None, "done": True, "text": "live captions"},
                  {"id": None, "done": False, "text": "plain line"}],
    })

    saved = profile.read_text(encoding="utf-8")
    assert "- [x] #2 live captions" in saved  # ticked, not removed - the record that it was done
    assert "- [ ] #3 plain line" in saved      # and a plain line joined the list it was meant to


def test_the_excephalon_card_shows_each_items_id(tmp_path):
    # "Add IDs to all of the enhancements so I can refer to them by ID." The number is drawn beside
    # the item and carried on the row, so a save sends it back and the same item keeps the same id.
    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements they want (roadmap, not now)\n"
                       "- [ ] #4 better voice\n- [x] #2 older idea\n", encoding="utf-8")
    client = _client(profile_path=profile)

    page = client.get("/projects").get_data(as_text=True)
    assert 'data-id="4"' in page and "#4" in page
    assert 'data-id="2"' in page and "#2" in page


def test_saving_the_enhancements_page_numbers_a_new_row_but_leaves_goals_plain(tmp_path):
    # Only the enhancements list is numbered - that is the one he refers to by id. A new row he adds
    # to it is handed the next number; the other panes stay the plain lists they were.
    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements they want (roadmap, not now)\n- [ ] #1 better voice\n\n"
                       "## Goals\n- swim\n", encoding="utf-8")
    client = _client(profile_path=profile)

    client.post("/profile", json={
        "heading": "Enhancements they want (roadmap, not now)",
        "drawn": ["better voice"],
        "items": [{"id": 1, "done": False, "text": "better voice"},
                  {"id": None, "done": False, "text": "dark mode"}]})
    client.post("/profile", json={"heading": "Goals", "drawn": ["swim"],
                                  "items": [{"id": None, "done": False, "text": "swim, thrice"}]})

    saved = profile.read_text(encoding="utf-8")
    assert "- [ ] #1 better voice" in saved and "- [ ] #2 dark mode" in saved
    assert "- [ ] swim, thrice" in saved and "#" not in profile_sections(saved)["Goals"]


def test_completed_items_sit_in_a_collapsible_done_section_at_the_bottom(tmp_path):
    # "All the completed tasks went to a done section at the bottom that is collapsible." The done
    # ones fold away so the list he still has to act on is what he sees; the fold is a <details>, so
    # it opens on a click with no script of its own.
    import re

    profile = tmp_path / "profile.md"
    profile.write_text("## Project: RTT app\n"
                       "- [ ] #1 still to do\n- [x] #2 finished one\n- [x] #3 also done\n",
                       encoding="utf-8")
    client = _client(profile_path=profile)

    page = client.get("/projects").get_data(as_text=True)
    fold = re.search(r"<details[^>]*class=\"done-fold\".*?</details>", page, re.S)
    assert fold is not None
    body = fold.group(0)
    assert "finished one" in body and "also done" in body   # the done ones are folded away
    assert "still to do" not in body                          # the open one is not
    assert "Done" in body and "2" in body                     # the summary counts them


def test_every_translation_in_force_is_an_editable_row_with_no_labels_and_no_second_copy(tmp_path):
    # One styled list, edited in place: no "built in" tag ("it doesn't matter whether a
    # translation is 'built-in' or not; don't display that"), no plain-text duplicate of his own
    # rules beneath it, and each row carries the stock rule for its words so a save can write
    # exactly what differs from what ships.
    translations = tmp_path / "translations.md"
    translations.write_text("notecraf -> Notecraft\n", encoding="utf-8")
    client = _client(translations_path=translations, scanned_terms=["Git Bash"],
                     lexicon_reader=lambda: ["Notecraft"])

    page = client.get("/config").get_data(as_text=True)
    assert "cloud agent" in page and "Claude agent" in page  # one that ships, shown unbadged
    assert "built in" not in page
    # His own rule appears on its one row only - in the words he sees and the row's memory of
    # them (data-heard) - never again in a plain-text box below.
    assert "notecraf" in page and page.count("notecraf") == 2
    assert 'data-translations' not in page                     # the raw textarea is gone
    assert 'id="add-swap"' in page                             # + makes the next empty row
    # The old Vocabulary card lives here now, as rows whose left side is the coined word for
    # "anything close enough": scanned folder names and his lexicon, one alphabet.
    assert 'id="card-vocabulary"' not in page
    assert page.count("(circasonant)") >= 2
    assert "Git Bash" in page
    # Sorted by the RIGHT side, one alphabet: for a destination word, every rule into it sits
    # together - the (circasonant) Notecraft row lands beside "notecraf -> Notecraft".
    assert page.index("Git Bash") < page.index("notecraf")

    client.post("/translations", data={"body": "notecraf -> Notecraft\nhi deas -> Notecraft"})

    assert "hi deas -> Notecraft" in translations.read_text(encoding="utf-8")


def test_the_config_page_has_a_contents_column_and_one_word_card_titles(tmp_path):
    profile = tmp_path / "profile.md"
    profile.write_text("## Goals\n- swim\n", encoding="utf-8")
    client = _client(profile_path=profile)

    page = client.get("/config").get_data(as_text=True)

    assert 'id="toc"' in page                     # each card is one click away
    assert ">Instructions</h2>" in page           # "Standing Instructions" went one-word
    assert 'id="card-credits"' not in page        # the credits card is gone; the warning speaks
    assert "Fixes for common mishearings of domain terms, sorted by what they fix." in page
    # The immediacy is the mechanism's job, not the copy's: neither the old staleness warning nor
    # the reassurance that replaced it belongs in a subtitle.
    assert "picked up when it next starts" not in page.lower()
    assert "applies immediately" not in page.lower()


def test_the_close_dialog_and_its_wiring_reach_every_page():
    # The X asks in the app's own styling now - the native confirm was a light-mode system box
    # inside a dark app - so the dialog and its script ride on the shared chrome.
    page = _client().get("/").get_data(as_text=True)

    assert 'id="veil"' in page
    assert "closing.js" in page


def test_quit_and_restart_reach_the_window_they_serve_under():
    ways = []
    client = _client(on_quit=lambda: ways.append("quit"), on_restart=lambda: ways.append("restart"))

    client.post("/quit")
    client.post("/restart")

    assert ways == ["quit", "restart"]


def test_the_restart_button_ships_hidden_and_upgrade_says_when_to_show_it():
    # "It should only appear when there are new changes to pick up" - the page asks /upgrade,
    # which is true exactly when the checkout on disk has moved past the running commit.
    ready = [False]
    client = _client(upgrade_ready=lambda: ready[0])

    page = client.get("/").get_data(as_text=True)
    button = page.split('id="restart"')[1].split(">")[0]
    assert "hidden" in button                   # born invisible; /upgrade is what reveals it
    assert "Restart to upgrade" in page
    assert client.get("/upgrade").get_json() == {"ready": False}
    ready[0] = True
    assert client.get("/upgrade").get_json() == {"ready": True}


def test_life_context_renders_as_bullets_and_saves_back_plain(tmp_path):
    # "Context shouldn't be checkboxes; they should be bullets" - background, not work, so no
    # boxes on the page, no open count on the card, and plain bullets back in the file.
    profile = tmp_path / "profile.md"
    profile.write_text("## Life context\n- [ ] lives alone\n\n## Goals\n- [ ] swim\n",
                       encoding="utf-8")
    client = _client(profile_path=profile)

    page = client.get("/config").get_data(as_text=True)
    context = page.split('data-heading="Life context"')[1].split("</section>")[0]
    assert 'type="checkbox"' not in context
    assert "open</span>" not in context.split("</h2>")[0]

    client.post("/profile", json={"heading": "Life context", "drawn": ["lives alone"],
                                  "items": [{"done": False, "text": "lives alone"}]})

    saved = profile.read_text(encoding="utf-8")
    assert "- lives alone" in saved and "- [ ] lives alone" not in saved
    assert "- [ ] swim" in saved  # the checklists beside it keep their boxes


def test_the_memory_card_hides_the_files_heading_and_saves_rows_back(tmp_path):
    # The "# Learned..." line is bookkeeping, not a memory - shown, it read as one.
    learned = tmp_path / "learned.md"
    learned.write_text("# Learned from Douglas\n- prefers metric units\n", encoding="utf-8")
    client = _client(learned_path=learned)

    page = client.get("/config").get_data(as_text=True)
    memory = page.split('id="card-memory"')[1].split("</section>")[0]
    assert "prefers metric units" in memory
    assert "Learned from Douglas" not in memory


def test_no_card_shows_raw_markdown_boxes_are_boxes_and_context_is_dots(tmp_path):
    # "consistent styling of all the tabs (all checkboxes, same font)". A checklist card draws real
    # boxes, not `- [ ]` for the reader to decode; life context draws dots, not raw bullets - the
    # same styling wherever a list is shown.
    profile = tmp_path / "profile.md"
    profile.write_text("## Project: RTT app\n- tuning\n- mapping\n\n"
                       "## Life context\n- new to the city\n", encoding="utf-8")
    client = _client(profile_path=profile)

    projects = client.get("/projects").get_data(as_text=True)
    assert projects.count('<input type="checkbox"') == 2  # the two items as boxes
    assert "- [ ] tuning" not in projects                 # never the raw markdown

    config = client.get("/config").get_data(as_text=True)
    assert '<span class="dot"' in config and "new to the city" in config  # a dot, not a box
    assert '<input type="checkbox"' not in config          # life context is background, not work

    # And a tick on a checklist card still writes markdown back, which is what the brain reads.
    client.post("/profile", json={"heading": "Project: RTT app", "drawn": ["tuning", "mapping"],
                                  "items": [{"id": 1, "done": False, "text": "tuning"},
                                            {"id": 2, "done": True, "text": "mapping"}]})
    assert "- [x] #2 mapping" in profile.read_text(encoding="utf-8")


def test_an_item_is_words_he_can_type_into_and_there_is_no_edit_as_text(tmp_path):
    # "I add new items, tab away, tab back, and they're just gone." The box to edit a section as
    # raw markdown was the only way to add one, and it lost what they typed - so the items
    # themselves are what they type into, and a new one is made by pressing Enter in the list.
    profile = tmp_path / "profile.md"
    profile.write_text("## Project: RTT app\n- tuning\n- mapping\n", encoding="utf-8")

    page = _client(profile_path=profile).get("/projects").get_data(as_text=True)

    # The words of an item are the item - one editable span per row (the two project items here),
    # no raw-markdown box.
    assert page.count('class="item" contenteditable="plaintext-only"') == 2
    assert "Edit as text" not in page


def test_the_instructions_card_is_bullet_rows_that_save_back(tmp_path):
    # "instructions should get the same bullet treatment that all the other cards get. c'mon."
    additions = tmp_path / "persona.md"
    additions.write_text("- never read a commit hash aloud\n", encoding="utf-8")
    client = _client(persona_additions_path=additions)

    page = client.get("/config").get_data(as_text=True)
    card = page.split('id="card-instructions"')[1].split("</section>")[0]
    assert "never read a commit hash aloud" in card
    assert "<textarea" not in card                    # rows, not a box
    assert 'contenteditable="plaintext-only"' in card

    client.post("/persona", data={"body": "- never read a commit hash aloud\n- one line at night"})

    assert "one line at night" in additions.read_text(encoding="utf-8")


def test_memory_and_instructions_declare_where_their_rows_save(tmp_path):
    # Both are whole-file bullet cards, not profile sections, and the in-place list editor wires
    # any section that says where its rows save. Instructions lost exactly this once: its card was
    # turned into bullet rows but nothing told the editor where they go, so Enter made no new bullet
    # and edits never left the page. The marker the editor keys on is rendered here, so a template
    # change can't silently un-wire either card again.
    learned = tmp_path / "learned.md"
    learned.write_text("- prefers metric units\n", encoding="utf-8")
    additions = tmp_path / "persona.md"
    additions.write_text("- never read a commit hash aloud\n", encoding="utf-8")
    client = _client(learned_path=learned, persona_additions_path=additions)

    page = client.get("/config").get_data(as_text=True)
    memory = page.split('id="card-memory"')[1].split("</section>")[0]
    instructions = page.split('id="card-instructions"')[1].split("</section>")[0]
    assert 'data-save="/memory"' in memory
    assert 'data-save="/persona"' in instructions


def test_what_entity_has_learned_is_read_and_written_back(tmp_path):
    learned = tmp_path / "learned.md"
    learned.write_text("- prefers metric units\n", encoding="utf-8")
    client = _client(learned_path=learned)

    assert "prefers metric units" in client.get("/config").get_data(as_text=True)

    client.post("/memory", data={"body": "- prefers metric units\n- hates a wall of text"})

    assert "hates a wall of text" in learned.read_text(encoding="utf-8")


def test_an_agents_exchange_reads_as_a_conversation_with_the_speakers_swapped(tmp_path):
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    (logs / "fixer.log").write_text("[10:00:00] ENTITY> fix the drive link\n"
                                    "[10:00:31] AGENT> Found it - repointed.\n", encoding="utf-8")
    client = _client(agent_logs_dir=logs, clock=lambda: "12:00:00")

    assert 'data-agent="fixer"' in client.get("/agents").get_data(as_text=True)

    shown = client.get("/agents/fixer").get_json()
    # In an agent's thread Excephalon is the one asking and the agent answers - the speakers are
    # swapped, so neither reads as the user talking to themselves.
    assert [(entry["name"], entry["text"]) for entry in shown["entries"]] == [
        ("Excephalon", "fix the drive link"), ("fixer", "Found it - repointed."),
    ]


def test_the_poll_is_the_pump_and_carries_the_mic_and_what_dictation_typed():
    feed = TranscriptFeed()
    mirror = Mirror(feed, clock=lambda: "12:00:00")
    client = _client(mirror.model, mirror=mirror)

    feed.push("message", ("you", "morning"))
    feed.push("state", "recording")
    feed.push("level", 0.03)
    feed.push("draft", "add eggs")
    feed.push("draft", "and milk")

    shown = client.get("/messages").get_json()

    assert [entry["text"] for entry in shown["entries"]] == ["morning"]  # drained by the poll
    assert (shown["state"], shown["level"]) == ("recording", 0.03)
    assert shown["dictated"] == ["add eggs", "and milk"]
    # Taken, not read: handed over twice they would be typed into the box twice.
    assert client.get("/messages?since=1").get_json()["dictated"] == []


def test_the_mic_is_waking_until_the_pump_first_reports():
    # Born "muted", the window enabled its record button on the first poll - seconds before the
    # mic's models had loaded, so clicks died silently and the button read as broken.
    feed = TranscriptFeed()
    mirror = Mirror(feed, clock=lambda: "12:00:00")
    client = _client(mirror.model, mirror=mirror)

    assert client.get("/messages").get_json()["state"] == "waking"

    feed.push("state", "muted")  # the pump's first act on starting: say how the mic stands

    assert client.get("/messages").get_json()["state"] == "muted"


def test_the_poll_carries_the_sentence_he_is_still_in_the_middle_of():
    feed = TranscriptFeed()
    mirror = Mirror(feed, clock=lambda: "12:00:00")
    client = _client(mirror.model, mirror=mirror)

    feed.push("hearing", "Then tell me exactly what")

    # A state, not a hand-off: the line stands on screen until it grows or is taken down, so every
    # poll has to carry it - unlike the draft chunks, which are typed into the box exactly once.
    assert client.get("/messages").get_json()["hearing"] == "Then tell me exactly what"
    assert client.get("/messages").get_json()["hearing"] == "Then tell me exactly what"

    feed.push("hearing", "")

    assert client.get("/messages").get_json()["hearing"] == ""


def test_taking_back_what_he_just_said_reaches_the_box_it_was_typed_into():
    feed = TranscriptFeed()
    mirror = Mirror(feed, clock=lambda: "12:00:00")
    client = _client(mirror.model, mirror=mirror)

    feed.push("draft", "pick up the drive subfolder work")
    client.get("/messages")  # the page has it in the box now, so the box is where it is undone
    feed.push("retract", "")
    feed.push("draft", "pick up the Notecraft work")

    shown = client.get("/messages").get_json()

    assert (shown["retract"], shown["dictated"]) == (1, ["pick up the Notecraft work"])
    assert client.get("/messages").get_json()["retract"] == 0  # taken, not read - undone once


def test_a_chunk_taken_back_before_the_page_saw_it_is_never_typed_at_all():
    # They caught it inside one poll. Undoing it in the box would mean putting it there first, so
    # the page is simply never told about it.
    feed = TranscriptFeed()
    mirror = Mirror(feed, clock=lambda: "12:00:00")
    client = _client(mirror.model, mirror=mirror)

    feed.push("draft", "pick up the drive subfolder work")
    feed.push("retract", "")
    feed.push("draft", "pick up the Notecraft work")

    shown = client.get("/messages").get_json()

    assert (shown["retract"], shown["dictated"]) == (0, ["pick up the Notecraft work"])


def test_dictation_saying_over_sends_the_box_as_it_stands():
    feed = TranscriptFeed()
    mirror = Mirror(feed, clock=lambda: "12:00:00")
    client = _client(mirror.model, mirror=mirror)

    feed.push("submit", "")

    assert client.get("/messages").get_json()["send"] is True
    assert client.get("/messages").get_json()["send"] is False  # and only the once


def test_an_agent_that_is_not_in_the_log_folder_is_not_a_path_to_read(tmp_path):
    logs = tmp_path / "agent-logs"
    logs.mkdir()

    answer = _client(agent_logs_dir=logs, clock=lambda: "12:00:00").get("/agents/..%2Fprofile")

    assert answer.status_code == 404


def test_a_message_naming_a_path_hands_it_over_as_something_to_open():
    # Excephalon names paths and addresses constantly, and reading one off the screen to retype it is
    # exactly what this saves. The rules live in links.py; the page only draws what it is handed.
    named = r"C:\ada\runtime\task.md"
    model = _model(("message", "excephalon", "10:00:00",
                    rf"Filed it at {named}, see https://ex.com/x"))

    parts = _client(model).get("/messages").get_json()["entries"][0]["parts"]

    assert [part["link"] for part in parts if part["link"]] == [named, "https://ex.com/x"]
    # The sentence's own punctuation stays outside the link, and not one word is lost.
    assert "".join(part["text"] for part in parts).strip() == (
        f"Filed it at {named}, see https://ex.com/x")


def test_only_what_was_offered_as_a_link_can_be_opened(tmp_path):
    opened = []
    client = _client(opener=opened.append)

    assert client.post("/open", data={"target": "https://ex.com/x"}).status_code == 204
    # A POST that opens whatever string it is handed is a way to run things by talking to the port.
    assert client.post("/open", data={"target": "not a link at all"}).status_code == 400

    # A real path with a space in it - the case that broke - opens, because the same rule that
    # offered it says it exists; an invented one with a space does not.
    spaced = tmp_path / "Field Notes"
    spaced.mkdir()
    assert client.post("/open", data={"target": str(spaced)}).status_code == 204
    assert client.post("/open", data={"target": str(tmp_path / "Made Up")}).status_code == 400

    assert opened == ["https://ex.com/x", str(spaced)]


def test_the_one_click_yes_and_the_bin_are_both_on_the_page():
    # Saying yes cost four gestures - mic on, the word, mic off, Submit - for about half their turns,
    # and the bin beside it throws a draft away undoably. Both went missing in the port.
    page = _client().get("/").get_data(as_text=True)

    assert 'id="yes"' in page and 'id="bin"' in page


def test_a_filed_enhancement_shows_its_stamp_as_a_link_not_as_text_he_edits(tmp_path):
    # The "filed" tag was dead text sitting in the words he edits; what matters is jumping to where
    # it was filed. So the words he edits lose the stamp, and the stamp becomes a link to that
    # moment in the conversation (the page reads #at= and scrolls there).
    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements\n- [ ] #3 warn about credits (filed 2026-07-28 02:23)\n",
                       encoding="utf-8")

    page = _client(profile_path=profile).get("/projects").get_data(as_text=True)

    # The editable words no longer carry the stamp...
    assert ">warn about credits</span>" in page
    assert "(filed 2026-07-28 02:23)" not in page
    # ...it is a link to that point in the conversation, and the row remembers it for the save.
    assert 'class="filed"' in page
    assert 'href="/#at=2026-07-28%2002%3A23"' in page
    assert 'data-filed="2026-07-28 02:23"' in page
    # The link shows the bare moment, not the word "filed", and says what it does on hover.
    assert '>2026-07-28 02:23</a>' in page
    assert '>filed 2026-07-28 02:23</a>' not in page
    assert 'title="Links back to where this enhancement was identified in the conversation"' in page


def test_the_page_builds_no_right_click_menus_of_its_own():
    # "Can't we just use the built-in Windows menus for all the text on this page? ... I had
    # misspelled 'proprietary' here and Windows had marked it with a jagged red underline, and
    # usually in that case I right-click and accept the spelling autocorrection, but I don't get
    # that option here, but I should." Edge's own menus are on app-wide (desktop.py); two boxes
    # cancelled them to draw bespoke ones, which is what took his spelling suggestions - and gave
    # him a menu on the date heading beside a different menu on the header above it.
    js = _client().get("/static/window.js").get_data(as_text=True)

    assert "popupMenu" not in js and "popmenu" not in js
    assert 'addEventListener("contextmenu"' not in js  # nothing cancels the real menu now
    css = _client().get("/static/app.css").get_data(as_text=True)
    assert ".popmenu" not in css


def test_the_clipboard_is_not_read_by_the_app_any_more():
    # The server read the clipboard only because the bespoke Paste needed it (a page cannot read
    # the clipboard without a permission nobody is there to grant). Edge's own Paste needs no
    # such thing - and the middot that arrived as "ú" was a casualty of that very detour.
    assert _client().get("/clipboard").status_code == 404


def test_the_link_button_copies_a_url_not_the_reference_text():
    # He asked for "an actual URL with an anchor hash", not the readable "Name · …" text - built
    # from this instance's own origin and the message's bare moment.
    js = _client().get("/static/window.js").get_data(as_text=True)

    assert "location.origin" in js
    assert "encodeURIComponent(entry.moment)" in js
    assert "#at=" in js


def test_closing_an_agent_archives_its_log_so_it_stays_closed(tmp_path):
    # The roster IS the log folder, so a log left in place comes straight back on the next poll.
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    (logs / "fixer.log").write_text("[10:00:00] ENTITY> fix it\n", encoding="utf-8")
    client = _client(agent_logs_dir=logs, clock=lambda: "12:00:00")

    assert client.post("/agents/fixer/close").status_code == 204

    assert not (logs / "fixer.log").exists()
    assert (tmp_path / "agent-logs-archive" / "fixer.log").exists()
    page = client.get("/agents").get_data(as_text=True)
    assert '<div class="agent thread" data-agent="fixer">' not in page  # the live tab is gone
    assert 'data-restore="fixer"' in page  # but its name moved to the rail's Archived list
    assert client.post("/agents/fixer/close").status_code == 404  # and it is not a path to touch


def test_the_rail_lists_every_log_and_restoring_an_archived_one_makes_it_a_tab_again(tmp_path):
    # His design, replacing an unfold-inside-the-archive fold he rejected outright: one rail
    # like the Config page's, active and archived logs each one click away - and clicking an
    # archived name UNARCHIVES it, so the exchange comes back as an ordinary tab in the main
    # view rather than being read in some second place.
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    (logs / "fixer.log").write_text("[10:00:00] ENTITY> fix it\n", encoding="utf-8")
    archive = tmp_path / "agent-logs-archive"
    archive.mkdir()
    (archive / "settler.log").write_text("[10:00:00] ENTITY> settle the merge\n"
                                         "[10:00:31] AGENT> Merged.\n", encoding="utf-8")
    client = _client(agent_logs_dir=logs, clock=lambda: "12:00:00")

    page = client.get("/agents").get_data(as_text=True)
    assert 'data-goes="agent-fixer"' in page       # the active log, a scroll away
    assert 'id="agent-fixer"' in page              # and the tab the rail scrolls to
    assert 'data-restore="settler"' in page        # the archived log, a restore away

    assert client.post("/agents/archived/settler/restore").status_code == 204

    assert (logs / "settler.log").exists()         # back in the live folder: it IS a tab again
    assert not (archive / "settler.log").exists()
    shown = client.get("/agents/settler").get_json()
    assert [(entry["name"], entry["text"]) for entry in shown["entries"]] == [
        ("Excephalon", "settle the merge"), ("settler", "Merged."),
    ]
    # Only names straight out of the archive folder are paths to touch.
    assert client.post("/agents/archived/elsewhere/restore").status_code == 404


def test_the_win_enter_chord_reaches_the_page_as_one_send():
    # The chord cannot reach any window on this machine, so it arrives by keyboard hook and
    # crosses the feed. Every link of that chain but the hook itself is checked here, because the
    # port moved the far end of it from a Tk binding to a page poll.
    from excephalon.chord import ENTER, LWIN, SubmitChord

    feed = TranscriptFeed()
    mirror = Mirror(feed, clock=lambda: "12:00:00")
    client = _client(mirror.model, mirror=mirror)
    chord = SubmitChord(submit=lambda: feed.push("submit", ""), focused=lambda: True)

    chord.key(LWIN, released=False)
    chord.key(ENTER, released=False)

    assert client.get("/messages").get_json()["send"] is True
    assert client.get("/messages").get_json()["send"] is False  # and the box is sent once


def test_saving_the_enhancements_hands_back_each_rows_number():
    # "when I'm inputting new tickets here the ID doesn't appear at first" - the page needs the
    # number the save assigned, so a fresh row shows its id the moment it first saves.
    import tempfile
    from pathlib import Path

    profile = Path(tempfile.mkdtemp()) / "profile.md"
    profile.write_text("# P\n\n## Enhancements he wants for you (roadmap, not now)\n- [ ] #7 old one\n",
                       encoding="utf-8")
    client = _client(profile_path=profile)

    answer = client.post("/profile", json={
        "heading": "Enhancements he wants for you (roadmap, not now)",
        "items": [{"id": 7, "done": False, "text": "old one"},
                  {"id": None, "done": False, "text": "a brand new ask"}],
        "drawn": ["old one"],
    }).get_json()

    assert answer == {"ids": [7, 8]}
    assert "- [ ] #8 a brand new ask" in profile.read_text(encoding="utf-8")


def test_the_conversation_is_replayed_from_what_was_said_not_parsed_back_out_of_prose(tmp_path):
    # "How fucking complicated can it be? It's just a fucking transcript!" It was complicated
    # because the window read messages back OUT of the prose log: which prefix, whose line, is
    # this bare line a continuation or something the app spoke aloud. Two rules, two rewrites of
    # his history in front of him. The record now holds the role each message was given as it was
    # said, so a reload is a replay - his long submission stays one bubble and a line the app
    # spoke stays Excephalon's, because nothing is being decided.
    from datetime import datetime

    from excephalon.transcript import MessageLog, past_messages

    kept = MessageLog(tmp_path / "session-20260729-021500.jsonl",
                      clock=lambda: datetime(2026, 7, 29, 2, 15, 0))
    kept.keep("you", "The demo is good to ship.\n\nI have lots of feedback on the other one:")
    kept.keep("excephalon", "Landing it.")
    kept.keep("excephalon", "I've got an update on the copy fixes when you're ready.")
    kept.keep("status", "(thinking\u2026)")

    model = TranscriptModel(clock=lambda: "12:00:00")
    for op, payload in past_messages(tmp_path):
        model.apply(op, payload)

    assert [entry["role"] for entry in model.entries] == ["day", "you", "excephalon", "excephalon",
                                                          "status"]
    assert model.entries[1]["text"].count("\n") == 2  # his paragraphs, whole
    assert model.entries[3]["text"].startswith("I've got an update")  # spoken, and his to see


def test_an_old_log_is_converted_once_and_read_as_messages_after(tmp_path):
    # Every session recorded before the message log is all there is for those days, so they are
    # converted rather than lost - and written down, so the guesswork runs once and never again.
    # A bare line of its own is something the app SPOKE without printing (an update offer), which
    # is Excephalon talking; only its own asides, which open with "(" or "[", are asides.
    from excephalon.transcript import past_messages

    (tmp_path / "session-20260718-024138.log").write_text(
        "===== 2026-07-18 =====\n"
        "[02:41:38] you said: morning\n"
        "[02:41:38] and one more thing\n"
        "[02:42:10] entity> Morning.\n"
        "[02:44:02] I've got an update on the copy fixes when you're ready.\n"
        "[02:44:10] (thinking\u2026)\n", encoding="utf-8")

    ops = past_messages(tmp_path)

    assert (tmp_path / "session-20260718-024138.jsonl").exists()  # converted, once
    said = [payload for _, payload in ops if payload[0] == "message"]
    assert [message[1] for message in said] == ["you", "excephalon", "excephalon", "status"]
    assert said[0][3] == "morning\nand one more thing"   # one submission, not two lines
    assert said[2][3].startswith("I've got an update")     # spoken aloud: Excephalon's own
    assert said[3][3] == "(thinking\u2026)"                # an aside stays an aside

    again = past_messages(tmp_path)  # and the second read comes off the record, unchanged
    assert [payload for _, payload in again] == [payload for _, payload in ops]


def test_the_archive_lists_the_newest_first_and_says_when(tmp_path):
    # "Archived agent logs should be sorted by date, not alphabetically, jesus... and show the
    # timestamp for them too." An archive is a history; alphabetical it read as a filing cabinet.
    import os

    logs = tmp_path / "agent-logs"
    logs.mkdir()
    archive = tmp_path / "agent-logs-archive"
    archive.mkdir()
    for name, when in (("alpha", 1_760_000_000), ("zulu", 1_770_000_000), ("mike", 1_750_000_000)):
        log = archive / f"{name}.log"
        log.write_text("[10:00:00] ENTITY> done" + chr(10), encoding="utf-8")
        os.utime(log, (when, when))
    client = _client(agent_logs_dir=logs, clock=lambda: "12:00:00")

    page = client.get("/agents").get_data(as_text=True)

    order = [page.index(f'data-restore="{name}"') for name in ("zulu", "alpha", "mike")]
    assert order == sorted(order)  # newest first, not a-z
    assert page.count('class="when"') == 3  # and each says when it last spoke


def test_a_tab_name_can_be_typed_in_and_the_desk_is_asked_to_move_it(tmp_path):
    # The name on the tab is his to change; the desk owns the move (its key, its log, its record),
    # so the page hands the ask over rather than renaming files behind it.
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    (logs / "fixer.log").write_text("[10:00:00] ENTITY> fix it" + chr(10), encoding="utf-8")
    asked = []
    client = _client(agent_logs_dir=logs, clock=lambda: "12:00:00",
                     on_rename=lambda name, to: asked.append((name, to)) or "the-fix")

    page = client.get("/agents").get_data(as_text=True)
    assert 'class="rename" contenteditable="plaintext-only" data-agent="fixer"' in page

    answer = client.post("/agents/fixer/rename", data={"to": "the fix"})

    assert asked == [("fixer", "the fix")]
    assert answer.get_json() == {"name": "the-fix"}
    assert client.post("/agents/nobody/rename", data={"to": "x"}).status_code == 404


def test_an_archived_exchange_can_be_renamed_too(tmp_path):
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    archive = tmp_path / "agent-logs-archive"
    archive.mkdir()
    (archive / "old-name.log").write_text("[10:00:00] ENTITY> done" + chr(10), encoding="utf-8")
    client = _client(agent_logs_dir=logs, clock=lambda: "12:00:00")

    answer = client.post("/agents/archived/old-name/rename", data={"to": "the early one"})

    assert answer.get_json() == {"name": "the-early-one"}
    assert (archive / "the-early-one.log").exists()
    assert not (archive / "old-name.log").exists()


def test_the_agents_rail_puts_the_dates_in_one_column_and_the_names_in_another(tmp_path):
    # "Could we have all the dates aligned vertically and then the names aligned vertically."
    # Every date is the same length, so a fixed first column lines up both.
    import os

    logs = tmp_path / "agent-logs"
    logs.mkdir()
    (logs / "live-one.log").write_text("[10:00:00] ENTITY> go" + chr(10), encoding="utf-8")
    archive = tmp_path / "agent-logs-archive"
    archive.mkdir()
    log = archive / "older.log"
    log.write_text("[09:00:00] ENTITY> done" + chr(10), encoding="utf-8")
    os.utime(log, (1_760_000_000, 1_760_000_000))
    client = _client(agent_logs_dir=logs, clock=lambda: "12:00:00")

    page = client.get("/agents").get_data(as_text=True)
    assert 'class="when"' in page and 'class="who"' in page
    assert "page agents" in page  # its own shape, so only this rail is the wide one
    # The row is not the control: one button on the left does the one thing to the row.
    assert 'data-archive="live-one"' in page and 'data-restore="older"' in page
    assert ">2025-10-09<" in page  # the day, not the minute

    css = client.get("/static/app.css").get_data(as_text=True)
    rail = _rule_for(css, "#toc .rail-row")
    assert "grid-template-columns" in rail  # the action, then the dates, then the names
    elided = _rule_for(css, "#toc .rail-row .who, #toc .rail-row .rail-name")
    assert "text-overflow: ellipsis" in elided and "nowrap" in elided  # cut, never wrapped


def test_a_tab_whose_agent_the_desk_no_longer_holds_can_still_be_renamed(tmp_path):
    # "I can edit the name, but the changes don't persist; they simply get silently rejected."
    # The desk rightly refuses a name it has never heard of - but the window draws a tab per LOG,
    # so a log outliving its agent is still a tab he is looking at, and its name is still his.
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    (logs / "leftover.log").write_text("[10:00:00] ENTITY> did a thing" + chr(10), encoding="utf-8")
    client = _client(agent_logs_dir=logs, clock=lambda: "12:00:00",
                     on_rename=lambda name, to: "")  # the desk does not hold it

    answer = client.post("/agents/leftover/rename", data={"to": "the early one"})

    assert answer.get_json() == {"name": "the-early-one"}
    assert (logs / "the-early-one.log").exists()
    assert not (logs / "leftover.log").exists()
    assert 'data-goes="agent-the-early-one"' in client.get("/agents").get_data(as_text=True)


def test_a_rename_onto_a_name_already_in_use_is_still_refused(tmp_path):
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    for name in ("one", "two"):
        (logs / f"{name}.log").write_text("[10:00:00] ENTITY> go" + chr(10), encoding="utf-8")
    client = _client(agent_logs_dir=logs, clock=lambda: "12:00:00", on_rename=lambda name, to: "")

    assert client.post("/agents/one/rename", data={"to": "two"}).status_code == 409
    assert (logs / "one.log").exists()  # nothing moved


def test_changing_only_the_capitals_of_a_name_is_a_rename_windows_can_make(tmp_path):
    # "The changes are back to failing to persist, now from both locations." Windows holds one file
    # under one name in ANY case, so `inbox-AUTO-play-toggle` -> `inbox-auto-play-toggle` looked
    # like a collision with itself and was refused - and fixing the capitals an all-caps heading
    # had led him to type is exactly the rename he had reason to make.
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    (logs / "inbox-AUTO-play-toggle.log").write_text("[10:00:00] ENTITY> go" + chr(10),
                                                    encoding="utf-8")
    client = _client(agent_logs_dir=logs, clock=lambda: "12:00:00", on_rename=lambda name, to: "")

    answer = client.post("/agents/inbox-AUTO-play-toggle/rename",
                         data={"to": "inbox-auto-play-toggle"})

    assert answer.get_json() == {"name": "inbox-auto-play-toggle"}
    assert [log.name for log in logs.iterdir()] == ["inbox-auto-play-toggle.log"]


def test_a_refused_rename_says_why_rather_than_putting_the_old_name_back_in_silence(tmp_path):
    # A rename that quietly reverts is indistinguishable from a broken app - "they simply get
    # silently rejected" - so every refusal comes back as a sentence the window can show him.
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    for name in ("one", "two"):
        (logs / f"{name}.log").write_text("[10:00:00] ENTITY> go" + chr(10), encoding="utf-8")
    archive = tmp_path / "agent-logs-archive"
    archive.mkdir()
    (archive / "old.log").write_text("[09:00:00] ENTITY> done" + chr(10), encoding="utf-8")
    client = _client(agent_logs_dir=logs, clock=lambda: "12:00:00", on_rename=lambda name, to: "")

    taken = client.post("/agents/one/rename", data={"to": "two"})
    same = client.post("/agents/one/rename", data={"to": "one"})
    nothing = client.post("/agents/one/rename", data={"to": "!!!"})
    archived = client.post("/agents/archived/old/rename", data={"to": "!!!"})

    assert taken.status_code == 409 and "already called that" in taken.get_json()["why"]
    assert same.status_code == 409 and "name it already has" in same.get_json()["why"]
    assert nothing.status_code == 409 and "nothing a file can be named" in nothing.get_json()["why"]
    assert archived.status_code == 409 and archived.get_json()["why"]

    script = client.get("/static/agents.js").get_data(as_text=True)
    assert "complain(" in script and "refused" in script  # and the window puts it on screen


def test_an_instruction_opens_with_its_name_in_bold(tmp_path):
    # "Modify each Instruction so that it begins with a bolded name - 3 words tops." The name is
    # markdown in the file and bold on the page: he should not have to read past asterisks.
    persona = tmp_path / "persona.md"
    persona.write_text("- **No internal jargon** Never use system words when speaking to him." + chr(10)
                       + "- An older instruction with no name at all." + chr(10), encoding="utf-8")
    client = _client(persona_additions_path=persona)

    page = client.get("/config").get_data(as_text=True)

    assert '<strong class="lede" contenteditable="plaintext-only">No internal jargon</strong>' in page
    assert "Never use system words when speaking to him." in page
    assert "**" not in page  # the asterisks are the file's business, not his to read
    assert "An older instruction with no name at all." in page  # unnamed lines are unchanged

    css = client.get("/static/app.css").get_data(as_text=True)
    assert "font-weight: 600" in _rule_for(css, ".checklist li .lede")
    # "Give the bolded name part of instructions a fixed width and wrap long ones": a column of its
    # own, so every rule begins at the same place and a long name wraps inside the column.
    assert '<li class="named">' in page
    column = _rule_for(css, ".checklist li.named")
    assert "grid-template-columns: 12px 12rem 1fr" in column
    assert "break-word" in _rule_for(css, ".checklist li.named .lede")
    script = client.get("/static/writing.js").get_data(as_text=True)
    assert "`**${lede.textContent.trim()}** ${words.trim()}`" in script  # and saving puts them back


def test_ctrl_f_reaches_every_page_not_just_the_lists():
    # "We have a Ctrl+F search feature on the Config tab, but can we get that on both the
    # Conversation and Agents tabs too?" It lived inside the Config page's own script, which left
    # the two pages with the most to read through unsearchable.
    client = _client()

    for path in ("/", "/config", "/agents"):
        assert "finder.js" in client.get(path).get_data(as_text=True)

    finder = client.get("/static/finder.js").get_data(as_text=True)
    for row in ("#thread .said", ".agent.thread .said", ".checklist li", "#toc .rail-row"):
        assert row in finder  # what counts as a row on each of the three pages
    assert "ctrlKey" in finder and "metaKey" in finder


def test_the_agents_rail_dates_every_row_by_when_its_agent_started(tmp_path):
    # "The active agents lack a date but they should have one too. the date should be when the
    # agent was started, not when it finished" - which is the first line the desk ever wrote to
    # that log, and every log opens with its own date header.
    import os

    logs = tmp_path / "agent-logs"
    logs.mkdir()
    live = logs / "still-going.log"
    live.write_text("===== 2026-07-21 =====" + chr(10) + "[09:00:00] ENTITY> go" + chr(10),
                    encoding="utf-8")
    os.utime(live, (1_769_000_000, 1_769_000_000))  # finished much later: the start is what shows
    archive = tmp_path / "agent-logs-archive"
    archive.mkdir()
    (archive / "older.log").write_text("===== 2026-07-19 =====" + chr(10)
                                       + "[09:00:00] ENTITY> done" + chr(10), encoding="utf-8")
    client = _client(agent_logs_dir=logs, clock=lambda: "12:00:00")

    page = client.get("/agents").get_data(as_text=True)

    assert ">2026-07-21<" in page  # the live row is dated by its start, not its last write
    assert ">2026-07-19<" in page


def test_the_archive_icons_are_actually_in_the_page(tmp_path):
    # "Whatever you added here is invisible." The two symbols sat outside every Jinja block, so
    # they were discarded and both buttons drew nothing at all.
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    (logs / "fixer.log").write_text("[10:00:00] ENTITY> go" + chr(10), encoding="utf-8")
    archive = tmp_path / "agent-logs-archive"
    archive.mkdir()
    (archive / "older.log").write_text("[09:00:00] ENTITY> done" + chr(10), encoding="utf-8")

    page = _client(agent_logs_dir=logs, clock=lambda: "12:00:00").get("/agents").get_data(as_text=True)

    assert 'id="archive-icon"' in page and 'id="unarchive-icon"' in page
    assert page.index('id="archive-icon"') > page.index('data-archive="fixer"')  # the sprite ships


def test_a_tab_name_keeps_the_case_he_typed():
    # He typed "OTO" and the heading's own uppercase made it indistinguishable from "oto", so he
    # could not tell which part of the name had actually shouted.
    css = _client().get("/static/app.css").get_data(as_text=True)

    assert "text-transform: none" in _rule_for(css, ".section h2 .rename")


def test_a_name_can_be_renamed_from_the_rail_as_well_as_its_card(tmp_path):
    # "I should be able to edit the agent names from the sidebar too." An archived log has no card
    # to edit its name on, so the rail is the only door for those.
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    (logs / "live-one.log").write_text("[10:00:00] ENTITY> go" + chr(10), encoding="utf-8")
    archive = tmp_path / "agent-logs-archive"
    archive.mkdir()
    (archive / "older.log").write_text("[09:00:00] ENTITY> done" + chr(10), encoding="utf-8")
    client = _client(agent_logs_dir=logs, clock=lambda: "12:00:00")

    page = client.get("/agents").get_data(as_text=True)
    assert 'data-rename="live-one"' in page and 'data-rename="older"' in page
    assert "double-click to rename" in page
    # The live row keeps its go-to-the-exchange click alongside the rename.
    assert 'data-goes="agent-live-one" data-rename="live-one"' in page

    js = client.get("/static/agents.js").get_data(as_text=True)
    assert "dblclick" in js and "/agents/archived/" in js


def test_the_rail_aligns_a_date_and_its_name_on_one_baseline():
    # "The names are so much lower than their corresponding dates that they almost look 1/3 of the
    # day offset from them" - two different font sizes, centred boxes, so the words sat apart.
    css = _client().get("/static/app.css").get_data(as_text=True)

    assert "align-items: baseline" in _rule_for(css, "#toc .rail-row")


def test_an_agent_cards_title_is_bigger_and_stands_off_its_exchange():
    css = _client().get("/static/app.css").get_data(as_text=True)

    heading = _rule_for(css, "body.agents .section h2")
    assert "font-size: 1rem" in heading and "margin-bottom: 14px" in heading


def test_saving_a_project_card_numbers_its_rows_the_way_the_excephalon_list_does(tmp_path):
    # A project card is the same checklist the Excephalon list is, so it carries stable ids he can
    # refer one of its items to by number - a new row it gains is handed the next number on save.
    profile = tmp_path / "profile.md"
    profile.write_text("## Project: RTT app\n- [ ] #1 tuning table\n", encoding="utf-8")
    client = _client(profile_path=profile)

    client.post("/profile", json={
        "heading": "Project: RTT app",
        "drawn": ["tuning table"],
        "items": [{"id": 1, "done": False, "text": "tuning table"},
                  {"id": None, "done": False, "text": "keyboard mapping"}]})

    saved = profile.read_text(encoding="utf-8")
    assert "- [ ] #1 tuning table" in saved
    assert "- [ ] #2 keyboard mapping" in saved  # the next number, like the Excephalon list


def test_the_projects_page_shows_a_card_per_project_named_without_its_tag(tmp_path):
    # #128: "the Projects section should become its own tab, with a card for each project (RTT app,
    # Highdeas, etc.)". Each "## Project: <name>" section is a card, titled by the name alone - the
    # "Project: " tag is the app's bookkeeping and never reaches his eyes.
    profile = tmp_path / "profile.md"
    profile.write_text("## Project: RTT app\n- [ ] #1 tuning table\n\n"
                       "## Project: Highdeas\n- [ ] #1 group merge\n", encoding="utf-8")
    client = _client(profile_path=profile)

    page = client.get("/projects").get_data(as_text=True)
    assert ">RTT app" in page and ">Highdeas" in page
    assert ">Project:" not in page  # the tag never shows as visible text, only the name does
    assert "tuning table" in page and "group merge" in page
    # Each card is the same in-place checklist the Excephalon list is, saved back by its heading.
    assert 'data-heading="Project: RTT app"' in page


def test_the_projects_page_carries_excephalon_as_the_enhancements_list(tmp_path):
    # "...and this Enhancements section just becomes the Project card for Excephalon itself." The
    # roadmap for the companion is Excephalon's own project card - drawn from the Enhancements
    # section, so the brain still files and ticks it exactly where it always did.
    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements he wants for you (roadmap, not now)\n"
                       "- [ ] #2 better voice\n", encoding="utf-8")
    client = _client(profile_path=profile)

    page = client.get("/projects").get_data(as_text=True)
    assert ">Excephalon" in page
    assert "better voice" in page and "#2" in page
    assert 'data-heading="Enhancements he wants for you (roadmap, not now)"' in page


def test_config_no_longer_carries_the_enhancements_card_now_that_projects_holds_it(tmp_path):
    # #128 moves the Enhancements roadmap out of Config and onto the Projects tab as the Excephalon
    # card. Config keeps his life-context and the rest; the roadmap is a project now.
    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements he wants for you (roadmap, not now)\n- [ ] #2 better voice\n\n"
                       "## Life context\n- lives in SF\n", encoding="utf-8")
    client = _client(profile_path=profile)

    config = client.get("/config").get_data(as_text=True)
    assert "better voice" not in config  # the roadmap is not here any more
    assert "lives in SF" in config       # but the rest of Config is untouched

    projects = client.get("/projects").get_data(as_text=True)
    assert "better voice" in projects and ">Excephalon" in projects




def test_the_projects_content_clears_the_fixed_rail():
    # The rail is position:fixed, so the cards need a left margin or they slide under it - the same
    # offset Config uses. Without it the first project card sits beneath the contents column.
    css = _client().get("/static/app.css").get_data(as_text=True)

    content = _rule_for(css, "body.config #content, body.projects #content")
    assert "margin-left: 180px" in content


def test_config_drops_the_long_term_projects_list_entirely(tmp_path):
    # His call: the "Projects (long-term)" list leaves Config for good - each of its items becomes
    # its own card on the Projects tab instead of one shared list card. Config keeps the rest.
    profile = tmp_path / "profile.md"
    profile.write_text("## Projects (long-term)\n- [ ] Gym & PT\n- [ ] Guitar\n\n"
                       "## Life context\n- lives in SF\n", encoding="utf-8")
    client = _client(profile_path=profile)

    config = client.get("/config").get_data(as_text=True)
    assert "Gym & PT" not in config and "Guitar" not in config  # the list is gone from Config
    assert "lives in SF" in config                              # but life context stays


def test_new_project_starts_a_placeholder_card_in_edit_mode(tmp_path):
    # The + button makes a new card in edit mode (no separate name field): a placeholder project is
    # created and the tab comes back with that card named for typing over.
    profile = tmp_path / "profile.md"
    profile.write_text("## Life context\n- SF\n", encoding="utf-8")
    client = _client(profile_path=profile)

    answer = client.post("/project/new")
    assert answer.status_code == 302
    assert "/projects?editing=" in answer.headers["Location"]
    assert "## Project: New project" in profile.read_text(encoding="utf-8")


def test_new_project_does_not_clobber_an_existing_placeholder(tmp_path):
    profile = tmp_path / "profile.md"
    profile.write_text("## Project: New project\n- [ ] keep me\n", encoding="utf-8")
    client = _client(profile_path=profile)

    client.post("/project/new")
    text = profile.read_text(encoding="utf-8")
    assert "## Project: New project 2" in text  # a fresh card, the first left intact
    assert "- [ ] keep me" in text


def test_renaming_a_project_moves_its_card(tmp_path):
    profile = tmp_path / "profile.md"
    profile.write_text("## Project: RTT app\n- [ ] #1 tuning\n", encoding="utf-8")
    client = _client(profile_path=profile)

    answer = client.post("/project/rename", data={"from": "RTT app", "to": "Rich tone tool"})
    assert answer.status_code == 204
    text = profile.read_text(encoding="utf-8")
    assert "## Project: Rich tone tool" in text and "## Project: RTT app" not in text
    assert "- [ ] #1 tuning" in text


def test_renaming_a_project_to_a_taken_name_is_refused_with_a_reason(tmp_path):
    profile = tmp_path / "profile.md"
    profile.write_text("## Project: A\n- [ ] x\n\n## Project: B\n- [ ] y\n", encoding="utf-8")
    client = _client(profile_path=profile)

    answer = client.post("/project/rename", data={"from": "A", "to": "B"})
    assert answer.status_code == 409
    assert "why" in answer.get_json()
    assert profile.read_text(encoding="utf-8").count("## Project:") == 2  # nothing merged


def test_reordering_projects_rewrites_their_order(tmp_path):
    from excephalon.memory import project_headings

    profile = tmp_path / "profile.md"
    profile.write_text("## Project: A\n- [ ] a\n\n## Project: B\n- [ ] b\n\n## Project: C\n- [ ] c\n",
                       encoding="utf-8")
    client = _client(profile_path=profile)

    answer = client.post("/project/reorder", json={"order": ["C", "A", "B"]})
    assert answer.status_code == 204
    assert project_headings(profile.read_text(encoding="utf-8")) == ["Project: C", "Project: A",
                                                                     "Project: B"]


def test_the_projects_rail_is_renameable_draggable_and_has_no_name_field(tmp_path):
    # Rail names rename in place like an agent's; project rows drag to reorder; and the + no longer
    # has a text field beside it - it starts a card in edit mode instead.
    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements he wants (roadmap)\n- [ ] #1 voice\n\n"
                       "## Project: RTT app\n- [ ] #1 tuning\n", encoding="utf-8")
    client = _client(profile_path=profile)

    page = client.get("/projects").get_data(as_text=True)
    # A project row: draggable and renameable, carrying its name for both.
    assert 'draggable="true"' in page
    assert 'data-rename="RTT app"' in page and 'data-name="RTT app"' in page
    # Excephalon is not a project section, so it neither drags nor renames.
    assert 'data-rename="Excephalon"' not in page
    # The + is a button that posts to /project/new; the old name input is gone.
    assert 'action="/project/new"' in page and 'id="add-project"' in page
    assert 'name="name"' not in page and 'placeholder="New project' not in page
    # projects.js drives the rail (rename, reorder, edit-on-new).
    assert 'src="/static/projects.js"' in page


def test_projects_js_wires_rename_reorder_and_edit_on_new():
    js = _client().get("/static/projects.js").get_data(as_text=True)
    assert "/project/rename" in js and "/project/reorder" in js
    assert "dragstart" in js and "editing" in js  # drag to reorder; focus the freshly-made card


def test_the_excephalon_card_carries_no_subtitle_like_the_project_cards(tmp_path):
    # "Remove any special styling from the Excephalon card - it should look the same as the other
    # project cards." Its subtitle note was the only difference; without it, every card is the same.
    import re

    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements he wants (roadmap)\n- [ ] #1 voice\n\n"
                       "## Project: RTT app\n- [ ] #1 tuning\n", encoding="utf-8")
    page = _client(profile_path=profile).get("/projects").get_data(as_text=True)

    excephalon = re.search(r'id="card-excephalon".*?</section>', page, re.S).group(0)
    project = re.search(r'id="card-rtt-app".*?</section>', page, re.S).group(0)
    assert 'class="note"' not in excephalon  # no subtitle, exactly like a project card
    assert 'class="note"' not in project


def test_a_long_project_name_truncates_in_the_sidebar():
    # Long names must fit the rail, not wrap or overflow it. The name is a flex item, so it needs
    # min-width:0 or it never shrinks below its own text to let the ellipsis show.
    css = _client().get("/static/app.css").get_data(as_text=True)

    rule = _rule_for(css, "#toc .rail-name")
    assert "text-overflow: ellipsis" in rule and "white-space: nowrap" in rule
    # The whole idiom, or a name at the rail's edge wraps/overflows rather than ellipsizing:
    # flex-basis 0 so the row sizes it at zero when deciding if it fits, min-width:0 so it can then
    # shrink below its own text.
    assert "flex: 1 1 0" in rule and "min-width: 0" in rule


def test_a_task_with_an_agent_on_it_shows_an_indicator_linking_to_its_log(tmp_path):
    # "When an agent is assigned to a task, show an indicator to the left of the checkbox... clicking
    # the indicator should link to that agent's log in the Agents tab." The tie is the agent's
    # recorded enhancement, matched to the item whose words carry it.
    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements\n- [ ] #3 warn about credits\n- [ ] #4 live captions\n",
                       encoding="utf-8")
    state = tmp_path / "agents.json"
    state.write_text('[{"name": "credits-warn", "enhancement": "warn about credits"}]',
                     encoding="utf-8")
    client = _client(profile_path=profile, agent_state_path=state)

    page = client.get("/projects").get_data(as_text=True)
    row = page.split('id="task-excephalon-3"')[1].split("</li>")[0]
    assert 'href="/agents#agent-credits-warn"' in row       # one click to that agent's log
    assert 'class="agent-link"' in row
    assert 'id="task-excephalon-4"' not in page             # only the assigned task gets an anchor


def test_every_checklist_row_reserves_the_indicator_gutter(tmp_path):
    # "Shift all checkboxes over to make room for this indicator." The gutter is reserved on every
    # row, agent or not, so the boxes stay in one column instead of jumping when an agent appears.
    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements\n- [ ] #3 warn about credits\n- [ ] #4 live captions\n",
                       encoding="utf-8")
    state = tmp_path / "agents.json"
    state.write_text('[{"name": "credits-warn", "enhancement": "warn about credits"}]',
                     encoding="utf-8")
    client = _client(profile_path=profile, agent_state_path=state)

    page = client.get("/projects").get_data(as_text=True)
    # #3 has the link; #4, with no agent, holds an empty slot of the same width in its place.
    assert page.count('class="agent-link"') == 1
    assert page.count('class="agent-slot"') == 1
    css = client.get("/static/app.css").get_data(as_text=True)
    gutter = _rule_for(css, ".checklist .agent-slot, .checklist .agent-link")
    assert "width" in gutter and "flex: none" in gutter    # a fixed column the boxes clear


def test_an_agents_log_links_back_to_the_task_it_is_working_on(tmp_path):
    # "Add a link back from each agent's log to the task it worked on in the Projects tab, so
    # navigation is seamless in both directions." Same tie, read the other way.
    profile = tmp_path / "profile.md"
    profile.write_text("## Enhancements\n- [ ] #3 warn about credits\n", encoding="utf-8")
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    (logs / "credits-warn.log").write_text("[10:00:00] ENTITY> warn about credits" + chr(10),
                                            encoding="utf-8")
    state = tmp_path / "agents.json"
    state.write_text('[{"name": "credits-warn", "enhancement": "warn about credits"}]',
                     encoding="utf-8")
    client = _client(profile_path=profile, agent_logs_dir=logs, agent_state_path=state,
                     clock=lambda: "12:00:00")

    page = client.get("/agents").get_data(as_text=True)
    section = page.split('id="agent-credits-warn"')[1].split("</section>")[0]
    assert 'href="/projects#task-excephalon-3"' in section   # straight to the exact task
    assert "warn about credits" in section                    # and it names which task


def test_an_agent_on_no_task_shows_no_back_link(tmp_path):
    # Most agents carry no enhancement; their tabs stay exactly as they were.
    logs = tmp_path / "agent-logs"
    logs.mkdir()
    (logs / "loose.log").write_text("[10:00:00] ENTITY> poke around" + chr(10), encoding="utf-8")
    client = _client(agent_logs_dir=logs, clock=lambda: "12:00:00")

    page = client.get("/agents").get_data(as_text=True)
    assert 'class="on-task"' not in page


def test_both_tabs_flash_the_row_a_cross_link_lands_on():
    # Landing at the top edge of a card or a tab is indistinguishable from not having moved, so the
    # destination flashes - the same "you landed here" highlight (.landed) the conversation uses.
    projects_js = _client().get("/static/projects.js").get_data(as_text=True)
    agents_js = _client().get("/static/agents.js").get_data(as_text=True)
    for js in (projects_js, agents_js):
        assert "location.hash" in js and "landed" in js

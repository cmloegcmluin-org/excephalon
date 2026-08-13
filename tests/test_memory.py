from pathlib import Path

from excephalon.memory import (
    append_learned,
    compose_persona,
    lexicon_path,
    lexicon_terms,
    load_learned,
    load_lexicon,
    load_profile,
    parse_facts,
    save_translations,
    translation_pairs,
    user_name,
)


def test_lexicon_terms_takes_the_head_of_each_line_ignoring_glosses_and_comments():
    text = (
        "# lexicon\n"
        "\n"
        "Notecraft — the audio-memo app\n"
        "WaveShaper\n"
        "- Skylark — a project of theirs\n"
    )
    assert lexicon_terms(text) == ["Notecraft", "WaveShaper", "Skylark"]


def test_lexicon_terms_is_empty_for_blank_or_comment_only_text():
    assert lexicon_terms("") == []
    assert lexicon_terms("# just a header\n\n") == []


def test_translations_are_read_as_the_arrow_he_writes_them_with():
    text = (
        "# what it keeps mishearing\n"
        "\n"
        "cloud agent -> Claude agent\n"
        "- notecraf -> Notecraft\n"
        "work tree → worktree\n"
        "not a translation\n"
    )
    # Lowercased on the left, because that is the side it is looked up by; a line with no arrow on
    # it is not one, and is left out rather than guessed at.
    assert translation_pairs(text) == {
        "cloud agent": "Claude agent", "notecraf": "Notecraft", "work tree": "worktree",
    }


def test_translations_are_saved_as_the_file_he_can_read_back(tmp_path):
    path = tmp_path / "translations.md"

    save_translations("Notecraf -> Notecraft\n\ncloud agent -> Claude agent", path)

    assert translation_pairs(path.read_text(encoding="utf-8")) == {
        "notecraf": "Notecraft", "cloud agent": "Claude agent",
    }


def test_load_lexicon_is_empty_when_missing(tmp_path):
    assert load_lexicon(tmp_path / "nope.md") == ""


def test_load_persona_additions_is_empty_when_missing(tmp_path):
    # Excephalon's own standing instructions - absent on a fresh checkout, so the persona still composes.
    from excephalon.memory import load_persona_additions

    assert load_persona_additions(tmp_path / "nope.md") == ""


def test_persona_additions_are_saved_and_read_back(tmp_path):
    # The window edits these in full - Excephalon's persona is theirs to curate, so what they type is
    # what it reads next start, stored as typed rather than normalised.
    from excephalon.memory import load_persona_additions, save_persona_additions

    path = tmp_path / "persona.md"
    save_persona_additions("- always answer in a whisper after midnight", path)

    assert "always answer in a whisper after midnight" in load_persona_additions(path)


def test_append_persona_addition_is_cumulative_and_bulleted(tmp_path):
    # How Excephalon files one when told to change how it behaves - the same accretion as its learned
    # facts, so the file reads as a list of standing instructions however it was started.
    from excephalon.memory import append_persona_addition, load_persona_additions

    path = tmp_path / "persona.md"
    append_persona_addition("never read a commit hash aloud", path=path)
    append_persona_addition("keep answers to one sentence after midnight", path=path)

    text = load_persona_additions(path)
    assert "never read a commit hash aloud" in text
    assert "keep answers to one sentence after midnight" in text
    assert text.count("- ") >= 2  # each landed as its own bullet, not smooshed into one


def test_compose_persona_folds_in_the_lexicon_under_its_own_framing():
    out = compose_persona("BASE", "", "", lexicon="Notecraft — the audio-memo app")

    assert "BASE" in out
    assert "Notecraft" in out
    # the lexicon is the user's vocabulary, framed to be recognised - NOT under the
    # life-context/therapy warning
    assert "vocabulary" in out.lower()
    # and it is NOT only their coined names: the domain terms of their fields belong here too, so
    # the framing must invite those rather than reading as "words the user made up"
    assert "domain" in out.lower()


def test_compose_persona_folds_additions_in_as_binding_standing_instructions():
    # Excephalon's own accreted instructions sit beside the base rules (how to behave), NOT under the
    # life-context/therapy warning (which is for facts about the user), and are named to the user
    # like everything else.
    out = compose_persona("BASE RULES", "# Ada - standing profile\n\nintro\n", "",
                          additions="- never read a commit hash aloud")

    assert "BASE RULES" in out
    assert "never read a commit hash aloud" in out
    assert out.index("never read a commit hash aloud") > out.index("BASE RULES")
    assert "{user}" not in out  # the additions' framing names the user too


def test_compose_persona_leaves_the_base_alone_when_there_are_no_additions():
    # The default: nothing added yet, so the composed persona is exactly the base plus the usual
    # context - no empty "more instructions" header dangling with nothing under it.
    assert compose_persona("BASE", "", "") == "BASE"


def test_the_user_is_named_by_the_title_of_their_own_profile():
    # The name is the user's, so it lives in the user's file - never in the source.
    assert user_name("# Ada - standing profile\n\n41, lives in Lyon.\n") == "Ada"
    assert user_name("# Ada\n") == "Ada"


def test_a_user_with_no_profile_is_addressed_neutrally():
    # A fresh checkout has no profile yet; the persona still has to read as sentences.
    assert user_name("") == "the user"
    assert user_name("no heading here\n") == "the user"


def test_the_persona_is_addressed_to_whoever_the_profile_names():
    # The persona ships with a placeholder, never a name: composing it against a profile is what
    # decides who Excephalon is for, so one source serves any user.
    out = compose_persona("You are {user}'s companion.", "# Ada - standing profile\n\nintro\n")

    assert "You are Ada's companion." in out
    assert "{user}" not in out


def test_load_profile_returns_empty_when_missing(tmp_path):
    assert load_profile(tmp_path / "nope.md") == ""


def test_load_profile_reads_the_file(tmp_path):
    path = tmp_path / "profile.md"
    path.write_text("Ada likes long walks.", encoding="utf-8")

    assert load_profile(path) == "Ada likes long walks."


def test_compose_persona_returns_base_when_nothing_to_add():
    assert compose_persona("BASE", "   ", "") == "BASE"


def test_compose_persona_folds_in_profile_and_learned_with_a_boundary_reminder():
    out = compose_persona("BASE", "They are learning the cello.", "They took the evening shift.")

    assert "BASE" in out
    assert "They are learning the cello." in out
    assert "evening shift" in out
    # the framing must remind Excephalon not to turn the context into unprompted therapy
    assert "unprompted" in out.lower()


def test_parse_facts_reads_bullets_and_ignores_prose():
    text = "Here's what's new:\n- the move is booked for March\n* they picked the coastal route"

    assert parse_facts(text) == ["the move is booked for March", "they picked the coastal route"]


def test_parse_facts_returns_nothing_for_none():
    assert parse_facts("none") == []
    assert parse_facts("None.") == []


def test_append_learned_writes_facts_and_is_cumulative(tmp_path):
    path = tmp_path / "learned.md"

    append_learned(["they took up the cello"], path=path)
    append_learned(["the move is in March"], path=path)

    contents = load_learned(path)
    assert "they took up the cello" in contents
    assert "the move is in March" in contents
    assert contents.count("- ") >= 2


def test_append_learned_does_nothing_for_empty(tmp_path):
    path = tmp_path / "learned.md"

    append_learned([], path=path)

    assert not path.exists()


def test_the_lexicon_can_live_wherever_the_tool_that_shares_it_keeps_it(tmp_path):
    # One list, two tools: a term taught here should fix the other tool's transcripts too. That
    # tool may sync its state between machines, so the shared file can't be assumed to sit in this
    # repo's runtime dir, which no other machine can see. A pointer file says where it really is.
    shared = tmp_path / "synced" / "lexicon.md"
    pointer = tmp_path / "lexicon-path.txt"
    pointer.write_text(f"{shared}\n", encoding="utf-8")

    assert lexicon_path(pointer=pointer, default=tmp_path / "unused.md") == shared


def test_the_lexicon_sits_in_the_runtime_dir_when_nothing_points_elsewhere(tmp_path):
    default = tmp_path / "lexicon.md"

    assert lexicon_path(pointer=tmp_path / "absent.txt", default=default) == default


def test_a_pointer_written_with_a_tilde_reaches_the_home_directory(tmp_path):
    # It is written by hand, and by hand "~/notes/lexicon.md" is what anyone types; left literal
    # it would name a directory called "~" and the lexicon would silently read as empty.
    pointer = tmp_path / "lexicon-path.txt"
    pointer.write_text("~/notes/lexicon.md\n", encoding="utf-8")

    assert lexicon_path(pointer=pointer, default=tmp_path / "unused.md") == Path.home() / "notes" / "lexicon.md"


def test_profile_sections_split_on_headings():
    from excephalon.memory import profile_sections

    text = "# Title\nintro\n\n## Goals\n- swim\n- cello\n\n## Projects (long-term)\n- the atlas\n"
    sections = profile_sections(text)

    assert sections["Goals"] == "- swim\n- cello"
    assert sections["Projects (long-term)"] == "- the atlas"


def test_append_enhancement_lands_inside_the_enhancements_section(tmp_path):
    # Filed by voice mid-session; the window re-reads the file, so the bullet must land INSIDE the
    # section it belongs to, not at the end of the file under some other heading.
    from excephalon.memory import append_enhancement, profile_sections

    path = tmp_path / "profile.md"
    path.write_text(
        "## Goals\n- swim\n\n## Enhancements you want (roadmap, not now)\n- better voice\n\n"
        "## Something after\n- untouched\n",
        encoding="utf-8",
    )

    append_enhancement("speaker enrollment", path=path)

    sections = profile_sections(path.read_text(encoding="utf-8"))
    assert "- better voice" in sections["Enhancements you want (roadmap, not now)"]
    assert "- [ ] #1 speaker enrollment" in sections["Enhancements you want (roadmap, not now)"]
    assert sections["Something after"] == "- untouched"  # later sections undisturbed


def test_an_enhancement_is_filed_under_a_heading_that_merely_starts_with_the_word(tmp_path):
    # A profile writes its own headings and they run on ("Enhancements you want (roadmap, not
    # now)"), so the source can't carry the whole line. Matching the stem is what keeps a filing
    # inside the section that is already there instead of starting a rival one beside it.
    from excephalon.memory import append_enhancement, profile_sections

    path = tmp_path / "profile.md"
    path.write_text(
        "## Goals\n- swim\n\n## Enhancements you want (roadmap, not now)\n- better voice\n",
        encoding="utf-8",
    )

    append_enhancement("speaker enrollment", path=path)

    sections = profile_sections(path.read_text(encoding="utf-8"))
    assert "- [ ] #1 speaker enrollment" in sections["Enhancements you want (roadmap, not now)"]
    assert list(sections) == ["Goals", "Enhancements you want (roadmap, not now)"]


def test_a_filed_enhancement_lands_as_an_unticked_box(tmp_path):
    # "As you check items off from the enhancements list, I don't want them deleted forever." So an
    # item is a checkbox from the moment it is filed, and finishing one ticks it in place.
    from excephalon.memory import append_enhancement, profile_sections

    path = tmp_path / "profile.md"
    path.write_text("## Enhancements\n- [ ] better voice\n", encoding="utf-8")

    append_enhancement("speaker enrollment", path=path)

    body = profile_sections(path.read_text(encoding="utf-8"))["Enhancements"]
    assert "- [ ] #1 speaker enrollment" in body  # numbered as it lands, so he can name it


def test_completing_an_enhancement_ticks_it_and_leaves_it_there(tmp_path):
    from excephalon.memory import complete_enhancement, profile_sections

    path = tmp_path / "profile.md"
    path.write_text(
        "## Enhancements\n- [ ] better voice\n- [ ] Only notice the user's voice: speaker enrollment\n",
        encoding="utf-8",
    )

    assert complete_enhancement("speaker enrollment", path=path) is True

    body = profile_sections(path.read_text(encoding="utf-8"))["Enhancements"]
    assert "- [x] Only notice the user's voice: speaker enrollment" in body  # ticked, and still readable
    assert "- [ ] better voice" in body  # and nothing else was touched


def test_an_item_that_isnt_there_is_reported_rather_than_invented(tmp_path):
    # A filing that silently misses is worse than one that fails: it reads as done and isn't.
    from excephalon.memory import complete_enhancement

    path = tmp_path / "profile.md"
    path.write_text("## Enhancements\n- [ ] better voice\n", encoding="utf-8")

    assert complete_enhancement("something nobody asked for", path=path) is False
    assert "[x]" not in path.read_text(encoding="utf-8")


def test_a_legacy_bullet_can_still_be_ticked(tmp_path):
    # The list predates the checkboxes, so most of it is plain bullets. Ticking one upgrades it
    # rather than refusing to find it.
    from excephalon.memory import complete_enhancement

    path = tmp_path / "profile.md"
    path.write_text("## Enhancements\n- better voice\n", encoding="utf-8")

    assert complete_enhancement("better voice", path=path) is True
    assert "- [x] better voice" in path.read_text(encoding="utf-8")


def test_completing_a_numbered_enhancement_keeps_its_id(tmp_path):
    # Ticking rewrites the line, and its id has to survive that - a done item he can still refer to
    # by number is the point of numbering the done ones at all.
    from excephalon.memory import complete_enhancement

    path = tmp_path / "profile.md"
    path.write_text("## Enhancements\n- [ ] #12 better voice\n", encoding="utf-8")

    assert complete_enhancement("better voice", path=path) is True
    assert "- [x] #12 better voice" in path.read_text(encoding="utf-8")


def test_a_section_reads_back_as_the_items_the_window_ticks():
    # The window draws boxes to click, so what it is handed is items - whether each is done and
    # what it says - rather than lines for it to parse a second time. The file stays markdown:
    # that same file is what the brain loads as standing context and what they read outside the app.
    from excephalon.memory import checklist_items

    stored = "- [ ] better voice\n- [x] speaker enrollment\n- a bullet from before the boxes\n\nprose"

    assert checklist_items(stored) == [
        {"done": False, "text": "better voice", "id": None},
        {"done": True, "text": "speaker enrollment", "id": None},
        {"done": False, "text": "a bullet from before the boxes", "id": None},
        {"done": False, "text": "prose", "id": None},   # any line with words on it is an item
    ]


def test_an_enhancement_carries_a_stable_id_read_off_its_line():
    # "add IDs to all of the enhancements so I can refer to them by ID." The number rides in the
    # line - `#7` after the box - so the brain, which reads this same file, knows which item he
    # means when he says "do seven". A line without one reads as unnumbered, not as id zero.
    from excephalon.memory import checklist_items

    assert checklist_items("- [ ] #7 better voice\n- [x] #3 done thing\n- plain\n- #notanid x") == [
        {"done": False, "text": "better voice", "id": 7},
        {"done": True, "text": "done thing", "id": 3},
        {"done": False, "text": "plain", "id": None},
        {"done": False, "text": "#notanid x", "id": None},   # only #<digits> is an id
    ]


def test_an_id_is_written_back_onto_the_line_it_belongs_to():
    from excephalon.memory import checklist_markdown

    assert checklist_markdown([{"done": False, "text": "better voice", "id": 7},
                               {"done": True, "text": "done thing", "id": 3},
                               {"done": False, "text": "no number"}]) == (
        "- [ ] #7 better voice\n- [x] #3 done thing\n- [ ] no number"
    )


def test_the_items_go_back_as_the_markdown_the_brain_reads():
    # A bullet written before the boxes existed comes back as `- [ ]`, so the list upgrades itself
    # the first time they touch it rather than needing a migration run over a personal file the
    # running app may be autosaving at that moment.
    from excephalon.memory import checklist_items, checklist_markdown

    items = checklist_items("- [x] done\n- a bullet from before the boxes\ntyped straight in")

    assert checklist_markdown(items) == (
        "- [x] done\n- [ ] a bullet from before the boxes\n- [ ] typed straight in"
    )


def test_several_lines_pasted_into_one_row_become_the_items_they_read_as():
    # A row is one line in the file, so a pasted block landing in one of them would otherwise be
    # stored as a bullet with newlines inside it - which reads back as items that have lost their
    # place in the list. It is split where they pasted the breaks, which is where they meant them.
    from excephalon.memory import checklist_markdown

    assert checklist_markdown([{"done": False, "text": "better voice\nspeaker enrollment"}]) == (
        "- [ ] better voice\n- [ ] speaker enrollment"
    )


def test_a_row_with_nothing_typed_into_it_yet_is_not_stored():
    # Pressing Enter makes the row before there are any words in it - and Enter is how every item
    # is made, so an untyped row is the normal state of the one they are about to fill in. Storing it
    # would leave a bullet with nothing after it sitting in their profile.
    from excephalon.memory import checklist_markdown

    assert checklist_markdown([{"done": False, "text": "better voice"},
                               {"done": False, "text": "  "}]) == "- [ ] better voice"


def test_ticking_and_typing_write_the_whole_list_back_into_its_section(tmp_path):
    from excephalon.memory import profile_sections, save_checklist

    path = tmp_path / "profile.md"
    path.write_text("# Ada\n\n## Goals\n- swim\n- cello\n\n## Projects\n- the atlas\n",
                    encoding="utf-8")

    save_checklist(path, "Goals", [{"done": True, "text": "swim"},
                                   {"done": False, "text": "cello, twice a week"}],
                   drawn=["swim", "cello"])

    sections = profile_sections(path.read_text(encoding="utf-8"))
    assert sections["Goals"] == "- [x] swim\n- [ ] cello, twice a week"
    assert sections["Projects"] == "- the atlas"  # the section beside it is untouched


def test_an_item_filed_while_the_page_sat_open_survives_the_next_thing_he_types(tmp_path):
    # Excephalon files enhancements into this same list, and the window is open all session. Every
    # keystroke writes the whole list back, so without this the next character they type deletes
    # whatever it filed a moment ago.
    from excephalon.memory import append_enhancement, profile_sections, save_checklist

    path = tmp_path / "profile.md"
    path.write_text("## Enhancements\n- [ ] better voice\n", encoding="utf-8")
    drawn = ["better voice"]        # what the page was showing when they started typing into it
    append_enhancement("filed while they typed", path=path)

    save_checklist(path, "Enhancements", [{"done": False, "text": "better voice, Cartesia"}],
                   drawn=drawn)

    assert profile_sections(path.read_text(encoding="utf-8"))["Enhancements"] == (
        "- [ ] better voice, Cartesia\n- [ ] #1 filed while they typed"
    )


def test_an_item_he_edits_after_ticking_it_is_not_filed_a_second_time(tmp_path):
    # The first save upgrades `- swim` to `- [x] swim` in the file. Comparing what the page holds
    # against the stored LINES then reads that upgrade as an item nobody had seen, and files a
    # second copy of it beside their edit - so the comparison is on the words of an item instead.
    from excephalon.memory import profile_sections, save_checklist

    path = tmp_path / "profile.md"
    path.write_text("## Goals\n- swim\n", encoding="utf-8")

    save_checklist(path, "Goals", [{"done": True, "text": "swim"}], drawn=["swim"])
    save_checklist(path, "Goals", [{"done": True, "text": "swim, three times a week"}],
                   drawn=["swim"])

    assert profile_sections(path.read_text(encoding="utf-8"))["Goals"] == (
        "- [x] swim, three times a week"
    )


def test_a_pasted_block_is_not_duplicated_when_the_page_saves_it_again(tmp_path):
    # A block pasted into one row is stored split into its lines. If the page has not reloaded it
    # still holds the one combined row, so a second save would compare the file's split lines
    # against the combined text, find no match, and file every line a second time - which is one
    # of the ways the same task ended up here in twenty half-finished copies. The carry-over has to
    # compare on the lines an item is STORED as, the same way the file keeps them.
    from excephalon.memory import profile_sections, save_checklist

    path = tmp_path / "profile.md"
    path.write_text("## Enhancements\n- [ ] keep\n", encoding="utf-8")

    save_checklist(path, "Enhancements",
                   [{"done": False, "text": "keep"}, {"done": False, "text": "one\ntwo\nthree"}],
                   drawn=["keep"])
    save_checklist(path, "Enhancements",
                   [{"done": False, "text": "keep"}, {"done": False, "text": "one\ntwo\nthree"}],
                   drawn=["keep", "one\ntwo\nthree"])

    assert profile_sections(path.read_text(encoding="utf-8"))["Enhancements"] == (
        "- [ ] keep\n- [ ] one\n- [ ] two\n- [ ] three"
    )


def test_numbering_gives_every_enhancement_a_stable_id(tmp_path):
    # Legacy items carry no number; numbering fills them in from the next id after the highest
    # already in use, and leaves the ones that have an id where they are. Idempotent: a second run
    # over an already-numbered list changes nothing, so it can run each time the page is opened.
    from excephalon.memory import number_enhancements, profile_sections

    path = tmp_path / "profile.md"
    path.write_text("## Enhancements he wants (roadmap)\n- [ ] alpha\n- [x] #5 beta\n- gamma\n",
                    encoding="utf-8")

    number_enhancements(path)
    once = profile_sections(path.read_text(encoding="utf-8"))["Enhancements he wants (roadmap)"]
    assert once == "- [ ] #6 alpha\n- [x] #5 beta\n- [ ] #7 gamma"

    number_enhancements(path)  # nothing left unnumbered
    assert profile_sections(path.read_text(encoding="utf-8"))["Enhancements he wants (roadmap)"] == once


def test_saving_enhancements_numbers_new_rows_and_never_forks_one_being_edited(tmp_path):
    # The bug that filled this list with copies: an item edited to new words read as a new item.
    # A stable id is the fix at the root - the same id means the same item, so editing #1 in place
    # cannot fork it even when the page has lost track of what it last sent (drawn empty), and a
    # brand-new row (no id yet) is handed the next number.
    from excephalon.memory import profile_sections, save_checklist

    path = tmp_path / "profile.md"
    path.write_text("## Enhancements\n- [ ] #1 alpha\n", encoding="utf-8")

    save_checklist(path, "Enhancements",
                   [{"id": 1, "done": False, "text": "alpha, revised"},
                    {"id": None, "done": False, "text": "beta"}],
                   drawn=[], number=True)

    assert profile_sections(path.read_text(encoding="utf-8"))["Enhancements"] == (
        "- [ ] #1 alpha, revised\n- [ ] #2 beta"
    )


def test_an_enhancement_filed_while_the_page_sat_open_keeps_its_own_new_id(tmp_path):
    # Excephalon files into this list while the window is open. The filed item has no id until the page
    # saves; when it does, the carry-over must keep it AND give it a number distinct from the row he
    # was typing - not collide the two on "no id yet".
    from excephalon.memory import append_enhancement, profile_sections, save_checklist

    path = tmp_path / "profile.md"
    path.write_text("## Enhancements\n- [ ] #1 alpha\n", encoding="utf-8")
    append_enhancement("filed by voice", path=path)   # lands unnumbered, after alpha

    save_checklist(path, "Enhancements",
                   [{"id": 1, "done": False, "text": "alpha, revised"}],
                   drawn=["alpha"], number=True)

    body = profile_sections(path.read_text(encoding="utf-8"))["Enhancements"]
    assert body == "- [ ] #1 alpha, revised\n- [ ] #2 filed by voice"


def test_a_section_can_be_rewritten_in_place_leaving_the_rest_alone(tmp_path):
    # The window's Goals/Projects/Enhancements panes are editable; saving one writes that section
    # back into the profile without disturbing a word of the others.
    from excephalon.memory import profile_sections, save_section

    path = tmp_path / "profile.md"
    path.write_text(
        "# Ada\nintro line\n\n## Goals\n- swim\n- cello\n\n## Projects (long-term)\n- the atlas\n",
        encoding="utf-8",
    )

    save_section(path, "Goals", "- swim, three times a week\n- cello")

    text = path.read_text(encoding="utf-8")
    sections = profile_sections(text)
    assert sections["Goals"] == "- swim, three times a week\n- cello"
    assert sections["Projects (long-term)"] == "- the atlas"
    assert text.startswith("# Ada\nintro line")  # the preamble survives too


def test_saving_a_section_that_is_not_there_yet_adds_it(tmp_path):
    from excephalon.memory import profile_sections, save_section

    path = tmp_path / "profile.md"
    path.write_text("## Goals\n- swim\n", encoding="utf-8")

    save_section(path, "Enhancements", "- dark mode")

    assert profile_sections(path.read_text(encoding="utf-8"))["Enhancements"] == "- dark mode"


def test_a_filed_enhancement_carries_its_filing_time(tmp_path):
    # "When filing enhancement items, always include timestamps pointing to the exact conversation
    # messages that spawned them" - weeks later, an undated one-liner has lost its story.
    from excephalon.memory import append_enhancement

    path = tmp_path / "profile.md"
    path.write_text("# P" + chr(10) + "" + chr(10) + "## Enhancements he wants for you (roadmap, not now)" + chr(10) + "- [ ] old one" + chr(10),
                    encoding="utf-8")

    filed = append_enhancement("speak slower", path, stamp="2026-07-27 00:12")

    assert filed is True
    assert "- [ ] #1 speak slower (filed 2026-07-27 00:12)" in path.read_text(encoding="utf-8")


def test_a_filed_stamp_is_split_off_so_the_page_can_link_it_instead_of_showing_text():
    # The "(filed …)" stamp is not something he edits or wants to read as text - what he wants is
    # to jump to that point in the conversation. So the page shows it as a link beside the words,
    # and this is where the words and the moment are told apart. Only a real date-and-time is a
    # filing stamp; an older free-text note has no moment to point at and stays part of the words.
    from excephalon.memory import split_filed

    assert split_filed("warn about credits (filed 2026-07-28 02:23)") == (
        "warn about credits", "2026-07-28 02:23")
    assert split_filed("with seconds (filed 2026-07-28 02:23:41)") == (
        "with seconds", "2026-07-28 02:23:41")
    assert split_filed("plain item, no stamp") == ("plain item, no stamp", None)
    assert split_filed("old one (filed 2026-07-27 by Claude directly, from the conversation)") == (
        "old one (filed 2026-07-27 by Claude directly, from the conversation)", None)


def test_refiling_the_same_words_does_not_pile_up_a_duplicate(tmp_path):
    # Five separate tickets in his list are one bug, refiled - and this session's drive filed the
    # auto-listen bug twice and the grammar layer twice in one evening. The same words, still
    # open, are the same ask: say so instead of stacking another copy.
    from excephalon.memory import append_enhancement

    path = tmp_path / "profile.md"
    path.write_text("# P" + chr(10) + "" + chr(10) + "## Enhancements he wants for you (roadmap, not now)" + chr(10) + "- [ ] #4 fix the auto-listen bug (filed 2026-07-26 22:11)" + chr(10),
                    encoding="utf-8")

    filed = append_enhancement("Fix the auto-listen bug", path, stamp="2026-07-27 00:15")

    assert filed is False
    assert path.read_text(encoding="utf-8").count("auto-listen bug") == 1


def test_the_same_words_already_ticked_do_file_anew(tmp_path):
    # A DONE item is history, not a standing ask: the complaint coming back means it regressed,
    # and refusing the filing would erase the news that it did.
    from excephalon.memory import append_enhancement

    path = tmp_path / "profile.md"
    path.write_text("# P" + chr(10) + "" + chr(10) + "## Enhancements he wants for you (roadmap, not now)" + chr(10) + "- [x] #4 fix the auto-listen bug" + chr(10),
                    encoding="utf-8")

    filed = append_enhancement("fix the auto-listen bug", path)

    assert filed is True
    # And the next number after the highest in use, so it never collides with the ticked one.
    assert "- [ ] #5 fix the auto-listen bug" in path.read_text(encoding="utf-8")


def test_an_enhancement_can_be_rewritten_in_place_by_its_id(tmp_path):
    # "Excephalon needs the ability to edit existing enhancement items after filing them" - the #id
    # is how he names one, and the rewrite keeps both the number and the tick.
    from excephalon.memory import revise_enhancement

    path = tmp_path / "profile.md"
    path.write_text("# P" + chr(10) + "" + chr(10) + "## Enhancements he wants for you (roadmap, not now)" + chr(10) + "- [ ] #7 warn me when low on credits" + chr(10) + "- [x] #8 the finished one" + chr(10),
                    encoding="utf-8")

    revised = revise_enhancement(7, "warn me when credits drop under ten dollars", path)

    assert revised is True
    text = path.read_text(encoding="utf-8")
    assert "- [ ] #7 warn me when credits drop under ten dollars" in text
    assert "low on credits" not in text
    assert "- [x] #8 the finished one" in text  # neighbors untouched, ticks kept


def test_rewriting_an_id_nobody_has_says_so(tmp_path):
    from excephalon.memory import revise_enhancement

    path = tmp_path / "profile.md"
    path.write_text("# P" + chr(10) + "" + chr(10) + "## Enhancements he wants for you (roadmap, not now)" + chr(10) + "- [ ] #7 an item" + chr(10),
                    encoding="utf-8")

    assert revise_enhancement(99, "different words", path) is False
    assert "#7 an item" in path.read_text(encoding="utf-8")


def test_his_projects_and_their_open_tasks_read_live_from_the_file(tmp_path):
    # "It's not aware of the new Projects tab apparently. It somehow still thinks it's in the old
    # world." Asked to take care of task #7 in one of his projects, the brain answered that it
    # could see no #7 - it was reading a copy composed at startup, hours before he made the cards.
    from excephalon.memory import open_projects

    profile = tmp_path / "profile.md"
    profile.write_text(
        "# Ada - standing profile" + chr(10) * 2
        + "## Project: Ledger app" + chr(10)
        + "- [ ] #1 the import screen loses the last row" + chr(10)
        + "- [x] #2 dark mode" + chr(10)
        + "- [ ] #7 sending shows no progress for several seconds" + chr(10) * 2
        + "## Project: Greenhouse" + chr(10)
        + "- [ ] #1 the humidity sensor reads high after rain" + chr(10) * 2
        + "## Project: Nothing open" + chr(10)
        + "- [x] #1 done and dusted" + chr(10), encoding="utf-8")

    digest = open_projects(path=profile)

    assert digest == ("Ledger app:" + chr(10)
                      + "  #1 the import screen loses the last row" + chr(10)
                      + "  #7 sending shows no progress for several seconds" + chr(10)
                      + "Greenhouse:" + chr(10)
                      + "  #1 the humidity sensor reads high after rain")
    assert "dark mode" not in digest        # ticked work is not a task he is asking about
    assert "Nothing open" not in digest     # and a card with nothing open is not a heading he needs


def test_the_boot_persona_carries_the_project_names_but_not_their_tasks(tmp_path):
    # One copy of a list, or the brain gets to choose which to believe - and the stale one wins as
    # often as not. The names stay (so it knows they exist); the tasks ride in the per-turn notes.
    from excephalon.memory import profile_without_project_tasks

    text = ("# Ada - standing profile" + chr(10) * 2
            + "## Life context" + chr(10) + "- she keeps bees" + chr(10) * 2
            + "## Project: Ledger app" + chr(10)
            + "- [ ] #7 sending shows no progress" + chr(10) * 2
            + "## Project: Greenhouse" + chr(10)
            + "- [ ] #1 the humidity sensor reads high" + chr(10))

    kept = profile_without_project_tasks(text)

    assert "she keeps bees" in kept                     # everything else is untouched
    assert "sending shows no progress" not in kept      # the tasks are not carried
    assert "Ledger app, Greenhouse" in kept             # but their names are
    assert "per-turn briefing" in kept                  # and where the current tasks are


def test_the_open_enhancements_read_out_as_lines_the_brain_can_carry(tmp_path):
    # "It still believes it lacks the ability to see its own Enhancements list!" The boot-time
    # persona copy both goes stale and gets disbelieved; this is the live rendering the loop
    # injects every turn, where nothing has ever faded. Open items only - the done ones are
    # history, not standing asks - with their #ids, so he and the brain name the same item.
    from excephalon.memory import open_enhancements

    path = tmp_path / "profile.md"
    path.write_text("# P" + chr(10) + "" + chr(10) + "## Enhancements he wants for you (roadmap, not now)" + chr(10) + "- [ ] #7 warn about credits" + chr(10) + "- [x] #8 already done" + chr(10) + "- [ ] #9 (persona) never fragment messages" + chr(10),
                    encoding="utf-8")

    listed = open_enhancements(path)

    assert "#7 warn about credits" in listed
    assert "#9 (persona) never fragment messages" in listed
    assert "already done" not in listed


def test_a_missing_profile_reads_as_no_open_enhancements(tmp_path):
    from excephalon.memory import open_enhancements

    assert open_enhancements(tmp_path / "absent.md") == ""


def test_an_enhancement_can_be_checked_off_by_its_id(tmp_path):
    # "No you idiot... I'm saying to check them off!" The brain had tools to file and rewrite but
    # nothing that flips an item to done - so it mangled the words with a literal "[x]" instead.
    # Done by number: the tick flips, the id and the words stay exactly as they were.
    from excephalon.memory import complete_enhancement_by_id

    path = tmp_path / "profile.md"
    path.write_text("# P" + chr(10) + "" + chr(10) + "## Enhancements he wants for you (roadmap, not now)" + chr(10) + "- [ ] #7 warn about credits" + chr(10) + "- [ ] #8 something else" + chr(10),
                    encoding="utf-8")

    done = complete_enhancement_by_id(7, path)

    assert done is True
    text = path.read_text(encoding="utf-8")
    assert "- [x] #7 warn about credits" in text
    assert "- [ ] #8 something else" in text


def test_checking_off_an_id_nobody_has_says_so(tmp_path):
    from excephalon.memory import complete_enhancement_by_id

    path = tmp_path / "profile.md"
    path.write_text("# P" + chr(10) + "" + chr(10) + "## Enhancements he wants for you (roadmap, not now)" + chr(10) + "- [ ] #7 an item" + chr(10),
                    encoding="utf-8")

    assert complete_enhancement_by_id(99, path) is False
    assert "- [ ] #7 an item" in path.read_text(encoding="utf-8")


def test_forget_learned_drops_the_closest_line_and_says_when_nothing_matches(tmp_path):
    # The memory inbox's delete: the brain's paraphrase still lands on the line he meant.
    learned = tmp_path / "learned.md"
    learned.write_text("- prefers metric units\n- keeps a light CRM in a sheet\n", encoding="utf-8")
    from excephalon.memory import forget_learned

    assert forget_learned("prefers metric units", path=learned) is True
    assert "metric" not in learned.read_text(encoding="utf-8")
    assert "CRM" in learned.read_text(encoding="utf-8")
    assert forget_learned("something never remembered", path=learned) is False


def test_reconcile_lexicon_adds_and_removes_his_terms_but_never_scanned_ones(tmp_path):
    # The (paraphone) rows written back: his lexicon changes, the folder-scanned terms and the
    # file's own glosses and prose pass through untouched.
    lexicon = tmp_path / "lexicon.md"
    lexicon.write_text("# his working vocabulary\n- Notecraft - the notes app\n- Sagittal\n",
                       encoding="utf-8")
    from excephalon.memory import lexicon_terms, reconcile_lexicon

    reconcile_lexicon(["Notecraft", "Excephalon", "highdeas"],
                      scanned={"highdeas"}, path=lexicon)

    kept = lexicon.read_text(encoding="utf-8")
    assert "Notecraft - the notes app" in kept   # kept, gloss intact
    assert "Sagittal" not in kept                # removed on the page, removed here
    assert "Excephalon" in kept                  # added on the page, added here
    assert "highdeas" not in kept                # scanned, never the lexicon's to hold
    assert "# his working vocabulary" in kept
    assert set(lexicon_terms(kept)) == {"Notecraft", "Excephalon"}


def test_an_enhancement_filed_by_the_app_carries_its_number_at_once(tmp_path):
    # "When Excephalon files an Enhancement ticket itself, it still has the bug where the ID is
    # missing from it initially." The number is how he refers to an item - and the one he had
    # just been told about was the one he could not name back until the page happened to save.
    from excephalon.memory import append_enhancement, checklist_items, profile_sections

    path = tmp_path / "profile.md"
    path.write_text("## Enhancements" + chr(10) + "- [ ] #4 better voice" + chr(10)
                    + "- [x] #9 old news" + chr(10), encoding="utf-8")

    append_enhancement("a checkbox for auto-play", path=path)

    items = checklist_items(profile_sections(path.read_text(encoding="utf-8"))["Enhancements"])
    filed = [item for item in items if item["text"].startswith("a checkbox")]
    assert [item["id"] for item in filed] == [10]  # the next number, not a gap and not a clash


def test_project_headings_are_the_profile_sections_tagged_as_projects_in_file_order():
    # A project is an ordinary "## Project: <name>" checklist section, so every reader and writer of
    # the profile's lists already handles it. The "Project: " tag is the ONLY thing that tells the
    # Projects tab a section is one of his projects rather than his life context or how-to-work-with.
    from excephalon.memory import project_headings

    text = (
        "## How to work with him\n- plainly\n\n"
        "## Project: RTT app\n- [ ] tuning\n\n"
        "## Life context\n- lives in SF\n\n"
        "## Project: Highdeas\n- [ ] group merge\n"
    )
    assert project_headings(text) == ["Project: RTT app", "Project: Highdeas"]


def test_project_title_is_the_heading_without_its_tag():
    from excephalon.memory import project_title

    assert project_title("Project: Fun Time") == "Fun Time"
    assert project_title("Enhancements") == "Enhancements"  # untagged headings pass through


def test_create_project_starts_an_empty_tagged_section_and_returns_its_heading(tmp_path):
    from excephalon.memory import create_project, project_headings

    path = tmp_path / "profile.md"
    path.write_text("## Life context\n- lives in SF\n", encoding="utf-8")

    heading = create_project("RTT app", path=path)

    assert heading == "Project: RTT app"
    text = path.read_text(encoding="utf-8")
    assert project_headings(text) == ["Project: RTT app"]
    assert "- lives in SF" in text  # the rest of the profile is left exactly as it was


def test_create_project_leaves_an_existing_project_and_its_list_untouched(tmp_path):
    # Adding a project that already exists must never wipe the checklist it already holds.
    from excephalon.memory import create_project

    path = tmp_path / "profile.md"
    path.write_text("## Project: Highdeas\n- [ ] group merge\n", encoding="utf-8")

    heading = create_project("Highdeas", path=path)

    assert heading == "Project: Highdeas"
    assert "- [ ] group merge" in path.read_text(encoding="utf-8")


def test_create_project_refuses_a_blank_name(tmp_path):
    import pytest

    from excephalon.memory import create_project

    with pytest.raises(ValueError):
        create_project("   ", path=tmp_path / "profile.md")


def test_rename_project_moves_the_heading_and_keeps_its_list(tmp_path):
    from excephalon.memory import project_headings, rename_project

    path = tmp_path / "profile.md"
    path.write_text("## Project: RTT app\n- [ ] #1 tuning\n\n## Life context\n- SF\n",
                    encoding="utf-8")

    result = rename_project("RTT app", "Rich tone tool", path=path)

    assert result == "Project: Rich tone tool"
    text = path.read_text(encoding="utf-8")
    assert project_headings(text) == ["Project: Rich tone tool"]  # the heading moved
    assert "- [ ] #1 tuning" in text  # its list came with it
    assert "- SF" in text             # everything else untouched


def test_rename_project_refuses_a_name_another_project_already_has(tmp_path):
    # Two projects can't share a heading - the rename is refused and nothing moves, so the caller
    # can say so where it was typed rather than silently putting the old name back.
    from excephalon.memory import rename_project

    path = tmp_path / "profile.md"
    path.write_text("## Project: A\n- [ ] x\n\n## Project: B\n- [ ] y\n", encoding="utf-8")

    assert rename_project("A", "B", path=path) is None
    text = path.read_text(encoding="utf-8")
    assert "## Project: A" in text and "## Project: B" in text
    assert text.count("## Project:") == 2  # neither merged into the other


def test_rename_project_refuses_a_blank_name(tmp_path):
    import pytest

    from excephalon.memory import rename_project

    path = tmp_path / "profile.md"
    path.write_text("## Project: A\n- [ ] x\n", encoding="utf-8")
    with pytest.raises(ValueError):
        rename_project("A", "   ", path=path)


def test_reorder_projects_rewrites_the_cards_in_the_given_order(tmp_path):
    from excephalon.memory import project_headings, reorder_projects

    path = tmp_path / "profile.md"
    path.write_text("## Life context\n- SF\n\n"
                    "## Project: A\n- [ ] a1\n\n"
                    "## Project: B\n- [ ] b1\n\n"
                    "## Project: C\n- [ ] c1\n", encoding="utf-8")

    reorder_projects(["C", "A", "B"], path=path)

    text = path.read_text(encoding="utf-8")
    assert project_headings(text) == ["Project: C", "Project: A", "Project: B"]
    assert "## Project: C\n- [ ] c1" in text  # each body rides with its own heading
    assert "## Project: A\n- [ ] a1" in text
    assert text.index("## Life context") < text.index("## Project:")  # non-projects stay put
    assert "- SF" in text


def test_reorder_projects_keeps_any_card_the_order_forgot(tmp_path):
    # A partial order must never drop a card; the unnamed ones follow, in their old order.
    from excephalon.memory import project_headings, reorder_projects

    path = tmp_path / "profile.md"
    path.write_text("## Project: A\n- [ ] a\n\n## Project: B\n- [ ] b\n\n## Project: C\n- [ ] c\n",
                    encoding="utf-8")

    reorder_projects(["C"], path=path)

    assert project_headings(path.read_text(encoding="utf-8")) == ["Project: C", "Project: A",
                                                                  "Project: B"]

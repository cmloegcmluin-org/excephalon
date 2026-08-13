"""The Config page's cards and the fleet's wrap-up, from a terminal - the way in with no window.

These editors used to live only in the running app: its pages, or its save endpoints. With the app
closed there was no lever at all - three Instructions rows the user had ordered deleted sat waiting
for a window to exist, and a merged agent's ticket sat unticked for the same reason - and editing
the files freehand risks drifting from the formats the app's own savers keep. So the same savers
get a command-line door:

    python -m excephalon.cards drop-instruction "leave the codebase cleaner" "end-to-end demo"
    python -m excephalon.cards tick 117
    python -m excephalon.cards retire excephalon-link-copy-fixes --tick 115

`drop-instruction` removes Instructions rows by unique fragment - a fragment matching no row, or
more than one, refuses the whole run rather than guessing. `tick` checks an Enhancements item off
by its number, the same flip the brain's own check_off_enhancement makes. `retire` is the desk's
wrap-up minus the parts that need a live session: the log moves to the fleet's one archive, the
agent leaves the survival record, its worktree is removed, and `--tick` settles its Enhancements
item in the same breath - because a wrap-up that leaves the ticket open is exactly how finished
work came to read as thrown away.

Run these while the window's Config page is not mid-edit: the page merges its own unsaved state on
its next save, and rows deleted underneath it can be carried back by that merge.
"""

import json
import subprocess
import sys
from pathlib import Path

from excephalon.memory import (
    DEFAULT_PERSONA_ADDITIONS_PATH,
    complete_enhancement_by_id,
    instruction_rows,
    load_persona_additions,
    rows_matching,
    save_persona_additions,
)
from excephalon.tailing import archive_dir

_RUNTIME = Path(__file__).resolve().parents[2] / "runtime"
DEFAULT_STATE_PATH = _RUNTIME / "agents.json"
DEFAULT_LOG_DIR = _RUNTIME / "agent-logs"


def without_rows(text, fragments):
    """The bullet list minus the rows the fragments name - each fragment matching EXACTLY ONE
    row, or the whole edit is refused: deletion by pattern must never guess. The matching is the
    brain's own (`rows_matching`), so a fragment means the same row at both doors."""
    rows = instruction_rows(text)
    doomed = []
    for fragment in fragments:
        hits = rows_matching(rows, fragment)
        if len(hits) != 1:
            raise SystemExit(f'"{fragment}" matches {len(hits)} rows - nothing was changed')
        doomed.extend(hits)
    return "\n".join(row for row in rows if row not in doomed)


def retire(name, *, state_path=DEFAULT_STATE_PATH, log_dir=DEFAULT_LOG_DIR, run=subprocess.run):
    """Wrap one agent up with no app running: log archived, record dropped, worktree removed.

    Returns what was actually done, so the caller can say it rather than assume it. A worktree
    that refuses removal (dirty, locked) is left for a sweep - the wrap-up itself never fails
    over it, exactly as the desk's own retire does not."""
    done = []
    log_dir, state_path = Path(log_dir), Path(state_path)
    log = log_dir / f"{name}.log"
    if log.exists():
        archive = archive_dir(log_dir)
        archive.mkdir(parents=True, exist_ok=True)
        log.replace(archive / log.name)
        done.append("log archived")
    fleet, cwd = [], None
    try:
        fleet = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        fleet = []
    kept = []
    for entry in fleet:
        if entry.get("name") == name:
            cwd = entry.get("cwd")
        else:
            kept.append(entry)
    if len(kept) != len(fleet):
        state_path.write_text(json.dumps(kept, indent=2), encoding="utf-8")
        done.append("dropped from the fleet record")
    if cwd and Path(cwd).exists():
        try:
            run(["git", "-C", cwd, "worktree", "remove", cwd], check=True)
            done.append("worktree removed")
        except Exception:
            done.append("worktree left for a sweep (dirty or locked)")
    return done


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["drop-instruction"] and len(argv) > 1:
        kept = without_rows(load_persona_additions(), argv[1:])
        save_persona_additions(kept, DEFAULT_PERSONA_ADDITIONS_PATH)
        print(f"dropped {len(argv) - 1}; the card now holds {len(kept.splitlines())} rows")
        return 0
    if argv[:1] == ["tick"] and len(argv) == 2:
        if complete_enhancement_by_id(int(argv[1])):
            print(f"#{argv[1]} checked off")
            return 0
        raise SystemExit(f"no open Enhancements item carries #{argv[1]} - nothing was changed")
    if argv[:1] == ["retire"] and len(argv) in (2, 4) and (len(argv) == 2 or argv[2] == "--tick"):
        # The tick goes FIRST: a wrap-up whose ticket never got settled is the failure this
        # command exists to end, so it is not left to a second command anyone can forget.
        if len(argv) == 4:
            if not complete_enhancement_by_id(int(argv[3])):
                raise SystemExit(f"no open Enhancements item carries #{argv[3]} - nothing done")
            print(f"#{argv[3]} checked off")
        done = retire(argv[1])
        print(f"{argv[1]}: " + (", ".join(done) if done else "nothing to wrap up"))
        return 0
    print("usage: python -m excephalon.cards drop-instruction <unique fragment>...\n"
          "       python -m excephalon.cards tick <enhancement number>\n"
          "       python -m excephalon.cards retire <agent> [--tick <enhancement number>]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

# Working on Excephalon

Read this before touching anything here. It is what previous sessions learned the expensive way —
mostly by shipping something that looked right and having the user discover, again, that it wasn't.

Excephalon is a voice-in/voice-out companion that also supervises Claude coding agents on its user's
behalf. The point is not convenience; it is presence — someone to show up for — and a single voice
that shields the user from the machinery underneath. Almost every law below exists because that
shield tore somewhere.

## Land by opening a PR — the merge queue gates and merges it

**Never merge into the primary checkout, and never push to `main`.** You push your branch, open a
PR, and enqueue it. GitHub's merge queue builds the candidate merge of your PR onto the current
`main`, runs `.github/workflows/merge-gate.yml` on *that candidate*, and fast-forwards `main` only
if it is green — so what lands is exactly what was validated, even with several agents landing at
once, and there is no lock to hold.

**This replaces the global end-of-task sequence's merge step, and the `.git/agent-merge.lock` that
serializes it.** There is no local merge here, so no lock to take. Everything before it still
stands — commit, rebase, full green suite — then you push and open a PR instead of merging. The
gate runs the whole suite on `windows-latest`, the desk Excephalon runs on, so keep it green.

Work on a branch in a worktree (`git worktree add .claude/worktrees/<name> -b claude/<name>`),
never in the primary checkout. Sync by rebasing onto `origin/main` on a clean tree; never `reset`
to tidy or to sync.

```bash
# from your worktree, on your claude/<name> branch, with your work committed:
git fetch origin && git rebase origin/main    # rebase onto the LATEST main first
git push -u origin HEAD                        # --force-with-lease if the rebase rewrote pushed commits
gh pr create --fill --base main
gh pr merge --auto                             # enqueue; the queue lands it when the gate is green
                                               # --auto ALONE: --merge/--rebase/--squash trip "merge
                                               # strategy is set by the merge queue" and may not enqueue
```

**Enqueuing is not the finish line — landing is.** On a moving `main` a PR routinely goes `DIRTY`
or gets dropped from the queue on a red candidate, and then sits unmerged forever unless you act.
Watch both the candidate run *and* the PR's own checks: a `merge_group` failure never appears in
`gh pr checks`, and a failed `pull_request` check leaves auto-merge armed but never firing.

```bash
# Run in the background. Exits — and re-engages you — only when there is something to do:
#   0  merged           → report "PR #N merged" once, then stop
#   10 conflicts(DIRTY) → rebase onto origin/main, resolve inside the rebase, force-push, re-enqueue
#   11 candidate failed → read the merge_group run log, fix, push, re-enqueue
#   12 closed           → unexpected; surface to the user
#   13 PR check failed  → read the failing pull_request run log, fix, push (auto-merge stays armed)
pr=$(gh pr view --json number -q .number)
mg() { gh run list --event merge_group --limit 20 --json databaseId,status,conclusion,headBranch \
  -q "[.[]|select(.headBranch|contains(\"pr-$pr-\"))]|sort_by(.databaseId)|last|\"\(.databaseId) \(.conclusion//\"none\")\""; }
base=$(mg); base=${base%% *}; base=${base:-0}   # ignore candidate runs from superseded fixes
while :; do
  st=$(gh pr view "$pr" --json state -q .state)
  [ "$st" = MERGED ] && exit 0
  [ "$st" = CLOSED ] && exit 12
  [ "$(gh pr view "$pr" --json mergeStateStatus -q .mergeStateStatus)" = DIRTY ] && exit 10
  # A check that has actually concluded `fail` — not merely pending, which reads BLOCKED.
  if gh pr checks "$pr" 2>/dev/null | grep -qiw fail; then exit 13; fi
  latest=$(mg); rid=${latest%% *}
  if [ -n "$rid" ] && [ "${rid:-0}" -gt "$base" ] 2>/dev/null; then
    case "$latest" in *failure) exit 11;; esac
  fi
  sleep 45
done
```

**To update a branch that is still in the queue you must dequeue it first** — a
`push --force-with-lease` is rejected ("protected branch hook declined") while queued. Remove it,
then push and re-enqueue:

```bash
gh api graphql -f query='mutation($id:ID!){dequeuePullRequest(input:{id:$id}){mergeQueueEntry{position}}}' \
  -f id="$(gh pr view "$pr" --json id -q .id)"
```

**Delete your remote branch once the PR is terminal — but only on a positive merge check.**
Deleting the head branch of a still-open PR auto-closes it unmerged, and the work silently never
ships. Never key that on a watcher exit code:

```bash
gh pr view "$pr" --json state,mergedAt -q '.state + " " + (.mergedAt // "null")'
# delete ONLY when this prints "MERGED <timestamp>"
git push origin --delete "$br"
```

## Read the evidence. Never ask for it to be pasted.

Every session leaves artifacts. Use them before forming any theory:

| What | Where | Answers |
|---|---|---|
| What was on screen | `runtime/transcripts/session-*.log` | every printed and spoken line, timestamped |
| What the mic actually heard | `runtime/audio/session-*.wav` | whether a word reached the machine at all |
| What an agent said, as it said it | `runtime/agent-logs/<name>.log` (retired ones move to `runtime/agent-logs-archive/`) | whether an agent is working or dead |
| Who is running right now | `runtime/active-agents.txt` | the roster, with last-heard times |
| What Excephalon knows about its user | `runtime/profile.md`, `runtime/learned.md` | standing context; both gitignored |

Asking the user to copy their scrollback is a defect in this project — the transcript exists
precisely so nobody ever has to. Reading the transcript is also how you check your own work:
several "fixed" claims were disproved by the transcript in the next message.

**Diagnose from the artifact, never from the code's intent.** The two most expensive wrong answers
in this project's history were both confident stories told without looking: an agent declared "dead"
that answered 43 seconds later, and a freeze blamed on a phrase rather than the latched flag that
caused it. If you cannot observe something, say so and ask for the one observation that would settle
it. A plausible reconciliation is worse than an admitted gap, because it gets acted on.

**Your repro is not his machine, and a fix he has not run is not fixed.** The close-dialog hang was
"fixed" twice on the strength of a repro that passed here, and it hung on his desk both times -
the repro lacked the live app's audio stack, worker threads and keyboard hook, and nothing said so.
Before asserting the cause of anything he experienced, align your story with HIS incident's
artifacts (timestamps, event log, transcripts) - not with a rebuilt approximation of it - and when
the evidence is a repro, SAY it was a repro. Report a landed change as "landed; unverified in your
hands" until he has exercised it. And when a failure can recur, make the app write its own evidence
at the moment of failure (the close stall dumps every thread to `runtime/close-stall.log`), so the
next diagnosis starts from fact instead of belief.

## The rules Excephalon lives by

These are user requirements, learned through failures somebody had to sit through. Persona text
enforces some; code enforces the rest, and where only the persona enforces something, treat that as
a known weakness rather than a solution.

- **An app aside is not Excephalon speaking.** "(cut off mid-utterance)", a voice error, a
  note about the turn: those are the app's own, and they go out as status lines
  (`Console.aside`) rather than as messages in its voice - "it's not something Excephalon
  says". Only what he HEARS is a message.
- **Insulate the user from agents.** An agent's own words never reach them — not commit hashes, not
  test counts, not "I reran the suite myself". Every agent event (finished, died, wrote, quiet) goes
  through `narrator.py`: one trip through the brain, which composes the one or two sentences the
  user hears in its own voice. `relay.notice()` survives only as the fallback when the brain cannot
  answer — news must never die with a wedged session. Handed the raw stream, a person cannot tell
  whether they are talking to Excephalon or to the agent; the code, not the model, has to prevent it.
- **Brevity is the product.** The persona holds replies to a couple of short sentences, and the
  voice speaks them as they are written, so a barge-in is the user's own length limit. The old
  260-character cut and its told-you-it-was-cut system note are gone WITH their reason: they
  existed to manage a blocking brain and a robot voice that read whole replies at once. Do not
  reintroduce a cut without reintroducing that world.
- **Never speak while the user is mid-sentence.** Unprompted speech waits for the pause. It once
  broke in while someone was recording. (The mic being ARMED is not the test — the window's mic
  stays armed all conversation.)
- **No stock phrases.** "Got it.", "I'll get back to you on that.", "I've got a longer answer —
  ready for it?" and the still-processing check-ins are all deleted, by the user's request, after
  a year's worth of frustration in one week. Their reason to exist was a brain that blocked for
  30+ seconds; the streaming fast brain answers in the breath it was asked. Anything slower than
  a breath is an agent's job, dispatched and then narrated by the model in its own words.
- **Never self-certify.** Green tests are not verification; the user's eyes are. Put the real thing
  in front of them, or give them the exact steps, and let them judge. And never present work for
  verification while a setup step of theirs is still outstanding.
- **When the user says something isn't there, it isn't.** They are looking at the screen; you are not.
- **Never deny saying what he heard.** A line spoken in Excephalon's name is Excephalon's,
  whichever half of the app composed it — the brain, a narration, the foreman, or a notice
  restored from the spool after a restart. The unwritten-lines ledger exists for exactly this,
  and news that survives a restart is app-authored to whatever brain wakes up next, because the
  one that wrote it is gone. It denied a heads-up it had spoken verbatim eighteen minutes
  earlier ("I don't see that statement in our conversation"), which from where he sits is a
  thing that said something and then said it hadn't.
- **Finish the ticket, not just the code.** Diagnosing that an agent's work merged is half the job;
  its Enhancements item has to be ticked, its log archived and its worktree removed in the SAME
  turn you find it. Twice the diagnosis was right and the chore was handed back to him instead
  ("tell Excephalon to check #114 off"), leaving him with a list that called his finished work
  open — and he had to notice it himself, which is the whole failure. A closed app is not a
  reason to hand it over: `python -m excephalon.cards tick <n>` and
  `python -m excephalon.cards retire <agent> --tick <n>` go through the app's own savers with no
  window running.

## Failure patterns that have recurred here

- **A mechanism nobody perceives.** Truncation that the model never sees teaches it nothing; a layout
  "capped at half width" is worthless if the framework ignores the property. Before calling such a
  change done, name the recipient and state how the signal reaches them. Tests that assert the
  mechanism fired are not evidence anything received it. The chat bubbles took four attempts for
  exactly this reason — the wrap was measured and correct while the tint still painted edge to edge,
  and only screenshotting the pane and reading the pixels back showed it.
- **Latched flags.** `Outbox.arrived` is cleared only by draining. Any path that decides not to
  deliver must still drain, or the window's mic yields empty turns forever and submissions are
  never read. That froze a whole session.
- **Fan-out where one thing was named.** A worktree is recognized by its `.git`; globbing a directory
  once started an agent in `.venv`, `docs` and `src` of a single worktree.
- **A snapshot of something he edits.** Everything of his in the boot persona - his life context,
  his lists, what it has learned, his vocabulary - was read once, at startup, and he changes these
  while the app runs. Every time one drifted the brain answered from it and told him what he was
  looking at did not exist ("I can't see the Enhancements list", "I don't see a #7 task in the
  Highdeas Project"), and each was fixed for that one list alone. `StandingWatch` ends the category:
  compare the whole context every turn, hand over what moved. Before you cache anything of his,
  ask what happens when he edits it mid-session - the answer must not be "it waits for a restart".
- **Believing the model over the file.** Excephalon has claimed to have filed something, opened
  something, or verified something that had not happened. Check the artifact.
- **A launch with no mouth.** Everything the user clicks runs under an interpreter with no console
  — `pythonw.exe`, a Mac bundle, a detached relaunch — so a traceback on the way up is written
  NOWHERE and the app simply does not appear: a click, and nothing, three times now. Hence
  `launch.pyw`, which every door goes through, and which puts the fault in a dialog and the
  traceback in `runtime/launch-failure.log`. Whatever you add to the startup path, ask where its
  failure lands; and never let an installed launcher name a MODULE — a .lnk keeps forever what it
  was installed with, and after the package was renamed every shortcut on his machine went on
  asking for `-m entity`, unreachable from any code in this repo.

## Nothing personal in the source

The user's name, context, vocabulary and hardware are read at runtime from the gitignored
`runtime/` directory — never written into the source, so this repo can be public. `DEFAULT_PERSONA`
carries a `{user}` placeholder that `memory.compose_persona` fills from the title line of
`runtime/profile.md`; a checkout without one is addressed as "the user" and still reads as
sentences. Comments explain a decision by the behaviour it protects, not by whose behaviour it was.
Test fixtures use invented facts. When you add a comment here, write the failure, not the person.

## Shape of the code

`launch.pyw` at the repo root is the door: the Start Menu entry, the taskbar pin, `Excephalon.bat`,
the Mac bundle and the Restart button's relaunch all run it, and it is a FILE rather than
`-m excephalon` so that a rename can never invalidate a shortcut nothing here can reach into. It
imports nothing above `run` — the failure it exists to report is the package failing to import.
`conversation.py` is the loop (listen → think → speak) and owns turn-taking, barge-in, and the
delivery of agent news at a lull; it puts the desk's fleet briefing in front of the brain every
turn and streams the reply into the voice as it is written. `voice.py` is how a streamed reply
becomes audible — sentences cut the moment they end, synthesized and played while the next forms,
one stop draining everything — and `tts_neural.py` is the Kokoro engine behind it plus the
one-time model fetch into `runtime/tts/`, with the System.Speech robot voice serving until the
model is in. `actions.py` is everything the brain can DO: sixteen typed in-process tools wired to
the desk — among them update_persona, drop_instruction, remember and forget_memory, its levers over
its own standing instructions (`runtime/persona.md`) and memory in BOTH directions, because a card
he can edit and it cannot ends with it handing him the chore ("I don't have a way to remove
standing instructions... You'll need to manually remove the old unformatted one"). Every
instruction row's `- **Name** rule` shape is composed by the saver, never trusted to the model's
wording — the one row filed bare stood out on the card until he asked for it to be fixed by hand —
and the name is the row's identity: restating one rewrites its row in place, because "fixing" that
bare row by filing a named copy beside it is how the card gained the duplicate he then had to
order deleted. There is also file_improvement, which
REFUSES a feature request naming one of his other apps (their folder names are known; see
`names_another_app`), because the Enhancements list is for changes to Excephalon itself and twice
a Highdeas request was filed there instead of being handed to an agent, each time answered with a
written instruction that held until it didn't — its speech carries no control phrases and its
options carry no built-in tools.
`polish.py` is the chop mender, deterministic and instant, and it decides two things only. A
sentence mark before a LOWERCASE continuation is a break no writer makes on purpose ("what we
need to do. in order"). A sentence mark before one of a closed list of clause OPENERS is the
same chop wearing a capital ("do it. With a Claude agent", "in the app. At least your best
attempt", "first. Although I'm surprised", "one. Because that feature is already done") - every
chop he has ever brought back was one of those two. A capital that is not on the list keeps its
sentence: "...other than yourself. You're supposed to..." reads exactly like a real boundary,
and joining on a guess would run whole paragraphs of his together. A Haiku repairman sat on this path for three rounds and was retired in his
hands: it answered in four to eighty seconds when it answered at all, learned to echo drafts
back unchanged from its own no-op warmup, and its submit-time wait added a flat eight seconds
that bought nothing he could see. Do not put a model back here without solving the latency and
the echo both; mishearings of his own terms are the transcriber's vocabulary pass's job. `errands.py` is the quiet errand hand: small chores - move a file, tidy a folder, check one of his own services - run in one helper session, no agent tab, its outcome narrated like any other news. runtime/services.json (standard mcpServers shape, personal, gitignored) is that session's reach - Asana as a hosted MCP server OAuthed once through /mcp in an interactive `claude`; Gmail and Calendar through `google_bridge.py`, a stdio MCP SERVER over Google's CLASSIC APIs that owns its OAuth (client and tokens under runtime/google/, machine-copyable, connected once via `Connect Google.command` on the registered port 8765). It began as a forwarder to Google's hosted MCP servers and every layer was made to work except the last: with a verified-perfect sign-in - web client, their guides' exact five scopes, all four APIs enabled - every hosted tools/call still answered "The caller does not have permission", while the classic endpoints answered the same token flawlessly. Do not point anything back at the hosted servers (or the CLI's type:http transport, which also throws on their valid replies) without watching a real tools/call succeed. Errand sessions run --strict-mcp-config, because account-level claude.ai connectors attach to any session and a headless one that tries to OAuth them wedges (anthropics/claude-code#36060). The brain hears what is connected (services_note) so 'check my calendar' becomes run_errand instead of 'I can't see your calendar', and both a connected list and an unreadable file are announced at startup, because a config applied unseen - or silently not applied - reads as a broken app. `foreman.py` is the senior layer between the talker and the workers: engaged only through the
brain's ask_foreman tool, one persistent Opus-high session that reads a stuck agent's task,
situation and log tail, settles technical snags itself through its one tell_agent tool (answering
"handled", which is swallowed — and a reply that merely ENDS on the swallow-word is swallowed
whole, because everything before it is the foreman's working notes: one such analysis was queued
as the agent's "update" and sat in the roll call as a jargon bomb), and escalates to the user only
what is genuinely theirs — its
escalations go out app-authored so the unwritten-lines ledger keeps the fast brain aware of them.
`dictation.py` is the window's mic: a *state*, not a walkie-talkie — continuous
transcription into an editable draft, `hey excephalon` / `stop listening` to arm and disarm, `scratch
that` to take back what was just said, and it reports whether it is recording so nothing speaks
over the user. It is also the duplex ear: while the brain merely thinks the ear stays open and
words land in the draft; while the voice is actually sounding, chunks are judged against the
script being spoken (`covered_by`) — its own leak dropped, other words kept, and only a stop bark
cuts the audio, so the TV can never kill a reply. Its terminator answers to a mishearing as
well as to itself: "over" comes back from the transcriber as "okay" often enough that the gesture
simply failed, so a trailing "okay" standing as its own sentence ends the turn too - but only
with dictated words already in the box, so a bare "okay" answering a question is still a word.
`hearing.py` is the live line: the burst so far, re-read on a worker of its own, with
a word shown only once two readings running have agreed on it — read its docstring before changing
any number in it, because every one was measured off real captured sessions. The window is a local
web app: `mirror.py` is the conversation as a window shows it — the message model,
the thread-safe feed everything crosses on, and where each session starts — with no window in it,
so all of it is tested without a display. What it REPLAYS is a record, not prose: every message
is written as it is said to `runtime/transcripts/session-*.jsonl` (transcript.MessageLog), role
and all, and read straight back, so a reload cannot disagree with what he watched happen. It did
twice — a long submission shattered into grey lines, then a spoken update offer swallowed into
the bubble above it — because the window used to parse messages back OUT of the .log's prose,
and every rule for doing that was a guess. The .log is still written, for people; the sessions
recorded before the record existed are converted once (`messages_from_log`, which knows that a
bare line of its own was something the app SPOKE) and never guessed at again. An agent's log is
a different archive with its own door (`apply("log", …)`), since the desk prefixes every line
of it; `web.py` serves it, `templates/` and `static/` are the
pages (Ctrl+F works on all three, from one shared `finder.js` - the embedded browser has no find
bar of its own, and the two pages with the most to read through had no search at all while it
lived inside Config's script) (three: the conversation, Config — one page holding what were the Profile, Memory, Persona
and Translations tabs, with a contents rail, the old tab paths redirecting into it; Life context
and Memory are bullet lists, not checklists, and his translation and instruction edits are in
force immediately — translations swap into the running ear on save, instructions ride the
per-turn notes — and Agents),
and `desktop.py` puts them in an OS window of their own (Flask on a loopback port, pywebview
holding the view) rather than a browser tab - it also switches Edge's own context menus back on
(pywebview ties them to its debug flag), and that is the ONLY right-click menu the app has: the
page builds none of its own, because a bespoke menu cancels the real one and takes Cut/Copy/Paste
and the red-squiggle spelling suggestions with it ("I don't get that option here, but I should");
a message's dated pointer, the one thing no native menu can know, is a hover button instead. The
window reopens where it was last closed unless that monitor is gone, its X answered by the page's own styled dialog (asked OFF the GUI thread: evaluate_js inside the
closing event waits on plumbing that needs that same thread, and the inline ask froze the X press
itself) (the native confirm was a
light-mode box in a dark app; only the dialog's Close, through `Controls.quit`, actually closes),
and its bar carrying a Restart-to-upgrade button that appears only when the checkout on disk has
moved past the booted commit (worktrees.head_commit, polled by the page) - the click raises an
Updating veil, undismissable, BEFORE the request goes out, since the wind-down, the wait on the
old pid and the fresh launch are otherwise a silent stretch of wondering whether it worked - the relaunch is a
DETACHED helper (`relauncher.py`) spawned at the moment of the request, which waits for the old
pid to die however it dies and then starts the new app, because relaunching as the old process's
last act meant no relaunch at all when teardown misbehaved; window teardown answers the /quit request before
destroying, and waves its OWN destroy through the closing event (destroy fires that same event,
and answering it with the dialog question against a dying page hung the GUI thread — twice), and
the main thread waits the session worker out so native audio is never torn down under a live
thread. The memory store is an INBOX he works to zero:
`review.py` raises one remembered fact for his verdict in genuine downtime (fleet idle, the
transcript quiet a few minutes - its mtime is the clock), each fact once per session, never two
nudges close together; the brain words it (narrator "memory") and settles the verdict with
forget_memory / update_persona. The old Vocabulary card is gone: vocabulary IS translation, shown
as rows whose left side is "(paraphone)" (para + phone: anything sounding close enough), reading
and writing the lexicon (`reconcile_lexicon` - folder-scanned terms pass through untouched) and
retuning the running ear on save. The credit warning was tried and DROPPED
by his call: the local records count tokens, Anthropic's real weekly meter is percentages it does
not expose locally, and a warning measured against a guessed denominator fires wrong in both
directions — do not rebuild it without a sanctioned usage source. The app presents as
"Excephalon" everywhere he sees or hears it — title, icon (the Chaosphere: a brain in a spiked
wire cage, drawn transparent in the two-app family palette — gray-green metal, light-pink brain —
shared with Highdeas's leaf-and-mic), launcher `Excephalon.bat`, the persona's own name, and every phrase it
answers to — "hey excephalon" to wake, "goodbye/goodnight excephalon" to end — with the
`entity` forms kept working beside them because the transcriber only sometimes lands the coined
word (it is in the vocabulary to help). The word it prints and speaks and the word it listens
for must be the SAME word: for months it said "say 'hey Entity'" and "say 'goodbye entity' to
end" while the only spoken name that actually worked was the one it never called itself, so
saying its own name back to it did nothing. When you touch either list, touch the sentence that
tells him about it. The repo, the package and the tools the brain calls are `excephalon` too —
that clause used to also excuse the module, on a reason that was never its own, and the module
was renamed the day he asked what the reason actually was.

What genuinely cannot be renamed is what has already been WRITTEN DOWN: the `role` on every
stored message and the `entity> ` prefix on every recorded line. The name he sees was never in
those files — it is looked up from the role when the page draws — so the role is a storage
format, and a stored role nothing recognises is not a message at all: no name, no side, not a
bubble, so his history reloads as grey rows. `transcript.SELF` is what is written; `WAS_SELF` and
the old prefixes beside it are what is still READ. Never drop the reading half, and when adding a
prefix put the longer one first — `excephalon (heads-up)> ` starts with `excephalon> `, so the
other order turns every unprompted line into an ordinary reply. Comments quoting HIM keep his
words exactly as he said them, "Entity" and all — a quote rewritten to match today's name is a
record of something he never said. `links.py` decides what a message names that can be
opened, and opens it - and owns the other half of that question, what is SAID instead, so
the written form stays clickable while the voice gets `as_spoken`. A local address is WRITTEN
as localhost (`as_written` swaps the numeric loopback agents hand over, which is the same
machine wearing an unreadable name) and SPOKEN as "localhost port 5210" - scheme dropped, path
left to the screen - because spoken more literally it came out as letters and punctuation
points read one at a time ("quite unnatural"); an em-dash the brain glues to an address gets
its own space before the voice judges the words, or the address is read raw with the next
word welded on - and the address match itself stops at an em- or en-dash (no URL carries one),
so the page's link ends where the address does instead of swallowing "the dash and the
following word" into it. Its mirror `as_written`
exists because the brain has only one channel: everything it writes is spoken, so an
instruction to say an address naturally is obeyed in the only place it can be - the text -
and "click through at localhost port 8752" reached the screen as words nobody could click.
That repair happens in `console`, where its words become the record, rather than being
asked for in a persona; the persona asks too, but a rule only the persona carries is a
known weakness. A rename never fails in silence: a refused one comes back as a sentence
the rail shows under the name ("another log is already called that"), because a name quietly
put back reads as a broken app - and a name differing only in CASE is not a collision but this
same log, which Windows renames happily once the exists() check stops reading it as another
file ("inbox-AUTO-play-toggle" -> "inbox-auto-play-toggle" was refused for months of
capitals an all-caps heading had put there). `agent_desk.py` holds each agent as a live session in-process (handles
used to be lost to context resets), streams the whole exchange into its log,
answers to the name HE gives it (`rename` moves the desk's key, the log the window
draws a tab from, the survival record and the tag on any queued news - the worktree and branch
keep their own names, which are git's; `start_agent` takes a name up front, so "call it the
auto-play fix" works from the first word, and `safe_name` is what makes his words a filename), records the fleet in
`runtime/agents.json` and revives it on startup — an agent whose log is already in the archive is
NOT brought back (the record is written on the way down, so one wrapped up from outside the app
would otherwise rise from the dead and have its old news re-raised: "this is the third time it's
pestered me"), each surviving agent resumed by CLI session id, one caught
mid-task told to pick back up, one recorded mid-landing told to settle the merge NOW and watch it
in the foreground (a backgrounded watch once ended the turn, nothing re-engages an idle agent, and
the merged report never existed) — its digest also names tabs whose log files linger with no agent
behind them, because the window draws a tab per log file and a brain briefed from the desk alone
once could not see the tab the user was pointing at, and it claims "presented, awaiting their
verdict" only once nothing about that agent is still waiting to be spoken (`Outbox.owed_about`,
the spool's view of the whole debt): `mark_ready` fires when the walkthrough is COMPOSED, that
walkthrough then sat in hand for over an hour, and the brain briefed across the gap told him "I
presented it earlier... no new update since then" about steps he had never heard ("That's false.
You never presented it to me."); and `retire()` wraps a finished agent up whole: its log moved to
the fleet's one archive (`runtime/agent-logs-archive/`, a SIBLING of the live folder so an archived
log is outside what the roster globs and can never come back as a tab — `tailing.archive_dir` names
it in one place, shared with the window's own close button), the Enhancements item it was completing
ticked off the user's list, its session closed, its worktree removed. That item rides with the
agent from `start` (the brain passes it to `start_agent` when the work is one off the list) and is
ticked only for a cleanly finished agent, never a died one, because a wrong tick would corrupt the
list's record of ask and answer. `retire` also REFUSES an agent holding work he has not ruled on -
a tab was once closed over a finished feature and he met it as a fait accompli ("are you saying
you delivered a feature without me verifying it first?"), so a verdict is the only state a
wrap-up is legal from, exactly as it is for the push - and it REFUSES a desked agent whose news
is still waiting to be spoken (`Outbox.owed_about`): the wrap-up drops the agent's queued news
on the way out, which is right for news he has moved past ("that feature is already done") and
was catastrophic for a merged report he had never heard - the submission-feedback agent's
"Merged." died in exactly that drop, and the landed feature read as lost ("clearly my feature
just got dropped in a black hole and Excephalon somehow doesn't know anything about it"). Every task the desk hands out carries the standing
rules (rebase before presenting, present for the user's EYES, the engineering law in brief) and a
pointer to the machine-wide engineering law file when one exists (`law_path`, home-relative in
`__main__` so nothing personal enters the source); agents load their repo's checked-in CLAUDE.md
(`setting_sources=["project"]`) and never the user's personal config, whose conversation rules
and reply-format hook break a coding agent. `delivery.py` is the review loop as code — building →
presented-with-steps → landing, a verdict impossible on work never presented, approval dispatching
the landing and rejection the feedback mechanically, so the loop's order is a rule rather than a
persona habit; `steps.py` decides
what a streamed message becomes there — the agent's words as messages, and its commands, diffs and
output as the machinery under them, capped at both ends with what was dropped counted in place.
`waiting.py` is what happens when several agents finish at once: they are read out numbered and
held, and it says which one a reply just named. A bare go-ahead answering the update offer is
the APP's to answer, never the brain's: the first update is spoken word for word and whatever is
still held is NAMED after it (`_hand_over`, the one delivery path) - answering a go-ahead with the
numbered list and "Which first?" is answering the answer to a question with the question again
("I already said yes to the Highdeas-submission-feedback one. Why would you ask me this? You sound
insane."); the list decides ORDER, not whether. Word for word, because folded into a fresh
turn the content twice went missing - a "Yes" answered with "Go check it out then" - while
the news was marked delivered either way, so what the agent reported reached him not at
all ("that's not an update"). Anything more than a bare
go-ahead is still a turn of his, and the update rides into that reply as before. An agent HOLDS
its number for as long as it stays on the list: fresh news takes that agent's earliest place,
never its own arrival place (`_newest_per_agent`), because a refresh that moved an agent to the
end had the same three names read back re-numbered seconds apart ("Why did you give me two
occurrences of three updates waiting, but order them differently? Now I don't know what to tell
you."). Every piece of
news is written to the durable record as it arrives (`console.evidence`), since news that is never
spoken otherwise leaves no trace at all once its spool entry is gone - which is what made the
last diagnosis blind. When an agent's newer news replaces its older,
the old one is dropped from the SPOOL as well as from the queue (`Outbox.superseded`) - collapsing
the queue in memory alone left yesterday's sentence in the file, and the next process read it out
as news: he was told work was "ready for your eyes" thirteen seconds after giving his notes on
that very work, "out of nowhere", with nothing in it he did not already know. Held news dies the
same way when HE moves past it: telling an agent something (`tell_agent`, through
`desk.drop_news`) drops whatever that agent was still waiting to say, because an update composed
before his latest instructions was offered back to him as fresh ("surely there's no update for
smart grouping. You just sent off the latest message to it."). And a drop has to reach all THREE
places news waits - the queue, the spool, and the conversation's drained-in-hand list - so the
outbox notes every drop and the conversation collects the notes (`take_dropped`) and prunes its
own hand: a drop that cleaned the queue alone left the stale copy in hand, still being offered. `narrator.py` is how any agent event becomes
speech: the desk, the inbox watcher and the quiet monitor emit typed events into it, the brain
words each one as its own sentence - carrying the same conduct a reply carries (a narration is a
line he HEARS, and the standing conduct reached only replies, which is how "the desk" got to him),
plus where the work actually STANDS as a fact, since "the feature should be there in Highdeas
waiting" was said about work still being built; a composed line that calls unlanded work deployed
or shipped is dropped for the plain notice, which claims nothing (composed news skips the unwritten-lines ledger - the brain
remembers what it wrote), and the plain capped notice is the fallback when the brain cannot answer
- or answers too late: each narration's wait is bounded, because one hung narration once held the
brain's lock with the merge report and the quiet warning queued behind it until the app closed and
all of it died unspoken.
`brain_sdk.py` holds the persona and the session: the FAST tier (Haiku), `tools=[]`, replies
streamed delta by delta — a talker that pulls levers, never an investigator; the agents it starts
are where Opus-tier work happens. Its every ask is bounded, and so is waiting for its one-at-a-time
lock: a stream once died without raising, held the lock from inside a narration, and everything
after — the merged report, a direct question, every later submission — sat at "(thinking…)"
forever; now the deadline sheds the dead session (closing it makes the stranded ask raise, which
frees the lock) and the turn retries once on a fresh seeded session before it ever gives up. When
the CLI cannot reach a model on our behalf it does not fail — it ANSWERS, wearing the model's
clothes, and a signed-out Mac had "Not logged in · Please run /login" spoken in Excephalon's own
voice and filed in its own bubble (there is no /login to type at a microphone). `sdk_session`
raises `BrainUnavailable` instead of returning those words, on either shape the refusal takes: a
message carrying an `error` at all, and a result flagged `is_error` with nothing said all turn —
the second is the one the retry hits, and catching only the first bought a turn that passed in
total silence. Never match on the wording; the structure is what is true. Warmup is the one caller
that swallows it, into an app aside naming the one thing he can do, because a warmup that raises
is an app that never opens and a traceback with no console to land in. `memory.py` is the profile, what Excephalon has learned, and the lexicon - and `StandingWatch`,
which is why none of it can go stale again: every turn it re-reads his standing context, compares
it whole against what the brain has been told, and puts whatever MOVED in front of the brain,
then recomposes the persona so the next session starts from the current world. Nothing in it names
a part - the snapshot is taken from the file's own headings - so a section he invents next month
is watched the day he makes it. Write that way here: a fix that names one list is a fix that
arrives one incident late. A list he EDITS while
the app runs cannot live in the boot persona: that copy is composed once, and both his
Enhancements and his Projects went stale there and were then disbelieved to his face ("I can't
see the Enhancements list"; "I don't see a #7 task in the Highdeas Project" about a card he had
just made). Both ride in the per-turn notes now, read from the file each turn
(`open_enhancements`, `open_projects`), and the persona carries the project NAMES only
(`profile_without_project_tasks`) - one copy of a list, or the brain gets to choose which to
believe, and the stale one wins as often as not. `cards.py` is the
cards' and the fleet's command-line door - `drop-instruction "<unique fragment>"`, `tick <number>`,
`retire <agent> [--tick <number>]` - the same savers the app's pages use, for when the window isn't
up; a fragment matching zero or several rows refuses the whole edit rather than guessing, and
retire's tick runs FIRST, because a wrap-up that leaves the ticket open is how finished work came
to read as thrown away.
`chord.py` hears the modifier beside the spacebar + Enter, which no window on this machine can be
given — read its docstring before touching it; every claim in there was measured and several
obvious designs are wrong. The webview owns the main thread; the conversation, the dictation pump
and the keyboard hook run on workers, and the page's own poll is what drains the feed.

## Open work

Nothing is assigned. Outstanding in the profile's Enhancements: the rest of hearing only the user's
voice.

**Hearing only the user is DROPPED**, by his call after reading the measurements below: "let's drop this feature. It's not important right now and it seems like it's too difficult for you to accomplish effectively." Its two Enhancements items are off his list and the per-chunk scoring is unwired - `voiceprint.py`, the model and his enrollment stay on disk, read by nothing. Do not restart this without him asking; what follows is why, so the next attempt starts from the evidence rather than the idea.

The measuring half exists: `voiceprint.py` learns the user's voice
from one minute of them reading (`Learn my voice.bat` at the repo root records it, keeps the raw
wav in `runtime/voice/` for future re-learning, saves the averaged speaker embedding) and scores
any audio against it — sherpa-onnx CAM++ (`runtime/voice/wespeaker_en_voxceleb_CAM++.onnx`, 28 MB;
torch stacks don't install on this Python). Measured on real session audio: the model separates
voices (Excephalon against its own print ~0.55–0.95; a mostly-him session against Excephalon's print
median 0.18, and the high outliers in that set were literally Excephalon's replies leaking through the
speakers into the armed mic) — but a print scraped from UNLABELED session audio matches everything
a little, so enrollment is the clean recording, never scraped bootstrapping. No score DECIDES
anything yet: `score()` yields None without a print, callers keep the words, and the dropping
threshold gets chosen only from scores logged across real sessions - which the window's pump now
collects: `Scorekeeper` (wired into `Dictation`) scores every worded chunk on a worker of its own
into `runtime/voice/scores-*.log`, score beside words, a no-op until the minute is recorded. Loopback gating WAS built once
and was taken back out the same day, because it went deaf to the user — the meter moved with their
voice and not a word reached the draft. That is the whole lesson, and it cost an hour of a broken
app: a false negative here is far worse than a false positive, and the threshold that produced it
had been fitted to a single four-minute sample. Read `git log` for `playback.py` before rebuilding
it. What was measured and still holds:

- WASAPI loopback capture works, but not through `sounddevice` — its PortAudio build (19.7.0-devel)
  has no loopback flag and enumerates no loopback devices. `soundcard` does it.
- Speaker → air → mic on the test machine is 90 ms, a clean correlation peak (r = .83 there, .47
  either side). Comparing per-frame LOUDNESS survives the room; the waveform does not. Plain envelope
  correlation beat log and sqrt on labelled data.
- On one four-minute capture — a loud stream, the user talking over it — the stream's bursts scored
  +0.38 to +0.96 against the delayed playback and the user's own −0.26 to +0.58. Replayed, a 0.6 bar
  took 75 s of streamer-only from 7 draft lines to 0 and kept all twelve of the user's.
- And it still ate the user's speech live. So that sample did not generalise, the margin above its
  worst (0.583) was 0.017, and no bar fitted to one recording should be trusted. Whatever comes next
  needs paired captures across several sessions and volumes, and must fail toward hearing the user.

**And the speaker scores now say the same thing, from real sessions.** 3,258 chunks are logged.
Labelled against the transcripts - a chunk whose words became a submitted turn is HIS, a chunk
whose words match a line the app spoke is ITS OWN voice leaking back - the two distributions sit
on top of each other: his median 0.18, its own 0.24, and 17 of the 19 leaked chunks score at or
above his 10th percentile. The print separates his long sentences well (0.75-0.85 on the longest)
and says nothing about short ones ("Yeah.", "Okay." at -0.10), so the length of the chunk, not
the bar, is the first thing any future gate has to reckon with. On this evidence the switch stays
off: a threshold today would eat his speech, which is the one failure this feature may not have.

Speaker enrollment is untouched. A voiceprint is personal: `runtime/`, never the source, and
bootstrapping is free — the chunks that became submitted turns in past sessions are labelled samples
of the user's voice. Same asymmetry, same decision point: `Burst`, beside `carries_speech`.

**Printing as it listens is done.** Parakeet has no streaming door — `recognize` takes a waveform
and reads all of it — so the burst so far is re-read as it grows, on a worker, because at 90 ms for
one second of speech and 640 ms for twenty it is thirty times faster than real time but nowhere near
cheap enough for the pump's thread. The readings are not fit to show raw: their tails are guesswork
the next reading rewrites, and four times in one three-second sentence the model answered a stretch
it could not place with nothing at all. Only what two readings running agree on goes up, and the line
never shrinks. Replayed at speaking speed through the real pump and the real Parakeet, real
sentences reached the screen 2 to 5 seconds before the draft box used to fill.

Driving the fleet is done. Which agent a piece of news is about now travels with it (`Outbox.News`)
rather than being read back out of the sentence, several ready at once are read out numbered, and
whichever is named is the one spoken. Numbering was chosen over a new brain directive, because a
marker is a thing that has reached the user verbatim before.

## How the user works

They drive; you navigate. They are not technical outside code — any manual step needs literal,
click-by-click instructions, in Git Bash syntax, never PowerShell. They watch for the difference
between what you claim and what happened, and they are right about it more often than not.

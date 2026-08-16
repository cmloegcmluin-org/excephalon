/* The pages that are edited in place: the profile's lists, what Excephalon has learned, and the words
   it swaps.

   There is no Save button, because a document you have to remember to save is one you lose. It
   writes back when typing stops, and says so - a save nobody can see is one nobody trusts.

   And it writes back BEFORE the page goes. A save half a second after the last keystroke never
   happens at all if the next thing you do is click another page in the bar, which is exactly how
   the profile lost everything they added to it: "I add new items, tab away, tab back, and they're
   just gone." So whatever is still waiting goes out the moment what they are editing loses focus,
   and again on the way off the page - by beacon, the only send that outlives the page that
   started it. */

const saved = document.getElementById("saved");
const AFTER = 500;   // milliseconds of not typing before what is written goes back

function announce(what) {
  saved.textContent = what;
  saved.classList.add("showing");
  setTimeout(() => saved.classList.remove("showing"), 1600);
}

/* Two shapes of thing to send, and one way to send either: a box of text is the field it holds,
   a list is its items. `fetch` and `sendBeacon` both read the encoding off the body itself. */
const asForm = (fields) => new URLSearchParams(fields);
const asJson = (payload) => new Blob([JSON.stringify(payload)], { type: "application/json" });

async function post(where, body, leaving) {
  if (leaving) {
    navigator.sendBeacon(where, body);   // the page is going; nothing that waits for a reply lands
    return;
  }
  await fetch(where, { method: "POST", body });
  announce("Saved");
}

/* ---- what is still waiting to be written back ----------------------------------------------- */

const waiting = new Map();   // the box or list being edited -> what will save it, and when

function soon(what, save) {
  clearTimeout(waiting.get(what)?.timer);
  waiting.set(what, { save, timer: setTimeout(() => flush(what), AFTER) });
}

function flush(what, leaving) {
  const held = waiting.get(what);
  if (!held) return;
  clearTimeout(held.timer);
  waiting.delete(what);
  held.save(leaving);
}

/* Ticking a box is not typing: there is no pause to wait out, so it goes now - and drops whatever
   was still on the timer, which is the same save reading the same list. */
function atOnce(what, save) {
  clearTimeout(waiting.get(what)?.timer);
  waiting.delete(what);
  save(false);
}

/* A click on another page in the bar takes this one away with the edit still on its timer. Both of
   these fire while the page can still speak: `pagehide` is its last word, and a window merely
   hidden - they switched to something else - may never come back to fire anything. */
const flushAll = () => { for (const what of [...waiting.keys()]) flush(what, true); };
addEventListener("pagehide", flushAll);
document.addEventListener("visibilitychange", () => { if (document.hidden) flushAll(); });

/* ---- the words it swaps: styled rows, edited in place --------------------------------------- */

/* One list, no labels, no second plain-text copy: each row is typed straight into, + makes the
   next. A save writes exactly the rows that differ from what ships (each row carries the stock
   rule for its words in data-heard/data-stock), so an untouched built-in is never copied into
   his file and an emptied row simply stops being written - which is how one is removed. */
const swaps = document.getElementById("swaps");
if (swaps) {
  /* A left side of (circasonant) - circa + sonant, anything sounding close enough - marks a
     lexicon row: those write back to his lexicon, the word-for-word rules to his translations. */
  const CIRCASONANT = "(circasonant)";
  const parts = () => {
    const rows = [...swaps.querySelectorAll("li")].map((row) => ({
      heard: row.querySelector(".heard").textContent.trim(),
      said: row.querySelector(".said").textContent.trim(),
      was: row.dataset.heard ?? "",
      stock: row.dataset.stock ?? "",
    }));
    return {
      rules: rows
        .filter((rule) => rule.heard && rule.heard !== CIRCASONANT && rule.said
                          && (rule.heard !== rule.was || rule.said !== rule.stock))
        .map((rule) => `${rule.heard} -> ${rule.said}`).join("\n"),
      /* Every term still on the page - the server reconciles his lexicon against this, and
         folder-scanned terms pass through untouched. */
      terms: rows.filter((rule) => rule.heard === CIRCASONANT && rule.said)
        .map((rule) => rule.said).join("\n"),
    };
  };
  const save = (leaving) => {
    const { rules, terms } = parts();
    post("/translations", asForm({ body: rules }), leaving);
    post("/lexicon", asForm({ terms }), leaving);
  };
  swaps.addEventListener("input", () => soon(swaps, save));
  swaps.addEventListener("focusout", () => flush(swaps));
  document.getElementById("add-swap")?.addEventListener("click", () => {
    const row = document.createElement("li");
    row.dataset.heard = "";
    row.dataset.stock = "";
    for (const part of ["heard", "arrow", "said"]) {
      const span = document.createElement("span");
      span.className = part;
      if (part === "arrow") {
        span.textContent = "→";
        span.setAttribute("aria-hidden", "true");
      } else {
        span.contentEditable = "plaintext-only";
      }
      row.append(span);
    }
    swaps.append(row);
    row.querySelector(".heard").focus();
  });
}

/* ---- the contents column shared by the Config and Projects pages ---------------------------- */

/* Only the buttons that name a card to scroll to - the Projects rail also holds the "+ New project"
   submit button, which is the form's, not one of these. */
for (const goes of document.querySelectorAll("#toc button[data-goes]")) {
  goes.addEventListener("click", () => {
    document.getElementById(goes.dataset.goes)?.scrollIntoView({ block: "start" });
    for (const other of goes.parentElement.children) other.removeAttribute("aria-current");
    goes.setAttribute("aria-current", "true");
  });
}

/* ---- the profile's lists: a box to tick, and the words beside it ----------------------------- */

const rowsOf = (list) => [...list.querySelectorAll("li")];
const wordsOf = (row) => row.querySelector(".item");
/* The row carries its stable id as `data-id`, so a save sends it back and the same item keeps the
   same number. A row he has just made has none yet; the server hands it the next one. A bullet
   row (Life context, Memory) has no box at all - background is never "done". */
const itemsOf = (list) => rowsOf(list).map((row) => ({
  id: row.dataset.id ? Number(row.dataset.id) : null,
  done: row.querySelector("input")?.checked ?? false,
  text: storedText(row),
}));

/* What the file stores for this row: the words he edits, with its filing stamp put back on the
   end so the round-trip keeps the exact "(filed …)" the file had. The stamp shows as a link beside
   the words, never inside them, so no keystroke can edit or lose it. */
function storedText(row) {
  const words = wordsOf(row).textContent;
  // A named bullet keeps its markdown in the file and its bold on the page: the asterisks go back
  // on here, so no keystroke in the words can lose them and no edit of his is stored as markup.
  const lede = row.querySelector(".lede");
  const said = lede && lede.textContent.trim() ? `**${lede.textContent.trim()}** ${words.trim()}`
                                               : words;
  return row.dataset.filed ? `${said} (filed ${row.dataset.filed})` : said;
}

/* A fresh, empty row shaped like an existing one. A new item carries none of the id the row it was
   cloned from has, so the server numbers it anew rather than two rows claiming one number. The page
   is the one place a row's shape is written, so a new row is made by cloning, never by building. */
function freshRow(like) {
  const row = like.cloneNode(true);
  row.className = "";
  row.removeAttribute("data-id");
  // A new row is not filed - drop the cloned stamp and its link, so it saves as plain words.
  row.removeAttribute("data-filed");
  row.querySelector(".filed")?.remove();
  // A fresh row is on no agent, whatever the row it was cloned from was: its gutter is the gray
  // "start an agent" button, not the green link an agent-task row carries. Enter pressed in such a
  // task would otherwise clone that link onto the new, agentless row - a lie about what is running
  // until the next draw. The robot glyph is reused, never rebuilt; only its wrapper changes.
  const link = row.querySelector(".agent-link");
  if (link) {
    const start = document.createElement("button");
    start.type = "button";
    start.className = "agent-start";
    // The canonical tooltip, read from a start button the server already rendered rather than
    // duplicated here; harmlessly empty in the rare card with no no-agent task to copy from.
    start.title = document.querySelector(".agent-start")?.title || "";
    while (link.firstChild) start.append(link.firstChild);
    link.replaceWith(start);
  }
  wordsOf(row).textContent = "";
  const box = row.querySelector("input");
  if (!box) return row;  // a bullet row: the dot came along in the clone, and that is all of it
  box.checked = false;
  // The number's spot is reserved from the first keystroke - typing used to start in the space
  // where the id belongs - and the placeholder becomes the real number when the save assigns it.
  let tag = row.querySelector(".tag");
  if (!tag) {
    tag = document.createElement("span");
    tag.className = "tag";
    box.after(tag);
  }
  tag.classList.add("pending");
  tag.textContent = "#·";
  return row;
}

/* Pasting a block is how a list arrives from somewhere else - a page, a doc, another list. Each
   line becomes its own item ("assume any newline is a checklist item when pasting"), and a line
   that comes punctuated as a bullet is stripped back to its words, so a pasted bullet list becomes
   the items it reads as instead of smooshing into one note. */
const LIST_MARKER = /^\s*(?:[-*•]|\d+[.)])\s+/;
const pastedLines = (text) =>
  text.split(/\r?\n/).map((line) => line.replace(LIST_MARKER, "").trim()).filter(Boolean);

/* Put the caret in a row, at one end or the other of what it says. */
function caretTo(row, atStart) {
  const words = wordsOf(row);
  words.focus();
  const spot = document.createRange();
  spot.selectNodeContents(words);
  spot.collapse(atStart);
  const chosen = getSelection();
  chosen.removeAllRanges();
  chosen.addRange(spot);
}

const lengthBefore = (words, node, offset) => {
  const spot = document.createRange();
  spot.selectNodeContents(words);
  spot.setEnd(node, offset);
  return spot.toString().length;
};

/* Where the caret is in a row's words, as the start and end of what is selected - the same number
   twice when nothing is. Measured as the length of the text before each point rather than as an
   offset into a node, because the words are a single text node only until something splits them.
   A selection that has escaped this row reads as the very end, so Enter adds a row rather than
   cutting across two of them. */
function caretIn(words) {
  const chosen = getSelection();
  const spot = chosen.rangeCount ? chosen.getRangeAt(0) : null;
  const end = words.textContent.length;
  if (!spot || !words.contains(spot.startContainer) || !words.contains(spot.endContainer)) {
    return [end, end];
  }
  return [lengthBefore(words, spot.startContainer, spot.startOffset),
          lengthBefore(words, spot.endContainer, spot.endOffset)];
}

/* A section is one list, whether it is drawn as one or as an open list with a folded Done one
   beneath it. So the unit here is the <section>, not a <ul>: a save gathers every row under it,
   and an edit in either list writes the whole thing back. */
for (const section of document.querySelectorAll(".section[data-heading], .section[data-save]")) {
  /* What the page believes the file holds, so a save can tell their own edit from an item Excephalon
     filed into the same section while the window sat open. It is what was last SENT rather than
     what was first drawn, because the file rewrites `- x` as `- [ ] x` the moment anything saves
     it, and a stale answer here files a second copy of everything they have edited since. */
  let drawn = itemsOf(section).map((item) => item.text);
  const save = async (leaving) => {
    const items = itemsOf(section);
    /* A card that names where it saves (Memory, Instructions) is a whole file of bullet lines,
       not a profile section: its rows go back as the `- x` lines the file keeps. */
    if (section.dataset.save) {
      const body = items.map((item) => item.text.trim()).filter(Boolean)
        .map((line) => `- ${line}`).join("\n");
      return post(section.dataset.save, asForm({ body }), leaving);
    }
    const was = drawn;
    drawn = items.map((item) => item.text);
    if (leaving) {
      return post("/profile", asJson({ heading: section.dataset.heading, items, drawn: was }), true);
    }
    const response = await fetch("/profile", {
      method: "POST", body: asJson({ heading: section.dataset.heading, items, drawn: was }),
    });
    announce("Saved");
    // The server hands each sent row its number; the pending tags become the real ids in place,
    // so a new item shows its number the moment it first saves rather than on the next page load.
    const { ids } = await response.json();
    rowsOf(section).forEach((row, at) => {
      if (!ids || ids[at] == null) return;
      row.dataset.id = ids[at];
      const tag = row.querySelector(".tag");
      if (tag) {
        tag.textContent = `#${ids[at]}`;
        tag.classList.remove("pending");
      }
    });
  };

  /* An item that gets done is ticked, never removed: it is the only record that a complaint was
     heard and acted on. Dimmed rather than struck through, so it stays legible. It joins the Done
     fold on the next draw of the page, not the instant it is ticked - moving it out from under the
     caret mid-click would be its own surprise. */
  section.addEventListener("change", (event) => {
    event.target.closest("li").classList.toggle("done", event.target.checked);
    atOnce(section, save);
  });

  section.addEventListener("input", () => soon(section, save));
  section.addEventListener("focusout", () => flush(section));

  /* A pasted block becomes one item per line. The first line joins whatever the caret was in; the
     rest become fresh rows after it, and the words that were to the right of the caret ride on the
     last one. Without this the browser drops a block into a single row and it smooshes into one
     note - and a plain-text paste can lose its line breaks entirely, which is why the lines are
     read off the clipboard here rather than out of the row afterwards. */
  section.addEventListener("paste", (event) => {
    const words = event.target.closest(".item");
    if (!words) return;
    const lines = pastedLines(event.clipboardData.getData("text/plain"));
    if (!lines.length) return;   // nothing but blank lines and markers - let the default no-op
    event.preventDefault();
    const said = words.textContent;
    const [from, to] = caretIn(words);
    words.textContent = said.slice(0, from) + lines[0];
    let anchor = words.closest("li");
    for (const line of lines.slice(1)) {
      const next = freshRow(anchor);
      wordsOf(next).textContent = line;
      anchor.after(next);
      anchor = next;
    }
    wordsOf(anchor).textContent += said.slice(to);
    soon(section, save);
    caretTo(anchor, false);
  });

  section.addEventListener("keydown", (event) => {
    const words = event.target.closest(".item");
    if (!words) return;
    const row = words.closest("li");
    if (event.key === "Enter") {
      /* Enter is how an item is made - the whole point of this page. Whatever is to the right of
         the caret goes with it, so Enter at the end of a line (which is where it is pressed) makes
         an empty one, and Enter in the middle of one splits it where they asked. */
      event.preventDefault();
      const said = words.textContent;
      const [from, to] = caretIn(words);
      words.textContent = said.slice(0, from);
      const next = freshRow(row);
      wordsOf(next).textContent = said.slice(to);
      row.after(next);
      soon(section, save);
      caretTo(next, true);                // ...which loses focus, and sends what was just made
    } else if (event.key === "Backspace" && !words.textContent
               && row.closest("ul").querySelectorAll("li").length > 1) {
      /* Backspace out of a row they made and did not fill in, the way they made it. Only an empty
         one: an item with words is removed by emptying it first, never by a stray keystroke. Never
         the last row of a list, so the open list always keeps a line to type into. */
      event.preventDefault();
      const above = row.previousElementSibling;
      const back = above || row.nextElementSibling;
      row.remove();
      soon(section, save);
      caretTo(back, !above);
    }
  });
}



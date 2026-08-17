/* The Projects rail: a name renames in place (double-click, like an agent's), a row drags to
   reorder the cards, and a freshly-added card opens already in edit mode. The checklist inside each
   card is writing.js's job; this file is only the rail. */

/* A refused rename says so where it was typed - a quiet restore of the old name is how a rename he
   had every reason to expect comes to read as a broken app. Same shape as the Agents rail. */
async function complain(where, answer) {
  const why = await answer.json().then((said) => said.why).catch(() => "");
  where?.querySelector(".refused")?.remove();
  const note = document.createElement("span");
  note.className = "refused";
  note.textContent = why || "that rename could not be made";
  where?.append(note);
  setTimeout(() => note.remove(), 12000);  // long enough to read, not long enough to litter
}

/* Open a name for editing: while it is being typed into, its row must not drag (selecting text in a
   draggable element starts a drag instead), so draggable is switched off until the edit is done. */
function openEditor(label) {
  label.closest(".rail-item").draggable = false;
  label.setAttribute("contenteditable", "plaintext-only");
  // On the next turn of the loop, not in this handler: the browser's own double-click word-select
  // runs after us, and taking the caret before it settled left the box focused but deaf.
  setTimeout(() => { label.focus(); getSelection().selectAllChildren(label); }, 0);
}

/* A single click on a name brings its card into view - but not the click that just placed the caret
   to edit it. */
for (const name of document.querySelectorAll("#project-rail [data-goes]")) {
  name.addEventListener("click", () => {
    if (name.getAttribute("contenteditable")) return;
    document.getElementById(name.dataset.goes)?.scrollIntoView({ block: "start" });
  });
}

/* Double-click a name, type, Enter or click away to rename. The card's heading moves, so the page
   reloads to redraw it in its new name. Escape puts back what was there. A name another card
   already has is refused, with the reason shown beside it. */
for (const label of document.querySelectorAll("#project-rail [data-rename]")) {
  const was = label.dataset.rename;
  label.addEventListener("dblclick", () => openEditor(label));
  const save = async () => {
    label.removeAttribute("contenteditable");
    label.closest(".rail-item").draggable = true;
    const wanted = label.textContent.trim();
    if (!wanted || wanted === was) { label.textContent = was; return; }
    const answer = await fetch("/project/rename",
      { method: "POST", body: new URLSearchParams({ from: was, to: wanted }) });
    if (!answer.ok) { label.textContent = was; await complain(label.closest(".rail-item"), answer); return; }
    location.assign("/projects");
    location.reload();  // the heading moved; redraw the rail and the cards under the new name
  };
  label.addEventListener("blur", save);
  label.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); label.blur(); }
    if (event.key === "Escape") { label.textContent = was; label.blur(); }
  });
}

/* A freshly-added card lands with ?editing=<name>: open its name for typing over, so + starts a
   card already in edit mode rather than one left called "New project". */
const editing = new URLSearchParams(location.search).get("editing");
if (editing) {
  const fresh = [...document.querySelectorAll("#project-rail [data-rename]")]
    .find((label) => label.dataset.rename === editing);
  if (fresh) openEditor(fresh);
  history.replaceState(null, "", "/projects");  // so a reload does not reopen the editor
}

/* The gray robot on a task an agent is NOT on starts one, then and there - the deterministic half
   of "telling Excephalon 'please take care of this task'", with no brain in the loop to decide. The
   click posts the task to /task/take-care, the server starts the agent, and the moment it hands the
   name back the robot goes green here: the task is now one an agent is on. A task already green is
   the link instead, left to the browser as an ordinary <a>.

   Delegated from the document so a row made after load (Enter clones one) is wired without rebinding,
   and it reads the task off its own row - the project its card names, and the words. */
function announceAsked(what) {
  const saved = document.getElementById("saved");
  if (!saved) return;   // the same quiet status line a save uses - a click nobody can see is one
  saved.textContent = what;   // nobody trusts
  saved.classList.add("showing");
  setTimeout(() => saved.classList.remove("showing"), 1600);
}

/* The gray start button becomes the green working link the instant its agent exists - the robot
   glyph itself is reused, only its wrapper changes, so the task turns green in place with no reload. */
function turnGreen(button, agent) {
  if (!button.isConnected) return;   // the poll may have already turned this one green after a reload
  const link = document.createElement("a");
  link.className = "agent-link working";
  link.href = `/agents#agent-${encodeURIComponent(agent)}`;
  link.title = `${agent} is on this — open its log`;
  while (button.firstChild) link.append(button.firstChild);
  button.replaceWith(link);
}

/* A start takes a beat, and turnGreen draws its green in THIS page's DOM - so a tab switch, which
   loads /projects afresh, loses it: the task shows gray until the desk's record makes it green on
   some later load, and the spinner in between vanishes. To carry the spinner across that switch,
   every task a start is fired on is remembered in sessionStorage (this browser session only), its
   spinner re-applied on load, then resolved against /projects/fleet - the server's own truth for
   "an agent is on this" - the moment the agent lands, or dropped after a bound so a start that never
   lands can't spin forever. sessionStorage never overrides that truth; it only bridges the gap until
   a load can read it. */
const STARTING_KEY = "excephalon:starting-tasks";
const STARTING_TTL = 90000;   // a start not landed in this long is treated as gone, not still spinning

function startingTasks() {
  try { return JSON.parse(sessionStorage.getItem(STARTING_KEY)) || {}; } catch { return {}; }
}
function saveStarting(tasks) { sessionStorage.setItem(STARTING_KEY, JSON.stringify(tasks)); }
function taskKey(project, text) { return `${project}\n${text}`; }
function rememberStarting(project, text) {
  const tasks = startingTasks(); tasks[taskKey(project, text)] = Date.now(); saveStarting(tasks);
}
function forgetStarting(project, text) {
  const tasks = startingTasks(); delete tasks[taskKey(project, text)]; saveStarting(tasks);
}

/* The gray start button for a given task, found by its words and its card - the handle a remembered
   start needs to spin or turn green after a reload, when the click that made it is long gone. */
function startButtonFor(project, text) {
  return [...document.querySelectorAll(".agent-start")].find((button) => {
    const row = button.closest("li");
    return row?.querySelector(".item")?.textContent.trim() === text
        && (row.closest(".section")?.dataset.project || "") === project;
  });
}

/* While any start is remembered, ask the server which have landed an agent and resolve each: a
   landed one turns green (spinner -> link, no reload), one that never lands drops back to gray after
   its bound, the rest keep spinning. Runs on load (to pick spinners back up after a tab switch) and
   after a click; stops itself once nothing is left to resolve. One poll for all of them. */
let resolving = null;
function pollStarting() {
  if (Object.keys(startingTasks()).length === 0) return;   // nothing remembered: no poll to run
  if (resolving === null) resolving = setInterval(resolveStarting, 1500);
  resolveStarting();
}
async function resolveStarting() {
  const tasks = startingTasks();
  if (Object.keys(tasks).length === 0) { clearInterval(resolving); resolving = null; return; }
  let working;
  try { working = ((await (await fetch("/projects/fleet")).json()).working) || {}; }
  catch { return; }   // a hiccup reaching the server: leave everything as it is, try again next tick
  for (const key of Object.keys(tasks)) {
    const [project, text] = key.split("\n");
    const button = startButtonFor(project, text);
    const landed = working[text];
    if (landed && landed.title === project) {
      forgetStarting(project, text);
      if (button) turnGreen(button, landed.agent);                 // the desk has it now
    } else if (Date.now() - tasks[key] > STARTING_TTL) {
      forgetStarting(project, text);                               // never landed: stop spinning
      if (button) { button.disabled = false; button.classList.remove("starting"); }
    } else if (button) {
      button.disabled = true; button.classList.add("starting");    // still coming up: keep it spinning
    }
  }
}

document.addEventListener("click", async (event) => {
  const button = event.target.closest(".agent-start");
  if (!button) return;
  const row = button.closest("li");
  const text = row?.querySelector(".item")?.textContent.trim();
  if (!text) return;   // an empty, not-yet-saved row is nothing to put an agent on yet
  const project = row.closest(".section")?.dataset.project || "";
  button.disabled = true;   // one click is one agent; a double-click must not start two
  button.classList.add("starting");   // a spinner in the robot's place, so the click plainly took -
                                       // the start takes a beat, and green waits for the log to be there
  rememberStarting(project, text);   // so the spinner survives a tab switch while the start is in flight
  pollStarting();                    // and turns green on its own once the desk records the agent
  try {
    const answer = await fetch("/task/take-care",
      { method: "POST", body: new URLSearchParams({ project, text }) });
    const agent = answer.ok ? (await answer.json()).agent : null;
    if (agent) {
      // The desk opens the log before it hands the name back, so by here the tab is there to open:
      // the spinner gives way to the green working link, which replaces the whole button.
      forgetStarting(project, text);
      turnGreen(button, agent);
      announceAsked(`On it — ${agent} is on this now`);
      return;
    }
    forgetStarting(project, text);   // a clean "nothing started" answer: drop the spinner and record
    button.disabled = false;
    button.classList.remove("starting");
  } catch {
    // Usually the page tearing down under a tab switch, which aborts this fetch - leave the record so
    // the poll resolves it on the page we land back on; the start may be proceeding server-side.
  }
});

/* On load, pick up any start still in flight from before a tab switch: show its spinner at once - no
   gray flash before the first poll - then let the poll turn it green (or drop it) against the server. */
const pending = startingTasks();
for (const key of Object.keys(pending)) {
  const [project, text] = key.split("\n");
  if (Date.now() - pending[key] > STARTING_TTL) { forgetStarting(project, text); continue; }
  const button = startButtonFor(project, text);
  if (button) { button.disabled = true; button.classList.add("starting"); }
}
pollStarting();

/* An agent's log links here with #task-<card>-<id>: bring that task into view and flash it, so it
   is obvious which one was meant rather than landing somewhere in a long card. The same "you
   landed here" highlight the conversation uses (.landed), and only for a moment. */
if (location.hash) {
  const task = document.getElementById(decodeURIComponent(location.hash.slice(1)));
  if (task) {
    task.scrollIntoView({ block: "center" });
    task.classList.add("landed");
    task.addEventListener("animationend", () => task.classList.remove("landed"), { once: true });
  }
}

/* Drag a row to reorder. The order the rows settle into is the order the cards are drawn in, saved
   on drop. Only within the project list - Excephalon is not part of it, and always leads. */
const rail = document.getElementById("project-rail");
if (rail) {
  let dragging = null;
  for (const row of rail.querySelectorAll(".rail-item")) {
    row.addEventListener("dragstart", () => { dragging = row; row.classList.add("dragging"); });
    row.addEventListener("dragend", () => { row.classList.remove("dragging"); dragging = null; });
  }
  rail.addEventListener("dragover", (event) => {
    event.preventDefault();  // a preventDefault here is what allows the drop
    if (!dragging) return;
    const below = [...rail.querySelectorAll(".rail-item:not(.dragging)")].find((row) => {
      const box = row.getBoundingClientRect();
      return event.clientY < box.top + box.height / 2;
    });
    if (below) rail.insertBefore(dragging, below);
    else rail.append(dragging);
  });
  rail.addEventListener("drop", async (event) => {
    event.preventDefault();
    const order = [...rail.querySelectorAll(".rail-item")].map((row) => row.dataset.name);
    await fetch("/project/reorder",
      { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ order }) });
    location.reload();  // redraw the cards in the order the rail now stands in
  });
}

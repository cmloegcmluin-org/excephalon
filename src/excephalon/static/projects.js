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
  const link = document.createElement("a");
  link.className = "agent-link working";
  link.href = `/agents#agent-${encodeURIComponent(agent)}`;
  link.title = `${agent} is on this — open its log`;
  while (button.firstChild) link.append(button.firstChild);
  button.replaceWith(link);
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
  try {
    const answer = await fetch("/task/take-care",
      { method: "POST", body: new URLSearchParams({ project, text }) });
    const agent = answer.ok ? (await answer.json()).agent : null;
    if (agent) {
      // The desk opens the log before it hands the name back, so by here the tab is there to open:
      // the spinner gives way to the green working link, which replaces the whole button.
      turnGreen(button, agent);
      announceAsked(`On it — ${agent} is on this now`);
      return;
    }
    button.disabled = false;   // nothing started; put the gray robot back to try again
    button.classList.remove("starting");
  } catch {
    button.disabled = false;
    button.classList.remove("starting");
  }
});

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

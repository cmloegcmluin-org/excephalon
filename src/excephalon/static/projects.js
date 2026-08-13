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

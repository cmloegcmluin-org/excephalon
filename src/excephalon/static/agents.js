/* Every agent's exchange on one page, each tailed as it works. */

const threads = [...document.querySelectorAll(".agent")].map((where) => ({ where, drawn: 0 }));

async function follow() {
  for (const agent of threads) {
    const shown = await (await fetch(
      `/agents/${encodeURIComponent(agent.where.dataset.agent)}?since=${agent.drawn}`)).json();
    drawInto(agent.where, shown.entries, shown.at);
    agent.drawn = shown.total;
  }
}

if (threads.length) {
  follow();
  setInterval(follow, 1000);  // an agent writes as it works, but not four times a second
}

/* A refused rename says so where it was refused. Putting the old name back without a word is
   how a rename he had every reason to expect came to read as a broken app - "the changes are back
   to failing to persist" - so the reason stands beside the name until he has read it. */
async function complain(where, answer) {
  const why = await answer.json().then((said) => said.why).catch(() => "");
  if (!where) return;
  where.querySelector(".refused")?.remove();
  const note = document.createElement("span");
  note.className = "refused";
  note.textContent = why || "that rename could not be made";
  where.append(note);
  setTimeout(() => note.remove(), 12000);  // long enough to be read, not long enough to litter
}

/* The rail: every log one click away. An active name scrolls its tab into view; an archived name
   UNARCHIVES the log - the reload redraws it as an ordinary tab - and the #hash carries which one
   to scroll to once it exists. The lists are server-drawn, so any change of membership (a close,
   a restore) reloads rather than patching the page by hand. */
for (const goes of document.querySelectorAll("#toc [data-goes]")) {
  goes.addEventListener("click", () => {
    // Not while it is being typed into: the click that placed the caret must not also scroll.
    if (goes.getAttribute("contenteditable")) return;
    document.getElementById(goes.dataset.goes)?.scrollIntoView({ block: "start" });
  });
}

for (const shelf of document.querySelectorAll("#toc [data-restore]")) {
  shelf.addEventListener("click", async () => {
    const name = shelf.dataset.restore;
    await fetch(`/agents/archived/${encodeURIComponent(name)}/restore`, { method: "POST" });
    location.assign(`/agents#agent-${encodeURIComponent(name)}`);
    location.reload();  // assign alone won't reload when only the hash differs
  });
}

if (location.hash) {
  // Two ways in: a restore reopening a tab, and a task on the Projects tab linking to the agent
  // that is on it. Either way the freshly-scrolled tab is flashed, the same "you landed here"
  // highlight (.landed) the conversation uses, so the destination is obvious rather than lost at
  // the top edge.
  const tab = document.getElementById(decodeURIComponent(location.hash.slice(1)));
  if (tab) {
    tab.scrollIntoView({ block: "start" });
    tab.classList.add("landed");
    tab.addEventListener("animationend", () => tab.classList.remove("landed"), { once: true });
  }
}

/* The rail's archive button does what the tab's ✕ does, from the list rather than the tab. */
for (const put of document.querySelectorAll("#toc [data-archive]")) {
  put.addEventListener("click", async () => {
    await fetch(`/agents/${encodeURIComponent(put.dataset.archive)}/close`, { method: "POST" });
    location.assign("/agents");
    location.reload();
  });
}

/* The same rename, from the rail: double-click a name, type, Enter or click away. An archived
   log has no tab to edit its name on, so this is the only door for those - and for a live one it
   saves the trip to its card. A single click still goes to the exchange. */
for (const label of document.querySelectorAll("#toc [data-rename]")) {
  const was = label.dataset.rename;
  label.addEventListener("dblclick", () => {
    label.setAttribute("contenteditable", "plaintext-only");
    // Focus on the NEXT turn of the loop, not in this handler: the browser's own double-click
    // word-selection runs after us, and taking the caret before it settled left the box focused
    // but deaf - he double-clicked, typed a whole name, and not one character landed in it.
    setTimeout(() => {
      label.focus();
      getSelection().selectAllChildren(label);
    }, 0);
  });
  const save = async () => {
    label.removeAttribute("contenteditable");
    const wanted = label.textContent.trim();
    if (!wanted || wanted === was) { label.textContent = was; return; }
    const road = label.dataset.archived ? `/agents/archived/${encodeURIComponent(was)}/rename`
                                       : `/agents/${encodeURIComponent(was)}/rename`;
    const answer = await fetch(road, { method: "POST", body: new URLSearchParams({ to: wanted }) });
    if (!answer.ok) { label.textContent = was; await complain(label.closest(".rail-row"), answer); return; }
    location.assign("/agents");
    location.reload();
  };
  label.addEventListener("blur", save);
  label.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); label.blur(); }
    if (event.key === "Escape") { label.textContent = was; label.blur(); }
  });
}

/* The name is his. Typing in a tab's heading and leaving it (or pressing Enter) saves it: the
   desk moves the log, re-keys its own record and re-tags any news waiting to be spoken, so the
   reload finds every mention of that agent under the new name. Escape puts back what was there. */
for (const heading of document.querySelectorAll(".rename")) {
  const was = heading.textContent.trim();
  const save = async () => {
    const wanted = heading.textContent.trim();
    if (!wanted || wanted === was) { heading.textContent = was; return; }
    const answer = await fetch(`/agents/${encodeURIComponent(was)}/rename`,
                               { method: "POST", body: new URLSearchParams({ to: wanted }) });
    if (!answer.ok) { heading.textContent = was; await complain(heading.parentElement, answer); return; }
    location.assign("/agents");
    location.reload();
  };
  heading.addEventListener("blur", save);
  heading.addEventListener("keydown", (event) => {
    if (event.key === "Enter") { event.preventDefault(); heading.blur(); }
    if (event.key === "Escape") { heading.textContent = was; heading.blur(); }
  });
}

/* Closing one archives its log, and the reload moves its name to the rail's Archived list - the
   archive is what makes the close stick: the roster is the log folder, so a log left in place
   comes back on the next poll. */
for (const shut of document.querySelectorAll(".shut")) {
  shut.onclick = async () => {
    const name = shut.dataset.agent;
    await fetch(`/agents/${encodeURIComponent(name)}/close`, { method: "POST" });
    location.assign("/agents");
    location.reload();
  };
}

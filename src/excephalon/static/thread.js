/* Drawing a conversation: the shape shared by Excephalon's own thread and every agent's.

   The server hands over entries that already know who said them and which side they belong on,
   so nothing here parses a transcript line. */

function element(entry) {
  // A break is a full-width row so it can be hovered anywhere along it, holding an inner mark
  // that is only as wide as what is drawn - which is what the copy button has to sit beside.
  if (entry.role === "day" || entry.role === "session") {
    const row = document.createElement("div");
    row.className = entry.role;
    const mark = document.createElement("span");
    mark.className = "mark";
    mark.append(entry.role === "day" ? entry.stamp : entry.label);
    row.append(mark);
    return row;
  }
  if (!entry.bubble) {
    const aside = document.createElement("div");
    aside.className = "aside";
    aside.append(entry.text);
    return aside;
  }
  const said = document.createElement("div");
  said.className = `said ${entry.side}${entry.historical ? " historical" : ""}`;
  const who = document.createElement("div");
  who.className = "who";
  who.append(`${entry.name} · ${entry.stamp}`);
  const box = document.createElement("div");
  box.className = "box";
  // What can be opened is decided on the server (links.py); the page only draws it. Reading a
  // path back off the screen to retype it is exactly what this saves.
  for (const part of entry.parts || [{ text: entry.text, link: "" }]) {
    if (!part.link) { box.append(part.text); continue; }
    const link = document.createElement("a");
    link.className = "opens";
    link.append(part.text);
    if (part.link.startsWith("/")) {
      // The app's own pages - an agent's name opening its log tab - navigate the window
      // itself; only real addresses and paths go through /open to the machine.
      link.href = part.link;
    } else {
      link.href = "#";
      link.title = part.link;
      link.onclick = (event) => {
        event.preventDefault();
        fetch("/open", { method: "POST", body: new URLSearchParams({ target: part.link }) });
      };
    }
    box.append(link);
  }
  said.append(who, box);
  return said;
}

/* Append what is new, and follow the live end only if that is where we already were. */
function drawInto(where, fresh, at) {
  // Nothing new means nothing to do. Following the live end on an EMPTY poll re-pinned the
  // thread to the bottom four times a second, which quietly cancelled every scroll the contents
  // started - the jump happened and was undone before it could be seen.
  if (!fresh.length) return;
  const atEnd = where.scrollTop + where.clientHeight >= where.scrollHeight - 40;
  fresh.forEach((entry, index) => {
    const node = element(entry);
    node.dataset.at = at + index;
    where.append(node);
  });
  if (atEnd) where.scrollTop = where.scrollHeight;
}

/* What copying an element means: a message is its own words; a break is the whole session it
   heads, up to the next break. */
function whatToCopy(node, entries) {
  const at = Number(node.dataset.at);
  if (entries[at].bubble) return entries[at].text;
  const said = [];
  for (const entry of entries.slice(at + 1)) {
    if (entry.role === "session" || entry.role === "day") break;
    said.push(entry.bubble ? `${entry.name} · ${entry.stamp}\n${entry.text}` : entry.text);
  }
  return said.join("\n");
}

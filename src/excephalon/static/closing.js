/* The window's own close question, and the restart button - the two ways this window ends.

   The native confirm was a light-mode system box inside a dark app, so it is off: the window's
   closing event calls askToClose() here and cancels itself, and only the dialog's Close button -
   through POST /quit, which the server answers by destroying the window - actually closes. */

const veil = document.getElementById("veil");
const keep = document.getElementById("keep-open");
const closing = document.getElementById("closing");
const restarting = document.getElementById("restarting");
const updating = document.getElementById("updating");
/* Set the moment a restart is actually CONFIRMED: the veil then says what is happening and stops
   being dismissable. A question can be waved away; a window already on its way out cannot. */
let leaving = false;

/* One veil, three dialogs, and only ever one of them up: the close question, the restart
   question, and the word that the restart is under way. */
function show(dialog) {
  closing.hidden = dialog !== closing;
  restarting.hidden = dialog !== restarting;
  updating.hidden = dialog !== updating;
  veil.hidden = false;
}

/* Called by the app when the X is pressed (see desktop.Controls.asked_to_close). */
function askToClose() {
  if (leaving) return;
  show(closing);
  keep.focus();
}

function dismiss() {
  if (!leaving) veil.hidden = true;
}

keep.addEventListener("click", dismiss);
document.getElementById("keep-running").addEventListener("click", dismiss);
addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !veil.hidden) dismiss();
});
veil.addEventListener("pointerdown", (event) => {
  if (event.target === veil) dismiss();   // a click off the dialog keeps the window
});

document.getElementById("really-close").addEventListener("click", () => {
  fetch("/quit", { method: "POST" });
});

/* A landed fix, one confirmation, and then running it. The process winds down exactly as a close
   does - goodbye, agents recorded - and its last act is to start a fresh one on the current code.

   The button ASKS first and waits. It used to wind the process down on the click itself, showing
   the Updating notice as it went - "I think the restart to upgrade button pops open the confirm
   dialog but skips it. it shouldn't skip it." A restart closes his window and takes the
   conversation on the screen down with it, so it is his to confirm, exactly as a close is. */
const restart = document.getElementById("restart");
restart.addEventListener("click", () => {
  show(restarting);
  document.getElementById("keep-running").focus();
});

document.getElementById("really-restart").addEventListener("click", () => {
  leaving = true;        // from here the veil cannot be waved away: nothing is left to decide
  show(updating);        // said BEFORE the request, so the wait is never a blank one
  fetch("/restart", { method: "POST" });
});

/* The button appears only when there is something to restart INTO: the checkout on disk has
   moved past the commit this process booted from. Checked on load and then once a minute -
   fixes land on the scale of minutes, and a button that is always there cries wolf. */
async function upgradeReady() {
  try {
    const { ready } = await (await fetch("/upgrade")).json();
    restart.hidden = !ready;
  } catch {
    /* an unreachable server means the window is going down; the button matters no more */
  }
}
upgradeReady();
setInterval(upgradeReady, 60000);

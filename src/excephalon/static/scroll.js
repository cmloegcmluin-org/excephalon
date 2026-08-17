/* Each reading tab remembers where it was scrolled to, so switching Projects -> Agents -> back to
   Projects lands where you were rather than at the top of the page.

   Every tab but the conversation is its own page load (see base.html), so navigating away tears the
   page down and drops its scroll with it - the same way it once dropped the half-written draft. The
   same sessionStorage that keeps the draft across that load keeps the scroll too, keyed by the tab's
   own path so each tab is remembered on its own rather than sharing one slot.

   The conversation is exempt by its nature, not by a special case here: it is a fixed-height grid
   whose thread follows the live end itself, so its document never scrolls - scrollTo(0, 0) is a
   no-op there and the inner thread is left to manage itself. */
const SCROLL_KEY = `excephalon:scroll:${location.pathname}`;
const scroller = document.scrollingElement || document.documentElement;

/* Put it back before the first paint, so the tab doesn't flash at the top and then jump down. A
   link into a specific row (#task-…, #agent-…) names where the page should land, and the page's own
   script scrolls there - so the remembered spot is only restored when arriving without such a
   target, which is exactly the plain tab-to-tab switch this is for. */
const wasAt = Number(sessionStorage.getItem(SCROLL_KEY)) || 0;
if (wasAt && !location.hash) scroller.scrollTo(0, wasAt);

/* Keep that remembered spot current as it scrolls. Passive: this only records the scroll, it never
   blocks it. */
addEventListener("scroll", () => sessionStorage.setItem(SCROLL_KEY, scroller.scrollTop),
                 { passive: true });

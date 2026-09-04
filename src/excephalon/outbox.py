"""The store of what he is owed, under the name every producer has always imported.

There used to be a queue here, a spool behind it, and a hand in the conversation that drained
the one and held items for a lull - three places for one piece of news, and every cross-place
failure in the record lived in the split. The store is `threads.Ledger` now: one place, read and
settled, never drained into anyone's keeping. This name stays so the desk, the narrator, the inbox
watcher, the errand hand, the scheduler and the window keep pushing to what they always pushed to.
"""

from excephalon.threads import CONCLUSIONS, Ledger, News

Outbox = Ledger

__all__ = ["CONCLUSIONS", "Ledger", "News", "Outbox"]

"""Where a piece of work stands between "described" and "landed" - tracked by code, not memory.

The describe -> deliver -> verify -> approve loop used to live entirely in the persona: the model
was ASKED to get see-it-running steps, ASKED to wait for a verdict, ASKED to wrap up afterwards.
On a good day it did all three. The stages here turn that order into a rule: a verdict cannot be
recorded for work that was never presented, work already approved and landing cannot be presented
again, and the steps the user needs are stored where every turn can read them rather than
remembered by whoever last spoke.
"""


class DeliveryError(ValueError):
    """A transition the loop does not allow - the message says what has to happen first."""


# The ladder every work thread climbs, in the user's own words - "it shouldn't be too hard for it
# to track 3 different things, whether they are unstarted, still in initial work, review, revision
# work, delivery work, or delivered." Named in one place so the briefing that renders a stage and
# the check that reads one back can never drift apart.
LADDER = ("unstarted (a list item with no agent on it) -> in work -> in review (presented, "
          "awaiting his verdict) -> in revision (sent back with his notes) -> landing (approved, "
          "being merged) -> DELIVERED (landed and wrapped up)")
IN_REVIEW = "in review"


class Delivery:
    """One piece of work's place in the loop: building -> ready (presented, steps on file) ->
    landing (approved, being merged). A rejection sends ready back to building, and is counted -
    "in revision" and "in work" are different answers to "where does it stand?", and the briefing
    that could not tell them apart is how a thread's state got retold from memory instead."""

    def __init__(self, stage="building", steps=None, rejections=0):
        self.stage = stage
        self.steps = steps
        self.rejections = rejections

    def present(self, steps):
        if self.stage == "landing":
            raise DeliveryError("that work is already approved and landing - nothing to present")
        self.stage = "ready"
        self.steps = steps

    def verdict(self, approved):
        if approved and self.stage == "landing":
            # Re-approving work already landing is agreement, not a transition: "The ship it
            # still stands." must land as a quiet yes, never as an error the brain then reads
            # back to him as machinery.
            return
        if self.stage != "ready":
            raise DeliveryError(
                "no verdict can be recorded - nothing has been presented for the user's eyes yet"
            )
        self.stage = "landing" if approved else "building"
        if not approved:
            self.steps = None  # rejected work returns with fresh steps, never yesterday's
            self.rejections += 1

    def describe(self):
        """The stage as a briefing phrase, always - every thread answers "where does it stand?".
        Being built used to earn no words at all, and a stage the briefing never stated was a
        stage the brain invented: it re-opened approval on delivered work and called unlanded
        work shipped, in one evening."""
        if self.stage == "ready":
            return f"{IN_REVIEW} - presented, awaiting his verdict"
        if self.stage == "landing":
            return "landing - approved, being merged now"
        if self.rejections:
            return "in revision - he sent it back with notes; presenting again when ready"
        return "in work - being built, not yet presented for his eyes"

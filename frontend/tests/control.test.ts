// Tests for the pure logic behind the stop control (T34).
//
// Run with `npm run test:logic` (`node --test`, which strips the types
// itself — no transpile step, no test framework added), same as
// fillerbotLogic.test.ts.
//
// The React button is a two-state widget with no logic worth testing; the
// part that can be wrong is the status -> outcome -> user-facing-message
// mapping, because every branch of it is a case the user sees *after*
// they have asked to quit and cannot retry blind:
//
//   - 202: the server really is going away.
//   - 501: it is NOT going away — started outside the launcher, so there
//     is nothing registered to stop it. Saying "stopped" here would be a
// lie that leaves a scheduler running (the product spec: a silently dead
//     scheduler is a defect; a silently *live* one the user believes is
//     dead is the same defect inverted).
//   - 401/403: the session or Origin gate rejected it
//     (jobfinder/web/security.py). Reloading from the launcher is the fix.
//   - a thrown fetch *after* a successful shutdown is expected, not an
//     error: the server can close the connection as it dies.
import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { stopMessage, stopOutcomeForStatus, type StopOutcome } from "../src/api/control.ts";

describe("stopOutcomeForStatus", () => {
  it("treats the documented 202 as a real shutdown", () => {
    assert.equal(stopOutcomeForStatus(202), "stopping");
  });

  it("treats any 2xx as a shutdown, so a status tweak does not look like a failure", () => {
    assert.equal(stopOutcomeForStatus(200), "stopping");
  });

  it("maps 501 to unsupported rather than success", () => {
    assert.equal(stopOutcomeForStatus(501), "unsupported");
  });

  it("maps the security gates to unauthorized", () => {
    assert.equal(stopOutcomeForStatus(401), "unauthorized");
    assert.equal(stopOutcomeForStatus(403), "unauthorized");
  });

  it("maps anything else to error", () => {
    assert.equal(stopOutcomeForStatus(500), "error");
    assert.equal(stopOutcomeForStatus(404), "error");
  });
});

describe("stopMessage", () => {
  it("never tells the user it stopped when it did not", () => {
    assert.match(stopMessage("unsupported"), /still running/i);
    assert.doesNotMatch(stopMessage("unsupported"), /has stopped/i);
  });

  it("tells the user the tab is safe to close once stopped", () => {
    assert.match(stopMessage("stopping"), /close this tab/i);
  });

  it("has a distinct message for every outcome", () => {
    const outcomes: StopOutcome[] = ["stopping", "unsupported", "unauthorized", "error"];
    const messages = outcomes.map(stopMessage);
    assert.equal(new Set(messages).size, outcomes.length);
    for (const message of messages) {
      assert.ok(message.length > 0);
    }
  });
});

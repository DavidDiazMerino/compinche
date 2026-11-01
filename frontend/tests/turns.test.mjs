import test from "node:test";
import assert from "node:assert/strict";
import { prepareTurn, pendingTurn, clearPendingTurn, deliverTurn } from "../../dist/turns.js";

function storage() {
  const values = new Map();
  return { getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value), removeItem: (key) => values.delete(key) };
}

test("a reloaded client retries exactly the same intent, including the answered question", () => {
  const local = storage();
  const payload = { text: "I stop here", evidence_ids: ["attempt"], answer_to: "question" };
  const first = prepareTurn("learner", payload, local, () => "stable-key");
  assert.deepEqual(pendingTurn("learner", local), first);
  assert.deepEqual(prepareTurn("learner", payload, local, () => "must-not-be-used"), first);
  assert.throws(() => prepareTurn("learner", { ...payload, answer_to: null }, local), /previous send/);
  assert.throws(() => prepareTurn("learner", { text: "Different" }, local), /previous send/);
});

test("an accepted send allows a deliberate later repetition with a new key", () => {
  const local = storage();
  const first = prepareTurn("session", { text: "Again" }, local, () => "first");
  clearPendingTurn("session", "wrong-key", local);
  assert.equal(pendingTurn("session", local).idempotency_key, "first");
  clearPendingTurn("session", first.idempotency_key, local);
  assert.equal(pendingTurn("session", local), null);
  assert.equal(prepareTurn("session", { text: "Again" }, local, () => "second").idempotency_key, "second");
});

test("storage failure happens before an intent can be sent", () => {
  const local = { getItem: () => null, setItem: () => { throw new Error("quota"); } };
  assert.throws(() => prepareTurn("session", { text: "Hello" }, local, () => "key"), /quota/);
});

test("an ambiguous timeout retains the intent until a receipt is recovered", async () => {
  const local = storage();
  let original;
  await assert.rejects(deliverTurn("session", { text: "Answer", answer_to: "question" },
    async (payload) => { original = payload; throw Object.assign(new Error("timeout"), { status: 408 }); },
    local, () => "retry-key"), /timeout/);
  const receipt = await deliverTurn("session", { text: "Answer", answer_to: "question" },
    async (payload) => { assert.deepEqual(payload, original); return { accepted: true }; }, local);
  assert.deepEqual(receipt, { accepted: true });
  assert.equal(pendingTurn("session", local), null);
});

test("a rejected validation allows the user to correct the next attempt", async () => {
  const local = storage();
  await assert.rejects(deliverTurn("session", { text: "Incorrect" },
    async () => { throw Object.assign(new Error("blocked"), { status: 422 }); }, local, () => "blocked-key"));
  assert.equal(pendingTurn("session", local), null);
  assert.equal(prepareTurn("session", { text: "Corrected" }, local, () => "new-key").idempotency_key, "new-key");
});

import test from "node:test";
import assert from "node:assert/strict";
import { selectedInterval, elapsedReplayInterval } from "../../dist/analysis.js";

test("a clip origin is not incorrectly used as a local seek position", () => {
  const evidence = { id: "clip", timestamp_seconds: 320, duration_seconds: 24 };
  assert.deepEqual(selectedInterval(evidence, "clip", 12), { start_seconds: 6, duration_seconds: 15 });
  assert.equal(selectedInterval(evidence, "another-video", 500).start_seconds, 0);
});

test("a replay observation only includes the interval already played", () => {
  assert.equal(elapsedReplayInterval(0), null);
  assert.deepEqual(elapsedReplayInterval(5), { start_seconds: 0, duration_seconds: 5 });
  assert.deepEqual(elapsedReplayInterval(332), { start_seconds: 320, duration_seconds: 12 });
  assert.equal(elapsedReplayInterval(Infinity), null);
});

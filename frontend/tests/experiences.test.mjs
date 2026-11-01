import test from "node:test";
import assert from "node:assert/strict";
import { deviceMode, replayClip, keptExplanation } from "../../dist/experiences.js";

test("screenless replay and phone are distinct modes without a hardware connection claim", () => {
  assert.equal(deviceMode("glasses-replay"), "glasses-replay");
  assert.equal(deviceMode("invented-device"), "phone");
});

test("replay selection keeps original time and clip-local time separate", () => {
  const clips = [{ start_seconds: 320, duration_seconds: 24, evidence_id: "clip" }];
  assert.equal(replayClip(clips, 320).local_seconds, 0);
  assert.equal(replayClip(clips, 332).local_seconds, 12);
  assert.equal(replayClip(clips, 344), null);
  assert.equal(replayClip(clips, NaN), null);
});

test("audio instructions require a reviewed explanation from the original lesson", () => {
  const data = { session: { id: "learner", source_session_id: "teacher" }, knowledge: [
    { id: "guess", session_id: "teacher", text: "A guess", confirmed: false },
    { id: "kept", session_id: "teacher", text: "The original explanation", confirmed: true },
    { id: "own", session_id: "learner", text: "My own observation", confirmed: true }] };
  assert.equal(keptExplanation(data, "guess"), null);
  assert.equal(keptExplanation(data, "own"), null);
  assert.equal(keptExplanation(data, "kept"), "The original explanation");
});

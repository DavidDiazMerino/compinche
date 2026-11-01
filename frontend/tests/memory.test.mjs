import test from "node:test";
import assert from "node:assert/strict";
import { searchKey, findPersonalNotes } from "../../dist/memory.js";

test("personal note queries preserve their own provenance and handle accents", () => {
  const notes = { own: "José mira cómo cambia el frío", example: "My memory of the story" };
  assert.deepEqual(findPersonalNotes(notes, " "), []);
  const found = findPersonalNotes(notes, "Jose");
  assert.equal(found[0].kind, "local_note");
  assert.equal(found[0].confirmed, undefined);
  assert.equal(findPersonalNotes(notes, "story", ["example"])[0].title, "Your note on an illustrative example");
  assert.equal(searchKey("Straße"), searchKey("STRASSE"));
});

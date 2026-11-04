"""A voice note answered after the session becomes the card for its question, with the teacher's words."""
from pathlib import Path
import tempfile
import unittest

from backend.store import Store
from backend.voice_answer import attach, find_question


class Provider:
    model = "fake-for-tests-only"
    def __init__(self):
        self.learn, self.recipe, self.transcribed = [], [], []
    async def live_learn(self, context):
        self.learn.append(context)
        return {"summary": "Baja el fuego para que no se queme.", "signal": None, "reported_sensation": None,
                "decision": "Bajar el fuego", "category": "fuego", "title": "Cuándo bajar el fuego"}
    async def live_recipe(self, context):
        self.recipe.append(context)
        return {"ingredients": [{"name": "harina", "quantity": "ochenta gramos"}]}
    async def live_transcribe(self, path, duration, language="es", keywords=None):
        self.transcribed.append((path, language))
        return {"text": "  Para que no se arrebate.  ", "model": "muse-voice-transcribe-1.0"}


class VoiceAnswerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = Store(root)
        self.sid = self.store.create_session({"title": "Bechamel", "live": {"config": {"language": "en", "recipe": {"dish": "Béchamel"}}}})["id"]
        self.question = self.store.put(self.sid, "question", {"text": "When you turned the heat down, what told you?",
            "status": "expired", "action": "Bajar el fuego", "cue": "Burbujas", "source_start_seconds": 688.0,
            "source_end_seconds": 693.0, "scene_epoch": 0, "evidence_ids": ["frame-1"]})
        self.audio = root / "nota.wav"
        self.audio.write_bytes(b"RIFF")
        self.media = root / "media"

    async def asyncTearDown(self):
        self.store.close()
        self.tmp.cleanup()

    async def test_confirmed_card_quotes_the_teacher_and_answers_the_question(self):
        provider = Provider()
        card = await attach(self.store, provider, self.sid, self.question, self.audio, text=" El fuego se baja para que no se arrebate. ",
                            speaker="Papá", confirmed_by="Papá (nota de voz)", media_dir=self.media)
        self.assertEqual(card["quote"], "El fuego se baja para que no se arrebate.")
        self.assertTrue(card["confirmed"])
        self.assertEqual(card["reviewer_verification"], "user_declared")
        self.assertEqual(card["created_by"], "voice_note_answer")
        self.assertEqual(card["decision_card"]["title"], "Cuándo bajar el fuego")
        self.assertEqual(card["decision_card"]["source_start_seconds"], 688.0)
        question = self.store.get(self.question["id"], "question")
        self.assertEqual(question["status"], "answered")
        self.assertEqual(question["answer_speaker"], "Papá")
        note = self.store.get(question["answer_note_id"], "note")
        self.assertEqual(note["origin"], "voice_note")
        self.assertTrue(note["provenance"]["answered_after_session"])
        audio = self.store.get(note["evidence_ids"][0], "evidence")
        self.assertTrue(Path(audio["path"]).exists())
        self.assertEqual(provider.learn[0]["human_explanation"], "El fuego se baja para que no se arrebate.")
        self.assertEqual(provider.learn[0]["language"], "es")
        self.assertEqual(provider.recipe, [])

    async def test_without_reviewer_the_card_stays_unconfirmed(self):
        card = await attach(self.store, Provider(), self.sid, self.question, self.audio, text="Para que no se arrebate.",
                            speaker="Papá", media_dir=self.media)
        self.assertFalse(card["confirmed"])
        self.assertNotIn("confirmed_by", card)

    async def test_without_text_it_transcribes_and_marks_it_unverified(self):
        provider = Provider()
        import backend.voice_answer as module
        original, module._duration = module._duration, lambda path: 6.0
        try:
            card = await attach(self.store, provider, self.sid, self.question, self.audio, speaker="Papá", media_dir=self.media)
        finally:
            module._duration = original
        self.assertEqual(card["quote"], "Para que no se arrebate.")
        note = self.store.get(card["decision_card"]["answer_note_id"], "note")
        self.assertEqual(note["provenance"]["text_source"], "muse-voice-transcribe-1.0")
        self.assertFalse(note["provenance"]["human_verified"])

    async def test_recipe_merges_quantities_in_the_session_language(self):
        provider = Provider()
        await attach(self.store, provider, self.sid, self.question, self.audio, text="Ochenta gramos de harina.",
                     speaker="Papá", recipe=True, media_dir=self.media)
        self.assertEqual(provider.recipe[0]["language"], "en")
        recipe = self.store.get_session(self.sid)["live"]["config"]["recipe"]
        self.assertEqual(recipe["dish"], "Béchamel")
        self.assertEqual(recipe["ingredients"][0]["quantity"], "ochenta gramos")

    def test_question_prefix_must_match_one_question(self):
        self.assertEqual(find_question(self.store, self.sid, self.question["id"][:8])["id"], self.question["id"])
        with self.assertRaises(SystemExit):
            find_question(self.store, self.sid, "zzzz")


if __name__ == "__main__":
    unittest.main()

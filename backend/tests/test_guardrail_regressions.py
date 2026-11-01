import pytest

from backend.agent import GuardrailFailure, validate_output


def output(**changes):
    return {"reply": "Entendido.", "evidence_ids": [], "question": None,
            "knowledge": [], "segmentation_targets": [], "uncertainty": [], **changes}


@pytest.mark.parametrize("text", [
    "Veo en el vídeo que la bechamel está lista.", "En el video se ve que remueve.",
    "He observado el gesto.", "Se observa una cuchara.", "I can see the pan.",
])
def test_direct_visual_assertions_need_active_media_in_both_languages(text):
    with pytest.raises(GuardrailFailure, match="direct observation"):
        validate_output(output(reply=text), {}, set(), None, False)
    assert validate_output(output(reply=text, evidence_ids=["clip"]),
                           {"clip": {"kind": "media", "text": ""}}, {"clip"}, None, False)


def test_inferred_label_cannot_hide_a_direct_visual_assertion():
    claim = {"text": "Veo la masa lista.", "source": "inferred", "evidence_ids": ["note"],
             "quote": None, "uncertainty": "Sin medir."}
    with pytest.raises(GuardrailFailure, match="direct observation"):
        validate_output(output(knowledge=[claim]), {"note": {"kind": "note", "text": "Estamos cocinando."}}, set(), None, False)


@pytest.mark.parametrize("temperature", ["180 ºC", "180 °C", "180º C", "180°C"])
def test_unsupported_temperature_is_rejected_regardless_of_degree_glyph(temperature):
    claim = {"text": f"El aceite está a {temperature}.", "source": "inferred", "evidence_ids": ["note"],
             "quote": None, "uncertainty": "Hipótesis sin medir."}
    with pytest.raises(GuardrailFailure, match="physical quantity"):
        validate_output(output(knowledge=[claim]), {"note": {"kind": "note", "text": "Estamos friendo."}}, set(), None, False)
    assert validate_output(output(reply=f"Tu nota indica {temperature}.", evidence_ids=["note"]),
                           {"note": {"kind": "note", "text": "Lo medí: 180 °C."}}, set(), None, False)


def test_reporting_an_exact_visual_quote_does_not_pretend_to_watch_a_video():
    refs = {"note": {"kind": "note", "text": "Veo que la masa está lista."}}
    result = validate_output(output(reply="Tu nota dice «Veo que la masa está lista.». No he recibido un vídeo.",
                                    evidence_ids=["note"]), refs, set(), None, False)
    assert result.reply.startswith("Tu nota")
    with pytest.raises(GuardrailFailure, match="direct observation"):
        validate_output(output(reply="Tu nota dice «Veo el aceite dorado»."), refs, set(), None, False)


def test_missing_media_acknowledgement_is_not_rejected_as_an_observation():
    assert validate_output(output(reply="No veo ningún vídeo adjunto."), {}, set(), None, False)
    with pytest.raises(GuardrailFailure, match="direct observation"):
        validate_output(output(reply="No lo sé. Veo una cuchara."), {}, set(), None, False)


def test_no_solo_is_not_a_negation_of_an_observation():
    with pytest.raises(GuardrailFailure, match="direct observation"):
        validate_output(output(reply="No solo veo el aceite dorado, también humo."), {}, set(), None, False)


@pytest.mark.parametrize("field", ["text", "reason"])
def test_questions_cannot_hide_unsupported_observations(field):
    question = {"text": "¿Puedes añadir evidencia?", "reason": "Falta el vídeo.", "evidence_ids": []}
    question[field] = "Veo en el vídeo que la bechamel está lista."
    with pytest.raises(GuardrailFailure, match="direct observation"):
        validate_output(output(question=question), {}, set(), None, False)


def test_a_said_label_does_not_allow_observation_with_an_unrelated_quote():
    refs = {"note": {"kind": "note", "text": "Estoy friendo."}}
    claim = {"text": "Veo el aceite dorado.", "source": "said", "evidence_ids": ["note"],
             "quote": "Estoy friendo", "uncertainty": "Sin verificar."}
    with pytest.raises(GuardrailFailure, match="direct observation"):
        validate_output(output(knowledge=[claim]), refs, set(), None, False)
    quote = "Veo que la masa está lista."
    refs["note"]["text"] = quote
    assert validate_output(output(knowledge=[{**claim, "text": quote, "quote": quote}]), refs, set(), None, False)

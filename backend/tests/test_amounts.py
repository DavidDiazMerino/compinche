"""Quantities read the same in digits and in spoken words, in Spanish and English."""
from types import SimpleNamespace

from backend.live import LiveCoordinator, amounts, invents_amount, numbers


def test_spoken_and_written_quantities_are_the_same_amount():
    assert amounts("ochenta gramos de harina") == amounts("80 g") == {(80, "g")}
    assert amounts("un litro de leche") == amounts("a litre") == amounts("1000 ml")
    assert amounts("treinta y cinco minutos") == {(2100, "s")}
    assert amounts("two hundred grams") == {(200, "g")}
    assert amounts("half a litre") == amounts("medio litro") == {(500, "ml")}
    assert amounts("1/2 cup") == {(.5, "cup")}
    assert amounts("dos vasos") == {(2, "glass")} and amounts("una pizca") == {(1, "pinch")}


def test_words_that_only_look_like_amounts_are_not_quantities():
    assert amounts("al fuego") == set()  # «a» + «l» is not one litre
    assert amounts("todos los gramos") == set()
    assert amounts("ponlo al fuego medio") == set()


def test_a_derived_quantity_must_come_from_the_teachers_words():
    assert not invents_amount(["Use 80 g of flour"], "Ochenta gramos de harina.")
    assert invents_amount(["Use three tablespoons of flour"], "Ochenta gramos de harina.")
    assert invents_amount(["doscientos gramos"], "Un poco de mantequilla.")


def test_numbers_in_words_and_digits():
    assert numbers("twenty-eight centimetres") == [28]
    assert numbers("El brick es de 1 litro y la sartén 28") == [1, 28]
    assert numbers("un litro") == []  # an article is not a number on its own


def stated(text):
    run = SimpleNamespace(config={}, measurements=["old"], last_measure=0.0)
    return LiveCoordinator._take_stated_size(None, run, text), run.config.get("stated_vessel_cm")


def test_the_cook_can_say_the_pan_size_in_words_or_next_to_other_numbers():
    assert stated("It's 28 centimetres.") == (True, 28)
    assert stated("twenty-eight centimetres") == (True, 28)
    assert stated("Veintiocho") == (True, 28)
    assert stated("El brick es de 1 litro y la sartén 28") == (True, 28)
    assert stated("11 inches") == (True, 27.94)
    assert stated("una de 28 y otra de 31") == (False, None)  # two sizes: ask again rather than guess
    assert stated("no lo sé") == (False, None)

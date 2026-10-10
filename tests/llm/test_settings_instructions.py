"""The candidate's own directions reach every task's system prompt, after the built-in rules."""

from autoapp.evaluation.prompt import build_system as evaluation_system
from autoapp.llm.settings import EXTRA_INSTRUCTIONS_INTRO, instructions_block
from autoapp.writing.prompt import FINAL_REMINDER, WritingContext
from autoapp.writing.prompt import build_system as writing_system


def text(blocks) -> str:
    return "\n".join(b.text for b in blocks)


def test_no_directions_no_block() -> None:
    assert instructions_block("") == [] and instructions_block("  \n") == []
    assert text(evaluation_system("p", "cv")) == text(evaluation_system("p", "cv", ""))


def test_evaluate_gets_the_directions_last() -> None:
    blocks = evaluation_system("p", "cv", "Only Python roles.")
    assert "Only Python roles." in blocks[-1].text and EXTRA_INSTRUCTIONS_INTRO in blocks[-1].text
    assert blocks[-1].stable


def test_writer_gets_the_directions_before_the_closing_reminder() -> None:
    blocks = writing_system(WritingContext(profile_text="p", cv_text="cv", task_instructions="Be warm."))
    assert "Be warm." in text(blocks)
    assert blocks[-1].text == FINAL_REMINDER and "Be warm." in blocks[-2].text

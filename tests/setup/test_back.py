"""The "Back" choice on yes/no questions (setup asks the question before it again)."""

from __future__ import annotations

from typing import Any

from autoapp.setup.prompts import BACK, ScriptedPrompter
from autoapp.setup.wizard import Section, SetupContext, _run_with_back


class BackPrompter(ScriptedPrompter):
    """A scripted prompter whose yes/no questions offer Back, as the terminal one does."""

    def __init__(self, answers: list[Any]) -> None:
        super().__init__(answers)
        self.offered_back: list[str] = []

    def confirm_or_back(self, message: str, default: bool = True) -> Any:
        self.offered_back.append(message)
        item = self._next("confirm", message)
        return default if item is None else item


def run(answers, body):
    prompter = BackPrompter(answers)
    section = Section("t", "T", "", body, lambda ctx: None)
    ctx = SetupContext(ws=None, prompter=prompter, checks=None)  # type: ignore[arg-type]
    return prompter, _run_with_back(ctx, section)


def test_back_asks_the_previous_question_again_without_repeating_older_ones():
    seen: list[str] = []

    def body(ctx):
        p = ctx.prompter
        p.say("intro")
        first = p.text("First?")
        p.say("after first")
        second = p.text("Second?")
        ok = p.confirm("Keep these?")
        seen.append(f"{first}/{second}/{ok}")
        return True

    prompter, finished = run(["a", "b", BACK, "b2", True], body)
    assert finished and seen == ["a/b2/True"]
    assert [m for k, m in prompter.asked if k == "text"] == ["First?", "Second?", "Second?"]  # "First?" is not asked twice
    # Older output is not repeated; what introduces the re-asked question is shown again.
    assert prompter.output.count("intro") == 1 and prompter.output.count("after first") == 2
    assert prompter.offered_back == ["Keep these?", "Keep these?"]


def test_back_can_go_further_back_and_the_first_question_has_no_back():
    def body(ctx):
        p = ctx.prompter
        a = p.confirm("Want A?")
        b = p.confirm("Want B?")
        p.confirm("Want C?")
        return a and b

    prompter, finished = run([True, True, BACK, False, BACK, True, True, True], body)
    assert finished
    assert "Want A?" not in prompter.offered_back  # nothing before it to go back to


def test_back_with_a_plain_prompter_is_simply_a_normal_question():
    prompter = ScriptedPrompter([True, False])
    section = Section("t", "T", "", lambda ctx: ctx.prompter.confirm("A?") and not ctx.prompter.confirm("B?"), lambda ctx: None)
    assert _run_with_back(SetupContext(ws=None, prompter=prompter, checks=None), section)  # type: ignore[arg-type]

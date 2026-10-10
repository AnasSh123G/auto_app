"""Shared schema for the LLM tests."""

from __future__ import annotations

from pydantic import BaseModel


class Requirement(BaseModel):
    text: str
    core: bool = False


class Evaluation(BaseModel):
    fit_score: int
    rationale: str
    requirements: list[Requirement] = []

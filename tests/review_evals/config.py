"""Eval model config. Generator and judge models come from env so they can be pinned per run."""
from __future__ import annotations

import os


def generator_model() -> str | None:
    return os.environ.get("REVIEW_GENERATOR_MODEL") or None


def judge_model() -> str | None:
    return os.environ.get("REVIEW_JUDGE_MODEL") or None

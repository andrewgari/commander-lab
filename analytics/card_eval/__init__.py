"""Single-card evaluation within a deck (EDHREC + Hugging Face). See docs/CARD_EVAL.md."""
from .config import ScoringWeights
from .schema import Candidate, DeckContext, EdhrecStats, EvaluationResult, RoleScore
from .service import CardEvalError, InvalidDeckError, UnknownCardError, evaluate_card

__all__ = ["evaluate_card", "DeckContext", "EvaluationResult", "RoleScore", "EdhrecStats", "Candidate",
           "ScoringWeights", "CardEvalError", "UnknownCardError", "InvalidDeckError"]

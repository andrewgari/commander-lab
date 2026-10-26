"""Single-card evaluation within a deck (EDHREC + Hugging Face). See docs/CARD_EVAL.md."""
from .config import ScoringWeights
from .schema import Candidate, DeckContext, EdhrecStats, EvaluationResult, RoleScore, SlotGate
from .service import CardEvalError, InvalidDeckError, UnknownCardError, evaluate_card

__all__ = ["evaluate_card", "DeckContext", "EvaluationResult", "RoleScore", "EdhrecStats", "Candidate",
           "SlotGate", "ScoringWeights", "CardEvalError", "UnknownCardError", "InvalidDeckError"]

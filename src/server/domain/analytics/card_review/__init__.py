"""Single Card Review: adversarial keep/remove debate + replacement suggester. See docs/CARD_REVIEW.md."""
from .config import rounds_from_env
from .debate import DebateConfig, final_confidence, run_debate, run_judge
from .llm import DebateLLM, OracleLLM
from .packet import ReviewContext, build_packet
from .replacements import suggest_replacements
from .review import ReviewNotAllowed, run_review

__all__ = ["run_review", "ReviewContext", "ReviewNotAllowed", "DebateLLM", "OracleLLM", "DebateConfig",
           "build_packet", "run_debate", "run_judge", "final_confidence", "suggest_replacements",
           "rounds_from_env"]

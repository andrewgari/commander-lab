"""
Hugging Face role classifier for card evaluation.

Zero-shot classification of a card's oracle text against config.ROLE_LABELS through the HF
Inference API (the repo already uses HF_TOKEN for the mech-similar bucket; the token is never
baked into the image). Configuration (env):

    CARD_EVAL_HF_MODE    api (default when HF_TOKEN is set) | off
    CARD_EVAL_HF_MODEL   default facebook/bart-large-mnli
    CARD_EVAL_HF_URL     default https://router.huggingface.co/hf-inference/models/{model}
    HF_TOKEN             bearer token
    CARD_EVAL_HF_TIMEOUT seconds, default 20

Failures (429 after retries, 503 model loading, timeouts, bad shape) raise HFError; the service
catches it and falls back to heuristic roles, so an HF outage degrades the evaluation, not breaks it.
"""
from __future__ import annotations

import os
import time
from typing import Callable, Dict, Mapping, Optional

import requests

from .config import ROLE_LABELS

DEFAULT_MODEL = "facebook/bart-large-mnli"
DEFAULT_URL = "https://router.huggingface.co/hf-inference/models/{model}"


class HFError(Exception):
    pass


class HFClassifier:
    def __init__(self, token: Optional[str] = None, model: Optional[str] = None, url: Optional[str] = None,
                 mode: Optional[str] = None, timeout: Optional[float] = None, max_retries: int = 3,
                 session: Optional[requests.Session] = None, sleep_fn: Callable[[float], None] = time.sleep,
                 env: Optional[Mapping[str, str]] = None):
        env = os.environ if env is None else env
        self.token = token if token is not None else env.get("HF_TOKEN")
        self.model_id = model or env.get("CARD_EVAL_HF_MODEL") or DEFAULT_MODEL
        self.url = (url or env.get("CARD_EVAL_HF_URL") or DEFAULT_URL).format(model=self.model_id)
        self.mode = (mode or env.get("CARD_EVAL_HF_MODE") or ("api" if self.token else "off")).lower()
        try:
            self.timeout = float(timeout if timeout is not None else env.get("CARD_EVAL_HF_TIMEOUT", 20))
        except ValueError:
            self.timeout = 20.0
        self.max_retries = max(1, max_retries)
        self._session = session or requests.Session()
        self._sleep = sleep_fn

    @property
    def enabled(self) -> bool:
        return self.mode == "api" and bool(self.token)

    def classify(self, text: str) -> Dict[str, float]:
        """{role: confidence 0..1} (multi-label). Raises HFError."""
        if not self.enabled:
            raise HFError("HF classifier disabled (set HF_TOKEN / CARD_EVAL_HF_MODE=api)")
        if not (text or "").strip():
            raise HFError("no card text to classify")
        by_label = {v: k for k, v in ROLE_LABELS.items()}
        body = {"inputs": text[:1500], "parameters": {"candidate_labels": list(ROLE_LABELS.values()),
                                                       "multi_label": True}}
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        last = "unknown"
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._session.post(self.url, json=body, headers=headers, timeout=self.timeout)
            except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
                last = f"network: {exc}"
            except requests.exceptions.RequestException as exc:
                raise HFError(f"HF request failed: {exc}") from exc
            else:
                if resp.status_code == 200:
                    try:
                        return self._parse(resp.json(), by_label)
                    except (ValueError, KeyError, TypeError) as exc:
                        raise HFError(f"unexpected HF response shape: {exc}") from exc
                if resp.status_code in (401, 403):
                    raise HFError(f"HF auth failed (HTTP {resp.status_code})")
                if resp.status_code == 429 or resp.status_code == 503 or resp.status_code >= 500:
                    last = f"HTTP {resp.status_code}"
                    if attempt < self.max_retries:
                        try:
                            wait = float(resp.headers.get("Retry-After", 0))
                        except (TypeError, ValueError):
                            wait = 0.0
                        self._sleep(min(max(wait, 0.5 * 2 ** (attempt - 1)), 10.0))
                    continue
                raise HFError(f"HF returned HTTP {resp.status_code}")
            if attempt < self.max_retries:
                self._sleep(0.5 * 2 ** (attempt - 1))
        raise HFError(f"HF request failed after {self.max_retries} attempts ({last})")

    @staticmethod
    def _parse(data, by_label: Dict[str, str]) -> Dict[str, float]:
        if isinstance(data, dict) and "labels" in data:          # legacy shape
            pairs = zip(data["labels"], data["scores"])
        elif isinstance(data, list):                              # [{label, score}, ...]
            pairs = [(d["label"], d["score"]) for d in data]
        else:
            raise ValueError("neither labels/scores nor [{label,score}]")
        out = {by_label[l]: max(0.0, min(1.0, float(s))) for l, s in pairs if l in by_label}
        if not out:
            raise ValueError("no known role labels in response")
        return out

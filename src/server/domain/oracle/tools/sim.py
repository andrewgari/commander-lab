"""sim_run: reuse a fresh saved simulation or run one Standard-pod sim (spec §2.7)."""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, Mapping, Optional

from ...analytics import sim_diff, sim_service, sim_store
from ...analytics.sim_client import SimBusy, SimUnavailable
from ..registry import Tool, ToolError
from .context import ToolContext


def _no_review():
    raise RuntimeError("oracle sim_run never starts an LLM review")


_NO_REVIEW = _no_review


class SimRunner:
    def __init__(self, r, deck: Mapping[str, Any], *, client=None, max_wait_s: float = 900.0,
                 sleep: Callable[[float], None] = time.sleep):
        self.r, self.deck, self.client = r, deck, client
        self.max_wait_s, self.sleep = max_wait_s, sleep

    def _comparison(self, report: Mapping) -> dict:
        static = sim_service._static_review(self.r, self.deck)
        return sim_service._slim_comparison(sim_diff.build_sim_comparison(dict(self.deck), report, static))

    def _matches_pod(self, report: Mapping) -> bool:
        """A saved run only counts when it was a Standard run against the current saved pod."""
        params = report.get("params") or {}
        pod = sim_service.get_pod(self.r)
        saved = (params.get("pod") or {}).get("saved_at")
        return params.get("opponent_mode") == "standard" and pod is not None and bool(saved) \
            and saved == pod.get("updated_at")

    def run(self, games: int) -> Dict[str, Any]:
        doc = sim_store.load_latest(self.r, self.deck, persist_stale=False)
        if doc and not (doc.get("meta") or {}).get("stale") and isinstance(doc.get("report"), Mapping) \
                and self._matches_pod(doc["report"]):
            report = doc["report"]
            have = (report.get("summary") or {}).get("games") or (doc.get("meta") or {}).get("games") or 0
            if isinstance(have, (int, float)) and have >= games:
                return {"source": "saved", "saved_at": (doc.get("meta") or {}).get("saved_at"),
                        "games": have, "comparison": self._comparison(report)}
        try:
            job = sim_service.begin_run(self.r, self.deck, {"opponent_mode": "standard", "games": games,
                                                            "notes": "oracle tool run"}, self.client,
                                         persist_focus=False)
            job = sim_service.run_job(self.r, job, self.deck, self.client, oracle_factory=_NO_REVIEW,
                                      max_wait=self.max_wait_s, sleep=self.sleep, skip_review=True)
        except sim_service.SimParamError as exc:
            raise ToolError("sim_not_configured", str(exc)[:200])
        except (sim_service.SimRunBusy, SimBusy):
            raise ToolError("sim_busy", "a simulation is already running (for this deck or on the simulator)")
        except SimUnavailable:
            raise ToolError("sim_unavailable", "the simulator is unavailable")
        except TimeoutError:
            raise ToolError("sim_timeout", "the simulation did not finish in time")
        if job.get("status") != "done":
            kind = job.get("error_kind")
            if kind == "sim_unavailable":
                raise ToolError("sim_unavailable", "the simulator is unavailable")
            if "TimeoutError" in str(job.get("error") or ""):
                raise ToolError("sim_timeout", "the simulation did not finish in time")
            raise ToolError("sim_failed", str(job.get("error") or "simulation failed")[:200])
        saved = sim_store.load_latest(self.r, self.deck, persist_stale=False) or {}
        report = saved.get("report") or {}
        return {"source": "new_run", "job_id": job.get("job_id"), "games": job.get("games_total", games),
                "comparison": self._comparison(report)}


def _sim_run(args: dict, ctx: ToolContext) -> dict:
    if ctx.sim is None:
        raise ToolError("unavailable", "simulation is not available in this context")
    return ctx.sim.run(args["games"])


def sim_run_tool() -> Tool:
    return Tool(
        name="sim_run",
        description=("Playtest results for this deck against the player's Standard pod. Reuses a fresh saved "
                     "simulation when one has enough games, otherwise runs a new one (can take minutes). "
                     "Returns win rates and card/mana performance. Call at most once."),
        parameters={"type": "object", "properties": {"games": {"type": "integer", "enum": [5, 20]}},
                    "required": ["games"], "additionalProperties": False},
        handler=_sim_run,
        max_calls=1,
        max_result_chars=16_000,
    )

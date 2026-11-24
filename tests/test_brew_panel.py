"""Brew reviewer panel: lens votes, critic only on challenged picks, deterministic reconciliation,
truncation handling and graceful degradation. LLM is mocked (no network)."""
import json

from src.server.domain.draft import brew_panel as bp
from src.server.domain.llm.port import LLMError, LLMTruncatedError

CTX = {"commanders": ["Atraxa"], "target_power": 6, "playstyle": "quirky", "template": ["ramp: 10"], "deck": []}
PROPS = ["A", "B", "C", "D"]


class FakeClient:
    """script: {role: reply-dict | Exception | callable(max_tokens)}; roles are lens names or 'critic'."""

    def __init__(self, script):
        self.script, self.calls = script, []

    def chat(self, messages, max_tokens=None):
        text = messages[1]["content"]
        role = "critic" if "ROLE: critic" in text else text.split("lens = ")[1].split("\n")[0]
        self.calls.append((role, max_tokens))
        out = self.script.get(role, {"votes": [], "suggest": []})
        if callable(out):
            out = out(max_tokens)
        if isinstance(out, Exception):
            raise out
        return json.dumps(out)


def cut(*cards):
    return {"votes": [{"card": c, "vote": "cut", "reason": f"bad {c}"} for c in cards], "suggest": []}


def run(script, props=PROPS, cands=("X", "Y"), stage="deck", **kw):
    client = FakeClient(script)
    return client, bp.review_stage(stage, CTX, props, cands, client=client, **kw)


def test_unanimous_cut_needs_no_critic():
    client, out = run({l: cut("A") for l in bp.LENSES})
    assert out.cuts == {"A"}
    assert "critic" not in [c[0] for c in client.calls]
    assert out.record["critic"]["status"] == "skipped"


def test_contested_pick_goes_to_critic_and_critic_breaks_the_tie():
    # 2 of 4 reviewers cut B -> contested; critic sides with the cut => 3 cut vs 2 keep... cut wins.
    client, out = run({"power_consistency": cut("B"), "synergy_flavor": cut("B"),
                       "critic": {"attacks": [{"card": "B", "verdict": "cut", "reason": "filler"}]}})
    assert [c[0] for c in client.calls].count("critic") == 1
    assert out.cuts == {"B"} and out.record["contested"] == ["B"]
    assert out.record["decisions"][0]["critic"] == "cut"


def test_critic_standing_keeps_a_split_pick():
    _, out = run({"power_consistency": cut("B"), "synergy_flavor": cut("B"),
                  "critic": {"attacks": [{"card": "B", "verdict": "stand", "reason": "fine"}]}})
    assert out.cuts == set()  # 2 cut vs 2 keep + critic stand: tie keeps


def test_uncontested_picks_never_reach_the_critic():
    client, out = run({l: {"votes": [], "suggest": []} for l in bp.LENSES})
    assert out.cuts == set() and "critic" not in [c[0] for c in client.calls]


def test_high_impact_pick_with_one_cut_is_challenged_even_if_few_reviewers_agree():
    client, out = run({"power_consistency": cut("C"),
                       "critic": {"attacks": [{"card": "C", "verdict": "stand", "reason": "ok"}]}}, high_impact=["C"])
    assert "critic" in [c[0] for c in client.calls] and out.cuts == set()


def test_protected_pick_needs_reviewer_majority_not_just_critic():
    _, out = run({"power_consistency": cut("B"),
                  "critic": {"attacks": [{"card": "B", "verdict": "cut", "reason": "meh"}]}}, protected=["B"])
    assert out.cuts == set()  # 1 reviewer cut + critic = 2 vs 3 keeps; also protected


def test_lone_reviewer_cannot_cut_by_itself():
    script = {"power_consistency": cut("A"), "synergy_flavor": LLMError("down"), "mana_base": LLMError("down"),
              "playstyle_fit": LLMError("down"), "critic": LLMError("down")}
    _, out = run(script)
    assert out.cuts == set()
    assert out.record["lenses"]["mana_base"]["status"] == "unavailable"


def test_truncated_reply_retries_with_larger_budget_then_degrades():
    seen = []

    def trunc_once(mt):
        seen.append(mt)
        if mt == bp.PanelBudget().max_tokens:
            raise LLMTruncatedError("cut off")
        return cut("A")

    client, out = run({"power_consistency": trunc_once, **{l: cut("A") for l in bp.LENSES if l != "power_consistency"}})
    assert seen == [2500, 8000] and out.cuts == {"A"}

    def always(mt):
        raise LLMTruncatedError("cut off")

    _, out = run({"power_consistency": always, "synergy_flavor": always, "mana_base": always, "playstyle_fit": always})
    assert out.record["status"] == "unavailable" and out.cuts == set()


def test_budget_caps_calls_and_critic_picks():
    big = bp.PanelBudget(max_calls=2)
    client, out = run({"power_consistency": cut("A"), "synergy_flavor": cut("A")}, budget=big)
    assert len(client.calls) == 2  # two lenses used the budget; the others are 'unavailable'
    assert out.record["calls"] == 2 and sum(v["status"] == "unavailable" for v in out.record["lenses"].values()) == 2
    assert out.cuts == {"A"}  # the two usable reviewers agree
    b = bp.PanelBudget(max_critic_picks=1)
    props = ["P1", "P2", "P3"]
    _, out = run({"power_consistency": cut("P1", "P2", "P3")}, props=props, budget=b,
                 high_impact=props)
    assert len(out.record["contested"]) == 1


def test_suggestions_only_from_candidates_and_need_two_backers():
    s = {"votes": [], "suggest": [{"card": "X", "reason": "r"}, {"card": "NOPE", "reason": "r"}]}
    _, out = run({"power_consistency": s, "synergy_flavor": s, "mana_base": {"votes": [], "suggest": [{"card": "Y", "reason": "r"}]}})
    assert out.adds == ["X"]


def test_no_client_and_junk_replies_degrade_without_raising():
    bp.set_client_factory(None)
    out = bp.review_stage("deck", CTX, PROPS, ())
    assert out.record["status"] == "unavailable" and not out.cuts

    class Junk:
        def chat(self, messages, max_tokens=None):
            return "not json at all"

    out = bp.review_stage("deck", CTX, PROPS, (), client=Junk())
    assert out.record["status"] == "unavailable"


def test_stage_lens_sets_and_env_budget():
    assert bp.STAGE_LENSES["core"] == ("power_consistency", "mana_base")
    assert set(bp.STAGE_LENSES["deck"]) == set(bp.LENSES)
    b = bp.PanelBudget.from_env({"BREW_PANEL_BUDGET": json.dumps({"max_tokens": 900, "max_calls": "x", "bogus": 1})})
    assert b.max_tokens == 900 and b.max_calls == bp.PanelBudget().max_calls
    assert bp.PanelBudget.from_env({"BREW_PANEL_BUDGET": "{nope"}) == bp.PanelBudget()

"""Scoring and agreement rules of the volunteer roof check (tools/roof_check_score.py),
and the answer checks of its inbox (services/roof_check_inbox/check_schema.py)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "services" / "roof_check_inbox"))

import roof_check_score as S  # noqa: E402
import check_schema as CS  # noqa: E402

ASPECTS = S.ASPECTS


def gold_key():
    """Gold cards: on every aspect, 4 known wrong and 4 known right."""
    key = {}
    for asp in ASPECTS:
        for i in range(4):
            key[f"gw-{asp}-{i}"] = dict(roof=f"g{i}", aspect=asp, gold="wrong", kind="planted")
            key[f"gr-{asp}-{i}"] = dict(roof=f"g{i}", aspect=asp, gold="right", kind="markup")
    return key


def careful(who, key, miss=0, cry=0):
    """A volunteer's gold answers: misses `miss` faults and cries wolf `cry` times per aspect."""
    out = []
    for asp in ASPECTS:
        for i in range(4):
            out.append(dict(volunteer=who, card=f"gw-{asp}-{i}", answer="right" if i < miss else "wrong"))
            out.append(dict(volunteer=who, card=f"gr-{asp}-{i}", answer="wrong" if i < cry else "right"))
    return out


def real_cards(key, roof="R"):
    for asp in ASPECTS:
        key[f"{roof}-{asp}"] = dict(roof=roof, aspect=asp, gold=None)
    return key


def say(who, roof, answers, **extra):
    """answers: {aspect: answer} on one roof's real cards."""
    return [dict(volunteer=who, card=f"{roof}-{asp}", answer=a, **extra) for asp, a in answers.items()]


ALL_RIGHT = {a: "right" for a in ASPECTS}


def test_careful_person_is_reliable():
    rel = S.reliability(careful("a", gold_key()), gold_key())
    assert all(rel["a"][asp]["reliable"] for asp in ASPECTS)
    assert rel["a"]["lines"]["catch"] == round(5 / 6, 3)


def test_always_right_is_not_reliable():
    # Passing everything is the failure the design is about: it must not count.
    key = gold_key()
    lazy = [dict(volunteer="z", card=c, answer="right") for c in key]
    rel = S.reliability(lazy, key)
    assert not any(rel["z"][asp]["reliable"] for asp in ASPECTS)
    assert rel["z"]["panels"]["catch"] == round(1 / 6, 3)


def test_always_wrong_is_not_reliable():
    key = gold_key()
    rel = S.reliability([dict(volunteer="w", card=c, answer="wrong") for c in key], key)
    assert not any(rel["w"][asp]["reliable"] for asp in ASPECTS)


def test_reliability_is_per_aspect():
    # Good on lines, blind to panel faults: reliable on lines only.
    key = gold_key()
    ans = [a for a in careful("p", key) if not a["card"].startswith("gw-panels")]
    ans += [dict(volunteer="p", card=f"gw-panels-{i}", answer="right") for i in range(4)]
    rel = S.reliability(ans, key)
    assert rel["p"]["lines"]["reliable"] and not rel["p"]["panels"]["reliable"]


def test_unsure_is_left_out_of_reliability():
    key = gold_key()
    ans = careful("u", key) + [dict(volunteer="u", card="gw-lines-0", answer="unsure")]
    assert S.reliability(ans, key)["u"]["lines"]["n_wrong"] == 4


def test_too_few_gold_is_not_reliable():
    key = gold_key()
    ans = [a for a in careful("n", key) if a["card"].endswith("-0")]  # one of each kind
    assert not S.reliability(ans, key)["n"]["outline"]["reliable"]


def test_three_reliable_rights_decide_right():
    key = real_cards(gold_key())
    ans = []
    for w in "abc":
        ans += careful(w, key) + say(w, "R", ALL_RIGHT)
    d, _ = S.decide(ans, key)
    assert d["R"]["state"] == "right"
    assert all(d["R"]["aspects"][a]["state"] == "right" for a in ASPECTS)


def test_two_rights_are_not_enough():
    key = real_cards(gold_key())
    ans = []
    for w in "ab":
        ans += careful(w, key) + say(w, "R", ALL_RIGHT)
    assert S.decide(ans, key)[0]["R"]["state"] == "open"


def test_unreliable_votes_do_not_count():
    key = real_cards(gold_key())
    ans = []
    for w in "abc":   # pass everything, gold included
        ans += [dict(volunteer=w, card=c, answer="right") for c in key]
    d, _ = S.decide(ans, key)
    assert d["R"]["state"] == "open"
    assert d["R"]["aspects"]["lines"]["unreliable"] == 3


def test_one_aspect_wrong_makes_the_roof_wrong():
    # Lines and outline pass; two reliable people fail the panels.
    key = real_cards(gold_key())
    ans = []
    for w in "abc":
        ans += careful(w, key)
        ans += say(w, "R", {**ALL_RIGHT, "panels": "wrong" if w in "ab" else "right"})
    d, _ = S.decide(ans, key)
    assert d["R"]["aspects"]["lines"]["state"] == "right"
    assert d["R"]["aspects"]["panels"]["state"] != "right"
    assert d["R"]["state"] != "right"


def test_something_else_note_is_a_wrong_vote_on_its_aspect():
    # Asked about lines, they say lines are right but tag the panels as wrong.
    key = real_cards(gold_key())
    ans = []
    for w in "abc":
        ans += careful(w, key)
        ans += say(w, "R", {a: "right" for a in ("outline", "lines", "obstructions")})
        ans.append(dict(volunteer=w, card="R-lines", answer="right",
                        else_aspect="panels", else_note="panels hang off the edge"))
    d, _ = S.decide(ans, key)
    assert d["R"]["aspects"]["panels"]["state"] == "wrong"
    assert d["R"]["aspects"]["lines"]["state"] == "right"
    assert d["R"]["state"] == "wrong"


def test_wrong_beats_right_for_the_same_person():
    key = real_cards(gold_key())
    ans = careful("a", key) + say("a", "R", {"panels": "right"})
    ans.append(dict(volunteer="a", card="R-outline", answer="right", else_aspect="panels"))
    votes, _ = S._votes(ans, key)
    assert votes[("R", "panels")]["a"] == "wrong"


def test_untagged_note_holds_the_roof():
    key = real_cards(gold_key())
    ans = []
    for w in "abc":
        ans += careful(w, key) + say(w, "R", ALL_RIGHT)
    ans.append(dict(volunteer="a", card="R-outline", answer="right", else_aspect="other",
                    else_note="this is two buildings"))
    d, _ = S.decide(ans, key)
    assert d["R"]["state"] == "needs a look"
    assert d["R"]["notes"] == ["this is two buildings"]


def test_disputed_after_max_votes():
    key = real_cards(gold_key())
    ans = []
    for i in range(S.MAX_VOTES):
        w = f"v{i}"
        ans += careful(w, key) + say(w, "R", {"lines": "wrong" if i % 2 else "right"})
    d, _ = S.decide(ans, key)
    assert d["R"]["aspects"]["lines"]["state"] == "disputed"
    assert d["R"]["state"] == "needs a look"


def test_passing_needs_more_than_failing():
    # The same people: two "wrong" votes decide wrong, two "right" votes do not decide right.
    key = real_cards(gold_key())
    base = careful("a", key) + careful("b", key)
    wrong = base + say("a", "R", {"lines": "wrong"}) + say("b", "R", {"lines": "wrong"})
    right = base + say("a", "R", {"lines": "right"}) + say("b", "R", {"lines": "right"})
    assert S.decide(wrong, key)[0]["R"]["aspects"]["lines"]["state"] == "wrong"
    assert S.decide(right, key)[0]["R"]["aspects"]["lines"]["state"] == "open"


def test_more_reliable_people_weigh_more():
    r = lambda c, k: {"lines": dict(catch=c, clear=k, reliable=True)}
    rel = {"s": r(0.95, 0.95), "h": r(0.78, 0.72)}
    assert S.p_wrong({"s": "wrong"}, rel, "lines") > S.p_wrong({"h": "wrong"}, rel, "lines")
    assert S.p_wrong({"s": "right"}, rel, "lines") < S.p_wrong({"h": "right"}, rel, "lines")


def test_one_slip_in_four_is_not_yet_reliable():
    # catch (3+1)/(4+2) = 0.67 is under MIN_CATCH: four gold cards cannot yet
    # tell one slip from a habit; more gold cards can.
    key = gold_key()
    assert not S.reliability(careful("h", key, miss=1), key)["h"]["lines"]["reliable"]


def test_gold_cards_are_not_decided():
    key = real_cards(gold_key())
    d, _ = S.decide(careful("a", key), key)
    assert set(d) == {"R"}


def test_owner_marked_aspect_is_decided_by_the_owner():
    # A real drawing the owner marked: his answer stands, and lazy votes on it change nothing.
    key = real_cards(gold_key())
    key["R-panels"]["gold"] = "wrong"
    ans = []
    for w in "abc":
        ans += careful(w, key) + say(w, "R", ALL_RIGHT)
    d, _ = S.decide(ans, key)
    assert d["R"]["aspects"]["panels"] == dict(state="wrong", owner=True)
    assert d["R"]["state"] == "wrong"


def test_gold_most_reliable_people_fail_is_listed_for_the_owner():
    key = gold_key()
    key["odd"] = dict(roof="g9", aspect="lines", gold="right", kind="markup")
    ans = []
    for w in "abcd":
        ans += careful(w, key) + [dict(volunteer=w, card="odd", answer="wrong" if w != "d" else "right")]
    rel = S.reliability(ans, key)
    audit = S.gold_audit(ans, key, rel)
    assert [g["card"] for g in audit] == ["odd"] and audit[0]["against"] == 3


def test_votes_needed():
    d = dict(aspects={"outline": dict(state="right", right=3, wrong=0),
                      "lines": dict(state="open", right=1, wrong=0),
                      "obstructions": dict(state="open", right=0, wrong=1),
                      "panels": dict(state="wrong", right=0, wrong=2)})
    assert S.votes_needed(d) == {"outline": 0, "lines": 2, "obstructions": 3, "panels": 0}


# --- inbox answer checks ---

def batch(**over):
    b = dict(volunteer="a1b2c3d4e5f6a7b8", deck="street_v85", answers=[
        dict(card="0f1e2d3c4b5a", answer="wrong", faults=["line_wrong"], spots=[[0.5, 0.25]],
             note="ridge is off", else_aspect=None, else_note="", ms=4200)])
    b.update(over)
    return b


def test_schema_keeps_a_good_batch():
    out = CS.clean(batch())
    assert out["answers"][0]["answer"] == "wrong" and out["answers"][0]["spots"] == [[0.5, 0.25]]


def test_schema_rejects():
    for bad in (batch(volunteer="x"), batch(answers=[]), batch(website="spam"),
                batch(answers=[dict(card="0f1e2d3c4b5a", answer="maybe")]),
                batch(answers=[dict(card="../../etc", answer="right")])):
        try:
            CS.clean(bad)
        except CS.BadBatch:
            continue
        raise AssertionError(f"accepted {bad}")


def test_schema_caps_text_and_drops_unknown_faults():
    out = CS.clean(batch(answers=[dict(card="0f1e2d3c4b5a", answer="right", faults=["nonsense"],
                                       else_aspect="panels", else_note="x" * 5000,
                                       spots=[[2, 2], [0.1, 0.2]])]))
    a = out["answers"][0]
    assert a["faults"] == [] and len(a["else_note"]) == CS.MAX_NOTE and a["spots"] == [[0.1, 0.2]]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("  pass ", name)

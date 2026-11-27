"""Scoring and agreement for the volunteer roof check (check_roofs.html).

Design: docs/maintainers/roof-check.md. Pure functions only, so the tally
(tools/roof_check.py tally) and tests/test_roof_check.py run the same rules.

THE RISK. A volunteer looks at one part of a roof and passes it ("the lines
are right") while another part is broken. So:

  - every card asks about ONE aspect (outline, lines, obstructions, panels),
    the four picks of the owner's blind review;
  - every card offers "something else is wrong"; a note tagged with an
    aspect counts as a "wrong" vote on that aspect, and an untagged note
    holds the whole roof for a person to read;
  - per person and per roof aspect, a "wrong" seen anywhere beats a "right";
  - some cards are gold (answer known: the owner's markup, a fault planted
    in a copy of a roof, or the owner's own fault marks), and each person's
    reliability is measured on them, aspect by aspect;
  - only reliable people's votes decide, and passing a roof aspect needs
    more agreement than failing it, because a missed fault is the costly
    mistake.

RELIABILITY (per person, per aspect, from gold cards only; "can't tell"
answers are left out):
  catch = (gold-wrong cards answered wrong + 1) / (gold-wrong cards + 2)
  clear = (gold-right cards answered right + 1) / (gold-right cards + 2)
A person is reliable on an aspect with at least MIN_GOLD_EACH gold-wrong and
MIN_GOLD_EACH gold-right answers, catch >= MIN_CATCH and clear >= MIN_CLEAR.
The +1/+2 keeps a person with two lucky answers from scoring 100%.

AGREEMENT (per roof aspect, reliable votes only). Each vote moves the odds
that the aspect is wrong by that person's own rates:
  "wrong" multiplies them by catch / (1 - clear)
  "right" multiplies them by (1 - catch) / clear
starting from PRIOR_WRONG. Decided wrong at P(wrong) >= DECIDE_WRONG_P with
at least MIN_WRONG_VOTES "wrong" votes; decided right at
P(wrong) <= DECIDE_RIGHT_P with at least MIN_RIGHT_VOTES "right" votes.
With MAX_VOTES reliable votes and neither, the aspect is disputed and goes
to the owner. A roof is right only when all four aspects are decided right
and no reliable person left an untagged note.
"""
import math
from collections import defaultdict

ASPECTS = ("outline", "lines", "obstructions", "panels")
ANSWERS = ("right", "wrong", "unsure")

MIN_GOLD_EACH = 2      # gold-wrong and gold-right answers needed on an aspect
MIN_CATCH = 0.75
MIN_CLEAR = 0.70
PRIOR_WRONG = 0.5      # most map roofs had at least one fault in the owner's marks
DECIDE_WRONG_P = 0.95
DECIDE_RIGHT_P = 0.02
MIN_WRONG_VOTES = 2
MIN_RIGHT_VOTES = 3
MAX_VOTES = 7
RATE_CAP = 0.98        # a perfect record still leaves room for one mistake


def _votes(answers, key):
    """Each person's verdict per (roof, aspect) on cards that are not gold.

    answers: dicts with volunteer, card, answer, and optional else_aspect
    (the aspect a "something else is wrong" note names; "other" or None for
    an untagged note) and else_note. key: card id -> {roof, aspect, gold}.
    Returns ({(roof, aspect): {volunteer: "right"|"wrong"|"unsure"}},
             {roof: [(volunteer, note)]}) - the second holds untagged notes.
    """
    rank = {"unsure": 0, "right": 1, "wrong": 2}
    votes = defaultdict(dict)
    notes = defaultdict(list)

    def put(ra, who, ans):
        if rank[ans] > rank.get(votes[ra].get(who), -1):
            votes[ra][who] = ans

    for a in answers:
        k = key.get(a["card"])
        if not k or k.get("gold") is not None:
            continue
        who, roof = a["volunteer"], k["roof"]
        if a.get("answer") in ANSWERS:
            put((roof, k["aspect"]), who, a["answer"])
        ea = a.get("else_aspect")
        if ea in ASPECTS:
            put((roof, ea), who, "wrong")
        elif a.get("else_note") or ea == "other":
            notes[roof].append((who, a.get("else_note") or ""))
    return votes, notes


def reliability(answers, key):
    """{volunteer: {aspect: {catch, clear, n_wrong, n_right, reliable}}} from gold cards."""
    tally = defaultdict(lambda: defaultdict(lambda: [0, 0, 0, 0]))  # caught, n_wrong, cleared, n_right
    for a in answers:
        k = key.get(a["card"])
        if not k or k.get("gold") not in ("right", "wrong") or a.get("answer") not in ("right", "wrong"):
            continue
        t = tally[a["volunteer"]][k["aspect"]]
        if k["gold"] == "wrong":
            t[1] += 1
            t[0] += a["answer"] == "wrong"
        else:
            t[3] += 1
            t[2] += a["answer"] == "right"
    out = {}
    for who, per in tally.items():
        out[who] = {}
        for asp, (caught, nw, cleared, nr) in per.items():
            catch = (caught + 1) / (nw + 2)
            clear = (cleared + 1) / (nr + 2)
            out[who][asp] = dict(
                catch=round(catch, 3), clear=round(clear, 3), n_wrong=nw, n_right=nr,
                reliable=nw >= MIN_GOLD_EACH and nr >= MIN_GOLD_EACH
                and catch >= MIN_CATCH and clear >= MIN_CLEAR)
    return out


def p_wrong(votes, rel, aspect, prior=PRIOR_WRONG):
    """P(aspect wrong) from reliable votes, each weighted by its own catch and clear."""
    lo = math.log(prior / (1 - prior))
    for who, ans in votes.items():
        r = rel.get(who, {}).get(aspect)
        if not r or not r["reliable"] or ans == "unsure":
            continue
        catch = min(r["catch"], RATE_CAP)
        clear = min(r["clear"], RATE_CAP)
        lo += math.log(catch / (1 - clear)) if ans == "wrong" else math.log((1 - catch) / clear)
    return 1 / (1 + math.exp(-lo))


def decide_aspect(votes, rel, aspect):
    """State of one roof aspect: right, wrong, disputed or open, with its counts."""
    counted = {w: a for w, a in votes.items()
               if rel.get(w, {}).get(aspect, {}).get("reliable") and a != "unsure"}
    n_w = sum(a == "wrong" for a in counted.values())
    n_r = sum(a == "right" for a in counted.values())
    p = p_wrong(votes, rel, aspect)
    if p >= DECIDE_WRONG_P and n_w >= MIN_WRONG_VOTES:
        state = "wrong"
    elif p <= DECIDE_RIGHT_P and n_r >= MIN_RIGHT_VOTES:
        state = "right"
    elif n_w + n_r >= MAX_VOTES:
        state = "disputed"
    else:
        state = "open"
    return dict(state=state, p_wrong=round(p, 4), wrong=n_w, right=n_r,
                unsure=sum(a == "unsure" for a in votes.values()),
                unreliable=len(votes) - len(counted) - sum(a == "unsure" for a in votes.values()))


def decide(answers, key):
    """Every roof drawn by a build (key kind "real"): per-aspect states and the roof's own state.

    An aspect the owner already marked on that same drawing (a real card
    with a gold answer) takes the owner's answer, with owner=True; the
    volunteers' answers on it only measure them.

    Roof state: "wrong" if any aspect is decided wrong; "needs a look" if a
    reliable person left an untagged note (or an aspect is disputed);
    "right" only when all four aspects are decided right; else "open".
    """
    rel = reliability(answers, key)
    votes, notes = _votes(answers, key)
    real = [k for k in key.values() if k.get("kind", "real") == "real"]
    known = {(k["roof"], k["aspect"]): k["gold"] for k in real if k.get("gold")}
    roofs = sorted({k["roof"] for k in real}, key=str)
    out = {}
    for roof in roofs:
        per = {}
        for asp in ASPECTS:
            if (roof, asp) in known:
                per[asp] = dict(state=known[(roof, asp)], owner=True)
            else:
                per[asp] = decide_aspect(votes.get((roof, asp), {}), rel, asp)
        reliable_notes = [n for who, n in notes.get(roof, [])
                          if any(v.get("reliable") for v in rel.get(who, {}).values())]
        states = [p["state"] for p in per.values()]
        if "wrong" in states:
            state = "wrong"
        elif reliable_notes or "disputed" in states:
            state = "needs a look"
        elif all(s == "right" for s in states):
            state = "right"
        else:
            state = "open"
        out[roof] = dict(state=state, aspects=per, notes=[n for _, n in notes.get(roof, [])])
    return out, rel


def votes_needed(decision):
    """How many more reliable votes each open aspect of a roof wants, for the next deck."""
    need = {}
    for asp, d in decision["aspects"].items():
        if d["state"] != "open" or d.get("owner"):
            need[asp] = 0
        else:
            need[asp] = max(MIN_RIGHT_VOTES - d["right"], MIN_WRONG_VOTES - d["wrong"], 1)
    return need


AUDIT_MIN_ANSWERS = 3
AUDIT_SHARE = 0.6


def gold_audit(answers, key, rel):
    """Gold cards that most reliable people answer against: the key may be wrong.

    A gold answer is the owner's drawing or mark, or a planted fault, and
    either can be wrong on the photo the card shows (markup traced on another
    photo, a planted fault that lands where the roof really is). Such a card
    is listed for the owner when at least AUDIT_MIN_ANSWERS people who are
    reliable on its aspect answered it and AUDIT_SHARE or more disagree with
    the key. It stays in the scores until the owner rules on it.
    """
    per = defaultdict(lambda: [0, 0])
    for a in answers:
        k = key.get(a["card"])
        if not k or k.get("gold") not in ("right", "wrong") or a.get("answer") not in ("right", "wrong"):
            continue
        if not rel.get(a["volunteer"], {}).get(k["aspect"], {}).get("reliable"):
            continue
        per[a["card"]][0] += 1
        per[a["card"]][1] += a["answer"] != k["gold"]
    out = []
    for card, (n, against) in per.items():
        if n >= AUDIT_MIN_ANSWERS and against / n >= AUDIT_SHARE:
            k = key[card]
            out.append(dict(card=card, roof=k["roof"], aspect=k["aspect"], gold=k["gold"],
                            why=k.get("why"), answers=n, against=against))
    return sorted(out, key=lambda g: -g["against"] / g["answers"])

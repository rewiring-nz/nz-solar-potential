# Volunteer roof check

A game-style page where volunteers check the map's roofs against the
photo, one question at a time: `check_roofs.html`. Status: first version,
not deployed. Rules live in `tools/roof_check_score.py`; this note says
why they are what they are.

## The risk it is built around

A person looks at one part of a roof and passes it ("the lines are right")
while another part is broken (the panels hang off the edge). One "looks
fine" is not evidence. So every answer is narrow, every roof is seen by
several people, and each person is measured before their answers count.

```
deck (tools/roof_check.py deck)          page (check_roofs.html)          tally (tools/roof_check.py tally)
 real roofs from a build snapshot  ──►   10 cards a round, one aspect  ──►  reliability per person per aspect
 owner's markup  (gold: right)           each; Right / Wrong / Can't         (gold cards only)
 planted faults  (gold: wrong)           tell; "Something else is      ──►  agreement per roof aspect
 owner's fault marks (gold)              wrong" + note on every card         (reliable votes only)
 key.json stays private                  answers → inbox (or download)  ──►  roof: right / wrong / open /
                                                                              needs a look; gold to recheck
```

## The questions

One card, one aspect, the four picks of the owner's blind review
(`tools/review_page.py` `ASPECTS`):

| Aspect | Question | What the card draws |
| --- | --- | --- |
| outline | Is the roof's outline right? | the outline only |
| lines | Are the roof lines right? | each face as a coloured patch |
| obstructions | Are the vents, skylights and chimneys right? | obstruction boxes and a faint outline |
| panels | Are the solar panels where an installer would put them? | panels and faint face lines |

Each card draws only what its question is about, so the eye goes there.
"Wrong" opens fault chips (the ones in `tools/mark_faults.py`, in plain
words), a tap on the photo to mark the spot, and a note. Every card also
has **Something else is wrong with this roof**: pick the part (or "something
else") and say what. "Photo only" (press and hold) hides the drawing.

Per person, each roof aspect is shown once: the real drawing and a planted
copy of the same roof aspect never both reach one person, since side by
side the planted fault would give itself away. A round takes the aspects in
turn (`roof_check.js` `pickRound`).

## Gold cards: how reliable is each person?

Gold cards look exactly like the rest. Card and photo ids are random and
the building id is only in the private `key.json`, so the page cannot tell
gold from real either.

| Gold source | Answer | Notes |
| --- | --- | --- |
| Owner's markup (`data/roof_labels.json`), flagged complete, no problem noted, homes first | right (outline, lines, obstructions) | Only markup traced on the map's own photo. The pilot's was traced on the survey-year photo, which leans off the map's by up to a few metres, so it is left out. Markup has no panels. |
| Planted fault in a copy of a real or markup roof | wrong on that aspect | outline slid 1.2 m; the line between the two faces sharing the longest edge taken out; the largest obstruction taken out or a 1 m one made up; three edge panels moved off the roof. |
| Owner's fault marks on the same snapshot (`data/fault_marks/<label>.json`) | wrong where marked; right on every aspect of a roof ticked right | The realistic, subtle faults. Panel gold that is "right" comes only from here, so it is thin: more ticked-right roofs are the cheapest fix. |

Per person and aspect, from gold cards only ("Can't tell" left out):

- **catch** = (gold-wrong answered wrong + 1) / (gold-wrong seen + 2)
- **clear** = (gold-right answered right + 1) / (gold-right seen + 2)

Reliable on an aspect: at least 2 of each kind seen, catch ≥ 0.75, clear
≥ 0.70. Someone who passes everything has a low catch and never counts; a
person can be reliable on lines and not on panels. The +1/+2 stops two lucky
answers from scoring 100%.

A gold card that most reliable people answer against (3 or more answers,
60% or more disagreeing) is listed in the tally as **gold to recheck**: the
key may be wrong for that photo. The owner rules on it.

## Agreement: when is a roof decided?

Only reliable people's votes count, each weighted by that person's own
catch and clear. Starting from even odds, a "wrong" multiplies the odds of
a fault by catch / (1 − clear), a "right" by (1 − catch) / clear.

| Outcome | Rule |
| --- | --- |
| aspect wrong | P(fault) ≥ 0.95 and at least 2 "wrong" votes |
| aspect right | P(fault) ≤ 0.02 and at least 3 "right" votes |
| aspect disputed | 7 reliable votes and neither |
| roof right | all four aspects right and no untagged note from a reliable person |
| roof wrong | any aspect wrong |
| roof needs a look | an aspect disputed, or a reliable person's untagged note |

Passing needs more than failing because a missed fault is the costly
mistake. Per person and roof aspect, a "wrong" seen anywhere beats a
"right": a "Something else is wrong" note that names the panels is a
"wrong" vote on the panels even on a lines card. An aspect the owner has
already marked on that drawing keeps the owner's answer.

## Where answers go

The page keeps answers in the browser and, when `SITE.roofCheckInbox` is
set, posts each round to `services/roof_check_inbox/` (a Cloud Run function
on the same pattern as the problem-report inbox, writing
`gs://solar-potential-map-data/roof-checks/inbox/<id>.json`; max one
instance, rate-limited, no other credentials). Until it is deployed,
"Download my answers" gives a file the tally reads. Nothing is asked about
the volunteer: a random id per browser is the only identity, and notes are
capped and kept private.

Cost: a deck is static files (about 100 KB of photo per roof); each round is
one small write. At thousands of rounds a month this is cents.

## How answers feed the roof ledger and training

Volunteer verdicts are evidence to check against the markup, not truth.

1. **Drawn roofs** (markup exists): the tally's per-aspect state is set
   beside `tools/roof_ledger.py`'s measured state for the same build. Where
   they disagree, the owner looks: either the measure or the volunteers are
   wrong, and either finding is worth having.
2. **Undrawn roofs** (most of the map): a roof decided **wrong** goes to the
   markup queue (`mark_roofs.html`) and, with its fault chips and spots, to
   `data/roof_cases.json` as a reported roof; the spots are NZTM once the key
   is applied. A roof decided **right** on all four aspects is a candidate
   for the next fresh confidence set, not a replacement for markup.
3. **Training**: decided aspects are labels for the reading choice
   (`tools/predict_faces.py`: which reading the people backed) and for
   evaluating the local line reader on roofs nobody has drawn. Spots on
   "wrong" are weak, located labels. No label goes into training until the
   gold audit is clear for that deck.

The 30 held-back roofs of `data/roof_set.json` are never put in a deck.

## Running it

```bash
.venv/bin/python tools/roof_check.py deck street_v85 --snapshot street_v85 --markup 16
```

writes `data/roof_check/street_v85/` (`deck.json` and `img/` are public;
`key.json` is private and never published). Open
`check_roofs.html?deck=data/roof_check/street_v85`. Tally:

```bash
.venv/bin/python tools/roof_check.py tally data/roof_check/street_v85 <answer files or folder>
```

Tests: `tests/test_roof_check.py` (scoring, agreement, gold audit, inbox
checks) and `tests/test_roof_check.js` (card choice).

## Open

- The gold share is set by the deck, not per person. A new person should
  see more gold in the first rounds; that needs the inbox to hand out cards.
- Planted faults are easier than real ones, so catch on planted cards
  overstates catch on subtle faults. The owner's fault marks are the
  realistic gold; report catch on each source separately once there are
  answers.
- One person can make several browser ids. Reliability still has to be
  earned per id, but a determined person could vote several times.
- Not yet deployed: the inbox, a published deck and a link from the map.

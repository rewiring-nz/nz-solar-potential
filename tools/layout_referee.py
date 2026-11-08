"""
Ask a vision model to review a roof drawing the way the owner does.

Each review render (tools/render_built.py: the map photo with the drawn roof
faces in red, obstructions in orange and panels in blue) is sent with the
instructions, legend and fault list of the blind review page
(tools/review_page.py), and the model returns a score out of 10 and the
fault tags. The point is to test whether such a referee agrees with the
owner's blind scores well enough to find bad layouts across the district.

First check (claude-opus-5-5, effort low, prompt version 1, $0.60 for 92
renders, about $0.006 each): on the 40 random v72 roofs
(data/live_review/v72/answers.json) its scores rank with the owner's at 0.47
(Spearman) and it ranks a roof the owner scored 5 or less below a better one
71% of the time; its 10 best held one such roof. On the 50 frame-fix
drawings (data/blind_check/frame_fix) it ranked the owner's tagged drawings
lower 65% of the time. It scores harder than the owner (mean 4.9 against
6.0) and tags "outline" and "missed" far more often. Needs the anthropic
package (pip install anthropic).

PAID: every uncached image is one Claude API call (the key is the project
secret Claude-API, src/project_secrets.py). --max-usd is required and the run
stops before the next call once the running cost reaches it. Answers are
cached per image, model and prompt version under data/layout_referee/, so a
repeat run costs nothing.

    python tools/layout_referee.py --max-usd 1 --out answers.json \
        data/live_review/v72/tiles/*_now.jpg
"""
import argparse
import base64
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CACHE = ROOT / "data" / "layout_referee"

MODEL = "claude-opus-5-5"
PRICE_PER_MTOK = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0)}
PROMPT_VERSION = 1

# Same tags and words as tools/review_page.py FAULTS.
FAULTS = {
    "outline": "Building outline wrong",
    "lines": "Roof lines wrong",
    "ridge": "Panels across a ridge or valley",
    "obstruction": "Panels over obstructions",
    "off_roof": "Panels over empty area",
    "racking": "Racking wrong direction",
    "missed": "Empty space with no panels",
}

PROMPT = (
    "This is an aerial photo of one building with a solar estimate drawn on it. "
    "Red lines are the roof faces the estimate found, orange shapes are "
    "obstructions it found (skylights, vents, chimneys), blue rectangles are "
    "the solar panels it placed.\n\n"
    "Score the drawing out of 10 against the real roof in the photo, and tick "
    "what's wrong with it. More panels is not automatically better: a good "
    "drawing follows the real roof planes, keeps panels on one plane each, off "
    "obstructions and on the roof, racked the way an installer would, and uses "
    "the good roof space.\n\n"
    "Faults to choose from:\n"
    + "\n".join(f"- {k}: {v}" for k, v in FAULTS.items())
    + "\n\nGive a one-sentence reason naming what you saw."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "score": {"type": "integer", "description": "1 to 10"},
        "faults": {"type": "array", "items": {"type": "string", "enum": list(FAULTS)}},
        "reason": {"type": "string"},
    },
    "required": ["score", "faults", "reason"],
    "additionalProperties": False,
}


def cache_path(img: Path, model: str, effort: str) -> Path:
    h = hashlib.sha256(img.read_bytes()).hexdigest()[:20]
    return CACHE / f"{h}_{model}_{effort}_p{PROMPT_VERSION}.json"


def ask(client, img: Path, model: str, effort: str):
    data = base64.standard_b64encode(img.read_bytes()).decode()
    r = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
            {"type": "text", "text": PROMPT},
        ]}],
    )
    if r.stop_reason == "refusal":
        return {"refused": True}, r.usage
    text = next(b.text for b in r.content if b.type == "text")
    ans = json.loads(text)
    ans["model"] = r.model
    return ans, r.usage


def cost(usage, model):
    pin, pout = PRICE_PER_MTOK[model]
    return (usage.input_tokens * pin + usage.output_tokens * pout) / 1e6


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("images", nargs="+", type=Path)
    ap.add_argument("--max-usd", type=float, required=True)
    ap.add_argument("--model", default=MODEL, choices=list(PRICE_PER_MTOK))
    ap.add_argument("--effort", default="low", choices=["low", "medium", "high"])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    client, spent, out = None, 0.0, {}
    for img in a.images:
        cp = cache_path(img, a.model, a.effort)
        if cp.exists():
            out[str(img)] = json.loads(cp.read_text())
            continue
        if spent >= a.max_usd:
            print(f"stopped at ${spent:.3f}: --max-usd {a.max_usd} reached", file=sys.stderr)
            break
        if client is None:
            import anthropic
            from src import project_secrets
            client = anthropic.Anthropic(api_key=project_secrets.anthropic_api_key())
        ans, usage = ask(client, img, a.model, a.effort)
        c = cost(usage, a.model)
        spent += c
        ans["usd"] = round(c, 5)
        cp.write_text(json.dumps(ans))
        out[str(img)] = ans
        print(f"{img.name}: {ans.get('score')} {ans.get('faults')} ${c:.4f} (total ${spent:.3f})", flush=True)
    json.dump(out, open(a.out, "w"), indent=1)
    print(f"{len(out)} answered, ${spent:.3f} spent this run", file=sys.stderr)


if __name__ == "__main__":
    main()

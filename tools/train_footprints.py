"""Train the building finder on photo tiles with LINZ outlines as answers.

    python tools/train_footprints.py [--epochs 8] [--threads 6]
    python tools/train_footprints.py --eval-only

Same U-Net as the roof-shape model (src/photo_shape.py), RGB in, one output:
is this pixel a building. Tiles from tools/footprint_dataset.py.

SPLIT BY TOWN, like every model here: TEST_REGIONS are never trained on and
are what --eval-only scores; VAL_REGIONS choose the checkpoint.

THE 2026 PHOTO IS NOT THE 2021 ONE: training applies colour, brightness and
blur jitter so the model reads both captures, and the evaluation reports
how it does on held-out towns.

SCORED AS BUILDINGS, NOT PIXELS: a LINZ outline counts as found when a
predicted blob covers half of it; a predicted blob counts as right when half
of it lies on an outline. Those are what matter for supplying missing
outlines: a found building, and no invented ones.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import photo_shape as ps  # noqa: E402

DATA = ROOT / "data" / "footprints"
OUT = ROOT / "data" / "models" / "footprints_v1.pt"
TEST_REGIONS = {"frankton_flats", "kelvin_heights", "arthurs_point"}
VAL_REGIONS = {"lake_hayes_est_west"}
MEAN = np.array([0.485, 0.456, 0.406], np.float32)[:, None, None]
STD = np.array([0.229, 0.224, 0.225], np.float32)[:, None, None]


def load(keep):
    P, M = [], []
    for p in sorted(DATA.glob("*.npz")):
        if not keep(p.stem):
            continue
        d = np.load(p)
        P.append(d["photo"]); M.append(d["mask"])
    return np.concatenate(P), np.concatenate(M)


def to_input(photo_u8):
    return (photo_u8.astype(np.float32) / 255.0 - MEAN) / STD


def batch(P, M, idx, rng):
    import torch
    from scipy.ndimage import gaussian_filter
    x = P[idx].astype(np.float32)
    if rng is not None:
        n = len(idx)
        x = x * rng.uniform(0.75, 1.25, (n, 1, 1, 1)) * rng.uniform(0.9, 1.1, (n, 3, 1, 1)) \
            + rng.uniform(-20, 20, (n, 1, 1, 1))
        for i in range(n):
            if rng.random() < 0.4:
                x[i] = gaussian_filter(x[i], sigma=(0, rng.uniform(0.5, 1.5), rng.uniform(0.5, 1.5)))
    x = np.clip(x, 0, 255).astype(np.uint8)
    xt = torch.from_numpy(np.stack([to_input(a) for a in x]))
    yt = torch.from_numpy(M[idx].astype(np.float32))
    if rng is not None:
        k = int(rng.integers(8))
        xt = ps.dihedral_x(xt, k)
        (yt,), _, _ = ps.dihedral_maps([yt], torch.zeros_like(yt), torch.zeros_like(yt), k)
    return xt, yt


def buildings_found(prob, mask, thr=0.5):
    """(outlines found, outlines, blobs right, blobs)."""
    from scipy.ndimage import label
    pred = prob > thr
    found = n_true = right = n_pred = 0
    tl, nt = label(mask > 0)
    for i in range(1, nt + 1):
        sel = tl == i
        if sel.sum() < 80:          # 5 m2 at 0.25 m: sheds below that are not the target
            continue
        n_true += 1
        found += pred[sel].mean() >= 0.5
    pl, npd = label(pred)
    for i in range(1, npd + 1):
        sel = pl == i
        if sel.sum() < 80:
            continue
        n_pred += 1
        right += (mask[sel] > 0).mean() >= 0.5
    return found, n_true, right, n_pred


def evaluate(model, P, M, thr=0.5, bs=32):
    import torch
    model.eval()
    tot = np.zeros(4)
    inter = union = 0
    with torch.no_grad():
        for i in range(0, len(P), bs):
            idx = np.arange(i, min(i + bs, len(P)))
            x, y = batch(P, M, idx, None)
            prob = torch.sigmoid(model(x)[:, 0]).numpy()
            for j in range(len(idx)):
                tot += buildings_found(prob[j], M[idx[j]], thr)
                p, t = prob[j] > thr, M[idx[j]] > 0
                inter += (p & t).sum(); union += (p | t).sum()
    return {"recall": float(tot[0] / max(tot[1], 1)), "precision": float(tot[2] / max(tot[3], 1)),
            "iou": float(inter / max(union, 1)), "outlines": int(tot[1]), "blobs": int(tot[3])}


def main():
    import torch
    import torch.nn.functional as F
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--bs", type=int, default=16)
    ap.add_argument("--threads", type=int, default=6)
    ap.add_argument("--eval-only", action="store_true")
    a = ap.parse_args()
    torch.set_num_threads(a.threads)
    torch.manual_seed(0)
    rng = np.random.default_rng(0)
    if a.eval_only:
        P, M = load(lambda r: r in TEST_REGIONS)
        model = ps.load(OUT, out_ch=1, in_ch=3)
        res = {str(t): evaluate(model, P, M, t) for t in (0.4, 0.5, 0.6)}
        print(json.dumps(res, indent=1))
        OUT.with_suffix(".eval.json").write_text(json.dumps(res, indent=1))
        return
    P, M = load(lambda r: r not in TEST_REGIONS | VAL_REGIONS)
    Pv, Mv = load(lambda r: r in VAL_REGIONS)
    print(f"train {len(P)} tiles, val {len(Pv)}; building share {M.mean():.3f}", flush=True)
    model = ps.build_model(pretrained=True, out_ch=1, in_ch=3)
    opt = torch.optim.AdamW(model.parameters(), lr=5e-4, weight_decay=1e-4)
    steps = a.epochs * (len(P) // a.bs)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=5e-4, total_steps=steps, pct_start=0.1)
    best = None
    for ep in range(a.epochs):
        model.train()
        t0 = time.time()
        order = rng.permutation(len(P))
        for b in range(len(order) // a.bs):
            x, y = batch(P, M, order[b * a.bs:(b + 1) * a.bs], rng)
            logit = model(x)[:, 0]
            bce = F.binary_cross_entropy_with_logits(logit, y)
            p = torch.sigmoid(logit)
            dice = 1 - (2 * (p * y).sum() + 1) / (p.sum() + y.sum() + 1)
            loss = bce + dice
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
        v = evaluate(model, Pv, Mv)
        f1 = 2 * v["recall"] * v["precision"] / max(v["recall"] + v["precision"], 1e-9)
        print(json.dumps({"epoch": ep + 1, **{k: round(x, 3) if isinstance(x, float) else x for k, x in v.items()},
                          "f1": round(f1, 3), "minutes": round((time.time() - t0) / 60, 1)}), flush=True)
        if best is None or f1 > best:
            best = f1
            OUT.parent.mkdir(parents=True, exist_ok=True)
            torch.save({"state": model.state_dict(), "epoch": ep + 1, "val": v}, OUT)
    print("saved", OUT)


if __name__ == "__main__":
    main()

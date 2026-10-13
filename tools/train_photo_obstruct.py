"""Train a photo obstruction detector on what the LiDAR saw standing on roofs.

    python tools/train_photo_obstruct.py [--epochs 12]
    python tools/train_photo_obstruct.py --eval-only      # score the saved model on the test towns

Same network and inputs as the roof-shape model (src/photo_shape.py), one
output: is this roof pixel covered by an obstruction. Answers come from
tools/photo_obstruction_dataset.py -- the LiDAR height test, or the hand
markup where it exists; roofs with no answer are left out.

Obstructions are a few percent of roof area, so the loss is weighted toward
them (BCE with pos_weight, plus a Dice term) and restricted to the footprint.
The split is the shape model's: whole towns held out (TEST_REGIONS), the
checkpoint chosen on VAL_REGIONS.

SCORED AS OBJECTS, NOT PIXELS. Each LiDAR obstruction counts as found when
the prediction covers any of it; each predicted blob counts as right when it
touches a LiDAR obstruction. Recall and precision on that basis are compared
with the build's colour detector's measured 0.30 / 0.30 (BACKLOG).
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from src import photo_shape as ps  # noqa: E402
from train_photo_shape import TEST_REGIONS, VAL_REGIONS  # noqa: E402

OUT = ROOT / "data" / "models" / "photo_obstruct_v1.pt"


def load(data_dir, keep, view=None):
    P, M, O, R, V = [], [], [], [], []
    for p in sorted(Path(data_dir).glob("*.npz")):
        if p.name.startswith("obstruct_"):
            continue
        q = p.with_name("obstruct_" + p.name)
        if not q.exists():
            continue
        d, o = np.load(p), np.load(q)
        sel = np.array([keep(r) for r in d["region"]]) & (o["known"] > 0)
        if view:
            sel &= d["view"] == view
        if not sel.any():
            continue
        P.append(d["photo"][sel]); M.append(d["mask"][sel]); O.append(o["obstruct"][sel])
        R.append(d["m_per_px"][sel]); V.append(d["view"][sel])
    return {"photo": np.concatenate(P), "mask": np.concatenate(M), "obs": np.concatenate(O),
            "m_per_px": np.concatenate(R), "view": np.concatenate(V)}


def tensors(D, idx, rng=None):
    import torch
    photo = D["photo"][idx].astype(np.float32)
    if rng is not None:
        n = len(idx)
        photo = photo * rng.uniform(0.8, 1.2, (n, 1, 1, 1)) + rng.uniform(-15, 15, (n, 1, 1, 1))
    photo = np.clip(photo, 0, 255).astype(np.uint8)
    x = torch.from_numpy(ps.make_input(photo, D["mask"][idx], D["m_per_px"][idx]))
    y = torch.from_numpy(D["obs"][idx].astype(np.float32))
    m = torch.from_numpy(D["mask"][idx].astype(np.float32))
    if rng is not None:
        k = int(rng.integers(8))
        x = ps.dihedral_x(x, k)
        (y, m), _, _ = ps.dihedral_maps([y, m], torch.zeros_like(y), torch.zeros_like(y), k)
    return x, y, m


def object_scores(prob, obs, mask, thr=0.5):
    """(lidar objects found, lidar objects, predicted blobs right, predicted blobs)."""
    from scipy.ndimage import label
    pred = (prob > thr) & (mask > 0)
    found = n_true = right = n_pred = 0
    tl, nt = label(obs > 0)
    for i in range(1, nt + 1):
        n_true += 1
        found += bool(pred[tl == i].any())
    pl, npd = label(pred)
    for i in range(1, npd + 1):
        n_pred += 1
        right += bool((obs > 0)[pl == i].any())
    return found, n_true, right, n_pred


def evaluate(model, D, thr=0.5, bs=256):
    import torch
    model.eval()
    tot = np.zeros(4)
    with torch.no_grad():
        for i in range(0, len(D["obs"]), bs):
            idx = np.arange(i, min(i + bs, len(D["obs"])))
            x, y, m = tensors(D, idx)
            prob = torch.sigmoid(model(x)[:, 0]).numpy()
            for j in range(len(idx)):
                tot += object_scores(prob[j], y[j].numpy(), m[j].numpy(), thr)
    return {"recall": tot[0] / max(tot[1], 1), "precision": tot[2] / max(tot[3], 1),
            "lidar_objects": int(tot[1]), "predicted": int(tot[3])}


def main():
    import torch
    import torch.nn.functional as F
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data" / "photo_shape"))
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--bs", type=int, default=64)
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--eval-only", action="store_true")
    a = ap.parse_args()
    torch.set_num_threads(16)
    torch.manual_seed(0)
    rng = np.random.default_rng(0)
    if a.eval_only:
        model = ps.load(a.out, out_ch=1)
        res = {}
        for view in ("p03", "p26"):
            te = load(a.data, lambda r: r in TEST_REGIONS, view)
            res[view] = {str(t): evaluate(model, te, t) for t in (0.3, 0.5, 0.7)}
        print(json.dumps(res, indent=1))
        Path(a.out).with_suffix(".eval.json").write_text(json.dumps(res, indent=1))
        return
    tr = load(a.data, lambda r: r not in TEST_REGIONS | VAL_REGIONS)
    va = load(a.data, lambda r: r in VAL_REGIONS)
    pos = (tr["obs"] > 0).sum() / max((tr["mask"] > 0).sum(), 1)
    print(f"train {len(tr['obs'])} samples, val {len(va['obs'])}; obstructions {100 * pos:.1f}% of roof", flush=True)
    pw = torch.tensor(min(20.0, (1 - pos) / max(pos, 1e-4)))
    model = ps.build_model(pretrained=True, out_ch=1)
    opt = torch.optim.AdamW(model.parameters(), lr=5e-4, weight_decay=1e-4)
    steps = a.epochs * (len(tr["obs"]) // a.bs)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=5e-4, total_steps=steps, pct_start=0.1)
    best = None
    for ep in range(a.epochs):
        model.train()
        t0 = time.time()
        order = rng.permutation(len(tr["obs"]))
        for b in range(len(order) // a.bs):
            x, y, m = tensors(tr, order[b * a.bs:(b + 1) * a.bs], rng)
            logit = model(x)[:, 0]
            bce = (F.binary_cross_entropy_with_logits(logit, y, pos_weight=pw, reduction="none") * m).sum() / m.sum().clamp(min=1)
            p = torch.sigmoid(logit) * m
            dice = 1 - (2 * (p * y).sum() + 1) / (p.sum() + (y * m).sum() + 1)
            loss = bce + dice
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
        v = {k: (float(x) if isinstance(x, float) else int(x)) for k, x in evaluate(model, va).items()}
        f1 = 2 * v["recall"] * v["precision"] / max(v["recall"] + v["precision"], 1e-9)
        print(json.dumps({"epoch": ep + 1, **{k: round(x, 3) if isinstance(x, float) else x for k, x in v.items()},
                          "f1": round(f1, 3), "minutes": round((time.time() - t0) / 60, 1)}), flush=True)
        if best is None or f1 > best:
            best = f1
            torch.save({"state": model.state_dict(), "epoch": ep + 1, "val": v}, a.out)
    print("saved", a.out)


if __name__ == "__main__":
    main()

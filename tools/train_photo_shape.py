"""Train src/photo_shape.py's model on LiDAR roofs and their photos.

    python tools/train_photo_shape.py [--epochs 14] [--data data/photo_shape]

THE SPLIT IS BY TOWN. Neighbouring houses share builders, eras and photo
conditions, so a random split would let the model pass its test by
recognising a street it trained on. Whole regions are held out instead:
TEST_REGIONS are never seen in training and are what tools/eval_photo_shape.py
judges against the current hip-roof guess; VAL_REGIONS pick the checkpoint.
Wanaka and Hawea are the test because, like Glenorchy, they are lakeside
towns outside Queenstown with their own building stock.

THE LOSS is on LiDAR-labelled roof pixels only: whether the roof is flat
there, the downslope direction where it is not (1 - cosine), and the slope
in degrees (L1). The slope head cannot exceed 45 degrees by construction.

AUGMENTATION. Every flip and rotation of a roof is a real roof (directions
turned with it, src/photo_shape.py dihedral_maps); colour and brightness
jitter for the two photo years; and the photo nudged up to 3 px against the
answers, because photos lean up to a metre or so off the LiDAR.
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

TEST_REGIONS = {"wanaka_town", "wanaka_west", "wanaka_albert_town", "hawea"}
VAL_REGIONS = {"arrowtown_east"}
OUT = ROOT / "data" / "models" / "photo_shape_v1.pt"


def load(data_dir, keep):
    parts = {k: [] for k in ("photo", "mask", "slope", "dirx", "diry", "labelled",
                             "m_per_px", "bid", "view", "region")}
    for p in sorted(Path(data_dir).glob("*.npz")):
        d = np.load(p)
        sel = np.array([keep(r) for r in d["region"]])
        if not sel.any():
            continue
        for k in parts:
            parts[k].append(d[k][sel])
    return {k: np.concatenate(v) for k, v in parts.items()} if parts["bid"] else None


def batch_tensors(D, idx, rng, augment):
    import torch
    photo = D["photo"][idx].astype(np.float32)
    if augment:
        n = len(idx)
        gain = rng.uniform(0.8, 1.2, (n, 1, 1, 1)).astype(np.float32)
        bias = rng.uniform(-15, 15, (n, 1, 1, 1)).astype(np.float32)
        tint = rng.uniform(0.93, 1.07, (n, 3, 1, 1)).astype(np.float32)
        photo = photo * gain * tint + bias
        for i in range(n):
            sx, sy = rng.integers(-3, 4, 2)
            photo[i] = np.roll(photo[i], (sy, sx), axis=(1, 2))
    photo = np.clip(photo, 0, 255).astype(np.uint8)
    x = torch.from_numpy(ps.make_input(photo, D["mask"][idx], D["m_per_px"][idx]))
    slope = torch.from_numpy(D["slope"][idx].astype(np.float32))
    dx = torch.from_numpy(D["dirx"][idx].astype(np.float32))
    dy = torch.from_numpy(D["diry"][idx].astype(np.float32))
    lab = torch.from_numpy(D["labelled"][idx].astype(np.float32))
    if augment:
        k = int(rng.integers(8))
        x = ps.dihedral_x(x, k)
        (slope, lab), dx, dy = ps.dihedral_maps([slope, lab], dx, dy, k)
    return x, slope, dx, dy, lab


def losses(out, slope, dx, dy, lab):
    import torch
    import torch.nn.functional as F
    flat_t = (slope < ps.FLAT_DEG).float()
    L = lab > 0
    n = L.sum().clamp(min=1)
    l_flat = (F.binary_cross_entropy_with_logits(out[:, 0], flat_t, reduction="none") * L).sum() / n
    _, pdx, pdy, ps_deg = ps.decode(out)
    tilted = L & (flat_t < 0.5)
    cos = pdx * dx + pdy * dy
    l_dir = ((1 - cos) * tilted).sum() / tilted.sum().clamp(min=1)
    l_slope = ((ps_deg - slope.clamp(min=0)).abs() * L).sum() / n / 10.0
    return l_flat + l_dir + l_slope, (l_flat.item(), l_dir.item(), l_slope.item() * 10)


def evaluate(model, D, bs=256):
    """Pixel metrics on labelled roof pixels (TTA off, for speed)."""
    import torch
    model.eval()
    err_s, err_d, flat_ok, n = [], [], 0, 0
    with torch.no_grad():
        for i in range(0, len(D["bid"]), bs):
            idx = np.arange(i, min(i + bs, len(D["bid"])))
            x, slope, dx, dy, lab = batch_tensors(D, idx, None, False)
            f, pdx, pdy, s = ps.decode(model(x))
            L = lab > 0
            ft = slope < ps.FLAT_DEG
            err_s.append((s - slope.clamp(min=0)).abs()[L].numpy())
            tilt = L & ~ft
            ang = torch.rad2deg(torch.arccos((pdx * dx + pdy * dy).clamp(-1, 1)))
            err_d.append(ang[tilt].numpy())
            flat_ok += ((f > 0.5) == ft)[L].sum().item()
            n += L.sum().item()
    es, ed = np.concatenate(err_s), np.concatenate(err_d)
    return {"slope_mae_deg": float(es.mean()), "slope_median_err": float(np.median(es)),
            "dir_median_err_deg": float(np.median(ed)), "dir_within_45": float((ed < 45).mean()),
            "flat_acc": flat_ok / max(n, 1)}


def main():
    import torch
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(ROOT / "data" / "photo_shape"))
    ap.add_argument("--epochs", type=int, default=14)
    ap.add_argument("--bs", type=int, default=64)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    torch.set_num_threads(16)
    torch.manual_seed(0)
    rng = np.random.default_rng(0)
    tr = load(a.data, lambda r: r not in TEST_REGIONS | VAL_REGIONS)
    va = load(a.data, lambda r: r in VAL_REGIONS)
    print(f"train {len(tr['bid'])} samples ({len(np.unique(tr['bid']))} roofs), "
          f"val {len(va['bid'])} ({len(np.unique(va['bid']))} roofs)", flush=True)
    model = ps.build_model(pretrained=True)
    enc = [p for n, p in model.named_parameters() if n.split(".")[0] in ("stem", "l1", "l2", "l3")]
    dec = [p for n, p in model.named_parameters() if n.split(".")[0] not in ("stem", "l1", "l2", "l3")]
    opt = torch.optim.AdamW([{"params": enc, "lr": 3e-4}, {"params": dec, "lr": 1e-3}], weight_decay=1e-4)
    steps = a.epochs * (len(tr["bid"]) // a.bs)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=[3e-4, 1e-3], total_steps=steps, pct_start=0.1)
    best, history = None, []
    for ep in range(a.epochs):
        model.train()
        t0 = time.time()
        order = rng.permutation(len(tr["bid"]))
        run = np.zeros(3)
        nb = len(order) // a.bs
        for b in range(nb):
            x, slope, dx, dy, lab = batch_tensors(tr, order[b * a.bs:(b + 1) * a.bs], rng, True)
            loss, parts = losses(model(x), slope, dx, dy, lab)
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
            run += parts
        m = evaluate(model, va)
        m.update(epoch=ep + 1, train_flat=run[0] / nb, train_dir=run[1] / nb,
                 train_slope_l1=run[2] / nb, minutes=(time.time() - t0) / 60)
        history.append(m)
        print(json.dumps({k: round(v, 3) if isinstance(v, float) else v for k, v in m.items()}), flush=True)
        score = m["slope_mae_deg"] / 10 + (1 - m["dir_within_45"]) + (1 - m["flat_acc"])
        if best is None or score < best:
            best = score
            Path(a.out).parent.mkdir(parents=True, exist_ok=True)
            torch.save({"state": model.state_dict(), "epoch": ep + 1, "val": m,
                        "test_regions": sorted(TEST_REGIONS), "val_regions": sorted(VAL_REGIONS)}, a.out)
    Path(a.out).with_suffix(".history.json").write_text(json.dumps(history, indent=1))
    print("saved", a.out)


if __name__ == "__main__":
    main()

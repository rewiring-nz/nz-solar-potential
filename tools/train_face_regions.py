"""Train the face-region model: Josh's drawn faces, learned as regions.

Predicts two maps per roof -- BOUNDARY and CORE -- from RGB + LiDAR
(see tools/export_face_regions.py for why those targets and those inputs).
src/face_regions.py turns the pair into polygons by watershed.

THE DATASET IS SMALL AND THE SPLIT IS SACRED. 96 training roofs, 35 held
out and pinned to data/bench_ids.txt. Every guard here exists because a
model this size on data this size will memorise if allowed to:

  * 8x dihedral augmentation (roofs have no canonical orientation, so
    every flip and rotation is a real roof) -- 96 roofs become 768 views.
  * the loss is masked to roof pixels; street and garden teach nothing.
  * boundary pixels are ~4% of the roof, so their term is weighted up --
    unweighted, predicting "no boundary anywhere" scores well and is
    useless. This is the same trap the line detector needed pos_weight for.
  * the checkpoint saved is the one with the best HELD-OUT boundary F1,
    not the last epoch and not the best training loss.

Usage:
    python tools/train_face_regions.py --epochs 120
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

DATA = ROOT / "data" / "face_regions"
OUTM = ROOT / "data" / "models" / "face_regions_v1.pt"


def load_split(split):
    xs, ys, ws, ids = [], [], [], []
    for p in sorted((DATA / split).glob("*.npz")):
        d = np.load(p)
        xs.append(d["image"]); ys.append(d["target"])
        ws.append(d["weight"]); ids.append(int(p.stem))
    if not xs:
        raise SystemExit(f"no samples in {DATA/split} -- run export_face_regions")
    return (torch.from_numpy(np.stack(xs)), torch.from_numpy(np.stack(ys)),
            torch.from_numpy(np.stack(ws)), ids)


def dihedral(x, y, w, k):
    """One of the 8 symmetries, applied to image and targets alike."""
    if k & 4:
        x, y, w = x.flip(-1), y.flip(-1), w.flip(-1)
    r = k & 3
    if r:
        x, y, w = (torch.rot90(x, r, (-2, -1)), torch.rot90(y, r, (-2, -1)),
                   torch.rot90(w, r, (-2, -1)))
    # rot90/flip leave non-contiguous views, which autograd refuses later
    return x.contiguous(), y.contiguous(), w.contiguous()


class Block(nn.Module):
    def __init__(s, i, o):
        super().__init__()
        s.f = nn.Sequential(
            nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(True),
            nn.Conv2d(o, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(True))

    def forward(s, x):
        return s.f(x)


class UNet(nn.Module):
    """Deliberately small (~1.2 M params). With 96 roofs, capacity is the
    enemy: a wider net reaches zero training loss and learns nothing."""

    def __init__(s, cin=7, cout=2, w=24):
        super().__init__()
        s.d1, s.d2, s.d3, s.d4 = Block(cin, w), Block(w, w*2), Block(w*2, w*4), Block(w*4, w*8)
        s.u3 = Block(w*8 + w*4, w*4)
        s.u2 = Block(w*4 + w*2, w*2)
        s.u1 = Block(w*2 + w, w)
        s.out = nn.Conv2d(w, cout, 1)
        s.pool = nn.MaxPool2d(2)

    def forward(s, x):
        c1 = s.d1(x)
        c2 = s.d2(s.pool(c1))
        c3 = s.d3(s.pool(c2))
        c4 = s.d4(s.pool(c3))
        u = F.interpolate(c4, size=c3.shape[-2:], mode="nearest")
        u = s.u3(torch.cat([u, c3], 1))
        u = F.interpolate(u, size=c2.shape[-2:], mode="nearest")
        u = s.u2(torch.cat([u, c2], 1))
        u = F.interpolate(u, size=c1.shape[-2:], mode="nearest")
        u = s.u1(torch.cat([u, c1], 1))
        return s.out(u)


def masked_bce(logits, y, w, pos_weight):
    m = w.unsqueeze(1).float()
    losses = []
    for c in range(y.shape[1]):
        l = F.binary_cross_entropy_with_logits(
            logits[:, c:c+1], y[:, c:c+1], reduction="none",
            pos_weight=pos_weight[c])
        losses.append((l * m).sum() / m.sum().clamp(min=1))
    return sum(losses)


def boundary_f1(logits, y, w, thr=0.5):
    p = (torch.sigmoid(logits[:, 0]) > thr) & (w > 0)
    t = (y[:, 0] > 0.5) & (w > 0)
    tp = (p & t).sum().item(); fp = (p & ~t).sum().item(); fn = (~p & t).sum().item()
    return 2 * tp / max(2 * tp + fp + fn, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=120)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--batch", type=int, default=8)
    a = ap.parse_args()

    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    xt, yt, wt, _ = load_split("train")
    xv, yv, wv, vid = load_split("val")
    print(f"train {len(xt)} roofs   val {len(xv)} roofs   device {dev}")

    # boundary is ~4% of roof pixels, core ~70%: weight each class by its
    # own scarcity so neither term can be won by predicting a constant.
    pw = []
    for c in range(2):
        frac = ((yt[:, :, :, c] > 127) & (wt > 0)).float().sum() / wt.sum().clamp(min=1)
        pw.append(float(min(20.0, max(1.0, (1 - frac) / frac.clamp(min=1e-6)))))
    print(f"pos_weight boundary {pw[0]:.1f}  core {pw[1]:.1f}")
    pos_weight = [torch.tensor(v, device=dev) for v in pw]

    net = UNet().to(dev)
    opt = torch.optim.AdamW(net.parameters(), lr=a.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, a.epochs)
    xv_d = (xv.permute(0, 3, 1, 2).float() / 255).to(dev)
    yv_d = (yv.permute(0, 3, 1, 2).float() / 255).to(dev)
    wv_d = wv.to(dev)

    best, t0 = -1.0, time.time()
    for ep in range(a.epochs):
        net.train()
        perm = torch.randperm(len(xt))
        tot = 0.0
        for i in range(0, len(perm), a.batch):
            idx = perm[i:i + a.batch]
            x = (xt[idx].permute(0, 3, 1, 2).float() / 255)
            y = (yt[idx].permute(0, 3, 1, 2).float() / 255)
            w = wt[idx]
            x, y, w = dihedral(x, y, w, int(torch.randint(0, 8, (1,))))
            x, y, w = x.to(dev), y.to(dev), w.to(dev)
            opt.zero_grad()
            loss = masked_bce(net(x), y, w, pos_weight)
            loss.backward(); opt.step()
            tot += float(loss) * len(idx)
        sched.step()
        if ep % 5 == 4 or ep == a.epochs - 1:
            net.eval()
            with torch.no_grad():
                lg = net(xv_d)
                f1 = boundary_f1(lg, yv_d, wv_d)
                vl = float(masked_bce(lg, yv_d, wv_d, pos_weight))
            flag = ""
            if f1 > best:
                best = f1
                OUTM.parent.mkdir(parents=True, exist_ok=True)
                torch.save({"state_dict": net.state_dict(), "cin": 7,
                            "cout": 2, "val_boundary_f1": f1,
                            "val_ids": vid}, OUTM)
                flag = "  <- saved"
            print(f"ep {ep+1:3d}  train {tot/len(xt):.4f}  val {vl:.4f}  "
                  f"held-out boundary F1 {f1:.3f}{flag}", flush=True)
    print(f"best held-out boundary F1 {best:.3f}  ({time.time()-t0:.0f}s)  -> {OUTM}")


if __name__ == "__main__":
    main()

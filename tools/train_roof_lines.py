"""Roof-line reader: a pretrained DINOv2 ViT-S/14 photo encoder with a LiDAR
side branch, predicting the BOUNDARY between roof faces and each face's CORE.

    python train.py <data dir> --out model.pt [--epochs 30] [--train vision points] [--val hand]
"""
import argparse, glob, math, os, random, sys, time
from pathlib import Path
import numpy as np, torch, torch.nn as nn, torch.nn.functional as F

MEAN = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
STD = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)
SIZE = 392                                  # 28 x 28 patches of 14


class Roofs(torch.utils.data.Dataset):
    def __init__(self, files, train):
        self.files, self.train = files, train

    def __len__(self):
        return len(self.files)

    def __getitem__(self, i):
        d = np.load(self.files[i])
        x = d["image"].astype(np.float32) / 255.0          # H W 7
        y = d["target"].astype(np.float32)                 # H W 2
        w = d["weight"].astype(np.float32)[..., None]
        if self.train:
            k = random.randrange(8)
            if k >= 4:                                     # flip left-right: east/west downhill flips
                x, y, w = x[:, ::-1], y[:, ::-1], w[:, ::-1]
                x = x.copy(); x[..., 5] = 1 - x[..., 5]
            for _ in range(k % 4):                         # quarter turn anticlockwise
                x, y, w = np.rot90(x), np.rot90(y), np.rot90(w)
                x = x.copy(); c5, c6 = x[..., 5].copy(), x[..., 6].copy()
                x[..., 5], x[..., 6] = 1 - c6, c5
            g = random.uniform(0.75, 1.3); bsh = random.uniform(-0.08, 0.08)
            x = x.copy(); x[..., :3] = np.clip(x[..., :3] ** g + bsh, 0, 1)
            if random.random() < 0.15:                     # sometimes no LiDAR: read the photo alone
                x[..., 3:] = 0
        t = lambda a: torch.from_numpy(np.ascontiguousarray(a)).permute(2, 0, 1)
        return t(x), t(y), t(w)


def conv(i, o):
    return nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.GroupNorm(8, o), nn.GELU(),
                         nn.Conv2d(o, o, 3, padding=1), nn.GroupNorm(8, o), nn.GELU())


class Net(nn.Module):
    def __init__(self, hub=None):
        super().__init__()
        hub = hub or str(Path.home() / ".cache/torch/hub/facebookresearch_dinov2_main")
        self.enc = torch.hub.load(hub, "dinov2_vits14", source="local", pretrained=False)
        # the fine branch starts at half resolution: at full resolution a batch
        # of 8 did not fit a 19 GB Mac and each step took a minute
        self.s1 = conv(7, 32)                    # 1/2: photo + LiDAR, for sharp lines
        self.s2 = conv(32, 64)                   # 1/4
        self.s4 = conv(64, 96)                   # 1/8
        self.lid = conv(4, 32)                   # LiDAR at patch scale
        self.neck = conv(384 * 4 + 32, 256)      # 1/14
        self.d4 = conv(256 + 96, 128)
        self.d2 = conv(128 + 64, 64)
        self.d1 = conv(64 + 32, 32)
        self.head = nn.Conv2d(32, 2, 1)

    def forward(self, x):
        rgb = (x[:, :3] - MEAN.to(x.device)) / STD.to(x.device)
        f = self.enc.get_intermediate_layers(rgb, n=4, reshape=True)        # 4 x (B,384,28,28)
        a1 = self.s1(F.avg_pool2d(x, 2)); a2 = self.s2(F.avg_pool2d(a1, 2)); a4 = self.s4(F.avg_pool2d(a2, 2))
        l = self.lid(F.adaptive_avg_pool2d(x[:, 3:], f[0].shape[-2:]))
        z = self.neck(torch.cat([*f, l], 1))
        z = self.d4(torch.cat([F.interpolate(z, size=a4.shape[-2:], mode="bilinear", align_corners=False), a4], 1))
        z = self.d2(torch.cat([F.interpolate(z, size=a2.shape[-2:], mode="bilinear", align_corners=False), a2], 1))
        z = self.d1(torch.cat([F.interpolate(z, size=a1.shape[-2:], mode="bilinear", align_corners=False), a1], 1))
        return F.interpolate(self.head(z), size=x.shape[-2:], mode="bilinear", align_corners=False)


def prep(x, y, w):
    r = lambda a, m: F.interpolate(a, size=(SIZE, SIZE), mode=m, **({"align_corners": False} if m == "bilinear" else {}))
    return r(x, "bilinear"), (r(y, "bilinear") > 0.5).float(), r(w, "nearest")


def loss_fn(logit, y, w):
    pw = torch.tensor([6.0, 1.0], device=logit.device).view(1, 2, 1, 1)
    bce = F.binary_cross_entropy_with_logits(logit, y, pos_weight=pw, reduction="none")
    bce = (bce * w).sum() / (w.sum() * 2 + 1e-6)
    p = torch.sigmoid(logit[:, :1]) * w; t = y[:, :1] * w
    dice = 1 - (2 * (p * t).sum() + 1) / (p.sum() + t.sum() + 1)
    return bce + 0.5 * dice


@torch.no_grad()
def boundary_f1(logit, y, w, tol=2):
    """Boundary F1 with a tolerance of `tol` pixels (a line 2 px off is still that line)."""
    p = (torch.sigmoid(logit[:, :1]) > 0.5).float() * w
    t = y[:, :1] * w
    k = 2 * tol + 1
    pd = F.max_pool2d(p, k, 1, tol); td = F.max_pool2d(t, k, 1, tol)
    tp_p = (p * td).sum(); tp_t = (t * pd).sum()
    return tp_p.item(), p.sum().item(), tp_t.item(), t.sum().item()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data"); ap.add_argument("--out", default="lines_dino.pt")
    ap.add_argument("--epochs", type=int, default=30); ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--train", nargs="*", default=["vision", "points"]); ap.add_argument("--val", nargs="*", default=["hand"])
    ap.add_argument("--limit", type=int, default=0); ap.add_argument("--init", default=None)
    ap.add_argument("--lr", type=float, default=1e-3); ap.add_argument("--enc-lr", type=float, default=2e-5)
    ap.add_argument("--folds", type=int, default=0, help="split the val kinds into this many folds: fold --fold is the test, the rest train")
    ap.add_argument("--fold", type=int, default=0)
    ap.add_argument("--repeat-val-kind", type=int, default=1, help="times each hand-drawn training roof is seen per epoch")
    a = ap.parse_args()
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    tr = [f for k in a.train for f in sorted(glob.glob(f"{a.data}/{k}/*.npz"))]
    va = [f for k in a.val for f in sorted(glob.glob(f"{a.data}/{k}/*.npz"))]
    if a.folds:
        # hand-drawn roofs are the best labels there are: all but one fold of
        # them train, and that fold alone is the test, so every hand-drawn
        # roof is scored by a model that never saw it
        random.Random(11).shuffle(va)
        test = va[a.fold::a.folds]
        tr += [f for f in va if f not in set(test)] * a.repeat_val_kind
        va = test
    random.Random(0).shuffle(tr)
    if a.limit:
        tr = tr[:a.limit]
    print(f"{len(tr)} training roofs, {len(va)} validation roofs, {dev}", flush=True)
    net = Net()
    sd = torch.load(Path.home() / ".cache/torch/hub/checkpoints/dinov2_vits14_pretrain.pth", map_location="cpu")
    net.enc.load_state_dict(sd)
    if a.init:
        net.load_state_dict(torch.load(a.init, map_location="cpu"))
    net.to(dev)
    enc = list(net.enc.parameters()); rest = [p for n, p in net.named_parameters() if not n.startswith("enc.")]
    opt = torch.optim.AdamW([{"params": enc, "lr": a.enc_lr}, {"params": rest, "lr": a.lr}], weight_decay=0.01)
    dl = torch.utils.data.DataLoader(Roofs(tr, True), batch_size=a.batch, shuffle=True, num_workers=6, drop_last=True, persistent_workers=True)
    dv = torch.utils.data.DataLoader(Roofs(va, False), batch_size=a.batch, num_workers=2)
    steps = a.epochs * len(dl); step = 0; best = -1
    for ep in range(a.epochs):
        net.train(); t0 = time.time(); tl = 0
        for x, y, w in dl:
            lr_s = 0.5 * (1 + math.cos(math.pi * step / steps)) * min(1.0, step / 200)
            opt.param_groups[0]["lr"] = a.enc_lr * lr_s; opt.param_groups[1]["lr"] = a.lr * lr_s
            x, y, w = prep(x.to(dev), y.to(dev), w.to(dev))
            loss = loss_fn(net(x), y, w)
            opt.zero_grad(); loss.backward(); nn.utils.clip_grad_norm_(net.parameters(), 1.0); opt.step()
            tl += loss.item(); step += 1
        net.eval(); s = np.zeros(4)
        with torch.no_grad():
            for x, y, w in dv:
                x, y, w = prep(x.to(dev), y.to(dev), w.to(dev))
                s += np.array(boundary_f1(net(x), y, w))
        pr, rc = s[0] / max(s[1], 1), s[2] / max(s[3], 1); f1 = 2 * pr * rc / max(pr + rc, 1e-9)
        print(f"epoch {ep + 1}/{a.epochs} loss {tl / max(len(dl), 1):.4f} val boundary F1 {f1:.3f} (precision {pr:.3f} recall {rc:.3f}) {time.time() - t0:.0f}s", flush=True)
        if f1 > best:
            best = f1; torch.save(net.state_dict(), a.out)
    print("best", round(best, 3))


if __name__ == "__main__":
    main()

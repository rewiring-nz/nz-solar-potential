"""Roof-line reader: a pretrained DINOv2 ViT-S/14 photo encoder with a LiDAR
side branch. It predicts eight maps (tools/line_targets.py): the line between
roof faces, each face's core, the line by kind (ridge, valley, cliff), the
roof's outer edge, the points where lines join, and the roof itself. A
sample teaches only the maps its label knows; the first-version set
(two maps) still trains the first two.

    python train.py <data dir> --out model.pt [--epochs 30] [--train vision points] [--val hand]
"""
import argparse, glob, json, math, os, random, sys, time
from pathlib import Path
import numpy as np, torch, torch.nn as nn, torch.nn.functional as F

MEAN = torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1)
STD = torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1)
SIZE = int(os.environ.get("SOLAR_LINES_SIZE", "392"))    # a multiple of the 14 px patch: 392 is 28 x 28 patches
NOUT = 8
NAMES = ["line", "core", "ridge", "valley", "cliff", "outline", "join", "roof"]
WHOLE = [5, 7]                              # scored on the whole picture, not just the roof
# a join is ~28 px of a 150,000 px picture and a line is a few hundred
POS_WEIGHT = [6.0, 1.0, 8.0, 8.0, 8.0, 8.0, 20.0, 1.0]


class Roofs(torch.utils.data.Dataset):
    def __init__(self, files, train):
        self.files, self.train = files, train

    def __len__(self):
        return len(self.files)

    def __getitem__(self, i):
        d = np.load(self.files[i])
        x = d["image"].astype(np.float32) / 255.0          # H W 7
        y = d["target"].astype(np.float32)                 # H W 2 (first set) or 8
        roof = d["weight"].astype(np.float32)[..., None]
        if y.shape[-1] == NOUT:
            known = d["known"].astype(np.float32); near = d["near"].astype(np.float32)[..., None]
        else:
            y = np.concatenate([y, np.zeros(y.shape[:2] + (NOUT - 2,), np.float32)], -1)
            known = np.array([1, 1] + [0] * (NOUT - 2), np.float32); near = roof
        # per-map scoring area: the first two inside the roof (as the first
        # version), the typed lines and joins on the roof and just beyond it,
        # the outline and the roof on the whole picture
        w = np.concatenate([roof, roof] + [np.ones_like(roof) if c in WHOLE else near for c in range(2, NOUT)], -1) * known
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
            if random.random() < 0.5:
                # the LiDAR sits up to a few tenths of a metre off the photo
                # even after alignment: the photo, not the LiDAR, places a line
                x[..., 3:] = np.roll(x[..., 3:], (random.randint(-4, 4), random.randint(-4, 4)), axis=(0, 1))
            if random.random() < 0.15:                     # sometimes no LiDAR: read the photo alone
                x[..., 3:] = 0
        t = lambda a: torch.from_numpy(np.ascontiguousarray(a)).permute(2, 0, 1)
        return t(x), t(y), t(w)


def conv(i, o):
    return nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.GroupNorm(8, o), nn.GELU(),
                         nn.Conv2d(o, o, 3, padding=1), nn.GroupNorm(8, o), nn.GELU())


class Net(nn.Module):
    def __init__(self, hub=None, nout=NOUT):
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
        self.head = nn.Conv2d(32, nout, 1)

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


def load_net(path):
    """A saved reader, whichever number of maps it was trained with."""
    sd = torch.load(path, map_location="cpu")
    net = Net(nout=sd["head.weight"].shape[0])
    net.load_state_dict(sd)
    return net.eval()


def prep(x, y, w):
    r = lambda a, m: F.interpolate(a, size=(SIZE, SIZE), mode=m, **({"align_corners": False} if m == "bilinear" else {}))
    return r(x, "bilinear"), (r(y, "bilinear") > 0.5).float(), r(w, "nearest")


def loss_fn(logit, y, w):
    pw = torch.tensor(POS_WEIGHT, device=logit.device).view(1, NOUT, 1, 1)
    bce = F.binary_cross_entropy_with_logits(logit, y, pos_weight=pw, reduction="none")
    # each map averaged over the pixels that know it, then the maps averaged:
    # the whole-picture maps would otherwise outweigh the line maps
    per = (bce * w).sum((0, 2, 3)) / (w.sum((0, 2, 3)) + 1e-6)
    has = (w.sum((0, 2, 3)) > 0).float()
    bce = (per * has).sum() / has.sum().clamp(min=1)
    dice = 0
    for c in (0, 5):
        p = torch.sigmoid(logit[:, c]) * w[:, c]; t = y[:, c] * w[:, c]
        dice = dice + 1 - (2 * (p * t).sum() + 1) / (p.sum() + t.sum() + 1)
    return bce + 0.25 * dice


@torch.no_grad()
def boundary_f1(logit, y, w, tol=2):
    """Per map: F1 parts with a tolerance of `tol` pixels (a line 2 px off is
    still that line). Returns NOUT x 4."""
    p = (torch.sigmoid(logit) > 0.5).float() * w
    t = y * w
    k = 2 * tol + 1
    pd = F.max_pool2d(p, k, 1, tol); td = F.max_pool2d(t, k, 1, tol)
    s = lambda a: a.sum((0, 2, 3))
    return torch.stack([s(p * td), s(p), s(t * pd), s(t)], 1).cpu().numpy()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("data"); ap.add_argument("--out", default="lines_dino.pt")
    ap.add_argument("--epochs", type=int, default=30); ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--train", nargs="*", default=["vision", "points"]); ap.add_argument("--val", nargs="*", default=["hand"])
    ap.add_argument("--limit", type=int, default=0); ap.add_argument("--init", default=None)
    ap.add_argument("--lr", type=float, default=1e-3); ap.add_argument("--enc-lr", type=float, default=2e-5)
    ap.add_argument("--folds", type=int, default=0, help="split the val kinds into this many folds: fold --fold is the test, the rest train")
    ap.add_argument("--fold", type=int, default=0)
    ap.add_argument("--fold-file", default=None, help="JSON {roof id: fold}; overrides the shuffled split")
    ap.add_argument("--repeat-val-kind", type=int, default=1, help="times each hand-drawn training roof is seen per epoch")
    a = ap.parse_args()
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    tr = [f for k in a.train for f in sorted(glob.glob(f"{a.data}/{k}/*.npz"))]
    va = [f for k in a.val for f in sorted(glob.glob(f"{a.data}/{k}/*.npz"))]
    if a.folds:
        # hand-drawn roofs are the best labels there are: all but one fold of
        # them train, and that fold alone is the test, so every hand-drawn
        # roof is scored by a model that never saw it
        if a.fold_file:
            # a fixed fold per roof, so roofs added later do not move the
            # earlier ones between folds and scores stay comparable
            ff = json.load(open(a.fold_file))
            test = [f for f in va if ff.get(os.path.basename(f)[:-4]) == a.fold]
        else:
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
        init = torch.load(a.init, map_location="cpu")
        own = net.state_dict()
        for k in ("head.weight", "head.bias"):        # a two-map model starts the first two maps
            if init[k].shape[0] != NOUT:
                new = own[k].clone(); new[:init[k].shape[0]] = init[k]; init[k] = new
        net.load_state_dict(init)
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
        net.eval(); s = np.zeros((NOUT, 4))
        with torch.no_grad():
            for x, y, w in dv:
                x, y, w = prep(x.to(dev), y.to(dev), w.to(dev))
                s += boundary_f1(net(x), y, w)
        prs, rcs = s[:, 0] / np.maximum(s[:, 1], 1), s[:, 2] / np.maximum(s[:, 3], 1)
        f1s = 2 * prs * rcs / np.maximum(prs + rcs, 1e-9)
        pr, rc, f1 = prs[0], rcs[0], f1s[0]
        typed = " ".join(f"{NAMES[c]} {f1s[c]:.2f}" for c in (2, 3, 4, 5, 6) if s[c, 3] > 0)
        print(f"epoch {ep + 1}/{a.epochs} loss {tl / max(len(dl), 1):.4f} val boundary F1 {f1:.3f} (precision {pr:.3f} recall {rc:.3f}) {typed} {time.time() - t0:.0f}s", flush=True)
        if f1 > best:
            best = f1; torch.save(net.state_dict(), a.out)
    print("best", round(best, 3))


if __name__ == "__main__":
    main()

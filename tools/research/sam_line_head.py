"""
Does a line head on frozen SAM features find roof lines better than the U-Net?

WHY. The error budget (BACKLOG "Roof lines") puts most of the panel gap on
roof shape: built with the drawn faces and lines, panel area IoU against the
drawn layout is 0.78; built from the photo reading it is 0.52. The line
detector is the weak step, and its learning curve (tools/train_line_model.py
--curve) is still climbing at 92 roofs: mean F1 0.27 at 60, 0.39 at 92. A
stronger pretrained encoder is the label-free way to move along that curve.
SAM ViT-B was pretrained on 11M images for boundaries; the U-Net's ResNet18
was pretrained on ImageNet classes.

THE TEST. Same patches, same split by roof, same loss, same positive
weights and same F1 at threshold 0.5 as train_line_model.py, so the numbers
compare directly with its curve row for all train roofs.
  * Features: four 128 px patches, each upsampled 4x, tiled 2x2 into one
    1024 px SAM input; the encoder gives 32x32x256 per patch (one feature per
    4 px). Cached once in float16.
  * Head: the features through two convolutions, upsampled to 128 px and
    joined with a small full-resolution RGB branch (lines are 2-4 px wide,
    finer than a feature cell), then two convolutions to the four channels.
  * Augmentation: dihedral flips and rotations of features and masks
    together (SAM features are near-equivariant to them; recomputing eight
    versions of every patch would cost 8x the encoder time).

Usage (on the VM):
    python tools/research/sam_line_head.py --cache data/sam_line_feats.pt
"""

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import train_line_model as T  # noqa: E402

FEAT = 32          # SAM feature cells per patch side
UP = 4             # patch upsampling into the SAM input


def sam_features(x, threads):
    """x: N x 3 x 128 x 128 uint8 -> N x 256 x 32 x 32 float16."""
    import torch
    import torch.nn.functional as F
    from segment_anything import sam_model_registry
    torch.set_num_threads(threads)
    sam = sam_model_registry["vit_b"](checkpoint=str(ROOT / "data/sam_vit_b.pth"))
    enc = sam.image_encoder.eval()
    mean = torch.tensor([123.675, 116.28, 103.53]).view(1, 3, 1, 1)
    std = torch.tensor([58.395, 57.12, 57.375]).view(1, 3, 1, 1)
    n = len(x)
    out = torch.empty(n, 256, FEAT, FEAT, dtype=torch.float16)
    s = 128 * UP
    t0 = time.time()
    with torch.no_grad():
        for i in range(0, n, 4):
            xb = x[i:i + 4].float()
            k = len(xb)
            xb = F.interpolate(xb, size=(s, s), mode="bilinear", align_corners=False)
            img = torch.zeros(1, 3, 2 * s, 2 * s)
            for j in range(k):
                r, c = divmod(j, 2)
                img[0, :, r * s:(r + 1) * s, c * s:(c + 1) * s] = xb[j]
            f = enc((img - mean) / std)[0]                 # 256 x 64 x 64
            for j in range(k):
                r, c = divmod(j, 2)
                out[i + j] = f[:, r * FEAT:(r + 1) * FEAT, c * FEAT:(c + 1) * FEAT].half()
            if (i // 4) % 25 == 0:
                print(f"  features {i + k}/{n}  {time.time() - t0:.0f}s", flush=True)
    return out


def build_head(out_channels=4):
    import torch
    import torch.nn as nn

    def block(i, o):
        return nn.Sequential(
            nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True))

    class Head(nn.Module):
        def __init__(self):
            super().__init__()
            self.f = nn.Sequential(block(256, 128), block(128, 64))
            self.up = nn.Sequential(
                nn.ConvTranspose2d(64, 64, 2, 2), nn.ReLU(inplace=True),
                nn.ConvTranspose2d(64, 32, 2, 2), nn.ReLU(inplace=True))
            self.rgb = nn.Sequential(block(3, 16), block(16, 32))
            self.mix = nn.Sequential(block(64, 32), block(32, 32))
            self.out = nn.Conv2d(32, out_channels, 1)

        def forward(self, feats, rgb):
            a = self.up(self.f(feats))                     # 32 x 128 x 128
            return self.out(self.mix(torch.cat([a, self.rgb(rgb)], 1)))

    return Head()


def evaluate(head, fv, xv, yv, wv, thr=0.5):
    import torch
    head.eval()
    with torch.no_grad():
        p = torch.cat([torch.sigmoid(head(fv[i:i + 32].float(), xv[i:i + 32].float() / 255))
                       for i in range(0, len(fv), 32)])
    y = yv.float() / 255
    w = wv.float() / 255
    f1s = []
    for k in range(p.shape[1]):
        pk = (p[:, k] > thr).float() * w[:, 0]
        tk = y[:, k] * w[:, 0]
        f1s.append((2 * (pk * tk).sum() / (pk.sum() + tk.sum() + 1e-6)).item())
    return f1s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=str(ROOT / "data/sam_line_feats.pt"))
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--threads", type=int, default=14)
    a = ap.parse_args()

    import torch
    torch.set_num_threads(a.threads)
    manifest = json.loads((T.DATA / "manifest.json").read_text())
    tr = T.load_split(manifest, "train")
    va = T.load_split(manifest, "val")
    print(f"{len(tr[0])} train patches, {len(va[0])} val patches")

    cache = Path(a.cache)
    if cache.exists():
        c = torch.load(cache)
        ft, fv = c["train"], c["val"]
    else:
        ft = sam_features(tr[0], a.threads)
        fv = sam_features(va[0], a.threads)
        torch.save({"train": ft, "val": fv}, cache)
        print(f"wrote {cache}")

    torch.manual_seed(0)
    head = build_head(tr[1].shape[1])
    x, y, w, cw = tr
    with torch.no_grad():
        freq = (y[:2000].float() / 255).mean((0, 2, 3)).clamp_min(1e-5)
    pos = (0.12 / freq).clamp(6.0, 40.0).view(1, -1, 1, 1)
    n = len(x)
    opt = torch.optim.AdamW(head.parameters(), lr=a.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.OneCycleLR(
        opt, max_lr=a.lr, total_steps=a.epochs * ((n + 15) // 16))
    t0 = time.time()
    for ep in range(a.epochs):
        head.train()
        perm = torch.randperm(n)
        tot = 0.0
        for i in range(0, n, 16):
            idx = perm[i:i + 16]
            fb = ft[idx].float()
            xb = x[idx].float() / 255
            yb = y[idx].float() / 255
            wb = w[idx].float() / 255
            cwb = cw[idx].view(len(idx), -1, 1, 1)
            xb = (xb * float(torch.empty(1).uniform_(0.8, 1.2))
                  + float(torch.empty(1).uniform_(-0.08, 0.08))).clamp(0, 1)
            ts = [fb, xb, yb, wb]
            if torch.rand(1).item() < 0.5:
                ts = [t.flip(-1) for t in ts]
            if torch.rand(1).item() < 0.5:
                ts = [t.flip(-2) for t in ts]
            k = int(torch.randint(0, 4, (1,)).item())
            if k:
                ts = [torch.rot90(t, k, (-2, -1)) for t in ts]
            fb, xb, yb, wb = [t.contiguous() for t in ts]
            opt.zero_grad()
            l = T.loss_fn(head(fb, xb), yb, wb, pos, cwb)
            l.backward()
            opt.step()
            sched.step()
            tot += l.item()
        if (ep + 1) % 5 == 0:
            f1 = evaluate(head, fv, va[0], va[1], va[2])
            print(f"  epoch {ep + 1:>3}  loss {tot / max(1, n // 16):.3f}  val F1 "
                  + " ".join(f"{k[:3]} {v:.3f}" for k, v in zip(T.KINDS, f1))
                  + f"  ({time.time() - t0:.0f}s)", flush=True)
    f1 = evaluate(head, fv, va[0], va[1], va[2])
    print("\nSAM head final val F1: "
          + "  ".join(f"{k} {v:.3f}" for k, v in zip(T.KINDS, f1))
          + f"   mean(/3, as train_line_model) {sum(f1) / 3:.3f}")
    torch.save({"state_dict": head.state_dict(), "kinds": T.KINDS},
               ROOT / "data/models/sam_line_head.pt")
    return 0


if __name__ == "__main__":
    sys.exit(main())

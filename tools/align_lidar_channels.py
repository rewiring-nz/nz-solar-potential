"""Move a training sample's LiDAR channels onto its photo.

A photo is rectified to the ground, so a roof is drawn some way from where
the LiDAR has it (its lean, typically 0.3 to 1 m). The first export moved
the LiDAR by the recorded drawing shift, which exists for few buildings
outside the pilot (158 of 716 readings, 0 of 88 drawn roofs): on the rest
the LiDAR channels sit off the photo and off the label drawn on it, and the
reader is taught two answers for one line.

The label is drawn on the photo, so it says where the LiDAR belongs: the
LiDAR's own edges (creases where the downhill direction turns, steps where
it is steep, and the edge of the building's returns) are slid over the
label's lines and outline, +/- SEARCH_M, and the slide with the most overlap
is kept when it beats no slide by MIN_GAIN. Channels 3..6 of the picture
are then translated by that slide (edge pixels repeat).

    python tools/align_lidar_channels.py <typed set in> <out> [--sheet x.jpg]
"""
import argparse, glob, os
import numpy as np
from scipy.ndimage import gaussian_filter, binary_erosion, binary_dilation, shift as nd_shift

SEARCH_M, MIN_GAIN, PRIOR_PER_M = 1.6, 1.08, 0.04


def lidar_edges(x, roof):
    """Edge evidence from the LiDAR channels (H W 7 floats 0..1)."""
    gx = np.hypot(*np.gradient(gaussian_filter(x[..., 5], 1.5)))
    gy = np.hypot(*np.gradient(gaussian_filter(x[..., 6], 1.5)))
    crease = gaussian_filter(gx + gy, 1.0)
    step = np.clip((x[..., 4] - 0.8) / 0.2, 0, 1)             # steeper than ~36 degrees: a wall or drop
    has = gaussian_filter(x[..., 3], 1.0) > 0.02
    foot = binary_dilation(has) & ~binary_erosion(has)
    near = binary_dilation(roof, iterations=12)
    e = lambda a: a / max(float(a[near].mean()), 1e-6)
    return e(crease) * near, e(step) * near, foot.astype(np.float32) * near


def best_slide(d):
    """(dx, dy) pixels to move the LiDAR channels by, and the gain over no move."""
    t, roof = d["target"], d["weight"].astype(bool)
    inner = binary_erosion(roof, iterations=5)
    return slide_for(d["image"].astype(np.float32) / 255.0, (t[..., 0] & inner).astype(np.float32),
                     t[..., 5].astype(np.float32), roof, float(d["meta"][0]))


def slide_for(x, lines, outline, roof, mpp):
    """The slide of the LiDAR channels of picture x (H W 7, 0..1) onto line and
    outline maps in the photo frame: a label's, or the reader's own first
    reading of the photo when there is no label."""
    crease, step, foot = lidar_edges(x, roof)
    lines = gaussian_filter(lines, 1.0)
    outline = gaussian_filter(outline, 1.0)
    if lines.sum() < 40:
        return 0, 0, 1.0
    F, iF = np.fft.rfft2, np.fft.irfft2
    corr = lambda a, b: iF(np.conj(F(a)) * F(b), s=a.shape)   # corr[dy, dx]: a moved by (dx, dy) over b
    surf = corr(crease + 0.5 * step, lines) / max(lines.sum(), 1) + 0.5 * corr(step + foot, outline) / max(outline.sum(), 1)
    r = int(round(SEARCH_M / mpp))
    best = (-1e9, 0, 0)
    zero = float(surf[0, 0])
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            v = float(surf[dy % surf.shape[0], dx % surf.shape[1]]) - PRIOR_PER_M * zero * np.hypot(dx, dy) * mpp
            if v > best[0]:
                best = (v, dx, dy)
    gain = best[0] / max(zero, 1e-9)
    return (best[1], best[2], gain) if gain >= MIN_GAIN else (0, 0, gain)


def moved(img, dx, dy):
    out = img.copy()
    for c in range(3, img.shape[-1]):
        out[..., c] = nd_shift(img[..., c].astype(np.float32), (dy, dx), order=1, mode="nearest").clip(0, 255).astype(np.uint8)
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("src"); ap.add_argument("out"); ap.add_argument("--sheet")
    a = ap.parse_args()
    tiles = []
    for kind in ("hand", "vision"):
        os.makedirs(f"{a.out}/{kind}", exist_ok=True)
        ms = []
        for f in sorted(glob.glob(f"{a.src}/{kind}/*.npz")):
            d = dict(np.load(f))
            dx, dy, gain = best_slide(d)
            mpp = float(d["meta"][0])
            ms.append(np.hypot(dx, dy) * mpp)
            img = moved(d["image"], dx, dy) if (dx or dy) else d["image"]
            if a.sheet and kind == "hand" and len(tiles) < 10 and ms[-1] > 0.5:
                def view(im):
                    v = im[..., :3].copy().astype(np.float32) * 0.5
                    cr, st, _ = lidar_edges(im.astype(np.float32) / 255.0, d["weight"].astype(bool))
                    v[..., 0] += np.clip(cr * 60, 0, 127); v[d["target"][..., 0]] = [255, 255, 0]
                    return v.clip(0, 255).astype(np.uint8)
                tiles.append(np.hstack([view(d["image"]), view(img)]))
            d["image"] = img; d["lidar_slide_px"] = np.array([dx, dy])
            np.savez_compressed(f"{a.out}/{kind}/{os.path.basename(f)}", **d)
        ms = np.array(ms)
        print(f"{kind}: {len(ms)} roofs, moved {(ms > 0).sum()}, median move of those {np.median(ms[ms > 0]) if (ms > 0).any() else 0:.2f} m, "
              f"over 0.5 m: {(ms > 0.5).sum()}", flush=True)
    if a.sheet and tiles:
        from PIL import Image
        Image.fromarray(np.vstack([np.hstack(tiles[i:i + 2]) for i in range(0, len(tiles) - 1, 2)])).save(a.sheet, quality=85)


if __name__ == "__main__":
    main()

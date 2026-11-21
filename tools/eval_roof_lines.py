"""The roof-line reader against hand-drawn roofs, per roof, in metres.

    python tools/eval_roof_lines.py <model.pt> <data dir>/hand [--tol 0.3] [--sheet out.jpg]

Per roof: of the drawn inner lines, the share within `tol` metres of a
predicted line (recall); of the predicted lines, the share within `tol` of a
drawn one (precision); F. A roof with no drawn inner line and none predicted
scores 1. Reports the mean and how many roofs reach 0.7 and 0.9.
"""
import argparse, glob, sys
from pathlib import Path
import numpy as np, torch
import torch.nn.functional as F
sys.path.insert(0, str(Path(__file__).resolve().parent))
import train_roof_lines as T


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model"); ap.add_argument("data"); ap.add_argument("--tol", type=float, default=0.3)
    ap.add_argument("--sheet"); ap.add_argument("--thr", type=float, default=0.5); ap.add_argument("--device", default="cpu")
    ap.add_argument("--folds", type=int, default=0); ap.add_argument("--fold", type=int, default=0)
    ap.add_argument("--csv")
    a = ap.parse_args()
    from scipy.ndimage import distance_transform_edt
    from skimage.morphology import skeletonize
    net = T.Net(); net.load_state_dict(torch.load(a.model, map_location="cpu")); net.eval().to(a.device)
    rows, tiles = [], []
    files = sorted(glob.glob(f"{a.data}/*.npz"))
    if a.folds:                      # the same split as train_roof_lines.py --folds
        import random
        random.Random(11).shuffle(files)
        files = files[a.fold::a.folds]
    for f in files:
        d = np.load(f)
        x = torch.from_numpy(d["image"].astype(np.float32) / 255).permute(2, 0, 1)[None].to(a.device)
        with torch.no_grad():
            p = torch.sigmoid(net(F.interpolate(x, size=(T.SIZE, T.SIZE), mode="bilinear", align_corners=False)))
            p = F.interpolate(p, size=x.shape[-2:], mode="bilinear", align_corners=False)[0].cpu().numpy()
        w = d["weight"]; t = d["target"][..., 0] & w
        pb = skeletonize((p[0] > a.thr) & w); tb = skeletonize(t)
        tol = a.tol / float(d["meta"][0])
        if tb.sum() == 0 and pb.sum() == 0:
            pr = rc = fs = 1.0
        else:
            rc = float((distance_transform_edt(~pb)[tb] <= tol).mean()) if tb.sum() and pb.sum() else 0.0
            pr = float((distance_transform_edt(~tb)[pb] <= tol).mean()) if tb.sum() and pb.sum() else 0.0
            fs = 2 * pr * rc / (pr + rc) if pr + rc else 0.0
        rows.append((Path(f).stem, fs, pr, rc, round(float(w.sum()) * float(d["meta"][0]) ** 2), str(d["region"])))
        if a.sheet:
            im = d["image"][..., :3].copy(); o1 = im.copy(); o2 = im.copy()
            o1[d["target"][..., 0]] = [255, 255, 0]; o2[(p[0] > a.thr) & w] = [255, 0, 0]
            tiles.append((fs, np.hstack([im, o1, o2])))
    if a.csv:
        with open(a.csv, "a") as fh:
            for r in rows:
                fh.write(",".join(str(v) for v in r) + "\n")
    fsv = np.array([r[1] for r in rows])
    print(f"{len(rows)} roofs at {a.tol} m: mean F {fsv.mean():.3f} (precision {np.mean([r[2] for r in rows]):.3f}, "
          f"recall {np.mean([r[3] for r in rows]):.3f}); F >= 0.7 on {(fsv >= 0.7).sum()}, >= 0.9 on {(fsv >= 0.9).sum()}")
    if a.sheet:
        from PIL import Image
        tiles.sort(key=lambda t: t[0])
        pick = [tiles[int(i)] for i in np.linspace(0, len(tiles) - 1, 12)]
        Image.fromarray(np.vstack([t[1] for t in pick])).resize((576 * 3 // 2, 12 * 384 // 2)).save(a.sheet, quality=85)
    return rows


if __name__ == "__main__":
    main()

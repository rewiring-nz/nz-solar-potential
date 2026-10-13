"""Read a roof's shape from its photo: slope and facing per pixel, then faces.

For buildings with no LiDAR -- towns no survey reached (Glenorchy, Kinloch,
Cromwell) and buildings newer than their survey -- the photo is the only
evidence of the roof. The model here was trained on LiDAR roofs paired with
their photos (tools/photo_shape_dataset.py, tools/train_photo_shape.py): it
learned what a roof of each look actually is from tens of thousands of
measured examples, instead of assuming every house is a 20-degree hip.

INPUT. A north-up crop centred on the footprint (photo RGB, footprint mask,
metres per pixel), exactly as the dataset exporter cuts it.

OUTPUT, per pixel: probability the roof is flat there, the downslope
direction as a unit vector, and the slope in degrees -- bounded to
[0, MAX_SLOPE_DEG] by construction (a sigmoid times the cap), because 99.5%
of measured roof area is at or under 45 degrees and the model must never
answer with a wall.

faces_from_maps() turns the maps into roof faces: pixels grouped by facing
(flat, or one of 8 compass sectors), tidied, and each group given one slope
and facing -- the shape src/synthesize_dsm.py builds a surface from.
"""

import numpy as np

MAX_SLOPE_DEG = 45.0
FLAT_DEG = 5.0
SIDE_PX = 96
IN_CH = 5            # R, G, B, footprint mask, metres per pixel
OUT_CH = 4           # flat logit, dir east, dir north, slope logit
MIN_FACE_M2 = 4.0


def build_model(pretrained=True, out_ch=OUT_CH):
    import torch
    import torch.nn as nn
    from torchvision.models import ResNet18_Weights, resnet18

    def block(i, o):
        return nn.Sequential(
            nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True),
            nn.Conv2d(o, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(inplace=True))

    class PhotoShapeNet(nn.Module):
        """U-Net on an ImageNet ResNet18 encoder, widened to 5 input channels."""

        def __init__(self):
            super().__init__()
            r = resnet18(weights=ResNet18_Weights.IMAGENET1K_V1 if pretrained else None)
            conv = nn.Conv2d(IN_CH, 64, 7, 2, 3, bias=False)
            with torch.no_grad():
                conv.weight.zero_()
                conv.weight[:, :3] = r.conv1.weight
            self.stem = nn.Sequential(conv, r.bn1, r.relu)
            self.pool = r.maxpool
            self.l1, self.l2, self.l3 = r.layer1, r.layer2, r.layer3
            self.u3, self.c3 = nn.ConvTranspose2d(256, 128, 2, 2), block(256, 128)
            self.u2, self.c2 = nn.ConvTranspose2d(128, 64, 2, 2), block(128, 64)
            self.u1, self.c1 = nn.ConvTranspose2d(64, 64, 2, 2), block(128, 64)
            self.u0, self.c0 = nn.ConvTranspose2d(64, 32, 2, 2), block(32, 32)
            self.out = nn.Conv2d(32, out_ch, 1)

        def forward(self, x):
            s = self.stem(x)
            a = self.l1(self.pool(s))
            b = self.l2(a)
            c = self.l3(b)
            x = self.c3(torch.cat([self.u3(c), b], 1))
            x = self.c2(torch.cat([self.u2(x), a], 1))
            x = self.c1(torch.cat([self.u1(x), s], 1))
            return self.out(self.c0(self.u0(x)))

    return PhotoShapeNet()


MEAN = np.array([0.485, 0.456, 0.406], np.float32)[:, None, None]
STD = np.array([0.229, 0.224, 0.225], np.float32)[:, None, None]


def make_input(photo_u8, mask_u8, m_per_px):
    """[N,5,H,W] float32 from uint8 photos [N,3,H,W], masks [N,H,W], m_per_px [N]."""
    x = np.empty((len(photo_u8), IN_CH, SIDE_PX, SIDE_PX), np.float32)
    x[:, :3] = (photo_u8.astype(np.float32) / 255.0 - MEAN) / STD
    x[:, 3] = mask_u8.astype(np.float32) * 2 - 1
    x[:, 4] = (np.asarray(m_per_px, np.float32)[:, None, None] - 0.3) / 0.3
    return x


def decode(out):
    """Network output -> (flat prob, dir east, dir north, slope deg), torch tensors."""
    import torch
    flat = torch.sigmoid(out[:, 0])
    d = out[:, 1:3]
    d = d / (d.norm(dim=1, keepdim=True) + 1e-6)
    slope = torch.sigmoid(out[:, 3]) * MAX_SLOPE_DEG
    return flat, d[:, 0], d[:, 1], slope


def load(path, device="cpu", out_ch=OUT_CH):
    import torch
    m = build_model(pretrained=False, out_ch=out_ch)
    m.load_state_dict(torch.load(path, map_location=device)["state"])
    return m.eval().to(device)


def predict(model, photo_u8, mask_u8, m_per_px, device="cpu", tta=True):
    """Per-pixel maps for a batch, averaged over the 8 flips/rotations if tta.

    Returns numpy arrays flat[N,H,W], dirx[N,H,W], diry[N,H,W], slope[N,H,W].
    """
    import torch
    x = torch.from_numpy(make_input(photo_u8, mask_u8, m_per_px)).to(device)
    acc = None
    ks = range(8) if tta else [0]
    with torch.no_grad():
        for k in ks:
            xi = dihedral_x(x, k)
            f, dx, dy, s = decode(model(xi))
            f, dx, dy, s = undo_dihedral(f, dx, dy, s, k)
            parts = torch.stack([f, dx, dy, s])
            acc = parts if acc is None else acc + parts
    acc = acc / len(ks)
    f, dx, dy, s = acc
    n = torch.sqrt(dx ** 2 + dy ** 2) + 1e-6
    return f.cpu().numpy(), (dx / n).cpu().numpy(), (dy / n).cpu().numpy(), s.cpu().numpy()


# The 8 symmetries. Image rows run north to south, columns west to east;
# directions are (east, north). k&1 rotates 90 degrees anticlockwise (k&2 a
# second time), k&4 mirrors east-west.

def dihedral_x(x, k):
    import torch
    if k & 4:
        x = x.flip(-1)
    r = (k & 1) + (k & 2)
    return torch.rot90(x, r, dims=(-2, -1)) if r else x


def dihedral_dir(dx, dy, k):
    """The same symmetry applied to direction vectors."""
    if k & 4:
        dx = -dx
    for _ in range((k & 1) + (k & 2)):
        dx, dy = -dy, dx
    return dx, dy


def dihedral_maps(maps, dx, dy, k):
    """Apply symmetry k to spatial maps [...,H,W] and to the vector field."""
    import torch
    r = (k & 1) + (k & 2)

    def sp(t):
        if k & 4:
            t = t.flip(-1)
        return torch.rot90(t, r, dims=(-2, -1)) if r else t
    maps = [sp(m) for m in maps]
    dx, dy = sp(dx), sp(dy)
    dx, dy = dihedral_dir(dx, dy, k)
    return maps, dx, dy


def undo_dihedral(f, dx, dy, s, k):
    import torch
    r = (k & 1) + (k & 2)

    def sp(t):
        t = torch.rot90(t, -r, dims=(-2, -1)) if r else t
        return t.flip(-1) if k & 4 else t
    f, s, dx, dy = sp(f), sp(s), sp(dx), sp(dy)
    for _ in range(r):
        dx, dy = dy, -dx
    if k & 4:
        dx = -dx
    return f, dx, dy, s


def sector_labels(flat, dirx, diry, slope, mask):
    """0 = outside, 1 = flat, 2..9 = facing N, NE, E, SE, S, SW, W, NW."""
    lab = np.zeros(mask.shape, np.int16)
    inside = mask > 0
    is_flat = (flat > 0.5) | (slope < FLAT_DEG)
    az = (np.degrees(np.arctan2(dirx, diry)) + 360.0) % 360.0
    sec = (np.round(az / 45.0).astype(int) % 8) + 2
    lab[inside & is_flat] = 1
    lab[inside & ~is_flat] = sec[inside & ~is_flat]
    return lab


def _majority(lab, mask, passes=2):
    from scipy.ndimage import generic_filter
    def mode(v):
        v = v[v > 0].astype(int)
        return np.bincount(v).argmax() if len(v) else 0
    for _ in range(passes):
        lab = generic_filter(lab, mode, size=3, mode="constant", cval=0).astype(np.int16)
        lab[mask == 0] = 0
    return lab


def faces_from_maps(flat, dirx, diry, slope, mask, transform, m_per_px):
    """Roof faces from one building's maps.

    Returns [(shapely polygon in the transform's CRS, slope_deg, aspect_deg)],
    aspect being the downslope compass bearing (None for a flat face).
    """
    from rasterio.features import shapes
    from scipy.ndimage import label as cc_label
    from shapely.geometry import shape
    lab = _majority(sector_labels(flat, dirx, diry, slope, mask), mask)
    px_m2 = m_per_px ** 2
    min_px = max(1, int(MIN_FACE_M2 / px_m2))
    # Small islands take the label of the face around them.
    for _ in range(3):
        changed = False
        for v in range(1, 10):
            comp, n = cc_label(lab == v)
            for i in range(1, n + 1):
                sel = comp == i
                if sel.sum() >= min_px:
                    continue
                from scipy.ndimage import binary_dilation
                ring = binary_dilation(sel) & ~sel & (lab > 0)
                if ring.any():
                    lab[sel] = np.bincount(lab[ring]).argmax()
                    changed = True
        if not changed:
            break
    faces = []
    for v in range(1, 10):
        comp, n = cc_label(lab == v)
        for i in range(1, n + 1):
            sel = comp == i
            if sel.sum() < min_px:
                continue
            s = float(np.clip(np.median(slope[sel]), 0.0, MAX_SLOPE_DEG))
            if v == 1 or s < FLAT_DEG:
                s_out, asp = (min(s, FLAT_DEG), None)
            else:
                asp = float((np.degrees(np.arctan2(dirx[sel].mean(), diry[sel].mean())) + 360) % 360)
                s_out = s
            geoms = [shape(g) for g, val in shapes(sel.astype(np.uint8), mask=sel, transform=transform) if val == 1]
            if not geoms:
                continue
            poly = max(geoms, key=lambda g: g.area).buffer(0)
            faces.append((poly, round(s_out, 1), None if asp is None else round(asp, 1)))
    return faces

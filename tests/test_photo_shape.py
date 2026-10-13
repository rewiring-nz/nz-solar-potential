"""photo_shape: the 8 symmetries keep pixels and compass directions together,
and faces_from_maps reads a plain gable as two faces facing opposite ways."""
import sys
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src import photo_shape as ps  # noqa: E402


def test_rotation_moves_north_half_west():
    # A field pointing north over the northern (top) half of the image.
    m = torch.zeros(1, 8, 8); m[0, :4, :] = 1
    dx, dy = torch.zeros(1, 8, 8), m.clone()
    (mr,), rx, ry = ps.dihedral_maps([m], dx, dy, 1)      # one turn anticlockwise
    assert mr[0, :, :4].sum() == 32 and mr[0, :, 4:].sum() == 0   # now the west half
    sel = mr[0] > 0
    assert torch.allclose(rx[0][sel], torch.tensor(-1.0)) and torch.allclose(ry[0][sel], torch.tensor(0.0))
    (mf,), fx, fy = ps.dihedral_maps([m], torch.ones(1, 8, 8), dy, 4)   # mirror east-west
    assert torch.allclose(fx, torch.tensor(-1.0))


def test_undo_inverts_every_symmetry():
    g = torch.Generator().manual_seed(0)
    f, s = torch.rand(2, 6, 6, generator=g), torch.rand(2, 6, 6, generator=g)
    dx, dy = torch.randn(2, 6, 6, generator=g), torch.randn(2, 6, 6, generator=g)
    for k in range(8):
        (f2, s2), dx2, dy2 = ps.dihedral_maps([f, s], dx, dy, k)
        f3, dx3, dy3, s3 = ps.undo_dihedral(f2, dx2, dy2, s2, k)
        for a, b in ((f, f3), (s, s3), (dx, dx3), (dy, dy3)):
            assert torch.allclose(a, b), k
        # the input-only transform agrees with the map transform
        assert torch.allclose(ps.dihedral_x(f.unsqueeze(1), k).squeeze(1), f2)


def test_gable_reads_as_two_faces():
    from rasterio.transform import from_origin
    n = ps.SIDE_PX
    mask = np.zeros((n, n), np.uint8); mask[30:66, 20:76] = 1
    dirx = np.zeros((n, n)); diry = np.where(np.arange(n)[:, None] < 48, 1.0, -1.0) * np.ones((1, n))
    slope = np.full((n, n), 22.0); flat = np.zeros((n, n))
    faces = ps.faces_from_maps(flat, dirx, diry, slope, mask, from_origin(0, n * 0.3, 0.3, 0.3), 0.3)
    assert len(faces) == 2, faces
    asp = sorted(a for _, _, a in faces)
    assert abs(asp[0] - 0) < 1 and abs(asp[1] - 180) < 1
    assert all(abs(s - 22) < 0.5 for _, s, _ in faces)
    assert abs(sum(p.area for p, _, _ in faces) - mask.sum() * 0.09) < 1


if __name__ == "__main__":
    test_rotation_moves_north_half_west()
    test_undo_inverts_every_symmetry()
    test_gable_reads_as_two_faces()
    print("photo_shape: ok")

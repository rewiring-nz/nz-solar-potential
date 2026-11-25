"""The reader's maps for every drawn roof, each read by the fold model that
never saw it, read twice as the build reads (src/line_reader.py). Written to
<out>/<id>.npz (maps uint8 C H W, slide px) for the conversion experiments.

    python tools/experiments/heldout_maps.py data/line_set/runs/D_fold{}.pt data/line_set/heldout_D
"""
import sys, os, glob, random, numpy as np
sys.path.insert(0, '.'); sys.path.insert(0, 'tools')
import src.line_reader as LR
pattern, out = sys.argv[1], sys.argv[2]
os.makedirs(out, exist_ok=True)
SET = os.environ.get('SOLAR_LINES_SET', 'data/line_set/typed_set2')
FOLDS = os.environ.get('SOLAR_LINES_FOLDS')              # JSON {roof id: fold}; else the shuffled split
if FOLDS:
    import json
    ff = json.load(open(FOLDS)); groups = [[f'{SET}/hand/{k}.npz' for k, v in sorted(ff.items()) if v == i] for i in range(5)]
else:
    fs = sorted(glob.glob('data/line_set/typed_set/hand/*.npz')); random.Random(11).shuffle(fs); groups = [fs[k::5] for k in range(5)]
for k in range(5):
    LR._NET.clear(); LR.MODEL = pattern.format(k)
    for f in groups[k]:
        name = os.path.basename(f)
        d = np.load(f'{SET}/hand/{name}')
        p, (dx, dy) = LR.read_twice(d['image'], float(d['meta'][0]))
        np.savez_compressed(f'{out}/{name}', maps=(p * 255).astype(np.uint8), slide=np.array([dx, dy]), fold=k)
    print('fold', k, 'done', flush=True)

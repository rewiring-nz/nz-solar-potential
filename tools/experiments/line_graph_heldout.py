"""Held-out drawn roofs: raw line map vs the joins-and-lines drawing. usage: model fold out.jpg"""
import sys, glob, random, numpy as np, torch, torch.nn.functional as F
sys.path.insert(0,'.'); sys.path.insert(0,'tools')
import train_roof_lines as T
from src.line_graph import faces_from_maps
from scipy.ndimage import distance_transform_edt
from skimage.morphology import skeletonize
from PIL import Image, ImageDraw
model, fold, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
net=T.load_net(model)
fs=sorted(glob.glob('data/line_set/typed_set/hand/*.npz')); random.Random(11).shuffle(fs); fs=fs[fold::5]
def score(pb,tb,tol):
    if tb.sum()<10 and pb.sum()<10: return 1.0
    if tb.sum()<10 or pb.sum()<10: return 0.0
    rc=(distance_transform_edt(~pb)[tb]<=tol).mean(); pr=(distance_transform_edt(~tb)[pb]<=tol).mean()
    return 2*pr*rc/(pr+rc) if pr+rc else 0.0
rows=[]; tiles=[]
COL={'ridge':(255,0,0),'valley':(0,120,255),'cliff':(255,160,0)}
for f in fs:
    d=np.load(f.replace('typed_set/','typed_set2/')); x=torch.from_numpy(d['image'].astype(np.float32)/255).permute(2,0,1)[None]
    with torch.no_grad():
        p=torch.sigmoid(net(F.interpolate(x,size=(T.SIZE,T.SIZE),mode='bilinear',align_corners=False)))
        p=F.interpolate(p,size=x.shape[-2:],mode='bilinear',align_corners=False)[0].numpy()
    w=d['weight']; tb=skeletonize(d['target'][...,0]&w); tol=0.75/float(d['meta'][0])
    raw=score(skeletonize((p[0]>0.5)&w),tb,tol)
    faces,lines,nodes,outline=faces_from_maps(p,T.NAMES)
    im=Image.new('L',w.shape,0); dr=ImageDraw.Draw(im)
    for a,b,_ in lines: dr.line([a,b],fill=255,width=1)
    g=score((np.array(im)>0)&w,tb,tol)
    # outline: intersection over union of the roof
    iou=((p[7]>0.5)&w).sum()/max(((p[7]>0.5)|w).sum(),1)
    rows.append((raw,g,iou,float(d['meta'][0])*304))
    t=d['target']; a=d['image'][...,:3].copy()
    for ch,c in ((2,COL['ridge']),(3,COL['valley']),(4,COL['cliff']),(5,(255,255,255)),(6,(0,255,0))): a[t[...,ch]]=c
    im=Image.fromarray(d['image'][...,:3].copy()); dr=ImageDraw.Draw(im)
    if outline is not None: dr.line(list(outline.exterior.coords),fill=(255,255,255),width=2)
    for u,v,k in lines: dr.line([u,v],fill=COL[k],width=2)
    for u,v in nodes: dr.ellipse([u-2,v-2,u+2,v+2],fill=(0,255,0))
    tiles.append((g,np.hstack([a,np.array(im)])))
r=np.array(rows)
print('%d roofs: raw line map F %.3f (>=0.7: %d, >=0.9: %d); joins-and-lines F %.3f (>=0.7: %d, >=0.9: %d); roof IoU %.3f'%(len(r),r[:,0].mean(),(r[:,0]>=.7).sum(),(r[:,0]>=.9).sum(),r[:,1].mean(),(r[:,1]>=.7).sum(),(r[:,1]>=.9).sum(),r[:,2].mean()))
h=r[:,3]<22
print('house-sized (%d): raw %.3f graph %.3f'%(h.sum(),r[h,0].mean(),r[h,1].mean()))
tiles.sort(key=lambda t:t[0]); pick=[tiles[int(i)][1] for i in np.linspace(0,len(tiles)-1,12)]
Image.fromarray(np.vstack([np.hstack(pick[i:i+3]) for i in range(0,12,3)])).save(out,quality=88)

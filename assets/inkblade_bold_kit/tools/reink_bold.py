"""Deterministic paint-over of R4 UV atlases; preserves GLB geometry, materials and sockets."""
import json,struct,io,random,hashlib,math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter
import numpy as np, cv2

ROOT=Path(__file__).resolve().parent.parent

def readglb(p):
 b=p.read_bytes(); n=struct.unpack_from('<I',b,12)[0]; doc=json.loads(b[20:20+n]); off=20+n; length=struct.unpack_from('<I',b,off)[0]; return doc,bytearray(b[off+8:off+8+length])
def writeglb(p,g,b):
 g['buffers'][0]['byteLength']=len(b);s=json.dumps(g,separators=(',',':')).encode();s+=b' '*((-len(s))%4);b+=b'\0'*((-len(b))%4);p.write_bytes(struct.pack('<III',0x46546c67,2,28+len(s)+len(b))+struct.pack('<II',len(s),0x4e4f534a)+s+struct.pack('<II',len(b),0x004e4942)+b)
def accessor(g,b,i):
 a=g['accessors'][i];v=g['bufferViews'][a['bufferView']];num={'VEC2':2,'VEC3':3,'SCALAR':1}[a['type']];dtype={5126:np.float32,5123:np.uint16,5125:np.uint32}[a['componentType']];size=np.dtype(dtype).itemsize*num
 start=v.get('byteOffset',0)+a.get('byteOffset',0);stride=v.get('byteStride',size)
 return np.ndarray((a['count'],num),dtype=dtype,buffer=b,offset=start,strides=(stride,np.dtype(dtype).itemsize)).copy()

def make_overlay(path):
 g,b=readglb(path);iv=g['bufferViews'][g['images'][0]['bufferView']];im=Image.open(io.BytesIO(b[iv.get('byteOffset',0):iv.get('byteOffset',0)+iv['byteLength']]));w,h=im.size
 mask=np.zeros((h,w),np.uint8)
 for mesh in g['meshes']:
  for p in mesh['primitives']:
   if 'TEXCOORD_0' not in p['attributes']:continue
   uv=accessor(g,b,p['attributes']['TEXCOORD_0']);idx=accessor(g,b,p['indices']).ravel() if 'indices' in p else np.arange(len(uv));xy=np.rint(uv*np.array([w,h])).astype(np.int32)
   for tri in idx.reshape(-1,3):cv2.fillConvexPoly(mask,xy[tri],255)
 contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
 shade=Image.new('RGBA',(w,h));ink=Image.new('RGBA',(w,h));sd=ImageDraw.Draw(shade);draw=ImageDraw.Draw(ink)
 rng=random.Random(int(hashlib.sha256(('bold5'+path.stem).encode()).hexdigest()[:12],16))
 for contour in contours:
  area=cv2.contourArea(contour);x,y,ww,hh=cv2.boundingRect(contour)
  if area<85 or min(ww,hh)<6:continue
  poly=cv2.approxPolyDP(contour,1.6,True).reshape(-1,2).astype(float)
  if len(poly)<3:continue
  centroid=poly.mean(axis=0);short=min(ww,hh)
  # Two bands of flat, translucent shadow form angular comic-book value planes.
  if area>1100:
   candidates=sorted(range(len(poly)),key=lambda i:np.linalg.norm(poly[(i+1)%len(poly)]-poly[i]),reverse=True)
   for ii in candidates[:1 if area<7000 else 2]:
    p=poly[ii];q=poly[(ii+1)%len(poly)];center=(p+q)/2;t=centroid-center;t=t/(np.linalg.norm(t)+1e-9)
    inset=min(20,max(5,short*.13));shallow=inset*.25
    sd.polygon([tuple(p+t*shallow),tuple(q+t*shallow),tuple(q+t*inset),tuple(p+t*inset)],fill=(24,29,34,rng.randint(28,52)))
  # Weighted dark perimeter. This retraces UV island boundaries only: no triangle diagonals.
  perimeter=[tuple(v) for v in poly]+[tuple(poly[0])]
  weight=3 if short<22 else 5 if short<65 else 7
  draw.line(perimeter,fill=(13,15,20,230),width=weight,joint='curve')
  # Broken second pen-strokes follow edges, with a little variation in placement.
  for i,p in enumerate(poly):
   q=poly[(i+1)%len(poly)];length=float(np.linalg.norm(q-p))
   if length<25 or rng.random()>.82:continue
   inward=centroid-(p+q)*.5;inward/=np.linalg.norm(inward)+1e-9
   a=p+(q-p)*rng.uniform(.06,.2)+inward*rng.uniform(5,9)
   z=p+(q-p)*rng.uniform(.68,.94)+inward*rng.uniform(5,9)
   mid=(a+z)*.5+inward*rng.uniform(-1.2,1.2)
   draw.line([tuple(a),tuple(mid),tuple(z)],fill=(17,17,21,rng.randint(150,220)),width=rng.choice([2,2,3]))
  # Corner shadows are layered beneath clusters of converging, uneven hatch strokes.
  if area>550:
   eligible=[]
   for i,corner in enumerate(poly):
    la=np.linalg.norm(poly[i-1]-corner);lb=np.linalg.norm(poly[(i+1)%len(poly)]-corner)
    if min(la,lb)>=12:eligible.append(i)
   rng.shuffle(eligible);n=min(len(eligible), 2 if area<3500 else 3 if area<14000 else 5)
   for i in eligible[:n]:
    corner=poly[i];va=poly[i-1]-corner;vb=poly[(i+1)%len(poly)]-corner;la=np.linalg.norm(va);lb=np.linalg.norm(vb);va/=la;vb/=lb
    span=min(105,max(16,math.sqrt(area)*.42),la*.5,lb*.5)
    sd.polygon([tuple(corner),tuple(corner+va*span*.9),tuple(corner+vb*span*.9)],fill=(22,25,29,rng.randint(35,66)))
    steps=max(4,min(13,int(span/6)))
    for j in range(1,steps+1):
     f=(j+.4)/float(steps+2);jitter=rng.uniform(-1.0,1.0);u=span*f
     start=corner+va*u+vb*2;end=corner+vb*(u*rng.uniform(.75,1.05))+va*2;mid=(start+end)*.5+np.array([jitter,-jitter])
     draw.line([tuple(start),tuple(mid),tuple(end)],fill=(15,16,20,rng.randint(180,240)),width=rng.choice([2,2,3]))
    # A small fractured accent helps the hatching read as hand-inked wear.
    if span>30 and rng.random()<.55:
     p=corner+va*span*.66+vb*span*.1;q=p+vb*min(14,span*.22)
     draw.line([tuple(p),tuple(q)],fill=(17,17,22,185),width=2)
  # Grouped directional scratches, including offset highlight nicks.
  if area>2500:
   for _ in range(min(22,int(area/5000)+4)):
    px=rng.randint(x,x+ww-1);py=rng.randint(y,y+hh-1)
    if px<0 or py<0 or px>=w or py>=h or not mask[py,px]:continue
    angle=rng.uniform(-.35,.35) if ww>=hh else rng.uniform(1.18,1.94)
    length=rng.uniform(5,min(45,max(7,max(ww,hh)*.10)));dx=math.cos(angle)*length;dy=math.sin(angle)*length
    draw.line([(px,py),(px+dx*.55,py+dy*.55),(px+dx,py+dy)],fill=(20,19,23,rng.randint(125,205)),width=rng.choice([1,2,2]))
    if rng.random()<.35:draw.line([(px+1,py+3),(px+dx*.6,py+dy*.6+3)],fill=(255,240,210,130),width=1)
 clip=Image.fromarray(cv2.erode(mask,np.ones((3,3),np.uint8),iterations=1))
 for layer in [shade,ink]:
  alpha=np.minimum(np.asarray(layer.getchannel('A')),np.asarray(clip));layer.putalpha(Image.fromarray(alpha.astype('uint8')))
 return Image.alpha_composite(shade,ink)

def apply(p,overlays):
 g,b=readglb(p)
 for image in g.get('images',[]):
  name=image.get('name','').replace('_basecolor','')
  if name not in overlays:continue
  view=g['bufferViews'][image['bufferView']]; off=view.get('byteOffset',0);im=Image.open(io.BytesIO(b[off:off+view['byteLength']])).convert('RGBA');out=Image.alpha_composite(im,overlays[name]).convert('RGB');bio=io.BytesIO();out.save(bio,'PNG',optimize=True);raw=bio.getvalue();b.extend(b'\0'*((-len(b))%4));image['bufferView']=len(g['bufferViews']);g['bufferViews'].append({'buffer':0,'byteOffset':len(b),'byteLength':len(raw)});b.extend(raw)
 writeglb(p,g,b)

def main():
 parts=ROOT/'models/parts';overlays={p.stem:make_overlay(p) for p in sorted(parts.glob('*.glb'))}
 for id,o in overlays.items():
  p=ROOT/'textures'/f'{id}_basecolor.png';img=Image.open(p).convert('RGBA'); Image.alpha_composite(img,o).convert('RGB').save(p,optimize=True)
 for p in sorted((ROOT/'models').glob('*/*.glb')):apply(p,overlays);print(p.name,flush=True)
if __name__=='__main__':main()

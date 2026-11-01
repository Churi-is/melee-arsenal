"""Copy a GLB and recolor only ELEMENT_ACCENT material textures. Requires Pillow.
Usage: python tint_accent.py input.glb output.glb '#40baff'
Does not enforce a blade's element policy; consult socket_manifest.json.
"""
import sys, json, struct, io, copy
from pathlib import Path
from PIL import Image,ImageOps,ImageColor

def run(inp,out,color):
 data=Path(inp).read_bytes();n=struct.unpack_from('<I',data,12)[0];g=json.loads(data[20:20+n]);off=20+n;size=struct.unpack_from('<I',data,off)[0];b=bytearray(data[off+8:off+8+size]);rgb=ImageColor.getrgb(color);changed=0
 for m in g.get('materials',[]):
  if m.get('extras',{}).get('channel')!='ELEMENT_ACCENT':continue
  ref=m['pbrMetallicRoughness'].get('baseColorTexture')
  if not ref:continue
  tex=copy.deepcopy(g['textures'][ref['index']]);im=copy.deepcopy(g['images'][tex['source']]);v=g['bufferViews'][im['bufferView']];p=v.get('byteOffset',0)
  image=Image.open(io.BytesIO(b[p:p+v['byteLength']])).convert('RGB')
  # Maximum-channel brightness keeps black outlines and tolerates already-tinted maps.
  channels=image.split(); from PIL import ImageChops
  lum=ImageChops.lighter(ImageChops.lighter(channels[0],channels[1]),channels[2]);colored=Image.merge('RGB',tuple(lum.point(lambda v,c=c:round(v*c/255)) for c in rgb));tmp=io.BytesIO();colored.save(tmp,format='PNG');blob=tmp.getvalue();b.extend(b'\0'*((-len(b))%4));im['bufferView']=len(g['bufferViews']);g['bufferViews'].append({'buffer':0,'byteOffset':len(b),'byteLength':len(blob)});b.extend(blob)
  tex['source']=len(g['images']);g['images'].append(im);ref['index']=len(g['textures']);g['textures'].append(tex);m['emissiveFactor']=[v/255*.12 for v in rgb];changed+=1
 if not changed:raise SystemExit('No ELEMENT_ACCENT material found; output not written.')
 g['buffers'][0]['byteLength']=len(b);s=json.dumps(g,separators=(',',':')).encode();s+=b' '*((-len(s))%4);b+=b'\0'*((-len(b))%4);Path(out).write_bytes(struct.pack('<III',0x46546c67,2,28+len(s)+len(b))+struct.pack('<II',len(s),0x4e4f534a)+s+struct.pack('<II',len(b),0x004e4942)+b);print('Updated',changed,'accent material(s).')
if __name__=='__main__':
 if len(sys.argv)!=4:raise SystemExit(__doc__)
 run(*sys.argv[1:])

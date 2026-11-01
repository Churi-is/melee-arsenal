"""Render included GLBs in Blender 4+/5+ (run with Blender Python)."""
import bpy, math, sys
from pathlib import Path
from mathutils import Vector
root=Path(__file__).resolve().parent.parent
files=sorted((root/'models').glob('*/*.glb'))
if '--' in sys.argv:
 args=sys.argv[sys.argv.index('--')+1:]
elif Path(sys.argv[0]).name.startswith('blender'):
 args=[]
else:
 args=sys.argv[1:]
if args: files=[p for p in files if args[0] in p.stem]
for i,p in enumerate(files):
 bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
 bpy.ops.import_scene.gltf(filepath=str(p))
 objs=[o for o in bpy.context.scene.objects if o.type=='MESH']
 bpy.context.view_layer.update();points=[o.matrix_world @ Vector(c) for o in objs for c in o.bound_box]
 lo=Vector([min(v[k] for v in points) for k in range(3)]);hi=Vector([max(v[k] for v in points) for k in range(3)]);center=(lo+hi)/2
 extent=max(hi.x-lo.x,hi.z-lo.z,hi.y-lo.y,.1)
 scene=bpy.context.scene; scene.render.engine='CYCLES';scene.cycles.samples=12;scene.cycles.use_denoising=True;scene.render.resolution_x=720;scene.render.resolution_y=720;scene.render.resolution_percentage=100;scene.render.image_settings.file_format='PNG';scene.render.film_transparent=False
 scene.world.color=(0.22,0.22,0.22);scene.world.use_nodes=True;scene.world.node_tree.nodes.get('Background').inputs['Color'].default_value=(0.33,0.36,0.39,1);scene.world.node_tree.nodes.get('Background').inputs['Strength'].default_value=.8
 scene.view_settings.view_transform='Standard';scene.view_settings.look='Medium High Contrast'
 def area(name,pos,energy,size):
  data=bpy.data.lights.new(name,'AREA');data.energy=energy*extent*extent;data.shape='DISK';data.size=size*extent;obj=bpy.data.objects.new(name,data);scene.collection.objects.link(obj);obj.location=center+Vector(pos)*extent;obj.rotation_euler=(center-obj.location).to_track_quat('-Z','Y').to_euler()
 area('Large softbox',(-2,-4,5),450,4);area('Fill',(4,-3,1),200,3);area('Rim',(0,3,3),250,3)
 data=bpy.data.cameras.new('Camera');camera=bpy.data.objects.new('Camera',data);scene.collection.objects.link(camera);camera.location=center+Vector((1.5,-12,1.1))*extent;camera.rotation_euler=(center-camera.location).to_track_quat('-Z','Y').to_euler();data.type='ORTHO';data.ortho_scale=extent*1.25;scene.camera=camera
 folder=root/'renders'/p.parent.name;folder.mkdir(parents=True,exist_ok=True);scene.render.filepath=str(folder/(p.stem+'.png'))
 print('RENDER',p.stem,flush=True);bpy.ops.render.render(write_still=True)

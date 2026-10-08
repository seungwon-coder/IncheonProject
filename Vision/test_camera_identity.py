from types import SimpleNamespace
import sys
import camera_identity as ci
p1=r'\\?\usb#vid_046d&pid_08e5&mi_00#A\global'
p2=r'\\?\usb#vid_046d&pid_082d&mi_00#B\global'
sys.modules['cv2']=SimpleNamespace(CAP_DSHOW=700)
records=[SimpleNamespace(index=3,backend=700,path=p1,name='C920 A'),SimpleNamespace(index=0,backend=700,path=p2,name='C920 B')]
index,backend,info=ci.resolve_camera({'camera':0,'camera_path':p1.upper(),'backend':'DSHOW'},records)
assert index==3 and backend==700 and info.name=='C920 A'
try:ci.resolve_camera({'camera':0,'camera_path':'missing','backend':'DSHOW'},records)
except RuntimeError as e:assert '찾지 못했습니다' in str(e)
else:raise AssertionError('missing path incorrectly fell back to index')
print('PASS: saved path resolves changed index and missing path never opens another camera')

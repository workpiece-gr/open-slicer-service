"""Exercise real Orca orientation and fresh-project reopening in Docker CI."""
import json
import struct
import urllib.error
import urllib.request


def box(x, y, z):
    points = [(0,0,0),(x,0,0),(x,y,0),(0,y,0),(0,0,z),(x,0,z),(x,y,z),(0,y,z)]
    faces = [(0,2,1),(0,3,2),(4,5,6),(4,6,7),(0,1,5),(0,5,4),(1,2,6),(1,6,5),(2,3,7),(2,7,6),(3,0,4),(3,4,7)]
    return b'Workpiece controlled orientation CI'.ljust(80,b'\0') + struct.pack('<I',12) + b''.join(struct.pack('<12fH',0,0,0,*points[a],*points[b],*points[c],0) for a,b,c in faces)


failures = []
for name, dimensions, expected in [
    ('normal',(250,20,20),200), ('low-rod',(290,20,20),200),
    ('tall-rod',(20,20,240),200), ('long-tall-rod',(20,20,350),200),
    ('too-long',(20,20,390),422), ('broad',(280,100,10),422),
    ('thin-tall',(10,10,180),200),
]:
    boundary = 'workpiece-ci-orientation-boundary'
    body = bytearray()
    for key,value in {'material':'pla','quality':'balanced','strength':'functional','quantity':'1','printer':'ratrig_vcore3_300','verify':'true'}.items():
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}.stl"\r\nContent-Type: model/stl\r\n\r\n'.encode())
    body.extend(box(*dimensions))
    body.extend(f'\r\n--{boundary}--\r\n'.encode())
    request = urllib.request.Request('http://127.0.0.1:8080/v1/project', data=body, headers={'Authorization':'Bearer ci-project-token','Content-Type':f'multipart/form-data; boundary={boundary}'})
    try:
        response = urllib.request.urlopen(request,timeout=240)
    except urllib.error.HTTPError as error:
        response = error
    result = json.load(response)
    print(json.dumps({'fixture':name,'status':response.status,'detail':result.get('detail'),'layout':result.get('project',{}).get('layout_repair'),'verification':result.get('verification')}),flush=True)
    if response.status != expected:
        failures.append(f'{name}: expected {expected}, received {response.status}')
    if response.status == 200 and not result['verification']['reopened_in_fresh_orca_process']:
        failures.append(f'{name}: fresh Orca reopen not verified')
assert not failures, '; '.join(failures)

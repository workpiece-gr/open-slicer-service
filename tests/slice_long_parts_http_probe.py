"""Quote long parts through /v1/slice with the real OrcaSlicer in Docker CI.

Before the first-layer line width resolution (app.main.absolute_first_layer_line_width)
OrcaSlicer's CLI arranger read the RatRig profiles' "110%" first-layer width as 110 mm
per skirt loop, shrank the arrange bed by 222 mm per side and refused every part longer
than about 144 mm with exit -50, while /v1/project placed the same parts with its
post-export layout repair. This probe keeps the quote path honest on the real binary:
every shape the routing rule accepts must slice, rods that only fit the square bed
diagonally are arranged with the one rotation retry, and the quoted filament for a long
bar must agree with the delivered project's fresh-process verification.

Environment: SLICER_URL (default http://127.0.0.1:8080), SLICER_TOKEN (default
ci-project-token), SLICER_PROBE_PROJECT (set to 0 to skip the /v1/project comparison).
"""
import json
import math
import os
import struct
import urllib.error
import urllib.request

URL = os.environ.get("SLICER_URL", "http://127.0.0.1:8080").rstrip("/")
TOKEN = os.environ.get("SLICER_TOKEN", "ci-project-token")
COMPARE_PROJECT = os.environ.get("SLICER_PROBE_PROJECT", "1") != "0"


def box(x, y, z):
    points = [(0,0,0),(x,0,0),(x,y,0),(0,y,0),(0,0,z),(x,0,z),(x,y,z),(0,y,z)]
    faces = [(0,2,1),(0,3,2),(4,5,6),(4,6,7),(0,1,5),(0,5,4),(1,2,6),(1,6,5),(2,3,7),(2,7,6),(3,0,4),(3,4,7)]
    triangles = []
    for a,b,c in faces:
        u = [points[b][i]-points[a][i] for i in range(3)]
        v = [points[c][i]-points[a][i] for i in range(3)]
        normal = [u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]]
        length = math.sqrt(sum(n*n for n in normal))
        triangles.append(struct.pack('<12fH',*[n/length for n in normal],*points[a],*points[b],*points[c],0))
    return b'Workpiece long-part quote CI'.ljust(80,b'\0') + struct.pack('<I',12) + b''.join(triangles)


def post(path, name, dimensions, fields):
    boundary = 'workpiece-ci-long-parts-boundary'
    body = bytearray()
    for key,value in fields.items():
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{name}.stl"\r\nContent-Type: model/stl\r\n\r\n'.encode())
    body.extend(box(*dimensions))
    body.extend(f'\r\n--{boundary}--\r\n'.encode())
    request = urllib.request.Request(f'{URL}{path}', data=body, headers={'Authorization':f'Bearer {TOKEN}','Content-Type':f'multipart/form-data; boundary={boundary}'})
    try:
        response = urllib.request.urlopen(request,timeout=300)
    except urllib.error.HTTPError as error:
        response = error
    return response.status, json.load(response)


failures = []
quotes = {}
# (name, dimensions, expected status, expected rotation retry or refusal code)
for name, dimensions, expected, rotated in [
    ('cube',(20,20,20),200,False),
    ('bar',(250,20,20),200,False),
    ('plate',(270,270,5),200,False),
    ('low-rod',(290,20,20),200,True),
    ('tall-pin',(20,20,290),200,True),
    ('long-tall-rod',(20,20,350),200,True),
    # Refused by routing: longer than the controlled diagonal allows.
    ('too-long',(20,20,390),422,'NO_PRINTER_FITS'),
    # Passes routing on its edge, laid flat by Orca, and not rod-like: the project's layout
    # repair refuses it and the quote must not rotate it either (Orca's -50 stays a failure).
    ('broad',(280,100,10),422,'SLICE_FAILED'),
]:
    status, result = post('/v1/slice', name, dimensions, {'material':'pla','quality':'balanced','strength':'functional','printer':'ratrig_vcore3_300','preview':'moves'})
    summary = result.get('summary') or {}
    codes = [item.get('code') for item in result.get('warnings') or []]
    bounds = (result.get('toolpath') or {}).get('bounds')
    print(json.dumps({'fixture':name,'status':status,'detail':result.get('detail'),'summary':{k: summary.get(k) for k in ('print_time_seconds','filament_grams','layer_count','support_grams')},'warnings':codes,'bounds':bounds}),flush=True)
    if status != expected:
        failures.append(f'{name}: expected {expected}, received {status} {result.get("detail")}')
        continue
    if status != 200:
        if result['detail']['code'] != rotated:
            failures.append(f'{name}: expected refusal {rotated}, received {result["detail"]["code"]}')
        continue
    quotes[name] = summary
    if not (summary.get('print_time_seconds') and summary.get('filament_grams') and summary.get('layer_count')):
        failures.append(f'{name}: incomplete summary {summary}')
    if ('arranged_with_rotation' in codes) != rotated:
        failures.append(f'{name}: rotation retry evidence {codes} does not match expected rotated={rotated}')
    if bounds is None:
        failures.append(f'{name}: no toolpath bounds')
    else:
        # Every extrusion, skirt included, must stay on the 300 mm bed.
        if min(bounds['min'][:2]) < 0 or max(bounds['max'][:2]) > 300:
            failures.append(f'{name}: toolpath leaves the bed {bounds}')
        width, depth = bounds['max'][0]-bounds['min'][0], bounds['max'][1]-bounds['min'][1]
        if name == 'bar' and not (245 <= max(width, depth) <= 262 and min(width, depth) <= 32):
            failures.append(f'{name}: expected an axis-aligned 250 mm footprint, got {width:.1f} x {depth:.1f}')
        if rotated and max(width, depth) > 299:
            failures.append(f'{name}: rotated part still spans the full bed {width:.1f} x {depth:.1f}')

# The 100-layer bar quoted here and the same bar's delivered project must report the same
# filament mass (same process, same supports; the project only disables the prime tower
# and adds G92 E0, neither of which extrudes).
if COMPARE_PROJECT and 'bar' in quotes:
    status, project = post('/v1/project', 'bar', (250,20,20), {'material':'pla','quality':'balanced','strength':'functional','quantity':'1','printer':'ratrig_vcore3_300','verify':'true'})
    verification = project.get('verification') or {}
    print(json.dumps({'fixture':'bar-project','status':status,'detail':project.get('detail'),'verification':{k: verification.get(k) for k in ('total_print_time_seconds','total_filament_grams','plate_count')}}),flush=True)
    if status != 200:
        failures.append(f'bar-project: expected 200, received {status} {project.get("detail")}')
    else:
        quoted = float(quotes['bar']['filament_grams'])
        delivered = float(verification['total_filament_grams'])
        if abs(quoted - delivered) > 0.05 * max(quoted, delivered):
            failures.append(f'bar-project: quoted {quoted} g but the project verification reports {delivered} g')

assert not failures, '; '.join(failures)

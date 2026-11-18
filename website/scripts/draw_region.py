"""Maintainer tool for the fictional Sunrise Pass relief map. No geographic inputs."""
import base64
from io import BytesIO
from pathlib import Path

import contourpy
import numpy as np
from PIL import Image

OUT = Path(__file__).resolve().parents[1] / 'assets/map/region.svg'
w, h = 720, 430
y, x = np.mgrid[:h, :w]
# Warp a synthetic valley with two tributary folds and small limestone spurs.
stream = (210 + 40*np.sin(x/109) - 36*np.cos(x/135)
          + 12*np.sin(x/29) + 5*np.sin(x/13))
ridge = y - stream
u = x + 14*np.sin(y/38) + 6*np.sin(x/25+y/17)
v = y + 15*np.sin(x/52) + 9*np.sin(y/29-x/41)
height = (10 + .51*np.abs(ridge) + 22*np.sin(u/45 + v/58)
          + 22*np.cos(u/74 - v/34) + 13*np.cos(u/29 + v/48)
          + 5*np.sin(u/12-v/17) + 2.7*np.cos(u/8+v/11))
dy, dx = np.gradient(height)
shade = np.clip(dx*.43 + dy*.7,-3.4,3.4)*9
woodland = (np.sin(u/47 + v/62) + .75*np.cos(v/39-u/70)
            + .44*np.sin(u/21-v/24) + .16*np.cos(v/6+u/13))
woodland = np.clip((woodland+.75)*1.35, 0, 1) * np.clip((145-height)/32,0,1)
meadow = np.array([218, 218, 171])
forest = np.array([145, 175, 127])
color = meadow + woodland[:,:,None]*(forest-meadow)
scree = np.clip((height-109)/58,0,1)[:,:,None]
color = color*(1-scree)+np.array([220,211,188])*scree
terrain = (color+shade[:,:,None]).clip(0,255).astype('uint8')
b=BytesIO()
Image.fromarray(terrain).save(b,'JPEG',quality=87)
base=base64.b64encode(b.getvalue()).decode()
parts=[f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}"><title>Illustrative topographic map of Sunrise Pass</title>',
       f'<image width="{w}" height="{h}" href="data:image/jpeg;base64,{base}"/>']
contours=contourpy.contour_generator(x=np.arange(0,w,4), y=np.arange(0,h,4), z=height[::4,::4])
parts.append('<g fill="none" stroke="#736e4f">')
for index,level in enumerate(np.arange(-15,210,4)):
    paths=[]
    for line in contours.lines(level):
        if len(line)>3:
            points=' '.join(f'{a:.0f},{b:.0f}' for a,b in line)
            paths.append('M'+points)
    if paths:
        parts.append('<path d="'+' '.join(paths)+'" stroke-width="'+('1.2' if index%5==0 else '.7')+'" opacity=".51"/>')
parts.append('</g>')
water='M'+' '.join(f'{i},{stream[0,i]:.1f}' for i in range(0,w,3))
parts.extend([f'<path d="{water}" stroke="#e6f5ed" stroke-width="8" fill="none"/>',
              f'<path d="{water}" stroke="#70a7bc" stroke-width="4" fill="none"/>'])
# A lake and tributaries connect to the river in the same coordinate system.
parts.extend(['<path d="M526 137Q535 150 553 151T584 164Q597 181 579 180T550 172Q528 164 526 137Z" fill="#9ec2c5" stroke="#73a2ab" stroke-width="1.6"/>',
              '<g fill="none" stroke="#82aeba" stroke-width="1.7"><path d="M279 0Q283 28 303 47T321 99Q317 119 333 149T351 194T369 243"/><path d="M715 320Q681 310 663 280T638 250T621 216T613 166"/><path d="M120 429Q130 382 109 364T129 327T134 296T161 249"/></g>'])
# Small cartographic silhouettes identify additional unlabelled summits.
for px,py in [(158,52),(370,57),(615,112),(136,287),(652,329),(374,340)]:
    parts.append(f'<path d="m{px} {py} 6-10 4 6 3-3 5 7z" fill="#53604a" stroke="#f1edd4" stroke-width="1.2"/>')
trail='M-6 418C45 393 49 351 100 343S166 339 179 297S240 254 268 231S293 183 333 177S349 148 366 141S449 142 459 103S507 125 544 77S607 74 639 40L685 -6'
parts += [f'<path d="{trail}" fill="none" stroke="#ffe9c4" stroke-width="6"/>',
          f'<path d="{trail}" fill="none" stroke="#a74232" stroke-width="3" stroke-dasharray="7 4"/>',
          '<path d="M183 297Q249 323 281 275T371 254T481 239T585 187T721 152" fill="none" stroke="#44775d" stroke-width="3" stroke-dasharray="5 5"/>',
          '<path d="m242 249 22-17m-24 13 22-17" fill="none" stroke="#584638" stroke-width="3"/>']
for px,py,label,icon in [(334,177,'Sunrise Pass','peak'),(466,106,'Juniper Hut','hut'),(248,245,'Stone Bridge','bridge'),(552,203,'Lake Ridge Trail','peak')]:
    path={'peak':'M-8 4 0-9 8 4z M-3-3l3 2 2-3','hut':'M-8-2 0-9l8 7M-6-3v9H6v-9M-1 6V0h3v6','bridge':'M-8 4V-2H8v6M-6 4q6-10 12 0'}[icon]
    parts.append(f'<g transform="translate({px} {py})"><circle r="10" fill="#f8f2df" stroke="#77836c"/><path d="{path}" fill="none" stroke="#2c4438" stroke-width="1.7"/></g>')
    parts.append(f'<text x="{px+13}" y="{py+5}" style="font:italic 13px Georgia,serif;paint-order:stroke;stroke:#f3ecd3;stroke-width:3;fill:#324d3c">{label}</text>')
parts.append('</svg>')
OUT.write_text('\n'.join(parts))

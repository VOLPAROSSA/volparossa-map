"""Render the editable diagrams against the exact picture planes used by CSS."""
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
NETWORK = json.loads((ROOT/'src/network.json').read_text())
SCENES = json.loads((ROOT/'src/scene-anchors.json').read_text())


def mesh(layout):
    scene = SCENES[layout]
    points = scene['anchors']
    view = ' '.join(map(str, scene['viewBox']))
    parts = [f'<svg class="hut-mesh {layout}" viewBox="{view}" role="img" aria-labelledby="mesh-title-{layout}">',
             f'<title id="mesh-title-{layout}">Six mountain huts can exchange public map data along several routes across the valley.</title>']
    for start, end in NETWORK['edges']:
        x1, y1 = points[start]
        x2, y2 = points[end]
        lift = min(38,abs(x2-x1)*.14)
        path = f'M{x1} {y1}Q{(x1+x2)/2:g} {(y1+y2)/2-lift:g} {x2} {y2}'
        parts.append(f'<path class="mesh-bed" d="{path}"/><path data-edge="{start}-{end}" class="gold-link" d="{path}"/>')
    for node, (x,y) in points.items():
        parts.append(f'<g data-hut="{node}"><circle cx="{x}" cy="{y}" r="4" class="mesh-light"/><text x="{x+5}" y="{y+16}" class="hut-id">{node}</text></g>')
        if node in scene['cards']:
            dx,dy = scene['cards'][node]
            w,h = (48,33) if layout=='desktop' else (29,22)
            # A small leader associates a map extract with its hut.
            crops = {'A':'230 118 205 134','C':'395 36 205 134','D':'395 36 205 134',
                     'E':'168 190 205 134','F':'253 142 205 134'}
            parts.append(f'<path class="card-lead" d="M{x} {y}L{x+dx+w/2} {y+dy+h/2}"/>'
                         f'<svg x="{x+dx}" y="{y+dy}" width="{w}" height="{h}" viewBox="0 0 48 33" aria-hidden="true">'
                         '<rect x="1" y="1" width="46" height="31" rx="3" fill="#f4e6c4" stroke="#725836"/>'
                         f'<svg x="3" y="3" width="42" height="27" viewBox="{crops.get(node,"220 140 205 134")}"><image width="720" height="430" href="assets/map/region.svg"/></svg>'
                         '<path d="m20 19 4-7 4 7z" fill="#30442c" stroke="#fff5cf" stroke-width=".8"/></svg>')
    return ''.join(parts)+'</svg>'


def overlay(view, title, elements, css_class):
    return (f'<svg class="overlay {css_class}" viewBox="{view}" role="img"><title>{title}</title>'
            + elements + '</svg>')


def positioning():
    body = '<ellipse class="uncertainty" cx="497" cy="248" rx="145" ry="38"/>'
    for x,y,icon in [(157,58,'satellite'),(815,79,'radio-tower'),(156,276,'radio-tower'),(776,286,'radio-tower')]:
        body += (f'<path class="observation" d="M{x} {y}L497 248"/><circle class="source-disc" cx="{x}" cy="{y}" r="21"/>'
                 f'<use href="#i-{icon}" x="{x-14}" y="{y-14}" width="28" height="28" class="source-icon"/>')
    body += '<rect x="486" y="229" width="22" height="38" rx="4" fill="#222c29" stroke="#fff1bd" stroke-width="3"/><rect x="490" y="235" width="14" height="23" rx="1" fill="#bdd299"/>'
    return overlay('0 0 1000 385','Possible positioning sources and an estimated area of uncertainty',body,'position-lines')


def bridge():
    body = '<g class="bridge-lines"><path d="M159 224Q218 126 391 111Q561 41 786 101Q871 118 938 211M391 111Q503 121 587 193Q699 121 786 101"/></g>'
    return overlay('0 0 1000 370','Illustrative cooperation between alpine workrooms',body,'village-lines')


def traffic_state(report, sample):
    if not report or report['map'] != sample['map'] or report['direction'] != sample['direction']:
        return 'unreported'
    if report['expires'] <= sample['at']:
        return 'expired'
    if report['conflicting'] or report['observations'] < 5:
        return 'unreported'
    return 'fresh'


def road():
    sample = json.loads((ROOT/'src/traffic-example.json').read_text())
    body = ''
    for segment in sample['segments']:
        state = traffic_state(segment['report'], sample)
        path = segment['path']
        body += f'<path class="asphalt-edge" d="{path}"/><path class="road-trace {state}" data-state="{state}" d="{path}"/>'
    body += '<path d="m217 140-11 11 16 1" fill="#fff2c4" stroke="#603e1f" stroke-width="1.5"/>'
    return overlay('0 0 800 400', 'A recent slow-traffic report, an expired report and a section without reliable observations',body,'road-lines')


def saved_screens():
    # Screen apertures measured in the 1000 × 400 photographic master plane.
    body = """<defs>
      <clipPath id="laptop-aperture"><path d="M136 81 437 82 459 257 153 274Z"/></clipPath>
      <clipPath id="phone-aperture"><path d="M673 95Q667 95 666 105L649 307Q649 315 656 316L741 322Q749 323 750 314L769 106Q770 98 763 97Z"/></clipPath>
    </defs>
    <g clip-path="url(#laptop-aperture)">
      <image href="assets/map/region.svg" x="135" y="79" width="331" height="198" preserveAspectRatio="xMidYMid slice"/>
      <path d="M130 76H309L329 270 145 282Z" fill="#fcfaf0" fill-opacity=".95"/>
    </g>
    <g clip-path="url(#phone-aperture)">
      <path d="M643 90H772V325H643Z" fill="#fbf7eb"/>
      <path d="M693 94h54q-1 10-6 10h-43q-6 0-5-10" fill="#1b201f"/>
    </g>"""
    labels=('My places','Sunrise Pass','Lake Ridge Trail','Stone Bridge')
    for transform,size in [('matrix(1,-.036,.085,1,146,108)',14),('matrix(.97,.06,-.088,1,671,127)',9.4)]:
        body += f'<g transform="{transform}" fill="#293a2f" font-family="Inter,Arial,sans-serif" font-size="{size}">'
        for index,label in enumerate(labels):
            y=index*(size*2.1)
            if index:
                body+=f'<use href="#i-star" x="1" y="{y-9}" width="10" height="10" color="#9f722a"/>'
            body+=f'<text x="{15 if index else 0}" y="{y}">{label}</text>'
        body+='</g>'
    body += '<path d="M465 195H654" stroke="#fff1c8" stroke-width="3" stroke-dasharray="6 5"/><rect x="535" y="173" width="43" height="42" rx="6" fill="#fff5dd" stroke="#9c895d"/><use href="#i-lock-keyhole" x="544" y="181" width="26" height="26" color="#2b4634"/>'
    return overlay('0 0 1000 400','Both private devices list Sunrise Pass, Lake Ridge Trail and Stone Bridge',body,'saved-screens')

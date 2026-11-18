"""Maintain art-directed WebP / JPEG exports. Only run when a master changes."""
import argparse
import hashlib
import json
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SCENES = ('hero','huts','road','dps','bridge','terrace','closing')
OUT = ROOT/'assets/map'


def export():
    variants = {}
    for name in SCENES:
        variants[name] = {}
        source = Image.open(ROOT/'masters'/f'{name}.png').convert('RGB')
        for kind in ('desktop','mobile'):
            master = name
            scene = source
            crop = (0,0,*source.size)
            if kind == 'mobile' and name in ('hero','huts','terrace'):
                master += '-mobile'
                scene = Image.open(ROOT/'masters'/f'{master}.png').convert('RGB')
                crop = (0,0,*scene.size)
            elif kind == 'mobile' and name == 'closing':
                # Keep the resting fox, marked stone and compass together.
                crop_width = min(source.width, round(source.height*1.26))
                crop = (source.width-crop_width,0,source.width,source.height)
                scene = source.crop(crop)
            widths = (480,800) if kind == 'mobile' else (800,1440,2048)
            rows = []
            for w in sorted({min(w,scene.width) for w in widths}):
                h = round(w*scene.height/scene.width)
                variant = scene.resize((w,h),Image.Resampling.LANCZOS)
                stem = f'{name}-{kind}-{w}'
                variant.save(OUT/(stem+'.avif'),quality=54,speed=6)
                variant.save(OUT/(stem+'.webp'),quality=83,method=6)
                variant.save(OUT/(stem+'.jpg'),quality=85,optimize=True,progressive=True)
                rows.append(dict(stem=stem,width=w,height=h))
            variants[name][kind] = dict(master=master+'.png',crop=crop,files=rows)
    (ROOT/'src/variants.json').write_text(json.dumps(variants,indent=2)+'\n')


def inventory():
    files = {}
    for base in ('assets','masters'):
        for p in sorted((ROOT/base).rglob('*')):
            if not p.is_file(): continue
            record = dict(bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
            if p.suffix in ('.avif','.jpg','.webp','.png'):
                with Image.open(p) as image: record['dimensions'] = list(image.size)
            files[p.relative_to(ROOT).as_posix()] = record
    (ROOT/'asset-manifest.json').write_text(json.dumps(files,indent=2)+'\n')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory-only',action='store_true')
    if not parser.parse_args().inventory_only: export()
    inventory()

"""Build the static Mountain Passes project site using the pinned central theme."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil

from diagrams import bridge, mesh, positioning, road, saved_screens

ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT/'vendor/volparossa-theme'
SITE = json.loads((ROOT/'src/content.json').read_text())
CANONICAL = 'https://volparossa.org/volparossa-map/'


def render(template, values):
    return re.sub(r'{{(\w+)}}', lambda match: values[match[1]], template)


def symbols():
    defs, icons = [], {}
    for file in sorted((ROOT/'masters/icons').glob('*.svg')):
        text = file.read_text()
        glyph = text[text.index('>',text.index('<svg'))+1:text.index('</svg>')]
        defs.append(f'<symbol id="i-{file.stem}" viewBox="0 0 24 24"><g fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round" stroke-linecap="round">{glyph}</g></symbol>')
        icons[file.stem.replace('-','_')] = f'<svg class="icon" aria-hidden="true" viewBox="0 0 24 24"><use href="#i-{file.stem}"/></svg>'
    icons['symbols'] = '<svg class="symbols" aria-hidden="true" width="0" height="0"><defs>'+''.join(defs)+'</defs></svg>'
    return icons


def picture(name, variants, sizes):
    sources = []
    for kind in ('mobile','desktop'):
        condition = ' media="(max-width: 800px)"' if kind == 'mobile' else ''
        items = variants[name][kind]['files']
        w,h = items[-1]['width'],items[-1]['height']
        for ext,mime in [('avif','image/avif'),('webp','image/webp'),('jpg','image/jpeg')]:
            srcset = ', '.join(f"assets/map/{row['stem']}.{ext} {row['width']}w" for row in items)
            sources.append(f'<source{condition} type="{mime}" srcset="{srcset}" sizes="{sizes}" width="{w}" height="{h}">')
    fallback = variants[name]['desktop']['files'][-1]
    priority = 'loading="eager" fetchpriority="high"' if name == 'hero' else 'loading="lazy" decoding="async"'
    return '<picture class="art">'+''.join(sources)+f'<img src="assets/map/{fallback["stem"]}.jpg" alt="" width="{fallback["width"]}" height="{fallback["height"]}" {priority}></picture>'


def build(output, production):
    manifest = json.loads((THEME/'theme-version.json').read_text())
    if manifest['source_commit'] != SITE['theme_commit']: raise ValueError('Unexpected shared theme commit')
    for name, digest in manifest['files'].items():
        if hashlib.sha256((THEME/name).read_bytes()).hexdigest() != digest: raise ValueError('Theme mismatch: '+name)
    output = output.resolve()
    marker = output/'.map-website-build'
    if output.exists() and any(output.iterdir()):
        if not marker.is_file(): raise ValueError('Choose an empty or previously generated Map output directory')
        shutil.rmtree(output)
    output.mkdir(exist_ok=True,parents=True)
    marker.touch()
    (output/'.nojekyll').touch()
    shutil.copytree(ROOT/'assets',output/'assets')
    for entry in ('site.css','site.js','tokens.css','logo.png','favicon.png'):
        shutil.copy2(THEME/'assets'/entry,output/'assets'/entry)
    for directory in ('fonts','icons'):
        shutil.copytree(THEME/'assets'/directory,output/'assets'/directory)
    shutil.copy2(ROOT/'src/map.css',output/'assets/map.css')
    shutil.copy2(THEME/'favicon.ico',output/'favicon.ico')
    shutil.copy2(THEME/'LICENSE',output/'LICENSE')
    (output/'credits').mkdir()

    spec = importlib.util.spec_from_file_location('nav',THEME/'shared/navigation.py')
    navlib = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(navlib)
    nav = json.loads((THEME/'data/navigation.json').read_text())
    projects = json.loads((THEME/'data/projects.json').read_text())
    links = navlib.site_links(nav, projects, 'https://volparossa.org/', {'github':SITE['repository']})
    github = (THEME/'assets/icons/github.svg').read_text().replace('<svg ', '<svg class="github-icon" aria-hidden="true" ',1)
    values = {**links,**SITE,**symbols(),'root':'','github_icon':github}
    for group,key in [('main','nav_links'),('footer','footer_links')]:
        values[key] = navlib.navigation_links(nav[group],links,active='applications',github_icon=github,footer=group=='footer')
    for part in ('header','footer'):
        values[part] = render((THEME/f'templates/{part}.html').read_text(),values)
    credits_header = render((THEME/'templates/header.html').read_text(), {**values, 'root':'../'})
    (output/'credits/index.html').write_text(render((ROOT/'src/credits.html').read_text(), {'header':credits_header}))
    variants = json.loads((ROOT/'src/variants.json').read_text())
    for name in variants:
        sizes = '100vw' if name in ('hero','closing','huts') else '(max-width:800px) 100vw, 47vw' if name=='road' else '(max-width:800px) 100vw, 68vw'
        values[name+'_picture'] = picture(name,variants,sizes)
    values.update(desktop_mesh=mesh('desktop'),mobile_mesh=mesh('mobile'),
                  dps_overlay=positioning(),bridge_overlay=bridge(),road_overlay=road(),saved_screens=saved_screens(),
                  map_surface='<img class="region" src="assets/map/region.svg" alt="" width="720" height="430">')
    values['places']='<div class="place-list"><strong>My places</strong>'+''.join('<span>'+values['star']+place+'</span>' for place in ('Sunrise Pass','Lake Ridge Trail','Stone Bridge'))+'</div>'
    values['content']=render((ROOT/'src/index.html').read_text(),values)
    robots='index,follow' if production else 'noindex,nofollow'
    values.update(canonical=CANONICAL,body_class='map-page',social_image=CANONICAL+'assets/map/social.jpg',
                  head_extra=f'<meta name="robots" content="{robots}"><link rel="stylesheet" href="assets/map.css">')
    (output/'index.html').write_text(render((THEME/'templates/layout.html').read_text(),values))
    if production:
        (output/'sitemap.xml').write_text(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>{CANONICAL}</loc></url></urlset>\n')
    print('Built', 'production' if production else 'noindex preview', output)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'preview/volparossa-map')
    parser.add_argument('--production',action='store_true')
    args=parser.parse_args()
    build(args.output,args.production)

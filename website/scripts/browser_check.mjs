import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium, firefox, webkit} from 'playwright';
import AxeBuilder from '@axe-core/playwright';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const url = process.env.MAP_QA_URL || 'http://127.0.0.1:8782/volparossa-map/';
const out = path.join(root, process.env.QA_OUTPUT || 'qa/browser');
fs.mkdirSync(out, {recursive:true});
const records=[];
const assert=(value,label)=>{if(!value) throw Error(label);};
for (const engine of (process.env.QA_ENGINES || 'chromium,firefox').split(',')) {
  const type = {chromium, firefox, webkit}[engine];
  const settings = engine==='chromium' ? {executablePath:process.env.CHROME_PATH,args:['--no-sandbox']} : {};
  const browser = await type.launch(settings);
  try {
    const widths = engine==='chromium' ? [1440,390,320,768,1920,844] : engine==='firefox' ? [390,1440,2560] : [390,1440];
    const profiles=widths.map(w=>({width:w,mode:'default'}));
    if(engine==='chromium') profiles.push({width:390,mode:'no-js'},{width:1440,mode:'text-200'},{width:320,mode:'reflow'},{width:390,mode:'dpr2'});
    for (const {width,mode} of profiles) {
      const context=await browser.newContext({
        viewport:{width,height:mode==='reflow'?225:width===844?390:width<800?844:900},
        deviceScaleFactor:mode==='dpr2'?2:1,javaScriptEnabled:mode!=='no-js',reducedMotion:'reduce'
      });
      const page=await context.newPage(), errors=[];
      page.on('requestfailed',r=>errors.push(r.url()+' '+r.failure()?.errorText));
      page.on('pageerror',e=>errors.push(e.message));
      page.on('response',r=>{if(r.status()>=400) errors.push(r.status()+' '+r.url());});
      page.on('request',r=>{if(new URL(r.url()).origin!==new URL(url).origin) errors.push('External request '+r.url());});
      if(mode==='text-200') await page.addInitScript(()=>document.addEventListener('DOMContentLoaded',()=>document.documentElement.style.fontSize='200%'));
      await page.goto(url,{waitUntil:'networkidle'});await page.evaluate(()=>document.fonts.ready);
      const initial=await page.evaluate(()=>performance.getEntriesByType('resource').reduce((n,r)=>n+r.transferSize,performance.getEntriesByType('navigation')[0].transferSize));
      const hero=await page.locator('.hero-view .art img').evaluate(e=>e.currentSrc.split('/').pop());
      await page.keyboard.press('Tab');
      assert(await page.locator('.skip-link').evaluate(e=>e===document.activeElement && getComputedStyle(e).outlineStyle!=='none'),'Skip link focus');
      if(width<=800 && width!==844) {
        const summary=page.locator('.mobile-nav summary');
        await summary.focus();await page.keyboard.press('Enter');
        assert(await page.locator('.mobile-nav nav a').first().isVisible(),'Mobile navigation');
        await page.keyboard.press('Enter');
      }
      for(const section of await page.locator('main > section').all()) {
        await section.scrollIntoViewIfNeeded();
        for(const image of await section.locator('img').all()){
          await image.evaluate(i=>i.decode());
        }
      }
      const geometry=await page.evaluate(()=>{
        const svg=[...document.querySelectorAll('.hut-mesh')].find(e=>e.getBoundingClientRect().width);
        const plane=svg.getBoundingClientRect(), photo=document.querySelector('.mesh-figure .art img').getBoundingClientRect();
        return {overflow:document.documentElement.scrollWidth>innerWidth,
          peers:svg.querySelectorAll('[data-hut]').length,edges:svg.querySelectorAll('[data-edge]').length,
          aligned:Math.abs(plane.width-photo.width)<1&&Math.abs(plane.height-photo.height)<1,
          docs:[...document.querySelectorAll('.actions a')].map(e=>e.href),
          missing:[...document.images].filter(i=>!i.naturalWidth).map(i=>i.src)
        };
      });
      assert(!geometry.overflow && !geometry.missing.length && geometry.aligned && geometry.peers===6 && geometry.edges===9,JSON.stringify({engine,width,mode,geometry}));
      assert(geometry.docs.filter(s=>s.endsWith('/docs/#map')).length===2,'Documentation links');
      assert(!errors.length,JSON.stringify(errors));
      const diagnostics=[];
      if(engine==='chromium'&&mode==='default'&&[390,1440].includes(width)){
        const scan=await new AxeBuilder({page}).withTags(['wcag2a','wcag2aa','wcag21aa','wcag22aa']).analyze();
        for(const v of scan.violations) diagnostics.push({id:v.id,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))});
      }
      await page.evaluate(()=>{document.activeElement?.blur();window.scrollTo(0,0);});
      if(mode==='default'||mode==='text-200'||mode==='reflow'){
        await page.screenshot({path:path.join(out,engine+'-'+width+'-'+mode+'.jpg'),type:'jpeg',quality:87,fullPage:true});
        if(engine==='chromium'&&mode==='default'&&[1440,390].includes(width)){
          for (const el of await page.locator('main>section').all()) {
            await el.screenshot({path:path.join(out,width+'-'+await el.getAttribute('id')+'.jpg'),type:'jpeg',quality:91});
          }
        }
      }
      for(const detail of await page.locator('main details').all()){
        await detail.locator('summary').focus();await page.keyboard.press('Enter');
        assert(await detail.getAttribute('open')!==null,'Keyboard disclosure');await page.keyboard.press('Enter');
      }
      const total=await page.evaluate(()=>performance.getEntriesByType('resource').reduce((n,r)=>n+r.transferSize,performance.getEntriesByType('navigation')[0].transferSize));
      records.push({engine,width,mode,initial,total,hero,diagnostics,...geometry,errors});
      fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(records,null,2)+'\n');
      console.log(engine, width, mode, initial, total, diagnostics.map(d=>d.id).join(','));
      await context.close();
    }
  } finally {await browser.close();}
}
assert(records.every(r=>!r.diagnostics.length),'See accessibility findings in results.json');

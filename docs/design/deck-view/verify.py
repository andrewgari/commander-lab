"""Render and exercise offline design artifacts; no production dependency."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

R=Path(__file__).resolve().parent
BASE='http://localhost:18932/docs/design/deck-view/'

def main():
    manifest=json.loads((R/'current/manifest.json').read_text())
    keys=list(dict.fromkeys(x['page'] for x in manifest['captures']))
    assert len(keys)==12 and len(manifest['captures'])==24
    assert all((R/'current'/c['screenshot']).is_file() and c['status']==200 for c in manifest['captures'])
    items=''.join(f'<article><h2>{k.replace("-"," ").title()}</h2><a href="current/{k}-1440.png"><img loading="lazy" src="current/{k}-1440.png" alt="Real {k} page at 1440 pixels, top of full-page capture"></a><div><a href="current/{k}-1440.png">1440 full page</a> · <a href="current/{k}-390.png">390 full page</a> · <a href="current/{k}.txt">Text transcript</a></div></article>' for k in keys)
    (R/'evidence.html').write_text('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Current deck view evidence</title><link rel="stylesheet" href="deck-view.css"></head><body><main class="canvas"><p class="eyebrow">DV-1 · Real application, not mockups</p><h1>Current-state evidence</h1><p class="explanation">Holy Hellfire · production read-only browser inspection. Twelve deck surfaces × two widths. Open full-page images to inspect lower sections. Non-read requests were blocked; the Primer draft failure is caused by that guard, not a verified service outage.</p><div class="actions"><a class="button" href="overview.html">Overview directions</a><a class="button" href="workspaces.html">Workspace proposals</a><a class="button" href="current/manifest.json">Capture manifest</a></div><section class="evidence-grid" style="margin-top:var(--s8)">'+items+'</section></main></body></html>')
    shots=R/'proposed';shots.mkdir(exist_ok=True)
    results=[]
    with sync_playwright() as p:
        b=p.chromium.launch()
        c=b.new_context(reduced_motion='reduce')
        page=c.new_page()
        errors=[]
        page.on('pageerror',lambda e: errors.append(str(e)))
        urls=[('direction-'+d,'overview.html?direction='+d) for d in ['a','b','c']]+[(k,'workspaces.html?page='+k) for k in ['cards','stats','identity','journal','tune','ideas','review','simulate','settings']]
        for key,url in urls:
            for w in [1440,390]:
                page.set_viewport_size({'width':w,'height':900 if w==1440 else 844})
                r=page.goto(BASE+url)
                assert r is not None
                page.locator('img').evaluate_all('(es)=>es.forEach(e=>e.loading="eager")')
                page.wait_for_function('[...document.images].every(i=>i.complete && i.naturalWidth>0)')
                page.wait_for_timeout(200)
                metrics=page.evaluate('({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,main:document.querySelectorAll("main").length,broken:[...document.images].filter(i=>!i.complete||!i.naturalWidth).length})')
                assert r.status==200 and metrics['scrollWidth']==w and metrics['main']==1 and metrics['broken']==0,(key,w,metrics)
                # Local navigation links must resolve to an actual artifact.
                for href in page.locator('a[href]').evaluate_all('(es)=>es.map(e=>e.getAttribute("href"))'):
                    if href.startswith(('http','#','?')):continue
                    target=(R/href.split('?')[0]).resolve()
                    assert target.exists(),(key,href)
                page.screenshot(path=str(shots/f'{key}-{w}.png'),full_page=True)
                results.append({'screen':key,'width':w,**metrics})
        page.goto(BASE+'workspaces.html?page=tune')
        page.get_by_role('button',name='Stage this example swap').click()
        assert '1' in page.locator('#staged-count').inner_text()
        page.get_by_role('button',name='Clear example').click()
        assert '0' in page.locator('#staged-count').inner_text()
        page.goto(BASE+'workspaces.html?page=cards')
        page.locator('#card-search').fill('Dragon')
        assert page.locator('[data-example-card]:visible').count()==1
        page.get_by_role('link',name='List view',exact=True).click()
        assert page.locator('table').count()==1
        page.goto(BASE+'overview.html?direction=c&focus=test')
        assert page.get_by_role('heading',name='Does Kaalia attack soon enough?').count()==1
        page.goto(BASE+'overview.html?direction=b')
        page.set_viewport_size({'width':390,'height':844})
        page.locator('#sections').select_option('workspaces.html?page=settings')
        page.wait_for_url('**/workspaces.html?page=settings')
        assert 'page=settings' in page.url
        page.goto(BASE+'workspaces.html?page=review')
        page.locator('summary').first.focus()
        page.keyboard.press('Enter')
        assert page.locator('details').first.get_attribute('open') is not None
        page.keyboard.press('Tab')
        focus=page.evaluate('getComputedStyle(document.activeElement).outlineStyle')
        assert focus!='none'
        # 200% equivalent CSS viewport reflow; not a production zoom certification.
        page.set_viewport_size({'width':720,'height':450})
        page.goto(BASE+'overview.html?direction=b')
        assert page.evaluate('document.documentElement.scrollWidth')==720
        assert not errors,errors
        b.close()
    report={'real_pages':len(keys),'real_captures':len(manifest['captures']),'mockup_renders':results,'checks':['No page overflow at 1440/390','One main landmark per prototype','No broken images or local links','No JS page errors','Stage/clear demo','Card search and list toggle','Decision desk test panel','Mobile section chooser','Keyboard disclosure and visible focus','720px reflow check under reduced motion'],'production_mutations':'None intentionally submitted; browser write guard used'}
    (R/'verification.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({'real_pages':len(keys),'real_captures':len(manifest['captures']),'mockup_renders':len(results),'checks':report['checks']},indent=2))
if __name__=='__main__':main()

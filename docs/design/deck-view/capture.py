"""Read-only live audit capture; blocks every non-GET/HEAD request."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parent
BASE='http://tower:8020'
DECK='/deck/Holy%20Hellfire'
PAGES={'overview':DECK,'stats':DECK+'/stats','identity':DECK+'/identity','journal':DECK+'/journal','tune':DECK+'/tune','ideas':DECK+'/ideas','review':DECK+'/review','simulate':DECK+'/sim','manage':DECK+'/manage','composition':DECK+'/composition','primer-builder':DECK+'/primer/build'}

def main():
    out=ROOT/'current';out.mkdir(exist_ok=True)
    manifest=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context(viewport={'width':1440,'height':900},reduced_motion='reduce')
        blocked=[]
        def guard(route):
            if route.request.method not in ('GET','HEAD','OPTIONS'):
                blocked.append({'url':route.request.url,'method':route.request.method});route.abort()
            else:route.continue_()
        context.route('**/*',guard)
        page=context.new_page()
        for key,url in list(PAGES.items()):
            res=page.goto(BASE+url,wait_until='domcontentloaded',timeout=60000)
            page.wait_for_timeout(4500)
            if key=='ideas':
                links=page.locator('.hud-idea-theme').evaluate_all('(els)=>els.map(a=>a.getAttribute("href"))')
                if links:PAGES['theory']=links[0]
            text=page.locator('main').first.inner_text() if page.locator('main').count() else page.locator('body').inner_text()
            (out/(key+'.txt')).write_text(text)
            links=page.locator('main a').evaluate_all('(els)=>els.map(a=>({text:a.innerText,href:a.getAttribute("href")}))')
            for width in [1440,390]:
                page.set_viewport_size({'width':width,'height':900 if width==1440 else 844});page.wait_for_timeout(700)
                page.screenshot(path=str(out/f'{key}-{width}.png'),full_page=True,timeout=60000)
                manifest.append({'page':key,'url':BASE+url,'status':res.status,'width':width,'height':page.evaluate('document.documentElement.scrollHeight'),'scrollWidth':page.evaluate('document.documentElement.scrollWidth'),'screenshot':f'{key}-{width}.png'})
            print(key,res.status,len(text),flush=True)
        if 'theory' in PAGES:
            res=page.goto(BASE+PAGES['theory'],wait_until='domcontentloaded');page.wait_for_timeout(3000)
            (out/'theory.txt').write_text(page.locator('main').first.inner_text())
            for width in [1440,390]:
                page.set_viewport_size({'width':width,'height':900 if width==1440 else 844});page.wait_for_timeout(500)
                page.screenshot(path=str(out/f'theory-{width}.png'),full_page=True)
                manifest.append({'page':'theory','url':page.url,'status':res.status,'width':width,'height':page.evaluate('document.documentElement.scrollHeight'),'scrollWidth':page.evaluate('document.documentElement.scrollWidth'),'screenshot':f'theory-{width}.png'})
        (out/'manifest.json').write_text(json.dumps({'captures':manifest,'blocked_writes':blocked},indent=2))
        browser.close()
    print('Captured',len(manifest),'screenshots; blocked writes:',len(blocked))
if __name__=='__main__':main()

import json
from pathlib import Path
from playwright.sync_api import sync_playwright
from PIL import Image,ImageDraw
R=Path(__file__).resolve().parent
out=R/'current'
m=json.loads((out/'manifest.json').read_text())
m['captures']=[c for c in m['captures'] if c['page']!='theory']
with sync_playwright() as p:
    b=p.chromium.launch()
    c=b.new_context(viewport={'width':1440,'height':900},reduced_motion='reduce')
    def guard(r):
        if r.request.method not in ['GET','HEAD','OPTIONS']:r.abort()
        else:r.continue_()
    c.route('**/*',guard)
    page=c.new_page()
    page.goto('http://tower:8020/deck/Holy%20Hellfire/ideas');page.wait_for_timeout(1000)
    href=page.locator('.hud-idea-theme').first.get_attribute('href')
    res=page.goto('http://tower:8020'+href);page.wait_for_timeout(1500)
    (out/'theory.txt').write_text(page.locator('main').first.inner_text())
    for w in [1440,390]:
        page.set_viewport_size({'width':w,'height':900 if w==1440 else 844});page.wait_for_timeout(400)
        page.screenshot(path=str(out/f'theory-{w}.png'),full_page=True)
        m['captures'].append({'page':'theory','url':page.url,'status':res.status,'width':w,'height':page.evaluate('document.documentElement.scrollHeight'),'scrollWidth':page.evaluate('document.documentElement.scrollWidth'),'screenshot':f'theory-{w}.png'})
    page.goto('http://tower:8020/deck/Holy%20Hellfire');page.wait_for_timeout(3000)
    positions={}
    for w in [1440,390]:
        page.set_viewport_size({'width':w,'height':900 if w==1440 else 844});page.wait_for_timeout(400)
        positions[str(w)]=page.locator('#syncControls,#psPanel,#deckDescriptionPanel,#decklistContainer,.deck-list-head,#deckCurve').evaluate_all('(els)=>els.map(e=>({id:e.id||e.className,y:Math.round(e.getBoundingClientRect().top+scrollY)}))')
    (out/'positions.json').write_text(json.dumps(positions,indent=2))
    b.close()
(out/'manifest.json').write_text(json.dumps(m,indent=2))
keys=list(dict.fromkeys(x['page'] for x in m['captures']))
for width in [1440,390]:
    tw=480 if width==1440 else 260
    th=330 if width==1440 else 585
    sheet=Image.new('RGB',(tw*3, (th+30)*((len(keys)+2)//3)), '#e8e8e8')
    d=ImageDraw.Draw(sheet)
    for i,k in enumerate(keys):
        im=Image.open(out/f'{k}-{width}.png').convert('RGB')
        im=im.crop((0,0,width,min(im.height,900 if width==1440 else 844)))
        im.thumbnail((tw,th))
        x=(i%3)*tw;y=(i//3)*(th+30)
        d.text((x+8,y+7),f'{k} / {width}',fill='black');sheet.paste(im,(x,y+30))
    sheet.save(R/f'current-contact-{width}.jpg',quality=90)
print('Pages',keys,'screenshots',len(m['captures']),'positions',positions)

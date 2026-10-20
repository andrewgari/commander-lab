"""Download only art already referenced by the observed deck."""
import json
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright
R=Path(__file__).resolve().parent
names={'Kaalia of the Vast':'commander','Dragon Tempest':'dragon-tempest','Lyra Dawnbringer':'lyra','Lightning Greaves':'greaves','Demonic Tutor':'tutor','Sol Ring':'sol-ring'}
with sync_playwright() as p:
    b=p.chromium.launch();page=b.new_page()
    page.route('**/*',lambda r:r.continue_() if r.request.method in ['GET','HEAD','OPTIONS'] else r.abort())
    page.goto('http://tower:8020/deck/Holy%20Hellfire');page.wait_for_timeout(4000)
    els=page.locator('#commanderShowcase img,#decklistContainer img').evaluate_all('(els)=>els.map(e=>({src:e.currentSrc || e.src,alt:e.alt}))')
    sources=[]
    for name,filename in names.items():
        img=next(x for x in els if x['alt']==name)
        url=img['src'].replace('/large/','/art_crop/').replace('/normal/','/art_crop/')
        (R/(filename+'.jpg')).write_bytes(urlopen(url).read())
        sources.append({'card':name,'file':filename+'.jpg','url':url,'credit':'Magic: The Gathering card illustration © Wizards of the Coast; via Scryfall. Design-reference use.'})
    (R/'art-sources.json').write_text(json.dumps(sources,indent=2))
    b.close()
print('Downloaded',len(sources),'observed card illustrations')

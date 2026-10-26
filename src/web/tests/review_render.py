"""Render every review scenario at desktop/mobile and exercise Chromium print.
Start the SvelteKit dev server on port 5178, then run:
  uv run --with playwright --with pymupdf python src/web/tests/review_render.py
"""
import json, os
from pathlib import Path
from playwright.sync_api import sync_playwright
import pymupdf

out=Path(os.environ.get('REVIEW_ARTIFACTS','artifacts/review')); out.mkdir(parents=True,exist_ok=True)
base=os.environ.get('REVIEW_URL','http://127.0.0.1:5178/dev/review/')
results=[]; errors=[]; unexpected_external=[]; allowed_images=[]
def deny_external_except_card_images(route):
 url=route.request.url
 allowed=url.startswith('https://api.scryfall.com/cards/named?exact=') and 'format=image' in url and 'version=small' in url
 if allowed: allowed_images.append(url)
 else: unexpected_external.append(url)
 route.abort()
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True)
 page=browser.new_page(viewport={'width':1440,'height':1000})
 page.on('pageerror',lambda e:errors.append(str(e)))
 # All card image requests are intentionally denied for deterministic offline rendering.
 page.route('https://**/*',deny_external_except_card_images)
 for width in [1440,390]:
  page.set_viewport_size({'width':width,'height':1000})
  for variant in ['example','no-cuts','many-swaps','long-names','unavailable-chart','grade-a']:
   response=page.goto(base+'?variant='+variant); page.wait_for_load_state('networkidle')
   assert response is not None and response.status==200, (variant, response.status if response else None)
   assert page.locator('h1').count()==1
   assert page.locator('.priorities li').count()==3
   expected_swaps=13 if variant=='many-swaps' else 3 if variant=='grade-a' else 9
   assert page.locator('.swap').count()==expected_swaps,(variant,page.locator('.swap').count())
   assert page.locator('.debug').get_attribute('open') is None
   assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), variant
   assert page.locator('a[href^="#"]').evaluate_all('(els)=>els.every(a=>document.getElementById(a.hash.slice(1)))')
   assert page.locator('.score-meter').count()==6
   widths=page.locator('.score-meter span').evaluate_all('(els)=>els.map(e=>parseFloat(e.style.width))')
   scores=page.locator('.score').evaluate_all('(els)=>els.map(e=>Number(e.querySelector(".score-head > span strong").textContent))')
   assert widths==[score*10 for score in scores], (variant,widths,scores)
   assert page.locator('.chart svg[role="img"]').count() >= 7
   assert page.locator('.card-image').count() >= 2
   assert page.locator('.card-image img, .image-fallback').count() >= 2
   image_srcs=page.locator('.card-image img').evaluate_all('(els)=>els.map(e=>e.src)')
   assert all(url.startswith('https://api.scryfall.com/cards/named?exact=') for url in image_srcs)
   if variant=='no-cuts': assert 'No additional cuts suggested' in page.locator('.review-report').inner_text()
   if variant=='many-swaps': assert len(set(page.locator('.swap').evaluate_all('(els)=>els.map(e=>e.id)')))==13
   if variant=='long-names': assert len(page.locator('.swap').nth(0).locator('.swap-card h3').nth(0).inner_text()) >= 20
   if variant=='unavailable-chart': assert 'Unavailable' in page.locator('.chart').nth(2).inner_text()
   if variant=='grade-a':
    assert page.locator('.grade strong').inner_text()=='A'
    assert page.locator('.review-report .finding[id^="finding-"]').count()==4
    assert 'No additional cuts suggested' in page.locator('.review-report').inner_text()
   results.append({'width':width,'variant':variant,'status':'passed'})
 assert allowed_images, 'No cardImage URLs were requested'
 assert not unexpected_external, unexpected_external
 # Full-page desktop/mobile captures, with card CDN online if reachable.
 page.unroute('https://**/*')
 for width,label in [(1440,'desktop'),(390,'mobile')]:
  page.set_viewport_size({'width':width,'height':1000});page.goto(base);page.wait_for_timeout(4500)
  page.screenshot(path=str(out/f'{label}.png'),full_page=True)
 page.set_viewport_size({'width':1440,'height':1000});page.goto(base+'?theme=light');page.wait_for_timeout(1500)
 page.screenshot(path=str(out/'light.png'),full_page=True)
 page.goto(base+'?print=1');page.wait_for_timeout(1500);page.emulate_media(media='print')
 assert page.locator('.review-preview').count()==0
 for selector in ['.swap','.chart']:
  assert page.locator(selector).evaluate_all('(els)=>els.every(e=>getComputedStyle(e).breakInside==="avoid")')
 page.pdf(path=str(out/'review.pdf'),prefer_css_page_size=True,print_background=True)
 assert not errors,errors
 browser.close()
pdf=pymupdf.open(out/'review.pdf')
texts=[p.get_text() for p in pdf]
assert 1 <= len(pdf) <= 10, len(pdf)
for text in texts:
 if 'Recommended swaps' in text: assert 'SWAP 01' in text, 'Swap heading separated from first card'
for i in range(min(3,len(pdf))):
 pix=pdf[i].get_pixmap(matrix=pymupdf.Matrix(1.2,1.2),alpha=False)
 pix.save(out/f'print-preview-{i+1}.png')
 # JPEG is a compact format for direct review-card screenshots.
 pix.save(out/f'print-preview-{i+1}.jpg',output='jpeg',jpg_quality=78)
# Repack the full-page screenshots as modest JPEGs for card attachments.
for name in ['desktop','mobile']:
 pix=pymupdf.Pixmap(out/f'{name}.png')
 pix.save(out/f'{name}.jpg',output='jpeg',jpg_quality=74)
(out/'results.json').write_text(json.dumps({'renders':results,'browser_errors':errors,'unexpected_external_requests':unexpected_external,'pdf_pages':len(pdf),'print_page_texts':texts},indent=2))
print(f'{len(results)} renders passed; no browser errors or non-card external requests; PDF {len(pdf)} pages; artifacts {out.resolve()}')

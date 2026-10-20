import json,re
from pathlib import Path
from PIL import Image, ImageDraw
R=Path(__file__).resolve().parent
for w,h in [(1440,1050),(390,1500)]:
    image=Image.open(R/'proposed'/f'direction-b-{w}.png')
    image.crop((0,0,w,min(image.height,h))).save(R/f'preview-b-{w}.png')
for width in [1440,390]:
    tw=480 if width==1440 else 260
    th=330 if width==1440 else 585
    files=sorted((R/'proposed').glob(f'*-{width}.png'))
    sheet=Image.new('RGB',(tw*3,(th+30)*((len(files)+2)//3)),'#e8e8e8')
    d=ImageDraw.Draw(sheet)
    for i,f in enumerate(files):
        im=Image.open(f).convert('RGB').crop((0,0,width,900 if width==1440 else 844))
        im.thumbnail((tw,th));x=(i%3)*tw;y=(i//3)*(th+30)
        d.text((x+8,y+7),f.stem,fill='black');sheet.paste(im,(x,y+30))
    sheet.save(R/f'proposed-contact-{width}.jpg',quality=90)
# Verify doc-relative local links and counts in the actual artifact set.
doc=R.parents[1]/'DESIGN_DECK_VIEW.md'
text=doc.read_text()
for link in re.findall(r'\]\(([^)]+)\)',text):
    if link.startswith(('http','#')):continue
    assert (doc.parent/link.split('#')[0]).exists(),link
print('Doc local links OK; screenshots:',len(list((R/'current').glob('*.png'))),'real,',len(list((R/'proposed').glob('*.png'))),'proposed')
def lum(hex):
    rgb=[int(hex[i:i+2],16)/255 for i in (1,3,5)]
    return sum(v*w for v,w in zip([v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in rgb],[.2126,.7152,.0722]))
for a,b in [('#edf1f4','#161e28'),('#b0bac5','#1d2835'),('#79d9ef','#161e28'),('#0d1117','#ffbf47'),('#607080','#161e28')]:
    x,y=sorted([lum(a),lum(b)])
    print(a,b,round((y+.05)/(x+.05),2))

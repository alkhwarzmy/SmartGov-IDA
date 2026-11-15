"""Render the frozen fictional references; outputs must remain outside Git."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from fpdf import FPDF

ROOT = Path(__file__).resolve().parents[1]
FONTS = {
    'development': Path('/usr/share/fonts/truetype/noto/NotoNaskhArabic-Regular.ttf'),
    'test': Path('/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf'),
}


def parts(line):
    for delimiter in (': ', ' — '):
        if delimiter in line:
            label, value = line.split(delimiter,1)
            return [(label,'rtl'),(delimiter,'ltr'),(value,'ltr' if value.isascii() else 'rtl')]
    return [(line,'rtl')]


def pdf_base(font):
    pdf=FPDF();pdf.set_creation_date(datetime(2026,11,15,tzinfo=timezone.utc))
    pdf.add_page();pdf.add_font('Arabic','',str(font));pdf.add_font('Latin','', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    return pdf


def generate(output: Path):
    output.mkdir(parents=True,exist_ok=True)
    records=json.loads((ROOT/'evaluation/ground_truth.json').read_text())
    manifest=[]
    for item in records:
        font_path=FONTS[item['split']]
        font=ImageFont.truetype(str(font_path),48)
        latin=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',48)
        image=Image.new('RGB',(1654,2339),'white');draw=ImageDraw.Draw(image)
        pdf=pdf_base(font_path)
        for index,line in enumerate(item['reference_text'].splitlines()):
            x=1530; px=195
            for part,direction in parts(line):
                selected=latin if part.isascii() else font
                draw.text((x,150+index*110),part,font=selected,fill='black',anchor='rt',direction=direction)
                x-=draw.textlength(part,font=selected,direction=direction)
                pdf.set_font('Latin' if part.isascii() else 'Arabic',size=17)
                pdf.set_text_shaping(True,direction=direction)
                width=pdf.get_string_width(part)+0.5
                px-=width;pdf.set_xy(px,20+index*14);pdf.cell(width,10,part)
        prefix=output/item['id']
        png=prefix.with_suffix('.png');image.save(png,dpi=(200,200))
        jpeg=prefix.with_suffix('.jpg');image.save(jpeg,quality=88,dpi=(200,200))
        digital=output/f'{item["id"]}-digital.pdf';pdf.output(str(digital))
        scanned=output/f'{item["id"]}-scanned.pdf';scan=pdf_base(font_path);scan.image(str(png),x=0,y=0,w=210,h=297);scan.output(str(scanned))
        for variant,path in [('png',png),('jpeg',jpeg),('digital_pdf',digital),('scanned_pdf',scanned)]:
            manifest.append(dict(document_id=item['id'],split=item['split'],category=item['category'],template_family=item['template_family'],variant=variant,file=path.name,sha256=hashlib.sha256(path.read_bytes()).hexdigest(),bytes=path.stat().st_size,pages=1))
    metadata=dict(generator_version=1,ground_truth_sha256=hashlib.sha256((ROOT/'evaluation/ground_truth.json').read_bytes()).hexdigest(),
                  fonts={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [*FONTS.values(),Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]},
                  image_dimensions=[1654,2339],dpi=200,source_license='CC0-1.0 (text); font software licenses remain separate',records=manifest)
    assert sum(x['pages'] for x in manifest)<=200
    assert sum(x['bytes'] for x in manifest)<=500*1024**2
    (output/'manifest.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
    return metadata

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();meta=generate(args.output)
    print(json.dumps(dict(files=len(meta['records']),pages=sum(x['pages'] for x in meta['records']),bytes=sum(x['bytes'] for x in meta['records']))))

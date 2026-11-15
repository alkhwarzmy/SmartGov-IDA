"""Browser smoke test against a running local app; uses fictional documents only."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--url',default='http://127.0.0.1:8765');parser.add_argument('--artifacts',type=Path,default=Path('/workspace/smartgov-artifacts'))
    args=parser.parse_args()
    if urlsplit(args.url).hostname not in ('127.0.0.1','localhost'):raise SystemExit('Only a local app may be tested')
    args.artifacts.mkdir(parents=True,exist_ok=True)
    source=Path('/workspace/smartgov-corpus/v1/development-letter-01-digital.pdf')
    errors=[];external=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path=shutil.which('chromium'),headless=True,args=['--no-sandbox'])
        page=browser.new_page(viewport={'width':1440,'height':1100})
        page.on('pageerror',lambda error:errors.append(str(error)))
        def route(request):
            parsed=urlsplit(request.request.url)
            host=parsed.hostname
            if parsed.scheme in ('http','https','ws','wss') and host not in ('127.0.0.1','localhost'):
                external.append(request.request.url);request.abort()
            else:request.continue_()
        page.route('**/*',route)
        page.goto(args.url);expect(page.locator('body')).to_have_attribute('aria-busy','false')
        page.locator('#file').set_input_files(source)
        page.get_by_role('button',name='رفع ومعالجة',exact=True).click()
        page.locator('#detail h2').filter(has_text=source.name).wait_for()
        expect(page.locator('body')).to_have_attribute('aria-busy','false')
        row=page.locator('[data-field="date"]')
        row.get_by_label('تقويم التاريخ').select_option('gregorian')
        row.get_by_role('button',name='حفظ الحقل').click();expect(page.locator('body')).to_have_attribute('aria-busy','false')
        row=page.locator('[data-field="subject"]');row.get_by_label('الموضوع',exact=True).fill('موضوع بحث موثق')
        row.get_by_role('button',name='حفظ الحقل').click();expect(page.locator('body')).to_have_attribute('aria-busy','false')
        page.locator('#acknowledge').check();page.get_by_role('button',name='تأكيد اكتمال المراجعة').click();expect(page.locator('body')).to_have_attribute('aria-busy','false')
        assert page.locator('#detail .badge.completed').count()==1
        page.locator('#query').fill('موضوع بحث موثق');page.get_by_role('button',name='بحث',exact=True).click();expect(page.locator('body')).to_have_attribute('aria-busy','false')
        assert page.locator('#documents .doc-card').count()==1
        with page.expect_download() as pending:page.get_by_role('link',name='تنزيل الأصل',exact=True).click()
        downloaded=pending.value.path()
        assert hashlib.sha256(Path(downloaded).read_bytes()).digest()==hashlib.sha256(source.read_bytes()).digest()
        expect(page.locator('.preview')).to_be_visible()
        assert page.locator('.preview').evaluate('(image) => image.complete && image.naturalWidth > 0')
        page.screenshot(path=str(args.artifacts/'archive-desktop.png'),full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
        page.screenshot(path=str(args.artifacts/'archive-mobile.png'),full_page=True)
        assert not errors,errors
        assert not external,external
        result=dict(upload=True,field_correction=True,confirmation=True,corrected_value_search=True,original_download_hash=True,mobile_no_horizontal_overflow=True,js_errors=errors,external_app_requests=external)
        (ROOT/'evaluation/results/browser-smoke.json').write_text(json.dumps(result,indent=2)+'\n')
        browser.close()
    print(json.dumps(result))

if __name__=='__main__':main()

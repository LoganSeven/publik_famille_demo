import io
import xml.etree.ElementTree as ET
import zipfile

from lingo.utils import ods


def login(app, username='admin', password='admin'):
    login_page = app.get('/login/')
    login_form = login_page.forms[0]
    login_form['username'] = username
    login_form['password'] = password
    resp = login_form.submit()
    assert resp.status_int == 302
    return app


def get_ods_rows(resp):
    with zipfile.ZipFile(io.BytesIO(resp.body)) as zipf:
        with zipf.open('content.xml') as fd:
            ods_sheet = ET.parse(fd)
            for row in ods_sheet.findall('.//{%s}table-row' % ods.NS['table']):
                yield [
                    x.text for x in row.findall('{%s}table-cell/{%s}p' % (ods.NS['table'], ods.NS['text']))
                ]

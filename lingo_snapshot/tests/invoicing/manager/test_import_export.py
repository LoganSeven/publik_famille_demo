import copy
import json

import pytest
from webtest import Upload

from lingo.invoicing.models import Regie
from lingo.snapshot.models import RegieSnapshot
from tests.utils import login

pytestmark = pytest.mark.django_db


def test_export_site(freezer, app, admin_user):
    freezer.move_to('2020-06-15')
    login(app)
    resp = app.get('/manage/invoicing/regies/')
    resp = resp.click('Export')

    resp = resp.form.submit()
    assert resp.headers['content-type'] == 'application/json'
    assert (
        resp.headers['content-disposition'] == 'attachment; filename="export_invoicing_config_20200615.json"'
    )

    site_json = json.loads(resp.text)
    assert site_json == {
        'regies': [],
    }

    Regie.objects.create(label='Foo Bar')
    resp = app.get('/manage/invoicing/export/')
    resp = resp.form.submit()

    site_json = json.loads(resp.text)
    assert len(site_json['regies']) == 1

    resp = app.get('/manage/invoicing/export/')
    resp.form['regies'] = False
    resp = resp.form.submit()

    site_json = json.loads(resp.text)
    assert 'regies' not in site_json

    resp = app.get('/manage/invoicing/export/')
    resp = resp.form.submit()

    site_text = resp.text
    site_json = json.loads(site_text)
    assert len(site_json['regies']) == 1
    resp = app.get('/manage/invoicing/import/')
    resp.form['config_json'] = Upload('export.json', site_text.encode('utf-8'), 'application/json')
    resp = resp.form.submit().follow()
    assert RegieSnapshot.objects.count() == 1


@pytest.mark.freeze_time('2023-06-02')
def test_import_regie(app, admin_user):
    regie = Regie.objects.create(label='Foo bar')

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/parameters/' % regie.pk)
    resp = resp.click(href='/manage/invoicing/regie/%s/export/' % regie.pk)
    assert resp.headers['content-type'] == 'application/json'
    assert resp.headers['content-disposition'] == 'attachment; filename="export_regie_foo-bar_20230602.json"'
    regie_export = resp.text

    # existing regie
    resp = app.get('/manage/invoicing/regies/')
    resp = resp.click('Import')
    resp.form['config_json'] = Upload('export.json', regie_export.encode('utf-8'), 'application/json')
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/regie/%s/' % regie.pk)
    resp = resp.follow()
    assert 'No regie created. A regie has been updated.' not in resp.text
    assert Regie.objects.count() == 1

    # new regie
    Regie.objects.all().delete()
    resp = app.get('/manage/invoicing/regies/')
    resp = resp.click('Import')
    resp.form['config_json'] = Upload('export.json', regie_export.encode('utf-8'), 'application/json')
    resp = resp.form.submit()
    regie = Regie.objects.latest('pk')
    assert resp.location.endswith('/manage/invoicing/regie/%s/' % regie.pk)
    resp = resp.follow()
    assert 'A regie has been created. No regie updated.' not in resp.text
    assert Regie.objects.count() == 1

    # multiple regies
    regies = json.loads(regie_export)
    regies['regies'].append(copy.copy(regies['regies'][0]))
    regies['regies'].append(copy.copy(regies['regies'][0]))
    regies['regies'][1]['label'] = 'Foo bar 2'
    regies['regies'][1]['slug'] = 'foo-bar-2'
    regies['regies'][2]['label'] = 'Foo bar 3'
    regies['regies'][2]['slug'] = 'foo-bar-3'

    resp = app.get('/manage/invoicing/regies/')
    resp = resp.click('Import')
    resp.form['config_json'] = Upload('export.json', json.dumps(regies).encode('utf-8'), 'application/json')
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/regies/')
    resp = resp.follow()
    assert '2 regies have been created. A regie has been updated.' in resp.text
    assert Regie.objects.count() == 3

    Regie.objects.all().delete()
    resp = app.get('/manage/invoicing/regies/')
    resp = resp.click('Import')
    resp.form['config_json'] = Upload('export.json', json.dumps(regies).encode('utf-8'), 'application/json')
    resp = resp.form.submit().follow()
    assert '3 regies have been created. No regie updated.' in resp.text
    assert Regie.objects.count() == 3

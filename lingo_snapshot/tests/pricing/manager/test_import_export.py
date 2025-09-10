import copy
import datetime
import json

import pytest
from webtest import Upload

from lingo.agendas.models import Agenda, CheckType, CheckTypeGroup
from lingo.pricing.models import Criteria, CriteriaCategory, Pricing
from lingo.snapshot.models import (
    AgendaSnapshot,
    CheckTypeGroupSnapshot,
    CriteriaCategorySnapshot,
    PricingSnapshot,
)
from tests.utils import login

pytestmark = pytest.mark.django_db


def test_export_site(freezer, app, admin_user):
    freezer.move_to('2020-06-15')
    login(app)
    resp = app.get('/manage/pricing/')
    resp = resp.click('Export')

    resp = resp.form.submit()
    assert resp.headers['content-type'] == 'application/json'
    assert resp.headers['content-disposition'] == 'attachment; filename="export_pricing_config_20200615.json"'

    site_json = json.loads(resp.text)
    assert site_json == {
        'agendas': [],
        'check_type_groups': [],
        'pricing_categories': [],
        'pricings': [],
    }

    Agenda.objects.create(label='Foo Bar')
    resp = app.get('/manage/pricing/export/')
    resp = resp.form.submit()

    site_json = json.loads(resp.text)
    assert len(site_json['agendas']) == 1

    resp = app.get('/manage/pricing/export/')
    resp.form['agendas'] = False
    resp.form['check_type_groups'] = False
    resp.form['pricing_categories'] = False
    resp.form['pricings'] = False
    resp = resp.form.submit()

    site_json = json.loads(resp.text)
    assert 'agendas' not in site_json
    assert 'check_type_groups' not in site_json
    assert 'pricing_categories' not in site_json
    assert 'pricings' not in site_json

    Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    CriteriaCategory.objects.create(label='Foo bar')
    CheckTypeGroup.objects.create(label='Foo bar')
    resp = app.get('/manage/pricing/export/')
    resp = resp.form.submit()

    site_text = resp.text
    site_json = json.loads(site_text)
    assert len(site_json['agendas']) == 1
    assert len(site_json['pricings']) == 1
    assert len(site_json['pricing_categories']) == 1
    assert len(site_json['check_type_groups']) == 1
    resp = app.get('/manage/pricing/import/')
    resp.form['config_json'] = Upload('export.json', site_text.encode('utf-8'), 'application/json')
    resp = resp.form.submit().follow()
    assert AgendaSnapshot.objects.count() == 1
    assert PricingSnapshot.objects.count() == 1
    assert CriteriaCategorySnapshot.objects.count() == 1
    assert CheckTypeGroupSnapshot.objects.count() == 1


@pytest.mark.freeze_time('2021-07-08')
def test_import_criteria_category(app, admin_user):
    category = CriteriaCategory.objects.create(label='Foo bar')
    Criteria.objects.create(label='Foo', category=category)
    Criteria.objects.create(label='Baz', category=category)

    app = login(app)
    resp = app.get('/manage/pricing/criteria/category/%s/export/' % category.id)
    assert resp.headers['content-type'] == 'application/json'
    assert (
        resp.headers['content-disposition']
        == 'attachment; filename="export_pricing_category_foo-bar_20210708.json"'
    )
    category_export = resp.text

    # existing category
    resp = app.get('/manage/pricing/')
    resp = resp.click('Import')
    resp.form['config_json'] = Upload('export.json', category_export.encode('utf-8'), 'application/json')
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/criterias/')
    resp = resp.follow()
    assert (
        'No pricing criteria category created. A pricing criteria category has been updated.' not in resp.text
    )
    assert CriteriaCategory.objects.count() == 1
    assert Criteria.objects.count() == 2

    # new category
    CriteriaCategory.objects.all().delete()
    resp = app.get('/manage/pricing/')
    resp = resp.click('Import')
    resp.form['config_json'] = Upload('export.json', category_export.encode('utf-8'), 'application/json')
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/criterias/')
    resp = resp.follow()
    assert (
        'A pricing criteria category has been created. No pricing criteria category updated.' not in resp.text
    )
    assert CriteriaCategory.objects.count() == 1
    assert Criteria.objects.count() == 2

    # multiple categories
    categories = json.loads(category_export)
    categories['pricing_categories'].append(copy.copy(categories['pricing_categories'][0]))
    categories['pricing_categories'].append(copy.copy(categories['pricing_categories'][0]))
    categories['pricing_categories'][1]['label'] = 'Foo bar 2'
    categories['pricing_categories'][1]['slug'] = 'foo-bar-2'
    categories['pricing_categories'][2]['label'] = 'Foo bar 3'
    categories['pricing_categories'][2]['slug'] = 'foo-bar-3'

    resp = app.get('/manage/pricing/')
    resp = resp.click('Import')
    resp.form['config_json'] = Upload(
        'export.json', json.dumps(categories).encode('utf-8'), 'application/json'
    )
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/')
    resp = resp.follow()
    assert (
        '2 pricing criteria categories have been created. A pricing criteria category has been updated.'
        in resp.text
    )
    assert CriteriaCategory.objects.count() == 3
    assert Criteria.objects.count() == 6

    CriteriaCategory.objects.all().delete()
    resp = app.get('/manage/pricing/')
    resp = resp.click('Import')
    resp.form['config_json'] = Upload(
        'export.json', json.dumps(categories).encode('utf-8'), 'application/json'
    )
    resp = resp.form.submit().follow()
    assert (
        '3 pricing criteria categories have been created. No pricing criteria category updated.' in resp.text
    )
    assert CriteriaCategory.objects.count() == 3
    assert Criteria.objects.count() == 6


@pytest.mark.freeze_time('2021-07-08')
def test_import_agenda(app, admin_user):
    agenda = Agenda.objects.create(label='Foo Bar')

    app = login(app)
    resp = app.get('/manage/pricing/agenda/%s/' % agenda.pk)
    resp = resp.click('Export')
    assert resp.headers['content-type'] == 'application/json'
    assert (
        resp.headers['content-disposition']
        == 'attachment; filename="export_pricing_agenda_foo-bar_20210708.json"'
    )
    agenda_export = resp.text

    # existing agenda
    resp = app.get('/manage/pricing/')
    resp = resp.click('Import')
    resp.form['config_json'] = Upload('export.json', agenda_export.encode('utf-8'), 'application/json')
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/agenda/%s/' % agenda.pk)
    resp = resp.follow()
    assert 'An agenda has been updated.' not in resp.text
    assert Agenda.objects.count() == 1

    # unknown agenda
    Agenda.objects.all().delete()
    resp = app.get('/manage/pricing/')
    resp = resp.click('Import')
    resp.form['config_json'] = Upload('export.json', agenda_export.encode('utf-8'), 'application/json')
    resp = resp.form.submit()
    assert resp.context['form'].errors['config_json'] == ['Missing "foo-bar" agenda']

    # multiple pricing
    Agenda.objects.create(label='Foo Bar')
    Agenda.objects.create(label='Foo Bar 2')
    agendas = json.loads(agenda_export)
    agendas['agendas'].append(copy.copy(agendas['agendas'][0]))
    agendas['agendas'][1]['slug'] = 'foo-bar-2'

    resp = app.get('/manage/pricing/')
    resp = resp.click('Import')
    resp.form['config_json'] = Upload('export.json', json.dumps(agendas).encode('utf-8'), 'application/json')
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/')
    resp = resp.follow()
    assert '2 agendas have been updated.' in resp.text
    assert Agenda.objects.count() == 2


@pytest.mark.freeze_time('2021-07-08')
def test_import_check_type_group(app, admin_user):
    group = CheckTypeGroup.objects.create(label='Foo bar')
    CheckType.objects.create(label='Foo reason', group=group)
    CheckType.objects.create(label='Baz', group=group)

    app = login(app)
    resp = app.get('/manage/pricing/check-type/group/%s/export/' % group.id)
    assert resp.headers['content-type'] == 'application/json'
    assert (
        resp.headers['content-disposition']
        == 'attachment; filename="export_check_type_group_foo-bar_20210708.json"'
    )
    group_export = resp.text

    # existing group
    resp = app.get('/manage/pricing/')
    resp = resp.click('Import')
    resp.form['config_json'] = Upload('export.json', group_export.encode('utf-8'), 'application/json')
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/check-types/')
    resp = resp.follow()
    assert 'No check type group created. A check type group has been updated.' not in resp.text
    assert CheckTypeGroup.objects.count() == 1
    assert CheckType.objects.count() == 2

    # new group
    CheckTypeGroup.objects.all().delete()
    resp = app.get('/manage/pricing/')
    resp = resp.click('Import')
    resp.form['config_json'] = Upload('export.json', group_export.encode('utf-8'), 'application/json')
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/check-types/')
    resp = resp.follow()
    assert 'A check type group has been created. No check type group updated.' not in resp.text
    assert CheckTypeGroup.objects.count() == 1
    assert CheckType.objects.count() == 2

    # multiple groups
    groups = json.loads(group_export)
    groups['check_type_groups'].append(copy.copy(groups['check_type_groups'][0]))
    groups['check_type_groups'].append(copy.copy(groups['check_type_groups'][0]))
    groups['check_type_groups'][1]['label'] = 'Foo bar 2'
    groups['check_type_groups'][1]['slug'] = 'foo-bar-2'
    groups['check_type_groups'][2]['label'] = 'Foo bar 3'
    groups['check_type_groups'][2]['slug'] = 'foo-bar-3'

    resp = app.get('/manage/pricing/')
    resp = resp.click('Import')
    resp.form['config_json'] = Upload('export.json', json.dumps(groups).encode('utf-8'), 'application/json')
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/')
    resp = resp.follow()
    assert '2 check type groups have been created. A check type group has been updated.' in resp.text
    assert CheckTypeGroup.objects.count() == 3
    assert CheckType.objects.count() == 6

    CheckTypeGroup.objects.all().delete()
    resp = app.get('/manage/pricing/')
    resp = resp.click('Import')
    resp.form['config_json'] = Upload('export.json', json.dumps(groups).encode('utf-8'), 'application/json')
    resp = resp.form.submit().follow()
    assert '3 check type groups have been created. No check type group updated.' in resp.text
    assert CheckTypeGroup.objects.count() == 3
    assert CheckType.objects.count() == 6


@pytest.mark.freeze_time('2021-07-08')
def test_import_pricing(app, admin_user):
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )

    app = login(app)
    resp = app.get('/manage/pricing/%s/export/' % pricing.pk)
    assert resp.headers['content-type'] == 'application/json'
    assert resp.headers['content-disposition'] == 'attachment; filename="export_pricing_foo_20210708.json"'
    pricing_export = resp.text

    # existing pricing
    resp = app.get('/manage/pricing/')
    resp = resp.click('Import')
    resp.form['config_json'] = Upload('export.json', pricing_export.encode('utf-8'), 'application/json')
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/%s/' % pricing.pk)
    resp = resp.follow()
    assert 'No pricing created. A pricing has been updated.' not in resp.text
    assert Pricing.objects.count() == 1

    # new pricing
    Pricing.objects.all().delete()
    resp = app.get('/manage/pricing/')
    resp = resp.click('Import')
    resp.form['config_json'] = Upload('export.json', pricing_export.encode('utf-8'), 'application/json')
    resp = resp.form.submit()
    pricing = Pricing.objects.latest('pk')
    assert resp.location.endswith('/manage/pricing/%s/' % pricing.pk)
    resp = resp.follow()
    assert 'A pricing has been created. No pricing updated.' not in resp.text
    assert Pricing.objects.count() == 1

    # multiple pricing
    pricings = json.loads(pricing_export)
    pricings['pricings'].append(copy.copy(pricings['pricings'][0]))
    pricings['pricings'].append(copy.copy(pricings['pricings'][0]))
    pricings['pricings'][1]['label'] = 'Foo bar 2'
    pricings['pricings'][1]['slug'] = 'foo-bar-2'
    pricings['pricings'][2]['label'] = 'Foo bar 3'
    pricings['pricings'][2]['slug'] = 'foo-bar-3'

    resp = app.get('/manage/pricing/')
    resp = resp.click('Import')
    resp.form['config_json'] = Upload('export.json', json.dumps(pricings).encode('utf-8'), 'application/json')
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/')
    resp = resp.follow()
    assert '2 pricings have been created. A pricing has been updated.' in resp.text
    assert Pricing.objects.count() == 3

    Pricing.objects.all().delete()
    resp = app.get('/manage/pricing/')
    resp = resp.click('Import')
    resp.form['config_json'] = Upload('export.json', json.dumps(pricings).encode('utf-8'), 'application/json')
    resp = resp.form.submit().follow()
    assert '3 pricings have been created. No pricing updated.' in resp.text
    assert Pricing.objects.count() == 3

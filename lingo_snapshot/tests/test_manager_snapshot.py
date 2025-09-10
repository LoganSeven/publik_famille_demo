import datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from lingo.agendas.models import Agenda, CheckTypeGroup
from lingo.invoicing.models import Regie
from lingo.pricing.models import CriteriaCategory, Pricing
from lingo.snapshot.models import (
    AgendaSnapshot,
    CheckTypeGroupSnapshot,
    CriteriaCategorySnapshot,
    PricingSnapshot,
    RegieSnapshot,
)
from tests.utils import login

pytestmark = pytest.mark.django_db


def test_agenda_history(settings, app, admin_user):
    regie = Regie.objects.create(label='Foo')
    agenda = Agenda.objects.create(slug='foo', label='Foo')
    snapshot1 = agenda.take_snapshot()
    agenda.regie = regie
    agenda.save()
    snapshot2 = agenda.take_snapshot()
    snapshot2.application_version = '42.0'
    snapshot2.save()
    assert AgendaSnapshot.objects.count() == 2

    app = login(app)
    resp = app.get('/manage/pricing/agenda/%s/' % agenda.pk)
    with CaptureQueriesContext(connection) as ctx:
        resp = resp.click('History')
        assert len(ctx.captured_queries) == 4
    assert [x.attrib['class'] for x in resp.pyquery.find('.snapshots-list tr')] == [
        'new-day',
        'collapsed',
    ]
    assert '(Version 42.0)' in resp.pyquery('tr:nth-child(1)').text()

    for mode in ['json', 'inspect', '']:
        resp = app.get(
            '/manage/pricing/agenda/%s/history/compare/?version1=%s&version2=%s&mode=%s'
            % (agenda.pk, snapshot1.pk, snapshot2.pk, mode)
        )
        assert 'Snapshot (%s)' % (snapshot1.pk) in resp
        assert 'Snapshot (%s) -  (Version 42.0)' % (snapshot2.pk) in resp
        if mode == 'inspect':
            assert resp.text.count('<ins>') == 1
            assert resp.text.count('<del>') == 0
        else:
            assert resp.text.count('diff_sub') == 1
            assert resp.text.count('diff_add') == 1
            assert resp.text.count('diff_chg') == 0
    resp = app.get(
        '/manage/pricing/agenda/%s/history/compare/?version1=%s&version2=%s'
        % (agenda.pk, snapshot2.pk, snapshot1.pk)
    )
    assert 'Snapshot (%s)' % (snapshot1.pk) in resp
    assert 'Snapshot (%s) -  (Version 42.0)' % (snapshot2.pk) in resp
    assert resp.text.count('diff_sub') == 1
    assert resp.text.count('diff_add') == 1
    assert resp.text.count('diff_chg') == 0


def test_check_type_group_history(settings, app, admin_user):
    group = CheckTypeGroup.objects.create(slug='foo', label='Foo')
    snapshot1 = group.take_snapshot()
    group.label = 'Foo Bar'
    group.save()
    snapshot2 = group.take_snapshot()
    snapshot2.application_version = '42.0'
    snapshot2.save()
    assert CheckTypeGroupSnapshot.objects.count() == 2

    app = login(app)
    resp = app.get('/manage/pricing/check-type/group/%s/history/' % group.pk)
    with CaptureQueriesContext(connection) as ctx:
        resp = resp.click('History')
        assert len(ctx.captured_queries) == 4
    assert [x.attrib['class'] for x in resp.pyquery.find('.snapshots-list tr')] == [
        'new-day',
        'collapsed',
    ]
    assert '(Version 42.0)' in resp.pyquery('tr:nth-child(1)').text()

    for mode in ['json', 'inspect', '']:
        resp = app.get(
            '/manage/pricing/check-type/group/%s/history/compare/?version1=%s&version2=%s&mode=%s'
            % (group.pk, snapshot1.pk, snapshot2.pk, mode)
        )
        assert 'Snapshot (%s)' % (snapshot1.pk) in resp
        assert 'Snapshot (%s) -  (Version 42.0)' % (snapshot2.pk) in resp
        if mode == 'inspect':
            assert resp.text.count('<ins>') == 1
            assert resp.text.count('<del>') == 0
        else:
            assert resp.text.count('diff_sub') == 0
            assert resp.text.count('diff_add') == 1
            assert resp.text.count('diff_chg') == 0
    resp = app.get(
        '/manage/pricing/check-type/group/%s/history/compare/?version1=%s&version2=%s'
        % (group.pk, snapshot2.pk, snapshot1.pk)
    )
    assert 'Snapshot (%s)' % (snapshot1.pk) in resp
    assert 'Snapshot (%s) -  (Version 42.0)' % (snapshot2.pk) in resp
    assert resp.text.count('diff_sub') == 0
    assert resp.text.count('diff_add') == 1
    assert resp.text.count('diff_chg') == 0


def test_pricing_history(settings, app, admin_user):
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
        flat_fee_schedule=True,
    )
    snapshot1 = pricing.take_snapshot()
    pricing.flat_fee_schedule = False
    pricing.save()
    snapshot2 = pricing.take_snapshot()
    snapshot2.application_version = '42.0'
    snapshot2.save()
    assert PricingSnapshot.objects.count() == 2

    app = login(app)
    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    with CaptureQueriesContext(connection) as ctx:
        resp = resp.click('History')
        assert len(ctx.captured_queries) == 5
    assert [x.attrib['class'] for x in resp.pyquery.find('.snapshots-list tr')] == [
        'new-day',
        'collapsed',
    ]
    assert '(Version 42.0)' in resp.pyquery('tr:nth-child(1)').text()

    for mode in ['json', 'inspect', '']:
        resp = app.get(
            '/manage/pricing/%s/history/compare/?version1=%s&version2=%s&mode=%s'
            % (pricing.pk, snapshot1.pk, snapshot2.pk, mode)
        )
        assert 'Snapshot (%s)' % (snapshot1.pk) in resp
        assert 'Snapshot (%s) -  (Version 42.0)' % (snapshot2.pk) in resp
        if mode == 'inspect':
            assert resp.text.count('<ins>') == 2
            assert resp.text.count('<del>') == 3
        else:
            assert resp.text.count('diff_sub') == 0
            assert resp.text.count('diff_add') == 0
            assert resp.text.count('diff_chg') == 2
    resp = app.get(
        '/manage/pricing/%s/history/compare/?version1=%s&version2=%s'
        % (pricing.pk, snapshot2.pk, snapshot1.pk)
    )
    assert 'Snapshot (%s)' % (snapshot1.pk) in resp
    assert 'Snapshot (%s) -  (Version 42.0)' % (snapshot2.pk) in resp
    assert resp.text.count('diff_sub') == 0
    assert resp.text.count('diff_add') == 0
    assert resp.text.count('diff_chg') == 2


def test_criteria_category_group_history(settings, app, admin_user):
    category = CriteriaCategory.objects.create(slug='foo', label='Foo')
    snapshot1 = category.take_snapshot()
    category.label = 'Foo Bar'
    category.save()
    snapshot2 = category.take_snapshot()
    snapshot2.application_version = '42.0'
    snapshot2.save()
    assert CriteriaCategorySnapshot.objects.count() == 2

    app = login(app)
    resp = app.get('/manage/pricing/criteria/category/%s/history/' % category.pk)
    with CaptureQueriesContext(connection) as ctx:
        resp = resp.click('History')
        assert len(ctx.captured_queries) == 4
    assert [x.attrib['class'] for x in resp.pyquery.find('.snapshots-list tr')] == [
        'new-day',
        'collapsed',
    ]
    assert '(Version 42.0)' in resp.pyquery('tr:nth-child(1)').text()

    for mode in ['json', 'inspect', '']:
        resp = app.get(
            '/manage/pricing/criteria/category/%s/history/compare/?version1=%s&version2=%s&mode=%s'
            % (category.pk, snapshot1.pk, snapshot2.pk, mode)
        )
        assert 'Snapshot (%s)' % (snapshot1.pk) in resp
        assert 'Snapshot (%s) -  (Version 42.0)' % (snapshot2.pk) in resp
        if mode == 'inspect':
            assert resp.text.count('<ins>') == 1
            assert resp.text.count('<del>') == 0
        else:
            assert resp.text.count('diff_sub') == 0
            assert resp.text.count('diff_add') == 1
            assert resp.text.count('diff_chg') == 0
    resp = app.get(
        '/manage/pricing/criteria/category/%s/history/compare/?version1=%s&version2=%s'
        % (category.pk, snapshot2.pk, snapshot1.pk)
    )
    assert 'Snapshot (%s)' % (snapshot1.pk) in resp
    assert 'Snapshot (%s) -  (Version 42.0)' % (snapshot2.pk) in resp
    assert resp.text.count('diff_sub') == 0
    assert resp.text.count('diff_add') == 1
    assert resp.text.count('diff_chg') == 0


def test_regie_history(settings, app, admin_user):
    regie = Regie.objects.create(slug='foo', label='Foo')
    snapshot1 = regie.take_snapshot()
    regie.description = 'foo bar'
    regie.save()
    snapshot2 = regie.take_snapshot()
    snapshot2.application_version = '42.0'
    snapshot2.save()
    assert RegieSnapshot.objects.count() == 2

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/parameters/' % regie.pk)
    with CaptureQueriesContext(connection) as ctx:
        resp = resp.click('History')
        assert len(ctx.captured_queries) == 5
    assert [x.attrib['class'] for x in resp.pyquery.find('.snapshots-list tr')] == [
        'new-day',
        'collapsed',
    ]
    assert '(Version 42.0)' in resp.pyquery('tr:nth-child(1)').text()

    for mode in ['json', 'inspect', '']:
        resp = app.get(
            '/manage/invoicing/regie/%s/history/compare/?version1=%s&version2=%s&mode=%s'
            % (regie.pk, snapshot1.pk, snapshot2.pk, mode)
        )
        assert 'Snapshot (%s)' % (snapshot1.pk) in resp
        assert 'Snapshot (%s) -  (Version 42.0)' % (snapshot2.pk) in resp
        if mode == 'inspect':
            assert resp.text.count('<ins>') == 2
            assert resp.text.count('<del>') == 0
        else:
            assert resp.text.count('diff_sub') == 1
            assert resp.text.count('diff_add') == 1
            assert resp.text.count('diff_chg') == 0
    resp = app.get(
        '/manage/invoicing/regie/%s/history/compare/?version1=%s&version2=%s'
        % (regie.pk, snapshot2.pk, snapshot1.pk)
    )
    assert 'Snapshot (%s)' % (snapshot1.pk) in resp
    assert 'Snapshot (%s) -  (Version 42.0)' % (snapshot2.pk) in resp
    assert resp.text.count('diff_sub') == 1
    assert resp.text.count('diff_add') == 1
    assert resp.text.count('diff_chg') == 0

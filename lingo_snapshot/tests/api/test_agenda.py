import datetime
from unittest import mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from lingo.agendas.models import Agenda, AgendaUnlockLog, CheckType, CheckTypeGroup
from lingo.invoicing.models import Campaign, Regie

pytestmark = pytest.mark.django_db


def test_agendas_check_types_api(app):
    agenda = Agenda.objects.create(label='Foo bar')
    group = CheckTypeGroup.objects.create(label='Foo bar')
    CheckType.objects.create(label='Foo reason', code='XX', group=group, kind='absence')
    CheckType.objects.create(label='Bar reason', group=group, kind='presence')
    check_type = CheckType.objects.create(label='Baz reason', group=group, kind='presence', disabled=True)
    group2 = CheckTypeGroup.objects.create(label='Foo bar 2')

    resp = app.get('/api/agenda/%s/check-types/' % agenda.slug)
    assert resp.json == {'data': [], 'err': 0}

    agenda.check_type_group = group2
    agenda.save()
    resp = app.get('/api/agenda/%s/check-types/' % agenda.slug)
    assert resp.json == {'data': [], 'err': 0}

    agenda.check_type_group = group
    agenda.save()

    with CaptureQueriesContext(connection) as ctx:
        resp = app.get('/api/agenda/%s/check-types/' % agenda.slug)
        assert len(ctx.captured_queries) == 4

    assert resp.json == {
        'data': [
            {
                'id': 'bar-reason',
                'kind': 'presence',
                'text': 'Bar reason',
                'code': '',
                'color': '#33CC33',
                'unexpected_presence': False,
                'unjustified_absence': False,
                'agendas': ['foo-bar'],
            },
            {
                'id': 'foo-reason',
                'kind': 'absence',
                'text': 'Foo reason',
                'code': 'XX',
                'color': '#33CC33',
                'unexpected_presence': False,
                'unjustified_absence': False,
                'agendas': ['foo-bar'],
            },
        ],
        'err': 0,
    }

    check_type.disabled = False
    check_type.save()
    group.unexpected_presence = check_type
    group.unjustified_absence = check_type
    group.save()
    resp = app.get('/api/agenda/%s/check-types/' % agenda.slug)
    assert resp.json == {
        'data': [
            {
                'id': 'bar-reason',
                'kind': 'presence',
                'text': 'Bar reason',
                'code': '',
                'color': '#33CC33',
                'unexpected_presence': False,
                'unjustified_absence': False,
                'agendas': ['foo-bar'],
            },
            {
                'id': 'baz-reason',
                'kind': 'presence',
                'text': 'Baz reason',
                'code': '',
                'color': '#33CC33',
                'unexpected_presence': True,
                'unjustified_absence': True,
                'agendas': ['foo-bar'],
            },
            {
                'id': 'foo-reason',
                'kind': 'absence',
                'text': 'Foo reason',
                'code': 'XX',
                'color': '#33CC33',
                'unexpected_presence': False,
                'unjustified_absence': False,
                'agendas': ['foo-bar'],
            },
        ],
        'err': 0,
    }

    # unknown
    resp = app.get('/api/agenda/xxxx/check-types/', status=404)


def test_agendas_multiple_check_types_api(app):
    group = CheckTypeGroup.objects.create(label='Foo bar')
    CheckType.objects.create(label='Foo reason', code='XX', group=group, kind='absence')
    CheckType.objects.create(label='Bar reason', group=group, kind='presence')

    Agenda.objects.create(label='Foo bar', check_type_group=group)
    Agenda.objects.create(label='Foo bar 2', check_type_group=group)

    group = CheckTypeGroup.objects.create(label='Foo bar 2')
    CheckType.objects.create(label='Foo reason', code='YY', group=group, kind='presence')

    with CaptureQueriesContext(connection) as ctx:
        resp = app.get('/api/agendas/check-types/?agendas=foo-bar,foo-bar-2')

        query_count = len(ctx.captured_queries)
        assert query_count == 3

    assert resp.json['data'] == [
        {
            'agendas': ['foo-bar', 'foo-bar-2'],
            'code': '',
            'color': '#33CC33',
            'id': 'bar-reason',
            'kind': 'presence',
            'text': 'Bar reason',
            'unexpected_presence': False,
            'unjustified_absence': False,
        },
        {
            'agendas': ['foo-bar', 'foo-bar-2'],
            'code': 'XX',
            'color': '#33CC33',
            'id': 'foo-reason',
            'kind': 'absence',
            'text': 'Foo reason',
            'unexpected_presence': False,
            'unjustified_absence': False,
        },
    ]

    # add one more agenda, with different group but check type with same slug
    Agenda.objects.create(label='Foo bar 3', check_type_group=group)

    group = CheckTypeGroup.objects.create(label='Foo bar 3')
    CheckType.objects.create(label='Foo reason', code='YY', group=group, kind='presence')

    with CaptureQueriesContext(connection) as ctx:
        new_resp = app.get('/api/agendas/check-types/?agendas=foo-bar,foo-bar-2,foo-bar-3')
        assert len(ctx.captured_queries) == query_count

    assert len(new_resp.json['data']) == 3
    assert new_resp.json['data'][:2] == resp.json['data']
    assert new_resp.json['data'][2] == {
        'agendas': ['foo-bar-3'],
        'code': 'YY',
        'color': '#33CC33',
        'id': 'foo-reason',
        'kind': 'presence',
        'text': 'Foo reason',
        'unexpected_presence': False,
        'unjustified_absence': False,
    }

    CheckType.objects.all().delete()

    resp = app.get('/api/agendas/check-types/?agendas=foo-bar,foo-bar-2,foo-bar-3')
    assert resp.json == {'data': [], 'err': 0}

    # unknown slug
    resp = app.get('/api/agendas/check-types/?agendas=foo-bar-42', status=400)
    assert resp.json['err'] == 1
    assert resp.json['errors']['agendas'][0] == 'unknown agendas: foo-bar-42'


def test_agendas_unlock(app, user):
    regie = Regie.objects.create(label='foo')
    agenda = Agenda.objects.create(label='Foo bar')
    Agenda.objects.create(label='Partial', partial_bookings=True)

    app.post('/api/agendas/unlock/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    resp = app.post('/api/agendas/unlock/', status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'agendas': ['This field is required.'],
        'date_start': ['This field is required.'],
        'date_end': ['This field is required.'],
    }

    params = {
        'agendas': 'foo-bar, partial',
        'date_start': '2025-01-01',
        'date_end': '2025-01-10',
    }
    resp = app.post('/api/agendas/unlock/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'agendas': ['unknown agendas: partial'],
    }

    params = {
        'agendas': 'foo-bar, foo-bar',
        'date_start': '2025-01-01',
        'date_end': '2025-01-10',
    }
    resp = app.post('/api/agendas/unlock/', params=params)
    assert resp.json == {'err': 0}
    assert AgendaUnlockLog.objects.count() == 0

    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2025, 9, 1),
        date_end=datetime.date(2025, 10, 1),
        date_publication=datetime.date(2025, 10, 1),
        date_payment_deadline=datetime.date(2025, 10, 31),
        date_due=datetime.date(2025, 10, 31),
        date_debit=datetime.date(2025, 10, 31),
        finalized=False,
    )
    campaign.agendas.add(agenda)
    resp = app.post('/api/agendas/unlock/', params=params)
    assert resp.json == {'err': 0}
    assert AgendaUnlockLog.objects.count() == 0

    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2024, 12, 31),
        date_end=datetime.date(2025, 1, 1),
        date_publication=datetime.date(2025, 10, 1),
        date_payment_deadline=datetime.date(2025, 10, 31),
        date_due=datetime.date(2025, 10, 31),
        date_debit=datetime.date(2025, 10, 31),
        finalized=False,
    )
    campaign.agendas.add(agenda)
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2025, 10, 1),
        date_end=datetime.date(2025, 11, 1),
        date_publication=datetime.date(2025, 10, 1),
        date_payment_deadline=datetime.date(2025, 10, 31),
        date_due=datetime.date(2025, 10, 31),
        date_debit=datetime.date(2025, 10, 31),
        finalized=False,
    )
    campaign.agendas.add(agenda)
    resp = app.post('/api/agendas/unlock/', params=params)
    assert resp.json == {'err': 0}
    assert AgendaUnlockLog.objects.count() == 0

    campaign1 = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2024, 12, 31),
        date_end=datetime.date(2025, 2, 1),
        date_publication=datetime.date(2025, 10, 1),
        date_payment_deadline=datetime.date(2025, 10, 31),
        date_due=datetime.date(2025, 10, 31),
        date_debit=datetime.date(2025, 10, 31),
        finalized=False,
    )
    campaign2 = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2025, 1, 1),
        date_end=datetime.date(2025, 2, 1),
        date_publication=datetime.date(2025, 10, 1),
        date_payment_deadline=datetime.date(2025, 10, 31),
        date_due=datetime.date(2025, 10, 31),
        date_debit=datetime.date(2025, 10, 31),
        finalized=False,
    )
    campaign3 = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2025, 1, 1),
        date_end=datetime.date(2025, 10, 1),
        date_publication=datetime.date(2025, 10, 1),
        date_payment_deadline=datetime.date(2025, 10, 31),
        date_due=datetime.date(2025, 10, 31),
        date_debit=datetime.date(2025, 10, 31),
        finalized=False,
    )
    campaign4 = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2025, 1, 9),
        date_end=datetime.date(2025, 1, 10),
        date_publication=datetime.date(2025, 10, 1),
        date_payment_deadline=datetime.date(2025, 10, 31),
        date_due=datetime.date(2025, 10, 31),
        date_debit=datetime.date(2025, 10, 31),
        finalized=False,
    )
    campaign5 = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2025, 1, 9),
        date_end=datetime.date(2025, 1, 11),
        date_publication=datetime.date(2025, 10, 1),
        date_payment_deadline=datetime.date(2025, 10, 31),
        date_due=datetime.date(2025, 10, 31),
        date_debit=datetime.date(2025, 10, 31),
        finalized=False,
    )
    resp = app.post('/api/agendas/unlock/', params=params)
    assert resp.json == {'err': 0}
    assert AgendaUnlockLog.objects.count() == 0

    campaign1.agendas.add(agenda)
    campaign2.agendas.add(agenda)
    campaign3.agendas.add(agenda)
    campaign4.agendas.add(agenda)
    campaign5.agendas.add(agenda)
    resp = app.post('/api/agendas/unlock/', params=params)
    assert resp.json == {'err': 0}
    assert AgendaUnlockLog.objects.count() == 5
    assert list(AgendaUnlockLog.objects.values_list('agenda', flat=True).distinct()) == [agenda.pk]
    assert list(
        AgendaUnlockLog.objects.values_list('campaign', flat=True).distinct().order_by('campaign')
    ) == [
        campaign1.pk,
        campaign2.pk,
        campaign3.pk,
        campaign4.pk,
        campaign5.pk,
    ]
    assert list(AgendaUnlockLog.objects.values_list('active', flat=True).distinct()) == [True]
    old_updated_at = list(AgendaUnlockLog.objects.values_list('updated_at', flat=True).order_by('created_at'))

    # again
    resp = app.post('/api/agendas/unlock/', params=params)
    assert resp.json == {'err': 0}
    assert AgendaUnlockLog.objects.count() == 5
    assert list(AgendaUnlockLog.objects.values_list('active', flat=True).distinct()) == [True]
    new_updated_at = AgendaUnlockLog.objects.values_list('updated_at', flat=True).order_by('created_at')
    for old_value, new_value in zip(old_updated_at, new_updated_at):
        assert old_value < new_value

    AgendaUnlockLog.objects.update(active=False)

    # again
    resp = app.post('/api/agendas/unlock/', params=params)
    assert resp.json == {'err': 0}
    assert AgendaUnlockLog.objects.count() == 10
    assert list(AgendaUnlockLog.objects.values_list('agenda', flat=True).distinct()) == [agenda.pk]
    assert list(
        AgendaUnlockLog.objects.values_list('campaign', flat=True).distinct().order_by('campaign')
    ) == [
        campaign1.pk,
        campaign2.pk,
        campaign3.pk,
        campaign4.pk,
        campaign5.pk,
    ]
    assert list(AgendaUnlockLog.objects.values_list('active', flat=True).distinct()) == [False, True]

    # not for corrective campaign
    campaign6 = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2025, 1, 9),
        date_end=datetime.date(2025, 1, 11),
        date_publication=datetime.date(2025, 10, 1),
        date_payment_deadline=datetime.date(2025, 10, 31),
        date_due=datetime.date(2025, 10, 31),
        date_debit=datetime.date(2025, 10, 31),
        finalized=False,
        primary_campaign=campaign1,
    )
    campaign6.agendas.add(agenda)
    resp = app.post('/api/agendas/unlock/', params=params)
    assert resp.json == {'err': 0}
    assert AgendaUnlockLog.objects.count() == 10
    assert list(AgendaUnlockLog.objects.values_list('agenda', flat=True).distinct()) == [agenda.pk]
    assert list(
        AgendaUnlockLog.objects.values_list('campaign', flat=True).distinct().order_by('campaign')
    ) == [
        campaign1.pk,
        campaign2.pk,
        campaign3.pk,
        campaign4.pk,
        campaign5.pk,
    ]
    assert list(AgendaUnlockLog.objects.values_list('active', flat=True).distinct()) == [False, True]


@mock.patch('lingo.agendas.chrono.collect_agenda_data')
def test_agendas_duplicate_settings_api(mock_collect, app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))
    agenda = Agenda.objects.create(label='Foo bar')
    group = CheckTypeGroup.objects.create(label='Foo bar')
    CheckType.objects.create(label='Foo reason', code='XX', group=group, kind='absence')
    CheckType.objects.create(label='Bar reason', group=group, kind='presence')
    regie = Regie.objects.create(label='foo')
    agenda.check_type_group = group
    agenda.regie = regie
    agenda.save()

    target_agenda = Agenda.objects.create(label='Target')

    app.post_json(
        '/api/agenda/%s/duplicate-settings/' % agenda.slug, params={'invalid': 'payload'}, status=400
    )

    resp = app.post_json(
        '/api/agenda/%s/duplicate-settings/' % agenda.slug, params={'target_agenda': target_agenda.slug}
    )
    assert resp.json == {'err': 0}
    target_agenda.refresh_from_db()
    assert target_agenda.check_type_group_id == group.id
    assert target_agenda.regie_id == regie.id

    mock_collect.return_value = [
        {'category_label': None, 'category_slug': None, 'label': 'Foo Bar', 'slug': 'foo-bar'},
        {
            'category_label': 'Foo',
            'category_slug': 'foo',
            'label': 'Events B',
            'slug': 'events-b',
            'partial_bookings': True,
        },
    ]

    # agenda that will appear via refresh_agendas
    resp = app.post_json(
        '/api/agenda/%s/duplicate-settings/' % agenda.slug, params={'target_agenda': 'events-b'}
    )
    assert mock_collect.mock_calls[0].kwargs == {'slug': 'events-b'}
    assert resp.json == {'err': 0}
    new_agenda = Agenda.objects.get(slug='events-b')
    assert new_agenda.check_type_group_id == group.id

    # unknown agenda
    resp = app.post_json(
        '/api/agenda/%s/duplicate-settings/' % agenda.slug, params={'target_agenda': 'unknown'}, status=400
    )
    assert resp.json == {'err': 1, 'err_class': 'unknown target agenda', 'err_desc': 'unknown target agenda'}

import datetime
import io
import json
import re
import tarfile
from unittest import mock

import pytest
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from requests.exceptions import ConnectionError
from requests.models import Response

from lingo.agendas.models import Agenda, CheckType, CheckTypeGroup
from lingo.export_import.models import Application, ApplicationElement
from lingo.invoicing.models import DraftInvoice, Invoice, Regie
from lingo.pricing.models import CriteriaCategory, Pricing
from tests.invoicing.utils import MockedRequestResponse, mocked_requests_send

pytestmark = pytest.mark.django_db


def test_object_types(app, admin_user):
    app.authorization = ('Basic', ('admin', 'admin'))
    resp = app.get('/api/export-import/')
    assert resp.json == {
        'data': [
            {
                'id': 'pricings',
                'singular': 'Pricing',
                'text': 'Pricings',
                'urls': {'list': 'http://testserver/api/export-import/pricings/'},
            },
            {
                'id': 'pricing_categories',
                'minor': True,
                'singular': 'Criteria category',
                'text': 'Criteria categories',
                'urls': {'list': 'http://testserver/api/export-import/pricing_categories/'},
            },
            {
                'id': 'lingo_agendas',
                'minor': True,
                'singular': 'Agenda (payment)',
                'text': 'Agendas (payment)',
                'urls': {'list': 'http://testserver/api/export-import/lingo_agendas/'},
            },
            {
                'id': 'check_type_groups',
                'minor': True,
                'singular': 'Check type group',
                'text': 'Check type groups',
                'urls': {'list': 'http://testserver/api/export-import/check_type_groups/'},
            },
            {
                'id': 'regies',
                'minor': True,
                'singular': 'Regie',
                'text': 'Regies',
                'urls': {'list': 'http://testserver/api/export-import/regies/'},
            },
            {
                'id': 'roles',
                'minor': True,
                'singular': 'Role',
                'text': 'Roles',
                'urls': {'list': 'http://testserver/api/export-import/roles/'},
            },
        ],
    }


def test_list(app, admin_user):
    app.authorization = ('Basic', ('admin', 'admin'))
    Pricing.objects.create(
        label='Foo Bar pricing',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )
    CriteriaCategory.objects.create(label='Foo Bar Cat')
    Agenda.objects.create(label='Foo Bar Agenda')
    CheckTypeGroup.objects.create(label='Foo Bar Group')
    Regie.objects.create(label='Foo Bar Regie')
    group = Group.objects.create(name='group1')
    resp = app.get('/api/export-import/pricings/')
    assert resp.json == {
        'data': [
            {
                'id': 'foo-bar-pricing',
                'text': 'Foo Bar pricing',
                'type': 'pricings',
                'urls': {
                    'dependencies': 'http://testserver/api/export-import/pricings/foo-bar-pricing/dependencies/',
                    'export': 'http://testserver/api/export-import/pricings/foo-bar-pricing/',
                    'redirect': 'http://testserver/api/export-import/pricings/foo-bar-pricing/redirect/',
                },
            }
        ]
    }
    resp = app.get('/api/export-import/pricing_categories/')
    assert resp.json == {
        'data': [
            {
                'id': 'foo-bar-cat',
                'text': 'Foo Bar Cat',
                'type': 'pricing_categories',
                'urls': {
                    'dependencies': 'http://testserver/api/export-import/pricing_categories/foo-bar-cat/dependencies/',
                    'export': 'http://testserver/api/export-import/pricing_categories/foo-bar-cat/',
                    'redirect': 'http://testserver/api/export-import/pricing_categories/foo-bar-cat/redirect/',
                },
            }
        ],
    }
    resp = app.get('/api/export-import/lingo_agendas/')
    assert resp.json == {
        'data': [
            {
                'id': 'foo-bar-agenda',
                'text': 'Foo Bar Agenda',
                'type': 'lingo_agendas',
                'urls': {
                    'dependencies': 'http://testserver/api/export-import/lingo_agendas/foo-bar-agenda/dependencies/',
                    'export': 'http://testserver/api/export-import/lingo_agendas/foo-bar-agenda/',
                    'redirect': 'http://testserver/api/export-import/lingo_agendas/foo-bar-agenda/redirect/',
                },
            }
        ]
    }
    resp = app.get('/api/export-import/check_type_groups/')
    assert resp.json == {
        'data': [
            {
                'id': 'foo-bar-group',
                'text': 'Foo Bar Group',
                'type': 'check_type_groups',
                'urls': {
                    'dependencies': 'http://testserver/api/export-import/check_type_groups/foo-bar-group/dependencies/',
                    'export': 'http://testserver/api/export-import/check_type_groups/foo-bar-group/',
                    'redirect': 'http://testserver/api/export-import/check_type_groups/foo-bar-group/redirect/',
                },
            }
        ]
    }
    resp = app.get('/api/export-import/regies/')
    assert resp.json == {
        'data': [
            {
                'id': 'foo-bar-regie',
                'text': 'Foo Bar Regie',
                'type': 'regies',
                'urls': {
                    'dependencies': 'http://testserver/api/export-import/regies/foo-bar-regie/dependencies/',
                    'export': 'http://testserver/api/export-import/regies/foo-bar-regie/',
                    'redirect': 'http://testserver/api/export-import/regies/foo-bar-regie/redirect/',
                },
            }
        ]
    }
    resp = app.get('/api/export-import/roles/')
    assert resp.json == {
        'data': [{'id': group.pk, 'text': 'group1', 'type': 'roles', 'urls': {}, 'uuid': None}]
    }

    # unknown component type
    app.get('/api/export-import/unknown/', status=404)


def test_export_pricing(app, admin_user):
    app.authorization = ('Basic', ('admin', 'admin'))
    Pricing.objects.create(
        label='Foo Bar pricing',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )
    resp = app.get('/api/export-import/pricings/foo-bar-pricing/')
    assert resp.json['data']['label'] == 'Foo Bar pricing'


def test_export_minor_components(app, admin_user):
    app.authorization = ('Basic', ('admin', 'admin'))
    CriteriaCategory.objects.create(label='Foo Bar Cat')
    Agenda.objects.create(label='Foo Bar Agenda')
    CheckTypeGroup.objects.create(label='Foo Bar Group')
    Regie.objects.create(label='Foo Bar Regie')

    resp = app.get('/api/export-import/pricing_categories/foo-bar-cat/')
    assert resp.json['data']['label'] == 'Foo Bar Cat'
    resp = app.get('/api/export-import/lingo_agendas/foo-bar-agenda/')
    assert resp.json['data']['slug'] == 'foo-bar-agenda'
    resp = app.get('/api/export-import/check_type_groups/foo-bar-group/')
    assert resp.json['data']['label'] == 'Foo Bar Group'
    resp = app.get('/api/export-import/regies/foo-bar-regie/')
    assert resp.json['data']['label'] == 'Foo Bar Regie'

    # unknown component
    app.get('/api/export-import/pricings/foo/', status=404)

    # unknown component type
    app.get('/api/export-import/unknown/foo/', status=404)


@mock.patch('requests.Session.send', side_effect=mocked_requests_send)
def test_pricing_dependencies(mock_send, app, admin_user):
    app.authorization = ('Basic', ('admin', 'admin'))
    group1 = Group.objects.create(name='group1')
    group2 = Group.objects.create(name='group2')
    pricing = Pricing.objects.create(
        label='Foo Bar pricing',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
        extra_variables={
            'foo': '{{ cards|objects:"card_model_1" }}',
            'bar': '{{ cards|objects:"card_model_2:custom-view" }}',
        },
        edit_role=group1,
        view_role=group2,
    )
    category1 = CriteriaCategory.objects.create(label='Cat 1')
    category2 = CriteriaCategory.objects.create(label='Cat 2')
    agenda1 = Agenda.objects.create(label='Foo bar 1')
    agenda2 = Agenda.objects.create(label='Foo bar 2')
    pricing.categories.add(category1, through_defaults={'order': 1})
    pricing.categories.add(category2, through_defaults={'order': 2})
    pricing.agendas.add(agenda1, agenda2)

    resp = app.get('/api/export-import/pricings/foo-bar-pricing/dependencies/')
    assert resp.json == {
        'data': [
            {'id': group1.pk, 'text': 'group1', 'type': 'roles', 'urls': {}, 'uuid': None},
            {'id': group2.pk, 'text': 'group2', 'type': 'roles', 'urls': {}, 'uuid': None},
            {
                'id': 'foo-bar-1',
                'text': 'Foo bar 1',
                'type': 'lingo_agendas',
                'urls': {
                    'dependencies': 'http://testserver/api/export-import/lingo_agendas/foo-bar-1/dependencies/',
                    'export': 'http://testserver/api/export-import/lingo_agendas/foo-bar-1/',
                    'redirect': 'http://testserver/api/export-import/lingo_agendas/foo-bar-1/redirect/',
                },
            },
            {
                'id': 'foo-bar-2',
                'text': 'Foo bar 2',
                'type': 'lingo_agendas',
                'urls': {
                    'dependencies': 'http://testserver/api/export-import/lingo_agendas/foo-bar-2/dependencies/',
                    'export': 'http://testserver/api/export-import/lingo_agendas/foo-bar-2/',
                    'redirect': 'http://testserver/api/export-import/lingo_agendas/foo-bar-2/redirect/',
                },
            },
            {
                'id': 'cat-1',
                'text': 'Cat 1',
                'type': 'pricing_categories',
                'urls': {
                    'dependencies': 'http://testserver/api/export-import/pricing_categories/cat-1/dependencies/',
                    'export': 'http://testserver/api/export-import/pricing_categories/cat-1/',
                    'redirect': 'http://testserver/api/export-import/pricing_categories/cat-1/redirect/',
                },
            },
            {
                'id': 'cat-2',
                'text': 'Cat 2',
                'type': 'pricing_categories',
                'urls': {
                    'dependencies': 'http://testserver/api/export-import/pricing_categories/cat-2/dependencies/',
                    'export': 'http://testserver/api/export-import/pricing_categories/cat-2/',
                    'redirect': 'http://testserver/api/export-import/pricing_categories/cat-2/redirect/',
                },
            },
            {
                'type': 'cards',
                'id': 'card_model_1',
                'text': 'Card Model 1',
                'urls': {
                    'export': 'http://wcs.example.org/api/export-import/cards/card_model_1/',
                    'dependencies': 'http://wcs.example.org/api/export-import/cards/card_model_1/dependencies/',
                    'redirect': 'http://wcs.example.org/api/export-import/cards/card_model_1/redirect/',
                },
            },
            {
                'type': 'cards',
                'id': 'card_model_2',
                'text': 'Card Model 2',
                'urls': {
                    'export': 'http://wcs.example.org/api/export-import/cards/card_model_2/',
                    'dependencies': 'http://wcs.example.org/api/export-import/cards/card_model_2/dependencies/',
                    'redirect': 'http://wcs.example.org/api/export-import/cards/card_model_2/redirect/',
                },
            },
        ],
        'err': 0,
    }

    pricing.extra_variables = {}
    pricing.reduction_rate = '{{ cards|objects:"card_model_1" }}'
    pricing.effort_rate_target = '{{ cards|objects:"card_model_2:custom-view" }}'
    pricing.accounting_code = '{{ cards|objects:"card_model_3" }}'
    pricing.save()
    resp = app.get('/api/export-import/pricings/foo-bar-pricing/dependencies/')
    assert {
        'type': 'cards',
        'id': 'card_model_1',
        'text': 'Card Model 1',
        'urls': {
            'export': 'http://wcs.example.org/api/export-import/cards/card_model_1/',
            'dependencies': 'http://wcs.example.org/api/export-import/cards/card_model_1/dependencies/',
            'redirect': 'http://wcs.example.org/api/export-import/cards/card_model_1/redirect/',
        },
    } not in resp.json['data']
    assert {
        'type': 'cards',
        'id': 'card_model_2',
        'text': 'Card Model 2',
        'urls': {
            'export': 'http://wcs.example.org/api/export-import/cards/card_model_2/',
            'dependencies': 'http://wcs.example.org/api/export-import/cards/card_model_2/dependencies/',
            'redirect': 'http://wcs.example.org/api/export-import/cards/card_model_2/redirect/',
        },
    } not in resp.json['data']
    assert {
        'type': 'cards',
        'id': 'card_model_3',
        'text': 'Card Model 3',
        'urls': {
            'export': 'http://wcs.example.org/api/export-import/cards/card_model_3/',
            'dependencies': 'http://wcs.example.org/api/export-import/cards/card_model_3/dependencies/',
            'redirect': 'http://wcs.example.org/api/export-import/cards/card_model_3/redirect/',
        },
    } in resp.json['data']

    pricing.kind = 'reduction'
    pricing.save()
    resp = app.get('/api/export-import/pricings/foo-bar-pricing/dependencies/')
    assert {
        'type': 'cards',
        'id': 'card_model_1',
        'text': 'Card Model 1',
        'urls': {
            'export': 'http://wcs.example.org/api/export-import/cards/card_model_1/',
            'dependencies': 'http://wcs.example.org/api/export-import/cards/card_model_1/dependencies/',
            'redirect': 'http://wcs.example.org/api/export-import/cards/card_model_1/redirect/',
        },
    } in resp.json['data']
    assert {
        'type': 'cards',
        'id': 'card_model_2',
        'text': 'Card Model 2',
        'urls': {
            'export': 'http://wcs.example.org/api/export-import/cards/card_model_2/',
            'dependencies': 'http://wcs.example.org/api/export-import/cards/card_model_2/dependencies/',
            'redirect': 'http://wcs.example.org/api/export-import/cards/card_model_2/redirect/',
        },
    } not in resp.json['data']

    pricing.kind = 'effort'
    pricing.save()
    resp = app.get('/api/export-import/pricings/foo-bar-pricing/dependencies/')
    assert {
        'type': 'cards',
        'id': 'card_model_1',
        'text': 'Card Model 1',
        'urls': {
            'export': 'http://wcs.example.org/api/export-import/cards/card_model_1/',
            'dependencies': 'http://wcs.example.org/api/export-import/cards/card_model_1/dependencies/',
            'redirect': 'http://wcs.example.org/api/export-import/cards/card_model_1/redirect/',
        },
    } not in resp.json['data']
    assert {
        'type': 'cards',
        'id': 'card_model_2',
        'text': 'Card Model 2',
        'urls': {
            'export': 'http://wcs.example.org/api/export-import/cards/card_model_2/',
            'dependencies': 'http://wcs.example.org/api/export-import/cards/card_model_2/dependencies/',
            'redirect': 'http://wcs.example.org/api/export-import/cards/card_model_2/redirect/',
        },
    } in resp.json['data']

    with mock.patch('requests.Session.get') as requests_get:
        requests_get.side_effect = ConnectionError()
        resp = app.get('/api/export-import/pricings/foo-bar-pricing/dependencies/', status=400)
        assert resp.json['err'] == 1
        assert resp.json['err_desc'] == 'Unable to get WCS service (request-error)'

    with mock.patch('requests.Session.get') as requests_get:
        mock_resp = Response()
        mock_resp.status_code = 500
        requests_get.return_value = mock_resp
        resp = app.get('/api/export-import/pricings/foo-bar-pricing/dependencies/', status=400)
        assert resp.json['err'] == 1
        assert resp.json['err_desc'] == 'Unable to get WCS service (request-error-status-500)'

    with mock.patch('requests.Session.get') as requests_get:
        mock_resp = Response()
        mock_resp.status_code = 404
        requests_get.return_value = mock_resp
        resp = app.get('/api/export-import/pricings/foo-bar-pricing/dependencies/', status=400)
        assert resp.json['err'] == 1
        assert resp.json['err_desc'] == 'Unable to get WCS service (request-error-status-404)'

    with mock.patch('requests.Session.get') as requests_get:
        requests_get.return_value = MockedRequestResponse(content=json.dumps({'foo': 'bar'}))
        resp = app.get('/api/export-import/pricings/foo-bar-pricing/dependencies/', status=400)
        assert resp.json['err'] == 1
        assert resp.json['err_desc'] == 'Unable to get WCS data'

    data = {'data': []}
    with mock.patch('requests.Session.get') as requests_get:
        requests_get.return_value = MockedRequestResponse(content=json.dumps(data))
        resp = app.get('/api/export-import/pricings/foo-bar-pricing/dependencies/', status=400)
        assert resp.json['err'] == 1
        assert resp.json['err_desc'] == 'Unable to get WCS data'


def test_agenda_dependencies(app, admin_user):
    app.authorization = ('Basic', ('admin', 'admin'))
    regie = Regie.objects.create(label='Foo Bar Regie')
    group = CheckTypeGroup.objects.create(label='Foo Bar Group')
    Agenda.objects.create(label='Foo Bar Agenda', check_type_group=group, regie=regie)
    resp = app.get('/api/export-import/lingo_agendas/foo-bar-agenda/dependencies/')
    assert resp.json == {
        'data': [
            {
                'id': 'foo-bar-group',
                'text': 'Foo Bar Group',
                'type': 'check_type_groups',
                'urls': {
                    'dependencies': 'http://testserver/api/export-import/check_type_groups/foo-bar-group/dependencies/',
                    'export': 'http://testserver/api/export-import/check_type_groups/foo-bar-group/',
                    'redirect': 'http://testserver/api/export-import/check_type_groups/foo-bar-group/redirect/',
                },
            },
            {
                'id': 'foo-bar-regie',
                'text': 'Foo Bar Regie',
                'type': 'regies',
                'urls': {
                    'dependencies': 'http://testserver/api/export-import/regies/foo-bar-regie/dependencies/',
                    'export': 'http://testserver/api/export-import/regies/foo-bar-regie/',
                    'redirect': 'http://testserver/api/export-import/regies/foo-bar-regie/redirect/',
                },
            },
            {
                'id': 'foo-bar-agenda',
                'text': 'Foo Bar Agenda',
                'type': 'agendas',
                'urls': {
                    'dependencies': 'http://chrono.example.org/api/export-import/agendas/foo-bar-agenda/dependencies/',
                    'export': 'http://chrono.example.org/api/export-import/agendas/foo-bar-agenda/',
                    'redirect': 'http://chrono.example.org/api/export-import/agendas/foo-bar-agenda/redirect/',
                },
            },
        ],
        'err': 0,
    }


def test_check_type_group_dependencies(app, admin_user):
    app.authorization = ('Basic', ('admin', 'admin'))
    group = CheckTypeGroup.objects.create(label='Foo Bar Group')
    CheckType.objects.create(label='Foo reason', group=group, kind='presence')
    CheckType.objects.create(label='Baz reason', group=group)
    resp = app.get('/api/export-import/check_type_groups/foo-bar-group/dependencies/')
    assert resp.json == {
        'data': [],
        'err': 0,
    }


@mock.patch('requests.Session.send', side_effect=mocked_requests_send)
def test_regie_dependencies(mock_send, app, admin_user):
    app.authorization = ('Basic', ('admin', 'admin'))
    group1 = Group.objects.create(name='group1')
    group2 = Group.objects.create(name='group2')
    group3 = Group.objects.create(name='group3')
    group4 = Group.objects.create(name='group4')
    regie = Regie.objects.create(
        label='Foo Bar Regie',
        edit_role=group1,
        view_role=group2,
        invoice_role=group3,
        control_role=group4,
    )
    resp = app.get('/api/export-import/regies/foo-bar-regie/dependencies/')
    assert resp.json == {
        'data': [
            {'id': group1.pk, 'text': 'group1', 'type': 'roles', 'urls': {}, 'uuid': None},
            {'id': group2.pk, 'text': 'group2', 'type': 'roles', 'urls': {}, 'uuid': None},
            {'id': group3.pk, 'text': 'group3', 'type': 'roles', 'urls': {}, 'uuid': None},
            {'id': group4.pk, 'text': 'group4', 'type': 'roles', 'urls': {}, 'uuid': None},
        ],
        'err': 0,
    }

    regie.payer_carddef_reference = 'default:card_model_1'
    regie.payer_cached_carddef_json = {'name': 'Card Model 1'}
    regie.save()
    resp = app.get('/api/export-import/regies/foo-bar-regie/dependencies/')
    assert {
        'type': 'cards',
        'id': 'card_model_1',
        'text': 'Card Model 1',
        'urls': {
            'export': 'http://wcs.example.org/api/export-import/cards/card_model_1/',
            'dependencies': 'http://wcs.example.org/api/export-import/cards/card_model_1/dependencies/',
            'redirect': 'http://wcs.example.org/api/export-import/cards/card_model_1/redirect/',
        },
    } in resp.json['data']

    regie.payer_carddef_reference = 'default:card_model_1:custom-view'
    regie.save()
    resp = app.get('/api/export-import/regies/foo-bar-regie/dependencies/')
    assert {
        'type': 'cards',
        'id': 'card_model_1',
        'text': 'Card Model 1',
        'urls': {
            'export': 'http://wcs.example.org/api/export-import/cards/card_model_1/',
            'dependencies': 'http://wcs.example.org/api/export-import/cards/card_model_1/dependencies/',
            'redirect': 'http://wcs.example.org/api/export-import/cards/card_model_1/redirect/',
        },
    } in resp.json['data']

    regie.payer_carddef_reference = ''
    regie.payer_external_id_template = '{{ cards|objects:"card_model_2" }}'
    regie.save()
    resp = app.get('/api/export-import/regies/foo-bar-regie/dependencies/')
    assert {
        'type': 'cards',
        'id': 'card_model_2',
        'text': 'Card Model 2',
        'urls': {
            'export': 'http://wcs.example.org/api/export-import/cards/card_model_2/',
            'dependencies': 'http://wcs.example.org/api/export-import/cards/card_model_2/dependencies/',
            'redirect': 'http://wcs.example.org/api/export-import/cards/card_model_2/redirect/',
        },
    } in resp.json['data']

    regie.payer_external_id_template = ''
    regie.payer_external_id_from_nameid_template = '{{ cards|objects:"card_model_2" }}'
    regie.save()
    resp = app.get('/api/export-import/regies/foo-bar-regie/dependencies/')
    assert {
        'type': 'cards',
        'id': 'card_model_2',
        'text': 'Card Model 2',
        'urls': {
            'export': 'http://wcs.example.org/api/export-import/cards/card_model_2/',
            'dependencies': 'http://wcs.example.org/api/export-import/cards/card_model_2/dependencies/',
            'redirect': 'http://wcs.example.org/api/export-import/cards/card_model_2/redirect/',
        },
    } in resp.json['data']


def test_pricing_categories_dependencies(app, admin_user):
    app.authorization = ('Basic', ('admin', 'admin'))
    CriteriaCategory.objects.create(label='Foo Bar Cat')
    resp = app.get('/api/export-import/pricing_categories/foo-bar-cat/dependencies/')
    assert resp.json == {
        'data': [],
        'err': 0,
    }


def test_unknown_compoment_dependencies(app, admin_user):
    app.authorization = ('Basic', ('admin', 'admin'))
    app.get('/api/export-import/pricings/foo/dependencies/', status=404)


def test_unknown_compoment_type_dependencies(app, admin_user):
    app.authorization = ('Basic', ('admin', 'admin'))
    app.get('/api/export-import/unknown/foo/dependencies/', status=404)


def test_redirect(app):
    pricing = Pricing.objects.create(
        label='Foo Bar pricing',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )
    category = CriteriaCategory.objects.create(label='Foo Bar Cat')
    agenda = Agenda.objects.create(label='Foo Bar Agenda')
    group = CheckTypeGroup.objects.create(label='Foo Bar Group')
    regie = Regie.objects.create(label='Foo Bar Regie')

    redirect_url = f'/api/export-import/pricings/{pricing.slug}/redirect/'
    resp = app.get(redirect_url, status=302)
    assert resp.location == f'/manage/pricing/{pricing.pk}/'

    redirect_url = f'/api/export-import/pricing_categories/{category.slug}/redirect/'
    resp = app.get(redirect_url, status=302)
    assert resp.location == '/manage/pricing/criterias/'

    redirect_url = f'/api/export-import/lingo_agendas/{agenda.slug}/redirect/'
    resp = app.get(redirect_url, status=302)
    assert resp.location == f'/manage/pricing/agenda/{agenda.pk}/'

    redirect_url = f'/api/export-import/check_type_groups/{group.slug}/redirect/'
    resp = app.get(redirect_url, status=302)
    assert resp.location == '/manage/pricing/check-types/'

    redirect_url = f'/api/export-import/regies/{regie.slug}/redirect/'
    resp = app.get(redirect_url, status=302)
    assert resp.location == f'/manage/invoicing/regie/{regie.pk}/'

    # unknown component type
    app.get('/api/export-import/unknown/foo/redirect/', status=404)


def create_bundle(app, admin_user, visible=True, version_number='42.0'):
    app.authorization = ('Basic', ('admin', 'admin'))

    group, _ = CheckTypeGroup.objects.get_or_create(label='Foo Bar Group')
    regie, _ = Regie.objects.get_or_create(label='Foo Bar Regie')
    pricing, _ = Pricing.objects.get_or_create(
        label='Foo Bar pricing',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )
    category, _ = CriteriaCategory.objects.get_or_create(label='Foo Bar Cat')
    agenda, _ = Agenda.objects.get_or_create(label='Foo Bar Agenda', check_type_group=group, regie=regie)
    pricing.categories.add(category, through_defaults={'order': 1})
    pricing.agendas.add(agenda)

    components = [
        (pricing, False),
        (category, True),
        (agenda, True),
        (group, True),
        (regie, True),
    ]

    tar_io = io.BytesIO()
    with tarfile.open(mode='w', fileobj=tar_io) as tar:
        manifest_json = {
            'application': 'Test',
            'slug': 'test',
            'icon': 'foo.png',
            'description': 'Foo Bar',
            'documentation_url': 'http://foo.bar',
            'visible': visible,
            'version_number': version_number,
            'version_notes': 'foo bar blah',
            'elements': [],
        }
        for component, auto_dependency in components:
            manifest_json['elements'].append(
                {
                    'type': component.application_component_type,
                    'slug': component.slug,
                    'name': component.label,
                    'auto-dependency': auto_dependency,
                }
            )
        manifest_fd = io.BytesIO(json.dumps(manifest_json, indent=2).encode())
        tarinfo = tarfile.TarInfo('manifest.json')
        tarinfo.size = len(manifest_fd.getvalue())
        tar.addfile(tarinfo, fileobj=manifest_fd)

        icon_fd = io.BytesIO(
            b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABAQAAAAA3bvkkAAAACklEQVQI12NoAAAAggCB3UNq9AAAAABJRU5ErkJggg=='
        )
        tarinfo = tarfile.TarInfo('foo.png')
        tarinfo.size = len(icon_fd.getvalue())
        tar.addfile(tarinfo, fileobj=icon_fd)

        for component, _ in components:
            component_export = app.get(
                '/api/export-import/%s/%s/' % (component.application_component_type, component.slug)
            ).content
            tarinfo = tarfile.TarInfo('%s/%s' % (component.application_component_type, component.slug))
            tarinfo.size = len(component_export)
            tar.addfile(tarinfo, fileobj=io.BytesIO(component_export))
    bundle = tar_io.getvalue()
    return bundle


@pytest.fixture
def bundle(app, admin_user):
    return create_bundle(app, admin_user)


@mock.patch('lingo.export_import.api_views.refresh_agendas')
def test_bundle_import(mock_refresh, app, admin_user):
    app.authorization = ('Basic', ('admin', 'admin'))

    bundles = []
    for version_number in ['42.0', '42.1']:
        bundles.append(create_bundle(app, admin_user, version_number=version_number))

    Pricing.objects.all().delete()
    CriteriaCategory.objects.all().delete()
    CheckTypeGroup.objects.all().delete()
    Regie.objects.all().delete()
    Agenda.objects.all().delete()
    agenda = Agenda.objects.create(label='Foo Bar Agenda')  # created by agenda refresh

    resp = app.post('/api/export-import/bundle-import/', upload_files=[('bundle', 'bundle.tar', bundles[0])])
    assert Pricing.objects.all().count() == 1
    assert resp.json['err'] == 0
    assert Application.objects.count() == 1
    application = Application.objects.latest('pk')
    assert application.slug == 'test'
    assert application.name == 'Test'
    assert application.description == 'Foo Bar'
    assert application.documentation_url == 'http://foo.bar'
    assert application.version_number == '42.0'
    assert application.version_notes == 'foo bar blah'
    assert re.match(r'applications/icons/foo(_\w+)?.png', application.icon.name)
    assert application.editable is False
    assert application.visible is True
    assert ApplicationElement.objects.count() == 5
    assert mock_refresh.call_args_list == [mock.call()]
    for model in [Pricing, CriteriaCategory, CheckTypeGroup, Regie, Agenda]:
        for instance in model.objects.all():
            last_snapshot = model.get_snapshot_model().objects.filter(instance=instance).latest('pk')
            assert last_snapshot.comment == 'Application (Test)'
            assert last_snapshot.application_slug == 'test'
            assert last_snapshot.application_version == '42.0'

    regie = Regie.objects.latest('pk')
    group = CheckTypeGroup.objects.latest('pk')
    agenda.refresh_from_db()
    assert agenda.regie == regie
    assert agenda.check_type_group == group
    category = CriteriaCategory.objects.latest('pk')
    last_pricing = Pricing.objects.latest('pk')
    assert list(last_pricing.agendas.all()) == [agenda]
    assert list(last_pricing.categories.all()) == [category]

    # check editable flag is kept on install
    application.editable = True
    application.save()

    # create link to element not present in manifest: it should be unlinked
    last_pricing = Pricing.objects.latest('pk')
    ApplicationElement.objects.create(
        application=application,
        content_type=ContentType.objects.get_for_model(Pricing),
        object_id=last_pricing.pk + 1,
    )

    # check update
    resp = app.post('/api/export-import/bundle-import/', upload_files=[('bundle', 'bundle.tar', bundles[1])])
    assert Pricing.objects.all().count() == 1
    assert resp.json['err'] == 0
    assert Application.objects.count() == 1
    application = Application.objects.latest('pk')
    assert application.editable is False
    assert ApplicationElement.objects.count() == 5
    assert (
        ApplicationElement.objects.filter(
            application=application,
            content_type=ContentType.objects.get_for_model(Pricing),
            object_id=last_pricing.pk + 1,
        ).exists()
        is False
    )
    for model in [Pricing, CriteriaCategory, CheckTypeGroup, Regie, Agenda]:
        for instance in model.objects.all():
            last_snapshot = model.get_snapshot_model().objects.filter(instance=instance).latest('pk')
            assert last_snapshot.comment == 'Application (Test)'
            assert last_snapshot.application_slug == 'test'
            assert last_snapshot.application_version == '42.1'

    # bad file format
    resp = app.post(
        '/api/export-import/bundle-import/', upload_files=[('bundle', 'bundle.tar', b'garbage')], status=400
    )
    assert resp.json['err']
    assert resp.json['err_desc'] == 'Invalid tar file'

    # missing manifest
    tar_io = io.BytesIO()
    with tarfile.open(mode='w', fileobj=tar_io) as tar:
        foo_fd = io.BytesIO(json.dumps({'foo': 'bar'}, indent=2).encode())
        tarinfo = tarfile.TarInfo('foo.json')
        tarinfo.size = len(foo_fd.getvalue())
        tar.addfile(tarinfo, fileobj=foo_fd)
    resp = app.post(
        '/api/export-import/bundle-import/',
        upload_files=[('bundle', 'bundle.tar', tar_io.getvalue())],
        status=400,
    )
    assert resp.json['err']
    assert resp.json['err_desc'] == 'Invalid tar file, missing manifest'

    # missing component
    tar_io = io.BytesIO()
    with tarfile.open(mode='w', fileobj=tar_io) as tar:
        manifest_json = {
            'application': 'Test',
            'slug': 'test',
            'elements': [{'type': 'pricings', 'slug': 'foo', 'name': 'foo'}],
        }
        manifest_fd = io.BytesIO(json.dumps(manifest_json, indent=2).encode())
        tarinfo = tarfile.TarInfo('manifest.json')
        tarinfo.size = len(manifest_fd.getvalue())
        tar.addfile(tarinfo, fileobj=manifest_fd)
    resp = app.post(
        '/api/export-import/bundle-import/',
        upload_files=[('bundle', 'bundle.tar', tar_io.getvalue())],
        status=400,
    )
    assert resp.json['err']
    assert resp.json['err_desc'] == 'Invalid tar file, missing component pricings/foo'


def test_bundle_declare(app, admin_user):
    app.authorization = ('Basic', ('admin', 'admin'))

    bundle = create_bundle(app, admin_user, visible=False)
    resp = app.post('/api/export-import/bundle-declare/', upload_files=[('bundle', 'bundle.tar', bundle)])
    assert Pricing.objects.all().count() == 1
    assert resp.json['err'] == 0
    assert Application.objects.count() == 1
    application = Application.objects.latest('pk')
    assert application.slug == 'test'
    assert application.name == 'Test'
    assert application.description == 'Foo Bar'
    assert application.documentation_url == 'http://foo.bar'
    assert application.version_number == '42.0'
    assert application.version_notes == 'foo bar blah'
    assert re.match(r'applications/icons/foo(_\w+)?.png', application.icon.name)
    assert application.editable is True
    assert application.visible is False
    assert ApplicationElement.objects.count() == 5

    bundle = create_bundle(app, admin_user, visible=True)
    # create link to element not present in manifest: it should be unlinked
    last_pricing = Pricing.objects.latest('pk')
    ApplicationElement.objects.create(
        application=application,
        content_type=ContentType.objects.get_for_model(Pricing),
        object_id=last_pricing.pk + 1,
    )
    # and remove regie to have unkown references in manifest
    Regie.objects.all().delete()

    resp = app.post('/api/export-import/bundle-declare/', upload_files=[('bundle', 'bundle.tar', bundle)])
    assert Application.objects.count() == 1
    application = Application.objects.latest('pk')
    assert application.visible is True
    assert ApplicationElement.objects.count() == 4  # pricing, categorie, agenda, group

    # bad file format
    resp = app.post(
        '/api/export-import/bundle-declare/', upload_files=[('bundle', 'bundle.tar', b'garbage')], status=400
    )
    assert resp.json['err']
    assert resp.json['err_desc'] == 'Invalid tar file'

    # missing manifest
    tar_io = io.BytesIO()
    with tarfile.open(mode='w', fileobj=tar_io) as tar:
        foo_fd = io.BytesIO(json.dumps({'foo': 'bar'}, indent=2).encode())
        tarinfo = tarfile.TarInfo('foo.json')
        tarinfo.size = len(foo_fd.getvalue())
        tar.addfile(tarinfo, fileobj=foo_fd)
    resp = app.post(
        '/api/export-import/bundle-declare/',
        upload_files=[('bundle', 'bundle.tar', tar_io.getvalue())],
        status=400,
    )
    assert resp.json['err']
    assert resp.json['err_desc'] == 'Invalid tar file, missing manifest'

    # missing component
    tar_io = io.BytesIO()
    with tarfile.open(mode='w', fileobj=tar_io) as tar:
        manifest_json = {
            'application': 'Test',
            'slug': 'test',
            'elements': [{'type': 'pricings', 'slug': 'foo', 'name': 'foo'}],
        }
        manifest_fd = io.BytesIO(json.dumps(manifest_json, indent=2).encode())
        tarinfo = tarfile.TarInfo('manifest.json')
        tarinfo.size = len(manifest_fd.getvalue())
        tar.addfile(tarinfo, fileobj=manifest_fd)
    resp = app.post(
        '/api/export-import/bundle-declare/',
        upload_files=[('bundle', 'bundle.tar', tar_io.getvalue())],
        status=400,
    )
    assert resp.json['err']
    assert resp.json['err_desc'] == 'Invalid tar file, missing component pricings/foo'


def test_bundle_unlink(app, admin_user, bundle):
    app.authorization = ('Basic', ('admin', 'admin'))

    application = Application.objects.create(
        name='Test',
        slug='test',
        version_number='42.0',
    )
    other_application = Application.objects.create(
        name='Other Test',
        slug='other-test',
        version_number='42.0',
    )
    pricing = Pricing.objects.latest('pk')
    ApplicationElement.objects.create(
        application=application,
        content_object=pricing,
    )
    ApplicationElement.objects.create(
        application=application,
        content_type=ContentType.objects.get_for_model(Pricing),
        object_id=pricing.pk + 1,
    )
    ApplicationElement.objects.create(
        application=other_application,
        content_object=pricing,
    )
    ApplicationElement.objects.create(
        application=other_application,
        content_type=ContentType.objects.get_for_model(Pricing),
        object_id=pricing.pk + 1,
    )

    assert Application.objects.count() == 2
    assert ApplicationElement.objects.count() == 4
    app.post('/api/export-import/unlink/', {'application': 'test'})
    assert Application.objects.count() == 1
    assert ApplicationElement.objects.count() == 2
    assert ApplicationElement.objects.filter(
        application=other_application,
        content_type=ContentType.objects.get_for_model(Pricing),
        object_id=pricing.pk,
    ).exists()
    assert ApplicationElement.objects.filter(
        application=other_application,
        content_type=ContentType.objects.get_for_model(Pricing),
        object_id=pricing.pk + 1,
    ).exists()

    # again
    app.post('/api/export-import/unlink/', {'application': 'test'})
    assert Application.objects.count() == 1
    assert ApplicationElement.objects.count() == 2


def test_bundle_check(app, admin_user):
    app.authorization = ('Basic', ('admin', 'admin'))
    assert app.post('/api/export-import/bundle-check/').json == {'err': 0, 'data': {}}


def test_uninstall(app, admin_user):
    pricing1 = Pricing.objects.create(
        label='Pricing1',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )
    pricing2 = Pricing.objects.create(
        label='Pricing2',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )
    pricing3 = Pricing.objects.create(
        label='Pricing3',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )
    application = Application.objects.create(name='Test', slug='test', version_number='42.0')
    ApplicationElement.objects.create(application=application, content_object=pricing1)
    ApplicationElement.objects.create(application=application, content_object=pricing2)
    ApplicationElement.objects.create(application=application, content_object=pricing3)

    regie = Regie.objects.create(label='Foo Bar Regie')
    ApplicationElement.objects.create(application=application, content_object=regie)

    # delete pricing2, ApplicationElement will point to an empty object
    pricing2.delete()

    # add pricing3 to a second app
    application2 = Application.objects.create(name='Test2', slug='test2', version_number='42.0')
    ApplicationElement.objects.create(application=application2, content_object=pricing3)

    app.post('/api/export-import/uninstall-check/', {'application': 'test'}, status=(401, 403))
    app.post('/api/export-import/uninstall/', {'application': 'test'}, status=(401, 403))

    app.authorization = ('Basic', (admin_user.username, admin_user.username))
    resp = app.post('/api/export-import/uninstall-check/', {'application': 'test'})
    assert resp.json == {'err': 0}
    assert Regie.objects.all().count() == 1

    resp = app.post('/api/export-import/uninstall-check/', {'application': 'missing'})
    assert resp.json == {'err': 0}

    Invoice.objects.create(
        regie=regie,
        label='invoice1',
        date_due=datetime.date(2025, 8, 1),
        date_publication=datetime.date(2025, 8, 1),
        date_payment_deadline=datetime.date(2025, 8, 1),
    )
    resp = app.post('/api/export-import/uninstall-check/', {'application': 'test'})
    assert resp.json == {'err': 1, 'err_desc': 'Regie (foo-bar-regie) referenced in "invoices"'}

    resp = app.post('/api/export-import/uninstall/', {'application': 'missing'})
    assert resp.json == {'err': 0}

    Invoice.objects.all().delete()

    # check fallback on database constraints
    DraftInvoice.objects.create(
        regie=regie,
        label='invoice1',
        date_due=datetime.date(2025, 8, 1),
        date_publication=datetime.date(2025, 8, 1),
        date_payment_deadline=datetime.date(2025, 8, 1),
    )
    resp = app.post('/api/export-import/uninstall-check/', {'application': 'test'})
    assert resp.json == {'err': 1, 'err_desc': 'Existing data'}

    DraftInvoice.objects.all().delete()

    resp = app.post('/api/export-import/uninstall/', {'application': 'test'})
    assert resp.json == {'err': 0}
    assert {x.label for x in Pricing.objects.all()} == {'Pricing3'}
    assert {x.slug for x in Application.objects.all()} == {'test2'}

    # check objects used in multiple apps are not considered in check install
    application = Application.objects.create(name='Test', slug='test', version_number='42.0')
    regie = Regie.objects.create(label='Foo Bar Regie')
    ApplicationElement.objects.create(application=application, content_object=regie)
    ApplicationElement.objects.create(application=application2, content_object=regie)

    Invoice.objects.create(
        regie=regie,
        label='invoice1',
        date_due=datetime.date(2025, 8, 1),
        date_publication=datetime.date(2025, 8, 1),
        date_payment_deadline=datetime.date(2025, 8, 1),
    )

    resp = app.post('/api/export-import/uninstall-check/', {'application': 'test'})
    assert resp.json == {'err': 0}

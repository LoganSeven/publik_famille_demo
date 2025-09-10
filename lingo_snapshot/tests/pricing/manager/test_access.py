import datetime

import pytest

from lingo.agendas.models import Agenda
from lingo.pricing.models import Criteria, CriteriaCategory, Pricing
from tests.utils import login

pytestmark = pytest.mark.django_db


def test_manager_as_nothing(app, manager_user):
    agenda = Agenda.objects.create(label='Foo bar')
    category1 = CriteriaCategory.objects.create(label='Cat 1')
    pricing = Pricing.objects.create(
        kind='reduction',
        label='Foo Bar',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )
    pricing.agendas.add(agenda)
    pricing.categories.add(category1, through_defaults={'order': 1})
    billing_date = pricing.billingdates.create(
        date_start=datetime.date(year=2021, month=8, day=31),
        label='Foo',
    )

    app = login(app, username='manager', password='manager')

    app.get('/manage/', status=403)
    app.get('/manage/inspect/', status=403)
    app.post('/manage/inspect/test-template/', status=403)
    resp = app.get('/manage/pricing/')
    assert list(resp.context['object_list']) == []
    assert '/manage/pricing/add/' not in resp
    assert '/manage/pricing/import/' not in resp
    assert '/manage/pricing/export/' not in resp
    assert '/manage/pricing/%s/' % pricing.pk not in resp
    assert '/manage/pricing/criterias/' not in resp
    assert '/manage/pricing/agendas/' not in resp
    assert '/manage/pricing/check-types/' not in resp

    app.get('/manage/pricing/add/', status=403)
    app.get('/manage/pricing/import/', status=403)
    app.get('/manage/pricing/export/', status=403)
    app.get('/manage/pricing/%s/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/parameters/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/edit/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/delete/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/export/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/duplicate/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/variable/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/permissions/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/category/add/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/category/%s/edit/' % (pricing.pk, category1.pk), status=403)
    app.get('/manage/pricing/%s/category/%s/delete/' % (pricing.pk, category1.pk), status=403)
    app.get('/manage/pricing/%s/order/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/agenda/add/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/agenda/%s/delete/' % (pricing.pk, agenda.pk), status=403)
    app.get('/manage/pricing/%s/matrix/edit/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/inspect/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/history/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/history/compare/' % pricing.pk, status=403)

    Criteria.objects.create(label='Crit 1-1', slug='crit-1-1', category=category1, order=1)
    Criteria.objects.create(label='Crit 1-2', slug='crit-1-2', category=category1, order=2)
    category2 = CriteriaCategory.objects.create(label='Cat 2')
    category3 = CriteriaCategory.objects.create(label='Cat 3')
    pricing.categories.add(category2, through_defaults={'order': 2})
    pricing.categories.add(category3, through_defaults={'order': 3})
    pricing.criterias.set(Criteria.objects.all())
    app.get('/manage/pricing/%s/matrix/crit-1-1/edit/' % pricing.pk, status=403)

    app.get('/manage/pricing/%s/matrix/edit/min/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/matrix/crit-1-1/edit/min/' % pricing.pk, status=403)

    pricing.kind = 'effort'
    pricing.save()
    app.get('/manage/pricing/%s/pricing-options/' % pricing.pk, status=403)

    pricing.flat_fee_schedule = True
    pricing.save()
    app.get('/manage/pricing/%s/billing-date/add/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/billing-date/%s/' % (pricing.pk, billing_date.pk), status=403)
    app.get('/manage/pricing/%s/billing-date/%s/delete/' % (pricing.pk, billing_date.pk), status=403)

    app.get('/manage/pricing/criterias/', status=403)
    app.get('/manage/pricing/criteria/category/add/', status=403)
    app.get('/manage/pricing/criteria/category/0/edit/', status=403)
    app.get('/manage/pricing/criteria/category/0/delete/', status=403)
    app.get('/manage/pricing/criteria/category/0/export/', status=403)
    app.get('/manage/pricing/criteria/category/0/order/', status=403)
    app.get('/manage/pricing/criteria/category/0/add/', status=403)
    app.get('/manage/pricing/criteria/category/0/0/edit/', status=403)
    app.get('/manage/pricing/criteria/category/0/0/delete/', status=403)
    app.get('/manage/pricing/criteria/category/0/inspect/', status=403)
    app.get('/manage/pricing/criteria/category/0/history/', status=403)
    app.get('/manage/pricing/criteria/category/0/history/compare/', status=403)

    app.get('/manage/pricing/agendas/', status=403)
    app.get('/manage/pricing/agendas/archived/', status=403)
    app.get('/manage/pricing/agendas/sync/', status=403)
    app.get('/manage/pricing/agenda/0/', status=403)
    app.get('/manage/pricing/agenda/0/export/', status=403)
    app.get('/manage/pricing/agenda/0/check-options/', status=403)
    app.get('/manage/pricing/agenda/0/invoicing-options/', status=403)
    app.get('/manage/pricing/agenda/0/inspect/', status=403)
    app.get('/manage/pricing/agenda/0/history/', status=403)
    app.get('/manage/pricing/agenda/0/history/compare/', status=403)

    app.get('/manage/pricing/check-types/', status=403)
    app.get('/manage/pricing/check-type/group/add/', status=403)
    app.get('/manage/pricing/check-type/group/0/edit/', status=403)
    app.get('/manage/pricing/check-type/group/0/unexpected-presence/', status=403)
    app.get('/manage/pricing/check-type/group/0/unjustified-absence/', status=403)
    app.get('/manage/pricing/check-type/group/0/delete/', status=403)
    app.get('/manage/pricing/check-type/group/0/export/', status=403)
    app.get('/manage/pricing/check-type/group/0/add/', status=403)
    app.get('/manage/pricing/check-type/group/0/0/edit/', status=403)
    app.get('/manage/pricing/check-type/group/0/0/delete/', status=403)
    app.get('/manage/pricing/check-type/group/0/inspect/', status=403)
    app.get('/manage/pricing/check-type/group/0/history/', status=403)
    app.get('/manage/pricing/check-type/group/0/history/compare/', status=403)


def test_manager_as_viewer(app, manager_user):
    agenda = Agenda.objects.create(label='Foo bar')
    category1 = CriteriaCategory.objects.create(label='Cat 1')
    pricing = Pricing.objects.create(
        kind='reduction',
        label='Foo Bar',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
        view_role=manager_user.groups.first(),
    )
    pricing.agendas.add(agenda)
    pricing.categories.add(category1, through_defaults={'order': 1})
    billing_date = pricing.billingdates.create(
        date_start=datetime.date(year=2021, month=8, day=31),
        label='Foo',
    )
    pricing2 = Pricing.objects.create(
        kind='reduction',
        label='Foo Bar',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )

    app = login(app, username='manager', password='manager')

    resp = app.get('/manage/')
    app.get('/manage/inspect/', status=403)
    app.post('/manage/inspect/test-template/', status=403)
    assert '/manage/pricing/' in resp
    resp = app.get('/manage/pricing/')
    assert list(resp.context['object_list']) == [pricing]
    assert '/manage/pricing/add/' not in resp
    assert '/manage/pricing/import/' not in resp
    assert '/manage/pricing/export/' not in resp
    assert '/manage/pricing/%s/' % pricing.pk in resp
    assert '/manage/pricing/%s/' % pricing2.pk not in resp
    assert '/manage/pricing/criterias/' not in resp
    assert '/manage/pricing/agendas/' not in resp
    assert '/manage/pricing/check-types/' not in resp

    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    assert '/manage/pricing/%s/parameters/' % pricing.pk in resp
    assert '/manage/pricing/%s/matrix/edit/' % pricing.pk not in resp
    assert '/manage/pricing/%s/matrix/edit/min/' % pricing.pk not in resp
    app.get('/manage/pricing/%s/matrix/edit/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/matrix/edit/min/' % pricing.pk, status=403)

    Criteria.objects.create(label='Crit 1-1', slug='crit-1-1', category=category1, order=1)
    Criteria.objects.create(label='Crit 1-2', slug='crit-1-2', category=category1, order=2)
    category2 = CriteriaCategory.objects.create(label='Cat 2')
    category3 = CriteriaCategory.objects.create(label='Cat 3')
    pricing.categories.add(category2, through_defaults={'order': 2})
    pricing.categories.add(category3, through_defaults={'order': 3})
    pricing.criterias.set(Criteria.objects.all())
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    assert '/manage/pricing/%s/matrix/crit-1-1/edit/' % pricing.pk not in resp
    assert '/manage/pricing/%s/matrix/crit-1-1/edit/min/' % pricing.pk not in resp
    app.get('/manage/pricing/%s/matrix/crit-1-1/edit/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/matrix/crit-1-1/edit/min/' % pricing.pk, status=403)

    pricing.categories.remove(category3)
    pricing.kind = 'effort'
    pricing.save()
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    assert '/manage/pricing/%s/pricing-options/' % pricing.pk not in resp
    app.get('/manage/pricing/%s/pricing-options/' % pricing.pk, status=403)

    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    assert '/manage/pricing/%s/edit/' % pricing.pk not in resp
    assert '/manage/pricing/%s/delete/' % pricing.pk not in resp
    assert '/manage/pricing/%s/export/' % pricing.pk not in resp
    assert '/manage/pricing/%s/duplicate/' % pricing.pk not in resp
    assert '/manage/pricing/%s/variable/' % pricing.pk not in resp
    assert '/manage/pricing/%s/permissions/' % pricing.pk not in resp
    assert '/manage/pricing/%s/category/add/' % pricing.pk not in resp
    assert '/manage/pricing/%s/category/%s/edit/' % (pricing.pk, category1.pk) not in resp
    assert '/manage/pricing/%s/category/%s/delete/' % (pricing.pk, category1.pk) not in resp
    assert '/manage/pricing/%s/order/' % pricing.pk not in resp
    assert '/manage/pricing/%s/agenda/add/' % pricing.pk not in resp
    assert '/manage/pricing/%s/agenda/%s/delete/' % (pricing.pk, agenda.pk) not in resp
    assert '/manage/pricing/%s/inspect/' % pricing.pk not in resp
    assert '/manage/pricing/%s/history/' % pricing.pk not in resp
    app.get('/manage/pricing/%s/edit/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/delete/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/export/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/duplicate/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/variable/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/permissions/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/category/add/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/category/%s/edit/' % (pricing.pk, category1.pk), status=403)
    app.get('/manage/pricing/%s/category/%s/delete/' % (pricing.pk, category1.pk), status=403)
    app.get('/manage/pricing/%s/order/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/agenda/add/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/agenda/%s/delete/' % (pricing.pk, agenda.pk), status=403)
    app.get('/manage/pricing/%s/inspect/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/history/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/history/compare/' % pricing.pk, status=403)

    pricing.flat_fee_schedule = True
    pricing.save()
    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    assert '/manage/pricing/%s/billing-date/add/' % pricing.pk not in resp
    assert '/manage/pricing/%s/billing-date/%s/' % (pricing.pk, billing_date.pk) not in resp
    assert '/manage/pricing/%s/billing-date/%s/delete/' % (pricing.pk, billing_date.pk) not in resp
    app.get('/manage/pricing/%s/billing-date/add/' % pricing.pk, status=403)
    app.get('/manage/pricing/%s/billing-date/%s/' % (pricing.pk, billing_date.pk), status=403)
    app.get('/manage/pricing/%s/billing-date/%s/delete/' % (pricing.pk, billing_date.pk), status=403)

    app.get('/manage/pricing/add/', status=403)
    app.get('/manage/pricing/import/', status=403)
    app.get('/manage/pricing/export/', status=403)

    app.get('/manage/pricing/criterias/', status=403)
    app.get('/manage/pricing/criteria/category/add/', status=403)
    app.get('/manage/pricing/criteria/category/0/edit/', status=403)
    app.get('/manage/pricing/criteria/category/0/delete/', status=403)
    app.get('/manage/pricing/criteria/category/0/export/', status=403)
    app.get('/manage/pricing/criteria/category/0/order/', status=403)
    app.get('/manage/pricing/criteria/category/0/add/', status=403)
    app.get('/manage/pricing/criteria/category/0/0/edit/', status=403)
    app.get('/manage/pricing/criteria/category/0/0/delete/', status=403)
    app.get('/manage/pricing/criteria/category/0/inspect/', status=403)
    app.get('/manage/pricing/criteria/category/0/history/', status=403)
    app.get('/manage/pricing/criteria/category/0/history/compare/', status=403)

    app.get('/manage/pricing/agendas/', status=403)
    app.get('/manage/pricing/agendas/archived/', status=403)
    app.get('/manage/pricing/agendas/sync/', status=403)
    app.get('/manage/pricing/agenda/0/', status=403)
    app.get('/manage/pricing/agenda/0/export/', status=403)
    app.get('/manage/pricing/agenda/0/check-options/', status=403)
    app.get('/manage/pricing/agenda/0/invoicing-options/', status=403)
    app.get('/manage/pricing/agenda/0/inspect/', status=403)
    app.get('/manage/pricing/agenda/0/history/', status=403)
    app.get('/manage/pricing/agenda/0/history/compare/', status=403)

    app.get('/manage/pricing/check-types/', status=403)
    app.get('/manage/pricing/check-type/group/add/', status=403)
    app.get('/manage/pricing/check-type/group/0/edit/', status=403)
    app.get('/manage/pricing/check-type/group/0/unexpected-presence/', status=403)
    app.get('/manage/pricing/check-type/group/0/unjustified-absence/', status=403)
    app.get('/manage/pricing/check-type/group/0/delete/', status=403)
    app.get('/manage/pricing/check-type/group/0/export/', status=403)
    app.get('/manage/pricing/check-type/group/0/add/', status=403)
    app.get('/manage/pricing/check-type/group/0/0/edit/', status=403)
    app.get('/manage/pricing/check-type/group/0/0/delete/', status=403)
    app.get('/manage/pricing/check-type/group/0/inspect/', status=403)
    app.get('/manage/pricing/check-type/group/0/history/', status=403)
    app.get('/manage/pricing/check-type/group/0/history/compare/', status=403)


def test_manager_as_editer(app, manager_user):
    agenda = Agenda.objects.create(label='Foo bar')
    category1 = CriteriaCategory.objects.create(label='Cat 1')
    pricing = Pricing.objects.create(
        kind='reduction',
        label='Foo Bar',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
        edit_role=manager_user.groups.first(),
    )
    pricing.agendas.add(agenda)
    pricing.categories.add(category1, through_defaults={'order': 1})
    billing_date = pricing.billingdates.create(
        date_start=datetime.date(year=2021, month=8, day=31),
        label='Foo',
    )
    pricing2 = Pricing.objects.create(
        kind='reduction',
        label='Foo Bar',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )

    app = login(app, username='manager', password='manager')

    resp = app.get('/manage/')
    app.get('/manage/inspect/')
    token = resp.context['csrf_token']
    app.post('/manage/inspect/test-template/', params={'csrfmiddlewaretoken': token, 'django_template': ''})
    assert '/manage/pricing/' in resp
    resp = app.get('/manage/pricing/')
    assert list(resp.context['object_list']) == [pricing]
    assert '/manage/pricing/add/' not in resp
    assert '/manage/pricing/import/' not in resp
    assert '/manage/pricing/export/' not in resp
    assert '/manage/pricing/%s/' % pricing.pk in resp
    assert '/manage/pricing/%s/' % pricing2.pk not in resp
    assert '/manage/pricing/criterias/' not in resp
    assert '/manage/pricing/agendas/' not in resp
    assert '/manage/pricing/check-types/' not in resp

    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    assert '/manage/pricing/%s/parameters/' % pricing.pk in resp
    assert '/manage/pricing/%s/matrix/edit/' % pricing.pk in resp
    assert '/manage/pricing/%s/matrix/edit/min/' % pricing.pk in resp
    app.get('/manage/pricing/%s/matrix/edit/' % pricing.pk)
    app.get('/manage/pricing/%s/matrix/edit/min/' % pricing.pk)

    Criteria.objects.create(label='Crit 1-1', slug='crit-1-1', category=category1, order=1)
    Criteria.objects.create(label='Crit 1-2', slug='crit-1-2', category=category1, order=2)
    category2 = CriteriaCategory.objects.create(label='Cat 2')
    category3 = CriteriaCategory.objects.create(label='Cat 3')
    pricing.categories.add(category2, through_defaults={'order': 2})
    pricing.categories.add(category3, through_defaults={'order': 3})
    pricing.criterias.set(Criteria.objects.all())
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    assert '/manage/pricing/%s/matrix/crit-1-1/edit/' % pricing.pk in resp
    assert '/manage/pricing/%s/matrix/crit-1-1/edit/min/' % pricing.pk in resp
    app.get('/manage/pricing/%s/matrix/crit-1-1/edit/' % pricing.pk)
    app.get('/manage/pricing/%s/matrix/crit-1-1/edit/min/' % pricing.pk)

    pricing.categories.remove(category3)
    pricing.kind = 'effort'
    pricing.save()
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    assert '/manage/pricing/%s/pricing-options/' % pricing.pk in resp
    app.get('/manage/pricing/%s/pricing-options/' % pricing.pk)

    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    assert '/manage/pricing/%s/edit/' % pricing.pk in resp
    assert '/manage/pricing/%s/delete/' % pricing.pk in resp
    assert '/manage/pricing/%s/export/' % pricing.pk in resp
    assert '/manage/pricing/%s/duplicate/' % pricing.pk in resp
    assert '/manage/pricing/%s/variable/' % pricing.pk in resp
    assert '/manage/pricing/%s/permissions/' % pricing.pk in resp
    assert '/manage/pricing/%s/category/%s/edit/' % (pricing.pk, category1.pk) in resp
    assert '/manage/pricing/%s/category/%s/delete/' % (pricing.pk, category1.pk) in resp
    assert '/manage/pricing/%s/category/add/' % pricing.pk in resp
    assert '/manage/pricing/%s/order/' % pricing.pk in resp
    assert '/manage/pricing/%s/agenda/add/' % pricing.pk in resp
    assert '/manage/pricing/%s/agenda/%s/delete/' % (pricing.pk, agenda.pk) in resp
    assert '/manage/pricing/%s/inspect/' % pricing.pk in resp
    assert '/manage/pricing/%s/history/' % pricing.pk in resp
    app.get('/manage/pricing/%s/edit/' % pricing.pk)
    app.get('/manage/pricing/%s/delete/' % pricing.pk)
    app.get('/manage/pricing/%s/export/' % pricing.pk)
    app.get('/manage/pricing/%s/duplicate/' % pricing.pk)
    app.get('/manage/pricing/%s/variable/' % pricing.pk)
    app.get('/manage/pricing/%s/permissions/' % pricing.pk)
    app.get('/manage/pricing/%s/category/add/' % pricing.pk)
    app.get('/manage/pricing/%s/category/%s/edit/' % (pricing.pk, category1.pk))
    app.get('/manage/pricing/%s/category/%s/delete/' % (pricing.pk, category1.pk))
    app.get('/manage/pricing/%s/order/' % pricing.pk, status=400)
    app.get('/manage/pricing/%s/agenda/add/' % pricing.pk)
    app.get('/manage/pricing/%s/agenda/%s/delete/' % (pricing.pk, agenda.pk))
    app.get('/manage/pricing/%s/inspect/' % pricing.pk)
    app.get('/manage/pricing/%s/history/' % pricing.pk)
    app.get('/manage/pricing/%s/history/compare/' % pricing.pk, status=404)

    pricing.flat_fee_schedule = True
    pricing.save()
    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    assert '/manage/pricing/%s/billing-date/add/' % pricing.pk in resp
    assert '/manage/pricing/%s/billing-date/%s/' % (pricing.pk, billing_date.pk) in resp
    assert '/manage/pricing/%s/billing-date/%s/delete/' % (pricing.pk, billing_date.pk) in resp
    app.get('/manage/pricing/%s/billing-date/add/' % pricing.pk)
    app.get('/manage/pricing/%s/billing-date/%s/' % (pricing.pk, billing_date.pk))
    app.get('/manage/pricing/%s/billing-date/%s/delete/' % (pricing.pk, billing_date.pk))

    app.get('/manage/pricing/add/', status=403)
    app.get('/manage/pricing/import/', status=403)
    app.get('/manage/pricing/export/', status=403)

    app.get('/manage/pricing/criterias/', status=403)
    app.get('/manage/pricing/criteria/category/add/', status=403)
    app.get('/manage/pricing/criteria/category/0/edit/', status=403)
    app.get('/manage/pricing/criteria/category/0/delete/', status=403)
    app.get('/manage/pricing/criteria/category/0/export/', status=403)
    app.get('/manage/pricing/criteria/category/0/order/', status=403)
    app.get('/manage/pricing/criteria/category/0/add/', status=403)
    app.get('/manage/pricing/criteria/category/0/0/edit/', status=403)
    app.get('/manage/pricing/criteria/category/0/0/delete/', status=403)
    app.get('/manage/pricing/criteria/category/0/inspect/', status=403)
    app.get('/manage/pricing/criteria/category/0/history/', status=403)
    app.get('/manage/pricing/criteria/category/0/history/compare/', status=403)

    app.get('/manage/pricing/agendas/', status=403)
    app.get('/manage/pricing/agendas/archived/', status=403)
    app.get('/manage/pricing/agendas/sync/', status=403)
    app.get('/manage/pricing/agenda/0/', status=403)
    app.get('/manage/pricing/agenda/0/export/', status=403)
    app.get('/manage/pricing/agenda/0/check-options/', status=403)
    app.get('/manage/pricing/agenda/0/invoicing-options/', status=403)
    app.get('/manage/pricing/agenda/0/inspect/', status=403)
    app.get('/manage/pricing/agenda/0/history/', status=403)
    app.get('/manage/pricing/agenda/0/history/compare/', status=403)

    app.get('/manage/pricing/check-types/', status=403)
    app.get('/manage/pricing/check-type/group/add/', status=403)
    app.get('/manage/pricing/check-type/group/0/edit/', status=403)
    app.get('/manage/pricing/check-type/group/0/unexpected-presence/', status=403)
    app.get('/manage/pricing/check-type/group/0/unjustified-absence/', status=403)
    app.get('/manage/pricing/check-type/group/0/delete/', status=403)
    app.get('/manage/pricing/check-type/group/0/export/', status=403)
    app.get('/manage/pricing/check-type/group/0/add/', status=403)
    app.get('/manage/pricing/check-type/group/0/0/edit/', status=403)
    app.get('/manage/pricing/check-type/group/0/0/delete/', status=403)
    app.get('/manage/pricing/check-type/group/0/inspect/', status=403)
    app.get('/manage/pricing/check-type/group/0/history/', status=403)
    app.get('/manage/pricing/check-type/group/0/history/compare/', status=403)

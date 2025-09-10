import datetime
from decimal import Decimal
from unittest import mock

import pytest
from django.contrib.auth.models import Group
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils.timezone import now

from lingo.agendas.chrono import ChronoError
from lingo.agendas.models import Agenda, CheckType, CheckTypeGroup
from lingo.pricing.errors import PricingDataError
from lingo.pricing.models import BillingDate, Criteria, CriteriaCategory, Pricing, PricingCriteriaCategory
from lingo.snapshot.models import PricingSnapshot
from tests.utils import login

pytestmark = pytest.mark.django_db


def test_add_pricing(app, admin_user):
    app = login(app)
    resp = app.get('/manage/')
    resp = resp.click(href='/manage/pricing/')
    resp = resp.click('New pricing')
    # first pricing, starts on today
    assert resp.form['date_start'].value == now().strftime('%Y-%m-%d')
    resp.form['label'] = 'Pricing for lunch'
    resp.form['date_start'] = '2021-09-01'
    resp.form['date_end'] = '2021-09-01'
    resp.form['flat_fee_schedule'] = False
    resp.form['subscription_required'] = False
    resp = resp.form.submit()
    assert resp.context['form'].errors['date_end'] == ['End date must be greater than start date.']
    resp.form['date_end'] = '2022-09-01'
    resp = resp.form.submit()
    pricing = Pricing.objects.latest('pk')
    assert resp.location.endswith('/manage/pricing/%s/' % pricing.pk)
    assert pricing.label == 'Pricing for lunch'
    assert pricing.slug == 'pricing-for-lunch'
    assert list(pricing.agendas.all()) == []
    assert pricing.date_start == datetime.date(2021, 9, 1)
    assert pricing.date_end == datetime.date(2022, 9, 1)
    assert pricing.flat_fee_schedule is False
    assert pricing.subscription_required is True
    assert PricingSnapshot.objects.count() == 1

    resp = app.get('/manage/pricing/add/')
    # starts on last date_end
    assert resp.form['date_start'].value == '2022-09-01'
    resp.form['label'] = 'Pricing for lunch'
    resp.form['date_end'] = '2023-09-01'
    resp.form['flat_fee_schedule'] = True
    resp.form['subscription_required'] = False
    resp.form.submit()
    pricing = Pricing.objects.latest('pk')
    assert pricing.flat_fee_schedule is True
    assert pricing.subscription_required is False


def test_edit_pricing(app, admin_user):
    pricing = Pricing.objects.create(
        label='Foo Bar',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )
    pricing2 = Pricing.objects.create(
        label='Foo Baz',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )

    app = login(app)
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    resp = resp.click(href='/manage/pricing/%s/parameters/' % pricing.pk)
    resp = resp.click(href='/manage/pricing/%s/edit/' % pricing.pk)
    assert resp.form['date_start'].value == '2021-09-01'
    resp.form['label'] = 'Foo Baz'
    resp.form['slug'] = pricing2.slug
    resp.form['date_start'] = '2021-09-01'
    resp.form['date_end'] = '2021-09-01'
    resp.form['flat_fee_schedule'] = False
    resp.form['subscription_required'] = False
    resp = resp.form.submit()
    assert resp.context['form'].errors['slug'] == ['Another pricing exists with the same identifier.']
    assert resp.context['form'].errors['date_end'] == ['End date must be greater than start date.']
    resp.form['slug'] = 'foo-bazz'
    resp.form['date_start'] = '2021-08-01'
    resp.form['date_end'] = '2022-09-01'
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/%s/parameters/' % pricing.pk)
    pricing.refresh_from_db()
    assert pricing.slug == 'foo-bazz'
    assert pricing.date_start == datetime.date(2021, 8, 1)
    assert pricing.date_end == datetime.date(2022, 9, 1)
    assert pricing.flat_fee_schedule is False
    assert pricing.subscription_required is True
    assert pricing.kind == 'basic'
    assert pricing.reduction_rate == ''
    assert pricing.effort_rate_target == ''
    assert pricing.accounting_code == ''
    assert PricingSnapshot.objects.count() == 1

    resp = app.get('/manage/pricing/%s/edit/' % pricing.pk)
    resp.form['kind'] = 'reduction'
    resp.form['accounting_code'] = '424242'
    resp = resp.form.submit()
    assert resp.context['form'].errors['reduction_rate'] == [
        'Declare the reduction rate you want to apply for this pricing.'
    ]
    resp.form['reduction_rate'] = 'foo'
    resp.form['flat_fee_schedule'] = True
    resp.form['subscription_required'] = False
    resp.form.submit()
    pricing.refresh_from_db()
    assert pricing.flat_fee_schedule is True
    assert pricing.subscription_required is False
    assert pricing.kind == 'reduction'
    assert pricing.reduction_rate == 'foo'
    assert pricing.effort_rate_target == ''
    assert pricing.accounting_code == '424242'

    resp = app.get('/manage/pricing/%s/edit/' % pricing.pk)
    resp.form['kind'] = 'effort'
    resp = resp.form.submit()
    assert resp.context['form'].errors['effort_rate_target'] == [
        'Declare the amount you want to multiply by the effort rate for this pricing.'
    ]
    resp.form['effort_rate_target'] = 'foo'
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/%s/parameters/' % pricing.pk)
    pricing.refresh_from_db()
    assert pricing.kind == 'effort'
    assert pricing.reduction_rate == ''
    assert pricing.effort_rate_target == 'foo'
    assert pricing.accounting_code == '424242'

    agenda = Agenda.objects.create(label='Foo bar')
    pricing.agendas.add(agenda)
    pricing.subscription_required = True
    pricing.save()
    resp = app.get('/manage/pricing/%s/edit/' % pricing.pk)
    resp.form['subscription_required'] = False
    resp = resp.form.submit()
    assert resp.context['form'].errors['subscription_required'] == [
        'Some agendas are linked to this pricing; please unlink them first.'
    ]
    pricing.subscription_required = False
    pricing.save()
    resp = app.get('/manage/pricing/%s/edit/' % pricing.pk)
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/%s/parameters/' % pricing.pk)

    pricing.flat_fee_schedule = True
    pricing.save()
    pricing.billingdates.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        label='Foo',
    )
    resp = app.get('/manage/pricing/%s/edit/' % pricing.pk)
    resp.form['flat_fee_schedule'] = False
    resp = resp.form.submit()
    assert resp.context['form'].errors['flat_fee_schedule'] == [
        'Some billing dates are are defined for this pricing; please delete them first.'
    ]


def test_edit_pricing_overlapping_flat_fee_schedule(app, admin_user):
    agenda1 = Agenda.objects.create(label='Foo bar 1')
    agenda2 = Agenda.objects.create(label='Foo bar 2')
    pricing1 = Pricing.objects.create(
        label='Foo Bar',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )
    pricing1.agendas.add(agenda1, agenda2)
    pricing2 = Pricing.objects.create(
        label='Foo Bar',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )
    pricing2.agendas.add(agenda1, agenda2)

    app = login(app)
    resp = app.get('/manage/pricing/%s/edit/' % pricing1.pk)
    # ok, flat_fee_schedule has not changed
    resp.form.submit().follow()

    resp = app.get('/manage/pricing/%s/edit/' % pricing1.pk)
    # ok, flat_fee_schedule is different
    resp.form['flat_fee_schedule'] = True
    resp.form.submit().follow()

    resp = app.get('/manage/pricing/%s/edit/' % pricing1.pk)
    resp.form['flat_fee_schedule'] = False
    resp = resp.form.submit()
    assert resp.context['form'].non_field_errors() == [
        'Agenda "Foo bar 1" has already a pricing overlapping this period.',
        'Agenda "Foo bar 2" has already a pricing overlapping this period.',
    ]
    pricing2.agendas.remove(agenda1)
    resp = resp.form.submit()
    assert resp.context['form'].non_field_errors() == [
        'Agenda "Foo bar 2" has already a pricing overlapping this period.'
    ]
    pricing2.agendas.remove(agenda2)
    # ok
    resp.form.submit().follow()


def test_edit_pricing_overlapping_date_start(app, admin_user):
    agenda1 = Agenda.objects.create(label='Foo bar 1')
    agenda2 = Agenda.objects.create(label='Foo bar 2')
    pricing1 = Pricing.objects.create(
        label='Foo Bar',
        date_start=datetime.date(year=2021, month=10, day=1),
        date_end=datetime.date(year=2022, month=10, day=1),
    )
    pricing1.agendas.add(agenda1, agenda2)
    pricing2 = Pricing.objects.create(
        label='Foo Bar',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )
    pricing2.agendas.add(agenda1, agenda2)

    app = login(app)
    resp = app.get('/manage/pricing/%s/edit/' % pricing1.pk)
    # ok, date_start has not changed
    resp.form.submit().follow()

    resp = app.get('/manage/pricing/%s/edit/' % pricing1.pk)
    # ok, no overlapping
    resp.form['date_start'] = '2022-09-01'
    resp.form.submit().follow()

    resp = app.get('/manage/pricing/%s/edit/' % pricing1.pk)
    resp.form['date_start'] = '2021-10-01'
    resp = resp.form.submit()
    assert resp.context['form'].non_field_errors() == [
        'Agenda "Foo bar 1" has already a pricing overlapping this period.',
        'Agenda "Foo bar 2" has already a pricing overlapping this period.',
    ]
    pricing2.agendas.remove(agenda1)
    resp = resp.form.submit()
    assert resp.context['form'].non_field_errors() == [
        'Agenda "Foo bar 2" has already a pricing overlapping this period.'
    ]
    pricing2.agendas.remove(agenda2)
    # ok
    resp.form.submit().follow()


def test_edit_pricing_overlapping_date_end(app, admin_user):
    agenda1 = Agenda.objects.create(label='Foo bar 1')
    agenda2 = Agenda.objects.create(label='Foo bar 2')
    pricing1 = Pricing.objects.create(
        label='Foo Bar',
        date_start=datetime.date(year=2021, month=8, day=1),
        date_end=datetime.date(year=2022, month=8, day=1),
    )
    pricing1.agendas.add(agenda1, agenda2)
    pricing2 = Pricing.objects.create(
        label='Foo Bar',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )
    pricing2.agendas.add(agenda1, agenda2)

    app = login(app)
    resp = app.get('/manage/pricing/%s/edit/' % pricing1.pk)
    # ok, date_end has not changed
    resp.form.submit().follow()

    resp = app.get('/manage/pricing/%s/edit/' % pricing1.pk)
    # ok, no overlapping
    resp.form['date_end'] = '2021-09-01'
    resp.form.submit().follow()

    resp = app.get('/manage/pricing/%s/edit/' % pricing1.pk)
    resp.form['date_end'] = '2022-08-01'
    resp = resp.form.submit()
    assert resp.context['form'].non_field_errors() == [
        'Agenda "Foo bar 1" has already a pricing overlapping this period.',
        'Agenda "Foo bar 2" has already a pricing overlapping this period.',
    ]
    pricing2.agendas.remove(agenda1)
    resp = resp.form.submit()
    assert resp.context['form'].non_field_errors() == [
        'Agenda "Foo bar 2" has already a pricing overlapping this period.'
    ]
    pricing2.agendas.remove(agenda2)
    # ok
    resp.form.submit().follow()


def test_edit_pricing_billing_date_start(app, admin_user):
    pricing = Pricing.objects.create(
        label='Foo Bar',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
        flat_fee_schedule=True,
    )
    pricing.billingdates.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        label='Foo',
    )

    app = login(app)
    resp = app.get('/manage/pricing/%s/edit/' % pricing.pk)
    # ok, date_start has not changed
    resp.form.submit().follow()

    resp = app.get('/manage/pricing/%s/edit/' % pricing.pk)
    # ok, billing date inside period
    resp.form['date_start'] = '2021-08-31'
    resp.form.submit().follow()

    resp = app.get('/manage/pricing/%s/edit/' % pricing.pk)
    resp.form['date_start'] = '2021-09-02'
    resp = resp.form.submit()
    assert resp.context['form'].non_field_errors() == ['Some billing dates are outside the pricing period.']

    # but don't check billing dates if not flat_fee_schedule
    pricing.flat_fee_schedule = False
    pricing.save()
    # ok
    resp = app.get('/manage/pricing/%s/edit/' % pricing.pk)
    resp.form.submit().follow()


def test_edit_pricing_billing_date_end(app, admin_user):
    pricing = Pricing.objects.create(
        label='Foo Bar',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
        flat_fee_schedule=True,
    )
    pricing.billingdates.create(
        date_start=datetime.date(year=2022, month=9, day=1),
        label='Foo',
    )

    app = login(app)
    resp = app.get('/manage/pricing/%s/edit/' % pricing.pk)
    # ok, date_end has not changed
    resp.form.submit().follow()

    resp = app.get('/manage/pricing/%s/edit/' % pricing.pk)
    # ok, billing date inside period
    resp.form['date_end'] = '2022-09-02'
    resp.form.submit().follow()

    resp = app.get('/manage/pricing/%s/edit/' % pricing.pk)
    resp.form['date_end'] = '2022-09-01'
    resp = resp.form.submit()
    assert resp.context['form'].non_field_errors() == ['Some billing dates are outside the pricing period.']

    # but don't check billing dates if not flat_fee_schedule
    pricing.flat_fee_schedule = False
    pricing.save()
    # ok
    resp = app.get('/manage/pricing/%s/edit/' % pricing.pk)
    resp.form.submit().follow()


def test_edit_pricing_pricingoptions(app, admin_user):
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )

    app = login(app)

    assert pricing.kind == 'basic'
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    assert 'Pricing options' not in resp
    app.get('/manage/pricing/%s/pricing-options/' % pricing.pk, status=404)

    pricing.kind = 'reduction'
    pricing.save()
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    assert 'Pricing options' not in resp
    app.get('/manage/pricing/%s/pricing-options/' % pricing.pk, status=404)

    pricing.kind = 'effort'
    pricing.save()
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    assert 'Pricing options' in resp
    resp = resp.click(href='/manage/pricing/%s/pricing-options/' % pricing.pk)
    assert 'min_pricing' in resp.context['form'].fields
    assert 'max_pricing' in resp.context['form'].fields
    resp.form['min_pricing'] = None
    resp.form['max_pricing'] = None
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/%s/#open:pricing-options' % pricing.pk)
    pricing.refresh_from_db()
    assert pricing.min_pricing is None
    assert pricing.max_pricing is None
    assert PricingSnapshot.objects.count() == 1

    resp = app.get('/manage/pricing/%s/pricing-options/' % pricing.pk)
    resp.form['min_pricing'] = 0
    resp.form['max_pricing'] = 0
    resp = resp.form.submit()
    pricing.refresh_from_db()
    assert pricing.min_pricing == 0
    assert pricing.max_pricing == 0

    resp = app.get('/manage/pricing/%s/pricing-options/' % pricing.pk)
    resp.form['min_pricing'] = 10
    resp.form['max_pricing'] = 10
    resp = resp.form.submit()
    pricing.refresh_from_db()
    assert pricing.min_pricing == 10
    assert pricing.max_pricing == 10


def test_detail_pricing(app, admin_user):
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )

    app = login(app)
    resp = app.get('/manage/pricing/')
    resp = resp.click(href='/manage/pricing/%s/' % pricing.pk)

    app.get('/manage/pricing/%s/' % 0, status=404)


def test_delete_pricing(app, admin_user):
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )

    app = login(app)
    app.get('/manage/pricing/%s/delete/' % 0, status=404)

    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    resp = resp.click(href='/manage/pricing/%s/delete/' % pricing.pk)
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/')
    assert Pricing.objects.exists() is False
    assert PricingSnapshot.objects.count() == 1


def test_duplicate_pricing(app, admin_user):
    pricing = Pricing.objects.create(
        label='a pricing',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )
    assert Pricing.objects.count() == 1

    app = login(app)
    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    resp = resp.click(href='/manage/pricing/%s/duplicate/' % pricing.pk)
    resp = resp.form.submit()
    assert Pricing.objects.count() == 2
    assert PricingSnapshot.objects.count() == 1

    new_pricing = Pricing.objects.latest('pk')
    assert resp.location == '/manage/pricing/%s/' % new_pricing.pk
    assert new_pricing.pk != pricing.pk

    resp = resp.follow()
    assert 'copy-of-a-pricing' in resp.text

    resp = resp.click(href='/manage/pricing/%s/parameters/' % new_pricing.pk)
    resp = resp.click(href='/manage/pricing/%s/duplicate/' % new_pricing.pk)
    resp.form['label'] = 'hop'
    resp.form['date_start'] = '2022-09-01'
    resp.form['date_end'] = '2023-09-01'
    resp = resp.form.submit().follow()
    assert 'hop' in resp.text
    new_pricing = Pricing.objects.latest('pk')
    assert new_pricing.label == 'hop'
    assert new_pricing.date_start == datetime.date(year=2022, month=9, day=1)
    assert new_pricing.date_end == datetime.date(year=2023, month=9, day=1)


def test_pricing_edit_permissions(app, admin_user):
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    group_foo = Group.objects.create(name='role-foo')
    group_bar = Group.objects.create(name='role-bar')

    app = login(app)
    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    assert '<li>Edit role: </li>' in resp
    assert '<li>View role: </li>' in resp
    resp = resp.click(href='/manage/pricing/%s/permissions/' % pricing.pk)
    resp.form.set('edit_role', group_foo.id)
    resp.form.set('view_role', group_bar.id)
    resp = resp.form.submit().follow()
    assert '<li>Edit role: role-foo</li>' in resp
    assert '<li>View role: role-bar</li>' in resp
    pricing.refresh_from_db()
    assert pricing.edit_role == group_foo
    assert pricing.view_role == group_bar
    assert PricingSnapshot.objects.count() == 1


def test_pricing_edit_extra_variables(app, admin_user):
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )
    assert pricing.extra_variables == {}

    app = login(app)
    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    assert '<label>Extra variables:</label>' not in resp.text
    resp = resp.click(href='/manage/pricing/%s/variable/' % pricing.pk)
    resp.form['form-0-key'] = 'foo'
    resp.form['form-0-value'] = 'bar'
    resp = resp.form.submit().follow()
    pricing.refresh_from_db()
    assert pricing.extra_variables == {'foo': 'bar'}
    assert '<label>Extra variables:</label>' in resp.text
    assert '<dt><b>foo:</b></dt>' in resp
    assert '<dd><pre>bar</pre></dd>' in resp
    assert PricingSnapshot.objects.count() == 1

    resp = resp.click(href='/manage/pricing/%s/variable/' % pricing.pk)
    assert resp.form['form-TOTAL_FORMS'].value == '2'
    assert resp.form['form-0-key'].value == 'foo'
    assert resp.form['form-0-value'].value == 'bar'
    assert resp.form['form-1-key'].value == ''
    assert resp.form['form-1-value'].value == ''
    resp.form['form-0-value'] = 'bar-bis'
    resp.form['form-1-key'] = 'blah'
    resp.form['form-1-value'] = 'baz'
    resp = resp.form.submit().follow()
    pricing.refresh_from_db()
    assert pricing.extra_variables == {
        'foo': 'bar-bis',
        'blah': 'baz',
    }
    assert '<dt><b>blah:</b></dt>' in resp
    assert '<dd><pre>baz</pre></dd>' in resp
    assert '<dt><b>foo:</b></dt>' in resp
    assert '<dd><pre>bar-bis</pre></dd>' in resp

    resp = resp.click(href='/manage/pricing/%s/variable/' % pricing.pk)
    assert resp.form['form-TOTAL_FORMS'].value == '3'
    assert resp.form['form-0-key'].value == 'blah'
    assert resp.form['form-0-value'].value == 'baz'
    assert resp.form['form-1-key'].value == 'foo'
    assert resp.form['form-1-value'].value == 'bar-bis'
    assert resp.form['form-2-key'].value == ''
    assert resp.form['form-2-value'].value == ''
    resp.form['form-1-key'] = 'foo'
    resp.form['form-1-value'] = 'bar'
    resp.form['form-0-key'] = ''
    resp = resp.form.submit().follow()
    pricing.refresh_from_db()
    assert pricing.extra_variables == {
        'foo': 'bar',
    }
    assert '<dt><b>foo:</b></dt>' in resp
    assert '<dd><pre>bar</pre></dd>' in resp


def test_pricing_add_category(app, admin_user):
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )
    category1 = CriteriaCategory.objects.create(label='Cat 1')
    category2 = CriteriaCategory.objects.create(label='Cat 2')
    category3 = CriteriaCategory.objects.create(label='Cat 3')
    category4 = CriteriaCategory.objects.create(label='Cat 4')

    app = login(app)
    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    resp = resp.click(href='/manage/pricing/%s/category/add/' % pricing.pk)
    assert list(resp.context['form'].fields['category'].queryset) == [
        category1,
        category2,
        category3,
        category4,
    ]
    resp.form['category'] = category1.pk
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/%s/parameters/#open:criterias' % pricing.pk)
    resp = resp.follow()
    assert '/manage/pricing/%s/category/add/' % pricing.pk in resp
    assert list(
        PricingCriteriaCategory.objects.filter(pricing=pricing).values_list('category', flat=True)
    ) == [category1.pk]
    assert list(PricingCriteriaCategory.objects.filter(pricing=pricing).values_list('order', flat=True)) == [
        1
    ]
    assert PricingSnapshot.objects.count() == 1

    resp = app.get('/manage/pricing/%s/category/add/' % pricing.pk)
    assert list(resp.context['form'].fields['category'].queryset) == [category2, category3, category4]
    resp.form['category'] = category4.pk
    resp = resp.form.submit().follow()
    assert list(
        PricingCriteriaCategory.objects.filter(pricing=pricing).values_list('category', flat=True)
    ) == [category1.pk, category4.pk]
    assert list(PricingCriteriaCategory.objects.filter(pricing=pricing).values_list('order', flat=True)) == [
        1,
        2,
    ]

    resp = app.get('/manage/pricing/%s/category/add/' % pricing.pk)
    assert list(resp.context['form'].fields['category'].queryset) == [category2, category3]
    resp.form['category'] = category2.pk
    resp = resp.form.submit().follow()
    assert '/manage/pricing/%s/category/add/' % pricing.pk not in resp
    assert list(
        PricingCriteriaCategory.objects.filter(pricing=pricing).values_list('category', flat=True)
    ) == [category1.pk, category4.pk, category2.pk]
    assert list(PricingCriteriaCategory.objects.filter(pricing=pricing).values_list('order', flat=True)) == [
        1,
        2,
        3,
    ]

    app.get('/manage/pricing/%s/category/add/' % pricing.pk, status=404)


def test_pricing_edit_category(app, admin_user):
    category1 = CriteriaCategory.objects.create(label='Cat 1')
    criteria1 = Criteria.objects.create(label='Crit 1', category=category1)
    criteria2 = Criteria.objects.create(label='Crit 2', category=category1)
    criteria3 = Criteria.objects.create(label='Crit 3', category=category1)
    criteria4 = Criteria.objects.create(label='Crit 4', category=category1)
    category2 = CriteriaCategory.objects.create(label='cat 2')
    criteria5 = Criteria.objects.create(label='Crit 5', category=category2)
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )
    pricing.categories.add(category1, through_defaults={'order': 1})
    pricing.categories.add(category2, through_defaults={'order': 2})

    app = login(app)
    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    resp = resp.click(href='/manage/pricing/%s/category/%s/edit/' % (pricing.pk, category1.pk))
    assert list(resp.context['form'].fields['criterias'].queryset) == [
        criteria1,
        criteria2,
        criteria3,
        criteria4,
    ]
    assert list(resp.context['form'].initial['criterias']) == []
    resp.form['criterias'] = [criteria1.pk, criteria3.pk]
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/%s/parameters/#open:criterias' % pricing.pk)
    resp = resp.follow()
    assert list(pricing.criterias.order_by('pk')) == [criteria1, criteria3]
    assert PricingSnapshot.objects.count() == 1

    resp = app.get('/manage/pricing/%s/category/%s/edit/' % (pricing.pk, category1.pk))
    assert list(resp.context['form'].initial['criterias']) == [criteria1, criteria3]
    resp.form['criterias'] = [criteria1.pk, criteria4.pk]
    resp = resp.form.submit().follow()
    assert list(pricing.criterias.order_by('pk')) == [criteria1, criteria4]

    resp = app.get('/manage/pricing/%s/category/%s/edit/' % (pricing.pk, category2.pk))
    assert list(resp.context['form'].fields['criterias'].queryset) == [criteria5]
    assert list(resp.context['form'].initial['criterias']) == []
    resp.form['criterias'] = [criteria5.pk]
    resp = resp.form.submit().follow()
    assert list(pricing.criterias.order_by('pk')) == [criteria1, criteria4, criteria5]


def test_pricing_delete_category(app, admin_user):
    category1 = CriteriaCategory.objects.create(label='Cat 1')
    criteria1 = Criteria.objects.create(label='Crit 1', category=category1)
    category2 = CriteriaCategory.objects.create(label='Cat 2')
    criteria2 = Criteria.objects.create(label='Crit 2', category=category2)
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )
    pricing.categories.add(category1, through_defaults={'order': 1})
    pricing.categories.add(category2, through_defaults={'order': 2})
    pricing.criterias.add(criteria1, criteria2)

    app = login(app)
    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    resp = resp.click(href='/manage/pricing/%s/category/%s/delete/' % (pricing.pk, category1.pk))
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/%s/parameters/#open:criterias' % pricing.pk)
    resp = resp.follow()
    assert list(pricing.categories.all()) == [category2]
    assert list(pricing.criterias.all()) == [criteria2]
    assert PricingSnapshot.objects.count() == 1
    assert CriteriaCategory.objects.filter(pk=category1.pk).exists() is True

    # not linked
    app.get(
        '/manage/pricing/%s/category/%s/delete/' % (pricing.pk, category1.pk),
        status=404,
    )
    # unknown
    app.get('/manage/pricing/%s/category/%s/delete/' % (pricing.pk, 0), status=404)


def test_pricing_reorder_categories(app, admin_user):
    category1 = CriteriaCategory.objects.create(label='Cat 1')
    category2 = CriteriaCategory.objects.create(label='Cat 2')
    category3 = CriteriaCategory.objects.create(label='Cat 3')
    category4 = CriteriaCategory.objects.create(label='Cat 4')
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )
    pricing.categories.add(category1, through_defaults={'order': 1})
    pricing.categories.add(category2, through_defaults={'order': 2})
    pricing.categories.add(category3, through_defaults={'order': 3})
    assert list(
        PricingCriteriaCategory.objects.filter(pricing=pricing).values_list('category', flat=True)
    ) == [category1.pk, category2.pk, category3.pk]
    assert list(PricingCriteriaCategory.objects.filter(pricing=pricing).values_list('order', flat=True)) == [
        1,
        2,
        3,
    ]

    app = login(app)
    # missing get params
    app.get('/manage/pricing/%s/order/' % (pricing.pk), status=400)

    # bad new-order param
    bad_params = [
        # missing category3 in order
        ','.join(str(x) for x in [category1.pk, category2.pk]),
        # category1 mentionned twice
        ','.join(str(x) for x in [category1.pk, category2.pk, category3.pk, category1.pk]),
        # category4 not in pricing categories
        ','.join(str(x) for x in [category1.pk, category2.pk, category3.pk, category4.pk]),
        # not an id
        'foo,1,2,3',
        ' 1 ,2,3',
    ]
    for bad_param in bad_params:
        app.get(
            '/manage/pricing/%s/order/' % (pricing.pk),
            params={'new-order': bad_param},
            status=400,
        )
    # not changed
    assert list(
        PricingCriteriaCategory.objects.filter(pricing=pricing).values_list('category', flat=True)
    ) == [category1.pk, category2.pk, category3.pk]
    assert list(PricingCriteriaCategory.objects.filter(pricing=pricing).values_list('order', flat=True)) == [
        1,
        2,
        3,
    ]

    # change order
    app.get(
        '/manage/pricing/%s/order/' % (pricing.pk),
        params={'new-order': ','.join(str(x) for x in [category3.pk, category1.pk, category2.pk])},
    )
    assert list(
        PricingCriteriaCategory.objects.filter(pricing=pricing).values_list('category', flat=True)
    ) == [category3.pk, category1.pk, category2.pk]
    assert list(PricingCriteriaCategory.objects.filter(pricing=pricing).values_list('order', flat=True)) == [
        1,
        2,
        3,
    ]
    assert PricingSnapshot.objects.count() == 1


def test_pricing_add_agenda(app, admin_user):
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )
    agenda1 = Agenda.objects.create(label='Foo bar 1')
    agenda2 = Agenda.objects.create(label='Foo bar 2')
    agenda3 = Agenda.objects.create(label='Foo bar 3')

    app = login(app)
    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    resp = resp.click(href='/manage/pricing/%s/agenda/add/' % pricing.pk)
    assert resp.form['agendas'].options == [
        ('', True, '---------'),
        (str(agenda1.pk), False, 'Foo bar 1'),
        (str(agenda2.pk), False, 'Foo bar 2'),
        (str(agenda3.pk), False, 'Foo bar 3'),
    ]
    assert list(resp.context['form'].fields['agendas'].queryset) == [
        agenda1,
        agenda2,
        agenda3,
    ]
    resp.form.get('agendas', 0).value = agenda1.pk
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/%s/parameters/#open:agendas' % pricing.pk)
    resp = resp.follow()
    assert list(pricing.agendas.all()) == [agenda1]
    assert PricingSnapshot.objects.count() == 1

    resp = app.get('/manage/pricing/%s/agenda/add/' % pricing.pk)
    assert list(resp.context['form'].fields['agendas'].queryset) == [
        agenda2,
        agenda3,
    ]
    resp.form.get('agendas', 0).value = agenda2.pk
    resp = resp.form.submit().follow()
    assert list(pricing.agendas.all()) == [agenda1, agenda2]

    pricing2 = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=11, day=1),
        date_end=datetime.date(year=2022, month=11, day=1),
    )
    resp = app.get('/manage/pricing/%s/agenda/add/' % pricing2.pk)
    assert list(resp.context['form'].fields['agendas'].queryset) == [
        agenda1,
        agenda2,
        agenda3,
    ]
    resp.form.get('agendas', 0).value = agenda1.pk
    resp = resp.form.submit()
    assert resp.context['form'].errors['agendas'] == [
        'The agenda "Foo bar 1" has already a pricing overlapping this period.'
    ]
    resp.form.get('agendas', 0).value = agenda3.pk
    resp.form.submit().follow()
    assert list(pricing2.agendas.all()) == [agenda3]

    pricing.flat_fee_schedule = True
    pricing.save()
    resp = app.get('/manage/pricing/%s/agenda/add/' % pricing2.pk)
    assert list(resp.context['form'].fields['agendas'].queryset) == [
        agenda1,
        agenda2,
    ]
    resp.form.get('agendas', 0).value = agenda1.pk
    resp.form.submit()
    assert list(pricing2.agendas.all()) == [agenda1, agenda3]

    pricing.subscription_required = False
    pricing.save()
    app.get('/manage/pricing/%s/agenda/add/' % pricing.pk, status=404)


def test_pricing_delete_agenda(app, admin_user):
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )
    agenda1 = Agenda.objects.create(label='Foo bar 1')
    agenda2 = Agenda.objects.create(label='Foo bar 2')
    Agenda.objects.create(label='Foo bar 3')
    pricing.agendas.add(agenda1, agenda2)

    app = login(app)
    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    resp = resp.click(href='/manage/pricing/%s/agenda/%s/delete/' % (pricing.pk, agenda1.pk))
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/%s/parameters/#open:agendas' % pricing.pk)
    resp = resp.follow()
    assert list(pricing.agendas.all()) == [agenda2]
    assert PricingSnapshot.objects.count() == 1
    assert Agenda.objects.filter(pk=agenda1.pk).exists() is True

    # not linked
    app.get('/manage/pricing/%s/agenda/%s/delete/' % (pricing.pk, agenda1.pk), status=404)
    # unknown
    app.get('/manage/pricing/%s/agenda/%s/delete/' % (pricing.pk, 0), status=404)

    pricing.subscription_required = False
    pricing.save()
    app.get('/manage/pricing/%s/agenda/%s/delete/' % (pricing.pk, agenda2.pk), status=404)


def test_pricing_add_billing_date(app, admin_user):
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
        flat_fee_schedule=True,
    )

    app = login(app)
    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    resp = resp.click(href='/manage/pricing/%s/billing-date/add/' % pricing.pk)
    resp.form['date_start'] = '2021-08-31'
    resp.form['label'] = 'Foo'
    resp = resp.form.submit()
    assert resp.context['form'].errors['date_start'] == [
        'The billing start date must be within the period of the pricing.'
    ]
    resp.form['date_start'] = '2022-09-01'
    resp = resp.form.submit()
    assert resp.context['form'].errors['date_start'] == [
        'The billing start date must be within the period of the pricing.'
    ]
    resp.form['date_start'] = '2022-08-31'
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/%s/parameters/#open:billing-dates' % pricing.pk)
    assert PricingSnapshot.objects.count() == 1

    assert pricing.billingdates.count() == 1
    billing_date = BillingDate.objects.latest('pk')
    assert billing_date.date_start == datetime.date(2022, 8, 31)
    assert billing_date.label == 'Foo'

    pricing.flat_fee_schedule = False
    pricing.save()
    app.get('/manage/pricing/%s/billing-date/add/' % pricing.pk, status=404)


def test_pricing_edit_billing_date(app, admin_user):
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
        flat_fee_schedule=True,
    )
    billing_date = pricing.billingdates.create(
        date_start=datetime.date(year=2021, month=8, day=31),
        label='Foo',
    )

    app = login(app)
    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    resp = resp.click(href=r'/manage/pricing/%s/billing-date/%s/$' % (pricing.pk, billing_date.pk))
    resp.form['label'] = 'Bar'
    resp = resp.form.submit()
    assert resp.context['form'].errors['date_start'] == [
        'The billing start date must be within the period of the pricing.'
    ]
    resp.form['date_start'] = '2022-09-01'
    resp = resp.form.submit()
    assert resp.context['form'].errors['date_start'] == [
        'The billing start date must be within the period of the pricing.'
    ]
    resp.form['date_start'] = '2022-08-31'
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/%s/parameters/#open:billing-dates' % pricing.pk)
    assert PricingSnapshot.objects.count() == 1

    assert pricing.billingdates.count() == 1
    billing_date.refresh_from_db()
    assert billing_date.date_start == datetime.date(2022, 8, 31)
    assert billing_date.label == 'Bar'

    pricing.flat_fee_schedule = False
    pricing.save()
    app.get(
        '/manage/pricing/%s/billing-date/%s/' % (pricing.pk, billing_date.pk),
        status=404,
    )


def test_pricing_delete_billing_date(app, admin_user):
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
        flat_fee_schedule=True,
    )
    billing_date1 = pricing.billingdates.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        label='Foo',
    )
    billing_date2 = pricing.billingdates.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        label='Bar',
    )

    app = login(app)
    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    resp = resp.click(href='/manage/pricing/%s/billing-date/%s/delete' % (pricing.pk, billing_date1.pk))
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/%s/parameters/#open:billing-dates' % pricing.pk)
    assert PricingSnapshot.objects.count() == 1

    assert pricing.billingdates.count() == 1

    pricing.flat_fee_schedule = False
    pricing.save()
    app.get(
        '/manage/pricing/%s/billing-date/%s/' % (pricing.pk, billing_date2.pk),
        status=404,
    )

    pricing2 = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2022, month=9, day=1),
    )
    app.get(
        '/manage/pricing/%s/billing-date/%s/' % (pricing2.pk, billing_date2.pk),
        status=404,
    )
    app.get('/manage/pricing/%s/billing-date/%s/' % (0, billing_date2.pk), status=404)


def test_detail_pricing_3_categories(app, admin_user):
    category1 = CriteriaCategory.objects.create(label='Cat 1')
    Criteria.objects.create(label='Crit 1-1', slug='crit-1-1', category=category1, order=1)
    Criteria.objects.create(label='Crit 1-2', slug='crit-1-2', category=category1, order=2)
    category2 = CriteriaCategory.objects.create(label='Cat 2')
    Criteria.objects.create(label='Crit 2-1', slug='crit-2-1', category=category2, order=1)
    Criteria.objects.create(label='Crit 2-2', slug='crit-2-2', category=category2, order=2)
    Criteria.objects.create(label='Crit 2-3', slug='crit-2-3', category=category2, order=3)
    category3 = CriteriaCategory.objects.create(label='Cat 3')
    Criteria.objects.create(label='Crit 3-1', slug='crit-3-1', category=category3, order=1)
    Criteria.objects.create(label='Crit 3-3', slug='crit-3-3', category=category3, order=3)
    Criteria.objects.create(label='Crit 3-4', slug='crit-3-4', category=category3, order=4)
    Criteria.objects.create(label='Crit 3-2', slug='crit-3-2', category=category3, order=2)

    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
        pricing_data={
            'cat-1:crit-1-1': {
                'cat-2:crit-2-1': {
                    'cat-3:crit-3-1': 111,
                    'cat-3:crit-3-3': 'not-a-decimal',
                    'cat-3:crit-3-4': 114,
                },
                'cat-2:crit-2-3': {
                    'cat-3:crit-3-2': 132,
                },
            },
            'cat-1:crit-1-2': {
                'cat-2:crit-2-2': {
                    'cat-3:crit-3-3': 223,
                },
            },
        },
    )
    pricing.categories.add(category1, through_defaults={'order': 1})
    pricing.categories.add(category2, through_defaults={'order': 2})
    pricing.categories.add(category3, through_defaults={'order': 3})
    pricing.criterias.set(Criteria.objects.all())

    app = login(app)
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    assert '<h3>Crit 1-1</h3>' in resp
    ths = resp.pyquery.find('table.pricing-matrix-crit-1-1 thead th')
    assert len(ths) == 4
    assert ths[0].text is None
    assert ths[1].text == 'Crit 2-1'
    assert ths[2].text == 'Crit 2-2'
    assert ths[3].text == 'Crit 2-3'
    assert resp.pyquery.find('table.pricing-matrix-crit-1-1 tr.pricing-row-crit-3-1 th')[0].text == 'Crit 3-1'
    assert resp.pyquery.find('table.pricing-matrix-crit-1-1 tr.pricing-row-crit-3-2 th')[0].text == 'Crit 3-2'
    assert resp.pyquery.find('table.pricing-matrix-crit-1-1 tr.pricing-row-crit-3-3 th')[0].text == 'Crit 3-3'
    assert resp.pyquery.find('table.pricing-matrix-crit-1-1 tr.pricing-row-crit-3-4 th')[0].text == 'Crit 3-4'
    assert (
        resp.pyquery.find('table.pricing-matrix-crit-1-1 tr.pricing-row-crit-3-1 td.pricing-cell-crit-2-1')[
            0
        ].text
        == '111.00'
    )
    assert (
        resp.pyquery.find('table.pricing-matrix-crit-1-1 tr.pricing-row-crit-3-2 td.pricing-cell-crit-2-1')[
            0
        ].text
        is None
    )  # not defined
    assert (
        resp.pyquery.find('table.pricing-matrix-crit-1-1 tr.pricing-row-crit-3-3 td.pricing-cell-crit-2-1')[
            0
        ].text
        is None
    )  # wrong value
    assert (
        resp.pyquery.find('table.pricing-matrix-crit-1-1 tr.pricing-row-crit-3-4 td.pricing-cell-crit-2-1')[
            0
        ].text
        == '114.00'
    )
    assert (
        resp.pyquery.find('table.pricing-matrix-crit-1-1 tr.pricing-row-crit-3-2 td.pricing-cell-crit-2-3')[
            0
        ].text
        == '132.00'
    )
    assert '<h3>Crit 1-2</h3>' in resp
    ths = resp.pyquery.find('table.pricing-matrix-crit-1-2 thead th')
    assert len(ths) == 4
    assert ths[0].text is None
    assert ths[1].text == 'Crit 2-1'
    assert ths[2].text == 'Crit 2-2'
    assert ths[3].text == 'Crit 2-3'
    assert resp.pyquery.find('table.pricing-matrix-crit-1-2 tr.pricing-row-crit-3-1 th')[0].text == 'Crit 3-1'
    assert resp.pyquery.find('table.pricing-matrix-crit-1-2 tr.pricing-row-crit-3-2 th')[0].text == 'Crit 3-2'
    assert resp.pyquery.find('table.pricing-matrix-crit-1-2 tr.pricing-row-crit-3-3 th')[0].text == 'Crit 3-3'
    assert resp.pyquery.find('table.pricing-matrix-crit-1-2 tr.pricing-row-crit-3-4 th')[0].text == 'Crit 3-4'
    assert (
        resp.pyquery.find('table.pricing-matrix-crit-1-2 tr.pricing-row-crit-3-2 td.pricing-cell-crit-2-2')[
            0
        ].text
        is None
    )  # not defined
    assert (
        resp.pyquery.find('table.pricing-matrix-crit-1-2 tr.pricing-row-crit-3-3 td.pricing-cell-crit-2-2')[
            0
        ].text
        == '223.00'
    )

    pricing.kind = 'effort'
    pricing.save()
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    assert (
        resp.pyquery.find('table.pricing-matrix-crit-1-2 tr.pricing-row-crit-3-3 td.pricing-cell-crit-2-2')[
            0
        ].text
        == '223.0000'
    )

    pricing.kind = 'reduction'
    pricing.min_pricing_data = {
        'cat-1:crit-1-1': {
            'cat-2:crit-2-1': {
                'cat-3:crit-3-1': 1.11,
                'cat-3:crit-3-3': 'not-a-decimal',
                'cat-3:crit-3-4': 1.14,
            },
            'cat-2:crit-2-3': {
                'cat-3:crit-3-2': 1.32,
            },
        },
        'cat-1:crit-1-2': {
            'cat-2:crit-2-2': {
                'cat-3:crit-3-3': 2.23,
            },
        },
    }
    pricing.save()
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    ths = resp.pyquery.find('table.pricing-min-matrix-crit-1-1 thead th')
    assert len(ths) == 4
    assert ths[0].text is None
    assert ths[1].text == 'Crit 2-1'
    assert ths[2].text == 'Crit 2-2'
    assert ths[3].text == 'Crit 2-3'
    assert (
        resp.pyquery.find('table.pricing-min-matrix-crit-1-1 tr.pricing-row-crit-3-1 th')[0].text
        == 'Crit 3-1'
    )
    assert (
        resp.pyquery.find('table.pricing-min-matrix-crit-1-1 tr.pricing-row-crit-3-2 th')[0].text
        == 'Crit 3-2'
    )
    assert (
        resp.pyquery.find('table.pricing-min-matrix-crit-1-1 tr.pricing-row-crit-3-3 th')[0].text
        == 'Crit 3-3'
    )
    assert (
        resp.pyquery.find('table.pricing-min-matrix-crit-1-1 tr.pricing-row-crit-3-4 th')[0].text
        == 'Crit 3-4'
    )
    assert (
        resp.pyquery.find(
            'table.pricing-min-matrix-crit-1-1 tr.pricing-row-crit-3-1 td.pricing-cell-crit-2-1'
        )[0].text
        == '1.11'
    )
    assert (
        resp.pyquery.find(
            'table.pricing-min-matrix-crit-1-1 tr.pricing-row-crit-3-2 td.pricing-cell-crit-2-1'
        )[0].text
        is None
    )  # not defined
    assert (
        resp.pyquery.find(
            'table.pricing-min-matrix-crit-1-1 tr.pricing-row-crit-3-3 td.pricing-cell-crit-2-1'
        )[0].text
        is None
    )  # wrong value
    assert (
        resp.pyquery.find(
            'table.pricing-min-matrix-crit-1-1 tr.pricing-row-crit-3-4 td.pricing-cell-crit-2-1'
        )[0].text
        == '1.14'
    )
    assert (
        resp.pyquery.find(
            'table.pricing-min-matrix-crit-1-1 tr.pricing-row-crit-3-2 td.pricing-cell-crit-2-3'
        )[0].text
        == '1.32'
    )
    assert '<h3>Crit 1-2</h3>' in resp
    ths = resp.pyquery.find('table.pricing-min-matrix-crit-1-2 thead th')
    assert len(ths) == 4
    assert ths[0].text is None
    assert ths[1].text == 'Crit 2-1'
    assert ths[2].text == 'Crit 2-2'
    assert ths[3].text == 'Crit 2-3'
    assert (
        resp.pyquery.find('table.pricing-min-matrix-crit-1-2 tr.pricing-row-crit-3-1 th')[0].text
        == 'Crit 3-1'
    )
    assert (
        resp.pyquery.find('table.pricing-min-matrix-crit-1-2 tr.pricing-row-crit-3-2 th')[0].text
        == 'Crit 3-2'
    )
    assert (
        resp.pyquery.find('table.pricing-min-matrix-crit-1-2 tr.pricing-row-crit-3-3 th')[0].text
        == 'Crit 3-3'
    )
    assert (
        resp.pyquery.find('table.pricing-min-matrix-crit-1-2 tr.pricing-row-crit-3-4 th')[0].text
        == 'Crit 3-4'
    )
    assert (
        resp.pyquery.find(
            'table.pricing-min-matrix-crit-1-2 tr.pricing-row-crit-3-2 td.pricing-cell-crit-2-2'
        )[0].text
        is None
    )  # not defined
    assert (
        resp.pyquery.find(
            'table.pricing-min-matrix-crit-1-2 tr.pricing-row-crit-3-3 td.pricing-cell-crit-2-2'
        )[0].text
        == '2.23'
    )


def test_detail_pricing_2_categories(app, admin_user):
    category2 = CriteriaCategory.objects.create(label='Cat 2')
    Criteria.objects.create(label='Crit 2-1', slug='crit-2-1', category=category2, order=1)
    Criteria.objects.create(label='Crit 2-2', slug='crit-2-2', category=category2, order=2)
    Criteria.objects.create(label='Crit 2-3', slug='crit-2-3', category=category2, order=3)
    category3 = CriteriaCategory.objects.create(label='Cat 3')
    Criteria.objects.create(label='Crit 3-1', slug='crit-3-1', category=category3, order=1)
    Criteria.objects.create(label='Crit 3-3', slug='crit-3-3', category=category3, order=3)
    Criteria.objects.create(label='Crit 3-4', slug='crit-3-4', category=category3, order=4)
    Criteria.objects.create(label='Crit 3-2', slug='crit-3-2', category=category3, order=2)

    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
        pricing_data={
            'cat-2:crit-2-1': {
                'cat-3:crit-3-1': 111,
                'cat-3:crit-3-3': 'not-a-decimal',
                'cat-3:crit-3-4': 114,
            },
            'cat-2:crit-2-3': {
                'cat-3:crit-3-2': 132,
            },
        },
    )
    pricing.categories.add(category2, through_defaults={'order': 1})
    pricing.categories.add(category3, through_defaults={'order': 2})
    pricing.criterias.set(Criteria.objects.all())

    app = login(app)
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    assert len(resp.pyquery.find('div.section.prixing-matrix h3')) == 0
    ths = resp.pyquery.find('table.pricing-matrix- thead th')
    assert len(ths) == 4
    assert ths[0].text is None
    assert ths[1].text == 'Crit 2-1'
    assert ths[2].text == 'Crit 2-2'
    assert ths[3].text == 'Crit 2-3'
    assert resp.pyquery.find('table.pricing-matrix- tr.pricing-row-crit-3-1 th')[0].text == 'Crit 3-1'
    assert resp.pyquery.find('table.pricing-matrix- tr.pricing-row-crit-3-2 th')[0].text == 'Crit 3-2'
    assert resp.pyquery.find('table.pricing-matrix- tr.pricing-row-crit-3-3 th')[0].text == 'Crit 3-3'
    assert resp.pyquery.find('table.pricing-matrix- tr.pricing-row-crit-3-4 th')[0].text == 'Crit 3-4'
    assert (
        resp.pyquery.find('table.pricing-matrix- tr.pricing-row-crit-3-1 td.pricing-cell-crit-2-1')[0].text
        == '111.00'
    )
    assert (
        resp.pyquery.find('table.pricing-matrix- tr.pricing-row-crit-3-2 td.pricing-cell-crit-2-1')[0].text
        is None
    )  # not defined
    assert (
        resp.pyquery.find('table.pricing-matrix- tr.pricing-row-crit-3-3 td.pricing-cell-crit-2-1')[0].text
        is None
    )  # wrong value
    assert (
        resp.pyquery.find('table.pricing-matrix- tr.pricing-row-crit-3-4 td.pricing-cell-crit-2-1')[0].text
        == '114.00'
    )
    assert (
        resp.pyquery.find('table.pricing-matrix- tr.pricing-row-crit-3-2 td.pricing-cell-crit-2-3')[0].text
        == '132.00'
    )

    pricing.kind = 'effort'
    pricing.save()
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    assert resp.pyquery.find('table tr.pricing-row-crit-3-2 td.pricing-cell-crit-2-3')[0].text == '132.0000'

    pricing.kind = 'reduction'
    pricing.min_pricing_data = {
        'cat-2:crit-2-1': {
            'cat-3:crit-3-1': 1.11,
            'cat-3:crit-3-3': 'not-a-decimal',
            'cat-3:crit-3-4': 1.14,
        },
        'cat-2:crit-2-3': {
            'cat-3:crit-3-2': 1.32,
        },
    }
    pricing.save()
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    ths = resp.pyquery.find('table.pricing-min-matrix- thead th')
    assert len(ths) == 4
    assert ths[0].text is None
    assert ths[1].text == 'Crit 2-1'
    assert ths[2].text == 'Crit 2-2'
    assert ths[3].text == 'Crit 2-3'
    assert resp.pyquery.find('table.pricing-min-matrix- tr.pricing-row-crit-3-1 th')[0].text == 'Crit 3-1'
    assert resp.pyquery.find('table.pricing-min-matrix- tr.pricing-row-crit-3-2 th')[0].text == 'Crit 3-2'
    assert resp.pyquery.find('table.pricing-min-matrix- tr.pricing-row-crit-3-3 th')[0].text == 'Crit 3-3'
    assert resp.pyquery.find('table.pricing-min-matrix- tr.pricing-row-crit-3-4 th')[0].text == 'Crit 3-4'
    assert (
        resp.pyquery.find('table.pricing-min-matrix- tr.pricing-row-crit-3-1 td.pricing-cell-crit-2-1')[
            0
        ].text
        == '1.11'
    )
    assert (
        resp.pyquery.find('table.pricing-min-matrix- tr.pricing-row-crit-3-2 td.pricing-cell-crit-2-1')[
            0
        ].text
        is None
    )  # not defined
    assert (
        resp.pyquery.find('table.pricing-min-matrix- tr.pricing-row-crit-3-3 td.pricing-cell-crit-2-1')[
            0
        ].text
        is None
    )  # wrong value
    assert (
        resp.pyquery.find('table.pricing-min-matrix- tr.pricing-row-crit-3-4 td.pricing-cell-crit-2-1')[
            0
        ].text
        == '1.14'
    )
    assert (
        resp.pyquery.find('table.pricing-min-matrix- tr.pricing-row-crit-3-2 td.pricing-cell-crit-2-3')[
            0
        ].text
        == '1.32'
    )


def test_detail_pricing_1_category(app, admin_user):
    category3 = CriteriaCategory.objects.create(label='Cat 3')
    Criteria.objects.create(label='Crit 3-1', slug='crit-3-1', category=category3, order=1)
    Criteria.objects.create(label='Crit 3-3', slug='crit-3-3', category=category3, order=3)
    Criteria.objects.create(label='Crit 3-4', slug='crit-3-4', category=category3, order=4)
    Criteria.objects.create(label='Crit 3-2', slug='crit-3-2', category=category3, order=2)

    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
        pricing_data={
            'cat-3:crit-3-1': 111,
            'cat-3:crit-3-3': 'not-a-decimal',
            'cat-3:crit-3-4': 114,
        },
    )
    pricing.categories.add(category3, through_defaults={'order': 3})
    pricing.criterias.set(Criteria.objects.all())

    app = login(app)
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    assert len(resp.pyquery.find('div.section.prixing-matrix h3')) == 0
    ths = resp.pyquery.find('table.pricing-matrix- thead')
    assert len(ths) == 0
    assert resp.pyquery.find('table.pricing-matrix- tr.pricing-row-crit-3-1 th')[0].text == 'Crit 3-1'
    assert resp.pyquery.find('table.pricing-matrix- tr.pricing-row-crit-3-2 th')[0].text == 'Crit 3-2'
    assert resp.pyquery.find('table.pricing-matrix- tr.pricing-row-crit-3-3 th')[0].text == 'Crit 3-3'
    assert resp.pyquery.find('table.pricing-matrix- tr.pricing-row-crit-3-4 th')[0].text == 'Crit 3-4'
    assert resp.pyquery.find('table.pricing-matrix- tr.pricing-row-crit-3-1 td')[0].text == '111.00'
    assert (
        resp.pyquery.find('table.pricing-matrix- tr.pricing-row-crit-3-2 td')[0].text is None
    )  # not defined
    assert (
        resp.pyquery.find('table.pricing-matrix- tr.pricing-row-crit-3-3 td')[0].text is None
    )  # wrong value
    assert resp.pyquery.find('table.pricing-matrix- tr.pricing-row-crit-3-4 td')[0].text == '114.00'

    pricing.kind = 'effort'
    pricing.save()
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    assert resp.pyquery.find('table.pricing-matrix- tr.pricing-row-crit-3-4 td')[0].text == '114.0000'

    pricing.kind = 'reduction'
    pricing.min_pricing_data = {
        'cat-3:crit-3-1': 1.11,
        'cat-3:crit-3-3': 'not-a-decimal',
        'cat-3:crit-3-4': 1.14,
    }
    pricing.save()
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    ths = resp.pyquery.find('table.pricing-min-matrix- thead')
    assert len(ths) == 0
    assert resp.pyquery.find('table.pricing-min-matrix- tr.pricing-row-crit-3-1 th')[0].text == 'Crit 3-1'
    assert resp.pyquery.find('table.pricing-min-matrix- tr.pricing-row-crit-3-2 th')[0].text == 'Crit 3-2'
    assert resp.pyquery.find('table.pricing-min-matrix- tr.pricing-row-crit-3-3 th')[0].text == 'Crit 3-3'
    assert resp.pyquery.find('table.pricing-min-matrix- tr.pricing-row-crit-3-4 th')[0].text == 'Crit 3-4'
    assert resp.pyquery.find('table.pricing-min-matrix- tr.pricing-row-crit-3-1 td')[0].text == '1.11'
    assert (
        resp.pyquery.find('table.pricing-min-matrix- tr.pricing-row-crit-3-2 td')[0].text is None
    )  # not defined
    assert (
        resp.pyquery.find('table.pricing-min-matrix- tr.pricing-row-crit-3-3 td')[0].text is None
    )  # wrong value
    assert resp.pyquery.find('table.pricing-min-matrix- tr.pricing-row-crit-3-4 td')[0].text == '1.14'


@mock.patch('lingo.pricing.forms.get_event')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_detail_pricing_test_tool_for_event(mock_pricing_data_event, mock_event, app, admin_user):
    agenda = Agenda.objects.create(label='Foo bar')
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    pricing.agendas.add(agenda)

    app = login(app)
    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    assert 'billing_date' not in resp.context['test_tool_form'].fields
    assert 'Computed pricing data' not in resp

    # check event date
    mock_event.return_value = {'start_datetime': '2021-08-31T12:00:00+02:00'}
    resp.form['agenda'] = agenda.pk
    resp.form['event_slug'] = 'foo'
    resp.form['user_external_id'] = 'user:1'
    resp.form['payer_external_id'] = 'payer:1'
    resp.form['booking_status'] = 'presence'
    resp = resp.form.submit().follow()
    assert 'Computed pricing data' not in resp
    assert resp.context['test_tool_form'].errors['event_slug'] == [
        'This event takes place outside the period covered by this pricing'
    ]
    assert mock_pricing_data_event.call_args_list == []
    mock_event.return_value = {'start_datetime': '2021-10-01T12:00:00+02:00'}
    resp = resp.form.submit().follow()
    assert 'Computed pricing data' not in resp
    assert resp.context['test_tool_form'].errors['event_slug'] == [
        'This event takes place outside the period covered by this pricing'
    ]

    mock_event.return_value = {'start_datetime': '2021-09-01T12:00:00+02:00'}
    mock_pricing_data_event.return_value = {'foo': 'bar', 'pricing': Decimal('42')}
    resp = resp.form.submit().follow()
    assert 'Computed pricing data' in resp
    assert mock_pricing_data_event.call_args_list == [
        mock.call(
            request=mock.ANY,
            agenda=agenda,
            event={'start_datetime': '2021-09-01T12:00:00+02:00'},
            check_status={'status': 'presence', 'check_type': None},
            user_external_id='user:1',
            payer_external_id='payer:1',
        )
    ]

    assert '<p>Pricing: 42.00</p>' in resp
    assert (
        '<pre>{&#x27;foo&#x27;: &#x27;bar&#x27;, &#x27;pricing&#x27;: Decimal(&#x27;42&#x27;)}</pre>' in resp
    )

    mock_pricing_data_event.side_effect = PricingDataError(
        details={'criterias': {'qf': 'qf-1', 'foo': 'bar'}}
    )
    resp = resp.form.submit().follow()
    assert 'Computed pricing data' in resp

    assert (
        '<p>Error: Impossible to determine a pricing for criterias: qf-1 (category: qf), bar (category: foo)</p>'
        in resp
    )
    assert '{&#x27;error&#x27;: PricingDataError(),' in resp
    assert (
        '&#x27;error_details&#x27;: {&#x27;criterias&#x27;: {&#x27;foo&#x27;: &#x27;bar&#x27;, &#x27;qf&#x27;: &#x27;qf-1&#x27;}}}'
        in resp
    )

    # check recurring event
    mock_pricing_data_event.reset_mock()
    mock_event.return_value = {'start_datetime': '2021-09-15T12:00:00+02:00', 'recurrence_days': [1]}
    mock_pricing_data_event.return_value = {'foo': 'bar', 'pricing': Decimal('42')}
    resp = resp.form.submit().follow()
    assert 'Computed pricing data' in resp
    assert mock_pricing_data_event.call_args_list == [
        mock.call(
            request=mock.ANY,
            agenda=agenda,
            event={'start_datetime': '2021-09-01T00:00:00+02:00', 'recurrence_days': [1]},
            check_status={'status': 'presence', 'check_type': None},
            user_external_id='user:1',
            payer_external_id='payer:1',
        )
    ]


@mock.patch('lingo.pricing.forms.get_event')
def test_detail_pricing_test_tool_for_event_event_error(mock_event, app, admin_user):
    agenda = Agenda.objects.create(label='Foo bar')
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    pricing.agendas.add(agenda)

    mock_event.side_effect = ChronoError('foo bar foo-event')

    app = login(app)
    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    resp.form['agenda'] = agenda.pk
    resp.form['event_slug'] = 'foo-event'
    resp.form['user_external_id'] = 'user:1'
    resp.form['payer_external_id'] = 'payer:1'
    resp.form['booking_status'] = 'presence'
    resp = resp.form.submit().follow()
    assert resp.context['test_tool_form'].errors['event_slug'] == ['foo bar foo-event']


@mock.patch('lingo.pricing.forms.get_event')
@mock.patch('lingo.pricing.models.Pricing.get_pricing_data_for_event')
def test_detail_pricing_test_tool_for_event_booking_status(
    mock_pricing_data_event, mock_event, app, admin_user
):
    agenda = Agenda.objects.create(label='Foo bar')
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    pricing.agendas.add(agenda)

    mock_event.return_value = {'start_datetime': '2021-09-01T12:00:00+02:00'}

    app = login(app)
    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    assert resp.form['booking_status'].options == [
        ('presence', False, 'Presence'),
        ('absence', False, 'Absence'),
    ]

    group = CheckTypeGroup.objects.create(label='Foo bar')
    CheckType.objects.create(label='Foo presence reason', group=group, kind='presence')
    CheckType.objects.create(label='Foo absence reason', group=group, kind='absence')
    agenda.check_type_group = group
    agenda.save()

    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    assert resp.form['booking_status'].options == [
        ('presence', False, 'Presence'),
        ('absence', False, 'Absence'),
    ]
    resp.form['agenda'] = agenda.pk
    resp.form['event_slug'] = 'foo'
    resp.form['user_external_id'] = 'user:1'
    resp.form['payer_external_id'] = 'payer:1'
    resp.form['booking_status'] = 'presence'
    resp = resp.form.submit().follow()
    assert resp.form['booking_status'].options == [
        ('presence', True, 'Presence'),
        ('presence::foo-presence-reason', False, 'Presence (Foo presence reason)'),
        ('absence', False, 'Absence'),
        ('absence::foo-absence-reason', False, 'Absence (Foo absence reason)'),
    ]
    assert mock_pricing_data_event.call_args_list[0][1]['check_status'] == {
        'check_type': None,
        'status': 'presence',
    }

    mock_pricing_data_event.reset_mock()
    resp.form['booking_status'] = 'presence::foo-presence-reason'
    resp = resp.form.submit().follow()
    assert mock_pricing_data_event.call_args_list[0][1]['check_status'] == {
        'check_type': 'foo-presence-reason',
        'status': 'presence',
    }

    mock_pricing_data_event.reset_mock()
    resp.form['booking_status'] = 'absence'
    resp = resp.form.submit().follow()
    assert mock_pricing_data_event.call_args_list[0][1]['check_status'] == {
        'check_type': None,
        'status': 'absence',
    }

    mock_pricing_data_event.reset_mock()
    resp.form['booking_status'] = 'absence::foo-absence-reason'
    resp = resp.form.submit().follow()
    assert mock_pricing_data_event.call_args_list[0][1]['check_status'] == {
        'check_type': 'foo-absence-reason',
        'status': 'absence',
    }


@mock.patch('lingo.pricing.models.Pricing.get_pricing_data')
def test_detail_pricing_test_tool_for_flat_fee_schedule(mock_pricing_data, app, admin_user):
    agenda = Agenda.objects.create(label='Foo bar')
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
        flat_fee_schedule=True,
        subscription_required=True,
    )
    pricing.agendas.add(agenda)

    app = login(app)
    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    assert 'event_slug' not in resp.context['test_tool_form'].fields
    assert 'booking_status' not in resp.context['test_tool_form'].fields
    assert 'billing_date' not in resp.context['test_tool_form'].fields
    assert 'Computed pricing data' not in resp

    resp.form['agenda'] = agenda.pk
    resp.form['user_external_id'] = 'user:1'
    resp.form['payer_external_id'] = 'payer:1'
    mock_pricing_data.return_value = {'foo': 'bar', 'pricing': Decimal('42')}
    resp = resp.form.submit().follow()
    assert 'Computed pricing data' in resp
    assert mock_pricing_data.call_args_list == [
        mock.call(
            request=mock.ANY,
            pricing_date=datetime.date(2021, 9, 1),
            user_external_id='user:1',
            payer_external_id='payer:1',
        )
    ]
    assert '<p>Pricing: 42.00</p>' in resp
    assert (
        '<pre>{&#x27;foo&#x27;: &#x27;bar&#x27;, &#x27;pricing&#x27;: Decimal(&#x27;42&#x27;)}</pre>' in resp
    )

    mock_pricing_data.side_effect = PricingDataError(details={'criterias': {'qf': 'qf-1', 'foo': 'bar'}})
    resp = resp.form.submit().follow()
    assert 'Computed pricing data' in resp

    assert (
        '<p>Error: Impossible to determine a pricing for criterias: qf-1 (category: qf), bar (category: foo)</p>'
        in resp
    )
    assert '{&#x27;error&#x27;: PricingDataError(),' in resp
    assert (
        '&#x27;error_details&#x27;: {&#x27;criterias&#x27;: {&#x27;foo&#x27;: &#x27;bar&#x27;, &#x27;qf&#x27;: &#x27;qf-1&#x27;}}}'
        in resp
    )

    billing_date1 = pricing.billingdates.create(
        date_start=datetime.date(2021, 9, 1),
        label='Foo 1',
    )
    billing_date2 = pricing.billingdates.create(
        date_start=datetime.date(2021, 9, 15),
        label='Foo 2',
    )

    # check with billing dates
    mock_pricing_data.reset_mock()
    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    resp.form['agenda'] = agenda.pk
    resp.form['billing_date'] = billing_date1.pk
    resp.form['user_external_id'] = 'user:1'
    resp.form['payer_external_id'] = 'payer:1'
    resp = resp.form.submit().follow()
    assert mock_pricing_data.call_args_list == [
        mock.call(
            request=mock.ANY,
            pricing_date=datetime.date(2021, 9, 1),
            user_external_id='user:1',
            payer_external_id='payer:1',
        )
    ]

    mock_pricing_data.reset_mock()
    resp.form['billing_date'] = billing_date2.pk
    resp = resp.form.submit().follow()
    assert mock_pricing_data.call_args_list == [
        mock.call(
            request=mock.ANY,
            pricing_date=datetime.date(2021, 9, 15),
            user_external_id='user:1',
            payer_external_id='payer:1',
        )
    ]

    # check with subscription_required False
    pricing.subscription_required = False
    pricing.save()
    mock_pricing_data.reset_mock()
    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    assert 'agenda' not in resp.context['test_tool_form'].fields
    resp.form['billing_date'] = billing_date1.pk
    resp.form['user_external_id'] = 'user:1'
    resp.form['payer_external_id'] = 'payer:1'
    resp = resp.form.submit().follow()
    assert mock_pricing_data.call_args_list == [
        mock.call(
            request=mock.ANY,
            pricing_date=datetime.date(2021, 9, 1),
            user_external_id='user:1',
            payer_external_id='payer:1',
        )
    ]


def test_edit_pricing_matrix_3_categories(app, admin_user):
    category1 = CriteriaCategory.objects.create(label='Cat 1')
    criteria11 = Criteria.objects.create(label='Crit 1-1', slug='crit-1-1', category=category1, order=1)
    criteria12 = Criteria.objects.create(label='Crit 1-2', slug='crit-1-2', category=category1, order=2)
    category2 = CriteriaCategory.objects.create(label='Cat 2')
    criteria21 = Criteria.objects.create(label='Crit 2-1', slug='crit-2-1', category=category2, order=1)
    Criteria.objects.create(label='Crit 2-2', slug='crit-2-2', category=category2, order=2)
    Criteria.objects.create(label='Crit 2-3', slug='crit-2-3', category=category2, order=3)
    category3 = CriteriaCategory.objects.create(label='Cat 3')
    criteria31 = Criteria.objects.create(label='Crit 3-1', slug='crit-3-1', category=category3, order=1)
    Criteria.objects.create(label='Crit 3-3', slug='crit-3-3', category=category3, order=3)
    Criteria.objects.create(label='Crit 3-4', slug='crit-3-4', category=category3, order=4)
    Criteria.objects.create(label='Crit 3-2', slug='crit-3-2', category=category3, order=2)

    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
        pricing_data={
            'cat-3:crit-3-1': {
                'cat-2:crit-2-1': {
                    'cat-1:crit-1-1': '111',
                },
            },
            'cat-3:crit-3-2': {
                'cat-2:crit-2-3': {
                    'cat-1:crit-1-1': '132',
                },
            },
            'cat-3:crit-3-3': {
                'cat-2:crit-2-1': {
                    'cat-1:crit-1-1': 'not-a-decimal',
                },
                'cat-2:crit-2-2': {
                    'cat-1:crit-1-2': '223',
                },
            },
            'cat-3:crit-3-4': {
                'cat-2:crit-2-1': {
                    'cat-1:crit-1-1': '114',
                },
            },
        },
    )
    pricing.categories.add(category1, through_defaults={'order': 1})
    pricing.categories.add(category2, through_defaults={'order': 2})
    pricing.categories.add(category3, through_defaults={'order': 3})
    pricing.criterias.set(Criteria.objects.all())

    app = login(app)
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    resp = resp.click(href='/manage/pricing/%s/matrix/%s/edit/' % (pricing.pk, criteria11.slug))
    assert resp.form['form-0-crit_0'].value == '111'
    assert resp.form['form-0-crit_1'].value == ''
    assert resp.form['form-0-crit_2'].value == ''
    assert resp.form['form-1-crit_0'].value == ''
    assert resp.form['form-1-crit_1'].value == ''
    assert resp.form['form-1-crit_2'].value == '132'
    assert resp.form['form-2-crit_0'].value == ''
    assert resp.form['form-2-crit_1'].value == ''
    assert resp.form['form-2-crit_2'].value == ''
    assert resp.form['form-3-crit_0'].value == '114'
    assert resp.form['form-3-crit_1'].value == ''
    assert resp.form['form-3-crit_2'].value == ''
    resp.form['form-0-crit_1'] = '121'
    resp.form['form-0-crit_2'] = '131'
    resp.form['form-1-crit_0'] = '112'
    resp.form['form-1-crit_1'] = '122'
    resp.form['form-1-crit_2'] = '132.5'
    resp.form['form-2-crit_0'] = '113'
    resp.form['form-2-crit_1'] = '123'
    resp.form['form-2-crit_2'] = '133'
    resp.form['form-3-crit_0'] = '0'
    resp.form['form-3-crit_1'] = '124'
    resp.form['form-3-crit_2'] = '134'
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/%s/#open:matrix' % pricing.pk)
    pricing.refresh_from_db()
    assert pricing.pricing_data == {
        'cat-1:crit-1-1': {
            'cat-2:crit-2-1': {
                'cat-3:crit-3-1': '111.0',
                'cat-3:crit-3-2': '112.0',
                'cat-3:crit-3-3': '113.0',
                'cat-3:crit-3-4': '0.0',
            },
            'cat-2:crit-2-2': {
                'cat-3:crit-3-1': '121.0',
                'cat-3:crit-3-2': '122.0',
                'cat-3:crit-3-3': '123.0',
                'cat-3:crit-3-4': '124.0',
            },
            'cat-2:crit-2-3': {
                'cat-3:crit-3-1': '131.0',
                'cat-3:crit-3-2': '132.5',
                'cat-3:crit-3-3': '133.0',
                'cat-3:crit-3-4': '134.0',
            },
        },
        'cat-1:crit-1-2': {
            'cat-2:crit-2-2': {
                'cat-3:crit-3-3': '223',
            },
        },
    }
    assert PricingSnapshot.objects.count() == 1
    app.get(
        '/manage/pricing/%s/matrix/%s/edit/' % (pricing.pk, criteria12.slug),
        status=200,
    )

    app.get('/manage/pricing/%s/matrix/%s/edit/' % (0, criteria11.slug), status=404)
    app.get(
        '/manage/pricing/%s/matrix/%s/edit/' % (pricing.pk, 'unknown'),
        status=404,
    )
    app.get(
        '/manage/pricing/%s/matrix/%s/edit/' % (pricing.pk, criteria21.slug),
        status=404,
    )
    app.get(
        '/manage/pricing/%s/matrix/%s/edit/' % (pricing.pk, criteria31.slug),
        status=404,
    )
    app.get('/manage/pricing/%s/matrix/edit/' % pricing.pk, status=404)

    app.get(
        '/manage/pricing/%s/matrix/%s/edit/min/' % (pricing.pk, criteria11.slug),
        status=404,
    )

    pricing.kind = 'effort'
    pricing.save()
    resp = app.get('/manage/pricing/%s/matrix/%s/edit/' % (pricing.pk, criteria11.slug))
    resp.form['form-0-crit_0'].value = '111.9999'
    resp = resp.form.submit()
    pricing.refresh_from_db()
    assert pricing.pricing_data['cat-1:crit-1-1']['cat-2:crit-2-1']['cat-3:crit-3-1'] == '111.9999'
    assert PricingSnapshot.objects.count() == 2
    app.get(
        '/manage/pricing/%s/matrix/%s/edit/min/' % (pricing.pk, criteria11.slug),
        status=404,
    )

    pricing.kind = 'reduction'
    pricing.min_pricing_data = {
        'cat-1:crit-1-1': {
            'cat-2:crit-2-1': {
                'cat-3:crit-3-1': 1.11,
                'cat-3:crit-3-3': 'not-a-decimal',
                'cat-3:crit-3-4': 1.14,
            },
            'cat-2:crit-2-3': {
                'cat-3:crit-3-2': 1.32,
            },
        },
        'cat-1:crit-1-2': {
            'cat-2:crit-2-2': {
                'cat-3:crit-3-3': 2.23,
            },
        },
    }
    pricing.save()
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    resp = resp.click(href='/manage/pricing/%s/matrix/%s/edit/min/' % (pricing.pk, criteria11.slug))
    assert resp.form['form-0-crit_0'].value == '1.11'
    assert resp.form['form-0-crit_1'].value == ''
    assert resp.form['form-0-crit_2'].value == ''
    assert resp.form['form-1-crit_0'].value == ''
    assert resp.form['form-1-crit_1'].value == ''
    assert resp.form['form-1-crit_2'].value == '1.32'
    assert resp.form['form-2-crit_0'].value == ''
    assert resp.form['form-2-crit_1'].value == ''
    assert resp.form['form-2-crit_2'].value == ''
    assert resp.form['form-3-crit_0'].value == '1.14'
    assert resp.form['form-3-crit_1'].value == ''
    assert resp.form['form-3-crit_2'].value == ''
    resp.form['form-0-crit_1'] = '1.21'
    resp.form['form-0-crit_2'] = '1.31'
    resp.form['form-1-crit_0'] = '1.12'
    resp.form['form-1-crit_1'] = '1.22'
    resp.form['form-1-crit_2'] = '1.32'
    resp.form['form-2-crit_0'] = '1.13'
    resp.form['form-2-crit_1'] = '1.23'
    resp.form['form-2-crit_2'] = '1.33'
    resp.form['form-3-crit_0'] = '9.14'
    resp.form['form-3-crit_1'] = '1.24'
    resp.form['form-3-crit_2'] = '1.34'
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/%s/#open:min-matrix' % pricing.pk)
    pricing.refresh_from_db()
    assert pricing.min_pricing_data == {
        'cat-1:crit-1-1': {
            'cat-2:crit-2-1': {
                'cat-3:crit-3-1': '1.11',
                'cat-3:crit-3-2': '1.12',
                'cat-3:crit-3-3': '1.13',
                'cat-3:crit-3-4': '9.14',
            },
            'cat-2:crit-2-2': {
                'cat-3:crit-3-1': '1.21',
                'cat-3:crit-3-2': '1.22',
                'cat-3:crit-3-3': '1.23',
                'cat-3:crit-3-4': '1.24',
            },
            'cat-2:crit-2-3': {
                'cat-3:crit-3-1': '1.31',
                'cat-3:crit-3-2': '1.32',
                'cat-3:crit-3-3': '1.33',
                'cat-3:crit-3-4': '1.34',
            },
        },
        'cat-1:crit-1-2': {
            'cat-2:crit-2-2': {
                'cat-3:crit-3-3': '2.23',
            },
        },
    }
    assert PricingSnapshot.objects.count() == 3
    app.get(
        '/manage/pricing/%s/matrix/%s/edit/min/' % (pricing.pk, criteria12.slug),
        status=200,
    )

    app.get('/manage/pricing/%s/matrix/%s/edit/min/' % (0, criteria11.slug), status=404)
    app.get(
        '/manage/pricing/%s/matrix/%s/edit/min/' % (pricing.pk, 'unknown'),
        status=404,
    )
    app.get(
        '/manage/pricing/%s/matrix/%s/edit/min/' % (pricing.pk, criteria21.slug),
        status=404,
    )
    app.get(
        '/manage/pricing/%s/matrix/%s/edit/min/' % (pricing.pk, criteria31.slug),
        status=404,
    )
    app.get('/manage/pricing/%s/matrix/edit/min/' % pricing.pk, status=404)


def test_edit_pricing_matrix_2_categories(app, admin_user):
    category2 = CriteriaCategory.objects.create(label='Cat 2')
    criteria21 = Criteria.objects.create(label='Crit 2-1', slug='crit-2-1', category=category2, order=1)
    Criteria.objects.create(label='Crit 2-2', slug='crit-2-2', category=category2, order=2)
    Criteria.objects.create(label='Crit 2-3', slug='crit-2-3', category=category2, order=3)
    category3 = CriteriaCategory.objects.create(label='Cat 3')
    criteria31 = Criteria.objects.create(label='Crit 3-1', slug='crit-3-1', category=category3, order=1)
    Criteria.objects.create(label='Crit 3-3', slug='crit-3-3', category=category3, order=3)
    Criteria.objects.create(label='Crit 3-4', slug='crit-3-4', category=category3, order=4)
    Criteria.objects.create(label='Crit 3-2', slug='crit-3-2', category=category3, order=2)

    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
        pricing_data={
            'cat-2:crit-2-1': {
                'cat-3:crit-3-1': 111,
                'cat-3:crit-3-3': 'not-a-decimal',
                'cat-3:crit-3-4': 114,
            },
            'cat-2:crit-2-3': {
                'cat-3:crit-3-2': 132,
            },
        },
    )
    pricing.categories.add(category2, through_defaults={'order': 1})
    pricing.categories.add(category3, through_defaults={'order': 2})
    pricing.criterias.set(Criteria.objects.all())

    app = login(app)
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    resp = resp.click(href='/manage/pricing/%s/matrix/edit/' % pricing.pk)
    assert resp.form['form-0-crit_0'].value == '111'
    assert resp.form['form-0-crit_1'].value == ''
    assert resp.form['form-0-crit_2'].value == ''
    assert resp.form['form-1-crit_0'].value == ''
    assert resp.form['form-1-crit_1'].value == ''
    assert resp.form['form-1-crit_2'].value == '132'
    assert resp.form['form-2-crit_0'].value == ''
    assert resp.form['form-2-crit_1'].value == ''
    assert resp.form['form-2-crit_2'].value == ''
    assert resp.form['form-3-crit_0'].value == '114'
    assert resp.form['form-3-crit_1'].value == ''
    assert resp.form['form-3-crit_2'].value == ''
    resp.form['form-0-crit_1'] = '121'
    resp.form['form-0-crit_2'] = '131'
    resp.form['form-1-crit_0'] = '112'
    resp.form['form-1-crit_1'] = '122'
    resp.form['form-1-crit_2'] = '132.5'
    resp.form['form-2-crit_0'] = '113'
    resp.form['form-2-crit_1'] = '123'
    resp.form['form-2-crit_2'] = '133'
    resp.form['form-3-crit_0'] = '914'
    resp.form['form-3-crit_1'] = '124'
    resp.form['form-3-crit_2'] = '134'
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/%s/#open:matrix' % pricing.pk)
    pricing.refresh_from_db()
    assert pricing.pricing_data == {
        'cat-2:crit-2-1': {
            'cat-3:crit-3-1': '111.0',
            'cat-3:crit-3-2': '112.0',
            'cat-3:crit-3-3': '113.0',
            'cat-3:crit-3-4': '914.0',
        },
        'cat-2:crit-2-2': {
            'cat-3:crit-3-1': '121.0',
            'cat-3:crit-3-2': '122.0',
            'cat-3:crit-3-3': '123.0',
            'cat-3:crit-3-4': '124.0',
        },
        'cat-2:crit-2-3': {
            'cat-3:crit-3-1': '131.0',
            'cat-3:crit-3-2': '132.5',
            'cat-3:crit-3-3': '133.0',
            'cat-3:crit-3-4': '134.0',
        },
    }
    assert PricingSnapshot.objects.count() == 1

    app.get('/manage/pricing/%s/matrix/edit/' % 0, status=404)
    app.get(
        '/manage/pricing/%s/matrix/%s/edit/' % (pricing.pk, criteria21.slug),
        status=404,
    )
    app.get(
        '/manage/pricing/%s/matrix/%s/edit/' % (pricing.pk, criteria31.slug),
        status=404,
    )

    app.get(
        '/manage/pricing/%s/matrix/edit/min/' % pricing.pk,
        status=404,
    )

    pricing.kind = 'effort'
    pricing.save()
    resp = app.get('/manage/pricing/%s/matrix/edit/' % pricing.pk)
    resp.form['form-0-crit_0'].value = '111.9999'
    resp = resp.form.submit()
    pricing.refresh_from_db()
    assert pricing.pricing_data['cat-2:crit-2-1']['cat-3:crit-3-1'] == '111.9999'
    assert PricingSnapshot.objects.count() == 2
    app.get(
        '/manage/pricing/%s/matrix/edit/min/' % pricing.pk,
        status=404,
    )

    pricing.kind = 'reduction'
    pricing.min_pricing_data = {
        'cat-2:crit-2-1': {
            'cat-3:crit-3-1': 1.11,
            'cat-3:crit-3-3': 'not-a-decimal',
            'cat-3:crit-3-4': 1.14,
        },
        'cat-2:crit-2-3': {
            'cat-3:crit-3-2': 1.32,
        },
    }
    pricing.save()
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    resp = resp.click(href='/manage/pricing/%s/matrix/edit/min/' % pricing.pk)
    assert resp.form['form-0-crit_0'].value == '1.11'
    assert resp.form['form-0-crit_1'].value == ''
    assert resp.form['form-0-crit_2'].value == ''
    assert resp.form['form-1-crit_0'].value == ''
    assert resp.form['form-1-crit_1'].value == ''
    assert resp.form['form-1-crit_2'].value == '1.32'
    assert resp.form['form-2-crit_0'].value == ''
    assert resp.form['form-2-crit_1'].value == ''
    assert resp.form['form-2-crit_2'].value == ''
    assert resp.form['form-3-crit_0'].value == '1.14'
    assert resp.form['form-3-crit_1'].value == ''
    assert resp.form['form-3-crit_2'].value == ''
    resp.form['form-0-crit_1'] = '1.21'
    resp.form['form-0-crit_2'] = '1.31'
    resp.form['form-1-crit_0'] = '1.12'
    resp.form['form-1-crit_1'] = '1.22'
    resp.form['form-1-crit_2'] = '1.32'
    resp.form['form-2-crit_0'] = '1.13'
    resp.form['form-2-crit_1'] = '1.23'
    resp.form['form-2-crit_2'] = '1.33'
    resp.form['form-3-crit_0'] = '9.14'
    resp.form['form-3-crit_1'] = '1.24'
    resp.form['form-3-crit_2'] = '1.34'
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/%s/#open:min-matrix' % pricing.pk)
    pricing.refresh_from_db()
    assert pricing.min_pricing_data == {
        'cat-2:crit-2-1': {
            'cat-3:crit-3-1': '1.11',
            'cat-3:crit-3-2': '1.12',
            'cat-3:crit-3-3': '1.13',
            'cat-3:crit-3-4': '9.14',
        },
        'cat-2:crit-2-2': {
            'cat-3:crit-3-1': '1.21',
            'cat-3:crit-3-2': '1.22',
            'cat-3:crit-3-3': '1.23',
            'cat-3:crit-3-4': '1.24',
        },
        'cat-2:crit-2-3': {
            'cat-3:crit-3-1': '1.31',
            'cat-3:crit-3-2': '1.32',
            'cat-3:crit-3-3': '1.33',
            'cat-3:crit-3-4': '1.34',
        },
    }
    assert PricingSnapshot.objects.count() == 3

    app.get('/manage/pricing/%s/matrix/edit/min/' % 0, status=404)
    app.get(
        '/manage/pricing/%s/matrix/%s/edit/min/' % (pricing.pk, criteria21.slug),
        status=404,
    )
    app.get(
        '/manage/pricing/%s/matrix/%s/edit/min/' % (pricing.pk, criteria31.slug),
        status=404,
    )


def test_edit_pricing_matrix_1_category(app, admin_user):
    category3 = CriteriaCategory.objects.create(label='Cat 3')
    criteria31 = Criteria.objects.create(label='Crit 3-1', slug='crit-3-1', category=category3, order=1)
    Criteria.objects.create(label='Crit 3-3', slug='crit-3-3', category=category3, order=3)
    Criteria.objects.create(label='Crit 3-4', slug='crit-3-4', category=category3, order=4)
    Criteria.objects.create(label='Crit 3-2', slug='crit-3-2', category=category3, order=2)

    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
        pricing_data={
            'cat-3:crit-3-1': 111,
            'cat-3:crit-3-3': 'not-a-decimal',
            'cat-3:crit-3-4': 114,
        },
    )
    pricing.categories.add(category3, through_defaults={'order': 3})
    pricing.criterias.set(Criteria.objects.all())

    app = login(app)
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    resp = resp.click(href='/manage/pricing/%s/matrix/edit/' % pricing.pk)
    assert resp.form['form-0-crit_0'].value == '111'
    assert resp.form['form-1-crit_0'].value == ''
    assert resp.form['form-2-crit_0'].value == ''
    assert resp.form['form-3-crit_0'].value == '114'
    resp.form['form-1-crit_0'] = '112.5'
    resp.form['form-2-crit_0'] = '113'
    resp.form['form-3-crit_0'] = '914'
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/%s/#open:matrix' % pricing.pk)
    pricing.refresh_from_db()
    assert pricing.pricing_data == {
        'cat-3:crit-3-1': '111.0',
        'cat-3:crit-3-2': '112.5',
        'cat-3:crit-3-3': '113.0',
        'cat-3:crit-3-4': '914.0',
    }
    assert PricingSnapshot.objects.count() == 1

    app.get('/manage/pricing/%s/matrix/edit/' % 0, status=404)
    app.get(
        '/manage/pricing/%s/matrix/%s/edit/' % (pricing.pk, criteria31.slug),
        status=404,
    )

    app.get('/manage/pricing/%s/matrix/edit/min/' % pricing.pk, status=404)

    pricing.kind = 'effort'
    pricing.save()
    resp = app.get('/manage/pricing/%s/matrix/edit/' % pricing.pk)
    resp.form['form-0-crit_0'].value = '111.9999'
    resp = resp.form.submit()
    pricing.refresh_from_db()
    assert pricing.pricing_data['cat-3:crit-3-1'] == '111.9999'
    assert PricingSnapshot.objects.count() == 2
    app.get('/manage/pricing/%s/matrix/edit/min/' % pricing.pk, status=404)

    pricing.kind = 'reduction'
    pricing.min_pricing_data = {
        'cat-3:crit-3-1': 1.11,
        'cat-3:crit-3-3': 'not-a-decimal',
        'cat-3:crit-3-4': 1.14,
    }
    pricing.save()
    resp = app.get('/manage/pricing/%s/' % pricing.pk)
    resp = resp.click(href='/manage/pricing/%s/matrix/edit/min/' % pricing.pk)
    assert resp.form['form-0-crit_0'].value == '1.11'
    assert resp.form['form-1-crit_0'].value == ''
    assert resp.form['form-2-crit_0'].value == ''
    assert resp.form['form-3-crit_0'].value == '1.14'
    resp.form['form-1-crit_0'] = '1.12'
    resp.form['form-2-crit_0'] = '1.13'
    resp.form['form-3-crit_0'] = '9.14'
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/pricing/%s/#open:min-matrix' % pricing.pk)
    pricing.refresh_from_db()
    assert pricing.min_pricing_data == {
        'cat-3:crit-3-1': '1.11',
        'cat-3:crit-3-2': '1.12',
        'cat-3:crit-3-3': '1.13',
        'cat-3:crit-3-4': '9.14',
    }
    assert PricingSnapshot.objects.count() == 3

    app.get('/manage/pricing/%s/matrix/edit/min/' % 0, status=404)
    app.get(
        '/manage/pricing/%s/matrix/%s/edit/min/' % (pricing.pk, criteria31.slug),
        status=404,
    )


def test_edit_pricing_matrix_empty(app, admin_user):
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )

    app = login(app)
    app.get('/manage/pricing/%s/matrix/edit/' % pricing.pk, status=404)


def test_pricing_inspect(app, admin_user):
    category3 = CriteriaCategory.objects.create(label='Cat 3')
    Criteria.objects.create(label='Crit 3-1', slug='crit-3-1', category=category3, order=1)
    Criteria.objects.create(label='Crit 3-3', slug='crit-3-3', category=category3, order=3)
    Criteria.objects.create(label='Crit 3-4', slug='crit-3-4', category=category3, order=4)
    Criteria.objects.create(label='Crit 3-2', slug='crit-3-2', category=category3, order=2)

    group_foo = Group.objects.create(name='role-foo')
    group_bar = Group.objects.create(name='role-bar')
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
        pricing_data={
            'cat-3:crit-3-1': 111,
            'cat-3:crit-3-3': 'not-a-decimal',
            'cat-3:crit-3-4': 114,
        },
        edit_role=group_foo,
        view_role=group_bar,
    )
    pricing.categories.add(category3, through_defaults={'order': 3})
    pricing.criterias.set(Criteria.objects.all())
    agenda = Agenda.objects.create(label='Foo bar')
    pricing.agendas.add(agenda)

    app = login(app)
    resp = app.get('/manage/pricing/%s/parameters/' % pricing.pk)
    with CaptureQueriesContext(connection) as ctx:
        resp = resp.click(href='/manage/pricing/%s/inspect/' % pricing.pk)
        assert len(ctx.captured_queries) == 7

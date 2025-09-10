import datetime
import decimal
import json
from unittest import mock

import pytest
from django.template import Context
from django.test.client import RequestFactory
from django.utils.timezone import make_aware
from publik_django_templatetags.wcs.context_processors import Cards

from lingo.agendas.models import Agenda, CheckType, CheckTypeGroup
from lingo.pricing.errors import (
    CriteriaConditionNotFound,
    MinPricingDataError,
    MinPricingDataFormatError,
    MultipleDefaultCriteriaCondition,
    PricingAccountingCodeError,
    PricingBookingCheckTypeError,
    PricingBookingNotCheckedError,
    PricingDataError,
    PricingDataFormatError,
    PricingEffortRateTargetError,
    PricingEffortRateTargetFormatError,
    PricingEffortRateTargetValueError,
    PricingError,
    PricingEventNotCheckedError,
    PricingMultipleBookingError,
    PricingNotFound,
    PricingReductionRateError,
    PricingReductionRateFormatError,
    PricingReductionRateValueError,
    PricingUnknownCheckStatusError,
)
from lingo.pricing.models import (
    Criteria,
    CriteriaCategory,
    Pricing,
    PricingCriteriaCategory,
    PricingMatrix,
    PricingMatrixCell,
    PricingMatrixRow,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def context():
    return Context(
        {
            'cards': Cards(),
            'request': RequestFactory().get('/'),
        }
    )


class MockedRequestResponse(mock.Mock):
    status_code = 200

    def json(self):
        return json.loads(self.content)


def mocked_requests_send(request, **kwargs):
    data = [
        {
            'id': 1,
            'fields': {'foo': 'bar', 'bar': False, 'rate': 42, 'target': 2000, 'accounting_code': '424242'},
        },
        {
            'id': 2,
            'fields': {'foo': 'baz', 'bar': True, 'rate': 35, 'target': 3000, 'accounting_code': '353535'},
        },
    ]  # fake result
    return MockedRequestResponse(content=json.dumps({'data': data}))


def test_criteria_category_slug():
    category = CriteriaCategory.objects.create(label='Foo bar')
    assert category.slug == 'foo-bar'


def test_criteria_category_existing_slug():
    category = CriteriaCategory.objects.create(label='Foo bar', slug='bar')
    assert category.slug == 'bar'


def test_criteria_category_duplicate_slugs():
    category = CriteriaCategory.objects.create(label='Foo baz')
    assert category.slug == 'foo-baz'
    category = CriteriaCategory.objects.create(label='Foo baz')
    assert category.slug == 'foo-baz-1'
    category = CriteriaCategory.objects.create(label='Foo baz')
    assert category.slug == 'foo-baz-2'


def test_criteria_slug():
    category = CriteriaCategory.objects.create(label='Foo')
    criteria = Criteria.objects.create(label='Foo bar', category=category)
    assert criteria.slug == 'foo-bar'


def test_criteria_existing_slug():
    category = CriteriaCategory.objects.create(label='Foo')
    criteria = Criteria.objects.create(label='Foo bar', slug='bar', category=category)
    assert criteria.slug == 'bar'


def test_criteria_duplicate_slugs():
    category = CriteriaCategory.objects.create(label='Foo')
    category2 = CriteriaCategory.objects.create(label='Bar')
    Criteria.objects.create(label='Foo baz', slug='foo-baz', category=category2)
    criteria = Criteria.objects.create(label='Foo baz', category=category)
    assert criteria.slug == 'foo-baz'
    criteria = Criteria.objects.create(label='Foo baz', category=category)
    assert criteria.slug == 'foo-baz-1'
    criteria = Criteria.objects.create(label='Foo baz', category=category)
    assert criteria.slug == 'foo-baz-2'


def test_criteria_order():
    category = CriteriaCategory.objects.create(label='Foo')
    criteria = Criteria.objects.create(label='Foo bar', category=category)
    assert criteria.order == 1
    criteria = Criteria.objects.create(label='Foo bar', category=category, default=True)
    assert criteria.order == 0


def test_criteria_existing_order():
    category = CriteriaCategory.objects.create(label='Foo')
    criteria = Criteria.objects.create(label='Foo bar', order=42, category=category)
    assert criteria.order == 42
    criteria = Criteria.objects.create(label='Foo bar', order=42, category=category, default=True)
    assert criteria.order == 0


def test_criteria_duplicate_orders():
    category = CriteriaCategory.objects.create(label='Foo')
    category2 = CriteriaCategory.objects.create(label='Bar')
    Criteria.objects.create(label='Foo baz', order=1, category=category2)
    criteria = Criteria.objects.create(label='Foo baz', category=category)
    assert criteria.order == 1
    criteria = Criteria.objects.create(label='Foo baz', category=category)
    assert criteria.order == 2
    criteria = Criteria.objects.create(label='Foo baz', category=category)
    assert criteria.order == 3
    criteria.default = True
    criteria.save()
    assert criteria.order == 0
    criteria = Criteria.objects.create(label='Foo baz', category=category, default=True)
    assert criteria.order == 0


def test_pricing_slug():
    pricing = Pricing.objects.create(
        label='Foo bar',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    assert pricing.slug == 'foo-bar'


def test_pricing_existing_slug():
    pricing = Pricing.objects.create(
        label='Foo bar',
        slug='bar',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    assert pricing.slug == 'bar'


def test_pricing_duplicate_slugs():
    pricing = Pricing.objects.create(
        label='Foo baz',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    assert pricing.slug == 'foo-baz'
    pricing = Pricing.objects.create(
        label='Foo baz',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    assert pricing.slug == 'foo-baz-1'
    pricing = Pricing.objects.create(
        label='Foo baz',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    assert pricing.slug == 'foo-baz-2'


def test_pricing_category_criteria_order():
    category = CriteriaCategory.objects.create(label='Foo')
    pricing = Pricing.objects.create(
        label='Foo baz',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    apcc = PricingCriteriaCategory.objects.create(pricing=pricing, category=category)
    assert apcc.order == 1


def test_pricing_category_criteria_existing_order():
    category = CriteriaCategory.objects.create(label='Foo')
    pricing = Pricing.objects.create(
        label='Foo baz',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    apcc = PricingCriteriaCategory.objects.create(order=42, pricing=pricing, category=category)
    assert apcc.order == 42


def test_pricing_category_criteria_duplicate_orders():
    category1 = CriteriaCategory.objects.create(label='Foo')
    category2 = CriteriaCategory.objects.create(label='Bar')
    category3 = CriteriaCategory.objects.create(label='Baz')
    pricing = Pricing.objects.create(
        label='Foo baz',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    pricing2 = Pricing.objects.create(
        label='Foo baz',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    PricingCriteriaCategory.objects.create(order=1, pricing=pricing2, category=category1)
    PricingCriteriaCategory.objects.create(order=2, pricing=pricing2, category=category2)
    PricingCriteriaCategory.objects.create(order=3, pricing=pricing2, category=category3)
    apcc = PricingCriteriaCategory.objects.create(pricing=pricing, category=category1)
    assert apcc.order == 1
    apcc = PricingCriteriaCategory.objects.create(pricing=pricing, category=category2)
    assert apcc.order == 2
    apcc = PricingCriteriaCategory.objects.create(pricing=pricing, category=category3)
    assert apcc.order == 3


def test_get_pricing():
    agenda = Agenda.objects.create(label='Foo bar')
    start_date = datetime.datetime(2021, 9, 15)

    # not found
    with pytest.raises(PricingNotFound):
        Pricing.get_pricing(
            agenda=agenda,
            start_date=start_date,
            flat_fee_schedule=False,
        )
    with pytest.raises(PricingNotFound):
        Pricing.get_pricing(
            agenda=agenda,
            start_date=start_date,
            flat_fee_schedule=True,
        )

    # ok
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
        flat_fee_schedule=False,
    )
    pricing.agendas.add(agenda)
    pricing2 = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
        flat_fee_schedule=True,
    )
    pricing2.agendas.add(agenda)
    assert (
        Pricing.get_pricing(
            agenda=agenda,
            start_date=start_date,
            flat_fee_schedule=False,
        )
        == pricing
    )
    assert (
        Pricing.get_pricing(
            agenda=agenda,
            start_date=start_date,
            flat_fee_schedule=True,
        )
        == pricing2
    )

    # more than one matching
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=14),
        date_end=datetime.date(year=2021, month=9, day=16),
        flat_fee_schedule=False,
    )
    pricing.agendas.add(agenda)
    pricing2 = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=14),
        date_end=datetime.date(year=2021, month=9, day=16),
        flat_fee_schedule=True,
    )
    pricing2.agendas.add(agenda)
    with pytest.raises(PricingNotFound):
        Pricing.get_pricing(
            agenda=agenda,
            start_date=start_date,
            flat_fee_schedule=False,
        )
        Pricing.get_pricing(
            agenda=agenda,
            start_date=start_date,
            flat_fee_schedule=True,
        )


def test_pricing_duplicate():
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
    not_used = Criteria.objects.create(label='Not used', slug='crit-3-notused', category=category3, order=5)

    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
        flat_fee_schedule=False,
        extra_variables={
            'foo': 'bar',
        },
    )
    pricing.categories.add(category1, through_defaults={'order': 1})
    pricing.categories.add(category2, through_defaults={'order': 2})
    pricing.categories.add(category3, through_defaults={'order': 3})
    pricing.criterias.set(Criteria.objects.exclude(pk=not_used.pk))
    agenda = Agenda.objects.create(label='Foo bar')
    pricing.agendas.add(agenda)

    new_pricing = pricing.duplicate()
    assert new_pricing.label == 'Copy of Foo'
    assert new_pricing.slug == 'copy-of-foo'
    assert new_pricing.extra_variables == pricing.extra_variables
    assert list(new_pricing.agendas.all()) == []
    assert list(new_pricing.criterias.all()) == list(pricing.criterias.all())
    original_apcc_categories = list(
        PricingCriteriaCategory.objects.filter(pricing=pricing).values_list('category', flat=True)
    )
    new_apcc_categories = list(
        PricingCriteriaCategory.objects.filter(pricing=new_pricing).values_list('category', flat=True)
    )
    assert new_apcc_categories == original_apcc_categories
    original_apcc_orders = list(
        PricingCriteriaCategory.objects.filter(pricing=pricing).values_list('order', flat=True)
    )
    new_apcc_orders = list(
        PricingCriteriaCategory.objects.filter(pricing=new_pricing).values_list('order', flat=True)
    )
    assert new_apcc_orders == original_apcc_orders

    new_pricing = pricing.duplicate(
        label='Bar',
        date_start=datetime.date(year=2022, month=9, day=1),
        date_end=datetime.date(year=2023, month=9, day=1),
    )
    assert new_pricing.label == 'Bar'
    assert new_pricing.slug == 'bar'
    assert new_pricing.date_start == datetime.date(year=2022, month=9, day=1)
    assert new_pricing.date_end == datetime.date(year=2023, month=9, day=1)


@pytest.mark.parametrize(
    'start_date, found',
    [
        # just before first day
        ((2021, 8, 31), False),
        # first day
        ((2021, 9, 1), True),
        # last day
        ((2021, 9, 30), True),
        # just after last day
        ((2021, 10, 1), False),
    ],
)
def test_get_pricing_start_date(start_date, found):
    agenda = Agenda.objects.create(label='Foo bar')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
        flat_fee_schedule=False,
    )
    pricing.agendas.add(agenda)
    pricing2 = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
        flat_fee_schedule=True,
    )
    pricing2.agendas.add(agenda)
    start_date = datetime.date(*start_date)
    if found:
        assert (
            Pricing.get_pricing(
                agenda=agenda,
                start_date=start_date,
                flat_fee_schedule=False,
            )
            == pricing
        )
        assert (
            Pricing.get_pricing(
                agenda=agenda,
                start_date=start_date,
                flat_fee_schedule=True,
            )
            == pricing2
        )
    else:
        with pytest.raises(PricingNotFound):
            Pricing.get_pricing(
                agenda=agenda,
                start_date=start_date,
                flat_fee_schedule=False,
            )
            Pricing.get_pricing(
                agenda=agenda,
                start_date=start_date,
                flat_fee_schedule=True,
            )


@mock.patch('requests.Session.send', side_effect=mocked_requests_send)
def test_get_pricing_context(mock_send, context, nocache):
    agenda = Agenda.objects.create(label='Foo bar')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    pricing.agendas.add(agenda)
    assert (
        pricing.get_pricing_context(
            request=context['request'], data={}, user_external_id='child:42', payer_external_id='parent:35'
        )
        == {}
    )
    pricing.extra_variables = {
        'foo': 'bar',
        'qf': '{{ 40|add:2 }}',
        'domicile': 'commune',
        'ids': '{{ cards|objects:"foo"|getlist:"id"|join:"," }}',
        'syntax_error': '{% for %}',
        'variable_error': '{{ "foo"|add:user.email }}',
        'event': '{{ data.event.foo }}',
    }
    pricing.save()
    data = {
        'event': {'foo': 42},
    }
    assert pricing.get_pricing_context(
        request=context['request'], data=data, user_external_id='child:42', payer_external_id='parent:35'
    ) == {
        'foo': 'bar',
        'qf': '42',
        'domicile': 'commune',
        'ids': '1,2',
        'event': '42',
    }

    # bypass some extra variables
    assert pricing.get_pricing_context(
        request=context['request'],
        data=data,
        user_external_id='child:42',
        payer_external_id='parent:35',
        bypass_extra_variables={'event': '35', 'foo': 'baz', 'bar': 'bar'},
    ) == {
        'foo': 'baz',
        'qf': '42',
        'domicile': 'commune',
        'ids': '1,2',
        'event': '35',
    }

    # user_external_id and payer_external_id can be used in variables
    pricing.extra_variables = {
        'qf': '{{ cards|objects:"qf"|filter_by:"foo"|filter_value:user_external_id|filter_by:"bar"|filter_value:payer_external_id|list }}',
    }
    pricing.save()
    mock_send.reset_mock()
    pricing.get_pricing_context(
        request=context['request'], data={}, user_external_id='child:42', payer_external_id='parent:35'
    )
    assert 'filter-foo=child%3A42&' in mock_send.call_args_list[0][0][0].url
    assert 'filter-bar=parent%3A35&' in mock_send.call_args_list[0][0][0].url
    pricing.extra_variables = {
        'qf': '{{ cards|objects:"qf"|filter_by:"foo"|filter_value:user_external_raw_id|filter_by:"bar"|filter_value:payer_external_raw_id|list }}',
    }
    pricing.save()
    mock_send.reset_mock()
    pricing.get_pricing_context(
        request=context['request'], data={}, user_external_id='child:42', payer_external_id='parent:35'
    )
    assert 'filter-foo=42&' in mock_send.call_args_list[0][0][0].url
    assert 'filter-bar=35&' in mock_send.call_args_list[0][0][0].url


@pytest.mark.parametrize(
    'condition, context, result',
    [
        ('qf < 1', {}, False),
        ('qf < 1', {'qf': 'foo'}, False),
        ('qf < 1', {'qf': 1}, False),
        ('qf < 1', {'qf': 0.9}, True),
        ('1 <= qf and qf < 2', {'qf': 0}, False),
        ('1 <= qf and qf < 2', {'qf': 2}, False),
        ('1 <= qf and qf < 2', {'qf': 10}, False),
        ('1 <= qf and qf < 2', {'qf': 1}, True),
        ('1 <= qf and qf < 2', {'qf': 1.5}, True),
        # no condition
        ('', {}, False),
    ],
)
def test_compute_condition(condition, context, result):
    category = CriteriaCategory.objects.create(label='QF', slug='qf')
    criteria = Criteria.objects.create(label='FOO', condition=condition, category=category)
    assert criteria.compute_condition(context) == result


@pytest.mark.parametrize('field', ['pricing', 'min_pricing'])
def test_compute_pricing(field):
    agenda = Agenda.objects.create(label='Foo bar')
    category = CriteriaCategory.objects.create(label='QF', slug='qf')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    pricing.categories.add(category, through_defaults={'order': 1})
    pricing.agendas.add(agenda)
    compute_method = getattr(pricing, 'compute_%s' % field)
    # no criteria defined on pricing
    with pytest.raises(CriteriaConditionNotFound) as e:
        compute_method(context={'qf': 2})
    assert e.value.details == {'category': 'qf', 'context': {'qf': 2}}

    # conditions are not set
    criteria1 = Criteria.objects.create(label='QF < 1', slug='qf-0', category=category)
    criteria2 = Criteria.objects.create(label='QF >= 1', slug='qf-1', category=category)
    pricing.criterias.add(criteria1)
    pricing.criterias.add(criteria2)
    with pytest.raises(CriteriaConditionNotFound) as e:
        compute_method(context={'qf': 2})
    assert e.value.details == {'category': 'qf', 'context': {'qf': 2}}

    # conditions set, but no match
    criteria1.condition = 'qf < 1'
    criteria1.save()
    criteria2.condition = 'False'
    criteria2.save()
    with pytest.raises(CriteriaConditionNotFound) as e:
        compute_method(context={'qf': 2})
    assert e.value.details == {'category': 'qf', 'context': {'qf': 2}}

    # but with a default criteria, there is a match, but pricing.(min_)pricing_data is not defined
    default_criteria1 = Criteria.objects.create(
        label='Else 1', slug='else-1', category=category, default=True
    )
    pricing.criterias.add(default_criteria1)
    error_class = PricingDataError
    if field == 'min_pricing':
        error_class = MinPricingDataError
    with pytest.raises(error_class) as e:
        compute_method(context={'qf': 2})
    assert e.value.details == {'criterias': {'qf': 'else-1'}, 'context': {'qf': 2}}
    # with more than one default criteria, fail
    default_criteria2 = Criteria.objects.create(
        label='Else 2', slug='else-2', category=category, default=True
    )
    pricing.criterias.add(default_criteria2)
    with pytest.raises(MultipleDefaultCriteriaCondition) as e:
        compute_method(context={'qf': 2})
    assert e.value.details == {'category': 'qf', 'context': {'qf': 2}}
    Criteria.objects.filter(default=True).delete()  # remove default criterias

    # criteria found, but pricing.(min_)pricing_data is not defined
    criteria1.condition = 'qf < 1'
    criteria1.save()
    criteria2.condition = 'qf >= 1'
    criteria2.save()
    error_class = PricingDataError
    if field == 'min_pricing':
        error_class = MinPricingDataError
    with pytest.raises(error_class) as e:
        compute_method(context={'qf': 2})
    assert e.value.details == {'criterias': {'qf': 'qf-1'}, 'context': {'qf': 2}}

    # criteria not found in (min_)pricing_data
    setattr(
        pricing,
        '%s_data' % field,
        {
            'qf:qf-0': 42,
        },
    )
    pricing.save()
    error_class = PricingDataError
    if field == 'min_pricing':
        error_class = MinPricingDataError
    with pytest.raises(error_class) as e:
        compute_method(context={'qf': 2})
    assert e.value.details == {'criterias': {'qf': 'qf-1'}, 'context': {'qf': 2}}

    # criteria found, but value is wrong
    for value in ['foo', ['foo']]:
        setattr(
            pricing,
            '%s_data' % field,
            {
                'qf:qf-0': 42,
                'qf:qf-1': value,
            },
        )
        pricing.save()
        error_class = PricingDataFormatError
        if field == 'min_pricing':
            error_class = MinPricingDataFormatError
        with pytest.raises(error_class) as e:
            compute_method(context={'qf': 2})
        assert e.value.details == {'pricing': value, 'wanted': 'decimal', 'context': {'qf': 2}}
    for value in [[], {}]:
        setattr(
            pricing,
            '%s_data' % field,
            {
                'qf:qf-0': 42,
                'qf:qf-1': value,
            },
        )
        pricing.save()
        error_class = PricingDataError
        if field == 'min_pricing':
            error_class = MinPricingDataError
        with pytest.raises(error_class) as e:
            compute_method(context={'qf': 2})
        assert e.value.details == {'criterias': {'qf': 'qf-1'}, 'context': {'qf': 2}}

    # correct value (decimal)
    setattr(
        pricing,
        '%s_data' % field,
        {
            'qf:qf-0': 42,
            'qf:qf-1': 52,
        },
    )
    pricing.save()
    assert compute_method(context={'qf': 2}) == (52, {'qf': 'qf-1'})

    # more complexe pricing model
    category2 = CriteriaCategory.objects.create(label='Domicile', slug='domicile')
    criteria1 = Criteria.objects.create(
        label='Commune', slug='dom-0', condition='domicile == "commune"', category=category2, order=1
    )
    criteria2 = Criteria.objects.create(
        label='Hors commune', slug='else', category=category2, default=True, order=0
    )
    pricing.categories.add(category2, through_defaults={'order': 2})
    pricing.criterias.add(criteria1)
    pricing.criterias.add(criteria2)

    # correct definition
    setattr(
        pricing,
        '%s_data' % field,
        {
            'domicile:dom-0': {
                'qf:qf-0': 3,
                'qf:qf-1': 5,
            },
            'domicile:else': {
                'qf:qf-0': 7,
                'qf:qf-1': 10,
            },
        },
    )
    pricing.save()
    assert compute_method(context={'qf': 2, 'domicile': 'commune'}) == (
        5,
        {'domicile': 'dom-0', 'qf': 'qf-1'},
    )
    assert compute_method(context={'qf': 0, 'domicile': 'commune'}) == (
        3,
        {'domicile': 'dom-0', 'qf': 'qf-0'},
    )
    assert compute_method(context={'qf': 2, 'domicile': 'ext'}) == (
        10,
        {'domicile': 'else', 'qf': 'qf-1'},
    )
    assert compute_method(context={'qf': 0, 'domicile': 'ext'}) == (
        7,
        {'domicile': 'else', 'qf': 'qf-0'},
    )

    # category ordering doesn't matter
    PricingCriteriaCategory.objects.filter(pricing=pricing, category=category).update(order=2)
    PricingCriteriaCategory.objects.filter(pricing=pricing, category=category2).update(order=1)
    assert compute_method(context={'qf': 2, 'domicile': 'commune'}) == (
        5,
        {'domicile': 'dom-0', 'qf': 'qf-1'},
    )
    assert compute_method(context={'qf': 0, 'domicile': 'commune'}) == (
        3,
        {'domicile': 'dom-0', 'qf': 'qf-0'},
    )
    assert compute_method(context={'qf': 2, 'domicile': 'ext'}) == (
        10,
        {'domicile': 'else', 'qf': 'qf-1'},
    )
    assert compute_method(context={'qf': 0, 'domicile': 'ext'}) == (
        7,
        {'domicile': 'else', 'qf': 'qf-0'},
    )


@mock.patch('requests.Session.send', side_effect=mocked_requests_send)
def test_compute_reduction_rate(mock_send, context, nocache):
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )

    # empty template
    assert pricing.reduction_rate == ''
    with pytest.raises(PricingReductionRateFormatError) as e:
        pricing.compute_reduction_rate(
            request=context['request'],
            original_context={},
            user_external_id='child:42',
            payer_external_id='parent:35',
        )
    assert e.value.details == {'reduction_rate': '', 'wanted': 'decimal'}

    for value in ['{% for %}', '{{ "foo"|add:user.email }}']:
        pricing.reduction_rate = value
        pricing.save()
        with pytest.raises(PricingReductionRateError):
            pricing.compute_reduction_rate(
                request=context['request'],
                original_context={},
                user_external_id='child:42',
                payer_external_id='parent:35',
            )

    # not a decimal
    for value in ['bar', '{{ foo }}']:
        pricing.reduction_rate = value
        pricing.save()
        with pytest.raises(PricingReductionRateFormatError) as e:
            pricing.compute_reduction_rate(
                request=context['request'],
                original_context={'foo': 'bar'},
                user_external_id='child:42',
                payer_external_id='parent:35',
            )
            assert e.value.details == {'reduction_rate': '"bar"', 'wanted': 'decimal'}

    # not a good rate
    for value in ['-0.01', '{{ min }}', '{{ 0|add:-1 }}', '100.01', '{{ max }}', '{{ 100|add:1 }}']:
        pricing.reduction_rate = value
        pricing.save()
        with pytest.raises(PricingReductionRateValueError) as e:
            pricing.compute_reduction_rate(
                request=context['request'],
                original_context={'min': '-0.01', 'max': '100.01'},
                user_external_id='child:42',
                payer_external_id='parent:35',
            )
            assert e.value.details == {}

    for value in ['42', '{{ foo }}', '{{ cards|objects:"foo"|first|get:"fields"|get:"rate" }}']:
        pricing.reduction_rate = value
        pricing.save()
        assert (
            pricing.compute_reduction_rate(
                request=context['request'],
                original_context={'foo': '42'},
                user_external_id='child:42',
                payer_external_id='parent:35',
            )
            == 42
        )

    # user_external_id and payer_external_id can be used
    pricing.reduction_rate = {
        'qf': '{{ cards|objects:"qf"|filter_by:"foo"|filter_value:user_external_id|filter_by:"bar"|filter_value:payer_external_id|list }}',
    }
    pricing.save()
    mock_send.reset_mock()
    with pytest.raises(PricingReductionRateFormatError):
        pricing.compute_reduction_rate(
            request=context['request'],
            original_context={},
            user_external_id='child:42',
            payer_external_id='parent:35',
        )
    assert 'filter-foo=child%3A42&' in mock_send.call_args_list[0][0][0].url
    assert 'filter-bar=parent%3A35&' in mock_send.call_args_list[0][0][0].url
    pricing.reduction_rate = {
        'qf': '{{ cards|objects:"qf"|filter_by:"foo"|filter_value:user_external_raw_id|filter_by:"bar"|filter_value:payer_external_raw_id|list }}',
    }
    pricing.save()
    mock_send.reset_mock()
    with pytest.raises(PricingReductionRateFormatError):
        pricing.compute_reduction_rate(
            request=context['request'],
            original_context={},
            user_external_id='child:42',
            payer_external_id='parent:35',
        )
    assert 'filter-foo=42&' in mock_send.call_args_list[0][0][0].url
    assert 'filter-bar=35&' in mock_send.call_args_list[0][0][0].url


@mock.patch('lingo.pricing.models.Pricing.compute_min_pricing')
def test_apply_reduction_rate(mock_compute, context):
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )

    # bad kind value or empty template
    assert pricing.kind == 'basic'
    assert pricing.reduction_rate == ''
    assert pricing.apply_reduction_rate(
        pricing=42,
        request=context['request'],
        context={'foo': '50'},
        user_external_id='child:42',
        payer_external_id='parent:35',
    ) == (42, {})

    pricing.kind = 'effort'
    pricing.save()
    assert pricing.apply_reduction_rate(
        pricing=42,
        request=context['request'],
        context={'foo': '50'},
        user_external_id='child:42',
        payer_external_id='parent:35',
    ) == (42, {})

    pricing.kind = 'basic'
    pricing.reduction_rate = '{{ foo }}'
    pricing.save()
    assert pricing.apply_reduction_rate(
        pricing=42,
        request=context['request'],
        context={'foo': '50'},
        user_external_id='child:42',
        payer_external_id='parent:35',
    ) == (42, {})

    pricing.kind = 'effort'
    pricing.reduction_rate = '{{ foo }}'
    pricing.save()
    assert pricing.apply_reduction_rate(
        pricing=42,
        request=context['request'],
        context={'foo': '50'},
        user_external_id='child:42',
        payer_external_id='parent:35',
    ) == (42, {})

    # template with correct value
    mock_compute.side_effect = PricingDataError
    pricing.kind = 'reduction'
    pricing.reduction_rate = '{{ foo }}'
    pricing.save()
    with pytest.raises(PricingError):
        pricing.apply_reduction_rate(
            pricing=42,
            request=context['request'],
            context={'foo': '50'},
            user_external_id='child:42',
            payer_external_id='parent:35',
        )
    assert mock_compute.call_args_list == [mock.call(context={'foo': '50'})]

    mock_compute.side_effect = None
    mock_compute.return_value = (21, 'criterias')
    assert pricing.apply_reduction_rate(
        pricing=42,
        request=context['request'],
        context={'foo': '50'},
        user_external_id='child:42',
        payer_external_id='parent:35',
    ) == (
        21,
        {
            'computed_pricing': 42,
            'reduction_rate': 50,
            'reduced_pricing': 21,
            'min_pricing': 21,
            'bounded_pricing': 21,
        },
    )


@mock.patch('requests.Session.send', side_effect=mocked_requests_send)
def test_compute_effort_rate_target(mock_send, context, nocache):
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )

    # empty template
    assert pricing.effort_rate_target == ''
    with pytest.raises(PricingEffortRateTargetFormatError) as e:
        pricing.compute_effort_rate_target(
            request=context['request'],
            original_context={},
            user_external_id='child:42',
            payer_external_id='parent:35',
        )
    assert e.value.details == {'effort_rate_target': '', 'wanted': 'decimal'}

    for value in ['{% for %}', '{{ "foo"|add:user.email }}']:
        pricing.effort_rate_target = value
        pricing.save()
        with pytest.raises(PricingEffortRateTargetError):
            pricing.compute_effort_rate_target(
                request=context['request'],
                original_context={},
                user_external_id='child:42',
                payer_external_id='parent:35',
            )

    # not a decimal
    for value in ['bar', '{{ foo }}']:
        pricing.effort_rate_target = value
        pricing.save()
        with pytest.raises(PricingEffortRateTargetFormatError) as e:
            pricing.compute_effort_rate_target(
                request=context['request'],
                original_context={'foo': 'bar'},
                user_external_id='child:42',
                payer_external_id='parent:35',
            )
            assert e.value.details == {'effort_rate_target': '"bar"', 'wanted': 'decimal'}

    # not a good rate
    for value in ['-0.01', '{{ min }}', '{{ 0|add:-1 }}']:
        pricing.effort_rate_target = value
        pricing.save()
        with pytest.raises(PricingEffortRateTargetValueError) as e:
            pricing.compute_effort_rate_target(
                request=context['request'],
                original_context={'min': '-0.01'},
                user_external_id='child:42',
                payer_external_id='parent:35',
            )
            assert e.value.details == {}

    for value in ['2000', '{{ foo }}', '{{ cards|objects:"foo"|first|get:"fields"|get:"target" }}']:
        pricing.effort_rate_target = value
        pricing.save()
        assert (
            pricing.compute_effort_rate_target(
                request=context['request'],
                original_context={'foo': '2000'},
                user_external_id='child:42',
                payer_external_id='parent:35',
            )
            == 2000
        )

    # user_external_id and payer_external_id can be used
    pricing.effort_rate_target = {
        'qf': '{{ cards|objects:"qf"|filter_by:"foo"|filter_value:user_external_id|filter_by:"bar"|filter_value:payer_external_id|list }}',
    }
    pricing.save()
    mock_send.reset_mock()
    with pytest.raises(PricingEffortRateTargetFormatError):
        pricing.compute_effort_rate_target(
            request=context['request'],
            original_context={},
            user_external_id='child:42',
            payer_external_id='parent:35',
        )
    assert 'filter-foo=child%3A42&' in mock_send.call_args_list[0][0][0].url
    assert 'filter-bar=parent%3A35&' in mock_send.call_args_list[0][0][0].url
    pricing.effort_rate_target = {
        'qf': '{{ cards|objects:"qf"|filter_by:"foo"|filter_value:user_external_raw_id|filter_by:"bar"|filter_value:payer_external_raw_id|list }}',
    }
    pricing.save()
    mock_send.reset_mock()
    with pytest.raises(PricingEffortRateTargetFormatError):
        pricing.compute_effort_rate_target(
            request=context['request'],
            original_context={},
            user_external_id='child:42',
            payer_external_id='parent:35',
        )
    assert 'filter-foo=42&' in mock_send.call_args_list[0][0][0].url
    assert 'filter-bar=35&' in mock_send.call_args_list[0][0][0].url


def test_apply_effort_rate_target(context):
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )

    # bad kind value or empty template
    assert pricing.kind == 'basic'
    assert pricing.effort_rate_target == ''
    assert pricing.apply_effort_rate_target(
        value=0.42,
        request=context['request'],
        context={'foo': '50'},
        user_external_id='child:42',
        payer_external_id='parent:35',
    ) == (0.42, {})

    pricing.kind = 'effort'
    pricing.save()
    assert pricing.apply_effort_rate_target(
        value=0.42,
        request=context['request'],
        context={'foo': '50'},
        user_external_id='child:42',
        payer_external_id='parent:35',
    ) == (0.42, {})

    pricing.kind = 'basic'
    pricing.effort_rate_target = '{{ foo }}'
    pricing.save()
    assert pricing.apply_effort_rate_target(
        value=0.42,
        request=context['request'],
        context={'foo': '50'},
        user_external_id='child:42',
        payer_external_id='parent:35',
    ) == (0.42, {})

    # template with correct value
    pricing.kind = 'effort'
    pricing.effort_rate_target = '{{ foo }}'
    pricing.save()
    assert pricing.apply_effort_rate_target(
        value=decimal.Decimal('0.42'),
        request=context['request'],
        context={'foo': '50'},
        user_external_id='child:42',
        payer_external_id='parent:35',
    ) == (
        decimal.Decimal('0.21'),
        {
            'effort_rate': decimal.Decimal('0.42'),
            'effort_rate_target': 50,
            'computed_pricing': decimal.Decimal('0.21'),
            'min_pricing': None,
            'max_pricing': None,
            'bounded_pricing': decimal.Decimal('0.21'),
        },
    )

    # with a min value
    pricing.min_pricing = decimal.Decimal('0.22')
    pricing.save()
    assert pricing.apply_effort_rate_target(
        value=decimal.Decimal('0.42'),
        request=context['request'],
        context={'foo': '50'},
        user_external_id='child:42',
        payer_external_id='parent:35',
    ) == (
        decimal.Decimal('0.22'),
        {
            'effort_rate': decimal.Decimal('0.42'),
            'effort_rate_target': 50,
            'computed_pricing': decimal.Decimal('0.21'),
            'min_pricing': decimal.Decimal('0.22'),
            'max_pricing': None,
            'bounded_pricing': decimal.Decimal('0.22'),
        },
    )
    pricing.min_pricing = decimal.Decimal('0.21')
    pricing.save()
    assert pricing.apply_effort_rate_target(
        value=decimal.Decimal('0.42'),
        request=context['request'],
        context={'foo': '50'},
        user_external_id='child:42',
        payer_external_id='parent:35',
    ) == (
        decimal.Decimal('0.21'),
        {
            'effort_rate': decimal.Decimal('0.42'),
            'effort_rate_target': 50,
            'computed_pricing': decimal.Decimal('0.21'),
            'min_pricing': decimal.Decimal('0.21'),
            'max_pricing': None,
            'bounded_pricing': decimal.Decimal('0.21'),
        },
    )

    # with a max value
    pricing.min_pricing = None
    pricing.max_pricing = decimal.Decimal('0.20')
    pricing.save()
    assert pricing.apply_effort_rate_target(
        value=decimal.Decimal('0.42'),
        request=context['request'],
        context={'foo': '50'},
        user_external_id='child:42',
        payer_external_id='parent:35',
    ) == (
        decimal.Decimal('0.20'),
        {
            'effort_rate': decimal.Decimal('0.42'),
            'effort_rate_target': 50,
            'computed_pricing': decimal.Decimal('0.21'),
            'min_pricing': None,
            'max_pricing': decimal.Decimal('0.20'),
            'bounded_pricing': decimal.Decimal('0.20'),
        },
    )
    pricing.max_pricing = decimal.Decimal('0.21')
    pricing.save()
    assert pricing.apply_effort_rate_target(
        value=decimal.Decimal('0.42'),
        request=context['request'],
        context={'foo': '50'},
        user_external_id='child:42',
        payer_external_id='parent:35',
    ) == (
        decimal.Decimal('0.21'),
        {
            'effort_rate': decimal.Decimal('0.42'),
            'effort_rate_target': 50,
            'computed_pricing': decimal.Decimal('0.21'),
            'min_pricing': None,
            'max_pricing': decimal.Decimal('0.21'),
            'bounded_pricing': decimal.Decimal('0.21'),
        },
    )


@mock.patch('requests.Session.send', side_effect=mocked_requests_send)
def test_compute_accounting_code(mock_send, context, nocache):
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )

    # empty template
    assert pricing.accounting_code == ''
    assert (
        pricing.compute_accounting_code(
            request=context['request'],
            original_context={},
            user_external_id='child:42',
            payer_external_id='parent:35',
        )
        == ''
    )

    for value in ['{% for %}', '{{ "foo"|add:user.email }}']:
        pricing.accounting_code = value
        pricing.save()
        with pytest.raises(PricingAccountingCodeError):
            pricing.compute_accounting_code(
                request=context['request'],
                original_context={},
                user_external_id='child:42',
                payer_external_id='parent:35',
            )

    for value in [
        '424242',
        '{{ foo }}',
        '{{ cards|objects:"foo"|first|get:"fields"|get:"accounting_code" }}',
    ]:
        pricing.accounting_code = value
        pricing.save()
        assert (
            pricing.compute_accounting_code(
                request=context['request'],
                original_context={'foo': '424242'},
                user_external_id='child:42',
                payer_external_id='parent:35',
            )
            == '424242'
        )

    # user_external_id and payer_external_id can be used
    pricing.accounting_code = {
        'qf': '{{ cards|objects:"qf"|filter_by:"foo"|filter_value:user_external_id|filter_by:"bar"|filter_value:payer_external_id|list }}',
    }
    pricing.save()
    mock_send.reset_mock()
    pricing.compute_accounting_code(
        request=context['request'],
        original_context={},
        user_external_id='child:42',
        payer_external_id='parent:35',
    )
    assert 'filter-foo=child%3A42&' in mock_send.call_args_list[0][0][0].url
    assert 'filter-bar=parent%3A35&' in mock_send.call_args_list[0][0][0].url
    pricing.accounting_code = {
        'qf': '{{ cards|objects:"qf"|filter_by:"foo"|filter_value:user_external_raw_id|filter_by:"bar"|filter_value:payer_external_raw_id|list }}',
    }
    pricing.save()
    mock_send.reset_mock()
    pricing.compute_accounting_code(
        request=context['request'],
        original_context={},
        user_external_id='child:42',
        payer_external_id='parent:35',
    )
    assert 'filter-foo=42&' in mock_send.call_args_list[0][0][0].url
    assert 'filter-bar=35&' in mock_send.call_args_list[0][0][0].url


@pytest.mark.parametrize('field', ['pricing', 'min_pricing'])
def test_format_pricing_data(field):
    agenda = Agenda.objects.create(label='Foo bar')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    pricing.agendas.add(agenda)
    assert pricing.format_pricing_data(field=field) == {}

    setattr(
        pricing,
        '%s_data' % field,
        {
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
                    'cat-3:crit-3-2': 0,
                    'cat-3:crit-3-3': 223,
                },
            },
        },
    )
    pricing.save()
    assert pricing.format_pricing_data(field=field) == {
        'cat-1:crit-1-1||cat-2:crit-2-1||cat-3:crit-3-1': 111,
        'cat-1:crit-1-1||cat-2:crit-2-1||cat-3:crit-3-3': 'not-a-decimal',
        'cat-1:crit-1-1||cat-2:crit-2-1||cat-3:crit-3-4': 114,
        'cat-1:crit-1-1||cat-2:crit-2-3||cat-3:crit-3-2': 132,
        'cat-1:crit-1-2||cat-2:crit-2-2||cat-3:crit-3-2': 0,
        'cat-1:crit-1-2||cat-2:crit-2-2||cat-3:crit-3-3': 223,
    }

    setattr(pricing, '%s_data' % field, {'foo': 42})
    pricing.save()
    assert pricing.format_pricing_data(field=field) == {'foo': 42}

    # wrong data
    setattr(pricing, '%s_data' % field, 'foo')
    pricing.save()
    assert pricing.format_pricing_data(field=field) == {'': 'foo'}
    setattr(pricing, '%s_data' % field, [])
    pricing.save()
    assert pricing.format_pricing_data(field=field) == {}
    setattr(pricing, '%s_data' % field, ['foo'])
    pricing.save()
    assert pricing.format_pricing_data(field=field) == {'': ['foo']}
    setattr(pricing, '%s_data' % field, {'foo': []})
    pricing.save()
    assert pricing.format_pricing_data(field=field) == {}


def test_get_booking_modifier_unknown_check_status():
    agenda = Agenda.objects.create(label='Foo bar')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    check_status = {'status': 'unknown'}
    with pytest.raises(PricingUnknownCheckStatusError):
        pricing.get_booking_modifier(agenda=agenda, check_status=check_status)


def test_get_booking_modifier_event_not_checked():
    agenda = Agenda.objects.create(label='Foo bar')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    check_status = {'status': 'error', 'error_reason': 'event-not-checked'}
    with pytest.raises(PricingEventNotCheckedError):
        pricing.get_booking_modifier(agenda=agenda, check_status=check_status)


def test_get_booking_modifier_no_booking():
    agenda = Agenda.objects.create(label='Foo bar')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    check_status = {'status': 'not-booked'}
    assert pricing.get_booking_modifier(agenda=agenda, check_status=check_status) == {
        'status': 'not-booked',
        'modifier_type': 'fixed',
        'modifier_fixed': 0,
    }

    # more than one booking found !
    check_status = {'status': 'error', 'error_reason': 'too-many-bookings-found'}
    with pytest.raises(PricingMultipleBookingError):
        pricing.get_booking_modifier(agenda=agenda, check_status=check_status)


def test_get_booking_modifier_booking_cancelled():
    agenda = Agenda.objects.create(label='Foo bar')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    check_status = {'status': 'cancelled'}
    assert pricing.get_booking_modifier(agenda=agenda, check_status=check_status) == {
        'status': 'cancelled',
        'modifier_type': 'fixed',
        'modifier_fixed': 0,
    }


def test_get_booking_modifier_booking_not_checked():
    agenda = Agenda.objects.create(label='Foo bar')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    check_status = {'status': 'error', 'error_reason': 'booking-not-checked'}
    with pytest.raises(PricingBookingNotCheckedError):
        pricing.get_booking_modifier(agenda=agenda, check_status=check_status)


def test_get_booking_modifier_unknown_check_type():
    agenda = Agenda.objects.create(label='Foo bar')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    check_status = {'status': 'presence', 'check_type': 'unknown'}
    with pytest.raises(PricingBookingCheckTypeError) as e:
        pricing.get_booking_modifier(agenda=agenda, check_status=check_status)
    assert e.value.details == {
        'reason': 'not-found',
    }
    check_status = {'status': 'absence', 'check_type': 'unknown'}
    with pytest.raises(PricingBookingCheckTypeError) as e:
        pricing.get_booking_modifier(agenda=agenda, check_status=check_status)
    assert e.value.details == {
        'reason': 'not-found',
    }


def test_get_booking_modifier_booking_absence():
    agenda = Agenda.objects.create(label='Foo bar')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )

    # no check type
    check_status = {'status': 'absence', 'check_type': ''}
    assert pricing.get_booking_modifier(agenda=agenda, check_status=check_status) == {
        'status': 'absence',
        'check_type_group': None,
        'check_type': None,
        'modifier_type': 'rate',
        'modifier_rate': 0,
    }

    # check_type but not configured on agenda
    group = CheckTypeGroup.objects.create(label='Foo bar')
    check_type = CheckType.objects.create(label='Foo reason', group=group, kind='absence')
    check_status = {'status': 'absence', 'check_type': check_type.slug}
    with pytest.raises(PricingBookingCheckTypeError) as e:
        pricing.get_booking_modifier(agenda=agenda, check_status=check_status)
    assert e.value.details == {
        'reason': 'not-found',
    }
    # incomplete configuration
    agenda.check_type_group = group
    agenda.save()
    with pytest.raises(PricingBookingCheckTypeError) as e:
        pricing.get_booking_modifier(agenda=agenda, check_status=check_status)
    assert e.value.details == {
        'check_type_group': 'foo-bar',
        'check_type': 'foo-reason',
        'reason': 'not-configured',
    }

    check_type.pricing = 42
    check_type.save()
    assert pricing.get_booking_modifier(agenda=agenda, check_status=check_status) == {
        'status': 'absence',
        'check_type_group': 'foo-bar',
        'check_type': 'foo-reason',
        'modifier_type': 'fixed',
        'modifier_fixed': 42,
    }

    check_type.pricing = -42
    check_type.save()
    assert pricing.get_booking_modifier(agenda=agenda, check_status=check_status) == {
        'status': 'absence',
        'check_type_group': 'foo-bar',
        'check_type': 'foo-reason',
        'modifier_type': 'fixed',
        'modifier_fixed': -42,
    }

    check_type.pricing = 0
    check_type.save()
    assert pricing.get_booking_modifier(agenda=agenda, check_status=check_status) == {
        'status': 'absence',
        'check_type_group': 'foo-bar',
        'check_type': 'foo-reason',
        'modifier_type': 'fixed',
        'modifier_fixed': 0,
    }

    check_type.pricing = None
    check_type.pricing_rate = 20
    check_type.save()
    assert pricing.get_booking_modifier(agenda=agenda, check_status=check_status) == {
        'status': 'absence',
        'check_type_group': 'foo-bar',
        'check_type': 'foo-reason',
        'modifier_type': 'rate',
        'modifier_rate': 20,
    }

    check_type.pricing_rate = -20
    check_type.save()
    assert pricing.get_booking_modifier(agenda=agenda, check_status=check_status) == {
        'status': 'absence',
        'check_type_group': 'foo-bar',
        'check_type': 'foo-reason',
        'modifier_type': 'rate',
        'modifier_rate': -20,
    }

    check_type.pricing_rate = 0
    check_type.save()
    assert pricing.get_booking_modifier(agenda=agenda, check_status=check_status) == {
        'status': 'absence',
        'check_type_group': 'foo-bar',
        'check_type': 'foo-reason',
        'modifier_type': 'rate',
        'modifier_rate': 0,
    }

    # bad check type kind
    check_type.kind = 'presence'
    check_type.save()
    with pytest.raises(PricingBookingCheckTypeError) as e:
        pricing.get_booking_modifier(agenda=agenda, check_status=check_status)
    assert e.value.details == {
        'check_type_group': 'foo-bar',
        'check_type': 'foo-reason',
        'reason': 'wrong-kind',
    }


def test_get_booking_modifier_booking_presence():
    agenda = Agenda.objects.create(label='Foo bar')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )

    # no check type
    check_status = {'status': 'presence', 'check_type': ''}
    assert pricing.get_booking_modifier(agenda=agenda, check_status=check_status) == {
        'status': 'presence',
        'check_type_group': None,
        'check_type': None,
        'modifier_type': 'rate',
        'modifier_rate': 100,
    }

    # check_type but not configured on agenda
    group = CheckTypeGroup.objects.create(label='Foo bar')
    check_type = CheckType.objects.create(label='Foo reason', group=group, kind='presence')
    check_status = {'status': 'presence', 'check_type': check_type.slug}
    with pytest.raises(PricingBookingCheckTypeError) as e:
        pricing.get_booking_modifier(agenda=agenda, check_status=check_status)
    assert e.value.details == {
        'reason': 'not-found',
    }
    # incomplete configuration
    agenda.check_type_group = group
    agenda.save()
    with pytest.raises(PricingBookingCheckTypeError) as e:
        pricing.get_booking_modifier(agenda=agenda, check_status=check_status)
    assert e.value.details == {
        'check_type_group': 'foo-bar',
        'check_type': 'foo-reason',
        'reason': 'not-configured',
    }

    check_type.pricing = 42
    check_type.save()
    assert pricing.get_booking_modifier(agenda=agenda, check_status=check_status) == {
        'status': 'presence',
        'check_type_group': 'foo-bar',
        'check_type': 'foo-reason',
        'modifier_type': 'fixed',
        'modifier_fixed': 42,
    }

    check_type.pricing = -42
    check_type.save()
    assert pricing.get_booking_modifier(agenda=agenda, check_status=check_status) == {
        'status': 'presence',
        'check_type_group': 'foo-bar',
        'check_type': 'foo-reason',
        'modifier_type': 'fixed',
        'modifier_fixed': -42,
    }

    check_type.pricing = 0
    check_type.save()
    assert pricing.get_booking_modifier(agenda=agenda, check_status=check_status) == {
        'status': 'presence',
        'check_type_group': 'foo-bar',
        'check_type': 'foo-reason',
        'modifier_type': 'fixed',
        'modifier_fixed': 0,
    }

    check_type.pricing = None
    check_type.pricing_rate = 150
    check_type.save()
    assert pricing.get_booking_modifier(agenda=agenda, check_status=check_status) == {
        'status': 'presence',
        'check_type_group': 'foo-bar',
        'check_type': 'foo-reason',
        'modifier_type': 'rate',
        'modifier_rate': 150,
    }

    check_type.pricing_rate = -50
    check_type.save()
    assert pricing.get_booking_modifier(agenda=agenda, check_status=check_status) == {
        'status': 'presence',
        'check_type_group': 'foo-bar',
        'check_type': 'foo-reason',
        'modifier_type': 'rate',
        'modifier_rate': -50,
    }

    check_type.pricing_rate = 0
    check_type.save()
    assert pricing.get_booking_modifier(agenda=agenda, check_status=check_status) == {
        'status': 'presence',
        'check_type_group': 'foo-bar',
        'check_type': 'foo-reason',
        'modifier_type': 'rate',
        'modifier_rate': 0,
    }

    # bad check type kind
    check_type.kind = 'absence'
    check_type.save()
    with pytest.raises(PricingBookingCheckTypeError) as e:
        pricing.get_booking_modifier(agenda=agenda, check_status=check_status)
    assert e.value.details == {
        'check_type_group': 'foo-bar',
        'check_type': 'foo-reason',
        'reason': 'wrong-kind',
    }


def test_get_pricing_data(context):
    agenda = Agenda.objects.create(label='Foo bar')
    category = CriteriaCategory.objects.create(label='Foo', slug='foo')
    criteria = Criteria.objects.create(label='Bar', slug='bar', condition='True', category=category)
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
        extra_variables={
            'domicile': 'commune',
            'qf': '2',
        },
        pricing_data={
            'foo:bar': 42,
        },
        accounting_code='{{ 424240|add:2 }}',
    )
    pricing.criterias.add(criteria)
    pricing.categories.add(category, through_defaults={'order': 1})
    pricing.agendas.add(agenda)
    assert pricing.get_pricing_data(
        request=context['request'],
        pricing_date=datetime.date(year=2021, month=9, day=1),
        user_external_id='child:42',
        payer_external_id='parent:35',
    ) == {
        'pricing': 42,
        'calculation_details': {
            'pricing': 42,
            'criterias': {'foo': 'bar'},
            'reduction_rate': {},
            'effort_rate': {},
            'context': {'domicile': 'commune', 'qf': '2'},
        },
        'accounting_code': '424242',
    }
    assert pricing.get_pricing_data(
        request=context['request'],
        pricing_date=datetime.date(year=2021, month=9, day=1),
        user_external_id='child:42',
        payer_external_id='parent:35',
        bypass_extra_variables={'qf': '42'},
    ) == {
        'pricing': 42,
        'calculation_details': {
            'pricing': 42,
            'criterias': {'foo': 'bar'},
            'reduction_rate': {},
            'effort_rate': {},
            'context': {'domicile': 'commune', 'qf': '42'},
        },
        'accounting_code': '424242',
    }


def test_get_pricing_data_for_event(context):
    agenda = Agenda.objects.create(label='Foo bar')
    category = CriteriaCategory.objects.create(label='Foo', slug='foo')
    criteria = Criteria.objects.create(label='Bar', slug='bar', condition='True', category=category)
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
        extra_variables={
            'domicile': 'commune',
            'qf': '2',
        },
        pricing_data={
            'foo:bar': 42,
        },
        accounting_code='424242',
    )
    pricing.criterias.add(criteria)
    pricing.categories.add(category, through_defaults={'order': 1})
    pricing.agendas.add(agenda)
    assert pricing.get_pricing_data_for_event(
        request=context['request'],
        agenda=agenda,
        event={'start_datetime': make_aware(datetime.datetime(2021, 9, 15, 12, 00)).isoformat()},
        check_status={'status': 'not-booked'},
        user_external_id='child:42',
        payer_external_id='parent:35',
    ) == {
        'pricing': 0,
        'booking_details': {
            'status': 'not-booked',
            'modifier_type': 'fixed',
            'modifier_fixed': 0,
        },
        'accounting_code': '424242',
    }
    assert pricing.get_pricing_data_for_event(
        request=context['request'],
        agenda=agenda,
        event={'start_datetime': make_aware(datetime.datetime(2021, 9, 15, 12, 00)).isoformat()},
        check_status={'status': 'cancelled'},
        user_external_id='child:42',
        payer_external_id='parent:35',
    ) == {
        'pricing': 0,
        'booking_details': {
            'status': 'cancelled',
            'modifier_type': 'fixed',
            'modifier_fixed': 0,
        },
        'accounting_code': '424242',
    }
    assert pricing.get_pricing_data_for_event(
        request=context['request'],
        agenda=agenda,
        event={'start_datetime': make_aware(datetime.datetime(2021, 9, 15, 12, 00)).isoformat()},
        check_status={'status': 'presence', 'check_type': ''},
        user_external_id='child:42',
        payer_external_id='parent:35',
    ) == {
        'pricing': 42,
        'calculation_details': {
            'pricing': 42,
            'criterias': {'foo': 'bar'},
            'reduction_rate': {},
            'effort_rate': {},
            'context': {'domicile': 'commune', 'qf': '2'},
        },
        'booking_details': {
            'status': 'presence',
            'check_type': None,
            'check_type_group': None,
            'modifier_type': 'rate',
            'modifier_rate': 100,
        },
        'accounting_code': '424242',
    }
    assert pricing.get_pricing_data_for_event(
        request=context['request'],
        agenda=agenda,
        event={'start_datetime': make_aware(datetime.datetime(2021, 9, 15, 12, 00)).isoformat()},
        check_status={'status': 'presence', 'check_type': ''},
        user_external_id='child:42',
        payer_external_id='parent:35',
        bypass_extra_variables={'qf': '42'},
    ) == {
        'pricing': 42,
        'calculation_details': {
            'pricing': 42,
            'criterias': {'foo': 'bar'},
            'reduction_rate': {},
            'effort_rate': {},
            'context': {'domicile': 'commune', 'qf': '42'},
        },
        'booking_details': {
            'status': 'presence',
            'check_type': None,
            'check_type_group': None,
            'modifier_type': 'rate',
            'modifier_rate': 100,
        },
        'accounting_code': '424242',
    }


@pytest.mark.parametrize(
    'modifier, pricing_amount',
    [
        # not booked
        (
            {
                'status': 'not-booked',
                'modifier_type': 'rate',
                'modifier_rate': 0,
            },
            0,
        ),
        # cancelled
        (
            {
                'status': 'cancelled',
                'modifier_type': 'rate',
                'modifier_rate': 0,
            },
            0,
        ),
        # absence
        (
            {
                'status': 'absence',
                'check_type_group': None,
                'check_type': None,
                'modifier_type': 'rate',
                'modifier_rate': 0,
            },
            0,
        ),
        (
            {
                'status': 'absence',
                'check_type_group': 'foo-bar',
                'check_type': 'foo-reason',
                'modifier_type': 'fixed',
                'modifier_fixed': 35,
            },
            35,
        ),
        (
            {
                'status': 'absence',
                'check_type_group': 'foo-bar',
                'check_type': 'foo-reason',
                'modifier_type': 'fixed',
                'modifier_fixed': -35,
            },
            -35,
        ),
        (
            {
                'status': 'absence',
                'check_type_group': 'foo-bar',
                'check_type': 'foo-reason',
                'modifier_type': 'fixed',
                'modifier_fixed': 0,
            },
            0,
        ),
        (
            {
                'status': 'absence',
                'check_type_group': 'foo-bar',
                'check_type': 'foo-reason',
                'modifier_type': 'rate',
                'modifier_rate': 20,
            },
            8.4,
        ),
        (
            {
                'status': 'absence',
                'check_type_group': 'foo-bar',
                'check_type': 'foo-reason',
                'modifier_type': 'rate',
                'modifier_rate': -100,
            },
            -42,
        ),
        (
            {
                'status': 'absence',
                'check_type_group': 'foo-bar',
                'check_type': 'foo-reason',
                'modifier_type': 'rate',
                'modifier_rate': 0,
            },
            0,
        ),
        # presence
        (
            {
                'status': 'presence',
                'check_type_group': None,
                'check_type': None,
                'modifier_type': 'rate',
                'modifier_rate': 100,
            },
            42,
        ),
        (
            {
                'status': 'presence',
                'check_type_group': 'foo-bar',
                'check_type': 'foo-reason',
                'modifier_type': 'fixed',
                'modifier_fixed': 35,
            },
            35,
        ),
        (
            {
                'status': 'presence',
                'check_type_group': 'foo-bar',
                'check_type': 'foo-reason',
                'modifier_type': 'fixed',
                'modifier_fixed': -35,
            },
            -35,
        ),
        (
            {
                'status': 'presence',
                'check_type_group': 'foo-bar',
                'check_type': 'foo-reason',
                'modifier_type': 'fixed',
                'modifier_fixed': 0,
            },
            0,
        ),
        (
            {
                'status': 'presence',
                'check_type_group': 'foo-bar',
                'check_type': 'foo-reason',
                'modifier_type': 'rate',
                'modifier_rate': 150,
            },
            63,
        ),
        (
            {
                'status': 'presence',
                'check_type_group': 'foo-bar',
                'check_type': 'foo-reason',
                'modifier_type': 'rate',
                'modifier_rate': -50,
            },
            -21,
        ),
        (
            {
                'status': 'presence',
                'check_type_group': 'foo-bar',
                'check_type': 'foo-reason',
                'modifier_type': 'rate',
                'modifier_rate': 0,
            },
            0,
        ),
    ],
)
def test_aggregate_pricing_data(modifier, pricing_amount):
    agenda = Agenda.objects.create(label='Foo bar')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    pricing.agendas.add(agenda)

    assert pricing.aggregate_pricing_data(
        pricing=42,
        criterias={'foo': 'bar'},
        reduction_rate={'foo': 'baz'},
        effort_rate={'foo': 'bazz'},
        context={'domicile': 'commune', 'qf': 2},
        modifier=modifier,
        accounting_code='424242',
    ) == {
        'pricing': pricing_amount,
        'calculation_details': {
            'pricing': 42,
            'criterias': {'foo': 'bar'},
            'reduction_rate': {'foo': 'baz'},
            'effort_rate': {'foo': 'bazz'},
            'context': {'domicile': 'commune', 'qf': 2},
        },
        'booking_details': modifier,
        'accounting_code': '424242',
    }


@pytest.mark.parametrize('field', ['pricing', 'min_pricing'])
def test_pricing_iter_pricing_matrix_3_categories(field):
    category1 = CriteriaCategory.objects.create(label='Cat 1')
    criteria11 = Criteria.objects.create(label='Crit 1-1', slug='crit-1-1', category=category1, order=1)
    criteria12 = Criteria.objects.create(label='Crit 1-2', slug='crit-1-2', category=category1, order=2)
    category2 = CriteriaCategory.objects.create(label='Cat 2')
    criteria21 = Criteria.objects.create(label='Crit 2-1', slug='crit-2-1', category=category2, order=1)
    criteria22 = Criteria.objects.create(label='Crit 2-2', slug='crit-2-2', category=category2, order=2)
    criteria23 = Criteria.objects.create(label='Crit 2-3', slug='crit-2-3', category=category2, order=3)
    category3 = CriteriaCategory.objects.create(label='Cat 3')
    criteria31 = Criteria.objects.create(label='Crit 3-1', slug='crit-3-1', category=category3, order=1)
    criteria33 = Criteria.objects.create(label='Crit 3-3', slug='crit-3-3', category=category3, order=3)
    criteria34 = Criteria.objects.create(label='Crit 3-4', slug='crit-3-4', category=category3, order=4)
    criteria32 = Criteria.objects.create(label='Crit 3-2', slug='crit-3-2', category=category3, order=2)
    not_used = Criteria.objects.create(label='Not used', slug='crit-3-notused', category=category3, order=5)
    category4 = CriteriaCategory.objects.create(label='Cat 4')  # ignored

    agenda = Agenda.objects.create(label='Foo bar')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    iter_method = getattr(pricing, 'iter_%s_matrix' % field)
    pricing.categories.add(category1, through_defaults={'order': 1})
    pricing.categories.add(category2, through_defaults={'order': 2})
    pricing.categories.add(category3, through_defaults={'order': 3})
    pricing.categories.add(category4, through_defaults={'order': 4})
    pricing.criterias.set(Criteria.objects.exclude(pk=not_used.pk))
    pricing.agendas.add(agenda)
    assert list(iter_method()) == [
        PricingMatrix(
            criteria=criteria11,
            rows=[
                PricingMatrixRow(
                    criteria=criteria31,
                    cells=[
                        PricingMatrixCell(criteria=criteria21, value=None),
                        PricingMatrixCell(criteria=criteria22, value=None),
                        PricingMatrixCell(criteria=criteria23, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria32,
                    cells=[
                        PricingMatrixCell(criteria=criteria21, value=None),
                        PricingMatrixCell(criteria=criteria22, value=None),
                        PricingMatrixCell(criteria=criteria23, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria33,
                    cells=[
                        PricingMatrixCell(criteria=criteria21, value=None),
                        PricingMatrixCell(criteria=criteria22, value=None),
                        PricingMatrixCell(criteria=criteria23, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria34,
                    cells=[
                        PricingMatrixCell(criteria=criteria21, value=None),
                        PricingMatrixCell(criteria=criteria22, value=None),
                        PricingMatrixCell(criteria=criteria23, value=None),
                    ],
                ),
            ],
        ),
        PricingMatrix(
            criteria=criteria12,
            rows=[
                PricingMatrixRow(
                    criteria=criteria31,
                    cells=[
                        PricingMatrixCell(criteria=criteria21, value=None),
                        PricingMatrixCell(criteria=criteria22, value=None),
                        PricingMatrixCell(criteria=criteria23, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria32,
                    cells=[
                        PricingMatrixCell(criteria=criteria21, value=None),
                        PricingMatrixCell(criteria=criteria22, value=None),
                        PricingMatrixCell(criteria=criteria23, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria33,
                    cells=[
                        PricingMatrixCell(criteria=criteria21, value=None),
                        PricingMatrixCell(criteria=criteria22, value=None),
                        PricingMatrixCell(criteria=criteria23, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria34,
                    cells=[
                        PricingMatrixCell(criteria=criteria21, value=None),
                        PricingMatrixCell(criteria=criteria22, value=None),
                        PricingMatrixCell(criteria=criteria23, value=None),
                    ],
                ),
            ],
        ),
    ]

    # some data defined
    setattr(
        pricing,
        '%s_data' % field,
        {
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
    pricing.save()
    # category ordering doesn't matter
    PricingCriteriaCategory.objects.filter(pricing=pricing, category=category1).update(order=2)
    PricingCriteriaCategory.objects.filter(pricing=pricing, category=category2).update(order=1)
    assert list(iter_method()) == [
        PricingMatrix(
            criteria=criteria21,
            rows=[
                PricingMatrixRow(
                    criteria=criteria31,
                    cells=[
                        PricingMatrixCell(criteria=criteria11, value=111),
                        PricingMatrixCell(criteria=criteria12, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria32,
                    cells=[
                        PricingMatrixCell(criteria=criteria11, value=None),
                        PricingMatrixCell(criteria=criteria12, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria33,
                    cells=[
                        PricingMatrixCell(criteria=criteria11, value=None),
                        PricingMatrixCell(criteria=criteria12, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria34,
                    cells=[
                        PricingMatrixCell(criteria=criteria11, value=114),
                        PricingMatrixCell(criteria=criteria12, value=None),
                    ],
                ),
            ],
        ),
        PricingMatrix(
            criteria=criteria22,
            rows=[
                PricingMatrixRow(
                    criteria=criteria31,
                    cells=[
                        PricingMatrixCell(criteria=criteria11, value=None),
                        PricingMatrixCell(criteria=criteria12, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria32,
                    cells=[
                        PricingMatrixCell(criteria=criteria11, value=None),
                        PricingMatrixCell(criteria=criteria12, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria33,
                    cells=[
                        PricingMatrixCell(criteria=criteria11, value=None),
                        PricingMatrixCell(criteria=criteria12, value=223),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria34,
                    cells=[
                        PricingMatrixCell(criteria=criteria11, value=None),
                        PricingMatrixCell(criteria=criteria12, value=None),
                    ],
                ),
            ],
        ),
        PricingMatrix(
            criteria=criteria23,
            rows=[
                PricingMatrixRow(
                    criteria=criteria31,
                    cells=[
                        PricingMatrixCell(criteria=criteria11, value=None),
                        PricingMatrixCell(criteria=criteria12, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria32,
                    cells=[
                        PricingMatrixCell(criteria=criteria11, value=132),
                        PricingMatrixCell(criteria=criteria12, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria33,
                    cells=[
                        PricingMatrixCell(criteria=criteria11, value=None),
                        PricingMatrixCell(criteria=criteria12, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria34,
                    cells=[
                        PricingMatrixCell(criteria=criteria11, value=None),
                        PricingMatrixCell(criteria=criteria12, value=None),
                    ],
                ),
            ],
        ),
    ]


@pytest.mark.parametrize('field', ['pricing', 'min_pricing'])
def test_pricing_iter_pricing_matrix_2_categories(field):
    category2 = CriteriaCategory.objects.create(label='Cat 2')
    criteria21 = Criteria.objects.create(label='Crit 2-1', slug='crit-2-1', category=category2, order=1)
    criteria22 = Criteria.objects.create(label='Crit 2-2', slug='crit-2-2', category=category2, order=2)
    criteria23 = Criteria.objects.create(label='Crit 2-3', slug='crit-2-3', category=category2, order=3)
    category3 = CriteriaCategory.objects.create(label='Cat 3')
    criteria31 = Criteria.objects.create(label='Crit 3-1', slug='crit-3-1', category=category3, order=1)
    criteria33 = Criteria.objects.create(label='Crit 3-3', slug='crit-3-3', category=category3, order=3)
    criteria34 = Criteria.objects.create(label='Crit 3-4', slug='crit-3-4', category=category3, order=4)
    criteria32 = Criteria.objects.create(label='Crit 3-2', slug='crit-3-2', category=category3, order=2)
    not_used = Criteria.objects.create(label='Not used', slug='crit-3-notused', category=category3, order=5)

    agenda = Agenda.objects.create(label='Foo bar')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    iter_method = getattr(pricing, 'iter_%s_matrix' % field)
    pricing.categories.add(category2, through_defaults={'order': 2})
    pricing.categories.add(category3, through_defaults={'order': 3})
    pricing.criterias.set(Criteria.objects.exclude(pk=not_used.pk))
    pricing.agendas.add(agenda)

    assert list(iter_method()) == [
        PricingMatrix(
            criteria=None,
            rows=[
                PricingMatrixRow(
                    criteria=criteria31,
                    cells=[
                        PricingMatrixCell(criteria=criteria21, value=None),
                        PricingMatrixCell(criteria=criteria22, value=None),
                        PricingMatrixCell(criteria=criteria23, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria32,
                    cells=[
                        PricingMatrixCell(criteria=criteria21, value=None),
                        PricingMatrixCell(criteria=criteria22, value=None),
                        PricingMatrixCell(criteria=criteria23, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria33,
                    cells=[
                        PricingMatrixCell(criteria=criteria21, value=None),
                        PricingMatrixCell(criteria=criteria22, value=None),
                        PricingMatrixCell(criteria=criteria23, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria34,
                    cells=[
                        PricingMatrixCell(criteria=criteria21, value=None),
                        PricingMatrixCell(criteria=criteria22, value=None),
                        PricingMatrixCell(criteria=criteria23, value=None),
                    ],
                ),
            ],
        ),
    ]

    # some data defined
    setattr(
        pricing,
        '%s_data' % field,
        {
            'cat-2:crit-2-1': {
                'cat-3:crit-3-1': 11,
                'cat-3:crit-3-3': 'not-a-decimal',
                'cat-3:crit-3-4': 14,
            },
            'cat-2:crit-2-3': {
                'cat-3:crit-3-2': 32,
            },
        },
    )
    pricing.save()
    assert list(iter_method()) == [
        PricingMatrix(
            criteria=None,
            rows=[
                PricingMatrixRow(
                    criteria=criteria31,
                    cells=[
                        PricingMatrixCell(criteria=criteria21, value=11),
                        PricingMatrixCell(criteria=criteria22, value=None),
                        PricingMatrixCell(criteria=criteria23, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria32,
                    cells=[
                        PricingMatrixCell(criteria=criteria21, value=None),
                        PricingMatrixCell(criteria=criteria22, value=None),
                        PricingMatrixCell(criteria=criteria23, value=32),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria33,
                    cells=[
                        PricingMatrixCell(criteria=criteria21, value=None),
                        PricingMatrixCell(criteria=criteria22, value=None),
                        PricingMatrixCell(criteria=criteria23, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria34,
                    cells=[
                        PricingMatrixCell(criteria=criteria21, value=14),
                        PricingMatrixCell(criteria=criteria22, value=None),
                        PricingMatrixCell(criteria=criteria23, value=None),
                    ],
                ),
            ],
        ),
    ]


@pytest.mark.parametrize('field', ['pricing', 'min_pricing'])
def test_pricing_iter_pricing_matrix_1_category(field):
    category3 = CriteriaCategory.objects.create(label='Cat 3')
    criteria31 = Criteria.objects.create(label='Crit 3-1', slug='crit-3-1', category=category3, order=1)
    criteria33 = Criteria.objects.create(label='Crit 3-3', slug='crit-3-3', category=category3, order=3)
    criteria34 = Criteria.objects.create(label='Crit 3-4', slug='crit-3-4', category=category3, order=4)
    criteria32 = Criteria.objects.create(label='Crit 3-2', slug='crit-3-2', category=category3, order=2)
    not_used = Criteria.objects.create(label='Not used', slug='crit-3-notused', category=category3, order=5)

    agenda = Agenda.objects.create(label='Foo bar')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    iter_method = getattr(pricing, 'iter_%s_matrix' % field)
    pricing.categories.add(category3, through_defaults={'order': 3})
    pricing.criterias.set(Criteria.objects.exclude(pk=not_used.pk))
    pricing.agendas.add(agenda)

    assert list(iter_method()) == [
        PricingMatrix(
            criteria=None,
            rows=[
                PricingMatrixRow(
                    criteria=criteria31,
                    cells=[
                        PricingMatrixCell(criteria=None, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria32,
                    cells=[
                        PricingMatrixCell(criteria=None, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria33,
                    cells=[
                        PricingMatrixCell(criteria=None, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria34,
                    cells=[
                        PricingMatrixCell(criteria=None, value=None),
                    ],
                ),
            ],
        ),
    ]

    # some data defined
    setattr(
        pricing,
        '%s_data' % field,
        {
            'cat-3:crit-3-1': 1,
            'cat-3:crit-3-3': 'not-a-decimal',
            'cat-3:crit-3-4': 4,
        },
    )
    pricing.save()
    assert list(iter_method()) == [
        PricingMatrix(
            criteria=None,
            rows=[
                PricingMatrixRow(
                    criteria=criteria31,
                    cells=[
                        PricingMatrixCell(criteria=None, value=1),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria32,
                    cells=[
                        PricingMatrixCell(criteria=None, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria33,
                    cells=[
                        PricingMatrixCell(criteria=None, value=None),
                    ],
                ),
                PricingMatrixRow(
                    criteria=criteria34,
                    cells=[
                        PricingMatrixCell(criteria=None, value=4),
                    ],
                ),
            ],
        ),
    ]


@pytest.mark.parametrize('field', ['pricing', 'min_pricing'])
def test_pricing_iter_pricing_matrix_empty(field):
    agenda = Agenda.objects.create(label='Foo bar')
    pricing = Pricing.objects.create(
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    iter_method = getattr(pricing, 'iter_%s_matrix' % field)
    pricing.agendas.add(agenda)

    assert list(iter_method()) == []

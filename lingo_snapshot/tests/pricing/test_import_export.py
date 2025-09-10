import copy
import datetime
import io

import pytest
from django.contrib.auth.models import Group

from lingo.agendas.models import Agenda, CheckType, CheckTypeGroup
from lingo.invoicing.models import Regie
from lingo.pricing.models import BillingDate, Criteria, CriteriaCategory, Pricing, PricingCriteriaCategory
from lingo.pricing.utils import export_site, import_site
from lingo.utils.misc import LingoImportError, json_dump

pytestmark = pytest.mark.django_db


def test_import_export(app):
    Agenda.objects.create(label='Foo Bar')
    Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    CriteriaCategory.objects.create(label='Foo bar')

    data = export_site()
    assert len(data['agendas']) == 1
    assert len(data['pricings']) == 1
    assert len(data['pricings'][0]['agendas']) == 0
    assert len(data['pricing_categories']) == 1
    import_site(data={})
    assert Pricing.objects.count() == 1
    assert CriteriaCategory.objects.count() == 1


def test_import_export_pricing(app):
    agenda = Agenda.objects.create(label='Foo Bar')
    group1 = Group.objects.create(name='role-foo-1')
    group2 = Group.objects.create(name='role-foo-2')
    pricing = Pricing.objects.create(
        label='Bar',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
        flat_fee_schedule=True,
        subscription_required=False,
        kind='reduction',
        reduction_rate='foo',
        effort_rate_target='bar',
        accounting_code='foo',
        min_pricing=35,
        max_pricing=42,
        pricing_data={
            'foo': 'bar',
        },
        min_pricing_data={
            'foo': 'bar',
        },
        extra_variables={'foo': 'bar'},
        edit_role=group1,
        view_role=group2,
    )
    pricing.agendas.set([agenda])
    data = export_site()
    json_dump(data, io.StringIO())  # no error

    Pricing.objects.all().delete()
    Agenda.objects.all().delete()
    Group.objects.all().delete()

    with pytest.raises(LingoImportError) as excinfo:
        import_site(data)
    assert str(excinfo.value) == 'Missing "foo-bar" agenda'

    agenda2 = Agenda.objects.create(label='Baz')
    with pytest.raises(LingoImportError) as excinfo:
        import_site(data)
    assert str(excinfo.value) == 'Missing "foo-bar" agenda'

    agenda = Agenda.objects.create(label='Foo Bar')
    Group.objects.create(name='role')
    with pytest.raises(LingoImportError) as excinfo:
        import_site(data)
    assert str(excinfo.value) == 'Missing role: role-foo-1'

    group1 = Group.objects.create(name='role-foo-1')
    with pytest.raises(LingoImportError) as excinfo:
        import_site(data)
    assert str(excinfo.value) == 'Missing role: role-foo-2'

    group2 = Group.objects.create(name='role-foo-2')
    import_site(data)
    pricing = Pricing.objects.latest('pk')
    assert list(pricing.agendas.all()) == [agenda]
    assert pricing.date_start == datetime.date(year=2021, month=9, day=1)
    assert pricing.date_end == datetime.date(year=2021, month=10, day=1)
    assert pricing.pricing_data == {'foo': 'bar'}
    assert pricing.min_pricing_data == {'foo': 'bar'}
    assert pricing.extra_variables == {'foo': 'bar'}
    assert pricing.flat_fee_schedule is True
    assert pricing.subscription_required is False
    assert pricing.kind == 'reduction'
    assert pricing.reduction_rate == 'foo'
    assert pricing.effort_rate_target == 'bar'
    assert pricing.accounting_code == 'foo'
    assert pricing.min_pricing == 35
    assert pricing.max_pricing == 42

    # again
    import_site(data)
    pricing = Pricing.objects.get(pk=pricing.pk)
    assert list(pricing.agendas.all()) == [agenda]

    Pricing.objects.all().delete()
    data['pricings'].append(
        {
            'slug': 'baz',
            'label': 'Baz',
            'pricing': 'foo',
            'agendas': ['foo-bar', 'baz'],
            'date_start': '2022-09-01',
            'date_end': '2022-10-01',
            'flat_fee_schedule': False,
            'subscription_required': True,
            'kind': 'effort',
            'reduction_rate': 'foo2',
            'effort_rate_target': 'bar2',
            'accounting_code': 'foo2',
            'min_pricing': 36,
            'max_pricing': 43,
            'pricing_data': {'foo': 'bar'},
            'min_pricing_data': {'foo': 'bar'},
            'extra_variables': {'foo': 'bar'},
        }
    )
    import_site(data)
    pricing = Pricing.objects.latest('pk')
    assert list(pricing.agendas.all().order_by('slug')) == [agenda2, agenda]
    assert pricing.date_start == datetime.date(year=2022, month=9, day=1)
    assert pricing.date_end == datetime.date(year=2022, month=10, day=1)
    assert pricing.pricing_data == {'foo': 'bar'}
    assert pricing.min_pricing_data == {'foo': 'bar'}
    assert pricing.extra_variables == {'foo': 'bar'}
    assert pricing.flat_fee_schedule is False
    assert pricing.subscription_required is True
    assert pricing.kind == 'effort'
    assert pricing.reduction_rate == 'foo2'
    assert pricing.effort_rate_target == 'bar2'
    assert pricing.accounting_code == 'foo2'
    assert pricing.min_pricing == 36
    assert pricing.max_pricing == 43


def test_import_export_pricing_with_categories(app):
    pricing = Pricing.objects.create(
        label='Bar',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    category = CriteriaCategory.objects.create(label='Foo bar')
    pricing.categories.add(category, through_defaults={'order': 42})

    data = export_site()
    category.delete()
    del data['pricing_categories']

    with pytest.raises(LingoImportError) as excinfo:
        import_site(data)
    assert str(excinfo.value) == 'Missing "foo-bar" pricing category'

    CriteriaCategory.objects.create(label='Foobar')
    with pytest.raises(LingoImportError) as excinfo:
        import_site(data)
    assert str(excinfo.value) == 'Missing "foo-bar" pricing category'

    category = CriteriaCategory.objects.create(label='Foo bar')
    import_site(data)
    pricing = Pricing.objects.get(slug=pricing.slug)
    assert list(pricing.categories.all()) == [category]
    assert PricingCriteriaCategory.objects.first().order == 42

    category2 = CriteriaCategory.objects.create(label='Foo bar 2')
    category3 = CriteriaCategory.objects.create(label='Foo bar 3')
    pricing.categories.add(category2, through_defaults={'order': 1})
    data = export_site()
    del data['pricing_categories']
    data['pricings'][0]['categories'] = [
        {
            'category': 'foo-bar-3',
            'order': 1,
            'criterias': [],
        },
        {
            'category': 'foo-bar',
            'order': 35,
            'criterias': [],
        },
    ]
    import_site(data)
    assert list(pricing.categories.all()) == [category, category3]
    assert list(
        PricingCriteriaCategory.objects.filter(pricing=pricing).values_list('category', flat=True)
    ) == [category3.pk, category.pk]
    assert list(PricingCriteriaCategory.objects.filter(pricing=pricing).values_list('order', flat=True)) == [
        1,
        35,
    ]
    assert list(pricing.criterias.all()) == []

    criteria1 = Criteria.objects.create(label='Crit 1', category=category)
    Criteria.objects.create(label='Crit 2', category=category)
    criteria3 = Criteria.objects.create(label='Crit 3', category=category)

    # unknown criteria
    data['pricings'][0]['categories'] = [
        {
            'category': 'foo-bar-3',
            'order': 1,
            'criterias': ['unknown'],
        },
        {
            'category': 'foo-bar',
            'order': 35,
            'criterias': [],
        },
    ]
    with pytest.raises(LingoImportError) as excinfo:
        import_site(data)
    assert str(excinfo.value) == 'Missing "unknown" pricing criteria for "foo-bar-3" category'

    # wrong criteria (from another category)
    data['pricings'][0]['categories'] = [
        {
            'category': 'foo-bar-3',
            'order': 1,
            'criterias': ['crit-1'],
        },
        {
            'category': 'foo-bar',
            'order': 35,
            'criterias': [],
        },
    ]
    with pytest.raises(LingoImportError) as excinfo:
        import_site(data)
    assert str(excinfo.value) == 'Missing "crit-1" pricing criteria for "foo-bar-3" category'

    data['pricings'][0]['categories'] = [
        {
            'category': 'foo-bar-3',
            'order': 1,
            'criterias': [],
        },
        {
            'category': 'foo-bar',
            'order': 35,
            'criterias': ['crit-1', 'crit-3'],
        },
    ]
    import_site(data)
    assert list(pricing.categories.all()) == [category, category3]
    assert list(
        PricingCriteriaCategory.objects.filter(pricing=pricing).values_list('category', flat=True)
    ) == [category3.pk, category.pk]
    assert list(PricingCriteriaCategory.objects.filter(pricing=pricing).values_list('order', flat=True)) == [
        1,
        35,
    ]
    assert set(pricing.criterias.all()) == {criteria1, criteria3}


def test_import_export_pricing_with_billing_dates(app):
    pricing = Pricing.objects.create(
        label='Bar',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    BillingDate.objects.create(
        pricing=pricing,
        date_start=datetime.date(year=2021, month=9, day=1),
        label='Period 1',
    )
    BillingDate.objects.create(
        pricing=pricing,
        date_start=datetime.date(year=2021, month=9, day=15),
        label='Period 2',
    )

    data = export_site()

    import_site(data)
    pricing = Pricing.objects.latest('pk')
    assert pricing.billingdates.count() == 2
    billing_date1 = pricing.billingdates.all()[0]
    assert billing_date1.date_start == datetime.date(year=2021, month=9, day=1)
    assert billing_date1.label == 'Period 1'
    billing_date2 = pricing.billingdates.all()[1]
    assert billing_date2.date_start == datetime.date(year=2021, month=9, day=15)
    assert billing_date2.label == 'Period 2'


def test_import_export_agenda_with_check_types(app):
    group = CheckTypeGroup.objects.create(label='foo')
    agenda = Agenda.objects.create(label='Foo Bar', check_type_group=group)
    data = export_site()

    group.delete()
    del data['check_type_groups']
    agenda.check_type_group = None
    agenda.save()

    with pytest.raises(LingoImportError) as excinfo:
        import_site(data)
    assert str(excinfo.value) == 'Missing "foo" check type group'

    CheckTypeGroup.objects.create(label='foobar')
    with pytest.raises(LingoImportError) as excinfo:
        import_site(data)
    assert str(excinfo.value) == 'Missing "foo" check type group'

    group = CheckTypeGroup.objects.create(label='foo')
    import_site(data)
    agenda.refresh_from_db()
    assert agenda.check_type_group == group


def test_import_export_agenda_with_regie(app):
    regie = Regie.objects.create(label='Foo')
    agenda = Agenda.objects.create(label='Foo Bar', regie=regie)
    data = export_site()

    agenda.regie = None
    agenda.save()
    regie.delete()

    with pytest.raises(LingoImportError) as excinfo:
        import_site(data)
    assert str(excinfo.value) == 'Missing "foo" regie'

    Regie.objects.create(label='foobar')
    with pytest.raises(LingoImportError) as excinfo:
        import_site(data)
    assert str(excinfo.value) == 'Missing "foo" regie'

    regie = Regie.objects.create(label='foo')
    import_site(data)
    agenda.refresh_from_db()
    assert agenda.regie == regie


def test_import_export_pricing_criteria_category(app):
    payload = export_site()
    assert len(payload['pricing_categories']) == 0

    category = CriteriaCategory.objects.create(label='Foo bar')
    Criteria.objects.create(label='Foo reason', category=category)
    Criteria.objects.create(label='Baz', category=category)

    payload = export_site()
    assert len(payload['pricing_categories']) == 1

    category.delete()
    assert not CriteriaCategory.objects.exists()
    assert not Criteria.objects.exists()

    import_site(copy.deepcopy(payload))
    assert CriteriaCategory.objects.count() == 1
    category = CriteriaCategory.objects.first()
    assert category.label == 'Foo bar'
    assert category.slug == 'foo-bar'
    assert category.criterias.count() == 2
    assert Criteria.objects.get(category=category, label='Foo reason', slug='foo-reason')
    assert Criteria.objects.get(category=category, label='Baz', slug='baz')

    # update
    update_payload = copy.deepcopy(payload)
    update_payload['pricing_categories'][0]['label'] = 'Foo bar Updated'
    import_site(update_payload)
    category.refresh_from_db()
    assert category.label == 'Foo bar Updated'

    # insert another category
    category.slug = 'foo-bar-updated'
    category.save()
    import_site(copy.deepcopy(payload))
    assert CriteriaCategory.objects.count() == 2
    category = CriteriaCategory.objects.latest('pk')
    assert category.label == 'Foo bar'
    assert category.slug == 'foo-bar'
    assert category.criterias.count() == 2
    assert Criteria.objects.get(category=category, label='Foo reason', slug='foo-reason')
    assert Criteria.objects.get(category=category, label='Baz', slug='baz')


def test_import_export_check_type_group(app):
    payload = export_site()
    assert len(payload['check_type_groups']) == 0

    group = CheckTypeGroup.objects.create(label='Foo bar')
    check_type = CheckType.objects.create(
        label='Foo reason',
        code='XX',
        colour='#424242',
        group=group,
        pricing=42,
        pricing_rate=35,
        disabled=True,
    )
    CheckType.objects.create(label='Baz', group=group)
    group.unexpected_presence = check_type
    group.unjustified_absence = check_type
    group.save()

    payload = export_site()
    assert len(payload['check_type_groups']) == 1

    group.delete()
    assert not CheckTypeGroup.objects.exists()
    assert not CheckType.objects.exists()

    import_site(copy.deepcopy(payload))
    assert CheckTypeGroup.objects.count() == 1
    group = CheckTypeGroup.objects.first()
    assert group.label == 'Foo bar'
    assert group.slug == 'foo-bar'
    assert group.check_types.count() == 2
    assert group.unexpected_presence.slug == 'foo-reason'
    assert group.unjustified_absence.slug == 'foo-reason'
    check_type = CheckType.objects.get(group=group, label='Foo reason', slug='foo-reason')
    assert check_type.code == 'XX'
    assert check_type.colour == '#424242'
    assert check_type.pricing == 42
    assert check_type.pricing_rate == 35
    assert check_type.disabled is True
    assert CheckType.objects.get(group=group, label='Baz', slug='baz')

    # update
    update_payload = copy.deepcopy(payload)
    update_payload['check_type_groups'][0]['label'] = 'Foo bar Updated'
    import_site(update_payload)
    group.refresh_from_db()
    assert group.label == 'Foo bar Updated'

    # insert another group
    group.slug = 'foo-bar-updated'
    group.save()
    payload['check_type_groups'][0]['unexpected_presence'] = None
    payload['check_type_groups'][0]['unjustified_absence'] = None
    import_site(copy.deepcopy(payload))
    assert CheckTypeGroup.objects.count() == 2
    group = CheckTypeGroup.objects.latest('pk')
    assert group.label == 'Foo bar'
    assert group.slug == 'foo-bar'
    assert group.check_types.count() == 2
    assert group.unexpected_presence is None
    assert group.unjustified_absence is None
    assert CheckType.objects.get(group=group, label='Foo reason', slug='foo-reason')
    assert CheckType.objects.get(group=group, label='Baz', slug='baz')

    # unknown unexpected_presence
    payload['check_type_groups'][0]['unexpected_presence'] = 'unknown'
    with pytest.raises(LingoImportError) as excinfo:
        import_site(copy.deepcopy(payload))
    assert str(excinfo.value) == 'Missing "unknown" check type'

    # unknown unjustified_absence
    payload['check_type_groups'][0]['unjustified_absence'] = 'unknown'
    with pytest.raises(LingoImportError) as excinfo:
        import_site(copy.deepcopy(payload))
    assert str(excinfo.value) == 'Missing "unknown" check type'

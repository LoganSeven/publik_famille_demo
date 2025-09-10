import datetime

import pytest
from django.core.management import call_command
from django.utils.timezone import now

from lingo.agendas.models import Agenda, CheckTypeGroup
from lingo.invoicing.models import PaymentType, Regie
from lingo.pricing.models import CriteriaCategory, Pricing
from lingo.snapshot.models import (
    AgendaSnapshot,
    CheckTypeGroupSnapshot,
    CriteriaCategorySnapshot,
    PricingSnapshot,
    RegieSnapshot,
)

pytestmark = pytest.mark.django_db


def test_clear_snapshot():
    agenda = Agenda.objects.create(label='Agenda')
    group = CheckTypeGroup.objects.create(label='Foo bar')
    pricing = Pricing.objects.create(
        label='Foo',
        date_start=datetime.date(year=2021, month=9, day=1),
        date_end=datetime.date(year=2021, month=10, day=1),
    )
    category = CriteriaCategory.objects.create(label='QF')
    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    assert PaymentType.objects.count() == 8

    agenda.take_snapshot()
    group.take_snapshot()
    pricing.take_snapshot()
    category.take_snapshot()
    regie.take_snapshot()

    snapshot = AgendaSnapshot.objects.get(instance=agenda)
    snapshot_agenda = snapshot.get_instance()
    assert snapshot_agenda.snapshot == snapshot
    assert snapshot_agenda.pk != agenda.pk
    snapshot = CheckTypeGroupSnapshot.objects.get(instance=group)
    snapshot_group = snapshot.get_instance()
    assert snapshot_group.snapshot == snapshot
    assert snapshot_group.pk != group.pk
    snapshot = PricingSnapshot.objects.get(instance=pricing)
    snapshot_pricing = snapshot.get_instance()
    assert snapshot_pricing.snapshot == snapshot
    assert snapshot_pricing.pk != pricing.pk
    snapshot = CriteriaCategorySnapshot.objects.get(instance=category)
    snapshot_category = snapshot.get_instance()
    assert snapshot_category.snapshot == snapshot
    assert snapshot_category.pk != category.pk
    snapshot = RegieSnapshot.objects.get(instance=regie)
    snapshot_regie = snapshot.get_instance()
    assert snapshot_regie.snapshot == snapshot
    assert snapshot_regie.pk != regie.pk
    assert PaymentType.objects.count() == 16

    # too soon
    call_command('clear_snapshots')
    for model in [Agenda, CheckTypeGroup, CriteriaCategory, Pricing, Regie]:
        assert model.objects.count() == 1
        assert model.snapshots.count() == 1
        assert model.get_snapshot_model().objects.count() == 1
    assert PaymentType.objects.count() == 16

    # still too soon
    for model in [Agenda, CheckTypeGroup, CriteriaCategory, Pricing, Regie]:
        model.snapshots.update(updated_at=now() - datetime.timedelta(days=1, minutes=-1))
    call_command('clear_snapshots')
    for model in [Agenda, CheckTypeGroup, CriteriaCategory, Pricing, Regie]:
        assert model.objects.count() == 1
        assert model.snapshots.count() == 1
        assert model.get_snapshot_model().objects.count() == 1
    assert PaymentType.objects.count() == 16

    # ok, 24H after page snapshot creation
    for model in [Agenda, CheckTypeGroup, CriteriaCategory, Pricing, Regie]:
        model.snapshots.update(updated_at=now() - datetime.timedelta(days=1))
    call_command('clear_snapshots')
    for model in [Agenda, CheckTypeGroup, CriteriaCategory, Pricing, Regie]:
        assert model.objects.count() == 1
        assert model.snapshots.count() == 0
        assert model.get_snapshot_model().objects.count() == 1
    assert PaymentType.objects.count() == 8

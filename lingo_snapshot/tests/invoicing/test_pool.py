import datetime

import pytest
from django.core.management import call_command
from django.utils.timezone import now

from lingo.invoicing.models import (
    Campaign,
    Credit,
    CreditLine,
    DraftInvoice,
    DraftInvoiceLine,
    DraftJournalLine,
    Invoice,
    InvoiceLine,
    JournalLine,
    Pool,
    Regie,
)

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('finalized', [True, False])
def test_clear_draft_pools(finalized):
    regie = Regie.objects.create(label='Foo')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        finalized=finalized,
    )
    pool1 = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='failed',
    )
    pool2 = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='success',
    )
    pool3 = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='success',
    )
    final_pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
        status='success',
    )

    for pool in [pool1, pool2, pool3]:
        invoice = DraftInvoice.objects.create(
            date_publication=campaign.date_publication,
            date_payment_deadline=campaign.date_payment_deadline,
            date_due=campaign.date_due,
            regie=regie,
            pool=pool,
        )
        invoice_line = DraftInvoiceLine.objects.create(
            pool=pool,
            invoice=invoice,
            event_date=now().date(),
            quantity=1,
            unit_amount=1,
        )
        DraftJournalLine.objects.create(
            pool=pool,
            invoice_line=invoice_line,
            event_date=now().date(),
            quantity=1,
            amount=1,
        )

    invoice = Invoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=final_pool,
    )
    invoice_line = InvoiceLine.objects.create(
        pool=final_pool,
        invoice=invoice,
        event_date=now().date(),
        quantity=1,
        unit_amount=1,
    )
    JournalLine.objects.create(
        pool=final_pool,
        invoice_line=invoice_line,
        event_date=now().date(),
        quantity=1,
        amount=1,
    )

    credit = Credit.objects.create(
        date_publication=campaign.date_publication,
        regie=regie,
        pool=final_pool,
    )
    credit_line = CreditLine.objects.create(
        pool=final_pool,
        credit=credit,
        event_date=now().date(),
        quantity=1,
        unit_amount=1,
    )
    JournalLine.objects.create(
        pool=final_pool,
        credit_line=credit_line,
        event_date=now().date(),
        quantity=1,
        amount=1,
    )

    # too soon
    call_command('clear_draft_pools')
    assert Pool.objects.count() == 4
    assert DraftInvoice.objects.count() == 3
    assert DraftInvoiceLine.objects.count() == 3
    assert DraftJournalLine.objects.count() == 3
    assert Invoice.objects.count() == 1
    assert InvoiceLine.objects.count() == 1
    assert Credit.objects.count() == 1
    assert CreditLine.objects.count() == 1
    assert JournalLine.objects.count() == 2

    # still too soon
    Campaign.objects.update(updated_at=now() - datetime.timedelta(days=31, minutes=-1))
    call_command('clear_draft_pools')
    assert Pool.objects.count() == 4
    assert DraftInvoice.objects.count() == 3
    assert DraftInvoiceLine.objects.count() == 3
    assert DraftJournalLine.objects.count() == 3
    assert Invoice.objects.count() == 1
    assert InvoiceLine.objects.count() == 1
    assert Credit.objects.count() == 1
    assert CreditLine.objects.count() == 1
    assert JournalLine.objects.count() == 2

    # ok, one month (~31 days) after campaign finalization
    Campaign.objects.update(updated_at=now() - datetime.timedelta(days=31))
    call_command('clear_draft_pools')
    if finalized:
        assert Pool.objects.count() == 2
        assert DraftInvoice.objects.count() == 1
        assert DraftInvoiceLine.objects.count() == 1
        assert DraftJournalLine.objects.count() == 1
        assert Invoice.objects.count() == 1
        assert InvoiceLine.objects.count() == 1
        assert Credit.objects.count() == 1
        assert CreditLine.objects.count() == 1
        assert JournalLine.objects.count() == 2
        assert Pool.objects.filter(pk=pool1.pk).exists() is False
        assert Pool.objects.filter(pk=pool2.pk).exists() is False
        assert Pool.objects.filter(pk=pool3.pk).exists() is True
        assert DraftInvoice.objects.filter(pool=pool3).count() == 1
        assert DraftInvoiceLine.objects.filter(pool=pool3).count() == 1
        assert DraftJournalLine.objects.filter(pool=pool3).count() == 1
    else:
        assert Pool.objects.count() == 4
        assert DraftInvoice.objects.count() == 3
        assert DraftInvoiceLine.objects.count() == 3
        assert DraftJournalLine.objects.count() == 3
        assert Invoice.objects.count() == 1
        assert InvoiceLine.objects.count() == 1
        assert Credit.objects.count() == 1
        assert CreditLine.objects.count() == 1
        assert JournalLine.objects.count() == 2

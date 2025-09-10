import datetime
import decimal
from unittest import mock

import pytest
from django.utils.formats import date_format
from django.utils.timezone import localtime, now
from pyquery import PyQuery

from lingo.agendas.chrono import ChronoError
from lingo.agendas.models import Agenda, AgendaUnlockLog
from lingo.invoicing.models import (
    Campaign,
    CollectionDocket,
    Credit,
    CreditAssignment,
    CreditCancellationReason,
    CreditLine,
    DraftInvoice,
    DraftInvoiceLine,
    DraftJournalLine,
    InjectedLine,
    Invoice,
    InvoiceCancellationReason,
    InvoiceLine,
    InvoiceLinePayment,
    JournalLine,
    Payment,
    PaymentType,
    Pool,
    Regie,
)
from tests.utils import login

pytestmark = pytest.mark.django_db


def test_add_pool(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    agenda1 = Agenda.objects.create(label='Foo bar 1', regie=regie)
    agenda2 = Agenda.objects.create(label='Foo bar 2', regie=regie)
    agenda3 = Agenda.objects.create(label='Foo bar 3', regie=regie)
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        invalid=True,
    )
    campaign.agendas.add(agenda1, agenda2)
    AgendaUnlockLog.objects.create(campaign=campaign, agenda=agenda1)
    AgendaUnlockLog.objects.create(campaign=campaign, agenda=agenda2)
    AgendaUnlockLog.objects.create(campaign=campaign, agenda=agenda3)
    old_updated_at = list(AgendaUnlockLog.objects.values_list('updated_at', flat=True).order_by('created_at'))

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk))
    with mock.patch.object(Campaign, 'generate', autospec=True) as mock_generate:
        resp = resp.form.submit()
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/campaign/%s/#open:pools' % (regie.pk, campaign.pk)
    )
    assert mock_generate.call_args_list == [mock.call(campaign)]
    campaign.refresh_from_db()
    assert campaign.invalid is False
    assert AgendaUnlockLog.objects.filter(campaign=campaign, agenda=agenda1, active=True).exists() is False
    assert AgendaUnlockLog.objects.filter(campaign=campaign, agenda=agenda2, active=True).exists() is False
    assert AgendaUnlockLog.objects.filter(campaign=campaign, agenda=agenda3, active=True).exists() is True
    new_updated_at = AgendaUnlockLog.objects.values_list('updated_at', flat=True).order_by('created_at')
    for i, (old_value, new_value) in enumerate(zip(old_updated_at, new_updated_at)):
        if i == 2:
            assert old_value == new_value
        else:
            assert old_value < new_value

    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='failed',
    )
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk))

    pool.status = 'completed'
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk))

    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (0, campaign.pk), status=404)

    pool.status = 'registered'
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk), status=404)

    pool.status = 'running'
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk), status=404)

    pool.status = 'completed'
    pool.draft = False
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk), status=404)

    pool.draft = True
    pool.save()
    campaign.finalized = True
    campaign.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk), status=404)

    campaign.finalized = False
    campaign.adjustment_campaign = True
    campaign.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk))

    agenda = Agenda.objects.create(label='Foo bar', regie=regie)
    campaign.agendas.add(agenda)
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk))

    agenda.partial_bookings = True
    agenda.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk))
    assert 'An adjustment campaign cannot be launched on a partial bookings agenda.' in resp
    resp = resp.form.submit()
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/campaign/%s/#open:pools' % (regie.pk, campaign.pk)
    )
    resp = resp.follow()
    assert 'An adjustment campaign cannot be launched on a partial bookings agenda.' in resp


def test_promote_pool(app, admin_user, settings):
    regie = Regie.objects.create(label='Foo')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='completed',
    )

    app = login(app)
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk)
    )
    with mock.patch.object(Pool, 'promote', autospec=True) as mock_promote:
        resp = resp.form.submit()
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/campaign/%s/#open:pools' % (regie.pk, campaign.pk)
    )
    assert mock_promote.call_args_list == [mock.call(pool)]

    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (0, campaign.pk, pool.pk), status=404)

    pool.status = 'registered'
    pool.save()
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk),
        status=404,
    )

    pool.status = 'running'
    pool.save()
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk),
        status=404,
    )

    pool.status = 'failed'
    pool.save()
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk),
        status=404,
    )

    pool.status = 'completed'
    pool.draft = False
    pool.save()
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk),
        status=404,
    )

    pool.draft = True
    pool.save()
    campaign.invalid = True
    campaign.save()
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk),
        status=404,
    )

    campaign.invalid = False
    campaign.finalized = True
    campaign.save()
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk),
        status=404,
    )

    campaign.finalized = False
    campaign.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk)
    )
    resp.form.submit()

    assert Pool.objects.filter(draft=False).exists()

    # not the last
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk),
        status=404,
    )

    # check errors
    settings.CAMPAIGN_ALLOW_PROMOTION_WITH_ERRORS = False
    Pool.objects.filter(draft=False).delete()
    DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=1,
        status='success',
        pool=pool,
    )
    DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=1,
        status='error',
        pool=pool,
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert 'Impossible to generate invoices: errors remain.' in resp
    resp.form.submit(status=404)

    settings.CAMPAIGN_ALLOW_PROMOTION_WITH_ERRORS = True
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert 'Impossible to generate invoices: errors remain.' not in resp
    resp.form.submit()

    settings.CAMPAIGN_ALLOW_PROMOTION_WITH_ERRORS = False
    JournalLine.objects.all().delete()
    Pool.objects.filter(draft=False).delete()
    DraftJournalLine.objects.filter(status='error').delete()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert 'Impossible to generate invoices: errors remain.' not in resp
    resp.form.submit()


def test_detail_pool(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    campaign2 = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 10, 1),
        date_end=datetime.date(2022, 11, 1),
        date_publication=datetime.date(2022, 11, 1),
        date_payment_deadline=datetime.date(2022, 11, 30),
        date_due=datetime.date(2022, 11, 30),
        date_debit=datetime.date(2022, 12, 15),
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='completed',
    )

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, pool.pk) not in resp
    )
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk) in resp
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk) in resp
    )

    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (0, campaign.pk, pool.pk), status=404)
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, 0, pool.pk), status=404)
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign2.pk, pool.pk), status=404)
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, 0), status=404)

    pool.draft = False
    pool.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, pool.pk) not in resp
    )
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk) in resp
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk)
        not in resp
    )

    pool.draft = True
    pool.status = 'registered'
    pool.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, pool.pk) in resp
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk)
        not in resp
    )

    pool.status = 'running'
    pool.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, pool.pk) in resp
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk)
        not in resp
    )

    pool.status = 'failed'
    pool.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, pool.pk) not in resp
    )
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk) in resp
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk)
        not in resp
    )

    pool.status = 'completed'
    pool.save()
    campaign.invalid = True
    campaign.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, pool.pk) not in resp
    )
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk) in resp
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk)
        not in resp
    )

    campaign.invalid = False
    campaign.finalized = True
    campaign.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, pool.pk) not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk)
        not in resp
    )

    campaign.finalized = False
    campaign.save()
    pool2 = Pool.objects.create(
        campaign=pool.campaign,
        status='running',
    )
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, pool.pk) not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk)
        not in resp
    )  # not the last

    pool2.status = 'completed'
    pool2.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, pool.pk) not in resp
    )
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk) in resp
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk)
        not in resp
    )  # not the last

    # orphan line
    DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=1,
        status='success',
    )
    line = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=1,
        status='success',
        pool=pool,
    )
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert '<span class="meta meta-success">1</span>' in resp
    assert 'meta-warning' not in resp
    assert 'meta-error' not in resp
    line.status = 'error'
    line.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert 'meta-success' not in resp
    assert 'meta-warning' not in resp
    assert '<span class="meta meta-error">1</span>' in resp
    line.status = 'warning'
    line.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert 'meta-success' not in resp
    assert '<span class="meta meta-warning">1</span>' in resp
    assert 'meta-error' not in resp
    line.delete()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert 'meta-success' not in resp
    assert 'meta-warning' not in resp
    assert 'meta-error' not in resp

    pool.draft = False
    pool.save()
    line = JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=1,
        status='success',
        pool=pool,
    )
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert '<span class="meta meta-success">1</span>' in resp
    assert 'meta-warning' not in resp
    assert 'meta-error' not in resp
    line.status = 'error'
    line.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert 'meta-success' not in resp
    assert 'meta-warning' not in resp
    assert '<span class="meta meta-error">1</span>' in resp
    line.status = 'warning'
    line.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert 'meta-success' not in resp
    assert '<span class="meta meta-warning">1</span>' in resp
    assert 'meta-error' not in resp


@pytest.mark.parametrize('draft', [True, False])
def test_detail_pool_invoices(app, admin_user, draft):
    invoice_model = DraftInvoice if draft else Invoice
    credit_model = DraftInvoice if draft else Credit
    line_model = DraftInvoiceLine if draft else InvoiceLine
    credit_line_model = DraftInvoiceLine if draft else CreditLine

    regie = Regie.objects.create(label='Foo')
    Agenda.objects.create(label='Agenda A', regie=regie)
    Agenda.objects.create(label='Agenda B', regie=regie)
    PaymentType.create_defaults(regie)
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline_displayed=datetime.date(2022, 10, 15),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=draft,
        status='completed',
    )
    invoice1 = invoice_model.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline_displayed=campaign.date_payment_deadline_displayed,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=False,
        origin='campaign',
    )
    invoice2 = invoice_model.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
        payer_address='42 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=True,
        origin='campaign',
    )
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=False)
    collection.set_number()
    collection.save()
    invoice3 = invoice_model.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        payer_address='43 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=True,
        origin='campaign',
        **(
            {
                'collection': collection,
            }
            if not draft
            else {}
        ),
    )
    invoice4 = invoice_model.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        pool=pool,
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        origin='campaign',
        **(
            {
                'cancelled_at': now(),
                'cancelled_by': admin_user,
                'cancellation_reason': InvoiceCancellationReason.objects.create(label='Final pool deletion'),
                'cancellation_description': 'foo bar\nblah',
            }
            if not draft
            else {}
        ),
    )
    orphan_invoice = invoice_model.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
    )
    credit = credit_model.objects.create(
        date_publication=campaign.date_publication,
        regie=regie,
        pool=pool,
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        payer_address='43 rue des kangourous\n99999 Kangourou Ville',
        origin='campaign',
        **(
            {
                'date_due': campaign.date_due,
                'date_payment_deadline': campaign.date_payment_deadline,
            }
            if draft
            else {}
        ),
    )
    if not draft:
        invoice1.set_number()
        invoice1.save()
        invoice2.set_number()
        invoice2.save()
        invoice3.set_number()
        invoice3.save()
        invoice4.set_number()
        invoice4.save()
        orphan_invoice.set_number()
        orphan_invoice.save()
        credit.set_number()
        credit.save()

    invoice_line11 = line_model.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=1.2,
        unit_amount=1,
        pool=pool,
        label='Event A',
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-a',
            'primary_event': 'event-a',
            'status': 'presence',
            'check_type': 'foo',
            'check_type_group': 'foobar',
            'check_type_label': 'Foo!',
            'dates': ['2022-09-01', '2022-09-02', '2022-09-03'],
        },
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        description='Thu01, Fri02, Sat03',
    )
    invoice_line12 = line_model.objects.create(
        event_date=datetime.date(2022, 9, 2),
        invoice=invoice1,
        quantity=1,
        unit_amount=2,
        pool=pool,
        label='Event B',
        accounting_code='424243',
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
        details={
            'agenda': 'agenda-b',
            'primary_event': 'event-b',
            'status': 'presence',
            'check_type': 'foo',
            'check_type_group': 'foobar',
            'check_type_label': 'Foo!',
            'dates': ['2022-09-01', '2022-09-02', '2022-09-03'],
        },
        event_slug='agenda-b@event-b',
        agenda_slug='agenda-b',
        activity_label='Agenda B',
        description='Thu01, Fri02, Sat03',
    )
    invoice_line13 = line_model.objects.create(
        event_date=datetime.date(2022, 9, 3),
        invoice=invoice1,
        quantity=1,
        unit_amount=3,
        pool=pool,
        label='Event A',
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-a',
            'primary_event': 'event-a',
            'status': 'presence',
            'dates': ['2022-09-04', '2022-09-05'],
        },
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        description='Sun04, Mon05',
    )
    if not draft:
        payment1 = Payment.objects.create(
            regie=regie,
            amount=1,
            payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
        )
        payment1.set_number()
        payment1.save()
        InvoiceLinePayment.objects.create(
            payment=payment1,
            line=invoice_line11,
            amount=1,
        )
        invoice1.refresh_from_db()
        assert invoice1.remaining_amount == decimal.Decimal('5.2')
        assert invoice1.paid_amount == 1

    line_model.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=1,
        unit_amount=42,
        label='Orphan',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        invoice=orphan_invoice,
    )

    invoice_line21 = line_model.objects.create(
        # non recurring event
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=1,
        unit_amount=1,
        pool=pool,
        label='Event AA',
        event_slug='agenda-a@event-aa',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        description='@overtaking@',
    )
    if not draft:
        payment2 = Payment.objects.create(
            regie=regie,
            amount=1,
            payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
        )
        payment2.set_number()
        payment2.save()
        InvoiceLinePayment.objects.create(
            payment=payment2,
            line=invoice_line21,
            amount=1,
        )
        invoice2.refresh_from_db()
        assert invoice2.remaining_amount == 0
        assert invoice2.paid_amount == 1

    line_model.objects.create(
        # from injected line
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice3,
        quantity=1,
        unit_amount=1,
        pool=pool,
        label='Event A',
        event_slug='injected',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    invoice3.refresh_from_db()
    if not draft:
        assert invoice3.remaining_amount == 1
        assert invoice3.paid_amount == 0

    line_model.objects.create(
        # from injected line
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice4,
        quantity=1,
        unit_amount=1,
        label='Event A',
        event_slug='injected',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    invoice4.refresh_from_db()
    if not draft:
        assert invoice4.remaining_amount == 1
        assert invoice4.paid_amount == 0

    credit_line_model.objects.create(
        event_date=datetime.date(2022, 9, 1),
        label='Event A',
        event_slug='injected',
        unit_amount=1,
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        **(
            {
                'pool': pool,
                'invoice': credit,
                'quantity': -1,
            }
            if draft
            else {
                'credit': credit,
                'quantity': 1,
            }
        ),
    )
    credit.refresh_from_db()
    if not draft:
        assert credit.remaining_amount == 1
        assert credit.assigned_amount == 0

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    if draft:
        assert resp.pyquery(
            'tr[data-invoicing-element-id="%s"]' % invoice1.pk
        ).text() == 'Invoice TEMPORARY-%s dated %s addressed to First1 Name1, amount 6.20€ - download (initial)' % (
            invoice1.pk,
            invoice1.created_at.strftime('%d/%m/%Y'),
        )
    else:
        assert resp.pyquery(
            'tr[data-invoicing-element-id="%s"]' % invoice1.pk
        ).text() == 'Partially paid Invoice F%02d-%s-0000001 dated %s addressed to First1 Name1, amount 6.20€ - download (initial)' % (
            regie.pk,
            invoice1.created_at.strftime('%y-%m'),
            invoice1.created_at.strftime('%d/%m/%Y'),
        )
    lines_url = resp.pyquery('tr[data-invoicing-element-id="%s"]' % invoice1.pk).attr(
        'data-invoicing-element-lines-url'
    )
    assert lines_url == '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/invoice/%s/lines/' % (
        regie.pk,
        campaign.pk,
        pool.pk,
        invoice1.pk,
    )
    lines_resp = app.get(lines_url)
    if draft:
        assert len(lines_resp.pyquery('tr')) == 15
        assert len(lines_resp.pyquery('tr[data-related-invoicing-element-id="%s"]' % invoice1.pk)) == 15
        assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
            'Direct debit: no',
            'Publication date: 01/10/2022',
            'Displayed payment deadline: 15/10/2022',
            'Effective payment deadline: 31/10/2022',
            'Due date: 31/10/2022',
            'Origin: Campaign',
            'User1 Name1',
            'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
            'Agenda A',
            'Event A\nFoo! Thu01, Fri02, Sat03\n424242\n1.00€\n1.2\n1.20€',
            'Event A\nPresence Sun04, Mon05\n424242\n3.00€\n1\n3.00€',
            'User2 Name2',
            'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
            'Agenda B',
            'Event B\nFoo! Thu01, Fri02, Sat03\n424243\n2.00€\n1\n2.00€',
        ]
        assert [PyQuery(a).attr('href') for a in lines_resp.pyquery('tr a')] == [
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?user_external_id=user:1'
            % (regie.pk, campaign.pk, pool.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?invoice_line=%s'
            % (regie.pk, campaign.pk, pool.pk, invoice_line11.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?invoice_line=%s'
            % (regie.pk, campaign.pk, pool.pk, invoice_line13.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?user_external_id=user:2'
            % (regie.pk, campaign.pk, pool.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?invoice_line=%s'
            % (regie.pk, campaign.pk, pool.pk, invoice_line12.pk),
        ]
    else:
        assert len(lines_resp.pyquery('tr')) == 29
        assert len(lines_resp.pyquery('tr[data-related-invoicing-element-id="%s"]' % invoice1.pk)) == 20
        assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
            'Direct debit: no',
            'Publication date: 01/10/2022',
            'Displayed payment deadline: 15/10/2022',
            'Effective payment deadline: 31/10/2022',
            'Due date: 31/10/2022',
            'Origin: Campaign',
            'User1 Name1',
            'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
            'Agenda A',
            'Event A\nFoo! Thu01, Fri02, Sat03\n424242\n1.00€\n1.2\n1.20€',
            'Payments',
            'Payment\nDate\nType\nAmount',
            'R%02d-%s-0000001\n%s\nCash\n1.00€'
            % (
                regie.pk,
                payment1.created_at.strftime('%y-%m'),
                date_format(localtime(payment1.created_at), 'DATETIME_FORMAT'),
            ),
            'Event A\nPresence Sun04, Mon05\n424242\n3.00€\n1\n3.00€',
            'Payments',
            'Payment\nDate\nType\nAmount',
            'No payments for this line',
            'User2 Name2',
            'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
            'Agenda B',
            'Event B\nFoo! Thu01, Fri02, Sat03\n424243\n2.00€\n1\n2.00€',
            'Payments',
            'Payment\nDate\nType\nAmount',
            'No payments for this line',
            'Payments',
            'Payment\nDate\nType\nAmount',
            'R%02d-%s-0000001\n%s\nCash\n1.00€'
            % (
                regie.pk,
                payment1.created_at.strftime('%y-%m'),
                date_format(localtime(payment1.created_at), 'DATETIME_FORMAT'),
            ),
            'Paid amount: 1.00€',
            'Remaining amount: 5.20€',
        ]
        assert [PyQuery(a).attr('href') for a in lines_resp.pyquery('tr a')] == [
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?user_external_id=user:1'
            % (regie.pk, campaign.pk, pool.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?invoice_line=%s'
            % (regie.pk, campaign.pk, pool.pk, invoice_line11.pk),
            '/manage/invoicing/regie/%s/payments/?number=R%s-%s-0000001'
            % (
                regie.pk,
                regie.pk,
                payment1.created_at.strftime('%y-%m'),
            ),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?invoice_line=%s'
            % (regie.pk, campaign.pk, pool.pk, invoice_line13.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?user_external_id=user:2'
            % (regie.pk, campaign.pk, pool.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?invoice_line=%s'
            % (regie.pk, campaign.pk, pool.pk, invoice_line12.pk),
            '/manage/invoicing/regie/%s/payments/?number=R%s-%s-0000001'
            % (
                regie.pk,
                regie.pk,
                payment1.created_at.strftime('%y-%m'),
            ),
        ]
    if draft:
        assert resp.pyquery(
            'tr[data-invoicing-element-id="%s"]' % invoice2.pk
        ).text() == 'Invoice TEMPORARY-%s dated %s addressed to First2 Name2, amount 1.00€ - download (initial)' % (
            invoice2.pk,
            invoice2.created_at.strftime('%d/%m/%Y'),
        )
    else:
        assert resp.pyquery(
            'tr[data-invoicing-element-id="%s"]' % invoice2.pk
        ).text() == 'Paid Invoice F%02d-%s-0000002 dated %s addressed to First2 Name2, amount 1.00€ - download (initial)' % (
            regie.pk,
            invoice2.created_at.strftime('%y-%m'),
            invoice2.created_at.strftime('%d/%m/%Y'),
        )
    lines_url = resp.pyquery('tr[data-invoicing-element-id="%s"]' % invoice2.pk).attr(
        'data-invoicing-element-lines-url'
    )
    assert lines_url == '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/invoice/%s/lines/' % (
        regie.pk,
        campaign.pk,
        pool.pk,
        invoice2.pk,
    )
    lines_resp = app.get(lines_url)
    if draft:
        assert len(lines_resp.pyquery('tr')) == 9
        assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
            'Direct debit: no',
            'Publication date: 01/10/2022',
            'Effective payment deadline: 31/10/2022',
            'Due date: 31/10/2022',
            'Origin: Campaign',
            'User1 Name1',
            'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
            'Agenda A',
            'Event AA\n424242\n1.00€\n1\n1.00€',
        ]
        assert [PyQuery(a).attr('href') for a in lines_resp.pyquery('tr a')] == [
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?user_external_id=user:1'
            % (regie.pk, campaign.pk, pool.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?invoice_line=%s'
            % (regie.pk, campaign.pk, pool.pk, invoice_line21.pk),
        ]
    else:
        assert len(lines_resp.pyquery('tr')) == 17
        assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
            'Direct debit: no',
            'Publication date: 01/10/2022',
            'Effective payment deadline: 31/10/2022',
            'Due date: 31/10/2022',
            'Origin: Campaign',
            'User1 Name1',
            'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
            'Agenda A',
            'Event AA\n424242\n1.00€\n1\n1.00€',
            'Payments',
            'Payment\nDate\nType\nAmount',
            'R%02d-%s-0000002\n%s\nCash\n1.00€'
            % (
                regie.pk,
                payment2.created_at.strftime('%y-%m'),
                date_format(localtime(payment2.created_at), 'DATETIME_FORMAT'),
            ),
            'Payments',
            'Payment\nDate\nType\nAmount',
            'R%02d-%s-0000002\n%s\nCash\n1.00€'
            % (
                regie.pk,
                payment2.created_at.strftime('%y-%m'),
                date_format(localtime(payment2.created_at), 'DATETIME_FORMAT'),
            ),
            'Paid amount: 1.00€',
            'Payments certificate: download',
        ]
        assert [PyQuery(a).attr('href') for a in lines_resp.pyquery('tr a')] == [
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?user_external_id=user:1'
            % (regie.pk, campaign.pk, pool.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?invoice_line=%s'
            % (regie.pk, campaign.pk, pool.pk, invoice_line21.pk),
            '/manage/invoicing/regie/%s/payments/?number=R%s-%s-0000002'
            % (
                regie.pk,
                regie.pk,
                payment2.created_at.strftime('%y-%m'),
            ),
            '/manage/invoicing/regie/%s/payments/?number=R%s-%s-0000002'
            % (
                regie.pk,
                regie.pk,
                payment2.created_at.strftime('%y-%m'),
            ),
            '/manage/invoicing/regie/%s/invoice/%s/payments/pdf/' % (regie.pk, invoice2.pk),
        ]
    if draft:
        assert resp.pyquery(
            'tr[data-invoicing-element-id="%s"]' % invoice3.pk
        ).text() == 'Invoice TEMPORARY-%s dated %s addressed to First3 Name3, amount 1.00€ - download (initial)' % (
            invoice3.pk,
            invoice3.created_at.strftime('%d/%m/%Y'),
        )
    else:
        assert resp.pyquery(
            'tr[data-invoicing-element-id="%s"]' % invoice3.pk
        ).text() == 'Collected Invoice F%02d-%s-0000003 dated %s addressed to First3 Name3, amount 1.00€ - download' % (
            regie.pk,
            invoice3.created_at.strftime('%y-%m'),
            invoice3.created_at.strftime('%d/%m/%Y'),
        )
        collection.draft = True
        collection.save()
        resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
        assert resp.pyquery(
            'tr[data-invoicing-element-id="%s"]' % invoice3.pk
        ).text() == 'Under collection Invoice F%02d-%s-0000003 dated %s addressed to First3 Name3, amount 1.00€ - download' % (
            regie.pk,
            invoice3.created_at.strftime('%y-%m'),
            invoice3.created_at.strftime('%d/%m/%Y'),
        )
    lines_url = resp.pyquery('tr[data-invoicing-element-id="%s"]' % invoice3.pk).attr(
        'data-invoicing-element-lines-url'
    )
    assert lines_url == '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/invoice/%s/lines/' % (
        regie.pk,
        campaign.pk,
        pool.pk,
        invoice3.pk,
    )
    lines_resp = app.get(lines_url)
    if draft:
        assert len(lines_resp.pyquery('tr')) == 8
        assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
            'Direct debit: no',
            'Publication date: 01/10/2022',
            'Effective payment deadline: 31/10/2022',
            'Due date: 31/10/2022',
            'Origin: Campaign',
            'User1 Name1',
            'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
            'Event A\n1.00€\n1\n1.00€',
        ]
    else:
        assert len(lines_resp.pyquery('tr')) == 16
        assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
            'Direct debit: no',
            'Publication date: 01/10/2022',
            'Effective payment deadline: 31/10/2022',
            'Due date: 31/10/2022',
            'Origin: Campaign',
            'User1 Name1',
            'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
            'Event A\n1.00€\n1\n1.00€',
            'Payments',
            'Payment\nDate\nType\nAmount',
            'No payments for this line',
            'Payments',
            'Payment\nDate\nType\nAmount',
            'No payments for this invoice',
            'Collection: TEMPORARY-%s' % collection.pk,
            'Remaining amount: 1.00€',
        ]
    if draft:
        assert resp.pyquery(
            'tr[data-invoicing-element-id="%s"]' % invoice4.pk
        ).text() == 'Invoice TEMPORARY-%s dated %s addressed to First3 Name3, amount 1.00€ - download (initial)' % (
            invoice4.pk,
            invoice4.created_at.strftime('%d/%m/%Y'),
        )
    else:
        assert resp.pyquery(
            'tr[data-invoicing-element-id="%s"]' % invoice4.pk
        ).text() == 'Cancelled Invoice F%02d-%s-0000004 dated %s addressed to First3 Name3, amount 1.00€ - download' % (
            regie.pk,
            invoice4.created_at.strftime('%y-%m'),
            invoice4.created_at.strftime('%d/%m/%Y'),
        )
    lines_url = resp.pyquery('tr[data-invoicing-element-id="%s"]' % invoice4.pk).attr(
        'data-invoicing-element-lines-url'
    )
    assert lines_url == '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/invoice/%s/lines/' % (
        regie.pk,
        campaign.pk,
        pool.pk,
        invoice4.pk,
    )
    lines_resp = app.get(lines_url)
    if draft:
        assert len(lines_resp.pyquery('tr')) == 8
        assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
            'Direct debit: no',
            'Publication date: 01/10/2022',
            'Effective payment deadline: 31/10/2022',
            'Due date: 31/10/2022',
            'Origin: Campaign',
            'User1 Name1',
            'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
            'Event A\n1.00€\n1\n1.00€',
        ]
    else:
        assert len(lines_resp.pyquery('tr')) == 12
        assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
            'Direct debit: no',
            'Publication date: 01/10/2022',
            'Effective payment deadline: 31/10/2022',
            'Due date: 31/10/2022',
            'Origin: Campaign',
            'User1 Name1',
            'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
            'Event A\n1.00€\n1\n1.00€',
            'Cancelled on: %s' % localtime(invoice4.cancelled_at).strftime('%d/%m/%Y %H:%M'),
            'Cancelled by: admin',
            'Reason: Final pool deletion',
            'Description: foo bar\nblah',
        ]

    # test filters
    if draft:
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
            params={'pk': invoice1.pk},
        )
        assert len(resp.pyquery('tr.invoice')) == 1
    else:
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
            params={'number': invoice1.formatted_number},
        )
        assert len(resp.pyquery('tr.invoice')) == 1
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
            params={'number': invoice1.created_at.strftime('%y-%m')},
        )
        assert len(resp.pyquery('tr.invoice')) == 4
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
            params={'payment_number': payment1.formatted_number},
        )
        assert len(resp.pyquery('tr.invoice')) == 1
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
            params={'payment_number': payment1.created_at.strftime('%y-%m')},
        )
        assert len(resp.pyquery('tr.invoice')) == 2
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={'payer_external_id': 'payer:1'},
    )
    assert len(resp.pyquery('tr.invoice')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={'payer_external_id': 'payer:2'},
    )
    assert len(resp.pyquery('tr.invoice')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={'payer_first_name': 'first'},
    )
    assert len(resp.pyquery('tr.invoice')) == 4
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={'payer_first_name': 'first1'},
    )
    assert len(resp.pyquery('tr.invoice')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={'payer_last_name': 'name'},
    )
    assert len(resp.pyquery('tr.invoice')) == 4
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={'payer_last_name': 'name1'},
    )
    assert len(resp.pyquery('tr.invoice')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={'payer_direct_debit': True},
    )
    assert len(resp.pyquery('tr.invoice')) == 2
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={'user_external_id': 'user:1'},
    )
    assert len(resp.pyquery('tr.invoice')) == 4
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={'user_external_id': 'user:2'},
    )
    assert len(resp.pyquery('tr.invoice')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={'user_first_name': 'user'},
    )
    assert len(resp.pyquery('tr.invoice')) == 4
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={'user_first_name': 'user2'},
    )
    assert len(resp.pyquery('tr.invoice')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={'user_last_name': 'name'},
    )
    assert len(resp.pyquery('tr.invoice')) == 4
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={'user_last_name': 'name1'},
    )
    assert len(resp.pyquery('tr.invoice')) == 4
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={
            'total_amount_min': '1',
            'total_amount_min_lookup': 'gt',
        },
    )
    assert len(resp.pyquery('tr.invoice')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={
            'total_amount_min': '1',
            'total_amount_min_lookup': 'gte',
        },
    )
    assert len(resp.pyquery('tr.invoice')) == 4
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={
            'total_amount_max': '6.2',
            'total_amount_max_lookup': 'lt',
        },
    )
    assert len(resp.pyquery('tr.invoice')) == 3
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={
            'total_amount_max': '6.2',
            'total_amount_max_lookup': 'lte',
        },
    )
    assert len(resp.pyquery('tr.invoice')) == 4
    if not draft:
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
            params={'paid': 'yes'},
        )
        assert len(resp.pyquery('tr.invoice')) == 1
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
            params={'paid': 'partially'},
        )
        assert len(resp.pyquery('tr.invoice')) == 1
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
            params={'paid': 'no'},
        )
        assert len(resp.pyquery('tr.invoice')) == 2
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={'agenda': 'agenda-a'},
    )
    assert len(resp.pyquery('tr.invoice')) == 2
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={'agenda': 'agenda-b'},
    )
    assert len(resp.pyquery('tr.invoice')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={'event': 'agenda-a@event-a'},
    )
    assert len(resp.pyquery('tr.invoice')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={'event': 'agenda-a@event-aa'},
    )
    assert len(resp.pyquery('tr.invoice')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={'event': 'agenda-b@event-b'},
    )
    assert len(resp.pyquery('tr.invoice')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={'accounting_code': '42'},
    )
    assert len(resp.pyquery('tr.invoice')) == 0
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={'accounting_code': '424242'},
    )
    assert len(resp.pyquery('tr.invoice')) == 2
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk),
        params={'accounting_code': '424243'},
    )
    assert len(resp.pyquery('tr.invoice')) == 1


@pytest.mark.parametrize('draft', [True, False])
def test_detail_pool_credits(app, admin_user, draft):
    credit_model = DraftInvoice if draft else Credit
    invoice_model = DraftInvoice if draft else Invoice
    line_model = DraftInvoiceLine if draft else CreditLine
    invoice_line_model = DraftInvoiceLine if draft else InvoiceLine

    regie = Regie.objects.create(label='Foo')
    Agenda.objects.create(label='Agenda A', regie=regie)
    Agenda.objects.create(label='Agenda B', regie=regie)
    PaymentType.create_defaults(regie)
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=draft,
        status='completed',
    )
    credit1 = credit_model.objects.create(
        date_publication=campaign.date_publication,
        regie=regie,
        pool=pool,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        origin='campaign',
        **(
            {
                'payer_direct_debit': False,
                'date_payment_deadline': campaign.date_payment_deadline,
                'date_due': campaign.date_due,
            }
            if draft
            else {}
        ),
    )
    credit2 = credit_model.objects.create(
        date_publication=campaign.date_publication,
        regie=regie,
        pool=pool,
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Name2',
        payer_address='42 rue des kangourous\n99999 Kangourou Ville',
        origin='campaign',
        **(
            {
                'payer_direct_debit': True,
                'date_payment_deadline': campaign.date_payment_deadline,
                'date_due': campaign.date_due,
            }
            if draft
            else {}
        ),
    )
    credit3 = credit_model.objects.create(
        date_publication=campaign.date_publication,
        regie=regie,
        pool=pool,
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        payer_address='43 rue des kangourous\n99999 Kangourou Ville',
        origin='campaign',
        **(
            {
                'payer_direct_debit': True,
                'date_payment_deadline': campaign.date_payment_deadline,
                'date_due': campaign.date_due,
            }
            if draft
            else {}
        ),
    )
    orphan_credit = credit_model.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        **(
            {
                'date_payment_deadline': datetime.date(2022, 10, 31),
                'date_due': datetime.date(2022, 10, 31),
            }
            if draft
            else {}
        ),
    )
    invoice = invoice_model.objects.create(
        date_publication=campaign.date_publication,
        date_due=campaign.date_due,
        date_payment_deadline=campaign.date_payment_deadline,
        regie=regie,
        pool=pool,
        payer_external_id='payer:3',
        payer_first_name='First3',
        payer_last_name='Name3',
        payer_address='43 rue des kangourous\n99999 Kangourou Ville',
        origin='campaign',
    )
    if not draft:
        credit1.set_number()
        credit1.save()
        credit2.set_number()
        credit2.save()
        credit3.set_number()
        credit3.save()
        orphan_credit.set_number()
        orphan_credit.save()
        invoice.set_number()
        invoice.save()

    quantity = 1
    if draft:
        quantity = -1

    credit_line11 = line_model.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=1.2 * quantity,
        unit_amount=1,
        pool=pool,
        label='Event A',
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-a',
            'primary_event': 'event-a',
            'status': 'presence',
            'check_type': 'foo',
            'check_type_group': 'foobar',
            'check_type_label': 'Foo!',
            'dates': ['2022-09-01', '2022-09-02', '2022-09-03'],
        },
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        description='Thu01, Fri02, Sat03',
        **(
            {
                'invoice': credit1,
            }
            if draft
            else {
                'credit': credit1,
            }
        ),
    )
    credit_line12 = line_model.objects.create(
        event_date=datetime.date(2022, 9, 2),
        quantity=1 * quantity,
        unit_amount=2,
        pool=pool,
        label='Event B',
        accounting_code='424243',
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
        details={
            'agenda': 'agenda-b',
            'primary_event': 'event-b',
            'status': 'presence',
            'check_type': 'foo',
            'check_type_group': 'foobar',
            'check_type_label': 'Foo!',
            'dates': ['2022-09-01', '2022-09-02', '2022-09-03'],
        },
        event_slug='agenda-b@event-b',
        agenda_slug='agenda-b',
        activity_label='Agenda B',
        description='Thu01, Fri02, Sat03',
        **(
            {
                'invoice': credit1,
            }
            if draft
            else {
                'credit': credit1,
            }
        ),
    )
    credit_line13 = line_model.objects.create(
        event_date=datetime.date(2022, 9, 3),
        quantity=1 * quantity,
        unit_amount=3,
        pool=pool,
        label='Event A',
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-a',
            'primary_event': 'event-a',
            'status': 'presence',
            'dates': ['2022-09-04', '2022-09-05'],
        },
        event_slug='agenda-a@event-a',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        description='Sun04, Mon05',
        **(
            {
                'invoice': credit1,
            }
            if draft
            else {
                'credit': credit1,
            }
        ),
    )
    if not draft:
        payment1 = Payment.objects.create(
            regie=regie,
            amount=1,
            payment_type=PaymentType.objects.get(regie=regie, slug='credit'),
        )
        payment1.set_number()
        payment1.save()
        CreditAssignment.objects.create(
            invoice=invoice,
            payment=payment1,
            credit=credit1,
            amount=1,
        )
        credit1.refresh_from_db()
        assert credit1.remaining_amount == decimal.Decimal('5.2')
        assert credit1.assigned_amount == 1

    line_model.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=1 * quantity,
        unit_amount=42,
        label='Orphan',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        **(
            {
                'invoice': orphan_credit,
            }
            if draft
            else {
                'credit': orphan_credit,
            }
        ),
    )

    credit_line21 = line_model.objects.create(
        # non recurring event
        event_date=datetime.date(2022, 9, 1),
        quantity=1 * quantity,
        unit_amount=1,
        pool=pool,
        label='Event AA',
        event_slug='agenda-a@event-aa',
        agenda_slug='agenda-a',
        activity_label='Agenda A',
        accounting_code='424242',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        **(
            {
                'invoice': credit2,
            }
            if draft
            else {
                'credit': credit2,
            }
        ),
    )
    if not draft:
        payment2 = Payment.objects.create(
            regie=regie,
            amount=1,
            payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
        )
        payment2.set_number()
        payment2.save()
        CreditAssignment.objects.create(
            invoice=invoice,
            payment=payment2,
            credit=credit2,
            amount=1,
        )
        credit2.refresh_from_db()
        assert credit2.remaining_amount == 0
        assert credit2.assigned_amount == 1

    line_model.objects.create(
        # from injected line
        event_date=datetime.date(2022, 9, 1),
        quantity=1 * quantity,
        unit_amount=1,
        pool=pool,
        label='Event A',
        event_slug='injected',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        **(
            {
                'invoice': credit3,
            }
            if draft
            else {
                'credit': credit3,
            }
        ),
    )
    credit3.refresh_from_db()
    if not draft:
        assert credit3.remaining_amount == 1
        assert credit3.assigned_amount == 0

    invoice_line_model.objects.create(
        event_date=datetime.date(2022, 9, 1),
        label='Event A',
        quantity=1,
        unit_amount=1,
        event_slug='injected',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        pool=pool,
        invoice=invoice,
    )
    invoice.refresh_from_db()
    if not draft:
        assert invoice.remaining_amount == 1
        assert invoice.paid_amount == 0

    app = login(app)
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk)
    )
    if draft:
        assert resp.pyquery(
            'tr[data-invoicing-element-id="%s"]' % credit1.pk
        ).text() == 'Credit TEMPORARY-%s dated %s addressed to First1 Name1, amount -6.20€ - download' % (
            credit1.pk,
            credit1.created_at.strftime('%d/%m/%Y'),
        )
    else:
        assert resp.pyquery(
            'tr[data-invoicing-element-id="%s"]' % credit1.pk
        ).text() == 'Partially assigned Credit A%02d-%s-0000001 dated %s addressed to First1 Name1, amount 6.20€ - download' % (
            regie.pk,
            credit1.created_at.strftime('%y-%m'),
            credit1.created_at.strftime('%d/%m/%Y'),
        )
    lines_url = resp.pyquery('tr[data-invoicing-element-id="%s"]' % credit1.pk).attr(
        'data-invoicing-element-lines-url'
    )
    assert lines_url == '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/credit/%s/lines/' % (
        regie.pk,
        campaign.pk,
        pool.pk,
        credit1.pk,
    )
    lines_resp = app.get(lines_url)
    if draft:
        assert len(lines_resp.pyquery('tr')) == 10
        assert len(lines_resp.pyquery('tr[data-related-invoicing-element-id="%s"]' % credit1.pk)) == 10
        assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
            'Origin: Campaign',
            'User1 Name1',
            'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
            'Agenda A',
            'Event A\nFoo! Thu01, Fri02, Sat03\n424242\n1.00€\n-1.2\n-1.20€',
            'Event A\nPresence Sun04, Mon05\n424242\n3.00€\n-1\n-3.00€',
            'User2 Name2',
            'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
            'Agenda B',
            'Event B\nFoo! Thu01, Fri02, Sat03\n424243\n2.00€\n-1\n-2.00€',
        ]
        assert [PyQuery(a).attr('href') for a in lines_resp.pyquery('tr a')] == [
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?user_external_id=user:1'
            % (regie.pk, campaign.pk, pool.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?invoice_line=%s'
            % (regie.pk, campaign.pk, pool.pk, credit_line11.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?invoice_line=%s'
            % (regie.pk, campaign.pk, pool.pk, credit_line13.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?user_external_id=user:2'
            % (regie.pk, campaign.pk, pool.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?invoice_line=%s'
            % (regie.pk, campaign.pk, pool.pk, credit_line12.pk),
        ]
    else:
        assert len(lines_resp.pyquery('tr')) == 16
        assert len(lines_resp.pyquery('tr[data-related-invoicing-element-id="%s"]' % credit1.pk)) == 16
        assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
            'Publication date: 01/10/2022',
            'Origin: Campaign',
            'User1 Name1',
            'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
            'Agenda A',
            'Event A\nFoo! Thu01, Fri02, Sat03\n424242\n1.00€\n1.2\n1.20€',
            'Event A\nPresence Sun04, Mon05\n424242\n3.00€\n1\n3.00€',
            'User2 Name2',
            'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
            'Agenda B',
            'Event B\nFoo! Thu01, Fri02, Sat03\n424243\n2.00€\n1\n2.00€',
            'Assignments',
            'Payment\nDate\nAmount',
            'R%02d-%s-0000001\n%s\n1.00€'
            % (
                regie.pk,
                payment1.created_at.strftime('%y-%m'),
                date_format(localtime(payment1.created_at), 'DATETIME_FORMAT'),
            ),
            'Assigned amount: 1.00€',
            'Remaining amount to assign: 5.20€',
        ]
        assert [PyQuery(a).attr('href') for a in lines_resp.pyquery('tr a')] == [
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?user_external_id=user:1'
            % (regie.pk, campaign.pk, pool.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?credit_line=%s'
            % (regie.pk, campaign.pk, pool.pk, credit_line11.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?credit_line=%s'
            % (regie.pk, campaign.pk, pool.pk, credit_line13.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?user_external_id=user:2'
            % (regie.pk, campaign.pk, pool.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?credit_line=%s'
            % (regie.pk, campaign.pk, pool.pk, credit_line12.pk),
            '/manage/invoicing/regie/%s/payments/?number=R%s-%s-0000001'
            % (
                regie.pk,
                regie.pk,
                payment1.created_at.strftime('%y-%m'),
            ),
        ]
    if draft:
        assert resp.pyquery(
            'tr[data-invoicing-element-id="%s"]' % credit2.pk
        ).text() == 'Credit TEMPORARY-%s dated %s addressed to First2 Name2, amount -1.00€ - download' % (
            credit2.pk,
            credit2.created_at.strftime('%d/%m/%Y'),
        )
    else:
        assert resp.pyquery(
            'tr[data-invoicing-element-id="%s"]' % credit2.pk
        ).text() == 'Assigned Credit A%02d-%s-0000002 dated %s addressed to First2 Name2, amount 1.00€ - download' % (
            regie.pk,
            credit2.created_at.strftime('%y-%m'),
            credit2.created_at.strftime('%d/%m/%Y'),
        )
    lines_url = resp.pyquery('tr[data-invoicing-element-id="%s"]' % credit2.pk).attr(
        'data-invoicing-element-lines-url'
    )
    assert lines_url == '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/credit/%s/lines/' % (
        regie.pk,
        campaign.pk,
        pool.pk,
        credit2.pk,
    )
    lines_resp = app.get(lines_url)
    if draft:
        assert len(lines_resp.pyquery('tr')) == 5
        assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
            'Origin: Campaign',
            'User1 Name1',
            'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
            'Agenda A',
            'Event AA\n424242\n1.00€\n-1\n-1.00€',
        ]
        assert [PyQuery(a).attr('href') for a in lines_resp.pyquery('tr a')] == [
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?user_external_id=user:1'
            % (regie.pk, campaign.pk, pool.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?invoice_line=%s'
            % (regie.pk, campaign.pk, pool.pk, credit_line21.pk),
        ]
    else:
        assert len(lines_resp.pyquery('tr')) == 10
        assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
            'Publication date: 01/10/2022',
            'Origin: Campaign',
            'User1 Name1',
            'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
            'Agenda A',
            'Event AA\n424242\n1.00€\n1\n1.00€',
            'Assignments',
            'Payment\nDate\nAmount',
            'R%02d-%s-0000002\n%s\n1.00€'
            % (
                regie.pk,
                payment2.created_at.strftime('%y-%m'),
                date_format(localtime(payment2.created_at), 'DATETIME_FORMAT'),
            ),
            'Assigned amount: 1.00€',
        ]
        assert [PyQuery(a).attr('href') for a in lines_resp.pyquery('tr a')] == [
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?user_external_id=user:1'
            % (regie.pk, campaign.pk, pool.pk),
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/?credit_line=%s'
            % (regie.pk, campaign.pk, pool.pk, credit_line21.pk),
            '/manage/invoicing/regie/%s/payments/?number=R%s-%s-0000002'
            % (
                regie.pk,
                regie.pk,
                payment2.created_at.strftime('%y-%m'),
            ),
        ]
    if draft:
        assert resp.pyquery(
            'tr[data-invoicing-element-id="%s"]' % credit3.pk
        ).text() == 'Credit TEMPORARY-%s dated %s addressed to First3 Name3, amount -1.00€ - download' % (
            credit3.pk,
            credit3.created_at.strftime('%d/%m/%Y'),
        )
    else:
        assert resp.pyquery(
            'tr[data-invoicing-element-id="%s"]' % credit3.pk
        ).text() == 'Credit A%02d-%s-0000003 dated %s addressed to First3 Name3, amount 1.00€ - download' % (
            regie.pk,
            credit3.created_at.strftime('%y-%m'),
            credit3.created_at.strftime('%d/%m/%Y'),
        )
    lines_url = resp.pyquery('tr[data-invoicing-element-id="%s"]' % credit3.pk).attr(
        'data-invoicing-element-lines-url'
    )
    assert lines_url == '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/credit/%s/lines/' % (
        regie.pk,
        campaign.pk,
        pool.pk,
        credit3.pk,
    )
    lines_resp = app.get(lines_url)
    if draft:
        assert len(lines_resp.pyquery('tr')) == 4
        assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
            'Origin: Campaign',
            'User1 Name1',
            'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
            'Event A\n1.00€\n-1\n-1.00€',
        ]
    else:
        assert len(lines_resp.pyquery('tr')) == 9
        assert [PyQuery(tr).text() for tr in lines_resp.pyquery('tr')] == [
            'Publication date: 01/10/2022',
            'Origin: Campaign',
            'User1 Name1',
            'Description\nAccounting code\nAmount\nQuantity\nSubtotal',
            'Event A\n1.00€\n1\n1.00€',
            'Assignments',
            'Payment\nDate\nAmount',
            'No assignments for this credit',
            'Remaining amount to assign: 1.00€',
        ]

    # test filters
    if draft:
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
            params={'pk': credit1.pk},
        )
        assert len(resp.pyquery('tr.credit')) == 1
    else:
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
            params={'number': credit1.formatted_number},
        )
        assert len(resp.pyquery('tr.credit')) == 1
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
            params={'number': credit1.created_at.strftime('%y-%m')},
        )
        assert len(resp.pyquery('tr.credit')) == 3
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
            params={'payment_number': payment1.formatted_number},
        )
        assert len(resp.pyquery('tr.credit')) == 1
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
            params={'payment_number': payment1.created_at.strftime('%y-%m')},
        )
        assert len(resp.pyquery('tr.credit')) == 2
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
        params={'payer_external_id': 'payer:1'},
    )
    assert len(resp.pyquery('tr.credit')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
        params={'payer_external_id': 'payer:2'},
    )
    assert len(resp.pyquery('tr.credit')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
        params={'payer_first_name': 'first'},
    )
    assert len(resp.pyquery('tr.credit')) == 3
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
        params={'payer_first_name': 'first1'},
    )
    assert len(resp.pyquery('tr.credit')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
        params={'payer_last_name': 'name'},
    )
    assert len(resp.pyquery('tr.credit')) == 3
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
        params={'payer_last_name': 'name1'},
    )
    assert len(resp.pyquery('tr.credit')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
        params={'user_external_id': 'user:1'},
    )
    assert len(resp.pyquery('tr.credit')) == 3
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
        params={'user_external_id': 'user:2'},
    )
    assert len(resp.pyquery('tr.credit')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
        params={'user_first_name': 'user'},
    )
    assert len(resp.pyquery('tr.credit')) == 3
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
        params={'user_first_name': 'user2'},
    )
    assert len(resp.pyquery('tr.credit')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
        params={'user_last_name': 'name'},
    )
    assert len(resp.pyquery('tr.credit')) == 3
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
        params={'user_last_name': 'name1'},
    )
    assert len(resp.pyquery('tr.credit')) == 3
    if draft:
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
            params={
                'total_amount_min': '-1',
                'total_amount_min_lookup': 'gt',
            },
        )
        assert len(resp.pyquery('tr.credit')) == 0
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
            params={
                'total_amount_min': '-1',
                'total_amount_min_lookup': 'gte',
            },
        )
        assert len(resp.pyquery('tr.credit')) == 2
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
            params={
                'total_amount_max': '-6.2',
                'total_amount_max_lookup': 'lt',
            },
        )
        assert len(resp.pyquery('tr.credit')) == 0
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
            params={
                'total_amount_max': '-6.2',
                'total_amount_max_lookup': 'lte',
            },
        )
        assert len(resp.pyquery('tr.credit')) == 1
    else:
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
            params={
                'total_amount_min': '1',
                'total_amount_min_lookup': 'gt',
            },
        )
        assert len(resp.pyquery('tr.credit')) == 1
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
            params={
                'total_amount_min': '1',
                'total_amount_min_lookup': 'gte',
            },
        )
        assert len(resp.pyquery('tr.credit')) == 3
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
            params={
                'total_amount_max': '6.2',
                'total_amount_max_lookup': 'lt',
            },
        )
        assert len(resp.pyquery('tr.credit')) == 2
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
            params={
                'total_amount_max': '6.2',
                'total_amount_max_lookup': 'lte',
            },
        )
        assert len(resp.pyquery('tr.credit')) == 3
    if not draft:
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
            params={'assigned': 'yes'},
        )
        assert len(resp.pyquery('tr.credit')) == 1
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
            params={'assigned': 'partially'},
        )
        assert len(resp.pyquery('tr.credit')) == 1
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
            params={'assigned': 'no'},
        )
        assert len(resp.pyquery('tr.credit')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
        params={'agenda': 'agenda-a'},
    )
    assert len(resp.pyquery('tr.credit')) == 2
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
        params={'agenda': 'agenda-b'},
    )
    assert len(resp.pyquery('tr.credit')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
        params={'event': 'agenda-a@event-a'},
    )
    assert len(resp.pyquery('tr.credit')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
        params={'event': 'agenda-a@event-aa'},
    )
    assert len(resp.pyquery('tr.credit')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
        params={'event': 'agenda-b@event-b'},
    )
    assert len(resp.pyquery('tr.credit')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
        params={'accounting_code': '42'},
    )
    assert len(resp.pyquery('tr.credit')) == 0
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
        params={'accounting_code': '424242'},
    )
    assert len(resp.pyquery('tr.credit')) == 2
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/?credits' % (regie.pk, campaign.pk, pool.pk),
        params={'accounting_code': '424243'},
    )
    assert len(resp.pyquery('tr.credit')) == 1


def test_stop_pool(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    campaign2 = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 10, 1),
        date_end=datetime.date(2022, 11, 1),
        date_publication=datetime.date(2022, 11, 1),
        date_payment_deadline=datetime.date(2022, 11, 30),
        date_due=datetime.date(2022, 11, 30),
        date_debit=datetime.date(2022, 12, 15),
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='registered',
    )

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, pool.pk))
    resp = resp.form.submit()
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk)
    )
    pool.refresh_from_db()
    assert pool.status == 'failed'
    assert pool.exception == 'Stopped'

    pool.status = 'running'
    pool.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, pool.pk))
    resp = resp.form.submit()
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk)
    )
    pool.refresh_from_db()
    assert pool.status == 'failed'
    assert pool.exception == 'Stopped'

    pool.status = 'running'
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (0, campaign.pk, pool.pk), status=404)
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, 0, pool.pk), status=404)
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign2.pk, pool.pk),
        status=404,
    )
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, 0), status=404)

    pool.status = 'completed'
    pool.save()
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, pool.pk),
        status=404,
    )

    pool.status = 'failed'
    pool.save()
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, pool.pk),
        status=404,
    )


@pytest.mark.parametrize('draft', [True, False])
def test_invoice_pdf(app, admin_user, draft):
    invoice_model = DraftInvoice if draft else Invoice
    line_model = DraftInvoiceLine if draft else InvoiceLine

    regie = Regie.objects.create(label='Foo')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline_displayed=datetime.date(2022, 10, 15),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    assert campaign.invoice_model == 'middle'
    pool = Pool.objects.create(
        campaign=campaign,
        draft=draft,
        status='completed',
    )
    invoice = invoice_model.objects.create(
        label='Invoice from 01/09/2022 to 30/09/2022',
        date_publication=campaign.date_publication,
        date_payment_deadline_displayed=campaign.date_payment_deadline_displayed,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=False,
    )
    if not draft:
        invoice.set_number()
        invoice.save()

    line_model.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice,
        quantity=1.2,
        unit_amount=1,
        pool=pool,
        label='Label 11',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-1',
            'primary_event': 'event-1',
            'status': 'presence',
            'check_type': 'foo',
            'check_type_group': 'foobar',
            'check_type_label': 'Foo!',
            'dates': ['2022-09-01', '2022-09-02', '2022-09-03'],
        },
        event_slug='agenda-1@event-1',
        agenda_slug='agenda-1',
        activity_label='Agenda 1',
        description='Thu01, Fri02, Sat03',
    )
    line_model.objects.create(
        event_date=datetime.date(2022, 9, 2),
        invoice=invoice,
        quantity=1,
        unit_amount=2,
        pool=pool,
        label='Label 12',
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
        description='@overtaking@',
    )
    line_model.objects.create(
        event_date=datetime.date(2022, 9, 3),
        invoice=invoice,
        quantity=1,
        unit_amount=3,
        pool=pool,
        label='Label 13',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )

    app = login(app)
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/invoice/%s/pdf/?html'
        % (regie.pk, campaign.pk, pool.pk, invoice.pk)
    )
    assert resp.pyquery('#document-label').text() == 'Invoice from 01/09/2022 to 30/09/2022'
    assert resp.pyquery('#regie-label').text() == 'Foo'
    assert resp.pyquery('address#to').text() == 'First1 Name1\n41 rue des kangourous\n99999 Kangourou Ville'
    if draft:
        assert resp.pyquery('dl#informations').text() == 'Invoice number:\nTEMPORARY-%s\nDate:\n%s' % (
            invoice.pk,
            date_format(localtime(invoice.created_at), 'DATE_FORMAT'),
        )
    else:
        assert resp.pyquery('dl#informations').text() == 'Invoice number:\nF%02d-%s-0000001\nDate:\n%s' % (
            regie.pk,
            invoice.created_at.strftime('%y-%m'),
            date_format(localtime(invoice.created_at), 'DATE_FORMAT'),
        )
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda 1',
        'Label 11\nFoo! Thu01, Fri02, Sat03\n1.00€\n1.2\n1.20€',
        '',
        'Label 13\n3.00€\n1\n3.00€',
        'Subtotal:\n4.20€',
        '',
        'User2 Name2',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Label 12\n2.00€\n1\n2.00€',
        'Subtotal:\n2.00€',
        'Total amount:\n6.20€',
    ]
    assert resp.pyquery('p.deadline').text() == 'Payment deadline: 15/10/2022'
    assert len(resp.pyquery('table#invoice-lines-details')) == 0
    invoice.refresh_from_db()
    invoice.date_payment_deadline_displayed = None
    invoice.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/invoice/%s/pdf/?html'
        % (regie.pk, campaign.pk, pool.pk, invoice.pk)
    )
    assert resp.pyquery('p.deadline').text() == 'Payment deadline: 31/10/2022'

    campaign.invoice_model = 'basic'
    campaign.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/invoice/%s/pdf/?html'
        % (regie.pk, campaign.pk, pool.pk, invoice.pk)
    )
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda 1',
        'Label 11\nFoo!\n1.00€\n1.2\n1.20€',
        '',
        'Label 13\n3.00€\n1\n3.00€',
        'Subtotal:\n4.20€',
        '',
        'User2 Name2',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Label 12\n2.00€\n1\n2.00€',
        'Subtotal:\n2.00€',
        'Total amount:\n6.20€',
    ]
    assert len(resp.pyquery('table#invoice-lines-details')) == 0

    campaign.invoice_model = 'full'
    campaign.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/invoice/%s/pdf/?html'
        % (regie.pk, campaign.pk, pool.pk, invoice.pk)
    )
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Agenda 1',
        'Label 11\nFoo!\n1.00€\n1.2\n1.20€',
        '',
        'Label 13\n3.00€\n1\n3.00€',
        'Subtotal:\n4.20€',
        '',
        'User2 Name2',
        'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
        'Label 12\n2.00€\n1\n2.00€',
        'Subtotal:\n2.00€',
        'Total amount:\n6.20€',
    ]
    assert len(resp.pyquery('table#invoice-lines-details')) == 1
    assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines-details tr')] == [
        '',
        'User1 Name1',
        'Services\nDetails',
        'Agenda 1',
        'Label 11\nFoo! Thu01, Fri02, Sat03',
    ]

    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/invoice/%s/pdf/?html'
        % (regie.pk, campaign.pk, pool.pk, invoice.pk)
    )

    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/invoice/%s/pdf/?html'
        % (0, campaign.pk, pool.pk, invoice.pk),
        status=404,
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/invoice/%s/pdf/?html'
        % (regie.pk, 0, pool.pk, invoice.pk),
        status=404,
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/invoice/%s/pdf/?html'
        % (regie.pk, campaign.pk, 0, invoice.pk),
        status=404,
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/invoice/%s/pdf/?html'
        % (regie.pk, campaign.pk, pool.pk, 0),
        status=404,
    )
    other_regie = Regie.objects.create(label='Foo')
    other_campaign = Campaign.objects.create(
        regie=other_regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    other_pool = Pool.objects.create(
        campaign=other_campaign,
        draft=draft,
        status='completed',
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/invoice/%s/pdf/?html'
        % (other_regie.pk, campaign.pk, pool.pk, invoice.pk),
        status=404,
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/invoice/%s/pdf/?html'
        % (regie.pk, other_campaign.pk, pool.pk, invoice.pk),
        status=404,
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/invoice/%s/pdf/?html'
        % (regie.pk, campaign.pk, other_pool.pk, invoice.pk),
        status=404,
    )


@pytest.mark.parametrize('draft', [True, False])
def test_credit_pdf(app, admin_user, draft):
    credit_model = DraftInvoice if draft else Credit
    line_model = DraftInvoiceLine if draft else CreditLine
    quantity = 1
    if draft:
        quantity = -1

    regie = Regie.objects.create(label='Foo')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    assert campaign.invoice_model == 'middle'
    pool = Pool.objects.create(
        campaign=campaign,
        draft=draft,
        status='completed',
    )
    credit = credit_model.objects.create(
        label='Credit from 01/09/2022 to 30/09/2022',
        date_publication=campaign.date_publication,
        regie=regie,
        pool=pool,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        **(
            {
                'date_payment_deadline': campaign.date_payment_deadline,
                'date_due': campaign.date_due,
                'payer_direct_debit': False,
            }
            if draft
            else {}
        ),
    )
    if not draft:
        credit.set_number()
        credit.save()

    line_model.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=1.2 * quantity,
        unit_amount=1,
        pool=pool,
        label='Label 11',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        details={
            'agenda': 'agenda-1',
            'primary_event': 'event-1',
            'status': 'presence',
            'check_type': 'foo',
            'check_type_group': 'foobar',
            'check_type_label': 'Foo!',
            'dates': ['2022-09-01', '2022-09-02', '2022-09-03'],
        },
        event_slug='agenda-1@event-1',
        agenda_slug='agenda-1',
        activity_label='Agenda 1',
        description='Thu01, Fri02, Sat03',
        **(
            {
                'invoice': credit,
            }
            if draft
            else {
                'credit': credit,
            }
        ),
    )
    line_model.objects.create(
        event_date=datetime.date(2022, 9, 2),
        quantity=1 * quantity,
        unit_amount=2,
        pool=pool,
        label='Label 12',
        user_external_id='user:2',
        user_first_name='User2',
        user_last_name='Name2',
        **(
            {
                'invoice': credit,
            }
            if draft
            else {
                'credit': credit,
            }
        ),
    )
    line_model.objects.create(
        event_date=datetime.date(2022, 9, 3),
        quantity=1 * quantity,
        unit_amount=3,
        pool=pool,
        label='Label 13',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        **(
            {
                'invoice': credit,
            }
            if draft
            else {
                'credit': credit,
            }
        ),
    )

    app = login(app)
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/credit/%s/pdf/?html'
        % (regie.pk, campaign.pk, pool.pk, credit.pk)
    )
    assert resp.pyquery('#document-label').text() == 'Credit from 01/09/2022 to 30/09/2022'
    assert resp.pyquery('#regie-label').text() == 'Foo'
    assert resp.pyquery('address#to').text() == 'First1 Name1\n41 rue des kangourous\n99999 Kangourou Ville'
    if draft:
        assert resp.pyquery('dl#informations').text() == 'Invoice number:\nTEMPORARY-%s\nDate:\n%s' % (
            credit.pk,
            date_format(localtime(credit.created_at), 'DATE_FORMAT'),
        )
        assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
            '',
            'User1 Name1',
            'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
            'Agenda 1',
            'Label 11\nFoo! Thu01, Fri02, Sat03\n1.00€\n-1.2\n-1.20€',
            '',
            'Label 13\n3.00€\n-1\n-3.00€',
            'Subtotal:\n-4.20€',
            '',
            'User2 Name2',
            'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
            'Label 12\n2.00€\n-1\n-2.00€',
            'Subtotal:\n-2.00€',
            'Total amount:\n-6.20€',
        ]
        assert resp.pyquery('p.deadline').text() == 'Payment deadline: 31/10/2022'
        assert len(resp.pyquery('table#invoice-lines-details')) == 0
    else:
        assert resp.pyquery('dl#informations').text() == 'Credit number:\nA%02d-%s-0000001\nDate:\n%s' % (
            regie.pk,
            credit.created_at.strftime('%y-%m'),
            date_format(localtime(credit.created_at), 'DATE_FORMAT'),
        )
        assert [PyQuery(tr).text() for tr in resp.pyquery('table#lines tr')] == [
            '',
            'User1 Name1',
            'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
            'Agenda 1',
            'Label 11\nFoo! Thu01, Fri02, Sat03\n1.00€\n1.2\n1.20€',
            '',
            'Label 13\n3.00€\n1\n3.00€',
            'Subtotal:\n4.20€',
            '',
            'User2 Name2',
            'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
            'Label 12\n2.00€\n1\n2.00€',
            'Subtotal:\n2.00€',
            'Total amount:\n6.20€',
        ]
        assert len(resp.pyquery('table#lines-details')) == 0

    campaign.invoice_model = 'basic'
    campaign.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/credit/%s/pdf/?html'
        % (regie.pk, campaign.pk, pool.pk, credit.pk)
    )
    if draft:
        assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
            '',
            'User1 Name1',
            'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
            'Agenda 1',
            'Label 11\nFoo!\n1.00€\n-1.2\n-1.20€',
            '',
            'Label 13\n3.00€\n-1\n-3.00€',
            'Subtotal:\n-4.20€',
            '',
            'User2 Name2',
            'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
            'Label 12\n2.00€\n-1\n-2.00€',
            'Subtotal:\n-2.00€',
            'Total amount:\n-6.20€',
        ]
        assert len(resp.pyquery('table#invoice-lines-details')) == 0
    else:
        assert [PyQuery(tr).text() for tr in resp.pyquery('table#lines tr')] == [
            '',
            'User1 Name1',
            'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
            'Agenda 1',
            'Label 11\nFoo!\n1.00€\n1.2\n1.20€',
            '',
            'Label 13\n3.00€\n1\n3.00€',
            'Subtotal:\n4.20€',
            '',
            'User2 Name2',
            'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
            'Label 12\n2.00€\n1\n2.00€',
            'Subtotal:\n2.00€',
            'Total amount:\n6.20€',
        ]
        assert len(resp.pyquery('table#lines-details')) == 0

    campaign.invoice_model = 'full'
    campaign.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/credit/%s/pdf/?html'
        % (regie.pk, campaign.pk, pool.pk, credit.pk)
    )
    if draft:
        assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines tr')] == [
            '',
            'User1 Name1',
            'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
            'Agenda 1',
            'Label 11\nFoo!\n1.00€\n-1.2\n-1.20€',
            '',
            'Label 13\n3.00€\n-1\n-3.00€',
            'Subtotal:\n-4.20€',
            '',
            'User2 Name2',
            'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
            'Label 12\n2.00€\n-1\n-2.00€',
            'Subtotal:\n-2.00€',
            'Total amount:\n-6.20€',
        ]
        assert len(resp.pyquery('table#invoice-lines-details')) == 1
        assert [PyQuery(tr).text() for tr in resp.pyquery('table#invoice-lines-details tr')] == [
            '',
            'User1 Name1',
            'Services\nDetails',
            'Agenda 1',
            'Label 11\nFoo! Thu01, Fri02, Sat03',
        ]
    else:
        assert [PyQuery(tr).text() for tr in resp.pyquery('table#lines tr')] == [
            '',
            'User1 Name1',
            'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
            'Agenda 1',
            'Label 11\nFoo!\n1.00€\n1.2\n1.20€',
            '',
            'Label 13\n3.00€\n1\n3.00€',
            'Subtotal:\n4.20€',
            '',
            'User2 Name2',
            'Services\nDetails\nUnit amount\nQuantity\nTotal amount',
            'Label 12\n2.00€\n1\n2.00€',
            'Subtotal:\n2.00€',
            'Total amount:\n6.20€',
        ]
        assert len(resp.pyquery('table#lines-details')) == 1
        assert [PyQuery(tr).text() for tr in resp.pyquery('table#lines-details tr')] == [
            '',
            'User1 Name1',
            'Services\nDetails',
            'Agenda 1',
            'Label 11\nFoo! Thu01, Fri02, Sat03',
        ]

    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/credit/%s/pdf/?html'
        % (regie.pk, campaign.pk, pool.pk, credit.pk)
    )

    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/credit/%s/pdf/?html'
        % (0, campaign.pk, pool.pk, credit.pk),
        status=404,
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/credit/%s/pdf/?html'
        % (regie.pk, 0, pool.pk, credit.pk),
        status=404,
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/credit/%s/pdf/?html'
        % (regie.pk, campaign.pk, 0, credit.pk),
        status=404,
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/credit/%s/pdf/?html'
        % (regie.pk, campaign.pk, pool.pk, 0),
        status=404,
    )
    other_regie = Regie.objects.create(label='Foo')
    other_campaign = Campaign.objects.create(
        regie=other_regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    other_pool = Pool.objects.create(
        campaign=other_campaign,
        draft=draft,
        status='completed',
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/credit/%s/pdf/?html'
        % (other_regie.pk, campaign.pk, pool.pk, credit.pk),
        status=404,
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/credit/%s/pdf/?html'
        % (regie.pk, other_campaign.pk, pool.pk, credit.pk),
        status=404,
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/credit/%s/pdf/?html'
        % (regie.pk, campaign.pk, other_pool.pk, credit.pk),
        status=404,
    )


def test_journal_pool(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    campaign2 = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 10, 1),
        date_end=datetime.date(2022, 11, 1),
        date_publication=datetime.date(2022, 11, 1),
        date_payment_deadline=datetime.date(2022, 11, 30),
        date_due=datetime.date(2022, 11, 30),
        date_debit=datetime.date(2022, 12, 15),
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='completed',
    )

    app = login(app)
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk))

    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (0, campaign.pk, pool.pk), status=404)
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, 0, pool.pk), status=404)
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign2.pk, pool.pk),
        status=404,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, 0), status=404
    )

    # orphan line
    DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=1,
        status='success',
    )

    line = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=1,
        status='success',
        pool=pool,
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert '<span class="meta meta-success">1</span>' in resp
    assert 'meta-warning' not in resp
    assert 'meta-error' not in resp
    line.status = 'error'
    line.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert 'meta-success' not in resp
    assert 'meta-warning' not in resp
    assert '<span class="meta meta-error">1</span>' in resp
    line.status = 'warning'
    line.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert 'meta-success' not in resp
    assert '<span class="meta meta-warning">1</span>' in resp
    assert 'meta-error' not in resp
    line.delete()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert 'meta-success' not in resp
    assert 'meta-warning' not in resp
    assert 'meta-error' not in resp

    pool.draft = False
    pool.save()
    line = JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=1,
        status='success',
        pool=pool,
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert '<span class="meta meta-success">1</span>' in resp
    assert 'meta-warning' not in resp
    assert 'meta-error' not in resp
    line.status = 'error'
    line.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert 'meta-success' not in resp
    assert 'meta-warning' not in resp
    assert '<span class="meta meta-error">1</span>' in resp
    line.status = 'warning'
    line.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert 'meta-success' not in resp
    assert '<span class="meta meta-warning">1</span>' in resp
    assert 'meta-error' not in resp
    line.status = 'error'
    line.error_status = 'ignored'
    line.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert 'meta-success' not in resp
    assert 'meta-warning' not in resp
    assert '<span class="meta meta-error">1</span>' not in resp
    line.error_status = 'fixed'
    line.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert 'meta-success' not in resp
    assert 'meta-warning' not in resp
    assert '<span class="meta meta-error">1</span>' not in resp


@pytest.mark.parametrize('draft', [True, False])
def test_journal_pool_lines(settings, app, admin_user, draft):
    invoice_model = DraftInvoice if draft else Invoice
    iline_model = DraftInvoiceLine if draft else InvoiceLine
    credit_model = DraftInvoice if draft else Credit
    cline_model = DraftInvoiceLine if draft else CreditLine
    line_model = DraftJournalLine if draft else JournalLine

    regie = Regie.objects.create(label='Foo')
    Agenda.objects.create(label='Agenda A', regie=regie)
    Agenda.objects.create(label='Agenda B', regie=regie)
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=draft,
        status='completed',
    )
    invoice1 = invoice_model.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
        payer_external_id='payer:1',
    )
    invoice2 = invoice_model.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
        payer_external_id='payer:2',
    )
    orphan_invoice = invoice_model.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
    )
    credit = credit_model.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        pool=pool,
        **(
            {
                'date_due': campaign.date_due,
                'date_payment_deadline': campaign.date_payment_deadline,
            }
            if draft
            else {}
        ),
    )
    if not draft:
        invoice1.set_number()
        invoice1.save()
        invoice2.set_number()
        invoice2.save()
        orphan_invoice.set_number()
        orphan_invoice.save()
        credit.set_number()
        credit.save()

    invoice_line = iline_model.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=1,
        unit_amount=42,
        label='Orphan',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        invoice=orphan_invoice,
    )
    line_model.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=42,
        status='success',
        label='Orphan',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
        invoice_line=invoice_line,
    )

    invoice_line = iline_model.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=1,
        unit_amount=1,
        pool=pool,
        details={'foo': 'bar'},
        user_external_id='user:1',
        user_first_name='First1',
        user_last_name='Last1',
    )
    lines = [
        line_model.objects.create(
            label='Event A',
            event_date=datetime.date(2022, 9, 1),
            amount=1,
            status='success',
            pool=pool,
            pricing_data={'foo': 'bar'},
            event={
                'agenda': 'agenda-a',
                'slug': 'event-a',
                'primary_event': None,
            },
            booking={'foo': 'bar'},
            accounting_code='424242',
            user_external_id='user:1',
            user_first_name='First1',
            user_last_name='Last1',
            payer_external_id='payer:1',
            payer_first_name='First1',
            payer_last_name='Last1',
            payer_address='41 rue des kangourous\n99999 Kangourou Ville',
            payer_direct_debit=False,
            invoice_line=invoice_line,
        ),
    ]
    errors = [
        ('PricingNotFound', {}),
        ('CriteriaConditionNotFound', {'category': 'cat-foo'}),
        ('MultipleDefaultCriteriaCondition', {'category': 'cat-foo'}),
        ('PricingDataError', {'criterias': {'qf': 'qf-1', 'foo': 'bar'}}),
        ('PricingDataFormatError', {'pricing': 'foobar', 'wanted': 'decimal'}),
        ('MinPricingDataError', {'criterias': {'qf': 'qf-1', 'foo': 'bar'}}),
        ('MinPricingDataFormatError', {'pricing': 'foobar', 'wanted': 'decimal'}),
        ('PricingReductionRateError', {}),
        ('PricingReductionRateFormatError', {'reduction_rate': 'foo', 'wanted': 'decimal'}),
        ('PricingReductionRateValueError', {'reduction_rate': 42}),
        ('PricingEffortRateTargetError', {}),
        ('PricingEffortRateTargetFormatError', {'effort_rate_target': 'foo', 'wanted': 'decimal'}),
        ('PricingEffortRateTargetValueError', {'effort_rate_target': 42}),
        ('PricingAccountingCodeError', {}),
        ('PricingUnknownCheckStatusError', {'status': 'unknown'}),
        ('PricingEventNotCheckedError', {}),
        ('PricingBookingNotCheckedError', {}),
        ('PricingMultipleBookingError', {}),
        ('PricingBookingCheckTypeError', {'reason': 'not-found'}),
        (
            'PricingBookingCheckTypeError',
            {
                'check_type_group': 'foo-bar',
                'check_type': 'foo-reason',
                'reason': 'not-configured',
            },
        ),
        (
            'PricingBookingCheckTypeError',
            {'check_type_group': 'foo-bar', 'check_type': 'foo-reason', 'reason': 'wrong-kind'},
        ),
        ('PayerError', {'reason': 'empty-template'}),
        ('PayerError', {'reason': 'empty-result'}),
        ('PayerError', {'reason': 'syntax-error'}),
        ('PayerError', {'reason': 'variable-error'}),
        ('PayerError', {'reason': 'missing-card-model'}),
        ('PayerDataError', {'key': 'foo', 'reason': 'not-defined'}),
        ('PayerDataError', {'key': 'foo', 'reason': 'empty-result'}),
        ('PayerDataError', {'key': 'foo', 'reason': 'not-a-boolean'}),
    ]
    for i, (error, error_details) in enumerate(errors):
        lines.append(
            line_model.objects.create(
                label='Event AA' if i > 0 else 'Event B',
                event_date=datetime.date(2022, 9, 1),
                amount=1,
                status='warning' if error == 'PricingNotFound' else 'error',
                pool=pool,
                pricing_data={
                    'error': error,
                    'error_details': error_details,
                },
                event={
                    'agenda': 'agenda-a' if i > 0 else 'agenda-b',
                    'slug': 'event-aa--date' if i > 0 else 'event-b',
                    'primary_event': 'event-aa' if i > 0 else None,
                },
                user_external_id='user:1',
                user_first_name='First1',
                user_last_name='Last1',
                payer_external_id='payer:2',
                payer_first_name='First2',
                payer_last_name='Last2',
                payer_address='42 rue des kangourous\n99999 Kangourou Ville',
                payer_direct_debit=True,
            )
        )
    lines[-1].error_status = 'ignored'
    lines[-1].save()
    lines[-2].error_status = 'fixed'
    lines[-2].save()

    injected_line = InjectedLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=1,
        user_external_id='user:2',
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Last2',
        payer_address='42 rue des kangourous\n99999 Kangourou Ville',
        payer_direct_debit=True,
        regie=regie,
    )
    invoice_line = iline_model.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=1,
        unit_amount=1,
        pool=pool,
        details={'foo': 'bar'},
        user_external_id='user:2',
        user_first_name='First2',
        user_last_name='Last2',
    )

    quantity = -1 if draft else 1
    credit_line = cline_model.objects.create(
        event_date=datetime.date(2022, 9, 1),
        quantity=5,
        unit_amount=1 * quantity,
        pool=pool,
        **(
            {
                'invoice': credit,
            }
            if draft
            else {
                'credit': credit,
            }
        ),
    )
    lines.append(
        line_model.objects.create(
            label='Event B',
            event_date=datetime.date(2022, 9, 1),
            amount=-1,
            status='success',
            pool=pool,
            pricing_data={'foo': 'bar'},
            user_external_id='user:2',
            user_first_name='First2',
            user_last_name='Last2',
            payer_external_id='payer:2',
            payer_first_name='First2',
            payer_last_name='Last2',
            payer_address='42 rue des kangourous\n99999 Kangourou Ville',
            payer_direct_debit=True,
            from_injected_line=injected_line,
            **(
                {
                    'invoice_line': credit_line,
                }
                if draft
                else {
                    'credit_line': credit_line,
                }
            ),
        ),
    )

    def format_status(value):
        return (' '.join([v.strip() for v in value.split('\n')])).strip()

    app = login(app)
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert [o[0] for o in resp.form['status'].options] == [
        '',
        'success',
        'success_injected',
        'warning',
        'error',
        'CriteriaConditionNotFound',
        'MinPricingDataError',
        'MinPricingDataFormatError',
        'MultipleDefaultCriteriaCondition',
        'PayerDataError',
        'PayerError',
        'PricingAccountingCodeError',
        'PricingBookingCheckTypeError',
        'PricingBookingNotCheckedError',
        'PricingDataError',
        'PricingDataFormatError',
        'PricingEffortRateTargetError',
        'PricingEffortRateTargetFormatError',
        'PricingEffortRateTargetValueError',
        'PricingEventNotCheckedError',
        'PricingMultipleBookingError',
        'PricingNotFound',
        'PricingReductionRateError',
        'PricingReductionRateFormatError',
        'PricingReductionRateValueError',
        'PricingUnknownCheckStatusError',
    ]
    assert len(resp.pyquery('td.status')) == 31
    assert format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[0].pk).text()) == 'Success'
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[0].pk).text().strip()
        == "{'foo': 'bar'} {'agenda': 'agenda-a', 'primary_event': None, 'slug': 'event-a'} {'foo': 'bar'}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[1].pk).text())
        == 'Warning (Agenda pricing not found)'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[1].pk).text().strip()
        == "{'error': 'PricingNotFound', 'error_details': {}} "
        "{'agenda': 'agenda-b', 'primary_event': None, 'slug': 'event-b'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[2].pk).text())
        == 'Error (No matching criteria for category: cat-foo) replay'
    )
    settings.CAMPAIGN_SHOW_FIX_ERROR = True
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert [o[0] for o in resp.form['status'].options] == [
        '',
        'success',
        'success_injected',
        'warning',
        'error',
        'error_todo',
        'error_ignored',
        'error_fixed',
        'CriteriaConditionNotFound',
        'MinPricingDataError',
        'MinPricingDataFormatError',
        'MultipleDefaultCriteriaCondition',
        'PayerDataError',
        'PayerError',
        'PricingAccountingCodeError',
        'PricingBookingCheckTypeError',
        'PricingBookingNotCheckedError',
        'PricingDataError',
        'PricingDataFormatError',
        'PricingEffortRateTargetError',
        'PricingEffortRateTargetFormatError',
        'PricingEffortRateTargetValueError',
        'PricingEventNotCheckedError',
        'PricingMultipleBookingError',
        'PricingNotFound',
        'PricingReductionRateError',
        'PricingReductionRateFormatError',
        'PricingReductionRateValueError',
        'PricingUnknownCheckStatusError',
    ]
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[2].pk).text())
        == 'Error (No matching criteria for category: cat-foo) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[2].pk).text().strip()
        == "{'error': 'CriteriaConditionNotFound', 'error_details': {'category': 'cat-foo'}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[3].pk).text())
        == 'Error (Multiple default criteria found for category: cat-foo) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[3].pk).text().strip()
        == "{'error': 'MultipleDefaultCriteriaCondition', 'error_details': {'category': 'cat-foo'}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[4].pk).text())
        == 'Error (Impossible to determine a pricing for criterias: qf-1 (category: qf), bar (category: foo)) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[4].pk).text().strip()
        == "{'error': 'PricingDataError', 'error_details': {'criterias': {'foo': 'bar', 'qf': 'qf-1'}}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[5].pk).text())
        == 'Error (Pricing is not a decimal: foobar) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[5].pk).text().strip()
        == "{'error': 'PricingDataFormatError', 'error_details': {'pricing': 'foobar', 'wanted': 'decimal'}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[6].pk).text())
        == 'Error (Impossible to determine a minimal pricing for criterias: qf-1 (category: qf), bar (category: foo)) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[6].pk).text().strip()
        == "{'error': 'MinPricingDataError', 'error_details': {'criterias': {'foo': 'bar', 'qf': 'qf-1'}}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[7].pk).text())
        == 'Error (Minimal pricing is not a decimal: foobar) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[7].pk).text().strip()
        == "{'error': 'MinPricingDataFormatError', 'error_details': {'pricing': 'foobar', 'wanted': 'decimal'}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[8].pk).text())
        == 'Error (Impossible to determine a reduction rate) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[8].pk).text().strip()
        == "{'error': 'PricingReductionRateError', 'error_details': {}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[9].pk).text())
        == 'Error (Reduction rate is not a decimal: foo) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[9].pk).text().strip()
        == "{'error': 'PricingReductionRateFormatError', 'error_details': {'reduction_rate': 'foo', 'wanted': 'decimal'}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[10].pk).text())
        == 'Error (Reduction rate bad value: 42) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[10].pk).text().strip()
        == "{'error': 'PricingReductionRateValueError', 'error_details': {'reduction_rate': 42}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[11].pk).text())
        == 'Error (Impossible to determine an effort rate target) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[11].pk).text().strip()
        == "{'error': 'PricingEffortRateTargetError', 'error_details': {}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[12].pk).text())
        == 'Error (Effort rate target is not a decimal: foo) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[12].pk).text().strip()
        == "{'error': 'PricingEffortRateTargetFormatError', 'error_details': {'effort_rate_target': 'foo', 'wanted': 'decimal'}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[13].pk).text())
        == 'Error (Effort rate target bad value: 42) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[13].pk).text().strip()
        == "{'error': 'PricingEffortRateTargetValueError', 'error_details': {'effort_rate_target': 42}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[14].pk).text())
        == 'Error (Impossible to determine an accounting code) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[14].pk).text().strip()
        == "{'error': 'PricingAccountingCodeError', 'error_details': {}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[15].pk).text())
        == 'Error (Unknown check status: unknown) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[15].pk).text().strip()
        == "{'error': 'PricingUnknownCheckStatusError', 'error_details': {'status': 'unknown'}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[16].pk).text())
        == 'Error (Event is not checked) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[16].pk).text().strip()
        == "{'error': 'PricingEventNotCheckedError', 'error_details': {}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[17].pk).text())
        == 'Error (Booking is not checked) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[17].pk).text().strip()
        == "{'error': 'PricingBookingNotCheckedError', 'error_details': {}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[18].pk).text())
        == 'Error (Multiple booking found) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[18].pk).text().strip()
        == "{'error': 'PricingMultipleBookingError', 'error_details': {}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[19].pk).text())
        == 'Error (Check type error: not found) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[19].pk).text().strip()
        == "{'error': 'PricingBookingCheckTypeError', 'error_details': {'reason': 'not-found'}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[20].pk).text())
        == 'Error (Check type error: pricing not configured (group: foo-bar, check type: foo-reason)) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[20].pk).text().strip()
        == "{'error': 'PricingBookingCheckTypeError', 'error_details': {'check_type': 'foo-reason', "
        "'check_type_group': 'foo-bar', 'reason': 'not-configured'}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[21].pk).text())
        == 'Error (Check type error: wrong kind (group: foo-bar, check type: foo-reason)) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[21].pk).text().strip()
        == "{'error': 'PricingBookingCheckTypeError', 'error_details': {'check_type': 'foo-reason', "
        "'check_type_group': 'foo-bar', 'reason': 'wrong-kind'}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[22].pk).text())
        == 'Error (Impossible to determine payer: template is empty) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[22].pk).text().strip()
        == "{'error': 'PayerError', 'error_details': {'reason': 'empty-template'}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[23].pk).text())
        == 'Error (Impossible to determine payer: result is empty) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[23].pk).text().strip()
        == "{'error': 'PayerError', 'error_details': {'reason': 'empty-result'}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[24].pk).text())
        == 'Error (Impossible to determine payer: syntax error) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[24].pk).text().strip()
        == "{'error': 'PayerError', 'error_details': {'reason': 'syntax-error'}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[25].pk).text())
        == 'Error (Impossible to determine payer: variable error) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[25].pk).text().strip()
        == "{'error': 'PayerError', 'error_details': {'reason': 'variable-error'}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[26].pk).text())
        == 'Error (Impossible to determine payer: card model is not configured) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[26].pk).text().strip()
        == "{'error': 'PayerError', 'error_details': {'reason': 'missing-card-model'}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[27].pk).text())
        == 'Error (Impossible to get payer foo: mapping not defined) ignore - mark as fixed replay'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[27].pk).text().strip()
        == "{'error': 'PayerDataError', 'error_details': {'key': 'foo', 'reason': 'not-defined'}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[28].pk).text())
        == 'Fixed (Impossible to get payer foo: result is empty) reset'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[28].pk).text().strip()
        == "{'error': 'PayerDataError', 'error_details': {'key': 'foo', 'reason': 'empty-result'}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[29].pk).text())
        == 'Ignored (Impossible to get payer foo: result is not a boolean) reset'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[29].pk).text().strip()
        == "{'error': 'PayerDataError', 'error_details': {'key': 'foo', 'reason': 'not-a-boolean'}} "
        "{'agenda': 'agenda-a', 'primary_event': 'event-aa', 'slug': 'event-aa--date'} {}"
    )
    assert (
        format_status(resp.pyquery('tr[data-line-id="%s"] td.status' % lines[30].pk).text())
        == 'Success (Injected)'
    )
    assert (
        resp.pyquery('tr[data-details-for-line-id="%s"] td pre' % lines[30].pk).text().strip()
        == "{'foo': 'bar'} {} {}"
    )

    # test filters
    if draft:
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
            params={'invoice_id': invoice1.pk},
        )
        assert len(resp.pyquery('tr td.status')) == 1
    else:
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
            params={'invoice_number': invoice1.formatted_number},
        )
        assert len(resp.pyquery('tr td.status')) == 1
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
            params={'invoice_number': invoice1.created_at.strftime('%y-%m')},
        )
        assert len(resp.pyquery('tr td.status')) == 1
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
            params={'credit_number': credit.formatted_number},
        )
        assert len(resp.pyquery('tr td.status')) == 1
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
            params={'credit_number': credit.created_at.strftime('%y-%m')},
        )
        assert len(resp.pyquery('tr td.status')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'invoice_line': lines[0].invoice_line_id},
    )
    assert len(resp.pyquery('tr td.status')) == 1
    if draft:
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
            params={'invoice_line': lines[-1].invoice_line_id},
        )
        assert len(resp.pyquery('tr td.status')) == 1
    else:
        resp = app.get(
            '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
            params={'credit_line': lines[-1].credit_line_id},
        )
        assert len(resp.pyquery('tr td.status')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'payer_external_id': 'payer:1'},
    )
    assert len(resp.pyquery('tr td.status')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'payer_external_id': 'payer:2'},
    )
    assert len(resp.pyquery('tr td.status')) == 30
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'payer_first_name': 'first'},
    )
    assert len(resp.pyquery('tr td.status')) == 31
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'payer_first_name': 'first1'},
    )
    assert len(resp.pyquery('tr td.status')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'payer_last_name': 'last'},
    )
    assert len(resp.pyquery('tr td.status')) == 31
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'payer_last_name': 'last1'},
    )
    assert len(resp.pyquery('tr td.status')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'payer_direct_debit': True},
    )
    assert len(resp.pyquery('tr td.status')) == 30
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'user_external_id': 'user:1'},
    )
    assert len(resp.pyquery('tr td.status')) == 30
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'user_external_id': 'user:2'},
    )
    assert len(resp.pyquery('tr td.status')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'user_first_name': 'first'},
    )
    assert len(resp.pyquery('tr td.status')) == 31
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'user_first_name': 'first1'},
    )
    assert len(resp.pyquery('tr td.status')) == 30
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'user_last_name': 'last'},
    )
    assert len(resp.pyquery('tr td.status')) == 31
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'user_last_name': 'last1'},
    )
    assert len(resp.pyquery('tr td.status')) == 30
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'agenda': 'agenda-a'},
    )
    assert len(resp.pyquery('tr td.status')) == 29
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'agenda': 'agenda-b'},
    )
    assert len(resp.pyquery('tr td.status')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'event': 'agenda-a@event-a'},
    )
    assert len(resp.pyquery('tr td.status')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'event': 'agenda-a@event-aa'},
    )
    assert len(resp.pyquery('tr td.status')) == 28
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'event': 'agenda-b@event-b'},
    )
    assert len(resp.pyquery('tr td.status')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'accounting_code': '42'},
    )
    assert len(resp.pyquery('tr td.status')) == 0
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'accounting_code': '424242'},
    )
    assert len(resp.pyquery('tr td.status')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'status': 'success'},
    )
    assert len(resp.pyquery('tr td.status')) == 2
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'status': 'success_injected'},
    )
    assert len(resp.pyquery('tr td.status')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'status': 'warning'},
    )
    assert len(resp.pyquery('tr td.status')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'status': 'error'},
    )
    assert len(resp.pyquery('tr td.status')) == 28
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'status': 'PayerError'},
    )
    assert len(resp.pyquery('tr td.status')) == 5
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'status': 'PricingBookingNotCheckedError'},
    )
    assert len(resp.pyquery('tr td.status')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'status': 'error_todo'},
    )
    assert len(resp.pyquery('tr td.status')) == 26
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'status': 'error_ignored'},
    )
    assert len(resp.pyquery('tr td.status')) == 1
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        params={'status': 'error_fixed'},
    )
    assert len(resp.pyquery('tr td.status')) == 1

    Pool.objects.create(
        campaign=campaign,
        draft=draft,
        status='completed',
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert not resp.pyquery('a.error-replay')


@pytest.mark.parametrize('draft', [True, False])
def test_journal_pool_lines_link(settings, app, admin_user, draft):
    settings.KNOWN_SERVICES = {}

    line_model = DraftJournalLine if draft else JournalLine

    regie = Regie.objects.create(label='Foo')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=draft,
        status='completed',
    )

    line = line_model.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=1,
        pool=pool,
    )

    app = login(app)
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert 'see agenda' not in resp
    assert 'see event' not in resp

    line.event = {
        'agenda': 'foobar',
    }
    line.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert '<a href="/manage/pricing/agenda/foobar/">see agenda</a>' in resp
    assert 'see event' not in resp

    line.event['slug'] = 'bazbaz'
    line.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert '<a href="/manage/pricing/agenda/foobar/">see agenda</a>' in resp
    assert 'see event' not in resp

    settings.KNOWN_SERVICES['chrono'] = {'default': {'url': 'https://chrono.dev/'}}
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert '<a href="/manage/pricing/agenda/foobar/">see agenda</a>' in resp
    assert '<a href="https://chrono.dev/manage/agendas/foobar/events/bazbaz/">see event</a>' in resp


def test_delete_pool(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    campaign2 = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 10, 1),
        date_end=datetime.date(2022, 11, 1),
        date_publication=datetime.date(2022, 11, 1),
        date_payment_deadline=datetime.date(2022, 11, 30),
        date_due=datetime.date(2022, 11, 30),
        date_debit=datetime.date(2022, 12, 15),
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='completed',
    )
    invoice = DraftInvoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
    )
    invoice_line = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice,
        quantity=1,
        unit_amount=1,
        pool=pool,
    )
    DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=1,
        status='success',
        pool=pool,
        invoice_line=invoice_line,
    )

    app = login(app)
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk)
    )
    resp = resp.form.submit()
    assert Pool.objects.count() == 0
    assert DraftInvoice.objects.count() == 0
    assert DraftInvoiceLine.objects.count() == 0
    assert DraftJournalLine.objects.count() == 0
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/campaign/%s/#open:pools' % (regie.pk, campaign.pk)
    )
    campaign.refresh_from_db()
    assert campaign.invalid is True

    pool.save()
    pool2 = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='completed',
    )
    campaign.invalid = False
    campaign.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk)
    )
    resp.form.submit()
    assert Pool.objects.count() == 1
    campaign.refresh_from_db()
    # pool is not the last, don't invalidate the campaign
    assert campaign.invalid is False

    pool2.delete()
    pool.draft = True
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (0, campaign.pk, pool.pk), status=404)
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, 0, pool.pk), status=404)
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign2.pk, pool.pk),
        status=404,
    )
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, 0), status=404)

    pool.status = 'registered'
    pool.save()
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk),
        status=404,
    )

    pool.status = 'running'
    pool.save()
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk),
        status=404,
    )

    campaign.finalized = True
    campaign.save()
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk),
        status=404,
    )

    campaign.finalized = False
    campaign.save()
    pool.draft = False
    pool.status = 'error'
    pool.save()
    invoice = Invoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
    )
    invoice_line = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice,
        quantity=1,
        unit_amount=1,
        pool=pool,
    )
    JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=1,
        status='success',
        pool=pool,
        invoice_line=invoice_line,
    )
    credit = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        pool=pool,
    )
    credit_line = CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit,
        quantity=5,
        unit_amount=1,
        pool=pool,
    )
    JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=1,
        status='success',
        pool=pool,
        credit_line=credit_line,
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk),
    )
    cancellation_reason = InvoiceCancellationReason.objects.get()
    cancellation_reason2 = CreditCancellationReason.objects.get()
    assert cancellation_reason.slug == 'final-pool-deletion'
    assert cancellation_reason.label == 'Final pool deletion'
    resp.form['cancellation_description'] = 'foo bar blah'
    resp.form.submit()
    assert Pool.objects.count() == 0
    assert Invoice.objects.count() == 1
    assert InvoiceLine.objects.count() == 1
    assert JournalLine.objects.count() == 0
    assert Credit.objects.count() == 1
    assert CreditLine.objects.count() == 1
    campaign.refresh_from_db()
    # pool is not draft, don't invalidate the campaign
    assert campaign.invalid is False
    invoice.refresh_from_db()
    assert invoice.cancelled_at is not None
    assert invoice.cancelled_by == admin_user
    assert invoice.cancellation_reason == cancellation_reason
    assert invoice.cancellation_description == 'foo bar blah'
    assert invoice.pool is None
    credit.refresh_from_db()
    assert credit.cancelled_at is not None
    assert credit.cancelled_by == admin_user
    assert credit.cancellation_reason == cancellation_reason2
    assert credit.cancellation_description == 'foo bar blah'
    assert credit.pool is None

    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk))


@pytest.mark.parametrize('draft', [True, False])
def test_set_error_status_line(settings, app, admin_user, draft):
    settings.CAMPAIGN_SHOW_FIX_ERROR = True
    regie = Regie.objects.create(label='Foo')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=draft,
        status='completed',
    )
    line_model = DraftJournalLine if draft else JournalLine
    line = line_model.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=1,
        status='success',
        pool=pool,
    )

    app = login(app)
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/reset/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/ignore/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/fix/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        not in resp
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/reset/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
        status=404,
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/ignore/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
        status=404,
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/fix/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
        status=404,
    )

    line.status = 'warning'
    line.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/reset/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/ignore/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/fix/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        not in resp
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/reset/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
        status=404,
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/ignore/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
        status=404,
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/fix/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
        status=404,
    )

    line.status = 'error'
    line.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/reset/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/ignore/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        in resp
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/fix/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        in resp
    )

    line.error_status = 'ignored'
    line.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/reset/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        in resp
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/ignore/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/fix/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        not in resp
    )

    line.error_status = 'fixed'
    line.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/reset/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        in resp
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/ignore/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/fix/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        not in resp
    )

    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/reset/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
    )
    line.refresh_from_db()
    assert line.error_status == ''

    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/ignore/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
    )
    line.refresh_from_db()
    assert line.error_status == 'ignored'

    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/fix/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
    )
    line.refresh_from_db()
    assert line.error_status == 'fixed'

    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/reset/'
        % (0, campaign.pk, pool.pk, line.pk),
        status=404,
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/reset/'
        % (regie.pk, 0, pool.pk, line.pk),
        status=404,
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/reset/'
        % (regie.pk, campaign.pk, 0, line.pk),
        status=404,
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/reset/'
        % (regie.pk, campaign.pk, pool.pk, 0),
        status=404,
    )


@mock.patch('lingo.invoicing.views.pool.replay_error')
def test_replay_error(mock_replay, app, admin_user):
    regie = Regie.objects.create(label='Foo')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='completed',
    )
    line = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=1,
        status='success',
        pool=pool,
    )

    app = login(app)
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        not in resp
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
        status=404,
    )
    assert mock_replay.call_args_list == []

    line.status = 'error'
    line.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        in resp
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
    )
    assert mock_replay.call_args_list == [mock.call(line)]

    mock_replay.side_effect = ChronoError('foo bar error')
    resp = app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
    )
    resp = resp.follow()
    assert '<li class="error">foo bar error</li>' in resp

    mock_replay.side_effect = Agenda.DoesNotExist
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
        status=404,
    )

    mock_replay.side_effect = None
    mock_replay.reset_mock()
    line.error_status = 'fixed'
    line.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        not in resp
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
        status=404,
    )

    line.error_status = ''
    line.save()
    pool.draft = False
    pool.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        not in resp
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
        status=404,
    )

    pool.draft = True
    pool.save()
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (0, campaign.pk, pool.pk, line.pk),
        status=404,
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, 0, pool.pk, line.pk),
        status=404,
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, 0, line.pk),
        status=404,
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, pool.pk, 0),
        status=404,
    )

    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
    )
    Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='completed',
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
        status=404,
    )

import datetime
import uuid
from unittest import mock

import pytest

from lingo.agendas.models import Agenda, AgendaUnlockLog
from lingo.invoicing.models import (
    Campaign,
    CampaignAsyncJob,
    CollectionDocket,
    Credit,
    CreditLine,
    DraftInvoice,
    DraftJournalLine,
    Invoice,
    InvoiceLine,
    InvoiceLinePayment,
    Payment,
    PaymentDocket,
    PaymentType,
    Pool,
    Regie,
)
from tests.invoicing.utils import mocked_requests_send
from tests.utils import login

pytestmark = pytest.mark.django_db


def test_manager_as_nothing(app, manager_user):
    regie = Regie.objects.create(
        label='Foo',
    )
    payment_type = PaymentType.objects.create(label='Foo', regie=regie)
    payment_type2 = PaymentType.objects.create(label='Foo', regie=regie)
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
    draft_invoice = DraftInvoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
    )
    line = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=1,
        status='error',
        pool=pool,
    )
    invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer',
    )
    docket = PaymentDocket.objects.create(
        regie=regie,
        date_end=datetime.date(2022, 10, 1),
        draft=False,
    )
    docket.payment_types.add(payment_type2)
    payment = Payment.objects.create(
        regie=regie,
        amount=1,
        payment_type=payment_type2,
        docket=docket,
    )
    credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer',
    )
    collection = CollectionDocket.objects.create(
        regie=regie,
        date_end=datetime.date(2022, 10, 1),
        draft=False,
    )

    app = login(app, username='manager', password='manager')

    app.get('/manage/', status=403)
    app.get('/manage/inspect/', status=403)
    app.post('/manage/inspect/test-template/', status=403)
    resp = app.get('/manage/invoicing/regies/')
    assert list(resp.context['object_list']) == []
    assert '/manage/invoicing/regie/add/' not in resp
    assert '/manage/invoicing/import/' not in resp
    assert '/manage/invoicing/export/' not in resp
    assert '/manage/invoicing/regie/%s/' % regie.pk not in resp
    assert '/manage/invoicing/appearance/' not in resp
    assert '/manage/invoicing/payers/' not in resp
    assert '/manage/invoicing/cancellation-reasons/' not in resp

    app.get('/manage/invoicing/regie/add/', status=403)
    app.get('/manage/invoicing/import/', status=403)
    app.get('/manage/invoicing/export/', status=403)
    app.get('/manage/invoicing/regie/%s/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/parameters/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/payment-type/add/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/payment-type/%s/edit/' % (regie.pk, payment_type.pk), status=403)
    app.get('/manage/invoicing/regie/%s/payment-type/%s/delete/' % (regie.pk, payment_type.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaigns/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/campaign/add/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/invoices/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk), status=403)
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, pool.pk), status=403
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/invoice/%s/pdf/'
        % (regie.pk, campaign.pk, pool.pk, draft_invoice.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/invoice/%s/lines/'
        % (regie.pk, campaign.pk, pool.pk, draft_invoice.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/ignore/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/add-corrective-campaign/' % (regie.pk, campaign.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agendas/add/' % (regie.pk, campaign.pk), status=403
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/0/add/' % (regie.pk, campaign.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/job/%s/' % (regie.pk, campaign.pk, uuid.uuid4()), status=403
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/report/event-amounts/' % (regie.pk, campaign.pk), status=403
    )
    app.get('/manage/invoicing/regie/%s/invoices/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/invoices/?&ods' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/invoice/%s/pdf/' % (regie.pk, invoice.pk), status=403)
    app.get('/manage/invoicing/regie/%s/invoice/%s/dynamic/pdf/' % (regie.pk, invoice.pk), status=403)
    app.get('/manage/invoicing/regie/%s/invoice/%s/payments/pdf/' % (regie.pk, invoice.pk), status=403)
    app.get('/manage/invoicing/ajax/regie/%s/invoice/%s/lines/' % (regie.pk, invoice.pk), status=403)
    app.get('/manage/invoicing/regie/%s/invoice/%s/cancel/' % (regie.pk, invoice.pk), status=403)
    app.get('/manage/invoicing/regie/%s/invoice/%s/edit/dates/' % (regie.pk, invoice.pk), status=403)
    app.get('/manage/invoicing/regie/%s/collections/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/collections/invoices/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/collection/add/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/collection/%s/' % (regie.pk, collection.pk), status=403)
    app.get('/manage/invoicing/regie/%s/collection/%s/edit/' % (regie.pk, collection.pk), status=403)
    app.get('/manage/invoicing/regie/%s/collection/%s/validate/' % (regie.pk, collection.pk), status=403)
    app.get('/manage/invoicing/regie/%s/collection/%s/delete/' % (regie.pk, collection.pk), status=403)
    app.get('/manage/invoicing/regie/%s/payments/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/payments/?&ods' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/payment/%s/pdf/' % (regie.pk, payment.pk), status=403)
    app.get('/manage/invoicing/regie/%s/payment/%s/cancel/' % (regie.pk, payment.pk), status=403)
    app.get('/manage/invoicing/regie/%s/dockets/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/dockets/?&ods' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/dockets/?&pdf' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/dockets/payments/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/docket/add/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk), status=403)
    app.get('/manage/invoicing/regie/%s/docket/%s/export/ods/' % (regie.pk, docket.pk), status=403)
    app.get('/manage/invoicing/regie/%s/docket/%s/export/pdf/' % (regie.pk, docket.pk), status=403)
    app.get('/manage/invoicing/regie/%s/docket/%s/edit/' % (regie.pk, docket.pk), status=403)
    app.get(
        '/manage/invoicing/regie/%s/docket/%s/payment-type/%s/' % (regie.pk, docket.pk, payment_type.pk),
        status=403,
    )
    app.get('/manage/invoicing/regie/%s/docket/%s/validate/' % (regie.pk, docket.pk), status=403)
    app.get('/manage/invoicing/regie/%s/docket/%s/delete/' % (regie.pk, docket.pk), status=403)
    app.get('/manage/invoicing/regie/%s/credits/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/credit/%s/pdf/' % (regie.pk, credit.pk), status=403)
    app.get('/manage/invoicing/ajax/regie/%s/credit/%s/lines/' % (regie.pk, credit.pk), status=403)
    app.get('/manage/invoicing/regie/%s/credit/%s/cancel/' % (regie.pk, credit.pk), status=403)
    app.get('/manage/invoicing/regie/%s/refunds/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/payers/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/payer/payer/transactions/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/transactions/for-event/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/non-invoiced-lines/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/edit/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/edit/permissions/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/edit/payer/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/edit/payer-mapping/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/edit/counters/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/edit/publishing/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/export/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/inspect/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/history/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/history/compare/' % regie.pk, status=403)

    DraftJournalLine.objects.all().delete()
    DraftInvoice.objects.all().delete()
    Pool.objects.all().delete()
    Campaign.objects.all().delete()
    app.get('/manage/invoicing/regie/%s/delete/' % regie.pk, status=403)

    app.get('/manage/invoicing/appearance/', status=403)

    app.get('/manage/invoicing/cancellation-reasons/', status=403)
    app.get('/manage/invoicing/cancellation-reason/invoice/add/', status=403)
    app.get('/manage/invoicing/cancellation-reason/invoice/0/edit/', status=403)
    app.get('/manage/invoicing/cancellation-reason/invoice/0/delete/', status=403)
    app.get('/manage/invoicing/cancellation-reason/credit/add/', status=403)
    app.get('/manage/invoicing/cancellation-reason/credit/0/edit/', status=403)
    app.get('/manage/invoicing/cancellation-reason/credit/0/delete/', status=403)
    app.get('/manage/invoicing/cancellation-reason/payment/add/', status=403)
    app.get('/manage/invoicing/cancellation-reason/payment/0/edit/', status=403)
    app.get('/manage/invoicing/cancellation-reason/payment/0/delete/', status=403)


@mock.patch('requests.Session.send', side_effect=mocked_requests_send)
def test_manager_as_viewer(mock_send, settings, app, manager_user):
    settings.SHOW_NON_INVOICED_LINES = True
    settings.CAMPAIGN_SHOW_FIX_ERROR = True
    regie = Regie.objects.create(
        label='Foo',
        view_role=manager_user.groups.first(),
        with_campaigns=True,
        payer_carddef_reference='default:card_model_1',
    )
    payment_type = PaymentType.objects.create(label='Foo', regie=regie)
    payment_type2 = PaymentType.objects.create(label='Foo', regie=regie)
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
    draft_invoice = DraftInvoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
    )
    line = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=1,
        status='error',
        pool=pool,
    )
    invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer',
    )
    invoice_line = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice,
        quantity=1,
        unit_amount=1,
    )
    docket = PaymentDocket.objects.create(
        regie=regie,
        date_end=datetime.date(2022, 10, 1),
        draft=False,
    )
    docket.payment_types.add(payment_type2)
    payment = Payment.objects.create(
        regie=regie,
        amount=1,
        payment_type=payment_type2,
        docket=docket,
    )
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=invoice_line,
        amount=1,
    )
    credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit,
        quantity=1,
        unit_amount=1,
    )
    collection = CollectionDocket.objects.create(
        regie=regie,
        date_end=datetime.date(2022, 10, 1),
        draft=False,
    )
    regie2 = Regie.objects.create(
        label='Foo',
    )

    app = login(app, username='manager', password='manager')

    resp = app.get('/manage/')
    app.get('/manage/inspect/', status=403)
    app.post('/manage/inspect/test-template/', status=403)
    assert '/manage/invoicing/regies/' in resp
    resp = app.get('/manage/invoicing/regies/')
    assert list(resp.context['object_list']) == [regie]
    assert '/manage/invoicing/regie/add/' not in resp
    assert '/manage/invoicing/import/' not in resp
    assert '/manage/invoicing/export/' not in resp
    assert '/manage/invoicing/regie/%s/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/' % regie2.pk not in resp
    assert '/manage/invoicing/appearance/' not in resp
    assert '/manage/invoicing/payers/' not in resp
    assert '/manage/invoicing/cancellation-reasons/' not in resp

    resp = app.get('/manage/invoicing/regie/%s/' % regie.pk)
    assert '/manage/invoicing/regie/%s/parameters/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/campaigns/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/invoices/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/collections/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/payments/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/dockets/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/credits/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/refunds/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/payers/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/non-invoiced-lines/' % regie.pk in resp
    app.get('/manage/invoicing/regie/%s/refunds/' % regie.pk)
    app.get('/manage/invoicing/regie/%s/non-invoiced-lines/' % regie.pk)

    resp = app.get('/manage/invoicing/regie/%s/campaigns/' % regie.pk)
    assert '/manage/invoicing/regie/%s/campaign/add/' % regie.pk not in resp
    app.get('/manage/invoicing/regie/%s/campaign/add/' % regie.pk, status=403)

    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/invoices/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk) in resp
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/invoices/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk), status=403)

    pool.draft = False
    pool.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert '/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk) not in resp
    app.get('/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk), status=403)

    pool.draft = True
    pool.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk) in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/invoice/%s/pdf/'
        % (regie.pk, campaign.pk, pool.pk, draft_invoice.pk)
        in resp
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/invoice/%s/lines/'
        % (regie.pk, campaign.pk, pool.pk, draft_invoice.pk)
        in resp
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/invoice/%s/pdf/'
        % (regie.pk, campaign.pk, pool.pk, draft_invoice.pk)
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/invoice/%s/lines/'
        % (regie.pk, campaign.pk, pool.pk, draft_invoice.pk)
    )

    pool.status = 'running'
    pool.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, pool.pk) not in resp
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, pool.pk),
        status=403,
    )

    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/ignore/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        not in resp
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/ignore/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
        status=403,
    )

    campaign.finalized = True
    campaign.save()
    agenda = Agenda.objects.create(label='Foo', regie=regie)
    campaign.agendas.add(agenda)
    AgendaUnlockLog.objects.create(campaign=campaign, agenda=agenda)
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/add-corrective-campaign/' % (regie.pk, campaign.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agendas/add/' % (regie.pk, campaign.pk) not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, campaign.pk, agenda.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/report/event-amounts/' % (regie.pk, campaign.pk) not in resp
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/add-corrective-campaign/' % (regie.pk, campaign.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agendas/add/' % (regie.pk, campaign.pk), status=403
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, campaign.pk, agenda.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/report/event-amounts/' % (regie.pk, campaign.pk), status=403
    )

    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/job/%s/' % (regie.pk, campaign.pk, uuid.uuid4()), status=403
    )

    resp = app.get('/manage/invoicing/regie/%s/invoices/' % regie.pk)
    assert '/manage/invoicing/regie/%s/invoice/%s/pdf/' % (regie.pk, invoice.pk) in resp
    assert '/manage/invoicing/regie/%s/invoice/%s/dynamic/pdf/' % (regie.pk, invoice.pk) in resp
    assert '/manage/invoicing/ajax/regie/%s/invoice/%s/lines/' % (regie.pk, invoice.pk) in resp
    assert '?&ods' not in resp
    app.get('/manage/invoicing/regie/%s/invoice/%s/pdf/' % (regie.pk, invoice.pk))
    app.get('/manage/invoicing/regie/%s/invoice/%s/dynamic/pdf/' % (regie.pk, invoice.pk))
    app.get('/manage/invoicing/regie/%s/invoices/?&ods' % regie.pk, status=403)
    resp = app.get('/manage/invoicing/ajax/regie/%s/invoice/%s/lines/' % (regie.pk, invoice.pk))
    assert '/manage/invoicing/regie/%s/invoice/%s/payments/pdf/' % (regie.pk, invoice.pk) in resp
    app.get('/manage/invoicing/regie/%s/invoice/%s/payments/pdf/' % (regie.pk, invoice.pk))
    InvoiceLinePayment.objects.all().delete()
    resp = app.get('/manage/invoicing/ajax/regie/%s/invoice/%s/lines/' % (regie.pk, invoice.pk))
    assert '/manage/invoicing/regie/%s/invoice/%s/cancel/' % (regie.pk, invoice.pk) not in resp
    app.get('/manage/invoicing/regie/%s/invoice/%s/cancel/' % (regie.pk, invoice.pk), status=403)
    assert '/manage/invoicing/regie/%s/invoice/%s/edit/dates/' % (regie.pk, invoice.pk) not in resp
    app.get('/manage/invoicing/regie/%s/invoice/%s/edit/dates/' % (regie.pk, invoice.pk), status=403)

    resp = app.get('/manage/invoicing/regie/%s/collections/' % regie.pk)
    assert '/manage/invoicing/regie/%s/collections/invoices/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/collection/%s/' % (regie.pk, collection.pk) in resp

    resp = app.get('/manage/invoicing/regie/%s/collections/invoices/' % regie.pk)
    assert '/manage/invoicing/regie/%s/collection/add/' % regie.pk not in resp
    app.get('/manage/invoicing/regie/%s/collection/add/' % regie.pk, status=403)

    collection.draft = True
    collection.save()
    resp = app.get('/manage/invoicing/regie/%s/collection/%s/' % (regie.pk, collection.pk))
    assert '/manage/invoicing/regie/%s/collection/%s/edit/' % (regie.pk, collection.pk) not in resp
    assert '/manage/invoicing/regie/%s/collection/%s/validate/' % (regie.pk, collection.pk) not in resp
    assert '/manage/invoicing/regie/%s/collection/%s/delete/' % (regie.pk, collection.pk) not in resp
    app.get('/manage/invoicing/regie/%s/collection/%s/edit/' % (regie.pk, collection.pk), status=403)
    app.get('/manage/invoicing/regie/%s/collection/%s/validate/' % (regie.pk, collection.pk), status=403)
    app.get('/manage/invoicing/regie/%s/collection/%s/delete/' % (regie.pk, collection.pk), status=403)

    resp = app.get('/manage/invoicing/regie/%s/payments/' % regie.pk)
    assert '/manage/invoicing/regie/%s/payment/%s/pdf/' % (regie.pk, payment.pk) in resp
    assert '/manage/invoicing/regie/%s/payment/%s/cancel/' % (regie.pk, payment.pk) not in resp
    assert '?&ods' not in resp
    app.get('/manage/invoicing/regie/%s/payment/%s/pdf/' % (regie.pk, payment.pk))
    app.get('/manage/invoicing/regie/%s/payment/%s/cancel/' % (regie.pk, payment.pk), status=403)
    app.get('/manage/invoicing/regie/%s/payments/?&ods' % regie.pk, status=403)

    resp = app.get('/manage/invoicing/regie/%s/credits/' % regie.pk)
    assert '/manage/invoicing/regie/%s/credit/%s/pdf/' % (regie.pk, credit.pk) in resp
    assert '/manage/invoicing/ajax/regie/%s/credit/%s/lines/' % (regie.pk, credit.pk) in resp
    app.get('/manage/invoicing/regie/%s/credit/%s/pdf/' % (regie.pk, credit.pk))
    resp = app.get('/manage/invoicing/ajax/regie/%s/credit/%s/lines/' % (regie.pk, credit.pk))
    assert '/manage/invoicing/regie/%s/credit/%s/cancel/' % (regie.pk, credit.pk) not in resp
    app.get('/manage/invoicing/regie/%s/credit/%s/cancel/' % (regie.pk, credit.pk), status=403)

    resp = app.get('/manage/invoicing/regie/%s/dockets/' % regie.pk)
    assert '?&ods' not in resp
    assert '?&pdf' not in resp
    assert '/manage/invoicing/regie/%s/dockets/payments/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk) in resp
    app.get('/manage/invoicing/regie/%s/dockets/?&ods' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/dockets/?&pdf' % regie.pk, status=403)

    resp = app.get('/manage/invoicing/regie/%s/dockets/payments/' % regie.pk)
    assert '/manage/invoicing/regie/%s/docket/add/' % regie.pk not in resp
    app.get('/manage/invoicing/regie/%s/docket/add/' % regie.pk, status=403)

    docket.draft = True
    docket.save()
    resp = app.get('/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk))
    assert '/manage/invoicing/regie/%s/docket/%s/export/ods/' % (regie.pk, docket.pk) not in resp
    assert '/manage/invoicing/regie/%s/docket/%s/export/pdf/' % (regie.pk, docket.pk) not in resp
    assert '/manage/invoicing/regie/%s/docket/%s/edit/' % (regie.pk, docket.pk) not in resp
    assert (
        '/manage/invoicing/regie/%s/docket/%s/payment-type/%s/' % (regie.pk, docket.pk, payment_type.pk)
        not in resp
    )
    assert '/manage/invoicing/regie/%s/docket/%s/validate/' % (regie.pk, docket.pk) not in resp
    assert '/manage/invoicing/regie/%s/docket/%s/delete/' % (regie.pk, docket.pk) not in resp
    app.get('/manage/invoicing/regie/%s/docket/%s/export/ods/' % (regie.pk, docket.pk), status=403)
    app.get('/manage/invoicing/regie/%s/docket/%s/export/pdf/' % (regie.pk, docket.pk), status=403)
    app.get('/manage/invoicing/regie/%s/docket/%s/edit/' % (regie.pk, docket.pk), status=403)
    app.get(
        '/manage/invoicing/regie/%s/docket/%s/payment-type/%s/' % (regie.pk, docket.pk, payment_type.pk),
        status=403,
    )
    app.get('/manage/invoicing/regie/%s/docket/%s/validate/' % (regie.pk, docket.pk), status=403)
    app.get('/manage/invoicing/regie/%s/docket/%s/delete/' % (regie.pk, docket.pk), status=403)

    resp = app.get('/manage/invoicing/regie/%s/payers/' % regie.pk)
    assert '/manage/invoicing/regie/%s/payer/payer/transactions/' % regie.pk in resp
    resp = app.get('/manage/invoicing/regie/%s/payer/payer/transactions/' % regie.pk)
    assert '?&ods' not in resp
    app.get('/manage/invoicing/regie/%s/payer/payer/transactions/?ods' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/transactions/for-event/' % regie.pk, status=404)

    resp = app.get('/manage/invoicing/regie/%s/parameters/' % regie.pk)
    assert '/manage/invoicing/regie/%s/edit/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/edit/permissions/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/edit/payer/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/edit/payer-mapping/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/edit/counters/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/edit/publishing/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/export/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/payment-type/add/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/payment-type/%s/edit/' % (regie.pk, payment_type.pk) not in resp
    assert '/manage/invoicing/regie/%s/payment-type/%s/delete/' % (regie.pk, payment_type.pk) not in resp
    assert '/manage/invoicing/regie/%s/inspect/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/history/' % regie.pk not in resp
    app.get('/manage/invoicing/regie/%s/edit/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/edit/permissions/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/edit/payer/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/edit/payer-mapping/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/edit/counters/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/edit/publishing/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/export/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/payment-type/add/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/payment-type/%s/edit/' % (regie.pk, payment_type.pk), status=403)
    app.get('/manage/invoicing/regie/%s/payment-type/%s/delete/' % (regie.pk, payment_type.pk), status=403)
    app.get('/manage/invoicing/regie/%s/inspect/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/history/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/history/compare/' % regie.pk, status=403)

    DraftJournalLine.objects.all().delete()
    DraftInvoice.objects.all().delete()
    Pool.objects.all().delete()
    Campaign.objects.all().delete()
    resp = app.get('/manage/invoicing/regie/%s/parameters/' % regie.pk)
    assert '/manage/invoicing/regie/%s/delete/' % regie.pk not in resp
    app.get('/manage/invoicing/regie/%s/delete/' % regie.pk, status=403)

    app.get('/manage/invoicing/regie/add/', status=403)
    app.get('/manage/invoicing/import/', status=403)
    app.get('/manage/invoicing/export/', status=403)

    app.get('/manage/invoicing/appearance/', status=403)

    app.get('/manage/invoicing/cancellation-reasons/', status=403)
    app.get('/manage/invoicing/cancellation-reason/invoice/add/', status=403)
    app.get('/manage/invoicing/cancellation-reason/invoice/0/edit/', status=403)
    app.get('/manage/invoicing/cancellation-reason/invoice/0/delete/', status=403)
    app.get('/manage/invoicing/cancellation-reason/credit/add/', status=403)
    app.get('/manage/invoicing/cancellation-reason/credit/0/edit/', status=403)
    app.get('/manage/invoicing/cancellation-reason/credit/0/delete/', status=403)
    app.get('/manage/invoicing/cancellation-reason/payment/add/', status=403)
    app.get('/manage/invoicing/cancellation-reason/payment/0/edit/', status=403)
    app.get('/manage/invoicing/cancellation-reason/payment/0/delete/', status=403)


@mock.patch('requests.Session.send', side_effect=mocked_requests_send)
def test_manager_as_editer(mock_send, settings, app, manager_user):
    settings.SHOW_NON_INVOICED_LINES = True
    settings.CAMPAIGN_SHOW_FIX_ERROR = True
    regie = Regie.objects.create(
        label='Foo',
        edit_role=manager_user.groups.first(),
        with_campaigns=True,
        payer_carddef_reference='default:card_model_1',
    )
    payment_type = PaymentType.objects.create(label='Foo', regie=regie)
    payment_type2 = PaymentType.objects.create(label='Foo', regie=regie)
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
    draft_invoice = DraftInvoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
    )
    line = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=1,
        status='error',
        pool=pool,
    )
    invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer',
    )
    invoice_line = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice,
        quantity=1,
        unit_amount=1,
    )
    docket = PaymentDocket.objects.create(
        regie=regie,
        date_end=datetime.date(2022, 10, 1),
        draft=False,
    )
    docket.payment_types.add(payment_type2)
    payment = Payment.objects.create(
        regie=regie,
        amount=1,
        payment_type=payment_type2,
        docket=docket,
    )
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=invoice_line,
        amount=1,
    )
    credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit,
        quantity=1,
        unit_amount=1,
    )
    collection = CollectionDocket.objects.create(
        regie=regie,
        date_end=datetime.date(2022, 10, 1),
        draft=False,
    )
    regie2 = Regie.objects.create(
        label='Foo',
    )

    app = login(app, username='manager', password='manager')

    resp = app.get('/manage/')
    app.get('/manage/inspect/', status=403)
    app.post('/manage/inspect/test-template/', status=403)
    assert '/manage/invoicing/regies/' in resp
    resp = app.get('/manage/invoicing/regies/')
    assert list(resp.context['object_list']) == [regie]
    assert '/manage/invoicing/regie/add/' not in resp
    assert '/manage/invoicing/import/' not in resp
    assert '/manage/invoicing/export/' not in resp
    assert '/manage/invoicing/regie/%s/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/' % regie2.pk not in resp
    assert '/manage/invoicing/appearance/' not in resp
    assert '/manage/invoicing/payers/' not in resp
    assert '/manage/invoicing/cancellation-reasons/' not in resp

    resp = app.get('/manage/invoicing/regie/%s/' % regie.pk)
    assert '/manage/invoicing/regie/%s/parameters/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/campaigns/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/invoices/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/collections/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/payments/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/dockets/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/credits/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/refunds/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/payers/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/non-invoiced-lines/' % regie.pk in resp
    app.get('/manage/invoicing/regie/%s/refunds/' % regie.pk)
    app.get('/manage/invoicing/regie/%s/non-invoiced-lines/' % regie.pk)

    resp = app.get('/manage/invoicing/regie/%s/campaigns/' % regie.pk)
    assert '/manage/invoicing/regie/%s/campaign/add/' % regie.pk not in resp
    app.get('/manage/invoicing/regie/%s/campaign/add/' % regie.pk, status=403)

    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/invoices/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk) in resp
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/invoices/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk), status=403)

    pool.draft = False
    pool.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert '/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk) not in resp
    app.get('/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk), status=403)

    pool.draft = True
    pool.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk) in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/invoice/%s/pdf/'
        % (regie.pk, campaign.pk, pool.pk, draft_invoice.pk)
        in resp
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/invoice/%s/lines/'
        % (regie.pk, campaign.pk, pool.pk, draft_invoice.pk)
        in resp
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/invoice/%s/pdf/'
        % (regie.pk, campaign.pk, pool.pk, draft_invoice.pk)
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/invoice/%s/lines/'
        % (regie.pk, campaign.pk, pool.pk, draft_invoice.pk)
    )

    pool.status = 'running'
    pool.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, pool.pk) not in resp
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, pool.pk),
        status=403,
    )

    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/ignore/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        not in resp
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/ignore/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
        status=403,
    )

    campaign.finalized = True
    campaign.save()
    agenda = Agenda.objects.create(label='Foo', regie=regie)
    campaign.agendas.add(agenda)
    AgendaUnlockLog.objects.create(campaign=campaign, agenda=agenda)
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/add-corrective-campaign/' % (regie.pk, campaign.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agendas/add/' % (regie.pk, campaign.pk) not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, campaign.pk, agenda.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/report/event-amounts/' % (regie.pk, campaign.pk) not in resp
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/add-corrective-campaign/' % (regie.pk, campaign.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agendas/add/' % (regie.pk, campaign.pk), status=403
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, campaign.pk, agenda.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/report/event-amounts/' % (regie.pk, campaign.pk), status=403
    )

    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/job/%s/' % (regie.pk, campaign.pk, uuid.uuid4()), status=403
    )

    resp = app.get('/manage/invoicing/regie/%s/invoices/' % regie.pk)
    assert '/manage/invoicing/regie/%s/invoice/%s/pdf/' % (regie.pk, invoice.pk) in resp
    assert '/manage/invoicing/regie/%s/invoice/%s/dynamic/pdf/' % (regie.pk, invoice.pk) in resp
    assert '/manage/invoicing/ajax/regie/%s/invoice/%s/lines/' % (regie.pk, invoice.pk) in resp
    assert '?&ods' not in resp
    app.get('/manage/invoicing/regie/%s/invoice/%s/pdf/' % (regie.pk, invoice.pk))
    app.get('/manage/invoicing/regie/%s/invoice/%s/dynamic/pdf/' % (regie.pk, invoice.pk))
    app.get('/manage/invoicing/regie/%s/invoices/?&ods' % regie.pk, status=403)
    resp = app.get('/manage/invoicing/ajax/regie/%s/invoice/%s/lines/' % (regie.pk, invoice.pk))
    assert '/manage/invoicing/regie/%s/invoice/%s/payments/pdf/' % (regie.pk, invoice.pk) in resp
    app.get('/manage/invoicing/regie/%s/invoice/%s/payments/pdf/' % (regie.pk, invoice.pk))
    InvoiceLinePayment.objects.all().delete()
    resp = app.get('/manage/invoicing/ajax/regie/%s/invoice/%s/lines/' % (regie.pk, invoice.pk))
    assert '/manage/invoicing/regie/%s/invoice/%s/cancel/' % (regie.pk, invoice.pk) not in resp
    app.get('/manage/invoicing/regie/%s/invoice/%s/cancel/' % (regie.pk, invoice.pk), status=403)
    assert '/manage/invoicing/regie/%s/invoice/%s/edit/dates/' % (regie.pk, invoice.pk) not in resp
    app.get('/manage/invoicing/regie/%s/invoice/%s/edit/dates/' % (regie.pk, invoice.pk), status=403)

    resp = app.get('/manage/invoicing/regie/%s/collections/' % regie.pk)
    assert '/manage/invoicing/regie/%s/collections/invoices/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/collection/%s/' % (regie.pk, collection.pk) in resp

    resp = app.get('/manage/invoicing/regie/%s/collections/invoices/' % regie.pk)
    assert '/manage/invoicing/regie/%s/collection/add/' % regie.pk not in resp
    app.get('/manage/invoicing/regie/%s/collection/add/' % regie.pk, status=403)

    collection.draft = True
    collection.save()
    resp = app.get('/manage/invoicing/regie/%s/collection/%s/' % (regie.pk, collection.pk))
    assert '/manage/invoicing/regie/%s/collection/%s/edit/' % (regie.pk, collection.pk) not in resp
    assert '/manage/invoicing/regie/%s/collection/%s/validate/' % (regie.pk, collection.pk) not in resp
    assert '/manage/invoicing/regie/%s/collection/%s/delete/' % (regie.pk, collection.pk) not in resp
    app.get('/manage/invoicing/regie/%s/collection/%s/edit/' % (regie.pk, collection.pk), status=403)
    app.get('/manage/invoicing/regie/%s/collection/%s/validate/' % (regie.pk, collection.pk), status=403)
    app.get('/manage/invoicing/regie/%s/collection/%s/delete/' % (regie.pk, collection.pk), status=403)

    resp = app.get('/manage/invoicing/regie/%s/payments/' % regie.pk)
    assert '/manage/invoicing/regie/%s/payment/%s/pdf/' % (regie.pk, payment.pk) in resp
    assert '/manage/invoicing/regie/%s/payment/%s/cancel/' % (regie.pk, payment.pk) not in resp
    assert '?&ods' not in resp
    app.get('/manage/invoicing/regie/%s/payment/%s/pdf/' % (regie.pk, payment.pk))
    app.get('/manage/invoicing/regie/%s/payment/%s/cancel/' % (regie.pk, payment.pk), status=403)
    app.get('/manage/invoicing/regie/%s/payments/?&ods' % regie.pk, status=403)

    resp = app.get('/manage/invoicing/regie/%s/credits/' % regie.pk)
    assert '/manage/invoicing/regie/%s/credit/%s/pdf/' % (regie.pk, credit.pk) in resp
    assert '/manage/invoicing/ajax/regie/%s/credit/%s/lines/' % (regie.pk, credit.pk) in resp
    app.get('/manage/invoicing/regie/%s/credit/%s/pdf/' % (regie.pk, credit.pk))
    resp = app.get('/manage/invoicing/ajax/regie/%s/credit/%s/lines/' % (regie.pk, credit.pk))
    assert '/manage/invoicing/regie/%s/credit/%s/cancel/' % (regie.pk, credit.pk) not in resp
    app.get('/manage/invoicing/regie/%s/credit/%s/cancel/' % (regie.pk, credit.pk), status=403)

    resp = app.get('/manage/invoicing/regie/%s/dockets/' % regie.pk)
    assert '?&ods' not in resp
    assert '?&pdf' not in resp
    assert '/manage/invoicing/regie/%s/dockets/payments/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk) in resp
    app.get('/manage/invoicing/regie/%s/dockets/?&ods' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/dockets/?&pdf' % regie.pk, status=403)

    resp = app.get('/manage/invoicing/regie/%s/dockets/payments/' % regie.pk)
    assert '/manage/invoicing/regie/%s/docket/add/' % regie.pk not in resp
    app.get('/manage/invoicing/regie/%s/docket/add/' % regie.pk, status=403)

    docket.draft = True
    docket.save()
    resp = app.get('/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk))
    assert '/manage/invoicing/regie/%s/docket/%s/export/ods/' % (regie.pk, docket.pk) not in resp
    assert '/manage/invoicing/regie/%s/docket/%s/export/pdf/' % (regie.pk, docket.pk) not in resp
    assert '/manage/invoicing/regie/%s/docket/%s/edit/' % (regie.pk, docket.pk) not in resp
    assert (
        '/manage/invoicing/regie/%s/docket/%s/payment-type/%s/' % (regie.pk, docket.pk, payment_type.pk)
        not in resp
    )
    assert '/manage/invoicing/regie/%s/docket/%s/validate/' % (regie.pk, docket.pk) not in resp
    assert '/manage/invoicing/regie/%s/docket/%s/delete/' % (regie.pk, docket.pk) not in resp
    app.get('/manage/invoicing/regie/%s/docket/%s/export/ods/' % (regie.pk, docket.pk), status=403)
    app.get('/manage/invoicing/regie/%s/docket/%s/export/pdf/' % (regie.pk, docket.pk), status=403)
    app.get('/manage/invoicing/regie/%s/docket/%s/edit/' % (regie.pk, docket.pk), status=403)
    app.get(
        '/manage/invoicing/regie/%s/docket/%s/payment-type/%s/' % (regie.pk, docket.pk, payment_type.pk),
        status=403,
    )
    app.get('/manage/invoicing/regie/%s/docket/%s/validate/' % (regie.pk, docket.pk), status=403)
    app.get('/manage/invoicing/regie/%s/docket/%s/delete/' % (regie.pk, docket.pk), status=403)

    resp = app.get('/manage/invoicing/regie/%s/payers/' % regie.pk)
    assert '/manage/invoicing/regie/%s/payer/payer/transactions/' % regie.pk in resp
    resp = app.get('/manage/invoicing/regie/%s/payer/payer/transactions/' % regie.pk)
    assert '?&ods' not in resp
    app.get('/manage/invoicing/regie/%s/payer/payer/transactions/?ods' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/transactions/for-event/' % regie.pk, status=404)

    resp = app.get('/manage/invoicing/regie/%s/parameters/' % regie.pk)
    assert '/manage/invoicing/regie/%s/edit/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/edit/permissions/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/edit/payer/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/edit/payer-mapping/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/edit/counters/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/edit/publishing/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/export/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/payment-type/add/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/payment-type/%s/edit/' % (regie.pk, payment_type.pk) in resp
    assert '/manage/invoicing/regie/%s/payment-type/%s/delete/' % (regie.pk, payment_type.pk) in resp
    assert '/manage/invoicing/regie/%s/inspect/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/history/' % regie.pk in resp
    app.get('/manage/invoicing/regie/%s/edit/' % regie.pk)
    app.get('/manage/invoicing/regie/%s/edit/permissions/' % regie.pk)
    app.get('/manage/invoicing/regie/%s/edit/payer/' % regie.pk)
    app.get('/manage/invoicing/regie/%s/edit/payer-mapping/' % regie.pk)
    app.get('/manage/invoicing/regie/%s/edit/counters/' % regie.pk)
    app.get('/manage/invoicing/regie/%s/edit/publishing/' % regie.pk)
    app.get('/manage/invoicing/regie/%s/export/' % regie.pk)
    app.get('/manage/invoicing/regie/%s/payment-type/add/' % regie.pk)
    app.get('/manage/invoicing/regie/%s/payment-type/%s/edit/' % (regie.pk, payment_type.pk))
    app.get('/manage/invoicing/regie/%s/payment-type/%s/delete/' % (regie.pk, payment_type.pk))
    app.get('/manage/invoicing/regie/%s/inspect/' % regie.pk)
    app.get('/manage/invoicing/regie/%s/history/' % regie.pk)
    app.get('/manage/invoicing/regie/%s/history/compare/' % regie.pk, status=404)

    DraftJournalLine.objects.all().delete()
    DraftInvoice.objects.all().delete()
    Pool.objects.all().delete()
    Campaign.objects.all().delete()
    resp = app.get('/manage/invoicing/regie/%s/parameters/' % regie.pk)
    assert '/manage/invoicing/regie/%s/delete/' % regie.pk in resp
    app.get('/manage/invoicing/regie/%s/delete/' % regie.pk)

    app.get('/manage/invoicing/regie/add/', status=403)
    app.get('/manage/invoicing/import/', status=403)
    app.get('/manage/invoicing/export/', status=403)

    app.get('/manage/invoicing/appearance/', status=403)

    app.get('/manage/invoicing/cancellation-reasons/', status=403)
    app.get('/manage/invoicing/cancellation-reason/invoice/add/', status=403)
    app.get('/manage/invoicing/cancellation-reason/invoice/0/edit/', status=403)
    app.get('/manage/invoicing/cancellation-reason/invoice/0/delete/', status=403)
    app.get('/manage/invoicing/cancellation-reason/credit/add/', status=403)
    app.get('/manage/invoicing/cancellation-reason/credit/0/edit/', status=403)
    app.get('/manage/invoicing/cancellation-reason/credit/0/delete/', status=403)
    app.get('/manage/invoicing/cancellation-reason/payment/add/', status=403)
    app.get('/manage/invoicing/cancellation-reason/payment/0/edit/', status=403)
    app.get('/manage/invoicing/cancellation-reason/payment/0/delete/', status=403)


@mock.patch('requests.Session.send', side_effect=mocked_requests_send)
def test_manager_as_invoicer(mock_send, settings, app, manager_user):
    settings.SHOW_NON_INVOICED_LINES = True
    settings.CAMPAIGN_SHOW_FIX_ERROR = True
    regie = Regie.objects.create(
        label='Foo',
        invoice_role=manager_user.groups.first(),
        with_campaigns=True,
        payer_carddef_reference='default:card_model_1',
    )
    payment_type = PaymentType.objects.create(label='Foo', regie=regie)
    payment_type2 = PaymentType.objects.create(label='Foo', regie=regie)
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
    draft_invoice = DraftInvoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
    )
    line = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=1,
        status='error',
        pool=pool,
    )
    invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer',
    )
    invoice_line = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice,
        quantity=1,
        unit_amount=1,
    )
    docket = PaymentDocket.objects.create(
        regie=regie,
        date_end=datetime.date(2022, 10, 1),
        draft=False,
    )
    docket.payment_types.add(payment_type2)
    payment = Payment.objects.create(
        regie=regie,
        amount=1,
        payment_type=payment_type2,
        docket=docket,
    )
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=invoice_line,
        amount=1,
    )
    credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit,
        quantity=1,
        unit_amount=1,
    )
    collection = CollectionDocket.objects.create(
        regie=regie,
        date_end=datetime.date(2022, 10, 1),
        draft=False,
    )
    regie2 = Regie.objects.create(
        label='Foo',
    )

    app = login(app, username='manager', password='manager')

    resp = app.get('/manage/')
    app.get('/manage/inspect/', status=403)
    app.post('/manage/inspect/test-template/', status=403)
    assert '/manage/invoicing/regies/' in resp
    resp = app.get('/manage/invoicing/regies/')
    assert list(resp.context['object_list']) == [regie]
    assert '/manage/invoicing/regie/add/' not in resp
    assert '/manage/invoicing/import/' not in resp
    assert '/manage/invoicing/export/' not in resp
    assert '/manage/invoicing/regie/%s/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/' % regie2.pk not in resp
    assert '/manage/invoicing/appearance/' not in resp
    assert '/manage/invoicing/payers/' not in resp
    assert '/manage/invoicing/cancellation-reasons/' not in resp

    resp = app.get('/manage/invoicing/regie/%s/' % regie.pk)
    assert '/manage/invoicing/regie/%s/parameters/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/campaigns/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/invoices/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/collections/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/payments/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/dockets/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/credits/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/refunds/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/payers/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/non-invoiced-lines/' % regie.pk in resp
    app.get('/manage/invoicing/regie/%s/refunds/' % regie.pk)
    app.get('/manage/invoicing/regie/%s/non-invoiced-lines/' % regie.pk)

    resp = app.get('/manage/invoicing/regie/%s/campaigns/' % regie.pk)
    assert '/manage/invoicing/regie/%s/campaign/add/' % regie.pk in resp
    app.get('/manage/invoicing/regie/%s/campaign/add/' % regie.pk)

    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/' % (regie.pk, campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (regie.pk, campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/invoices/' % (regie.pk, campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk) in resp
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/' % (regie.pk, campaign.pk))
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (regie.pk, campaign.pk))
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/invoices/' % (regie.pk, campaign.pk))
    app.get('/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, campaign.pk))
    app.get('/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk))
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk))

    pool.draft = False
    pool.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert '/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk) in resp
    app.get('/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk))

    pool.draft = True
    pool.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk) in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk) in resp
    )
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk) in resp
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/invoice/%s/pdf/'
        % (regie.pk, campaign.pk, pool.pk, draft_invoice.pk)
        in resp
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/invoice/%s/lines/'
        % (regie.pk, campaign.pk, pool.pk, draft_invoice.pk)
        in resp
    )
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk))
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk))
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/invoice/%s/pdf/'
        % (regie.pk, campaign.pk, pool.pk, draft_invoice.pk)
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/invoice/%s/lines/'
        % (regie.pk, campaign.pk, pool.pk, draft_invoice.pk)
    )

    pool.status = 'running'
    pool.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, pool.pk) in resp
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, pool.pk),
    )

    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/ignore/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        in resp
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        in resp
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/ignore/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
        status=404,  # Agenda.DoesNotExist
    )

    campaign.finalized = True
    campaign.save()
    agenda = Agenda.objects.create(label='Foo', regie=regie)
    campaign.agendas.add(agenda)
    AgendaUnlockLog.objects.create(campaign=campaign, agenda=agenda)
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert '/manage/invoicing/regie/%s/campaign/%s/add-corrective-campaign/' % (regie.pk, campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/corrective/agendas/add/' % (regie.pk, campaign.pk) in resp
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, campaign.pk, agenda.pk)
        in resp
    )
    assert '/manage/invoicing/regie/%s/campaign/%s/report/event-amounts/' % (regie.pk, campaign.pk) in resp
    app.get('/manage/invoicing/regie/%s/campaign/%s/add-corrective-campaign/' % (regie.pk, campaign.pk))
    app.get('/manage/invoicing/regie/%s/campaign/%s/corrective/agendas/add/' % (regie.pk, campaign.pk))
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, campaign.pk, agenda.pk)
    )
    app.get('/manage/invoicing/regie/%s/campaign/%s/report/event-amounts/' % (regie.pk, campaign.pk))

    job = CampaignAsyncJob.objects.create(campaign=campaign)
    app.get('/manage/invoicing/regie/%s/campaign/%s/job/%s/' % (regie.pk, campaign.pk, job.uuid))

    resp = app.get('/manage/invoicing/regie/%s/invoices/' % regie.pk)
    assert '/manage/invoicing/regie/%s/invoice/%s/pdf/' % (regie.pk, invoice.pk) in resp
    assert '/manage/invoicing/regie/%s/invoice/%s/dynamic/pdf/' % (regie.pk, invoice.pk) in resp
    assert '/manage/invoicing/ajax/regie/%s/invoice/%s/lines/' % (regie.pk, invoice.pk) in resp
    assert '?&ods' in resp
    app.get('/manage/invoicing/regie/%s/invoice/%s/pdf/' % (regie.pk, invoice.pk))
    app.get('/manage/invoicing/regie/%s/invoice/%s/dynamic/pdf/' % (regie.pk, invoice.pk))
    app.get('/manage/invoicing/regie/%s/invoices/?&ods' % regie.pk)
    resp = app.get('/manage/invoicing/ajax/regie/%s/invoice/%s/lines/' % (regie.pk, invoice.pk))
    assert '/manage/invoicing/regie/%s/invoice/%s/payments/pdf/' % (regie.pk, invoice.pk) in resp
    app.get('/manage/invoicing/regie/%s/invoice/%s/payments/pdf/' % (regie.pk, invoice.pk))
    InvoiceLinePayment.objects.all().delete()
    resp = app.get('/manage/invoicing/ajax/regie/%s/invoice/%s/lines/' % (regie.pk, invoice.pk))
    assert '/manage/invoicing/regie/%s/invoice/%s/cancel/' % (regie.pk, invoice.pk) in resp
    app.get('/manage/invoicing/regie/%s/invoice/%s/cancel/' % (regie.pk, invoice.pk))
    assert '/manage/invoicing/regie/%s/invoice/%s/edit/dates/' % (regie.pk, invoice.pk) in resp
    app.get('/manage/invoicing/regie/%s/invoice/%s/edit/dates/' % (regie.pk, invoice.pk))

    resp = app.get('/manage/invoicing/regie/%s/collections/' % regie.pk)
    assert '/manage/invoicing/regie/%s/collections/invoices/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/collection/%s/' % (regie.pk, collection.pk) in resp

    resp = app.get('/manage/invoicing/regie/%s/collections/invoices/' % regie.pk)
    assert '/manage/invoicing/regie/%s/collection/add/' % regie.pk in resp
    app.get('/manage/invoicing/regie/%s/collection/add/' % regie.pk)

    collection.draft = True
    collection.save()
    resp = app.get('/manage/invoicing/regie/%s/collection/%s/' % (regie.pk, collection.pk))
    assert '/manage/invoicing/regie/%s/collection/%s/edit/' % (regie.pk, collection.pk) in resp
    assert '/manage/invoicing/regie/%s/collection/%s/validate/' % (regie.pk, collection.pk) in resp
    assert '/manage/invoicing/regie/%s/collection/%s/delete/' % (regie.pk, collection.pk) in resp
    app.get('/manage/invoicing/regie/%s/collection/%s/edit/' % (regie.pk, collection.pk))
    app.get('/manage/invoicing/regie/%s/collection/%s/validate/' % (regie.pk, collection.pk))
    app.get('/manage/invoicing/regie/%s/collection/%s/delete/' % (regie.pk, collection.pk))

    resp = app.get('/manage/invoicing/regie/%s/payments/' % regie.pk)
    assert '/manage/invoicing/regie/%s/payment/%s/pdf/' % (regie.pk, payment.pk) in resp
    assert '/manage/invoicing/regie/%s/payment/%s/cancel/' % (regie.pk, payment.pk) not in resp
    assert '?&ods' not in resp
    app.get('/manage/invoicing/regie/%s/payment/%s/pdf/' % (regie.pk, payment.pk))
    app.get('/manage/invoicing/regie/%s/payment/%s/cancel/' % (regie.pk, payment.pk), status=403)
    app.get('/manage/invoicing/regie/%s/payments/?&ods' % regie.pk, status=403)

    resp = app.get('/manage/invoicing/regie/%s/credits/' % regie.pk)
    assert '/manage/invoicing/regie/%s/credit/%s/pdf/' % (regie.pk, credit.pk) in resp
    assert '/manage/invoicing/ajax/regie/%s/credit/%s/lines/' % (regie.pk, credit.pk) in resp
    app.get('/manage/invoicing/regie/%s/credit/%s/pdf/' % (regie.pk, credit.pk))
    resp = app.get('/manage/invoicing/ajax/regie/%s/credit/%s/lines/' % (regie.pk, credit.pk))
    assert '/manage/invoicing/regie/%s/credit/%s/cancel/' % (regie.pk, credit.pk) in resp
    app.get('/manage/invoicing/regie/%s/credit/%s/cancel/' % (regie.pk, credit.pk))

    resp = app.get('/manage/invoicing/regie/%s/dockets/' % regie.pk)
    assert '?&ods' not in resp
    assert '?&pdf' not in resp
    assert '/manage/invoicing/regie/%s/dockets/payments/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk) in resp
    app.get('/manage/invoicing/regie/%s/dockets/?&ods' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/dockets/?&pdf' % regie.pk, status=403)

    resp = app.get('/manage/invoicing/regie/%s/dockets/payments/' % regie.pk)
    assert '/manage/invoicing/regie/%s/docket/add/' % regie.pk not in resp
    app.get('/manage/invoicing/regie/%s/docket/add/' % regie.pk, status=403)

    docket.draft = True
    docket.save()
    resp = app.get('/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk))
    assert '/manage/invoicing/regie/%s/docket/%s/export/ods/' % (regie.pk, docket.pk) not in resp
    assert '/manage/invoicing/regie/%s/docket/%s/export/pdf/' % (regie.pk, docket.pk) not in resp
    assert '/manage/invoicing/regie/%s/docket/%s/edit/' % (regie.pk, docket.pk) not in resp
    assert (
        '/manage/invoicing/regie/%s/docket/%s/payment-type/%s/' % (regie.pk, docket.pk, payment_type.pk)
        not in resp
    )
    assert '/manage/invoicing/regie/%s/docket/%s/validate/' % (regie.pk, docket.pk) not in resp
    assert '/manage/invoicing/regie/%s/docket/%s/delete/' % (regie.pk, docket.pk) not in resp
    app.get('/manage/invoicing/regie/%s/docket/%s/export/ods/' % (regie.pk, docket.pk), status=403)
    app.get('/manage/invoicing/regie/%s/docket/%s/export/pdf/' % (regie.pk, docket.pk), status=403)
    app.get('/manage/invoicing/regie/%s/docket/%s/edit/' % (regie.pk, docket.pk), status=403)
    app.get(
        '/manage/invoicing/regie/%s/docket/%s/payment-type/%s/' % (regie.pk, docket.pk, payment_type.pk),
        status=403,
    )
    app.get('/manage/invoicing/regie/%s/docket/%s/validate/' % (regie.pk, docket.pk), status=403)
    app.get('/manage/invoicing/regie/%s/docket/%s/delete/' % (regie.pk, docket.pk), status=403)

    resp = app.get('/manage/invoicing/regie/%s/payers/' % regie.pk)
    assert '/manage/invoicing/regie/%s/payer/payer/transactions/' % regie.pk in resp
    resp = app.get('/manage/invoicing/regie/%s/payer/payer/transactions/' % regie.pk)
    assert '?&ods' in resp
    app.get('/manage/invoicing/regie/%s/payer/payer/transactions/?ods' % regie.pk)
    app.get('/manage/invoicing/regie/%s/transactions/for-event/' % regie.pk, status=404)

    resp = app.get('/manage/invoicing/regie/%s/parameters/' % regie.pk)
    assert '/manage/invoicing/regie/%s/edit/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/edit/permissions/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/edit/payer/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/edit/payer-mapping/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/edit/counters/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/edit/publishing/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/export/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/payment-type/add/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/payment-type/%s/edit/' % (regie.pk, payment_type.pk) not in resp
    assert '/manage/invoicing/regie/%s/payment-type/%s/delete/' % (regie.pk, payment_type.pk) not in resp
    assert '/manage/invoicing/regie/%s/inspect/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/history/' % regie.pk not in resp
    app.get('/manage/invoicing/regie/%s/edit/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/edit/permissions/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/edit/payer/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/edit/payer-mapping/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/edit/counters/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/edit/publishing/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/export/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/payment-type/add/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/payment-type/%s/edit/' % (regie.pk, payment_type.pk), status=403)
    app.get('/manage/invoicing/regie/%s/payment-type/%s/delete/' % (regie.pk, payment_type.pk), status=403)
    app.get('/manage/invoicing/regie/%s/history/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/inspect/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/history/compare/' % regie.pk, status=403)

    DraftJournalLine.objects.all().delete()
    DraftInvoice.objects.all().delete()
    Pool.objects.all().delete()
    Campaign.objects.all().delete()
    resp = app.get('/manage/invoicing/regie/%s/parameters/' % regie.pk)
    assert '/manage/invoicing/regie/%s/delete/' % regie.pk not in resp
    app.get('/manage/invoicing/regie/%s/delete/' % regie.pk, status=403)

    app.get('/manage/invoicing/regie/add/', status=403)
    app.get('/manage/invoicing/import/', status=403)
    app.get('/manage/invoicing/export/', status=403)

    app.get('/manage/invoicing/appearance/', status=403)

    app.get('/manage/invoicing/cancellation-reasons/', status=403)
    app.get('/manage/invoicing/cancellation-reason/invoice/add/', status=403)
    app.get('/manage/invoicing/cancellation-reason/invoice/0/edit/', status=403)
    app.get('/manage/invoicing/cancellation-reason/invoice/0/delete/', status=403)
    app.get('/manage/invoicing/cancellation-reason/credit/add/', status=403)
    app.get('/manage/invoicing/cancellation-reason/credit/0/edit/', status=403)
    app.get('/manage/invoicing/cancellation-reason/credit/0/delete/', status=403)
    app.get('/manage/invoicing/cancellation-reason/payment/add/', status=403)
    app.get('/manage/invoicing/cancellation-reason/payment/0/edit/', status=403)
    app.get('/manage/invoicing/cancellation-reason/payment/0/delete/', status=403)


@mock.patch('requests.Session.send', side_effect=mocked_requests_send)
def test_manager_as_controller(mock_send, settings, app, manager_user):
    settings.SHOW_NON_INVOICED_LINES = True
    settings.CAMPAIGN_SHOW_FIX_ERROR = True
    regie = Regie.objects.create(
        label='Foo',
        control_role=manager_user.groups.first(),
        with_campaigns=True,
        payer_carddef_reference='default:card_model_1',
    )
    payment_type = PaymentType.objects.create(label='Foo', regie=regie)
    payment_type2 = PaymentType.objects.create(label='Foo', regie=regie)
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
    draft_invoice = DraftInvoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
    )
    line = DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=1,
        status='error',
        pool=pool,
    )
    invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer',
    )
    invoice_line = InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice,
        quantity=1,
        unit_amount=1,
    )
    docket = PaymentDocket.objects.create(
        regie=regie,
        date_end=datetime.date(2022, 10, 1),
        draft=False,
    )
    docket.payment_types.add(payment_type2)
    payment = Payment.objects.create(
        regie=regie,
        amount=1,
        payment_type=payment_type2,
        docket=docket,
    )
    InvoiceLinePayment.objects.create(
        payment=payment,
        line=invoice_line,
        amount=1,
    )
    credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit,
        quantity=1,
        unit_amount=1,
    )
    collection = CollectionDocket.objects.create(
        regie=regie,
        date_end=datetime.date(2022, 10, 1),
        draft=False,
    )
    regie2 = Regie.objects.create(
        label='Foo',
    )

    app = login(app, username='manager', password='manager')

    resp = app.get('/manage/')
    app.get('/manage/inspect/', status=403)
    app.post('/manage/inspect/test-template/', status=403)
    assert '/manage/invoicing/regies/' in resp
    resp = app.get('/manage/invoicing/regies/')
    assert list(resp.context['object_list']) == [regie]
    assert '/manage/invoicing/regie/add/' not in resp
    assert '/manage/invoicing/import/' not in resp
    assert '/manage/invoicing/export/' not in resp
    assert '/manage/invoicing/regie/%s/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/' % regie2.pk not in resp
    assert '/manage/invoicing/appearance/' not in resp
    assert '/manage/invoicing/payers/' not in resp
    assert '/manage/invoicing/cancellation-reasons/' not in resp

    resp = app.get('/manage/invoicing/regie/%s/' % regie.pk)
    assert '/manage/invoicing/regie/%s/parameters/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/campaigns/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/invoices/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/collections/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/payments/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/dockets/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/credits/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/refunds/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/payers/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/non-invoiced-lines/' % regie.pk in resp
    app.get('/manage/invoicing/regie/%s/refunds/' % regie.pk)
    app.get('/manage/invoicing/regie/%s/non-invoiced-lines/' % regie.pk)

    resp = app.get('/manage/invoicing/regie/%s/campaigns/' % regie.pk)
    assert '/manage/invoicing/regie/%s/campaign/add/' % regie.pk not in resp
    app.get('/manage/invoicing/regie/%s/campaign/add/' % regie.pk, status=403)

    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/invoices/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk) in resp
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/invoices/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk), status=403)
    app.get('/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk), status=403)

    pool.draft = False
    pool.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert '/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk) not in resp
    app.get('/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk), status=403)

    pool.draft = True
    pool.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk) in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/invoice/%s/pdf/'
        % (regie.pk, campaign.pk, pool.pk, draft_invoice.pk)
        in resp
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/invoice/%s/lines/'
        % (regie.pk, campaign.pk, pool.pk, draft_invoice.pk)
        in resp
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/promote/' % (regie.pk, campaign.pk, pool.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/delete/' % (regie.pk, campaign.pk, pool.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/invoice/%s/pdf/'
        % (regie.pk, campaign.pk, pool.pk, draft_invoice.pk)
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/invoice/%s/lines/'
        % (regie.pk, campaign.pk, pool.pk, draft_invoice.pk)
    )

    pool.status = 'running'
    pool.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, pool.pk) not in resp
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/stop/' % (regie.pk, campaign.pk, pool.pk),
        status=403,
    )

    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/journal/' % (regie.pk, campaign.pk, pool.pk)
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/ignore/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, pool.pk, line.pk)
        not in resp
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/ignore/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/ajax/regie/%s/campaign/%s/pool/%s/line/%s/replay/'
        % (regie.pk, campaign.pk, pool.pk, line.pk),
        status=403,
    )

    campaign.finalized = True
    campaign.save()
    agenda = Agenda.objects.create(label='Foo', regie=regie)
    campaign.agendas.add(agenda)
    AgendaUnlockLog.objects.create(campaign=campaign, agenda=agenda)
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/add-corrective-campaign/' % (regie.pk, campaign.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agendas/add/' % (regie.pk, campaign.pk) not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, campaign.pk, agenda.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/report/event-amounts/' % (regie.pk, campaign.pk) not in resp
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/add-corrective-campaign/' % (regie.pk, campaign.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agendas/add/' % (regie.pk, campaign.pk), status=403
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, campaign.pk, agenda.pk),
        status=403,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/report/event-amounts/' % (regie.pk, campaign.pk), status=403
    )

    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/job/%s/' % (regie.pk, campaign.pk, uuid.uuid4()), status=403
    )

    resp = app.get('/manage/invoicing/regie/%s/invoices/' % regie.pk)
    assert '/manage/invoicing/regie/%s/invoice/%s/pdf/' % (regie.pk, invoice.pk) in resp
    assert '/manage/invoicing/regie/%s/invoice/%s/dynamic/pdf/' % (regie.pk, invoice.pk) in resp
    assert '/manage/invoicing/ajax/regie/%s/invoice/%s/lines/' % (regie.pk, invoice.pk) in resp
    assert '?&ods' not in resp
    app.get('/manage/invoicing/regie/%s/invoice/%s/pdf/' % (regie.pk, invoice.pk))
    app.get('/manage/invoicing/regie/%s/invoice/%s/dynamic/pdf/' % (regie.pk, invoice.pk))
    app.get('/manage/invoicing/regie/%s/invoices/?&ods' % regie.pk, status=403)
    resp = app.get('/manage/invoicing/ajax/regie/%s/invoice/%s/lines/' % (regie.pk, invoice.pk))
    assert '/manage/invoicing/regie/%s/invoice/%s/payments/pdf/' % (regie.pk, invoice.pk) in resp
    app.get('/manage/invoicing/regie/%s/invoice/%s/payments/pdf/' % (regie.pk, invoice.pk))
    InvoiceLinePayment.objects.all().delete()
    resp = app.get('/manage/invoicing/ajax/regie/%s/invoice/%s/lines/' % (regie.pk, invoice.pk))
    assert '/manage/invoicing/regie/%s/invoice/%s/cancel/' % (regie.pk, invoice.pk) not in resp
    app.get('/manage/invoicing/regie/%s/invoice/%s/cancel/' % (regie.pk, invoice.pk), status=403)
    assert '/manage/invoicing/regie/%s/invoice/%s/edit/dates/' % (regie.pk, invoice.pk) not in resp
    app.get('/manage/invoicing/regie/%s/invoice/%s/edit/dates/' % (regie.pk, invoice.pk), status=403)

    resp = app.get('/manage/invoicing/regie/%s/collections/' % regie.pk)
    assert '/manage/invoicing/regie/%s/collections/invoices/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/collection/%s/' % (regie.pk, collection.pk) in resp

    resp = app.get('/manage/invoicing/regie/%s/collections/invoices/' % regie.pk)
    assert '/manage/invoicing/regie/%s/collection/add/' % regie.pk not in resp
    app.get('/manage/invoicing/regie/%s/collection/add/' % regie.pk, status=403)

    collection.draft = True
    collection.save()
    resp = app.get('/manage/invoicing/regie/%s/collection/%s/' % (regie.pk, collection.pk))
    assert '/manage/invoicing/regie/%s/collection/%s/edit/' % (regie.pk, collection.pk) not in resp
    assert '/manage/invoicing/regie/%s/collection/%s/validate/' % (regie.pk, collection.pk) not in resp
    assert '/manage/invoicing/regie/%s/collection/%s/delete/' % (regie.pk, collection.pk) not in resp
    app.get('/manage/invoicing/regie/%s/collection/%s/edit/' % (regie.pk, collection.pk), status=403)
    app.get('/manage/invoicing/regie/%s/collection/%s/validate/' % (regie.pk, collection.pk), status=403)
    app.get('/manage/invoicing/regie/%s/collection/%s/delete/' % (regie.pk, collection.pk), status=403)

    resp = app.get('/manage/invoicing/regie/%s/payments/' % regie.pk)
    assert '/manage/invoicing/regie/%s/payment/%s/pdf/' % (regie.pk, payment.pk) in resp
    assert '/manage/invoicing/regie/%s/payment/%s/cancel/' % (regie.pk, payment.pk) in resp
    assert '?&ods' in resp
    app.get('/manage/invoicing/regie/%s/payment/%s/pdf/' % (regie.pk, payment.pk))
    app.get('/manage/invoicing/regie/%s/payment/%s/cancel/' % (regie.pk, payment.pk))
    app.get('/manage/invoicing/regie/%s/payments/?&ods' % regie.pk)

    resp = app.get('/manage/invoicing/regie/%s/credits/' % regie.pk)
    assert '/manage/invoicing/regie/%s/credit/%s/pdf/' % (regie.pk, credit.pk) in resp
    assert '/manage/invoicing/ajax/regie/%s/credit/%s/lines/' % (regie.pk, credit.pk) in resp
    app.get('/manage/invoicing/regie/%s/credit/%s/pdf/' % (regie.pk, credit.pk))
    resp = app.get('/manage/invoicing/ajax/regie/%s/credit/%s/lines/' % (regie.pk, credit.pk))
    assert '/manage/invoicing/regie/%s/credit/%s/cancel/' % (regie.pk, credit.pk) not in resp
    app.get('/manage/invoicing/regie/%s/credit/%s/cancel/' % (regie.pk, credit.pk), status=403)

    resp = app.get('/manage/invoicing/regie/%s/dockets/' % regie.pk)
    assert '?&ods' in resp
    assert '?&pdf' in resp
    assert '/manage/invoicing/regie/%s/dockets/payments/' % regie.pk in resp
    assert '/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk) in resp
    app.get('/manage/invoicing/regie/%s/dockets/?&ods' % regie.pk)
    app.get('/manage/invoicing/regie/%s/dockets/?&pdf' % regie.pk)

    resp = app.get('/manage/invoicing/regie/%s/dockets/payments/' % regie.pk)
    assert '/manage/invoicing/regie/%s/docket/add/' % regie.pk in resp
    app.get('/manage/invoicing/regie/%s/docket/add/' % regie.pk)

    docket.draft = True
    docket.save()
    resp = app.get('/manage/invoicing/regie/%s/docket/%s/' % (regie.pk, docket.pk))
    assert '/manage/invoicing/regie/%s/docket/%s/export/ods/' % (regie.pk, docket.pk) in resp
    assert '/manage/invoicing/regie/%s/docket/%s/export/pdf/' % (regie.pk, docket.pk) in resp
    assert '/manage/invoicing/regie/%s/docket/%s/edit/' % (regie.pk, docket.pk) in resp
    assert (
        '/manage/invoicing/regie/%s/docket/%s/payment-type/%s/' % (regie.pk, docket.pk, payment_type2.pk)
        in resp
    )
    assert '/manage/invoicing/regie/%s/docket/%s/validate/' % (regie.pk, docket.pk) in resp
    assert '/manage/invoicing/regie/%s/docket/%s/delete/' % (regie.pk, docket.pk) in resp
    app.get('/manage/invoicing/regie/%s/docket/%s/export/ods/' % (regie.pk, docket.pk))
    app.get('/manage/invoicing/regie/%s/docket/%s/export/pdf/' % (regie.pk, docket.pk))
    app.get('/manage/invoicing/regie/%s/docket/%s/edit/' % (regie.pk, docket.pk))
    app.get(
        '/manage/invoicing/regie/%s/docket/%s/payment-type/%s/' % (regie.pk, docket.pk, payment_type2.pk),
    )
    app.get('/manage/invoicing/regie/%s/docket/%s/validate/' % (regie.pk, docket.pk))
    app.get('/manage/invoicing/regie/%s/docket/%s/delete/' % (regie.pk, docket.pk))

    resp = app.get('/manage/invoicing/regie/%s/payers/' % regie.pk)
    assert '/manage/invoicing/regie/%s/payer/payer/transactions/' % regie.pk in resp
    resp = app.get('/manage/invoicing/regie/%s/payer/payer/transactions/' % regie.pk)
    assert '?&ods' not in resp
    app.get('/manage/invoicing/regie/%s/payer/payer/transactions/?ods' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/transactions/for-event/' % regie.pk, status=404)

    resp = app.get('/manage/invoicing/regie/%s/parameters/' % regie.pk)
    assert '/manage/invoicing/regie/%s/edit/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/edit/permissions/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/edit/payer/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/edit/payer-mapping/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/edit/counters/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/edit/publishing/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/export/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/payment-type/add/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/payment-type/%s/edit/' % (regie.pk, payment_type.pk) not in resp
    assert '/manage/invoicing/regie/%s/payment-type/%s/delete/' % (regie.pk, payment_type.pk) not in resp
    assert '/manage/invoicing/regie/%s/history/' % regie.pk not in resp
    assert '/manage/invoicing/regie/%s/inspect/' % regie.pk not in resp
    app.get('/manage/invoicing/regie/%s/edit/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/edit/permissions/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/edit/payer/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/edit/payer-mapping/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/edit/counters/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/edit/publishing/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/export/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/payment-type/add/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/payment-type/%s/edit/' % (regie.pk, payment_type.pk), status=403)
    app.get('/manage/invoicing/regie/%s/payment-type/%s/delete/' % (regie.pk, payment_type.pk), status=403)
    app.get('/manage/invoicing/regie/%s/inspect/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/history/' % regie.pk, status=403)
    app.get('/manage/invoicing/regie/%s/history/compare/' % regie.pk, status=403)

    DraftJournalLine.objects.all().delete()
    DraftInvoice.objects.all().delete()
    Pool.objects.all().delete()
    Campaign.objects.all().delete()
    resp = app.get('/manage/invoicing/regie/%s/parameters/' % regie.pk)
    assert '/manage/invoicing/regie/%s/delete/' % regie.pk not in resp
    app.get('/manage/invoicing/regie/%s/delete/' % regie.pk, status=403)

    app.get('/manage/invoicing/regie/add/', status=403)
    app.get('/manage/invoicing/import/', status=403)
    app.get('/manage/invoicing/export/', status=403)

    app.get('/manage/invoicing/appearance/', status=403)

    app.get('/manage/invoicing/cancellation-reasons/', status=403)
    app.get('/manage/invoicing/cancellation-reason/invoice/add/', status=403)
    app.get('/manage/invoicing/cancellation-reason/invoice/0/edit/', status=403)
    app.get('/manage/invoicing/cancellation-reason/invoice/0/delete/', status=403)
    app.get('/manage/invoicing/cancellation-reason/credit/add/', status=403)
    app.get('/manage/invoicing/cancellation-reason/credit/0/edit/', status=403)
    app.get('/manage/invoicing/cancellation-reason/credit/0/delete/', status=403)
    app.get('/manage/invoicing/cancellation-reason/payment/add/', status=403)
    app.get('/manage/invoicing/cancellation-reason/payment/0/edit/', status=403)
    app.get('/manage/invoicing/cancellation-reason/payment/0/delete/', status=403)

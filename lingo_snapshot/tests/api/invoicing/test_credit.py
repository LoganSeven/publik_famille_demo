import datetime
import random
import uuid
from unittest import mock

import pytest
from django.utils.timezone import now

from lingo.basket.models import Basket
from lingo.invoicing.errors import PayerError
from lingo.invoicing.models import (
    Campaign,
    CollectionDocket,
    Credit,
    CreditAssignment,
    CreditLine,
    DraftInvoice,
    DraftInvoiceLine,
    Invoice,
    InvoiceLine,
    Payment,
    PaymentType,
    Pool,
    Regie,
)

pytestmark = pytest.mark.django_db


@mock.patch.object(Regie, 'get_payer_external_id_from_nameid', autospec=True)
def test_list_credits(mock_payer, app, user):
    app.get('/api/regie/foo/credits/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/credits/', status=404)

    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    app.get('/api/regie/foo/credits/', status=404)

    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=now().date(),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
    )
    payment = Payment.objects.create(
        regie=regie,
        amount=42,
        payment_type=PaymentType.objects.get(regie=regie, slug='credit'),
        payer_external_id='payer:1',
    )
    payment.set_number()
    payment.save()
    credit = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    credit.set_number()
    credit.save()
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit,
        quantity=42,
        unit_amount=1,
        label='Label 11',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    CreditAssignment.objects.create(
        invoice=invoice,
        payment=payment,
        credit=credit,
        amount=1,
    )
    credit.refresh_from_db()

    mock_payer.return_value = 'payer:1'
    resp = app.get('/api/regie/foo/credits/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'id': str(credit.uuid),
            'display_id': 'A%02d-%s-0000001'
            % (
                regie.pk,
                credit.created_at.strftime('%y-%m'),
            ),
            'label': 'A%02d-%s-0000001 - Credit from 01/09/2022 (credit left: 41.00€)'
            % (
                regie.pk,
                credit.created_at.strftime('%y-%m'),
            ),
            'total_amount': 42,
            'remaining_amount': 41,
            'created': now().date().isoformat(),
            'usable': True,
            'has_pdf': True,
        }
    ]
    assert mock_payer.call_args_list == [mock.call(regie, mock.ANY, 'foobar')]
    credit.date_invoicing = datetime.date(2022, 9, 1)
    credit.save()
    resp = app.get('/api/regie/foo/credits/', params={'NameID': 'foobar'})
    assert resp.json['data'][0]['created'] == '2022-09-01'
    assert 'real_created' not in resp.json['data'][0]
    credit.date_invoicing = None
    credit.save()

    # publication date is in the future
    credit.date_publication = now().date() + datetime.timedelta(days=1)
    credit.save()
    resp = app.get('/api/regie/foo/credits/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # credit is cancelled
    credit.date_publication = datetime.date(2022, 10, 1)
    credit.cancelled_at = now()
    credit.save()
    resp = app.get('/api/regie/foo/credits/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    credit.cancelled_at = None
    credit.regie = other_regie
    credit.save()
    resp = app.get('/api/regie/foo/credits/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # campaign is not finalized
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        finalized=False,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
    )
    credit.regie = regie
    credit.pool = pool
    credit.save()
    resp = app.get('/api/regie/foo/credits/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # campaign is finalized
    campaign.finalized = True
    campaign.save()
    resp = app.get('/api/regie/foo/credits/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1

    # no matching payer id
    mock_payer.return_value = 'payer:unknown'
    resp = app.get('/api/regie/foo/credits/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # payer error
    mock_payer.side_effect = PayerError
    app.get('/api/regie/foo/credits/', params={'NameID': 'foobar'}, status=404)

    # credit fully assigned
    mock_payer.return_value = 'payer:1'
    mock_payer.side_effect = None
    CreditAssignment.objects.create(
        invoice=invoice,
        payment=payment,
        credit=credit,
        amount=41,
    )
    resp = app.get('/api/regie/foo/credits/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # filter on usable
    CreditAssignment.objects.all().delete()
    resp = app.get('/api/regie/foo/credits/', params={'NameID': 'foobar', 'usable': True})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1
    resp = app.get('/api/regie/foo/credits/', params={'NameID': 'foobar', 'usable': False})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0
    credit.usable = False
    credit.save()
    resp = app.get('/api/regie/foo/credits/', params={'NameID': 'foobar', 'usable': True})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0
    resp = app.get('/api/regie/foo/credits/', params={'NameID': 'foobar', 'usable': False})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1
    resp = app.get('/api/regie/foo/credits/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1


def test_list_credits_for_payer(app, user):
    app.get('/api/regie/foo/credits/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/credits/', status=404)

    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    app.get('/api/regie/foo/credits/', status=404)

    resp = app.get('/api/regie/foo/credits/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=now().date(),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
    )
    payment = Payment.objects.create(
        regie=regie,
        amount=42,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
        payer_external_id='payer:1',
    )
    payment.set_number()
    payment.save()
    credit = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    credit.set_number()
    credit.save()
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit,
        quantity=42,
        unit_amount=1,
        label='Label 11',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    CreditAssignment.objects.create(
        invoice=invoice,
        payment=payment,
        credit=credit,
        amount=1,
    )
    credit.refresh_from_db()

    resp = app.get('/api/regie/foo/credits/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'id': str(credit.uuid),
            'display_id': 'A%02d-%s-0000001'
            % (
                regie.pk,
                credit.created_at.strftime('%y-%m'),
            ),
            'label': 'A%02d-%s-0000001 - Credit from 01/09/2022 (credit left: 41.00€)'
            % (
                regie.pk,
                credit.created_at.strftime('%y-%m'),
            ),
            'total_amount': 42,
            'remaining_amount': 41,
            'created': now().date().isoformat(),
            'usable': True,
            'has_pdf': True,
        }
    ]
    credit.date_invoicing = datetime.date(2022, 9, 1)
    credit.save()
    resp = app.get('/api/regie/foo/credits/', params={'payer_external_id': 'payer:1'})
    assert resp.json['data'][0]['created'] == '2022-09-01'
    assert resp.json['data'][0]['real_created'] == now().date().isoformat()
    credit.date_invoicing = None
    credit.save()

    # publication date is in the future
    credit.date_publication = now().date() + datetime.timedelta(days=1)
    credit.save()
    resp = app.get('/api/regie/foo/credits/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # credit is cancelled
    credit.date_publication = datetime.date(2022, 10, 1)
    credit.cancelled_at = now()
    credit.save()
    resp = app.get('/api/regie/foo/credits/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    credit.cancelled_at = None
    credit.regie = other_regie
    credit.save()
    resp = app.get('/api/regie/foo/credits/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # campaign is not finalized
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        finalized=False,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
    )
    credit.regie = regie
    credit.pool = pool
    credit.save()
    resp = app.get('/api/regie/foo/credits/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # campaign is finalized
    campaign.finalized = True
    campaign.save()
    resp = app.get('/api/regie/foo/credits/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1

    # credit fully assigned
    CreditAssignment.objects.create(
        invoice=invoice,
        payment=payment,
        credit=credit,
        amount=41,
    )
    resp = app.get('/api/regie/foo/credits/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # filter on usable
    CreditAssignment.objects.all().delete()
    resp = app.get('/api/regie/foo/credits/', params={'payer_external_id': 'payer:1', 'usable': True})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1
    resp = app.get('/api/regie/foo/credits/', params={'payer_external_id': 'payer:1', 'usable': False})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0
    credit.usable = False
    credit.save()
    resp = app.get('/api/regie/foo/credits/', params={'payer_external_id': 'payer:1', 'usable': True})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 0
    resp = app.get('/api/regie/foo/credits/', params={'payer_external_id': 'payer:1', 'usable': False})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1
    resp = app.get('/api/regie/foo/credits/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1


@mock.patch.object(Regie, 'get_payer_external_id_from_nameid', autospec=True)
def test_list_history_credits(mock_payer, app, user):
    app.get('/api/regie/foo/credits/history/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/credits/history/', status=404)

    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    app.get('/api/regie/foo/credits/history/', status=404)

    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=now().date(),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
    )
    payment = Payment.objects.create(
        regie=regie,
        amount=42,
        payment_type=PaymentType.objects.get(regie=regie, slug='credit'),
        payer_external_id='payer:1',
    )
    payment.set_number()
    payment.save()
    credit = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    credit.set_number()
    credit.save()
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit,
        quantity=42,
        unit_amount=1,
        label='Label 11',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    CreditAssignment.objects.create(
        invoice=invoice,
        payment=payment,
        credit=credit,
        amount=1,
    )
    credit.refresh_from_db()

    mock_payer.return_value = 'payer:1'
    resp = app.get('/api/regie/foo/credits/history/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # credit fully assigned
    mock_payer.reset_mock()
    CreditAssignment.objects.create(
        invoice=invoice,
        payment=payment,
        credit=credit,
        amount=41,
    )
    credit.refresh_from_db()
    resp = app.get('/api/regie/foo/credits/history/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'id': str(credit.uuid),
            'display_id': 'A%02d-%s-0000001'
            % (
                regie.pk,
                credit.created_at.strftime('%y-%m'),
            ),
            'label': 'A%02d-%s-0000001 - Credit from 01/09/2022'
            % (
                regie.pk,
                credit.created_at.strftime('%y-%m'),
            ),
            'total_amount': 42,
            'remaining_amount': 0,
            'created': now().date().isoformat(),
            'usable': True,
            'has_pdf': True,
        }
    ]
    assert mock_payer.call_args_list == [mock.call(regie, mock.ANY, 'foobar')]

    # publication date is in the future
    credit.date_publication = now().date() + datetime.timedelta(days=1)
    credit.save()
    resp = app.get('/api/regie/foo/credits/history/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # credit is cancelled
    credit.date_publication = datetime.date(2022, 10, 1)
    credit.cancelled_at = now()
    credit.save()
    resp = app.get('/api/regie/foo/credits/history/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    credit.cancelled_at = None
    credit.regie = other_regie
    credit.save()
    resp = app.get('/api/regie/foo/credits/history/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # campaign is not finalized
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        finalized=False,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
    )
    credit.regie = regie
    credit.pool = pool
    credit.save()
    resp = app.get('/api/regie/foo/credits/history/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # campaign is finalized
    campaign.finalized = True
    campaign.save()
    resp = app.get('/api/regie/foo/credits/history/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1

    # no matching payer id
    mock_payer.return_value = 'payer:unknown'
    resp = app.get('/api/regie/foo/credits/history/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # payer error
    mock_payer.side_effect = PayerError
    app.get('/api/regie/foo/credits/history/', params={'NameID': 'foobar'}, status=404)


def test_list_history_credits_for_payer(app, user):
    app.get('/api/regie/foo/credits/history/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/credits/history/', status=404)

    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    app.get('/api/regie/foo/credits/history/', status=404)

    resp = app.get('/api/regie/foo/credits/history/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=now().date(),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
    )
    payment = Payment.objects.create(
        regie=regie,
        amount=42,
        payment_type=PaymentType.objects.get(regie=regie, slug='cash'),
        payer_external_id='payer:1',
    )
    payment.set_number()
    payment.save()
    credit = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    credit.set_number()
    credit.save()
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit,
        quantity=42,
        unit_amount=1,
        label='Label 11',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    CreditAssignment.objects.create(
        invoice=invoice,
        payment=payment,
        credit=credit,
        amount=1,
    )
    credit.refresh_from_db()

    resp = app.get('/api/regie/foo/credits/history/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # credit fully assigned
    CreditAssignment.objects.create(
        invoice=invoice,
        payment=payment,
        credit=credit,
        amount=41,
    )
    credit.refresh_from_db()
    resp = app.get('/api/regie/foo/credits/history/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'id': str(credit.uuid),
            'display_id': 'A%02d-%s-0000001'
            % (
                regie.pk,
                credit.created_at.strftime('%y-%m'),
            ),
            'label': 'A%02d-%s-0000001 - Credit from 01/09/2022'
            % (
                regie.pk,
                credit.created_at.strftime('%y-%m'),
            ),
            'total_amount': 42,
            'remaining_amount': 0,
            'created': now().date().isoformat(),
            'usable': True,
            'has_pdf': True,
        }
    ]

    # publication date is in the future
    credit.date_publication = now().date() + datetime.timedelta(days=1)
    credit.save()
    resp = app.get('/api/regie/foo/credits/history/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # credit is cancelled
    credit.date_publication = datetime.date(2022, 10, 1)
    credit.cancelled_at = now()
    credit.save()
    resp = app.get('/api/regie/foo/credits/history/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    credit.cancelled_at = None
    credit.regie = other_regie
    credit.save()
    resp = app.get('/api/regie/foo/credits/history/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # campaign is not finalized
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        finalized=False,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
    )
    credit.regie = regie
    credit.pool = pool
    credit.save()
    resp = app.get('/api/regie/foo/credits/history/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # campaign is finalized
    campaign.finalized = True
    campaign.save()
    resp = app.get('/api/regie/foo/credits/history/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert len(resp.json['data']) == 1


@mock.patch.object(Regie, 'get_payer_external_id_from_nameid', autospec=True)
def test_pdf_credit(mock_payer, app, user):
    app.get('/api/regie/foo/credit/%s/pdf/' % str(uuid.uuid4()), status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/credit/%s/pdf/' % str(uuid.uuid4()), status=404)

    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    app.get('/api/regie/foo/credit/%s/pdf/' % str(uuid.uuid4()), status=404)

    app.get('/api/regie/foo/credit/%s/pdf/' % str(uuid.uuid4()), params={'NameID': 'foobar'}, status=404)

    credit = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )

    mock_payer.return_value = 'payer:1'
    resp = app.get('/api/regie/foo/credit/%s/pdf/' % str(credit.uuid), params={'NameID': 'foobar'})
    assert resp.headers['Content-Disposition'] == 'attachment; filename="%s.pdf"' % credit.formatted_number

    # publication date is in the future
    credit.date_publication = now().date() + datetime.timedelta(days=1)
    credit.save()
    app.get('/api/regie/foo/credit/%s/pdf/' % str(credit.uuid), params={'NameID': 'foobar'}, status=404)

    # credit is cancelled
    credit.date_publication = datetime.date(2022, 10, 1)
    credit.cancelled_at = now()
    credit.save()
    app.get('/api/regie/foo/credit/%s/pdf/' % str(credit.uuid), params={'NameID': 'foobar'}, status=404)

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    credit.cancelled_at = None
    credit.regie = other_regie
    credit.save()
    app.get('/api/regie/foo/credit/%s/pdf/' % str(credit.uuid), params={'NameID': 'foobar'}, status=404)

    # campaign is not finalized
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        finalized=False,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
    )
    credit.regie = regie
    credit.pool = pool
    credit.save()
    app.get('/api/regie/foo/credit/%s/pdf/' % str(credit.uuid), params={'NameID': 'foobar'}, status=404)

    # campaign is finalized
    campaign.finalized = True
    campaign.save()
    app.get(
        '/api/regie/foo/credit/%s/pdf/' % str(credit.uuid),
        params={'NameID': 'foobar'},
    )

    # no matching payer id
    mock_payer.return_value = 'payer:unknown'
    app.get('/api/regie/foo/credit/%s/' % str(credit.uuid), params={'NameID': 'foobar'}, status=404)

    # payer error
    mock_payer.side_effect = PayerError
    app.get('/api/regie/foo/credit/%s/' % str(credit.uuid), params={'NameID': 'foobar'}, status=404)


def test_pdf_credit_for_payer(app, user):
    app.get('/api/regie/foo/credit/%s/pdf/' % str(uuid.uuid4()), status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/credit/%s/pdf/' % str(uuid.uuid4()), status=404)

    regie = Regie.objects.create(label='Foo')
    PaymentType.create_defaults(regie)
    app.get('/api/regie/foo/credit/%s/pdf/' % str(uuid.uuid4()), status=404)

    app.get(
        '/api/regie/foo/credit/%s/pdf/' % str(uuid.uuid4()),
        params={'payer_external_id': 'payer:1'},
        status=404,
    )

    credit = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )

    resp = app.get(
        '/api/regie/foo/credit/%s/pdf/' % str(credit.uuid), params={'payer_external_id': 'payer:1'}
    )
    assert resp.headers['Content-Disposition'] == 'attachment; filename="%s.pdf"' % credit.formatted_number

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    credit.regie = other_regie
    credit.save()
    # publication date is in the future
    credit.date_publication = now().date() + datetime.timedelta(days=1)
    credit.save()
    app.get(
        '/api/regie/foo/credit/%s/pdf/' % str(credit.uuid),
        params={'payer_external_id': 'payer:1'},
        status=404,
    )

    # credit is cancelled
    credit.date_publication = datetime.date(2022, 10, 1)
    credit.cancelled_at = now()
    credit.save()
    app.get(
        '/api/regie/foo/credit/%s/pdf/' % str(credit.uuid),
        params={'payer_external_id': 'payer:1'},
        status=404,
    )

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    credit.cancelled_at = None
    credit.regie = other_regie
    credit.save()
    app.get(
        '/api/regie/foo/credit/%s/pdf/' % str(credit.uuid),
        params={'payer_external_id': 'payer:1'},
        status=404,
    )

    # campaign is not finalized
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        finalized=False,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
    )
    credit.regie = regie
    credit.pool = pool
    credit.save()
    app.get(
        '/api/regie/foo/credit/%s/pdf/' % str(credit.uuid),
        params={'payer_external_id': 'payer:1'},
        status=404,
    )

    # campaign is finalized
    campaign.finalized = True
    campaign.save()
    app.get(
        '/api/regie/foo/credit/%s/pdf/' % str(credit.uuid),
        params={'payer_external_id': 'payer:1'},
    )


def test_add_draft_credit(app, user):
    app.post('/api/regie/foo/draft-credits/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    resp = app.post('/api/regie/foo/draft-credits/', status=404)

    regie = Regie.objects.create(label='Foo')
    other_regie = Regie.objects.create(label='Other Foo')
    resp = app.post('/api/regie/foo/draft-credits/', status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'date_publication': ['This field is required.'],
        'label': ['This field is required.'],
        'payer_external_id': ['This field is required.'],
        'payer_first_name': ['This field is required.'],
        'payer_last_name': ['This field is required.'],
        'payer_address': ['This field is required.'],
    }

    for usable in [True, False, None]:
        params = {
            'date_publication': '2023-04-21',
            'label': 'Foo Bar',
            'payer_external_id': 'payer:1',
            'payer_first_name': 'First',
            'payer_last_name': 'Last',
            'payer_address': '41 rue des kangourous\n99999 Kangourou Ville',
            'payer_email': 'email1',
            'payer_phone': 'phone1',
        }
        if usable is not None:
            params['usable'] = usable
            params['previous_invoice'] = ''
        resp = app.post('/api/regie/foo/draft-credits/', params=params)
        assert resp.json['err'] == 0
        invoice = DraftInvoice.objects.latest('pk')
        assert resp.json['data'] == {'draft_invoice_id': str(invoice.uuid)}
        assert invoice.label == 'Foo Bar'
        assert invoice.total_amount == 0
        assert invoice.date_publication == datetime.date(2023, 4, 21)
        assert invoice.date_payment_deadline == datetime.date(2023, 4, 21)
        assert invoice.date_due == datetime.date(2023, 4, 21)
        assert invoice.date_debit is None
        assert invoice.date_invoicing is None
        assert invoice.regie == regie
        assert invoice.payer_external_id == 'payer:1'
        assert invoice.payer_first_name == 'First'
        assert invoice.payer_last_name == 'Last'
        assert invoice.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
        assert invoice.payer_email == 'email1'
        assert invoice.payer_phone == 'phone1'
        assert invoice.payer_direct_debit is False
        assert invoice.usable == usable if usable is not None else True
        assert invoice.pool is None
        assert invoice.previous_invoice is None
        assert invoice.origin == 'api'
        assert invoice.lines.count() == 0

    previous_invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=now().date(),
        date_due=datetime.date(2022, 10, 31),
        regie=other_regie,
        payer_external_id='payer:1',
        payer_first_name='Foo',
        payer_last_name='Bar',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )

    params['previous_invoice'] = uuid.uuid4()
    params['date_invoicing'] = '2022-11-06'
    resp = app.post('/api/regie/foo/draft-credits/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'previous_invoice': ['Unknown invoice.'],
    }

    params['previous_invoice'] = previous_invoice.uuid
    resp = app.post('/api/regie/foo/draft-credits/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'previous_invoice': ['Unknown invoice.'],
    }

    previous_invoice.regie = regie
    previous_invoice.save()
    resp = app.post('/api/regie/foo/draft-credits/', params=params)
    assert resp.json['err'] == 0
    invoice = DraftInvoice.objects.latest('pk')
    assert invoice.previous_invoice == previous_invoice
    assert invoice.date_invoicing == datetime.date(2022, 11, 6)


def test_add_draft_credit_line(app, user):
    app.post('/api/regie/foo/draft-credit/%s/lines/' % str(uuid.uuid4()), status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.post('/api/regie/foo/draft-credit/%s/lines/' % str(uuid.uuid4()), status=404)

    regie = Regie.objects.create(label='Foo')
    app.post('/api/regie/foo/draft-credit/%s/lines/' % str(uuid.uuid4()), status=404)

    invoice = DraftInvoice.objects.create(
        date_publication=datetime.date(2023, 4, 21),
        date_due=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 21),
        regie=regie,
        payer_external_id=random.choice(['payer:1', 'payer:2']),
        payer_first_name=random.choice(['First', 'Fiirst']),
        payer_last_name=random.choice(['Last', 'Laast']),
        payer_address=random.choice(
            [
                '41 rue des kangourous\n99999 Kangourou Ville',
                '42 rue des kangourous\n99999 Kangourou Ville',
            ]
        ),
        payer_direct_debit=random.choice([True, False]),
    )

    app.post('/api/regie/fooooo/draft-credit/%s/lines/' % str(invoice.uuid), status=404)

    resp = app.post('/api/regie/foo/draft-credit/%s/lines/' % str(invoice.uuid), status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'event_date': ['This field is required.'],
        'label': ['This field is required.'],
        'quantity': ['This field is required.'],
        'slug': ['This field is required.'],
        'unit_amount': ['This field is required.'],
        'user_external_id': ['This field is required.'],
        'user_first_name': ['This field is required.'],
        'user_last_name': ['This field is required.'],
    }

    params = {
        'event_date': '2023-04-21',
        'label': 'Bar Foo',
        'quantity': '2',
        'slug': 'bar-foo',
        'unit_amount': '21',
        'user_external_id': 'user:1',
        'user_first_name': 'First1',
        'user_last_name': 'Last1',
    }
    resp = app.post('/api/regie/foo/draft-credit/%s/lines/' % str(invoice.uuid), params=params)
    assert resp.json['err'] == 0
    line1 = DraftInvoiceLine.objects.latest('pk')
    assert resp.json['data'] == {'draft_line_id': line1.pk}
    assert line1.invoice == invoice
    assert line1.event_date == datetime.date(2023, 4, 21)
    assert line1.label == 'Bar Foo'
    assert line1.quantity == 2
    assert line1.unit_amount == 21
    assert line1.total_amount == 42
    assert line1.accounting_code == ''
    assert line1.user_external_id == 'user:1'
    assert line1.user_first_name == 'First1'
    assert line1.user_last_name == 'Last1'
    assert line1.details == {'dates': ['2023-04-21']}
    assert line1.event_slug == 'bar-foo'
    assert line1.agenda_slug == ''
    assert line1.activity_label == ''
    assert line1.description == ''
    assert line1.pool is None
    invoice.refresh_from_db()
    assert invoice.total_amount == 42

    params = {
        'event_date': '2023-04-21',
        'label': 'Bar Foo',
        'quantity': '2',
        'slug': 'agenda@bar-foo',
        'activity_label': 'Activity Label !',
        'description': 'A description !',
        'unit_amount': '21',
        'accounting_code': '424242',
        'user_external_id': 'user:1',
        'user_first_name': 'First1',
        'user_last_name': 'Last1',
    }
    resp = app.post('/api/regie/foo/draft-credit/%s/lines/' % str(invoice.uuid), params=params)
    assert resp.json['err'] == 0
    line2 = DraftInvoiceLine.objects.latest('pk')
    assert resp.json['data'] == {'draft_line_id': line2.pk}
    assert line2.invoice == invoice
    assert line2.event_date == datetime.date(2023, 4, 21)
    assert line2.label == 'Bar Foo'
    assert line2.quantity == 2
    assert line2.unit_amount == 21
    assert line2.total_amount == 42
    assert line2.accounting_code == '424242'
    assert line2.user_external_id == 'user:1'
    assert line2.user_first_name == 'First1'
    assert line2.user_last_name == 'Last1'
    assert line2.details == {'dates': ['2023-04-21']}
    assert line2.event_slug == 'agenda@bar-foo'
    assert line2.event_label == 'Bar Foo'
    assert line2.agenda_slug == 'agenda'
    assert line2.activity_label == 'Activity Label !'
    assert line2.description == 'A description !'
    assert line2.pool is None
    invoice.refresh_from_db()
    assert invoice.total_amount == 84

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
    )
    invoice.pool = pool
    invoice.save()
    app.post('/api/regie/foo/draft-credit/%s/lines/' % str(invoice.uuid), status=404)

    invoice.pool = None
    invoice.save()

    # test merge feature

    # missing agenda_slug, event_slug, subject
    params = {
        'event_date': '2023-04-21',
        'label': 'Bar Foo',
        'quantity': '2',
        'slug': 'bar-foo',
        'unit_amount': '21',
        'user_external_id': 'user:1',
        'user_first_name': 'First1',
        'user_last_name': 'Last1',
        'form_url': 'http://form.com/3/',
    }
    resp = app.post('/api/regie/foo/draft-credit/%s/lines/' % str(invoice.uuid), params=params)
    assert resp.json['err'] == 0
    line3 = DraftInvoiceLine.objects.latest('pk')
    assert line3.form_url == 'http://form.com/3/'
    assert resp.json['data'] == {'draft_line_id': line3.pk}
    invoice.refresh_from_db()
    assert invoice.total_amount == 126

    # same params, but no subject
    params = {
        'event_date': '2023-04-21',
        'label': 'Bar Foo',
        'quantity': '1',
        'slug': 'agenda@bar-foo',
        'activity_label': 'Activity Label !',
        'description': 'A new description !',
        'unit_amount': '21',
        'accounting_code': '424242',
        'user_external_id': 'user:1',
        'user_first_name': 'First1',
        'user_last_name': 'Last1',
        'form_url': 'http://form.com/4/',
        'merge_lines': True,
    }
    resp = app.post('/api/regie/foo/draft-credit/%s/lines/' % str(invoice.uuid), params=params)
    line4 = DraftInvoiceLine.objects.latest('pk')
    assert resp.json['data'] == {'draft_line_id': line4.pk}
    assert line4.invoice == invoice
    assert line4.event_date == datetime.date(2023, 4, 21)
    assert line4.label == 'Bar Foo'
    assert line4.quantity == 1
    assert line4.unit_amount == 21
    assert line4.total_amount == 21
    assert line4.accounting_code == '424242'
    assert line4.user_external_id == 'user:1'
    assert line4.user_first_name == 'First1'
    assert line4.user_last_name == 'Last1'
    assert line4.details == {'dates': ['2023-04-21']}
    assert line4.event_slug == 'agenda@bar-foo'
    assert line4.event_label == 'Bar Foo'
    assert line4.agenda_slug == 'agenda'
    assert line4.activity_label == 'Activity Label !'
    assert line4.form_url == 'http://form.com/4/'
    assert line4.description == 'A new description !'
    assert line4.pool is None
    invoice.refresh_from_db()
    assert invoice.total_amount == 147

    # same params with subject
    params = {
        'event_date': '2023-04-21',
        'label': 'Bar Foo',
        'quantity': '1',
        'slug': 'agenda@bar-foo',
        'activity_label': 'Activity Label !',
        'description': 'Another description !',
        'unit_amount': '21',
        'accounting_code': '424242',
        'user_external_id': 'user:1',
        'user_first_name': 'First1',
        'user_last_name': 'Last1',
        'form_url': 'http://form.com/5/',
        'merge_lines': True,
        'subject': 'FooBar',
    }
    resp = app.post('/api/regie/foo/draft-credit/%s/lines/' % str(invoice.uuid), params=params)
    line5 = DraftInvoiceLine.objects.latest('pk')
    assert resp.json['data'] == {'draft_line_id': line5.pk}
    assert line5.invoice == invoice
    assert line5.event_date == datetime.date(2023, 4, 21)
    assert line5.label == 'Bar Foo'
    assert line5.quantity == 1
    assert line5.unit_amount == 21
    assert line5.total_amount == 21
    assert line5.accounting_code == '424242'
    assert line5.user_external_id == 'user:1'
    assert line5.user_first_name == 'First1'
    assert line5.user_last_name == 'Last1'
    assert line5.details == {'dates': ['2023-04-21']}
    assert line5.event_slug == 'agenda@bar-foo'
    assert line5.event_label == 'Bar Foo'
    assert line5.agenda_slug == 'agenda'
    assert line5.activity_label == 'Activity Label !'
    assert line5.description == 'FooBar Another description !'
    assert line5.form_url == 'http://form.com/5/'
    assert line5.pool is None
    invoice.refresh_from_db()
    assert invoice.total_amount == 168

    # again
    params['description'] = 'Again !'
    params['event_date'] = '2023-04-22'
    resp = app.post('/api/regie/foo/draft-credit/%s/lines/' % str(invoice.uuid), params=params)
    line5.refresh_from_db()
    assert resp.json['data'] == {'draft_line_id': line5.pk}
    assert line5.event_date == datetime.date(2023, 4, 21)
    assert line5.quantity == 2
    assert line5.unit_amount == 21
    assert line5.total_amount == 42
    assert line5.description == 'FooBar Another description !, Again !'
    assert line5.form_url == 'http://form.com/5/'
    assert line5.details == {'dates': ['2023-04-21', '2023-04-22']}
    invoice.refresh_from_db()
    assert invoice.total_amount == 189

    # change params
    params['event_date'] = '2023-04-21'
    values = [
        ('label', 'Bar Fooo'),
        ('slug', 'agendaa@bar-foo'),  # change agenda
        ('slug', 'agenda@bar-fooo'),  # change event
        ('activity_label', ''),
        ('activity_label', 'Activity Label !!'),
        ('unit_amount', 20),
        ('accounting_code', ''),
        ('accounting_code', '424243'),
        ('user_external_id', 'user:2'),
        ('form_url', 'http://form.com/xx/'),
    ]
    for key, value in values:
        new_params = params.copy()
        new_params[key] = value
        resp = app.post('/api/regie/foo/draft-credit/%s/lines/' % str(invoice.uuid), params=new_params)
        assert resp.json['err'] == 0
        line = DraftInvoiceLine.objects.latest('pk')
        assert resp.json['data'] == {'draft_line_id': line.pk}
        assert line.invoice == invoice
        assert line.event_date == datetime.date(2023, 4, 21)
        assert line.label == ('Bar Foo' if key != 'label' else value)
        assert line.quantity == 1
        assert line.unit_amount == (21 if key != 'unit_amount' else value)
        assert line.accounting_code == ('424242' if key != 'accounting_code' else value)
        assert line.user_external_id == ('user:1' if key != 'user_external_id' else value)
        assert line.user_first_name == 'First1'
        assert line.user_last_name == 'Last1'
        assert line.details == {'dates': ['2023-04-21']}
        assert line.event_slug == ('agenda@bar-foo' if key != 'slug' else value)
        assert line.event_label == ('Bar Foo' if key != 'label' else value)
        assert line.agenda_slug == ('agenda' if key != 'slug' else value.split('@', maxsplit=1)[0])
        assert line.activity_label == ('Activity Label !' if key != 'activity_label' else value)
        assert line.description == 'FooBar Again !'
        assert line.form_url == ('http://form.com/5/' if key != 'form_url' else value)

    # change subject, other line
    params['subject'] = 'Other subject'
    del params['form_url']
    resp = app.post('/api/regie/foo/draft-credit/%s/lines/' % str(invoice.uuid), params=params)
    line6 = DraftInvoiceLine.objects.latest('pk')
    assert resp.json['data'] == {'draft_line_id': line6.pk}
    assert line6.invoice == invoice
    assert line6.event_date == datetime.date(2023, 4, 21)
    assert line6.label == 'Bar Foo'
    assert line6.quantity == 1
    assert line6.unit_amount == 21
    assert line6.total_amount == 21
    assert line6.accounting_code == '424242'
    assert line6.user_external_id == 'user:1'
    assert line6.user_first_name == 'First1'
    assert line6.user_last_name == 'Last1'
    assert line6.details == {'dates': ['2023-04-21']}
    assert line6.event_slug == 'agenda@bar-foo'
    assert line6.event_label == 'Bar Foo'
    assert line6.agenda_slug == 'agenda'
    assert line6.activity_label == 'Activity Label !'
    assert line6.description == 'Other subject Again !'
    assert line6.form_url == ''
    assert line6.pool is None


def test_close_draft_credit(app, user):
    app.post('/api/regie/foo/draft-credit/%s/close/' % str(uuid.uuid4()), status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.post('/api/regie/foo/draft-credit/%s/close/' % str(uuid.uuid4()), status=404)

    regie = Regie.objects.create(label='Foo')
    app.post('/api/regie/foo/draft-credit/%s/close/' % str(uuid.uuid4()), status=404)

    previous_invoice = Invoice.objects.create(
        label='My invoice',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=now().date(),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='Foo',
        payer_last_name='Bar',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    invoice = DraftInvoice.objects.create(
        date_publication=datetime.date(2023, 4, 21),
        date_due=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 21),
        date_invoicing=datetime.date(2022, 11, 6),
        regie=regie,
        label='Foo Bar',
        payer_external_id=random.choice(['payer:1', 'payer:2']),
        payer_first_name=random.choice(['First', 'Fiirst']),
        payer_last_name=random.choice(['Last', 'Laast']),
        payer_address=random.choice(
            [
                '41 rue des kangourous\n99999 Kangourou Ville',
                '42 rue des kangourous\n99999 Kangourou Ville',
            ]
        ),
        payer_email=random.choice(['email1', 'email2']),
        payer_phone=random.choice(['phone1', 'phone2']),
        payer_direct_debit=random.choice([True, False]),
        previous_invoice=previous_invoice,
        origin='api',
    )
    line = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2023, 4, 21),
        quantity=1,
        unit_amount=42,
        invoice=invoice,
        form_url='http://form.com',
    )
    line.refresh_from_db()
    invoice.refresh_from_db()

    app.post('/api/regie/fooooo/draft-credit/%s/close/' % str(invoice.uuid), status=404)

    resp = app.post('/api/regie/foo/draft-credit/%s/close/' % str(invoice.uuid), status=400)
    assert resp.json['err'] == 1
    assert resp.json['err_class'] == 'can not create credit from draft invoice with positive amount'

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
    )
    invoice.pool = pool
    invoice.save()
    app.post('/api/regie/foo/draft-credit/%s/close/' % str(invoice.uuid), status=404)

    invoice.pool = None
    invoice.save()
    line2 = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2023, 4, 21),
        quantity=-1,
        unit_amount=45,
        invoice=invoice,
    )
    line2.refresh_from_db()
    invoice.refresh_from_db()

    resp = app.post('/api/regie/foo/draft-credit/%s/close/' % str(invoice.uuid))
    assert resp.json['err'] == 0
    assert Invoice.objects.count() == 1
    assert Invoice.objects.get() == previous_invoice
    assert Credit.objects.count() == 1
    credit = Credit.objects.latest('pk')
    assert resp.json['data'] == {
        'credit_id': str(credit.uuid),
        'credit': {
            'id': str(credit.uuid),
            'total_amount': 3,
        },
        'urls': {
            'credit_in_backoffice': 'http://testserver/manage/invoicing/redirect/credit/%s/' % credit.uuid,
            'credit_pdf': 'http://testserver/manage/invoicing/redirect/credit/%s/pdf/' % credit.uuid,
        },
        'api_urls': {
            'credit_pdf': 'http://testserver/api/regie/foo/credit/%s/pdf/' % credit.uuid,
        },
    }
    assert credit.regie == regie
    assert credit.label == 'Credit from %s' % now().strftime('%d/%m/%Y')
    assert credit.payer_external_id == invoice.payer_external_id
    assert credit.payer_first_name == invoice.payer_first_name
    assert credit.payer_last_name == invoice.payer_last_name
    assert credit.payer_address == invoice.payer_address
    assert credit.payer_phone == invoice.payer_phone
    assert credit.payer_email == invoice.payer_email
    assert credit.total_amount == -invoice.total_amount == 3
    assert credit.number == 1
    assert credit.formatted_number == 'A%02d-22-11-0000001' % regie.pk
    assert credit.pool is None
    assert credit.date_publication == datetime.date(2023, 4, 21)
    assert credit.date_invoicing == invoice.date_invoicing
    assert credit.previous_invoice == previous_invoice
    assert credit.origin == 'api'

    credit_line1 = CreditLine.objects.order_by('pk')[0]
    assert credit_line1.event_date == line.event_date
    assert credit_line1.label == line.label
    assert credit_line1.quantity == -line.quantity
    assert credit_line1.unit_amount == line.unit_amount
    assert credit_line1.total_amount == -line.total_amount
    assert credit_line1.user_external_id == line.user_external_id
    assert credit_line1.user_first_name == line.user_first_name
    assert credit_line1.user_last_name == line.user_last_name
    assert credit_line1.form_url == line.form_url
    assert credit_line1.credit == credit
    credit_line2 = CreditLine.objects.order_by('pk')[1]
    assert credit_line2.event_date == line2.event_date
    assert credit_line2.label == line2.label
    assert credit_line2.quantity == -line2.quantity
    assert credit_line2.unit_amount == line2.unit_amount
    assert credit_line2.total_amount == -line2.total_amount
    assert credit_line2.user_external_id == line2.user_external_id
    assert credit_line2.user_first_name == line2.user_first_name
    assert credit_line2.user_last_name == line2.user_last_name
    assert credit_line2.form_url == line2.form_url
    assert credit_line2.credit == credit


def test_close_draft_credit_with_invoices(app, user):
    app.authorization = ('Basic', ('john.doe', 'password'))

    regie = Regie.objects.create(label='Foo')
    other_regie = Regie.objects.create(label='Other Foo')
    invoice = DraftInvoice.objects.create(
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First',
        payer_last_name='Last',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    line = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2023, 4, 21),
        quantity=-1,
        unit_amount=42,
        invoice=invoice,
    )
    line.refresh_from_db()
    invoice.refresh_from_db()

    invoice1 = Invoice.objects.create(
        label='Invoice from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payment_callback_url='http://payment1.com',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=5,
        unit_amount=1,
    )
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        finalized=True,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
    )
    invoice2 = Invoice.objects.create(
        label='Invoice from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        pool=pool,
        payment_callback_url='http://payment2.com',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=5,
        unit_amount=1,
    )
    invoice3 = Invoice.objects.create(
        label='Invoice from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:42',  # wrong payer
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice3,
        quantity=5,
        unit_amount=1,
    )
    other_invoice = Invoice.objects.create(
        label='Invoice from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=other_regie,  # other regie
        payer_external_id='payer:1',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=other_invoice,
        quantity=5,
        unit_amount=1,
    )
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        finalized=False,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
    )
    other_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        pool=pool,  # not finalized pool
    )
    InvoiceLine.objects.create(
        invoice=other_invoice,
        event_date=datetime.date(2022, 9, 1),
        quantity=5,
        unit_amount=1,
    )
    other_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        cancelled_at=now(),  # cancelled
    )
    InvoiceLine.objects.create(
        invoice=other_invoice,
        event_date=datetime.date(2022, 9, 1),
        quantity=5,
        unit_amount=1,
    )
    other_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
    )
    InvoiceLine.objects.create(
        invoice=other_invoice,
        event_date=datetime.date(2022, 9, 1),
        quantity=5,
        unit_amount=1,
    )
    draft_invoice = DraftInvoice.objects.create(
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First',
        payer_last_name='Last',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    Basket.objects.create(
        regie=regie,
        draft_invoice=draft_invoice,
        invoice=other_invoice,  # in basket
    )
    other_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date() - datetime.timedelta(days=1),  # not payable
        regie=regie,
        payer_external_id='payer:1',
    )
    InvoiceLine.objects.create(
        invoice=other_invoice,
        event_date=datetime.date(2022, 9, 1),
        quantity=5,
        unit_amount=1,
    )
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=True)
    collected_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        collection=collection,
    )
    collected_invoice.set_number()
    collected_invoice.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=collected_invoice,
        quantity=2,
        unit_amount=1,
    )

    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/draft-credit/%s/close/' % str(invoice.uuid))
    assert [x[0][0].url for x in mock_send.call_args_list] == ['http://payment1.com/', 'http://payment2.com/']
    assert resp.json['err'] == 0
    credit = Credit.objects.latest('pk')
    assert credit.total_amount == 42
    assert credit.remaining_amount == 32
    invoice1.refresh_from_db()
    assert invoice1.remaining_amount == 0
    assert invoice1.paid_amount == 5
    invoice2.refresh_from_db()
    assert invoice2.remaining_amount == 0
    assert invoice2.paid_amount == 5
    assert Payment.objects.count() == 2
    assert CreditAssignment.objects.count() == 2
    assignment1, assignment2 = CreditAssignment.objects.all().order_by('pk')
    assert assignment1.amount == 5
    assert assignment1.invoice == invoice1
    assert assignment1.credit == credit
    assert assignment2.amount == 5
    assert assignment2.invoice == invoice2
    assert assignment2.credit == credit
    assert Payment.objects.count() == 2
    payment1, payment2 = Payment.objects.all().order_by('pk')
    assert payment1.amount == 5
    assert payment1.payment_type.slug == 'credit'
    assert payment2.amount == 5
    assert payment2.payment_type.slug == 'credit'
    assert assignment1.payment == payment1
    assert assignment2.payment == payment2
    assert payment1.invoicelinepayment_set.count() == 1
    (invoicelinepayment11,) = payment1.invoicelinepayment_set.order_by('pk')
    assert invoicelinepayment11.line == invoice1.lines.get()
    assert invoicelinepayment11.amount == 5
    assert payment2.invoicelinepayment_set.count() == 1
    (invoicelinepayment21,) = payment2.invoicelinepayment_set.order_by('pk')
    assert invoicelinepayment21.line == invoice2.lines.get()
    assert invoicelinepayment21.amount == 5

    # more invoice amount to pay than credit amount
    invoice = DraftInvoice.objects.create(
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First',
        payer_last_name='Last',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    line = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2023, 4, 21),
        quantity=-1,
        unit_amount=42,
        invoice=invoice,
    )
    line.refresh_from_db()
    invoice.refresh_from_db()

    invoice1 = Invoice.objects.create(
        label='Invoice from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payment_callback_url='http://payment.com',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=43,
        unit_amount=1,
    )

    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/draft-credit/%s/close/' % str(invoice.uuid))
    assert [x[0][0].url for x in mock_send.call_args_list] == []
    assert resp.json['err'] == 0
    credit = Credit.objects.latest('pk')
    assert credit.total_amount == 42
    assert credit.remaining_amount == 0
    invoice1.refresh_from_db()
    assert invoice1.remaining_amount == 1
    assert invoice1.paid_amount == 42

    # credit not usable to pay invoices
    invoice = DraftInvoice.objects.create(
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First',
        payer_last_name='Last',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        usable=False,
    )
    line = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2023, 4, 21),
        quantity=-1,
        unit_amount=42,
        invoice=invoice,
    )
    line.refresh_from_db()
    invoice.refresh_from_db()

    invoice1 = Invoice.objects.create(
        label='Invoice from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payment_callback_url='http://payment.com',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=43,
        unit_amount=1,
    )

    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/draft-credit/%s/close/' % str(invoice.uuid))
    assert [x[0][0].url for x in mock_send.call_args_list] == []
    assert resp.json['err'] == 0
    credit = Credit.objects.latest('pk')
    assert credit.total_amount == 42
    assert credit.remaining_amount == 42
    invoice1.refresh_from_db()
    assert invoice1.remaining_amount == 43
    assert invoice1.paid_amount == 0

    # credit assignment disabled from params
    invoice = DraftInvoice.objects.create(
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First',
        payer_last_name='Last',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    line = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2023, 4, 21),
        quantity=-1,
        unit_amount=42,
        invoice=invoice,
    )
    line.refresh_from_db()
    invoice.refresh_from_db()

    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post(
            '/api/regie/foo/draft-credit/%s/close/' % str(invoice.uuid), params={'make_assignments': False}
        )
    assert [x[0][0].url for x in mock_send.call_args_list] == []
    assert resp.json['err'] == 0
    credit = Credit.objects.latest('pk')
    assert credit.total_amount == 42
    assert credit.remaining_amount == 42
    invoice1.refresh_from_db()
    assert invoice1.remaining_amount == 43
    assert invoice1.paid_amount == 0

    # regie not configured to assign credits when created
    regie.assign_credits_on_creation = False
    regie.save()
    invoice = DraftInvoice.objects.create(
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First',
        payer_last_name='Last',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    line = DraftInvoiceLine.objects.create(
        event_date=datetime.date(2023, 4, 21),
        quantity=-1,
        unit_amount=42,
        invoice=invoice,
    )
    line.refresh_from_db()
    invoice.refresh_from_db()

    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/draft-credit/%s/close/' % str(invoice.uuid))
    assert [x[0][0].url for x in mock_send.call_args_list] == []
    assert resp.json['err'] == 0
    credit = Credit.objects.latest('pk')
    assert credit.total_amount == 42
    assert credit.remaining_amount == 42
    invoice1.refresh_from_db()
    assert invoice1.remaining_amount == 43
    assert invoice1.paid_amount == 0


def test_assign_credit(app, user):
    app.post('/api/regie/foo/credit/%s/assign/' % str(uuid.uuid4()), status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.post('/api/regie/foo/credit/%s/assign/' % str(uuid.uuid4()), status=404)

    regie = Regie.objects.create(label='Foo', assign_credits_on_creation=False)
    app.post('/api/regie/foo/credit/%s/assign/' % str(uuid.uuid4()), status=404)

    credit = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    credit.set_number()
    credit.save()
    credit_line = CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit,
        quantity=42,
        unit_amount=1,
        label='Label 11',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    app.post('/api/regie/foo/credit/%s/assign/' % credit.uuid)
    credit.refresh_from_db()
    assert credit.assigned_amount == 0
    assert credit.remaining_amount == 42

    invoice1 = Invoice.objects.create(
        label='Invoice from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payment_callback_url='http://payment1.com',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=5,
        unit_amount=1,
    )
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        finalized=True,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
    )
    invoice2 = Invoice.objects.create(
        label='Invoice from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        pool=pool,
        payment_callback_url='http://payment2.com',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=5,
        unit_amount=1,
    )
    invoice3 = Invoice.objects.create(
        label='Invoice from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:42',  # wrong payer
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice3,
        quantity=5,
        unit_amount=1,
    )
    other_regie = Regie.objects.create(label='Other Foo')
    other_invoice = Invoice.objects.create(
        label='Invoice from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=other_regie,  # other regie
        payer_external_id='payer:1',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=other_invoice,
        quantity=5,
        unit_amount=1,
    )
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        finalized=False,
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
    )
    other_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        pool=pool,  # not finalized pool
    )
    InvoiceLine.objects.create(
        invoice=other_invoice,
        event_date=datetime.date(2022, 9, 1),
        quantity=5,
        unit_amount=1,
    )
    other_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        cancelled_at=now(),  # cancelled
    )
    InvoiceLine.objects.create(
        invoice=other_invoice,
        event_date=datetime.date(2022, 9, 1),
        quantity=5,
        unit_amount=1,
    )
    other_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
    )
    InvoiceLine.objects.create(
        invoice=other_invoice,
        event_date=datetime.date(2022, 9, 1),
        quantity=5,
        unit_amount=1,
    )
    draft_invoice = DraftInvoice.objects.create(
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First',
        payer_last_name='Last',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    Basket.objects.create(
        regie=regie,
        draft_invoice=draft_invoice,
        invoice=other_invoice,  # in basket
    )
    other_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date() - datetime.timedelta(days=1),  # not payable
        regie=regie,
        payer_external_id='payer:1',
    )
    InvoiceLine.objects.create(
        invoice=other_invoice,
        event_date=datetime.date(2022, 9, 1),
        quantity=5,
        unit_amount=1,
    )
    collection = CollectionDocket.objects.create(regie=regie, date_end=now().date(), draft=True)
    collected_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        collection=collection,
    )
    collected_invoice.set_number()
    collected_invoice.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=collected_invoice,
        quantity=2,
        unit_amount=1,
    )

    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        resp = app.post('/api/regie/foo/credit/%s/assign/' % credit.uuid)
    assert [x[0][0].url for x in mock_send.call_args_list] == ['http://payment1.com/', 'http://payment2.com/']
    assert resp.json['err'] == 0
    credit.refresh_from_db()
    assert credit.assigned_amount == 10
    assert credit.remaining_amount == 32
    invoice1.refresh_from_db()
    assert invoice1.remaining_amount == 0
    assert invoice1.paid_amount == 5
    invoice2.refresh_from_db()
    assert invoice2.remaining_amount == 0
    assert invoice2.paid_amount == 5
    assert Payment.objects.count() == 2
    assert CreditAssignment.objects.count() == 2
    assignment1, assignment2 = CreditAssignment.objects.all().order_by('pk')
    assert assignment1.amount == 5
    assert assignment1.invoice == invoice1
    assert assignment1.credit == credit
    assert assignment2.amount == 5
    assert assignment2.invoice == invoice2
    assert assignment2.credit == credit
    assert Payment.objects.count() == 2
    payment1, payment2 = Payment.objects.all().order_by('pk')
    assert payment1.amount == 5
    assert payment1.payment_type.slug == 'credit'
    assert payment2.amount == 5
    assert payment2.payment_type.slug == 'credit'
    assert assignment1.payment == payment1
    assert assignment2.payment == payment2
    assert payment1.invoicelinepayment_set.count() == 1
    (invoicelinepayment11,) = payment1.invoicelinepayment_set.order_by('pk')
    assert invoicelinepayment11.line == invoice1.lines.get()
    assert invoicelinepayment11.amount == 5
    assert payment2.invoicelinepayment_set.count() == 1
    (invoicelinepayment21,) = payment2.invoicelinepayment_set.order_by('pk')
    assert invoicelinepayment21.line == invoice2.lines.get()
    assert invoicelinepayment21.amount == 5

    # already assigned
    credit_line.quantity = 10
    credit_line.save()
    app.post('/api/regie/foo/credit/%s/assign/' % credit.uuid, status=404)

    # pool not finalized
    credit_line.quantity = 42
    credit_line.save()
    credit.pool = pool
    credit.save()
    app.post('/api/regie/foo/credit/%s/assign/' % credit.uuid, status=404)

    # not publicated
    credit.pool = None
    credit.date_publication = (now() + datetime.timedelta(days=1)).date()
    credit.save()
    app.post('/api/regie/foo/credit/%s/assign/' % credit.uuid, status=404)

    # cancelled
    credit.date_publication = datetime.date(2022, 10, 1)
    credit.cancelled_at = now()
    credit.save()
    app.post('/api/regie/foo/credit/%s/assign/' % credit.uuid, status=404)

    # not usable
    credit.cancelled_at = None
    credit.usable = False
    credit.save()
    app.post('/api/regie/foo/credit/%s/assign/' % credit.uuid, status=404)

    credit.usable = True
    credit.save()
    with mock.patch('lingo.utils.requests_wrapper.RequestsSession.send') as mock_send:
        app.post('/api/regie/foo/credit/%s/assign/' % credit.uuid)
    assert [x[0][0].url for x in mock_send.call_args_list] == []

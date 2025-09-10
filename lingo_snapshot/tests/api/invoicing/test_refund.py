import datetime
import uuid
from unittest import mock

import pytest
from django.utils.timezone import now

from lingo.invoicing.errors import PayerError
from lingo.invoicing.models import Campaign, Credit, CreditAssignment, CreditLine, Pool, Refund, Regie

pytestmark = pytest.mark.django_db


def test_add_refund(app, user):
    app.post('/api/regie/foo/refunds/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.post('/api/regie/foo/refunds/', status=404)

    regie = Regie.objects.create(slug='foo')
    other_regie = Regie.objects.create(slug='bar')
    resp = app.post('/api/regie/foo/refunds/', status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'credit': ['This field is required.'],
    }

    params = {
        'credit': str(uuid.uuid4()),
    }
    resp = app.post('/api/regie/foo/refunds/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {'credit': ['Unknown credit.']}

    credit = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
        payer_email='email1',
        payer_phone='phone1',
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

    # not the same regie
    other_credit = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=other_regie,
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Name1',
        payer_address='41 rue des kangourous\n99999 Kangourou Ville',
    )
    CreditLine.objects.create(
        credit=other_credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=3,
        unit_amount=1,
    )
    params = {
        'credit': str(other_credit.uuid),
    }
    resp = app.post('/api/regie/foo/refunds/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {'credit': ['Unknown credit.']}
    other_credit = Credit.objects.create(
        date_publication=now().date() + datetime.timedelta(days=1),  # not published
        regie=regie,
        payer_external_id='payer:1',
    )
    CreditLine.objects.create(
        credit=other_credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=3,
        unit_amount=1,
    )
    params = {
        'credit': str(other_credit.uuid),
    }
    resp = app.post('/api/regie/foo/refunds/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {'credit': ['Unknown credit.']}
    other_credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        cancelled_at=now(),  # cancelled
    )
    CreditLine.objects.create(
        credit=other_credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=3,
        unit_amount=1,
    )
    params = {
        'credit': str(other_credit.uuid),
    }
    resp = app.post('/api/regie/foo/refunds/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {'credit': ['Unknown credit.']}
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
    other_credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        pool=pool,  # not finalized pool
    )
    CreditLine.objects.create(
        credit=other_credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=3,
        unit_amount=1,
    )
    params = {
        'credit': str(other_credit.uuid),
    }
    resp = app.post('/api/regie/foo/refunds/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {'credit': ['Unknown credit.']}

    params = {
        'credit': str(credit.uuid),
    }
    resp = app.post('/api/regie/foo/refunds/', params=params)
    assert Refund.objects.count() == 1
    assert CreditAssignment.objects.count() == 1
    refund = Refund.objects.latest('pk')
    assert resp.json['data'] == {
        'refund_id': str(refund.uuid),
        'urls': {
            'refund_in_backoffice': 'http://testserver/manage/invoicing/redirect/refund/%s/' % refund.uuid
        },
    }
    assert refund.regie == regie
    assert refund.amount == 42
    assert refund.number == 1
    assert refund.date_refund is None
    assert refund.formatted_number == 'V%02d-%s-0000001' % (
        regie.pk,
        now().strftime('%y-%m'),
    )
    assert refund.payer_external_id == 'payer:1'
    assert refund.payer_first_name == 'First1'
    assert refund.payer_last_name == 'Name1'
    assert refund.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert refund.payer_email == 'email1'
    assert refund.payer_phone == 'phone1'
    assignment = CreditAssignment.objects.latest('pk')
    assert assignment.credit == credit
    assert assignment.refund == refund
    assert assignment.invoice is None
    assert assignment.payment is None
    assert assignment.amount == 42
    credit.refresh_from_db()
    assert credit.total_amount == 42
    assert credit.assigned_amount == 42
    assert credit.remaining_amount == 0

    # again
    resp = app.post('/api/regie/foo/refunds/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {'credit': ['Credit already completely assigned.']}

    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit,
        quantity=13,
        unit_amount=1,
        label='Label 11',
        user_external_id='user:1',
        user_first_name='User1',
        user_last_name='Name1',
    )
    credit.refresh_from_db()
    campaign.finalized = True
    campaign.save()
    credit.pool = pool
    credit.save()
    assert credit.total_amount == 55
    assert credit.assigned_amount == 42
    assert credit.remaining_amount == 13
    params['date_refund'] = '2022-11-06'
    resp = app.post('/api/regie/foo/refunds/', params=params)
    assert Refund.objects.count() == 2
    assert CreditAssignment.objects.count() == 2
    refund = Refund.objects.latest('pk')
    assert refund.regie == regie
    assert refund.amount == 13
    assert refund.number == 1
    assert refund.date_refund == datetime.date(2022, 11, 6)
    assert refund.formatted_number == 'V%02d-22-11-0000001' % regie.pk
    assert refund.payer_external_id == 'payer:1'
    assert refund.payer_first_name == 'First1'
    assert refund.payer_last_name == 'Name1'
    assert refund.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert refund.payer_email == 'email1'
    assert refund.payer_phone == 'phone1'
    assignment = CreditAssignment.objects.latest('pk')
    assert assignment.credit == credit
    assert assignment.refund == refund
    assert assignment.invoice is None
    assert assignment.payment is None
    assert assignment.amount == 13
    credit.refresh_from_db()
    assert credit.total_amount == 55
    assert credit.assigned_amount == 55
    assert credit.remaining_amount == 0


@mock.patch.object(Regie, 'get_payer_external_id_from_nameid', autospec=True)
def test_list_refunds(mock_payer, app, user):
    app.get('/api/regie/foo/refunds/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/refunds/', status=404)

    regie = Regie.objects.create(label='Foo')
    app.get('/api/regie/foo/refunds/', status=404)

    refund = Refund.objects.create(
        regie=regie,
        amount=42,
        payer_external_id='payer:1',
    )
    refund.set_number()
    refund.save()

    mock_payer.return_value = 'payer:1'
    resp = app.get('/api/regie/foo/refunds/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'id': str(refund.uuid),
            'display_id': 'V%02d-%s-0000001'
            % (
                regie.pk,
                refund.created_at.strftime('%y-%m'),
            ),
            'amount': 42,
            'created': now().date().isoformat(),
            'has_pdf': False,
        }
    ]
    assert mock_payer.call_args_list == [mock.call(regie, mock.ANY, 'foobar')]
    refund.date_refund = datetime.date(2022, 9, 1)
    refund.save()
    resp = app.get('/api/regie/foo/refunds/', params={'NameID': 'foobar'})
    assert resp.json['data'][0]['created'] == '2022-09-01'
    assert 'real_created' not in resp.json['data'][0]
    refund.date_refund = None
    refund.save()

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    refund.regie = other_regie
    refund.save()
    resp = app.get('/api/regie/foo/refunds/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # no matching payer id
    refund.regie = regie
    refund.save()
    mock_payer.return_value = 'payer:unknown'
    resp = app.get('/api/regie/foo/refunds/', params={'NameID': 'foobar'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    # payer error
    mock_payer.side_effect = PayerError
    app.get('/api/regie/foo/refunds/', params={'NameID': 'foobar'}, status=404)


def test_list_refunds_for_payer(app, user):
    app.get('/api/regie/foo/refunds/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/foo/refunds/', status=404)

    regie = Regie.objects.create(label='Foo')
    app.get('/api/regie/foo/refunds/', status=404)

    resp = app.get('/api/regie/foo/refunds/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    refund = Refund.objects.create(
        regie=regie,
        amount=42,
        payer_external_id='payer:1',
    )
    refund.set_number()
    refund.save()

    resp = app.get('/api/regie/foo/refunds/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == [
        {
            'id': str(refund.uuid),
            'display_id': 'V%02d-%s-0000001'
            % (
                regie.pk,
                refund.created_at.strftime('%y-%m'),
            ),
            'amount': 42,
            'created': now().date().isoformat(),
            'has_pdf': False,
        }
    ]
    refund.date_refund = datetime.date(2022, 9, 1)
    refund.save()
    resp = app.get('/api/regie/foo/refunds/', params={'payer_external_id': 'payer:1'})
    assert resp.json['data'][0]['created'] == '2022-09-01'
    assert resp.json['data'][0]['real_created'] == now().date().isoformat()
    refund.date_refund = None
    refund.save()

    # other regie
    other_regie = Regie.objects.create(label='Other Foo')
    refund.regie = other_regie
    refund.save()
    resp = app.get('/api/regie/foo/refunds/', params={'payer_external_id': 'payer:1'})
    assert resp.json['err'] == 0
    assert resp.json['data'] == []

    refund.regie = regie
    refund.save()

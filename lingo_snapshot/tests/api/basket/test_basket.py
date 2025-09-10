import datetime
import decimal
import uuid

import pytest
from django.utils.timezone import now

from lingo.basket.models import Basket, BasketLine, BasketLineItem
from lingo.invoicing.models import DraftInvoice, Regie

pytestmark = pytest.mark.django_db


def test_add_basket(settings, app, user):
    app.post('/api/regie/foo/baskets/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    resp = app.post('/api/regie/foo/baskets/', status=404)

    regie = Regie.objects.create(label='Foo')
    resp = app.post('/api/regie/foo/baskets/', status=400)

    assert resp.json['err']
    assert resp.json['errors'] == {
        'payer_nameid': ['This field is required.'],
        'payer_external_id': ['This field is required.'],
        'payer_first_name': ['This field is required.'],
        'payer_last_name': ['This field is required.'],
        'payer_address': ['This field is required.'],
    }

    params = {
        'payer_nameid': 'uuid1',
        'payer_external_id': 'payer:1',
        'payer_first_name': 'First',
        'payer_last_name': 'Last',
        'payer_address': '41 rue des kangourous\n99999 Kangourou Ville',
        'payer_email': 'email1',
        'payer_phone': 'phone1',
    }
    resp = app.post('/api/regie/foo/baskets/', params=params)
    assert Basket.objects.count() == 1
    basket = Basket.objects.latest('pk')
    assert resp.json == {'err': 0, 'data': {'basket_id': str(basket.uuid)}}
    assert basket.regie == regie
    assert basket.payer_nameid == 'uuid1'
    assert basket.payer_external_id == 'payer:1'
    assert basket.payer_first_name == 'First'
    assert basket.payer_last_name == 'Last'
    assert basket.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert basket.payer_email == 'email1'
    assert basket.payer_phone == 'phone1'
    assert basket.status == 'open'
    assert basket.validated_at is None
    assert basket.paid_at is None
    assert basket.completed_at is None
    assert basket.cancelled_at is None
    assert basket.expired_at is None
    assert basket.created_at is not None
    assert basket.expiry_at is not None
    assert (basket.expiry_at - basket.created_at) < datetime.timedelta(minutes=60)
    assert basket.expiry_at - basket.created_at > datetime.timedelta(minutes=59)
    assert DraftInvoice.objects.count() == 1
    invoice = DraftInvoice.objects.latest('pk')
    assert invoice.label == 'Invoice from %s' % now().strftime('%d/%m/%Y')
    assert invoice.total_amount == 0
    assert invoice.date_publication == now().date()
    assert invoice.date_payment_deadline == now().date() + datetime.timedelta(days=1)
    assert invoice.date_due == now().date() + datetime.timedelta(days=1)
    assert invoice.date_debit is None
    assert invoice.regie == regie
    assert invoice.payer_external_id == 'payer:1'
    assert invoice.payer_first_name == 'First'
    assert invoice.payer_last_name == 'Last'
    assert invoice.payer_address == '41 rue des kangourous\n99999 Kangourou Ville'
    assert invoice.payer_email == 'email1'
    assert invoice.payer_phone == 'phone1'
    assert invoice.payer_direct_debit is False
    assert invoice.pool is None
    assert invoice.origin == 'basket'
    assert invoice.lines.count() == 0

    # basket already open
    resp = app.post('/api/regie/foo/baskets/', params=params)
    assert resp.json == {'err': 0, 'data': {'basket_id': str(basket.uuid)}}
    assert Basket.objects.count() == 1
    assert DraftInvoice.objects.count() == 1

    # basket to be paid, cannot open another one
    basket.status = 'tobepaid'
    basket.save()
    resp = app.post('/api/regie/foo/baskets/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {'payer_nameid': ['a basket to finalize already exists']}

    # basket completed, cancelled or expired, ok to open another one
    for status in ['completed', 'cancelled', 'expired']:
        basket.status = status
        basket.save()
        resp = app.post('/api/regie/foo/baskets/', params=params)
        assert resp.json['err'] == 0
        assert resp.json['data']['basket_id'] != basket.uuid
        Basket.objects.filter(uuid=resp.json['data']['basket_id']).delete()

    # basket already open in another regie, cannot open another one
    basket.delete()
    other_regie = Regie.objects.create(label='Other')
    invoice = DraftInvoice.objects.create(
        regie=other_regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(regie=other_regie, draft_invoice=invoice, payer_nameid='uuid1')
    resp = app.post('/api/regie/foo/baskets/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {'payer_nameid': ['a basket to finalize already exists']}
    basket.status = 'tobepaid'
    basket.save()
    resp = app.post('/api/regie/foo/baskets/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {'payer_nameid': ['a basket to finalize already exists']}
    settings.BASKET_EXPIRY_DELAY = 30
    # basket completed, cancelled or expired in another regie, ok to open another one
    for status in ['completed', 'cancelled', 'expired']:
        basket.status = status
        basket.save()
        resp = app.post('/api/regie/foo/baskets/', params=params)
        assert resp.json['err'] == 0
        assert resp.json['data']['basket_id'] != basket.uuid
        new_basket = Basket.objects.latest('pk')
        assert new_basket.expiry_at is not None
        assert (new_basket.expiry_at - new_basket.created_at) < datetime.timedelta(minutes=30)
        assert new_basket.expiry_at - new_basket.created_at > datetime.timedelta(minutes=29)
        Basket.objects.filter(uuid=resp.json['data']['basket_id']).delete()


def test_line_available(app, user):
    app.get('/api/regie/foo/basket/check/', status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.get('/api/regie/bar/basket/check/', status=404)
    regie = Regie.objects.create(label='Foo')
    resp = app.get('/api/regie/foo/basket/check/', status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'payer_nameid': ['This field is required.'],
        'user_external_id': ['This field is required.'],
    }

    params = {
        'payer_nameid': 'uuid1',
        'user_external_id': 'user:1',
    }
    resp = app.get('/api/regie/foo/basket/check/', params=params)
    assert resp.json['err'] == 0

    # existing basket for other payer_nameid
    invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(regie=regie, draft_invoice=invoice, payer_nameid='uuid2')
    BasketLine.objects.create(basket=basket, user_external_id='user:1')
    resp = app.get('/api/regie/foo/basket/check/', params=params)
    assert resp.json['err'] == 0

    # existing basket for user, but no line
    invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(regie=regie, draft_invoice=invoice, payer_nameid='uuid1')
    resp = app.get('/api/regie/foo/basket/check/', params=params)
    assert resp.json['err'] == 0

    # existing line for another user
    BasketLine.objects.create(basket=basket, user_external_id='user:42')
    resp = app.get('/api/regie/foo/basket/check/', params=params)
    assert resp.json['err'] == 0

    # basket is to be paid, with a line for another user
    basket.status = 'tobepaid'
    basket.save()
    resp = app.get('/api/regie/foo/basket/check/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['err_class'] == 'payer_active_basket_to_pay'
    assert resp.json['errors'] == {'payer_nameid': ['a basket to pay already exists']}

    # existing line for this user
    basket.status = 'open'
    basket.save()
    line = BasketLine.objects.create(basket=basket, user_external_id='user:1')
    resp = app.get('/api/regie/foo/basket/check/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['err_class'] == 'user_existing_line'
    assert resp.json['errors'] == {
        'user_external_id': ['a line already exists in active basket in this regie for this user_external_id']
    }
    line.closed = True
    line.save()
    resp = app.get('/api/regie/foo/basket/check/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['err_class'] == 'user_existing_line'
    assert resp.json['errors'] == {
        'user_external_id': ['a line already exists in active basket in this regie for this user_external_id']
    }

    # check basket status
    for status in ['completed', 'cancelled', 'expired']:
        basket.status = status
        basket.save()
        resp = app.get('/api/regie/foo/basket/check/', params=params)
        assert resp.json['err'] == 0

    for status in ['open', 'tobepaid']:
        basket.status = status
        basket.save()
        resp = app.get('/api/regie/foo/basket/check/', params=params, status=400)
        assert resp.json['err']
        assert resp.json['err_class'] == 'user_existing_line'
        assert resp.json['errors'] == {
            'user_external_id': [
                'a line already exists in active basket in this regie for this user_external_id'
            ]
        }

    # basket already open in another regie
    BasketLine.objects.all().delete()
    basket.delete()
    other_regie = Regie.objects.create(label='Other')
    invoice = DraftInvoice.objects.create(
        regie=other_regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(regie=other_regie, draft_invoice=invoice, payer_nameid='uuid1')
    resp = app.get('/api/regie/foo/basket/check/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['err_class'] == 'payer_active_basket'
    assert resp.json['errors'] == {'payer_nameid': ['a basket to finalize already exists in another regie']}
    basket.status = 'tobepaid'
    basket.save()
    resp = app.get('/api/regie/foo/basket/check/', params=params, status=400)
    assert resp.json['err']
    assert resp.json['err_class'] == 'payer_active_basket'
    assert resp.json['errors'] == {'payer_nameid': ['a basket to finalize already exists in another regie']}
    # basket completed, cancelled or expired in another regie, ok to open another one
    for status in ['completed', 'cancelled', 'expired']:
        basket.status = status
        basket.save()
        resp = app.get('/api/regie/foo/basket/check/', params=params)
        assert resp.json['err'] == 0


def test_add_line(app, user):
    app.post('/api/regie/foo/basket/%s/lines/' % uuid.uuid4(), status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.post('/api/regie/foo/basket/%s/lines/' % uuid.uuid4(), status=404)

    regie = Regie.objects.create(label='Foo')
    app.post('/api/regie/foo/basket/%s/lines/' % uuid.uuid4(), status=404)

    invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(regie=regie, draft_invoice=invoice)
    old_expiry_at = basket.expiry_at
    app.post('/api/regie/bar/basket/%s/lines/' % basket.uuid, status=404)
    resp = app.post('/api/regie/foo/basket/%s/lines/' % basket.uuid, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'user_external_id': ['This field is required.'],
        'user_first_name': ['This field is required.'],
        'user_last_name': ['This field is required.'],
    }

    params = {
        'user_external_id': 'user:1',
        'user_first_name': 'First1',
        'user_last_name': 'Last1',
        'information_message': 'foo baz',
        'cancel_information_message': 'foo bar',
        'form_url': 'http://form.com',
        'validation_callback_url': 'http://validation.com',
        'payment_callback_url': 'http://payment.com',
        'credit_callback_url': 'http://credit.com',
        'cancel_callback_url': 'http://cancel.com',
        'expiration_callback_url': 'http://expiration.com',
    }
    resp = app.post_json('/api/regie/foo/basket/%s/lines/' % basket.uuid, params=params)
    assert BasketLine.objects.count() == 1
    line = BasketLine.objects.latest('pk')
    assert resp.json == {'err': 0, 'data': {'line_id': str(line.uuid), 'closed': False}}
    assert line.basket == basket
    assert line.user_external_id == 'user:1'
    assert line.user_first_name == 'First1'
    assert line.user_last_name == 'Last1'
    assert line.information_message == 'foo baz'
    assert line.cancel_information_message == 'foo bar'
    assert line.group_items is False
    assert line.form_url == 'http://form.com'
    assert line.validation_callback_url == 'http://validation.com'
    assert line.payment_callback_url == 'http://payment.com'
    assert line.credit_callback_url == 'http://credit.com'
    assert line.cancel_callback_url == 'http://cancel.com'
    assert line.expiration_callback_url == 'http://expiration.com'
    assert line.closed is False
    basket.refresh_from_db()
    assert basket.expiry_at == old_expiry_at
    assert invoice.lines.count() == 0

    # again, a line already exists for this user
    resp = app.post_json('/api/regie/foo/basket/%s/lines/' % basket.uuid, params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'user_external_id': ['a line is already opened in basket for this user_external_id']
    }
    # not ok with param reuse=True, when line is not closed
    params['reuse'] = True
    resp = app.post_json('/api/regie/foo/basket/%s/lines/' % basket.uuid, params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'user_external_id': ['a line is already opened in basket for this user_external_id']
    }
    # even if line is closed
    params['reuse'] = False
    line.closed = True
    line.save()
    resp = app.post_json('/api/regie/foo/basket/%s/lines/' % basket.uuid, params=params, status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'user_external_id': ['a line is already opened in basket for this user_external_id']
    }
    # but ok with param reuse=True
    params['reuse'] = True
    resp = app.post_json('/api/regie/foo/basket/%s/lines/' % basket.uuid, params=params)
    assert BasketLine.objects.count() == 1
    assert resp.json == {'err': 0, 'data': {'line_id': str(line.uuid), 'closed': False}}
    line.refresh_from_db()
    assert line.closed is False

    # ok for another user
    params = {
        'user_external_id': 'user:2',
        'user_first_name': 'First2',
        'user_last_name': 'Last2',
        'group_items': True,
    }
    resp = app.post('/api/regie/foo/basket/%s/lines/' % basket.uuid, params=params)
    assert BasketLine.objects.count() == 2
    line = BasketLine.objects.latest('pk')
    assert resp.json == {'err': 0, 'data': {'line_id': str(line.uuid), 'closed': False}}
    assert line.basket == basket
    assert line.user_external_id == 'user:2'
    assert line.user_first_name == 'First2'
    assert line.user_last_name == 'Last2'
    assert line.information_message == ''
    assert line.cancel_information_message == ''
    assert line.group_items is True
    assert line.form_url == ''
    assert line.validation_callback_url == ''
    assert line.payment_callback_url == ''
    assert line.credit_callback_url == ''
    assert line.cancel_callback_url == ''
    assert line.expiration_callback_url == ''
    assert line.closed is False

    # basket wrong status
    for status in ['tobepaid', 'completed', 'cancelled', 'expired']:
        basket.status = status
        basket.save()
        app.post('/api/regie/foo/basket/%s/lines/' % basket.uuid, status=404)

    # no existing line for the user
    basket.status = 'open'
    basket.save()
    BasketLine.objects.all().delete()
    params['reuse'] = True
    resp = app.post_json('/api/regie/foo/basket/%s/lines/' % basket.uuid, params=params)
    assert BasketLine.objects.count() == 1
    line = BasketLine.objects.get()
    assert resp.json == {'err': 0, 'data': {'line_id': str(line.uuid), 'closed': False}}


def test_add_item(app, user):
    app.post('/api/regie/foo/basket/%s/line/%s/items/' % (uuid.uuid4(), uuid.uuid4()), status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.post('/api/regie/foo/basket/%s/line/%s/items/' % (uuid.uuid4(), uuid.uuid4()), status=404)

    regie = Regie.objects.create(label='Foo')
    app.post('/api/regie/foo/basket/%s/line/%s/items/' % (uuid.uuid4(), uuid.uuid4()), status=404)

    invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(regie=regie, draft_invoice=invoice)
    old_expiry_at = basket.expiry_at
    app.post('/api/regie/foo/basket/%s/line/%s/items/' % (basket.uuid, uuid.uuid4()), status=404)

    line = BasketLine.objects.create(basket=basket)
    app.post('/api/regie/bar/basket/%s/line/%s/items/' % (basket.uuid, line.uuid), status=404)
    app.post('/api/regie/foo/basket/%s/line/%s/items/' % (uuid.uuid4(), line.uuid), status=404)
    resp = app.post('/api/regie/foo/basket/%s/line/%s/items/' % (basket.uuid, line.uuid), status=400)
    assert resp.json['err']
    assert resp.json['errors'] == {
        'event_date': ['This field is required.'],
        'label': ['This field is required.'],
        'quantity': ['This field is required.'],
        'unit_amount': ['This field is required.'],
    }

    params = {
        'event_date': '2023-11-06',
        'label': 'Repas',
        'subject': 'Réservation',
        'details': 'Lun 06/11, Mar 07/11',
        'quantity': 2,
        'unit_amount': 3,
    }
    resp = app.post('/api/regie/foo/basket/%s/line/%s/items/' % (basket.uuid, line.uuid), params=params)
    assert BasketLineItem.objects.count() == 1
    item = BasketLineItem.objects.latest('pk')
    assert resp.json == {'err': 0, 'data': {'item_id': str(item.uuid)}}
    assert item.line == line
    assert item.event_date == datetime.date(2023, 11, 6)
    assert item.label == 'Repas'
    assert item.subject == 'Réservation'
    assert item.details == 'Lun 06/11, Mar 07/11'
    assert item.quantity == 2
    assert item.unit_amount == 3
    assert item.event_slug == ''
    assert item.event_label == ''
    assert item.agenda_slug == ''
    assert item.activity_label == ''
    assert item.accounting_code == ''
    basket.refresh_from_db()
    assert basket.expiry_at == old_expiry_at
    assert invoice.lines.count() == 0

    # it's ok to add exactly the same item
    resp = app.post('/api/regie/foo/basket/%s/line/%s/items/' % (basket.uuid, line.uuid), params=params)
    assert BasketLineItem.objects.count() == 2
    item = BasketLineItem.objects.latest('pk')
    assert resp.json == {'err': 0, 'data': {'item_id': str(item.uuid)}}
    assert item.line == line
    assert item.event_date == datetime.date(2023, 11, 6)
    assert item.label == 'Repas'
    assert item.subject == 'Réservation'
    assert item.details == 'Lun 06/11, Mar 07/11'
    assert item.quantity == 2
    assert item.unit_amount == 3
    assert item.event_slug == ''
    assert item.event_label == ''
    assert item.agenda_slug == ''
    assert item.activity_label == ''
    assert item.accounting_code == ''

    # add a negative item
    params = {
        'event_date': '2023-11-09',
        'label': 'Repas',
        'subject': 'Annulation',
        'details': 'Jeu 09/11',
        'quantity': -1,
        'unit_amount': 3,
        'accounting_code': '424242',
        # related to agenda and event
        'slug': 'agenda@bar-foo',
        'activity_label': 'Activity Label !',
    }
    resp = app.post('/api/regie/foo/basket/%s/line/%s/items/' % (basket.uuid, line.uuid), params=params)
    assert BasketLineItem.objects.count() == 3
    item = BasketLineItem.objects.latest('pk')
    assert resp.json == {'err': 0, 'data': {'item_id': str(item.uuid)}}
    assert item.line == line
    assert item.event_date == datetime.date(2023, 11, 9)
    assert item.label == 'Repas'
    assert item.subject == 'Annulation'
    assert item.details == 'Jeu 09/11'
    assert item.quantity == -1
    assert item.unit_amount == 3
    assert item.event_slug == 'agenda@bar-foo'
    assert item.event_label == 'Repas'
    assert item.agenda_slug == 'agenda'
    assert item.activity_label == 'Activity Label !'
    assert item.accounting_code == '424242'


def test_close_line(app, user):
    app.post('/api/regie/foo/basket/%s/line/%s/close/' % (uuid.uuid4(), uuid.uuid4()), status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.post('/api/regie/foo/basket/%s/line/%s/close/' % (uuid.uuid4(), uuid.uuid4()), status=404)

    regie = Regie.objects.create(label='Foo')
    app.post('/api/regie/foo/basket/%s/line/%s/close/' % (uuid.uuid4(), uuid.uuid4()), status=404)

    invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(regie=regie, draft_invoice=invoice)
    old_expiry_at = basket.expiry_at
    app.post('/api/regie/foo/basket/%s/line/%s/close/' % (basket.uuid, uuid.uuid4()), status=404)

    line = BasketLine.objects.create(
        basket=basket,
        group_items=False,
        user_external_id='user:1',
        user_first_name='First1',
        user_last_name='Last1',
        form_url='http://form.com',
    )
    app.post('/api/regie/bar/basket/%s/line/%s/close/' % (basket.uuid, line.uuid), status=404)
    app.post('/api/regie/foo/basket/%s/line/%s/close/' % (uuid.uuid4(), line.uuid), status=404)
    resp = app.post('/api/regie/foo/basket/%s/line/%s/close/' % (basket.uuid, line.uuid))
    assert resp.json == {'err': 0, 'data': {'line_id': str(line.uuid), 'closed': True}}
    line.refresh_from_db()
    assert line.closed is True
    basket.refresh_from_db()
    assert basket.expiry_at > old_expiry_at
    assert invoice.lines.count() == 0

    # again, but already closed
    app.post('/api/regie/foo/basket/%s/line/%s/close/' % (basket.uuid, line.uuid), status=404)

    # now, with items
    line.closed = False
    line.save()
    BasketLineItem.objects.create(
        line=line,
        event_date=datetime.date(2023, 11, 6),
        label='Repas',
        subject='Réservation',
        details='Lun 6/11',
        quantity=2,
        unit_amount=3,
        event_slug='agenda@repas',
        event_label='Repas',
        agenda_slug='agenda',
        activity_label='Activity Label !',
        accounting_code='424242',
    )
    BasketLineItem.objects.create(
        line=line,
        event_date=datetime.date(2023, 11, 9),
        label='Repas',
        subject='Annulation',
        details='Jeu 9/11',
        quantity=-1,
        unit_amount=3,
        event_slug='agenda@repas',
        event_label='Repas',
        agenda_slug='agenda',
        activity_label='Activity Label !',
        accounting_code='424242',
    )
    BasketLineItem.objects.create(
        line=line,
        event_date=datetime.date(2023, 11, 13),
        label='Repas',
        subject='Réservation',
        details='Lun 13/11',
        quantity=1,
        unit_amount=3,
        event_slug='agenda@repas',
        event_label='Repas',
        agenda_slug='agenda',
        activity_label='Activity Label !',
        accounting_code='424242',
    )
    basket_item = BasketLineItem.objects.create(
        line=line,
        event_date=datetime.date(2023, 11, 16),
        label='Repas',
        subject='Annulation',
        details='Jeu 16/11',
        quantity=-1,
        unit_amount=3,
        event_slug='agenda@repas',
        event_label='Repas',
        agenda_slug='agenda',
        activity_label='Activity Label !',
        accounting_code='424242',
    )
    BasketLineItem.objects.create(
        line=line,
        event_date=datetime.date(2023, 11, 8),
        label='Mercredi',
        subject='Réservation',
        details='Mer 8/11',
        quantity=1,
        unit_amount=5,
        event_slug='agenda@mercredi',
        event_label='Mercredi',
        agenda_slug='agenda',
        activity_label='Activity Label !',
        accounting_code='424243',
    )
    BasketLineItem.objects.create(
        line=line,
        event_date=datetime.date(2023, 11, 15),
        label='Mercredi',
        subject='Annulation',
        details='Mer 15/11',
        quantity=-0.5,  # for decimal testing
        unit_amount=5,
        event_slug='agenda@mercredi',
        event_label='Mercredi',
        agenda_slug='agenda',
        activity_label='Activity Label !',
        accounting_code='424243',
    )
    # no grouping
    resp = app.post('/api/regie/foo/basket/%s/line/%s/close/' % (basket.uuid, line.uuid))
    assert resp.json == {'err': 0, 'data': {'line_id': str(line.uuid), 'closed': True}}
    line.refresh_from_db()
    assert line.closed is True
    basket.refresh_from_db()
    assert invoice.lines.count() == 6
    line1, line2, line3, line4, line5, line6 = invoice.lines.all().order_by('pk')
    assert line1.event_date == datetime.date(2023, 11, 15)
    assert line1.event_slug == 'agenda@mercredi'
    assert line1.event_label == 'Mercredi'
    assert line1.agenda_slug == 'agenda'
    assert line1.activity_label == 'Activity Label !'
    assert line1.label == 'Mercredi'
    assert line1.description == 'Annulation Mer 15/11'
    assert line1.quantity == -decimal.Decimal('0.5')
    assert line1.unit_amount == 5
    assert line1.total_amount == -decimal.Decimal('2.5')
    assert line1.accounting_code == '424243'
    assert line1.user_external_id == 'user:1'
    assert line1.user_first_name == 'First1'
    assert line1.user_last_name == 'Last1'
    assert line1.details == {'dates': ['2023-11-15']}
    assert line1.form_url == 'http://form.com'
    assert line1.pool is None
    assert line2.event_date == datetime.date(2023, 11, 8)
    assert line2.event_slug == 'agenda@mercredi'
    assert line2.event_label == 'Mercredi'
    assert line2.agenda_slug == 'agenda'
    assert line2.activity_label == 'Activity Label !'
    assert line2.label == 'Mercredi'
    assert line2.description == 'Réservation Mer 8/11'
    assert line2.quantity == 1
    assert line2.unit_amount == 5
    assert line2.total_amount == 5
    assert line2.accounting_code == '424243'
    assert line2.user_external_id == 'user:1'
    assert line2.user_first_name == 'First1'
    assert line2.user_last_name == 'Last1'
    assert line2.form_url == 'http://form.com'
    assert line2.details == {'dates': ['2023-11-08']}
    assert line3.event_date == datetime.date(2023, 11, 16)
    assert line3.event_slug == 'agenda@repas'
    assert line3.event_label == 'Repas'
    assert line3.agenda_slug == 'agenda'
    assert line3.activity_label == 'Activity Label !'
    assert line3.label == 'Repas'
    assert line3.description == 'Annulation Jeu 16/11'
    assert line3.quantity == -1
    assert line3.unit_amount == 3
    assert line3.total_amount == -3
    assert line3.accounting_code == '424242'
    assert line3.user_external_id == 'user:1'
    assert line3.user_first_name == 'First1'
    assert line3.user_last_name == 'Last1'
    assert line3.form_url == 'http://form.com'
    assert line3.details == {'dates': ['2023-11-16']}
    assert line4.event_date == datetime.date(2023, 11, 9)
    assert line4.event_slug == 'agenda@repas'
    assert line4.event_label == 'Repas'
    assert line4.agenda_slug == 'agenda'
    assert line4.activity_label == 'Activity Label !'
    assert line4.label == 'Repas'
    assert line4.description == 'Annulation Jeu 9/11'
    assert line4.quantity == -1
    assert line4.unit_amount == 3
    assert line4.total_amount == -3
    assert line4.accounting_code == '424242'
    assert line4.user_external_id == 'user:1'
    assert line4.user_first_name == 'First1'
    assert line4.user_last_name == 'Last1'
    assert line4.form_url == 'http://form.com'
    assert line4.details == {'dates': ['2023-11-09']}
    assert line5.event_date == datetime.date(2023, 11, 13)
    assert line5.event_slug == 'agenda@repas'
    assert line5.event_label == 'Repas'
    assert line5.agenda_slug == 'agenda'
    assert line5.activity_label == 'Activity Label !'
    assert line5.label == 'Repas'
    assert line5.description == 'Réservation Lun 13/11'
    assert line5.quantity == 1
    assert line5.unit_amount == 3
    assert line5.total_amount == 3
    assert line5.accounting_code == '424242'
    assert line5.user_external_id == 'user:1'
    assert line5.user_first_name == 'First1'
    assert line5.user_last_name == 'Last1'
    assert line5.form_url == 'http://form.com'
    assert line5.details == {'dates': ['2023-11-13']}
    assert line6.event_date == datetime.date(2023, 11, 6)
    assert line6.event_slug == 'agenda@repas'
    assert line6.event_label == 'Repas'
    assert line6.agenda_slug == 'agenda'
    assert line6.activity_label == 'Activity Label !'
    assert line6.label == 'Repas'
    assert line6.description == 'Réservation Lun 6/11'
    assert line6.quantity == 2
    assert line6.unit_amount == 3
    assert line6.total_amount == 6
    assert line6.accounting_code == '424242'
    assert line6.user_external_id == 'user:1'
    assert line6.user_first_name == 'First1'
    assert line6.user_last_name == 'Last1'
    assert line6.form_url == 'http://form.com'
    assert line6.details == {'dates': ['2023-11-06']}

    # and with grouping
    line.group_items = True
    line.closed = False
    line.save()
    invoice.lines.all().delete()
    resp = app.post('/api/regie/foo/basket/%s/line/%s/close/' % (basket.uuid, line.uuid))
    assert resp.json == {'err': 0, 'data': {'line_id': str(line.uuid), 'closed': True}}
    line.refresh_from_db()
    assert line.closed is True
    basket.refresh_from_db()
    assert invoice.lines.count() == 4
    line1, line2, line3, line4 = invoice.lines.all().order_by('pk')
    assert line1.event_date == datetime.date(2023, 11, 15)
    assert line1.event_slug == 'agenda@mercredi'
    assert line1.event_label == 'Mercredi'
    assert line1.agenda_slug == 'agenda'
    assert line1.activity_label == 'Activity Label !'
    assert line1.label == 'Mercredi'
    assert line1.description == 'Annulation Mer 15/11'
    assert line1.quantity == -decimal.Decimal('0.5')
    assert line1.unit_amount == 5
    assert line1.total_amount == -decimal.Decimal('2.5')
    assert line1.accounting_code == '424243'
    assert line1.user_external_id == 'user:1'
    assert line1.user_first_name == 'First1'
    assert line1.user_last_name == 'Last1'
    assert line1.form_url == 'http://form.com'
    assert line1.details == {'dates': ['2023-11-15']}
    assert line2.event_date == datetime.date(2023, 11, 8)
    assert line2.event_slug == 'agenda@mercredi'
    assert line2.event_label == 'Mercredi'
    assert line2.agenda_slug == 'agenda'
    assert line2.activity_label == 'Activity Label !'
    assert line2.label == 'Mercredi'
    assert line2.description == 'Réservation Mer 8/11'
    assert line2.quantity == 1
    assert line2.unit_amount == 5
    assert line2.total_amount == 5
    assert line2.accounting_code == '424243'
    assert line2.user_external_id == 'user:1'
    assert line2.user_first_name == 'First1'
    assert line2.user_last_name == 'Last1'
    assert line2.form_url == 'http://form.com'
    assert line2.details == {'dates': ['2023-11-08']}
    assert line3.event_date == datetime.date(2023, 11, 9)
    assert line3.event_slug == 'agenda@repas'
    assert line3.event_label == 'Repas'
    assert line3.agenda_slug == 'agenda'
    assert line3.activity_label == 'Activity Label !'
    assert line3.label == 'Repas'
    assert line3.description == 'Annulation Jeu 9/11, Jeu 16/11'
    assert line3.quantity == -2
    assert line3.unit_amount == 3
    assert line3.total_amount == -6
    assert line3.accounting_code == '424242'
    assert line3.user_external_id == 'user:1'
    assert line3.user_first_name == 'First1'
    assert line3.user_last_name == 'Last1'
    assert line3.form_url == 'http://form.com'
    assert line3.details == {'dates': ['2023-11-09', '2023-11-16']}
    assert line4.event_date == datetime.date(2023, 11, 6)
    assert line4.event_slug == 'agenda@repas'
    assert line4.event_label == 'Repas'
    assert line4.agenda_slug == 'agenda'
    assert line4.activity_label == 'Activity Label !'
    assert line4.label == 'Repas'
    assert line4.description == 'Réservation Lun 6/11, Lun 13/11'
    assert line4.quantity == 3
    assert line4.unit_amount == 3
    assert line4.total_amount == 9
    assert line4.accounting_code == '424242'
    assert line4.user_external_id == 'user:1'
    assert line4.user_first_name == 'First1'
    assert line4.user_last_name == 'Last1'
    assert line4.form_url == 'http://form.com'
    assert line4.details == {'dates': ['2023-11-06', '2023-11-13']}

    # grouping but event_slug are not the same
    basket_item.event_slug = 'agenda2@repas2'
    basket_item.event_label = 'Repas2'
    basket_item.agenda_slug = 'agenda2'
    basket_item.activity_label = 'Activity Label !'
    basket_item.save()
    line.closed = False
    line.form_url = ''
    line.save()
    invoice.lines.all().delete()
    resp = app.post('/api/regie/foo/basket/%s/line/%s/close/' % (basket.uuid, line.uuid))
    assert resp.json == {'err': 0, 'data': {'line_id': str(line.uuid), 'closed': True}}
    line.refresh_from_db()
    assert line.closed is True
    basket.refresh_from_db()
    assert invoice.lines.count() == 5
    line1, line2, line3, line4, line5 = invoice.lines.all().order_by('pk')
    assert line1.event_date == datetime.date(2023, 11, 15)
    assert line1.event_slug == 'agenda@mercredi'
    assert line1.event_label == 'Mercredi'
    assert line1.agenda_slug == 'agenda'
    assert line1.activity_label == 'Activity Label !'
    assert line1.label == 'Mercredi'
    assert line1.description == 'Annulation Mer 15/11'
    assert line1.quantity == -decimal.Decimal('0.5')
    assert line1.unit_amount == 5
    assert line1.total_amount == -decimal.Decimal('2.5')
    assert line1.accounting_code == '424243'
    assert line1.user_external_id == 'user:1'
    assert line1.user_first_name == 'First1'
    assert line1.user_last_name == 'Last1'
    assert line1.form_url == ''
    assert line1.details == {'dates': ['2023-11-15']}
    assert line2.event_date == datetime.date(2023, 11, 8)
    assert line2.event_slug == 'agenda@mercredi'
    assert line2.event_label == 'Mercredi'
    assert line2.agenda_slug == 'agenda'
    assert line2.activity_label == 'Activity Label !'
    assert line2.label == 'Mercredi'
    assert line2.description == 'Réservation Mer 8/11'
    assert line2.quantity == 1
    assert line2.unit_amount == 5
    assert line2.total_amount == 5
    assert line2.accounting_code == '424243'
    assert line2.user_external_id == 'user:1'
    assert line2.user_first_name == 'First1'
    assert line2.user_last_name == 'Last1'
    assert line2.form_url == ''
    assert line2.details == {'dates': ['2023-11-08']}
    assert line3.event_date == datetime.date(2023, 11, 16)
    assert line3.event_slug == 'agenda2@repas2'
    assert line3.event_label == 'Repas2'
    assert line3.agenda_slug == 'agenda2'
    assert line3.activity_label == 'Activity Label !'
    assert line3.label == 'Repas'
    assert line3.description == 'Annulation Jeu 16/11'
    assert line3.quantity == -1
    assert line3.unit_amount == 3
    assert line3.total_amount == -3
    assert line3.accounting_code == '424242'
    assert line3.user_external_id == 'user:1'
    assert line3.user_first_name == 'First1'
    assert line3.user_last_name == 'Last1'
    assert line3.form_url == ''
    assert line3.details == {'dates': ['2023-11-16']}
    assert line4.event_date == datetime.date(2023, 11, 9)
    assert line4.event_slug == 'agenda@repas'
    assert line4.event_label == 'Repas'
    assert line4.agenda_slug == 'agenda'
    assert line4.activity_label == 'Activity Label !'
    assert line4.label == 'Repas'
    assert line4.description == 'Annulation Jeu 9/11'
    assert line4.quantity == -1
    assert line4.unit_amount == 3
    assert line4.total_amount == -3
    assert line4.accounting_code == '424242'
    assert line4.user_external_id == 'user:1'
    assert line4.user_first_name == 'First1'
    assert line4.user_last_name == 'Last1'
    assert line4.form_url == ''
    assert line4.details == {'dates': ['2023-11-09']}
    assert line5.event_date == datetime.date(2023, 11, 6)
    assert line5.event_slug == 'agenda@repas'
    assert line5.event_label == 'Repas'
    assert line5.agenda_slug == 'agenda'
    assert line5.activity_label == 'Activity Label !'
    assert line5.label == 'Repas'
    assert line5.description == 'Réservation Lun 6/11, Lun 13/11'
    assert line5.quantity == 3
    assert line5.unit_amount == 3
    assert line5.total_amount == 9
    assert line5.accounting_code == '424242'
    assert line5.user_external_id == 'user:1'
    assert line5.user_first_name == 'First1'
    assert line5.user_last_name == 'Last1'
    assert line5.form_url == ''
    assert line5.details == {'dates': ['2023-11-06', '2023-11-13']}

    # grouping but accounting_code are not the same
    basket_item.event_slug = 'agenda@repas'
    basket_item.event_label = 'Repas'
    basket_item.agenda_slug = 'agenda'
    basket_item.activity_label = 'Activity Label !'
    basket_item.accounting_code = '424244'
    basket_item.save()
    line.closed = False
    line.save()
    invoice.lines.all().delete()
    resp = app.post('/api/regie/foo/basket/%s/line/%s/close/' % (basket.uuid, line.uuid))
    assert resp.json == {'err': 0, 'data': {'line_id': str(line.uuid), 'closed': True}}
    line.refresh_from_db()
    assert line.closed is True
    basket.refresh_from_db()
    assert invoice.lines.count() == 5
    line1, line2, line3, line4, line5 = invoice.lines.all().order_by('pk')
    assert line1.event_date == datetime.date(2023, 11, 15)
    assert line1.event_slug == 'agenda@mercredi'
    assert line1.event_label == 'Mercredi'
    assert line1.agenda_slug == 'agenda'
    assert line1.activity_label == 'Activity Label !'
    assert line1.label == 'Mercredi'
    assert line1.description == 'Annulation Mer 15/11'
    assert line1.quantity == -decimal.Decimal('0.5')
    assert line1.unit_amount == 5
    assert line1.total_amount == -decimal.Decimal('2.5')
    assert line1.accounting_code == '424243'
    assert line1.user_external_id == 'user:1'
    assert line1.user_first_name == 'First1'
    assert line1.user_last_name == 'Last1'
    assert line1.form_url == ''
    assert line1.details == {'dates': ['2023-11-15']}
    assert line2.event_date == datetime.date(2023, 11, 8)
    assert line2.event_slug == 'agenda@mercredi'
    assert line2.event_label == 'Mercredi'
    assert line2.agenda_slug == 'agenda'
    assert line2.activity_label == 'Activity Label !'
    assert line2.label == 'Mercredi'
    assert line2.description == 'Réservation Mer 8/11'
    assert line2.quantity == 1
    assert line2.unit_amount == 5
    assert line2.total_amount == 5
    assert line2.accounting_code == '424243'
    assert line2.user_external_id == 'user:1'
    assert line2.user_first_name == 'First1'
    assert line2.user_last_name == 'Last1'
    assert line2.form_url == ''
    assert line2.details == {'dates': ['2023-11-08']}
    assert line3.event_date == datetime.date(2023, 11, 16)
    assert line3.event_slug == 'agenda@repas'
    assert line3.event_label == 'Repas'
    assert line3.agenda_slug == 'agenda'
    assert line3.activity_label == 'Activity Label !'
    assert line3.label == 'Repas'
    assert line3.description == 'Annulation Jeu 16/11'
    assert line3.quantity == -1
    assert line3.unit_amount == 3
    assert line3.total_amount == -3
    assert line3.accounting_code == '424244'
    assert line3.user_external_id == 'user:1'
    assert line3.user_first_name == 'First1'
    assert line3.user_last_name == 'Last1'
    assert line3.form_url == ''
    assert line3.details == {'dates': ['2023-11-16']}
    assert line4.event_date == datetime.date(2023, 11, 9)
    assert line4.event_slug == 'agenda@repas'
    assert line4.event_label == 'Repas'
    assert line4.agenda_slug == 'agenda'
    assert line4.activity_label == 'Activity Label !'
    assert line4.label == 'Repas'
    assert line4.description == 'Annulation Jeu 9/11'
    assert line4.quantity == -1
    assert line4.unit_amount == 3
    assert line4.total_amount == -3
    assert line4.accounting_code == '424242'
    assert line4.user_external_id == 'user:1'
    assert line4.user_first_name == 'First1'
    assert line4.user_last_name == 'Last1'
    assert line4.form_url == ''
    assert line4.details == {'dates': ['2023-11-09']}
    assert line5.event_date == datetime.date(2023, 11, 6)
    assert line5.event_slug == 'agenda@repas'
    assert line5.event_label == 'Repas'
    assert line5.agenda_slug == 'agenda'
    assert line5.activity_label == 'Activity Label !'
    assert line5.label == 'Repas'
    assert line5.description == 'Réservation Lun 6/11, Lun 13/11'
    assert line5.quantity == 3
    assert line5.unit_amount == 3
    assert line5.total_amount == 9
    assert line5.accounting_code == '424242'
    assert line5.user_external_id == 'user:1'
    assert line5.user_first_name == 'First1'
    assert line5.user_last_name == 'Last1'
    assert line5.form_url == ''
    assert line5.details == {'dates': ['2023-11-06', '2023-11-13']}


def test_cancel_line(app, user):
    app.post('/api/regie/foo/basket/%s/line/%s/cancel/' % (uuid.uuid4(), uuid.uuid4()), status=403)
    app.authorization = ('Basic', ('john.doe', 'password'))

    app.post('/api/regie/foo/basket/%s/line/%s/cancel/' % (uuid.uuid4(), uuid.uuid4()), status=404)

    regie = Regie.objects.create(label='Foo')
    app.post('/api/regie/foo/basket/%s/line/%s/cancel/' % (uuid.uuid4(), uuid.uuid4()), status=404)

    invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(regie=regie, draft_invoice=invoice)
    app.post('/api/regie/foo/basket/%s/line/%s/cancel/' % (basket.uuid, uuid.uuid4()), status=404)

    line = BasketLine.objects.create(
        basket=basket,
        group_items=False,
        user_external_id='user:1',
        user_first_name='First1',
        user_last_name='Last1',
    )
    BasketLineItem.objects.create(
        event_date=datetime.date(2023, 11, 6),
        line=line,
        label='Repas',
        subject='Réservation',
        details='Lun 6/11',
        quantity=2,
        unit_amount=3,
        event_slug='agenda@repas',
        event_label='Repas',
        agenda_slug='agenda',
        activity_label='Activity Label !',
        accounting_code='424242',
    )
    line2 = BasketLine.objects.create(
        basket=basket,
        group_items=False,
        user_external_id='user:2',
        user_first_name='First2',
        user_last_name='Last2',
    )
    BasketLineItem.objects.create(
        event_date=datetime.date(2023, 11, 6),
        line=line2,
        label='Repas',
        subject='Réservation',
        details='Lun 6/11',
        quantity=2,
        unit_amount=3,
        event_slug='agenda@repas',
        event_label='Repas',
        agenda_slug='agenda',
        activity_label='Activity Label !',
        accounting_code='424242',
    )
    app.post('/api/regie/bar/basket/%s/line/%s/cancel/' % (basket.uuid, line.uuid), status=404)
    app.post('/api/regie/foo/basket/%s/line/%s/cancel/' % (uuid.uuid4(), line.uuid), status=404)
    resp = app.post('/api/regie/foo/basket/%s/line/%s/cancel/' % (basket.uuid, line.uuid))
    assert resp.json == {'err': 0}
    assert BasketLine.objects.count() == 1
    assert BasketLineItem.objects.count() == 1

    # line already closed
    line = BasketLine.objects.create(
        basket=basket,
        group_items=False,
        user_external_id='user:1',
        user_first_name='First1',
        user_last_name='Last1',
        closed=True,
    )
    app.post('/api/regie/foo/basket/%s/line/%s/cancel/' % (basket.uuid, line.uuid), status=404)

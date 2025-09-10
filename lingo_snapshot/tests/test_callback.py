import datetime
from unittest import mock

import pytest
from django.core.management import call_command
from django.utils.timezone import now
from requests.exceptions import ConnectionError
from requests.models import Response

from lingo.basket.models import Basket, BasketLine
from lingo.callback.models import Callback
from lingo.invoicing.models import DraftInvoice, Invoice, Regie

pytestmark = pytest.mark.django_db


@pytest.fixture
def regie():
    return Regie.objects.create(label='Foo')


@pytest.fixture
def basket_line(regie):
    draft_invoice = DraftInvoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2023, 4, 21),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=datetime.date(2023, 4, 23),
    )
    basket = Basket.objects.create(
        regie=regie,
        draft_invoice=draft_invoice,
        expiry_at=now(),
    )
    return BasketLine.objects.create(
        basket=basket,
        closed=True,
        validation_callback_url='http://basketline-validation.com',
        credit_callback_url='http://basketline-credit.com',
        payment_callback_url='http://basketline-payment.com',
        cancel_callback_url='http://basketline-cancel.com',
        expiration_callback_url='http://basketline-expiration.com',
    )


@pytest.fixture
def invoice(regie):
    return Invoice.objects.create(
        regie=regie,
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        payment_callback_url='http://invoice-payment.com',
        cancel_callback_url='http://invoice-cancel.com',
    )


@mock.patch('lingo.utils.requests_wrapper.RequestsSession.send')
def test_notify_immediatly(mock_send, invoice, basket_line):
    def test():
        callback_count = Callback.objects.count()
        for notification_type in ['payment', 'cancel']:
            mock_send.reset_mock()
            invoice.notify(notification_type, payload={'foo': 'bar'})
            assert Callback.objects.count() == callback_count + 1
            callback = Callback.objects.latest('created_at')
            assert callback.content_object == invoice
            assert callback.notification_type == notification_type
            assert callback.payload == {'foo': 'bar'}
            assert callback.status == 'completed'
            assert callback.retries_counter == 0
            assert callback.retry_reason == ''
            assert len(mock_send.call_args_list) == 1
            callback.delete()

        for notification_type in ['validation', 'payment', 'credit', 'cancel', 'expiration']:
            mock_send.reset_mock()
            basket_line.notify(notification_type, payload={'foo': 'bar'})
            assert Callback.objects.count() == callback_count + 1
            callback = Callback.objects.latest('created_at')
            assert callback.content_object == basket_line
            assert callback.notification_type == notification_type
            assert callback.payload == {'foo': 'bar'}
            assert callback.status == 'completed'
            assert callback.retries_counter == 0
            assert callback.retry_reason == ''
            assert len(mock_send.call_args_list) == 1
            callback.delete()

    # no callbacks for this instance, callback is run immediatly
    test()
    invoice.notify('payment', payload={'foo': 'bar'})
    basket_line.notify('validation', payload={'foo': 'bar'})
    for status in ['failure', 'completed']:
        # previous terminated callbacks for this instance, callback is run immediatly
        Callback.objects.update(status=status)
        test()


@mock.patch('lingo.utils.requests_wrapper.RequestsSession.send')
def test_notify_later(mock_send, invoice, basket_line):
    def test():
        for notification_type in ['payment', 'cancel']:
            mock_send.reset_mock()
            invoice.notify(notification_type, payload={'foo': 'bar'})
            assert Callback.objects.count() == 3
            callback = Callback.objects.latest('created_at')
            assert callback.content_object == invoice
            assert callback.notification_type == notification_type
            assert callback.payload == {'foo': 'bar'}
            assert callback.status == 'registered'
            assert callback.retries_counter == 0
            assert callback.retry_reason == ''
            assert len(mock_send.call_args_list) == 0
            callback.delete()

        for notification_type in ['validation', 'payment', 'credit', 'cancel', 'expiration']:
            mock_send.reset_mock()
            basket_line.notify(notification_type, payload={'foo': 'bar'})
            assert Callback.objects.count() == 3
            callback = Callback.objects.latest('created_at')
            assert callback.content_object == basket_line
            assert callback.notification_type == notification_type
            assert callback.payload == {'foo': 'bar'}
            assert callback.status == 'registered'
            assert callback.retries_counter == 0
            assert callback.retry_reason == ''
            assert len(mock_send.call_args_list) == 0
            callback.delete()

    invoice.notify('payment', payload={'foo': 'bar'})
    basket_line.notify('validation', payload={'foo': 'bar'})
    for status in ['registered', 'toretry']:
        # previous non terminated callbacks for this instance, callback will be run later
        Callback.objects.update(status=status)
        test()


@mock.patch('lingo.utils.requests_wrapper.RequestsSession.send')
def test_notify_failure(mock_send, invoice, basket_line):
    def test(retry_reason):
        for notification_type in ['payment', 'cancel']:
            mock_send.reset_mock()
            invoice.notify(notification_type, payload={'foo': 'bar'})
            assert Callback.objects.count() == 1
            callback = Callback.objects.latest('created_at')
            assert callback.content_object == invoice
            assert callback.notification_type == notification_type
            assert callback.payload == {'foo': 'bar'}
            assert callback.status == 'toretry'
            assert callback.retries_counter == 1
            assert callback.retry_reason == retry_reason % notification_type
            assert len(mock_send.call_args_list) == 1
            callback.delete()

        for notification_type in ['validation', 'payment', 'credit', 'cancel', 'expiration']:
            mock_send.reset_mock()
            basket_line.notify(notification_type, payload={'foo': 'bar'})
            assert Callback.objects.count() == 1
            callback = Callback.objects.latest('created_at')
            assert callback.content_object == basket_line
            assert callback.notification_type == notification_type
            assert callback.payload == {'foo': 'bar'}
            assert callback.status == 'toretry'
            assert callback.retries_counter == 1
            assert callback.retry_reason == retry_reason % notification_type
            assert len(mock_send.call_args_list) == 1
            callback.delete()

    mock_send.side_effect = ConnectionError()
    test("error (<class 'requests.exceptions.ConnectionError'>) notifying %s")

    mock_resp = Response()
    mock_resp.status_code = 500
    mock_send.side_effect = None
    mock_send.return_value = mock_resp
    test('error (HTTP 500) notifying %s')

    mock_resp = Response()
    mock_resp.status_code = 404
    mock_send.return_value = mock_resp
    test('error (HTTP 404) notifying %s')


@mock.patch('lingo.utils.requests_wrapper.RequestsSession.send')
def test_notify_cmd(mock_send, settings, invoice, basket_line):
    settings.CALLBACK_MAX_RETRIES = 3

    callback_invoice = invoice.notify('payment', payload={'foo': 'bar'})
    callback_basketline = basket_line.notify('validation', payload={'foo': 'bar'})
    Callback.objects.update(status='registered')

    mock_send.side_effect = ConnectionError()
    call_command('retry_callbacks')
    callback_invoice.refresh_from_db()
    assert callback_invoice.status == 'toretry'
    assert callback_invoice.retries_counter == 1
    assert (
        callback_invoice.retry_reason
        == "error (<class 'requests.exceptions.ConnectionError'>) notifying payment"
    )
    callback_basketline.refresh_from_db()
    assert callback_basketline.status == 'toretry'
    assert callback_basketline.retries_counter == 1
    assert (
        callback_basketline.retry_reason
        == "error (<class 'requests.exceptions.ConnectionError'>) notifying validation"
    )
    assert mock_send.call_args_list[0][1]['timeout'] == 15

    mock_send.reset_mock()
    mock_resp = Response()
    mock_resp.status_code = 500
    mock_send.side_effect = None
    mock_send.return_value = mock_resp
    call_command('retry_callbacks')
    callback_invoice.refresh_from_db()
    assert callback_invoice.status == 'toretry'
    assert callback_invoice.retries_counter == 2
    assert callback_invoice.retry_reason == 'error (HTTP 500) notifying payment'
    callback_basketline.refresh_from_db()
    assert callback_basketline.status == 'toretry'
    assert callback_basketline.retries_counter == 2
    assert callback_basketline.retry_reason == 'error (HTTP 500) notifying validation'
    assert mock_send.call_args_list[0][1]['timeout'] == (15, 60)

    mock_resp = Response()
    mock_resp.status_code = 404
    mock_send.return_value = mock_resp
    call_command('retry_callbacks')
    callback_invoice.refresh_from_db()
    assert callback_invoice.status == 'toretry'
    assert callback_invoice.retries_counter == 3
    assert callback_invoice.retry_reason == 'error (HTTP 404) notifying payment'
    callback_basketline.refresh_from_db()
    assert callback_basketline.status == 'toretry'
    assert callback_basketline.retries_counter == 3
    assert callback_basketline.retry_reason == 'error (HTTP 404) notifying validation'

    # the last retry
    call_command('retry_callbacks')
    callback_invoice.refresh_from_db()
    assert callback_invoice.status == 'failed'
    assert callback_invoice.retries_counter == 4
    assert callback_invoice.retry_reason == 'error (HTTP 404) notifying payment'
    callback_basketline.refresh_from_db()
    assert callback_basketline.status == 'failed'
    assert callback_basketline.retries_counter == 4
    assert callback_basketline.retry_reason == 'error (HTTP 404) notifying validation'

    # but if run anyway, with success, it's ok
    Callback.objects.update(status='toretry')
    mock_resp = Response()
    mock_resp.status_code = 200
    mock_send.return_value = mock_resp
    call_command('retry_callbacks')
    callback_invoice.refresh_from_db()
    assert callback_invoice.status == 'completed'
    assert callback_invoice.retries_counter == 4
    assert callback_invoice.retry_reason == 'error (HTTP 404) notifying payment'
    callback_basketline.refresh_from_db()
    assert callback_basketline.status == 'completed'
    assert callback_basketline.retries_counter == 4
    assert callback_basketline.retry_reason == 'error (HTTP 404) notifying validation'

    # unknown content_object
    Callback.objects.update(status='toretry', object_id=0)
    call_command('retry_callbacks')
    callback_invoice.refresh_from_db()
    assert callback_invoice.status == 'toretry'
    assert callback_invoice.retries_counter == 4
    assert callback_invoice.retry_reason == 'error (HTTP 404) notifying payment'
    callback_basketline.refresh_from_db()
    assert callback_basketline.status == 'toretry'
    assert callback_basketline.retries_counter == 4
    assert callback_basketline.retry_reason == 'error (HTTP 404) notifying validation'


@mock.patch('lingo.utils.requests_wrapper.RequestsSession.send')
def test_callback_clean(mock_send, invoice, basket_line):
    callback_invoice = invoice.notify('payment', payload={'foo': 'bar'})
    callback_invoice.status = 'failed'
    callback_invoice.save()
    callback_basketline = basket_line.notify('validation', payload={'foo': 'bar'})
    callback_basketline.status = 'completed'
    callback_basketline.save()

    callback_invoice2 = invoice.notify('payment', payload={'foo': 'bar'})
    callback_invoice2.status = 'registered'
    callback_invoice2.save()
    callback_basketline2 = basket_line.notify('validation', payload={'foo': 'bar'})
    callback_basketline2.status = 'toretry'
    callback_basketline2.save()

    call_command('clear_callbacks')
    assert Callback.objects.count() == 4

    Callback.objects.update(updated_at=now() - datetime.timedelta(days=50))
    call_command('clear_callbacks')
    assert Callback.objects.count() == 2
    assert Callback.objects.filter(pk=callback_invoice.pk).exists() is False
    assert Callback.objects.filter(pk=callback_invoice2.pk).exists() is True
    assert Callback.objects.filter(pk=callback_basketline.pk).exists() is False
    assert Callback.objects.filter(pk=callback_basketline2.pk).exists() is True

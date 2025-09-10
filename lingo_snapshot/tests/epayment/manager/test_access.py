import eopayment
import pytest

from lingo.epayment.models import PaymentBackend, Transaction
from tests.utils import login

pytestmark = pytest.mark.django_db


def test_manager_as_nothing(app, manager_user):
    backend = PaymentBackend.objects.create(
        label='Test',
        slug='test',
        service='dummy',
        service_options={'origin': 'Blah'},
    )
    Transaction.objects.create(
        order_id='1234', bank_transaction_id='2345', status=eopayment.WAITING, amount=20
    )
    Transaction.objects.create(
        order_id='1234',
        bank_transaction_id='2345',
        status=eopayment.WAITING,
        amount=20,
        backend=backend,
    )

    app = login(app, username='manager', password='manager')

    app.get('/manage/', status=403)
    app.get('/manage/inspect/', status=403)
    app.post('/manage/inspect/test-template/', status=403)
    resp = app.get('/manage/epayment/')
    assert list(resp.context['object_list']) == []
    assert '/manage/epayment/backend/' not in resp

    resp = app.get('/manage/epayment/backend/')
    assert list(resp.context['object_list']) == []
    assert '/manage/epayment/backend/add/' not in resp
    assert '/manage/epayment/backend/%s/' % backend.pk not in resp

    app.get('/manage/epayment/backend/add/', status=403)
    app.get('/manage/epayment/backend/%s/' % backend.pk, status=403)
    app.get('/manage/epayment/backend/%s/edit/' % backend.pk, status=403)
    app.get('/manage/epayment/backend/%s/delete/' % backend.pk, status=403)


def test_manager_as_viewer(app, manager_user):
    backend = PaymentBackend.objects.create(
        label='Test',
        service='dummy',
        service_options={'origin': 'Blah'},
        view_role=manager_user.groups.first(),
    )
    backend2 = PaymentBackend.objects.create(
        label='Test',
        service='dummy',
        service_options={'origin': 'Blah'},
    )
    Transaction.objects.create(
        order_id='1234', bank_transaction_id='2345', status=eopayment.WAITING, amount=20
    )
    transaction = Transaction.objects.create(
        order_id='1234',
        bank_transaction_id='2345',
        status=eopayment.WAITING,
        amount=20,
        backend=backend,
    )
    Transaction.objects.create(
        order_id='1234',
        bank_transaction_id='2345',
        status=eopayment.WAITING,
        amount=20,
        backend=backend2,
    )

    app = login(app, username='manager', password='manager')

    resp = app.get('/manage/')
    app.get('/manage/inspect/', status=403)
    app.post('/manage/inspect/test-template/', status=403)
    assert '/manage/epayment/' in resp
    resp = app.get('/manage/epayment/')
    assert list(resp.context['object_list']) == [transaction]
    assert '/manage/epayment/backend/' in resp

    resp = app.get('/manage/epayment/backend/')
    assert list(resp.context['object_list']) == [backend]
    assert '/manage/epayment/backend/add/' not in resp
    assert '/manage/epayment/backend/%s/' % backend.pk in resp
    assert '/manage/epayment/backend/%s/' % backend2.pk not in resp

    resp = app.get('/manage/epayment/backend/%s/' % backend.pk)
    assert '/manage/epayment/backend/%s/edit/' % backend.pk not in resp
    assert '/manage/epayment/backend/%s/delete/' % backend.pk not in resp
    app.get('/manage/epayment/backend/%s/edit/' % backend.pk, status=403)
    app.get('/manage/epayment/backend/%s/delete/' % backend.pk, status=403)

    app.get('/manage/epayment/backend/add/', status=403)


def test_manager_as_editer(app, manager_user):
    backend = PaymentBackend.objects.create(
        label='Test',
        service='dummy',
        service_options={'origin': 'Blah'},
        edit_role=manager_user.groups.first(),
    )
    backend2 = PaymentBackend.objects.create(
        label='Test',
        service='dummy',
        service_options={'origin': 'Blah'},
    )
    Transaction.objects.create(
        order_id='1234', bank_transaction_id='2345', status=eopayment.WAITING, amount=20
    )
    transaction = Transaction.objects.create(
        order_id='1234',
        bank_transaction_id='2345',
        status=eopayment.WAITING,
        amount=20,
        backend=backend,
    )
    Transaction.objects.create(
        order_id='1234',
        bank_transaction_id='2345',
        status=eopayment.WAITING,
        amount=20,
        backend=backend2,
    )

    app = login(app, username='manager', password='manager')

    resp = app.get('/manage/')
    app.get('/manage/inspect/', status=403)
    app.post('/manage/inspect/test-template/', status=403)
    assert '/manage/epayment/' in resp
    resp = app.get('/manage/epayment/')
    assert list(resp.context['object_list']) == [transaction]
    assert '/manage/epayment/backend/' in resp

    resp = app.get('/manage/epayment/backend/')
    assert list(resp.context['object_list']) == [backend]
    assert '/manage/epayment/backend/add/' not in resp
    assert '/manage/epayment/backend/%s/' % backend.pk in resp
    assert '/manage/epayment/backend/%s/' % backend2.pk not in resp

    resp = app.get('/manage/epayment/backend/%s/' % backend.pk)
    assert '/manage/epayment/backend/%s/edit/' % backend.pk in resp
    assert '/manage/epayment/backend/%s/delete/' % backend.pk in resp
    app.get('/manage/epayment/backend/%s/edit/' % backend.pk)
    app.get('/manage/epayment/backend/%s/delete/' % backend.pk)

    app.get('/manage/epayment/backend/add/', status=403)

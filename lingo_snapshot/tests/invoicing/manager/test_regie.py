import base64
import datetime
from unittest import mock
from urllib.parse import urlparse

import pytest
from django.contrib.auth.models import Group
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from webtest import Upload

from lingo.agendas.models import Agenda
from lingo.invoicing.models import (
    DEFAULT_PAYMENT_TYPES,
    AppearanceSettings,
    Campaign,
    Counter,
    Credit,
    DraftJournalLine,
    InjectedLine,
    Invoice,
    JournalLine,
    Payment,
    PaymentType,
    Pool,
    Refund,
    Regie,
)
from lingo.snapshot.models import RegieSnapshot
from tests.invoicing.utils import mocked_requests_send
from tests.utils import login

pytestmark = pytest.mark.django_db


def test_manager_invoicing_regie_list_title(app, admin_user):
    app = login(app)
    resp = app.get(reverse('lingo-manager-invoicing-regie-list'))
    h2 = resp.pyquery('div#appbar h2')
    assert h2.text() == 'Regies'


def test_manager_invoicing_regie_list_empty(app, admin_user):
    app = login(app)
    resp = app.get(reverse('lingo-manager-invoicing-regie-list'))
    msg_info = resp.pyquery('div#content div.big-msg-info')
    assert (
        msg_info.text()
        == "This site doesn't have any regie yet. Click on the \"New\" button in the top right of the page to add a first one."
    )


def test_manager_invoicing_regie_list_add_button(app, admin_user):
    app = login(app)
    resp = app.get(reverse('lingo-manager-invoicing-regie-list'))
    add_button = resp.pyquery('a[href="%s"]' % reverse('lingo-manager-invoicing-regie-add'))
    assert add_button.text() == 'New regie'


def test_manager_invoicing_regie_list_show_objetcs(app, admin_user):
    app = login(app)
    regie = Regie.objects.create(label='Foo')
    resp = app.get(reverse('lingo-manager-invoicing-regie-list'))
    href = resp.pyquery(
        'div#content div ul li a[href="%s"]'
        % reverse('lingo-manager-invoicing-regie-detail', kwargs={'pk': regie.pk})
    )
    assert href.text() == 'Foo [identifier: foo]'


def test_manager_invoicing_regie_add(app, admin_user):
    app = login(app)
    assert Regie.objects.count() == 0
    group = Group.objects.create(name='role-foo')
    resp = app.get(reverse('lingo-manager-invoicing-regie-add'))
    h2 = resp.pyquery('div#appbar h2')
    assert h2.text() == 'New regie'
    form = resp.form
    form.set('label', 'Foo')
    form.set('description', 'foo description')
    form.set('edit_role', group.id)
    form.set('view_role', group.id)
    form.set('invoice_role', group.id)
    form.set('control_role', group.id)
    response = form.submit().follow()
    assert Regie.objects.count() == 1
    regie = Regie.objects.first()
    assert regie.label == 'Foo'
    assert regie.slug == 'foo'
    assert regie.description == 'foo description'
    assert regie.edit_role == group
    assert regie.view_role == group
    assert regie.invoice_role == group
    assert regie.control_role == group
    assert regie.paymenttype_set.count() == len(DEFAULT_PAYMENT_TYPES)
    assert urlparse(response.request.url).path == reverse(
        'lingo-manager-invoicing-regie-detail', kwargs={'pk': regie.pk}
    )
    assert RegieSnapshot.objects.count() == 1


def test_manager_invoicing_regie_detail(app, admin_user):
    app = login(app)
    regie = Regie.objects.create(label='Foo', description='foo description')
    resp = app.get(reverse('lingo-manager-invoicing-regie-detail', kwargs={'pk': regie.pk}))
    h2 = resp.pyquery('div#appbar h2')
    assert h2.text() == 'Regie - Foo'


def test_manager_invoicing_regie_parameters(app, admin_user):
    app = login(app)
    group = Group.objects.create(name='role-foo')
    regie = Regie.objects.create(
        label='Foo',
        description='foo description',
        edit_role=group,
        view_role=group,
        invoice_role=group,
        control_role=group,
    )
    resp = app.get(reverse('lingo-manager-invoicing-regie-parameters', kwargs={'pk': regie.pk}))
    h2 = resp.pyquery('div#appbar h2')
    assert h2.text() == 'Regie - Foo'
    descr = resp.pyquery('div#panel-settings p')[0]
    assert descr.text == 'foo description'
    slug = resp.pyquery('div#panel-settings ul li')[0]
    assert slug.text == 'Identifier: foo'
    campaigns = resp.pyquery('div#panel-settings ul li')[1]
    assert campaigns.text == 'Regie with invoicing campaigns: no'
    assign = resp.pyquery('div#panel-settings ul li')[2]
    assert assign.text == 'Use a credit when created to pay old invoices: yes'
    edit_role = resp.pyquery('div#panel-permissions ul li')[0]
    assert edit_role.text == 'Edit role: role-foo'
    view_role = resp.pyquery('div#panel-permissions ul li')[1]
    assert view_role.text == 'View role: role-foo'
    invoice_role = resp.pyquery('div#panel-permissions ul li')[2]
    assert invoice_role.text == 'Invoice role: role-foo'
    control_role = resp.pyquery('div#panel-permissions ul li')[3]
    assert control_role.text == 'Control role: role-foo'
    usage = resp.pyquery('div#panel-usage div')[0]
    assert 'This regie is not used yet.' in usage.text
    edit_button = resp.pyquery(
        'a[href="%s"]' % reverse('lingo-manager-invoicing-regie-edit', kwargs={'pk': regie.pk})
    )
    assert edit_button.text() == 'Edit'
    delete_button = resp.pyquery(
        'a[href="%s"]' % reverse('lingo-manager-invoicing-regie-delete', kwargs={'pk': regie.pk})
    )
    assert delete_button.text() == 'Delete'

    agenda1 = Agenda.objects.create(label='Foo Bar', regie=regie)
    agenda2 = Agenda.objects.create(label='Foo Bar 2', regie=regie)
    agenda3 = Agenda.objects.create(label='Foo Bar 3')
    resp = app.get(reverse('lingo-manager-invoicing-regie-parameters', kwargs={'pk': regie.pk}))
    assert '/manage/pricing/agenda/%s/' % agenda1.pk in resp
    assert '/manage/pricing/agenda/%s/' % agenda2.pk in resp
    assert '/manage/pricing/agenda/%s/' % agenda3.pk not in resp


def test_manager_invoicing_regie_edit(app, admin_user):
    app = login(app)
    regie = Regie.objects.create(
        label='Foo',
        description='foo description',
    )
    resp = app.get(reverse('lingo-manager-invoicing-regie-edit', kwargs={'pk': regie.pk}))
    h2 = resp.pyquery('div#appbar h2')
    assert h2.text() == 'Edit regie - Foo'
    form = resp.form
    form.set('label', 'Foo bar')
    form.set('slug', 'foo-bar')
    form.set('description', 'foo new description')
    form.set('assign_credits_on_creation', '')
    response = form.submit().follow()
    assert Regie.objects.count() == 1
    regie = Regie.objects.first()
    assert regie.label == 'Foo bar'
    assert regie.slug == 'foo-bar'
    assert regie.description == 'foo new description'
    assert regie.assign_credits_on_creation is False
    assert urlparse(response.request.url).path == reverse(
        'lingo-manager-invoicing-regie-parameters', kwargs={'pk': regie.pk}
    )
    assert RegieSnapshot.objects.count() == 1

    Regie.objects.create(label='Foo', description='foo description')
    resp = app.get(reverse('lingo-manager-invoicing-regie-edit', kwargs={'pk': regie.pk}))
    form = resp.form
    form.set('slug', 'foo')
    response = form.submit()
    assert response.context['form'].errors['slug'] == ['Another regie exists with the same identifier.']


def test_manager_invoicing_regie_permissions_edit(app, admin_user):
    regie = Regie.objects.create(label='Foo', description='foo description')
    group_foo1 = Group.objects.create(name='role-foo1')
    group_foo2 = Group.objects.create(name='role-foo2')
    group_foo3 = Group.objects.create(name='role-foo3')
    group_foo4 = Group.objects.create(name='role-foo4')

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/parameters/' % regie.pk)
    assert '<li>Edit role: </li>' in resp
    assert '<li>View role: </li>' in resp
    assert '<li>Invoice role: </li>' in resp
    assert '<li>Control role: </li>' in resp
    resp = resp.click(href='/manage/invoicing/regie/%s/edit/permissions/' % regie.pk)
    resp.form.set('edit_role', group_foo1.id)
    resp.form.set('view_role', group_foo2.id)
    resp.form.set('invoice_role', group_foo3.id)
    resp.form.set('control_role', group_foo4.id)
    resp = resp.form.submit().follow()
    assert '<li>Edit role: role-foo1</li>' in resp
    assert '<li>View role: role-foo2</li>' in resp
    assert '<li>Invoice role: role-foo3</li>' in resp
    assert '<li>Control role: role-foo4</li>' in resp
    regie.refresh_from_db()
    assert regie.edit_role == group_foo1
    assert regie.view_role == group_foo2
    assert regie.invoice_role == group_foo3
    assert regie.control_role == group_foo4
    assert RegieSnapshot.objects.count() == 1


def test_manager_invoicing_regie_counters_edit(app, admin_user):
    app = login(app)
    regie = Regie.objects.create(label='Foo', description='foo description')
    resp = app.get(reverse('lingo-manager-invoicing-regie-counters-edit', kwargs={'pk': regie.pk}))
    h2 = resp.pyquery('div#appbar h2')
    assert h2.text() == 'Edit counters'
    form = resp.form
    form.set('counter_name', '{yyyy}')
    form.set('invoice_number_format', 'Ffoobar-{yyyy}-{number:08d}')
    form.set('collection_number_format', 'Tfoobar-{yyyy}-{number:08d}')
    form.set('payment_number_format', 'Rfoobar-{yyyy}-{number:08d}')
    form.set('docket_number_format', 'Bfoobar-{yyyy}-{number:08d}')
    form.set('credit_number_format', 'Afoobar-{yyyy}-{number:08d}')
    form.set('refund_number_format', 'Vfoobar-{yyyy}-{number:08d}')
    resp = form.submit()
    regie.refresh_from_db()
    assert regie.counter_name == '{yyyy}'
    assert regie.invoice_number_format == 'Ffoobar-{yyyy}-{number:08d}'
    assert regie.collection_number_format == 'Tfoobar-{yyyy}-{number:08d}'
    assert regie.payment_number_format == 'Rfoobar-{yyyy}-{number:08d}'
    assert regie.docket_number_format == 'Bfoobar-{yyyy}-{number:08d}'
    assert regie.credit_number_format == 'Afoobar-{yyyy}-{number:08d}'
    assert regie.refund_number_format == 'Vfoobar-{yyyy}-{number:08d}'
    assert resp.location.endswith('/manage/invoicing/regie/%s/parameters/#open:counters' % regie.pk)
    assert RegieSnapshot.objects.count() == 1


@mock.patch('requests.Session.send', side_effect=mocked_requests_send)
def test_manager_invoicing_regie_payer_edit(mock_send, app, admin_user):
    app = login(app)
    regie = Regie.objects.create(label='Foo', description='foo description', with_campaigns=True)
    resp = app.get(reverse('lingo-manager-invoicing-regie-payer-edit', kwargs={'pk': regie.pk}))
    assert resp.context['form'].fields['payer_carddef_reference'].widget.choices == [
        ('', '-----'),
        ('default:card_model_1', 'Card Model 1'),
        ('default:card_model_2', 'Card Model 2'),
        ('default:card_model_3', 'Card Model 3'),
    ]
    resp.form['payer_carddef_reference'] = 'default:card_model_1'
    resp.form['payer_external_id_prefix'] = 'prefix'
    resp.form['payer_external_id_template'] = 'template'
    resp.form['payer_external_id_from_nameid_template'] = 'nameid'
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/regie/%s/parameters/#open:payer' % regie.pk)
    regie.refresh_from_db()
    assert regie.payer_carddef_reference == 'default:card_model_1'
    assert regie.payer_external_id_prefix == 'prefix'
    assert regie.payer_external_id_template == 'template'
    assert regie.payer_external_id_from_nameid_template == 'nameid'
    assert regie.payer_cached_carddef_json == {
        'name': 'Card Model 1',
        'fields': [
            {'type': 'string', 'label': 'Field A', 'varname': 'fielda'},
            {'type': 'bool', 'label': 'Field B', 'varname': 'fieldb'},
            {'type': 'comment', 'label': 'Comment'},
            {'type': 'page', 'label': 'Page', 'varname': 'page'},
        ],
    }
    assert RegieSnapshot.objects.count() == 1

    regie.with_campaigns = False
    regie.save()
    resp = app.get(reverse('lingo-manager-invoicing-regie-payer-edit', kwargs={'pk': regie.pk}))
    assert 'payer_carddef_reference' not in resp.context['form'].fields
    assert 'payer_external_id_prefix' not in resp.context['form'].fields
    assert 'payer_external_id_template' not in resp.context['form'].fields
    assert 'payer_external_id_from_nameid_template' in resp.context['form'].fields


@mock.patch('requests.Session.send', side_effect=mocked_requests_send)
def test_manager_invoicing_regie_payer_mapping_edit(mock_send, app, admin_user):
    app = login(app)
    regie = Regie.objects.create(label='Foo', description='foo description', with_campaigns=True)
    resp = app.get('/manage/invoicing/regie/%s/parameters/' % regie.pk)
    assert '/manage/invoicing/regie/%s/edit/payer-mapping/' % regie.pk not in resp
    regie.payer_carddef_reference = 'default:card_model_1'
    regie.save()
    resp = app.get('/manage/invoicing/regie/%s/parameters/' % regie.pk)
    resp = resp.click(href='/manage/invoicing/regie/%s/edit/payer-mapping/' % regie.pk)
    assert len(resp.context['form'].fields) == 6
    choices = [('', '-----'), ('fielda', 'Field A'), ('fieldb', 'Field B')]
    assert resp.context['form'].fields['first_name'].choices == choices
    assert resp.context['form'].fields['last_name'].choices == choices
    assert resp.context['form'].fields['address'].choices == choices
    assert resp.context['form'].fields['email'].choices == choices
    assert resp.context['form'].fields['phone'].choices == choices
    assert resp.context['form'].fields['direct_debit'].choices == choices
    resp.form['first_name'] = 'fielda'
    resp.form['last_name'] = 'fieldb'
    resp.form['address'] = 'fieldb'
    resp.form['email'] = 'fieldb'
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/regie/%s/parameters/#open:payer-mapping' % regie.pk)
    regie.refresh_from_db()
    assert regie.payer_user_fields_mapping == {
        'first_name': 'fielda',
        'last_name': 'fieldb',
        'address': 'fieldb',
        'email': 'fieldb',
        'phone': '',
        'direct_debit': '',
    }
    resp = app.get('/manage/invoicing/regie/%s/edit/payer-mapping/' % regie.pk)
    assert resp.form['first_name'].value == 'fielda'
    assert resp.form['last_name'].value == 'fieldb'
    assert resp.form['address'].value == 'fieldb'
    assert resp.form['email'].value == 'fieldb'
    assert RegieSnapshot.objects.count() == 1


def test_manager_invoicing_appearance_settings(app, admin_user, settings):
    app = login(app)
    regie = Regie.objects.create(label='Foo', description='foo description')
    assert regie.invoice_model == 'middle'
    assert regie.certificate_model == ''
    resp = app.get(reverse('lingo-manager-invoicing-regie-list'))
    resp = resp.click('Appearance Settings')
    resp.form['logo'] = Upload(
        'test.png',
        base64.decodebytes(
            b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABAQAAAAA3bvkkAAAACklEQVQI12NoAAAAggCB3UNq9AAAAABJRU5ErkJggg=='
        ),
        'image/png',
    )
    resp.form['address'] = '<p>Foo bar<br>Streetname</p>'
    resp.form['extra_info'] = '<p>Opening hours...</p>'
    resp = resp.form.submit('submit').follow()
    appearance_settings = AppearanceSettings.singleton()
    assert appearance_settings.logo.name == 'logo/test.png'
    assert appearance_settings.address == '<p>Foo bar<br>Streetname</p>'
    assert appearance_settings.extra_info == '<p>Opening hours...</p>'
    resp = resp.click('Appearance Settings')
    assert resp.form['address'].value == '<p>Foo bar<br>Streetname</p>'

    # regie settings
    resp = app.get(reverse('lingo-manager-invoicing-regie-publishing-edit', kwargs={'pk': regie.pk}))
    resp.form.set('invoice_custom_text', '<p>custom text</p>')
    assert resp.form['invoice_model'].options == [
        ('basic', False, 'Basic'),
        ('middle', True, 'Middle'),
        ('full', False, 'Full'),
    ]
    assert resp.form['certificate_model'].options == [
        ('', True, 'Invoice information: No'),
        ('basic', False, 'Invoice information: Basic'),
        ('middle', False, 'Invoice information: Middle'),
        ('full', False, 'Invoice information: Full'),
    ]
    resp.form['invoice_model'] = 'basic'
    resp.form['certificate_model'] = 'basic'
    resp.form['main_colour'] = '#9141ac'
    resp = resp.form.submit()
    regie.refresh_from_db()
    assert regie.invoice_custom_text == '<p>custom text</p>'
    assert regie.invoice_model == 'basic'
    assert regie.certificate_model == 'basic'
    assert regie.main_colour == '#9141ac'
    assert resp.location.endswith('/manage/invoicing/regie/%s/parameters/#open:publishing' % regie.pk)

    # check French typography fixes
    settings.LANGUAGE_CODE = 'fr-fr'
    resp = app.get(reverse('lingo-manager-invoicing-regie-publishing-edit', kwargs={'pk': regie.pk}))
    resp.form.set('invoice_custom_text', '<p>custom : text</p>')
    resp = resp.form.submit()
    regie.refresh_from_db()
    assert regie.invoice_custom_text == '<p>custom\u00a0: text</p>'


def test_manager_invoicing_regie_delete(app, admin_user):
    app = login(app)
    regie = Regie.objects.create(label='Foo', description='foo description')
    PaymentType.objects.create(label='Foo', regie=regie)
    assert Regie.objects.count() == 1
    resp = app.get(reverse('lingo-manager-invoicing-regie-delete', kwargs={'pk': regie.pk}))
    response = resp.form.submit().follow()
    assert Regie.objects.count() == 0
    assert urlparse(response.request.url).path == reverse('lingo-manager-invoicing-regie-list')
    assert RegieSnapshot.objects.count() == 1

    # can not delete regie containing campaign
    regie.save()
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    resp = app.get(reverse('lingo-manager-invoicing-regie-parameters', kwargs={'pk': regie.pk}))
    assert reverse('lingo-manager-invoicing-regie-delete', kwargs={'pk': regie.pk}) not in resp
    resp = app.get(reverse('lingo-manager-invoicing-regie-delete', kwargs={'pk': regie.pk}), status=404)

    campaign.delete()

    # can not delete regie containing injected line
    injected_line = InjectedLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=1,
        regie=regie,
    )
    resp = app.get(reverse('lingo-manager-invoicing-regie-parameters', kwargs={'pk': regie.pk}))
    assert reverse('lingo-manager-invoicing-regie-delete', kwargs={'pk': regie.pk}) not in resp
    resp = app.get(reverse('lingo-manager-invoicing-regie-delete', kwargs={'pk': regie.pk}), status=404)

    injected_line.delete()

    # check counters are deleted
    Counter.get_count(regie=regie, name='bar', kind='invoice')
    Counter.get_count(regie=regie, name='bar', kind='collection')
    Counter.get_count(regie=regie, name='bar', kind='payment')
    Counter.get_count(regie=regie, name='bar', kind='docket')
    Counter.get_count(regie=regie, name='bar', kind='credit')
    Counter.get_count(regie=regie, name='bar', kind='refund')
    assert Counter.objects.count() == 6
    resp = app.get(reverse('lingo-manager-invoicing-regie-delete', kwargs={'pk': regie.pk}))
    response = resp.form.submit().follow()
    assert Regie.objects.count() == 0
    assert Counter.objects.count() == 0
    assert PaymentType.objects.count() == 0


def test_add_payment_type(app, admin_user):
    regie = Regie.objects.create(label='Regie')

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/parameters/' % regie.pk)
    resp = resp.click('New payment type')
    resp.form['label'] = 'Foo'
    assert 'slug' not in resp.context['form'].fields
    assert 'disabled' not in resp.context['form'].fields
    resp = resp.form.submit()
    payment_type = PaymentType.objects.latest('pk')
    assert resp.location.endswith('/manage/invoicing/regie/%s/parameters/#open:payment-types' % regie.pk)
    assert payment_type.label == 'Foo'
    assert payment_type.regie == regie
    assert payment_type.slug == 'foo'
    assert payment_type.disabled is False
    assert RegieSnapshot.objects.count() == 1

    resp = app.get('/manage/invoicing/regie/%s/payment-type/add/' % regie.pk)
    resp.form['label'] = 'Foo'
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/regie/%s/parameters/#open:payment-types' % regie.pk)
    payment_type = PaymentType.objects.latest('pk')
    assert payment_type.label == 'Foo'
    assert payment_type.regie == regie
    assert payment_type.slug == 'foo-1'
    assert payment_type.disabled is False


def test_edit_payment_type(app, admin_user):
    regie = Regie.objects.create(label='Regie')
    payment_type = PaymentType.objects.create(label='Foo', regie=regie)
    payment_type2 = PaymentType.objects.create(label='Baz', regie=regie)
    regie2 = Regie.objects.create(label='Regie2')
    payment_type3 = PaymentType.objects.create(label='Foo bar', regie=regie2)

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/parameters/' % regie.pk)
    resp = resp.click(href='/manage/invoicing/regie/%s/payment-type/%s/edit/' % (regie.pk, payment_type.pk))
    resp.form['label'] = 'Foo bar'
    resp.form['slug'] = payment_type2.slug
    resp.form['disabled'] = True
    resp = resp.form.submit()
    assert resp.context['form'].errors['slug'] == ['Another payment type exists with the same identifier.']

    resp.form['slug'] = payment_type3.slug
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/regie/%s/parameters/#open:payment-types' % regie.pk)
    payment_type.refresh_from_db()
    assert payment_type.label == 'Foo bar'
    assert payment_type.slug == 'foo-bar'
    assert payment_type.disabled is True
    assert RegieSnapshot.objects.count() == 1

    app.get('/manage/invoicing/regie/%s/payment-type/%s/edit/' % (regie2.pk, payment_type.pk), status=404)


def test_delete_payment_type(app, admin_user):
    regie = Regie.objects.create(label='Regie')
    regie2 = Regie.objects.create(label='Regie2')
    payment_type = PaymentType.objects.create(label='Foo', regie=regie)
    payment = Payment.objects.create(
        regie=regie,
        amount=1,
        payment_type=payment_type,
    )

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/parameters/' % regie.pk)
    assert '/manage/invoicing/regie/%s/payment-type/%s/delete/' % (regie.pk, payment_type.pk) not in resp
    app.get('/manage/invoicing/regie/%s/payment-type/%s/delete/' % (regie.pk, payment_type.pk), status=404)

    payment.delete()

    app.get('/manage/invoicing/regie/%s/payment-type/%s/delete/' % (regie2.pk, payment_type.pk), status=404)

    resp = app.get('/manage/invoicing/regie/%s/parameters/' % regie.pk)
    resp = resp.click(href='/manage/invoicing/regie/%s/payment-type/%s/delete/' % (regie.pk, payment_type.pk))
    resp = resp.form.submit()
    assert resp.location.endswith('/manage/invoicing/regie/%s/parameters/#open:payment-types' % regie.pk)
    assert PaymentType.objects.exists() is False
    assert RegieSnapshot.objects.count() == 1


def test_regie_inspect(app, admin_user):
    regie = Regie.objects.create(
        label='Regie',
        edit_role=Group.objects.create(name='role-foo1'),
        view_role=Group.objects.create(name='role-foo2'),
        invoice_role=Group.objects.create(name='role-foo3'),
        control_role=Group.objects.create(name='role-foo4'),
    )
    PaymentType.objects.create(label='Foo', regie=regie)
    PaymentType.objects.create(label='Bar', regie=regie)

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/parameters/' % regie.pk)
    with CaptureQueriesContext(connection) as ctx:
        resp = resp.click(href='/manage/invoicing/regie/%s/inspect/' % regie.pk)
        assert len(ctx.captured_queries) == 4


def test_non_invoiced_line_list(app, admin_user, settings):
    regie = Regie.objects.create(label='Regie')
    other_regie = Regie.objects.create(label='Other Regie')

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
    campaign2 = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    pool2 = Pool.objects.create(
        campaign=campaign2,
        draft=False,
    )

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
        draft=True,
    )
    other_campaign2 = Campaign.objects.create(
        regie=other_regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    other_pool2 = Pool.objects.create(
        campaign=other_campaign2,
        draft=False,
    )

    # not invoiced
    InjectedLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='event-2022-09-01',
        label='Event 2022-09-01',
        amount=3,
        user_external_id='user:1',
        payer_external_id='payer:1',
        regie=regie,
    )
    # not invoiced but in another regie
    InjectedLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        slug='other-event-2022-09-01',
        label='Other Event 2022-09-01',
        amount=3,
        user_external_id='user:1',
        payer_external_id='payer:1',
        regie=other_regie,
    )
    # not invoiced, but linked in a DraftJournalLine
    injected_line2 = InjectedLine.objects.create(
        event_date=datetime.date(2022, 9, 2),
        slug='event-2022-09-02',
        label='Event 2022-09-02',
        amount=3,
        user_external_id='user:1',
        payer_external_id='payer:1',
        regie=regie,
    )
    DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 2),
        slug='event-2022-09-02',
        label='Event 2022-09-02',
        amount=3,
        pool=pool,
        from_injected_line=injected_line2,
    )
    # not invoiced, but linked in a DraftJournalLine, but in another regie
    other_injected_line2 = InjectedLine.objects.create(
        event_date=datetime.date(2022, 9, 2),
        slug='other-event-2022-09-02',
        label='Other Event 2022-09-02',
        amount=3,
        user_external_id='user:1',
        payer_external_id='payer:1',
        regie=other_regie,
    )
    DraftJournalLine.objects.create(
        event_date=datetime.date(2022, 9, 2),
        slug='other-event-2022-09-02',
        label='Other Event 2022-09-02',
        amount=3,
        pool=other_pool,
        from_injected_line=other_injected_line2,
    )
    # invoiced, as linked in a non draft pool
    injected_line3 = InjectedLine.objects.create(
        event_date=datetime.date(2022, 9, 3),
        slug='event-2022-09-03',
        label='Event 2022-09-03',
        amount=3,
        user_external_id='user:1',
        payer_external_id='payer:1',
        regie=regie,
    )
    JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 3),
        slug='event-2022-09-03',
        label='Event 2022-09-03',
        amount=3,
        pool=pool2,
        from_injected_line=injected_line3,
    )

    # non fixed error
    JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 4),
        slug='event-2022-09-04',
        label='Event 2022-09-04',
        amount=0,
        pool=pool2,
        status='error',
    )
    # non fixed error, but in another regie
    JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 4),
        slug='other-event-2022-09-04',
        label='Other Event 2022-09-04',
        amount=0,
        pool=other_pool2,
        status='error',
    )
    # fixed or ignored errors
    JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 5),
        slug='event-2022-09-05',
        label='Event 2022-09-05',
        amount=0,
        pool=pool2,
        status='error',
        error_status='fixed',
    )
    JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 6),
        slug='event-2022-09-06',
        label='Event 2022-09-06',
        amount=0,
        pool=pool2,
        status='error',
        error_status='ignored',
    )
    # not errors
    JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 7),
        slug='event-2022-09-07',
        label='Event 2022-09-07',
        amount=0,
        pool=pool2,
        status='success',
    )
    JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 8),
        slug='event-2022-09-08',
        label='Event 2022-09-08',
        amount=0,
        pool=pool2,
        status='warning',
    )

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/' % regie.pk)
    assert '/manage/invoicing/regie/%s/non-invoiced-lines/' % regie.pk not in resp
    settings.SHOW_NON_INVOICED_LINES = True
    resp = app.get('/manage/invoicing/regie/%s/' % regie.pk)
    assert '/manage/invoicing/regie/%s/non-invoiced-lines/' % regie.pk in resp
    resp = app.get('/manage/invoicing/regie/%s/non-invoiced-lines/' % regie.pk)
    assert 'event-2022-09-01' in resp
    assert 'other-event-2022-09-01' not in resp
    assert 'event-2022-09-02' in resp
    assert 'other-event-2022-09-02' not in resp
    assert 'event-2022-09-03' not in resp
    assert 'event-2022-09-04' in resp
    assert 'other-event-2022-09-04' not in resp
    assert 'event-2022-09-05' not in resp
    assert 'event-2022-09-06' not in resp
    assert 'event-2022-09-07' not in resp
    assert 'event-2022-09-08' not in resp


def test_non_invoiced_line_link(app, admin_user, settings):
    settings.KNOWN_SERVICES = {}

    regie = Regie.objects.create(label='Regie')
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
        draft=False,
    )

    line = JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 4),
        slug='event-2022-09-04',
        label='Event 2022-09-04',
        amount=0,
        pool=pool,
        status='error',
    )

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/non-invoiced-lines/' % regie.pk)
    assert 'see agenda' not in resp
    assert 'see event' not in resp

    line.event = {
        'agenda': 'foobar',
    }
    line.save()
    resp = app.get('/manage/invoicing/regie/%s/non-invoiced-lines/' % regie.pk)
    assert '<a href="/manage/pricing/agenda/foobar/">see agenda</a>' in resp
    assert 'see event' not in resp

    line.event['slug'] = 'bazbaz'
    line.save()
    resp = app.get('/manage/invoicing/regie/%s/non-invoiced-lines/' % regie.pk)
    assert '<a href="/manage/pricing/agenda/foobar/">see agenda</a>' in resp
    assert 'see event' not in resp

    settings.KNOWN_SERVICES['chrono'] = {'default': {'url': 'https://chrono.dev/'}}
    resp = app.get('/manage/invoicing/regie/%s/non-invoiced-lines/' % regie.pk)
    assert '<a href="/manage/pricing/agenda/foobar/">see agenda</a>' in resp
    assert '<a href="https://chrono.dev/manage/agendas/foobar/events/bazbaz/">see event</a>' in resp


def test_manager_regies_goto_reference(app, admin_user):
    regie = Regie.objects.create(label='Regie')
    invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        regie=regie,
    )
    invoice.set_number()
    invoice.save()

    credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
    )
    credit.set_number()
    credit.save()

    payment = Payment.objects.create(
        amount=42,
        payment_type=PaymentType.objects.create(regie=regie, label='foo'),
        regie=regie,
    )
    payment.set_number()
    payment.save()

    refund = Refund.objects.create(
        regie=regie,
        amount=5.2,
    )
    refund.set_number()
    refund.save()

    app = login(app)
    for obj in (invoice, credit, payment, refund):
        resp = app.get(reverse('lingo-manager-invoicing-regie-list'))
        resp.form['reference'] = obj.formatted_number
        resp = resp.form.submit().follow()
        assert resp.request.url.endswith(f'?number={obj.formatted_number}')
        assert resp.pyquery('table tr')

    resp = app.get(reverse('lingo-manager-invoicing-regie-list'))
    resp.form['reference'] = obj.formatted_number
    resp.form['reference'] = 'unknown'
    resp = resp.form.submit().follow()
    assert resp.pyquery('.error').text() == 'No document found for "unknown"'

import copy
import datetime
import uuid
from unittest import mock

import pytest
from django.urls import reverse
from django.utils.formats import date_format
from django.utils.timezone import localtime, now
from pyquery import PyQuery

from lingo.agendas.chrono import ChronoError
from lingo.agendas.models import Agenda, AgendaUnlockLog, CheckType, CheckTypeGroup
from lingo.basket.models import Basket
from lingo.invoicing.models import (
    Campaign,
    CampaignAsyncJob,
    CollectionDocket,
    Credit,
    CreditAssignment,
    CreditLine,
    DraftInvoice,
    DraftInvoiceLine,
    DraftJournalLine,
    InjectedLine,
    Invoice,
    InvoiceLine,
    JournalLine,
    Payment,
    Pool,
    PoolAsyncJob,
    Regie,
)
from tests.utils import get_ods_rows, login

pytestmark = pytest.mark.django_db


def test_list_campaign(app, admin_user):
    app = login(app)
    regie = Regie.objects.create(label='Foo', description='foo description')
    resp = app.get(reverse('lingo-manager-invoicing-regie-detail', kwargs={'pk': regie.pk}))
    assert reverse('lingo-manager-invoicing-campaign-list', kwargs={'regie_pk': regie.pk}) not in resp
    regie.with_campaigns = True
    regie.save()
    resp = app.get(reverse('lingo-manager-invoicing-regie-detail', kwargs={'pk': regie.pk}))
    resp = resp.click(href=reverse('lingo-manager-invoicing-campaign-list', kwargs={'regie_pk': regie.pk}))
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
    resp = app.get(reverse('lingo-manager-invoicing-campaign-list', kwargs={'regie_pk': regie.pk}))
    assert (
        reverse('lingo-manager-invoicing-campaign-detail', kwargs={'regie_pk': regie.pk, 'pk': campaign.pk})
        in resp
    )
    li = resp.pyquery('ul li[data-campaign-id="%s"]' % campaign.pk)
    assert li.text() == '(01/09/2022 - 01/10/2022)'
    campaign.finalized = True
    campaign.save()
    resp = app.get(reverse('lingo-manager-invoicing-campaign-list', kwargs={'regie_pk': regie.pk}))
    li = resp.pyquery('ul li[data-campaign-id="%s"]' % campaign.pk)
    assert li.text() == '(01/09/2022 - 01/10/2022) validated'

    agenda = Agenda.objects.create(label='agenda')
    AgendaUnlockLog.objects.create(campaign=campaign, agenda=agenda)
    resp = app.get(reverse('lingo-manager-invoicing-campaign-list', kwargs={'regie_pk': regie.pk}))
    li = resp.pyquery('ul li[data-campaign-id="%s"]' % campaign.pk)
    assert li.text() == '(01/09/2022 - 01/10/2022) validated'

    campaign.agendas.add(agenda)
    resp = app.get(reverse('lingo-manager-invoicing-campaign-list', kwargs={'regie_pk': regie.pk}))
    li = resp.pyquery('ul li[data-campaign-id="%s"]' % campaign.pk)
    assert li.text() == '(01/09/2022 - 01/10/2022) [unlocked agendas: 1] validated'

    AgendaUnlockLog.objects.all().delete()
    corrective_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        primary_campaign=campaign,
        finalized=False,
    )
    resp = app.get(reverse('lingo-manager-invoicing-campaign-list', kwargs={'regie_pk': regie.pk}))
    assert (
        reverse('lingo-manager-invoicing-campaign-detail', kwargs={'regie_pk': regie.pk, 'pk': campaign.pk})
        in resp
    )
    assert (
        reverse(
            'lingo-manager-invoicing-campaign-detail',
            kwargs={'regie_pk': regie.pk, 'pk': corrective_campaign.pk},
        )
        in resp
    )
    li = resp.pyquery('ul li[data-campaign-id="%s"]' % campaign.pk)
    assert li.text() == '(01/09/2022 - 01/10/2022) [corrective campaigns: 1] correction in progress'
    li = resp.pyquery('ul li[data-primary-campaign-id="%s"]' % campaign.pk)
    assert li.text() == '(01/09/2022 - 01/10/2022)'

    corrective_campaign.finalized = True
    corrective_campaign.save()
    resp = app.get(reverse('lingo-manager-invoicing-campaign-list', kwargs={'regie_pk': regie.pk}))
    li = resp.pyquery('ul li[data-campaign-id="%s"]' % campaign.pk)
    assert li.text() == '(01/09/2022 - 01/10/2022) [corrective campaigns: 1] validated'
    li = resp.pyquery('ul li[data-primary-campaign-id="%s"]' % campaign.pk)
    assert li.text() == '(01/09/2022 - 01/10/2022) validated'

    AgendaUnlockLog.objects.create(campaign=campaign, agenda=agenda)
    resp = app.get(reverse('lingo-manager-invoicing-campaign-list', kwargs={'regie_pk': regie.pk}))
    li = resp.pyquery('ul li[data-campaign-id="%s"]' % campaign.pk)
    assert li.text() == '(01/09/2022 - 01/10/2022) [corrective campaigns: 1 - unlocked agendas: 1] validated'
    li = resp.pyquery('ul li[data-primary-campaign-id="%s"]' % campaign.pk)
    assert li.text() == '(01/09/2022 - 01/10/2022) validated'


def test_add_campaign(app, admin_user):
    regie = Regie.objects.create(label='Foo', invoice_model='basic')
    agenda = Agenda.objects.create(label='Foo bar', regie=regie)
    Agenda.objects.create(label='Other Foo bar')
    other_regie = Regie.objects.create(label='Other Foo')
    Campaign.objects.create(
        regie=other_regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/campaigns/' % regie.pk)
    resp = resp.click('New campaign')
    resp.form['label'] = 'Foo'
    resp.form['date_start'] = '2022-09-01'
    resp.form['date_end'] = '2022-08-01'
    resp.form.get('agendas', 0).value = agenda.pk
    assert resp.form['agendas'].options == [('', True, '---------'), (str(agenda.pk), False, 'Foo bar')]
    resp = resp.form.submit()
    assert resp.context['form'].errors['date_end'] == ['End date must be greater than start date.']
    resp.form['date_end'] = '2022-10-01'
    resp = resp.form.submit()
    campaign = Campaign.objects.latest('pk')
    assert resp.location.endswith('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert campaign.regie == regie
    assert campaign.label == 'Foo'
    assert campaign.date_start == datetime.date(2022, 9, 1)
    assert campaign.date_end == datetime.date(2022, 10, 1)
    assert campaign.date_publication == campaign.date_end
    assert campaign.date_payment_deadline_displayed is None
    assert campaign.date_payment_deadline == campaign.date_end
    assert campaign.date_due == campaign.date_end
    assert campaign.date_debit == campaign.date_end
    assert campaign.invoice_model == 'basic'
    assert list(campaign.agendas.all()) == [agenda]


def test_add_campaign_overlapping_date_start(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    agenda1 = Agenda.objects.create(label='Foo bar 1', regie=regie)
    agenda2 = Agenda.objects.create(label='Foo bar 2', regie=regie)
    agenda3 = Agenda.objects.create(label='Foo bar 3', regie=regie)
    campaign = Campaign.objects.create(
        regie=regie,
        label='Campaign',
        date_start=datetime.date(2022, 9, 15),
        date_end=datetime.date(2022, 10, 15),
        date_publication=datetime.date(2022, 11, 1),
        date_payment_deadline=datetime.date(2022, 11, 30),
        date_due=datetime.date(2022, 11, 30),
        date_debit=datetime.date(2022, 12, 15),
    )
    campaign.agendas.add(agenda1, agenda2)

    def add_agenda(resp):
        select = copy.copy(resp.form.fields['agendas'][0])
        select.id = 'id_agendas_1'
        resp.form.fields['agendas'].append(select)
        resp.form.field_order.append(('agendas', select))

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/campaign/add/' % regie.pk)
    add_agenda(resp)
    resp.form['label'] = 'Foo'
    resp.form['date_start'] = '2022-10-01'
    resp.form['date_end'] = '2022-11-01'
    resp.form.get('agendas', 0).value = agenda1.pk
    resp.form.get('agendas', 1).value = agenda2.pk
    resp = resp.form.submit()
    assert resp.context['form'].non_field_errors() == [
        'Agenda "Foo bar 1" has already a campaign overlapping this period.',
        'Agenda "Foo bar 2" has already a campaign overlapping this period.',
    ]

    # ok, no overlapping
    resp.form['date_start'] = '2022-10-15'
    resp.form.submit().follow()
    new_campaign = Campaign.objects.latest('pk')
    assert list(new_campaign.agendas.all()) == [agenda1, agenda2]

    resp = app.get('/manage/invoicing/regie/%s/campaign/add/' % regie.pk)
    add_agenda(resp)
    resp.form['label'] = 'Foo'
    resp.form['date_start'] = '2021-10-01'
    resp.form['date_end'] = '2022-11-01'
    resp.form.get('agendas', 0).value = agenda1.pk
    resp.form.get('agendas', 1).value = agenda2.pk
    resp = resp.form.submit()
    assert resp.context['form'].non_field_errors() == [
        'Agenda "Foo bar 1" has already a campaign overlapping this period.',
        'Agenda "Foo bar 2" has already a campaign overlapping this period.',
    ]
    resp.form.get('agendas', 0).value = agenda3.pk
    resp = resp.form.submit()
    assert resp.context['form'].non_field_errors() == [
        'Agenda "Foo bar 2" has already a campaign overlapping this period.'
    ]
    resp.form.get('agendas', 1).value = agenda3.pk
    # ok
    resp.form.submit().follow()
    new_campaign = Campaign.objects.latest('pk')
    assert list(new_campaign.agendas.all()) == [agenda3]


def test_add_campaign_overlapping_date_end(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    agenda1 = Agenda.objects.create(label='Foo bar 1', regie=regie)
    agenda2 = Agenda.objects.create(label='Foo bar 2', regie=regie)
    agenda3 = Agenda.objects.create(label='Foo bar 3', regie=regie)
    campaign = Campaign.objects.create(
        regie=regie,
        label='Campaign',
        date_start=datetime.date(2022, 10, 15),
        date_end=datetime.date(2022, 11, 15),
        date_publication=datetime.date(2022, 11, 1),
        date_payment_deadline=datetime.date(2022, 11, 30),
        date_due=datetime.date(2022, 11, 30),
        date_debit=datetime.date(2022, 12, 15),
    )
    campaign.agendas.add(agenda1, agenda2)

    def add_agenda(resp):
        select = copy.copy(resp.form.fields['agendas'][0])
        select.id = 'id_agendas_1'
        resp.form.fields['agendas'].append(select)
        resp.form.field_order.append(('agendas', select))

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/campaign/add/' % regie.pk)
    add_agenda(resp)
    resp.form['label'] = 'Foo'
    resp.form['date_start'] = '2022-10-01'
    resp.form['date_end'] = '2022-11-01'
    resp.form.get('agendas', 0).value = agenda1.pk
    resp.form.get('agendas', 1).value = agenda2.pk
    resp = resp.form.submit()
    assert resp.context['form'].non_field_errors() == [
        'Agenda "Foo bar 1" has already a campaign overlapping this period.',
        'Agenda "Foo bar 2" has already a campaign overlapping this period.',
    ]

    # ok, no overlapping
    resp.form['date_end'] = '2022-10-15'
    resp.form.submit().follow()
    new_campaign = Campaign.objects.latest('pk')
    assert list(new_campaign.agendas.all()) == [agenda1, agenda2]

    resp = app.get('/manage/invoicing/regie/%s/campaign/add/' % regie.pk)
    add_agenda(resp)
    resp.form['label'] = 'Foo'
    resp.form['date_start'] = '2022-10-01'
    resp.form['date_end'] = '2022-11-01'
    resp.form.get('agendas', 0).value = agenda1.pk
    resp.form.get('agendas', 1).value = agenda2.pk
    resp = resp.form.submit()
    assert resp.context['form'].non_field_errors() == [
        'Agenda "Foo bar 1" has already a campaign overlapping this period.',
        'Agenda "Foo bar 2" has already a campaign overlapping this period.',
    ]
    resp.form.get('agendas', 0).value = agenda3.pk
    resp = resp.form.submit()
    assert resp.context['form'].non_field_errors() == [
        'Agenda "Foo bar 2" has already a campaign overlapping this period.'
    ]
    resp.form.get('agendas', 1).value = agenda3.pk
    # ok
    resp.form.submit().follow()
    new_campaign = Campaign.objects.latest('pk')
    assert list(new_campaign.agendas.all()) == [agenda3]


def test_detail_campaign(app, admin_user):
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
    pool1 = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='completed',
    )
    pool2 = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='registered',
    )

    app = login(app)
    resp = app.get(url='/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/"' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/invoices/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool1.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool2.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk) not in resp

    pool2.status = 'running'
    pool2.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/"' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/nvoices/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool1.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool2.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk) not in resp

    pool2.status = 'failed'
    pool2.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/"' % (regie.pk, campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (regie.pk, campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/invoices/' % (regie.pk, campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool1.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool2.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk) not in resp

    pool3 = Pool.objects.create(
        campaign=campaign,
        draft=False,
        status='completed',
    )
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/"' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (regie.pk, campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/invoices/' % (regie.pk, campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool1.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool2.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool3.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk) in resp
    assert 'The last pool is invalid, please start a new pool.' not in resp
    for status in ['running', 'failed', 'registered']:
        pool3.status = status
        pool3.save()
        resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
        assert '/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk) not in resp

    pool3.status = 'completed'
    pool3.draft = True
    pool3.save()
    campaign.invalid = True
    campaign.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/"' % (regie.pk, campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (regie.pk, campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/invoices/' % (regie.pk, campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool1.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool2.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool3.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk) not in resp
    assert 'The last pool is invalid, please start a new pool.' in resp

    campaign.invalid = False
    campaign.finalized = True
    campaign.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/"' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (regie.pk, campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/invoices/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool1.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool2.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/%s/' % (regie.pk, campaign.pk, pool3.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk) not in resp

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
        pool=pool1,
    )
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert '<span class="meta meta-success">1</span>' in resp
    assert 'meta-warning' not in resp
    assert 'meta-error' not in resp
    line.status = 'error'
    line.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert 'meta-success' not in resp
    assert 'meta-warning' not in resp
    assert '<span class="meta meta-error">1</span>' in resp
    line.status = 'warning'
    line.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert 'meta-success' not in resp
    assert '<span class="meta meta-warning">1</span>' in resp
    assert 'meta-error' not in resp
    line.delete()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert 'meta-success' not in resp
    assert 'meta-warning' not in resp
    assert 'meta-error' not in resp

    line = JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        amount=1,
        status='success',
        pool=pool1,
    )
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert '<span class="meta meta-success">1</span>' in resp
    assert 'meta-warning' not in resp
    assert 'meta-error' not in resp
    line.status = 'error'
    line.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert 'meta-success' not in resp
    assert 'meta-warning' not in resp
    assert '<span class="meta meta-error">1</span>' in resp
    line.status = 'warning'
    line.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert 'meta-success' not in resp
    assert '<span class="meta meta-warning">1</span>' in resp
    assert 'meta-error' not in resp
    line.status = 'error'
    line.error_status = 'ignored'
    line.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert 'meta-success' not in resp
    assert 'meta-warning' not in resp
    assert '<span class="meta meta-error">1</span>' not in resp
    line.error_status = 'fixed'
    line.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert 'meta-success' not in resp
    assert 'meta-warning' not in resp
    assert '<span class="meta meta-error">1</span>' not in resp

    app.get('/manage/invoicing/regie/%s/campaign/%s/' % (0, campaign.pk), status=404)

    corrective_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        primary_campaign=campaign,
    )
    corrective_pool1 = Pool.objects.create(
        campaign=corrective_campaign,
        draft=True,
        status='completed',
    )
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, corrective_campaign.pk))
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/"' % (regie.pk, corrective_campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (regie.pk, corrective_campaign.pk) in resp
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/edit/invoices/' % (regie.pk, corrective_campaign.pk)
        not in resp
    )
    assert '/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, corrective_campaign.pk) in resp
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/'
        % (regie.pk, corrective_campaign.pk, corrective_pool1.pk)
        in resp
    )
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, corrective_campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, corrective_campaign.pk) in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, corrective_campaign.pk) not in resp

    corrective_pool1.draft = False
    corrective_pool1.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, corrective_campaign.pk))
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/"' % (regie.pk, corrective_campaign.pk) not in resp
    assert '/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (regie.pk, corrective_campaign.pk) in resp
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/edit/invoices/' % (regie.pk, corrective_campaign.pk)
        not in resp
    )
    assert '/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, corrective_campaign.pk) not in resp
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/pool/%s/'
        % (regie.pk, corrective_campaign.pk, corrective_pool1.pk)
        in resp
    )
    assert '/manage/invoicing/regie/%s/campaign/%s/pool/add/' % (regie.pk, corrective_campaign.pk) not in resp
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, corrective_campaign.pk)
        not in resp
    )
    assert '/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, corrective_campaign.pk) in resp

    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, corrective_campaign.pk))
    assert 'Some agendas have been unlocked since the last run:' not in resp
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert 'Some agendas have been unlocked since the last run:' not in resp

    agenda = Agenda.objects.create(label='Foo bar', regie=regie)
    AgendaUnlockLog.objects.create(campaign=campaign, agenda=agenda)
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, corrective_campaign.pk))
    assert 'Some agendas have been unlocked since the last run:' not in resp
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert 'Some agendas have been unlocked since the last run:' not in resp

    campaign.agendas.add(agenda)
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, corrective_campaign.pk))
    assert 'Some agendas have been unlocked since the last run:' in resp
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert 'Some agendas have been unlocked since the last run:' in resp


def test_edit_campaign(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    agenda = Agenda.objects.create(label='Foo bar', regie=regie)
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        injected_lines='no',
        adjustment_campaign=False,
    )

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/edit/' % (regie.pk, campaign.pk))
    assert 'injected_lines' not in resp.context['form'].fields
    InjectedLine.objects.create(
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
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/edit/' % (regie.pk, campaign.pk))
    resp.form['label'] = 'Bar'
    resp.form['date_start'] = '2022-09-30'
    resp.form['date_end'] = '2022-08-01'
    resp.form['injected_lines'] = 'period'
    resp.form['adjustment_campaign'] = True
    resp.form.get('agendas', 0).value = agenda.pk
    resp = resp.form.submit()
    assert resp.context['form'].errors['date_end'] == ['End date must be greater than start date.']
    resp.form['date_end'] = '2022-10-02'
    resp = resp.form.submit()
    campaign.refresh_from_db()
    assert campaign.date_start == datetime.date(2022, 9, 30)
    assert campaign.date_end == datetime.date(2022, 10, 2)
    assert campaign.injected_lines == 'period'
    assert campaign.adjustment_campaign is True
    assert campaign.invalid is False

    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='completed',
    )
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/edit/' % (regie.pk, campaign.pk))
    resp = resp.form.submit()
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/campaign/%s/#open:settings' % (regie.pk, campaign.pk)
    )
    campaign.refresh_from_db()
    assert campaign.invalid is True

    pool.status = 'failed'
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/' % (regie.pk, campaign.pk))

    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/' % (0, campaign.pk), status=404)

    pool.status = 'registered'
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/' % (regie.pk, campaign.pk), status=404)

    pool.status = 'running'
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/' % (regie.pk, campaign.pk), status=404)

    pool.status = 'completed'
    pool.draft = False
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/' % (regie.pk, campaign.pk), status=404)

    pool.draft = True
    pool.save()
    campaign.finalized = True
    campaign.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/' % (regie.pk, campaign.pk), status=404)

    corrective_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        primary_campaign=campaign,
    )
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/' % (regie.pk, corrective_campaign.pk), status=404)


def test_edit_campaign_overlapping_date_start(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    agenda1 = Agenda.objects.create(label='Foo bar 1', regie=regie)
    agenda2 = Agenda.objects.create(label='Foo bar 2', regie=regie)
    agenda3 = Agenda.objects.create(label='Foo bar 3', regie=regie)
    campaign1 = Campaign.objects.create(
        regie=regie,
        label='Campaign',
        date_start=datetime.date(2022, 10, 1),
        date_end=datetime.date(2022, 11, 1),
        date_publication=datetime.date(2022, 11, 1),
        date_payment_deadline=datetime.date(2022, 11, 30),
        date_due=datetime.date(2022, 11, 30),
        date_debit=datetime.date(2022, 12, 15),
    )
    campaign1.agendas.add(agenda1, agenda2)
    campaign2 = Campaign.objects.create(
        regie=regie,
        label='Campaign',
        date_start=datetime.date(2022, 9, 15),
        date_end=datetime.date(2022, 10, 15),
        date_publication=datetime.date(2022, 11, 1),
        date_payment_deadline=datetime.date(2022, 11, 30),
        date_due=datetime.date(2022, 11, 30),
        date_debit=datetime.date(2022, 12, 15),
    )
    campaign2.agendas.add(agenda1, agenda2)

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/edit/' % (regie.pk, campaign1.pk))
    resp = resp.form.submit()
    assert resp.context['form'].non_field_errors() == [
        'Agenda "Foo bar 1" has already a campaign overlapping this period.',
        'Agenda "Foo bar 2" has already a campaign overlapping this period.',
    ]

    # ok, no overlapping
    resp.form['date_start'] = '2022-10-15'
    resp.form.submit().follow()

    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/edit/' % (regie.pk, campaign1.pk))
    resp.form['date_start'] = '2021-10-01'
    resp = resp.form.submit()
    assert resp.context['form'].non_field_errors() == [
        'Agenda "Foo bar 1" has already a campaign overlapping this period.',
        'Agenda "Foo bar 2" has already a campaign overlapping this period.',
    ]
    resp.form.get('agendas', 0).value = agenda3.pk
    resp = resp.form.submit()
    assert resp.context['form'].non_field_errors() == [
        'Agenda "Foo bar 2" has already a campaign overlapping this period.'
    ]
    resp.form.get('agendas', 1).value = agenda3.pk
    # ok
    resp.form.submit().follow()
    assert list(campaign1.agendas.all()) == [agenda3]


def test_edit_campaign_overlapping_date_end(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    agenda1 = Agenda.objects.create(label='Foo bar 1', regie=regie)
    agenda2 = Agenda.objects.create(label='Foo bar 2', regie=regie)
    agenda3 = Agenda.objects.create(label='Foo bar 3', regie=regie)
    campaign1 = Campaign.objects.create(
        regie=regie,
        label='Campaign',
        date_start=datetime.date(2022, 10, 1),
        date_end=datetime.date(2022, 11, 1),
        date_publication=datetime.date(2022, 11, 1),
        date_payment_deadline=datetime.date(2022, 11, 30),
        date_due=datetime.date(2022, 11, 30),
        date_debit=datetime.date(2022, 12, 15),
    )
    campaign1.agendas.add(agenda1, agenda2)
    campaign2 = Campaign.objects.create(
        regie=regie,
        label='Campaign',
        date_start=datetime.date(2022, 10, 15),
        date_end=datetime.date(2022, 11, 15),
        date_publication=datetime.date(2022, 11, 1),
        date_payment_deadline=datetime.date(2022, 11, 30),
        date_due=datetime.date(2022, 11, 30),
        date_debit=datetime.date(2022, 12, 15),
    )
    campaign2.agendas.add(agenda1, agenda2)

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/edit/' % (regie.pk, campaign1.pk))
    resp = resp.form.submit()
    assert resp.context['form'].non_field_errors() == [
        'Agenda "Foo bar 1" has already a campaign overlapping this period.',
        'Agenda "Foo bar 2" has already a campaign overlapping this period.',
    ]

    # ok, no overlapping
    resp.form['date_end'] = '2022-10-15'
    resp.form.submit().follow()

    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/edit/' % (regie.pk, campaign1.pk))
    resp.form['date_end'] = '2022-11-01'
    resp = resp.form.submit()
    assert resp.context['form'].non_field_errors() == [
        'Agenda "Foo bar 1" has already a campaign overlapping this period.',
        'Agenda "Foo bar 2" has already a campaign overlapping this period.',
    ]
    resp.form.get('agendas', 0).value = agenda3.pk
    resp = resp.form.submit()
    assert resp.context['form'].non_field_errors() == [
        'Agenda "Foo bar 2" has already a campaign overlapping this period.'
    ]
    resp.form.get('agendas', 1).value = agenda3.pk
    # ok
    resp.form.submit().follow()
    assert list(campaign1.agendas.all()) == [agenda3]


def test_edit_campaign_dates(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    agenda = Agenda.objects.create(label='Foo bar', regie=regie)
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )
    campaign.agendas.add(agenda)

    pool1 = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='completed',
    )
    draft_invoice1 = DraftInvoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool1,
    )
    draft_invoice2 = DraftInvoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        date_debit=campaign.date_debit,
        regie=regie,
        pool=pool1,
        payer_direct_debit=True,
    )

    pool2 = Pool.objects.create(
        campaign=campaign,
        draft=False,
        status='completed',
    )
    invoice1 = Invoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool2,
    )
    invoice2 = Invoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        date_debit=campaign.date_debit,
        regie=regie,
        pool=pool2,
        payer_direct_debit=True,
    )

    orphan_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        regie=regie,
        payer_direct_debit=True,
    )

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (regie.pk, campaign.pk))
    resp.form['date_publication'] = '2022-10-31'
    resp.form['date_payment_deadline'] = '2022-10-30'
    resp.form['date_due'] = '2022-10-29'
    resp.form['date_debit'] = '2022-12-15'
    resp = resp.form.submit()
    assert resp.context['form'].errors == {
        'date_payment_deadline': ['Payment deadline must be greater than publication date.']
    }

    resp.form['date_publication'] = '2022-10-30'
    resp.form['date_payment_deadline'] = '2022-10-31'
    resp = resp.form.submit()
    assert resp.context['form'].errors == {'date_due': ['Due date must be greater than payment deadline.']}
    resp.form['date_publication'] = '2022-10-29'
    resp.form['date_payment_deadline'] = '2022-10-30'
    resp.form['date_due'] = '2022-10-31'
    resp.form.submit().follow()
    campaign.refresh_from_db()
    assert campaign.date_publication == datetime.date(2022, 10, 29)
    assert campaign.date_payment_deadline == datetime.date(2022, 10, 30)
    assert campaign.date_due == datetime.date(2022, 10, 31)
    assert campaign.date_debit == datetime.date(2022, 12, 15)
    assert campaign.invalid is False  # no impact

    draft_invoice1.refresh_from_db()
    assert draft_invoice1.date_publication == campaign.date_publication
    assert draft_invoice1.date_payment_deadline_displayed is None
    assert draft_invoice1.date_payment_deadline == campaign.date_payment_deadline
    assert draft_invoice1.date_due == campaign.date_due
    assert draft_invoice1.date_debit is None
    draft_invoice2.refresh_from_db()
    assert draft_invoice2.date_publication == campaign.date_publication
    assert draft_invoice2.date_payment_deadline_displayed is None
    assert draft_invoice2.date_payment_deadline == campaign.date_payment_deadline
    assert draft_invoice2.date_due == campaign.date_due
    assert draft_invoice2.date_debit == campaign.date_debit
    invoice1.refresh_from_db()
    assert invoice1.date_publication == campaign.date_publication
    assert invoice1.date_payment_deadline_displayed is None
    assert invoice1.date_payment_deadline == campaign.date_payment_deadline
    assert invoice1.date_due == campaign.date_due
    assert invoice1.date_debit is None
    invoice2.refresh_from_db()
    assert invoice2.date_publication == campaign.date_publication
    assert invoice1.date_payment_deadline_displayed is None
    assert invoice2.date_payment_deadline == campaign.date_payment_deadline
    assert invoice2.date_due == campaign.date_due
    assert invoice2.date_debit == campaign.date_debit
    orphan_invoice.refresh_from_db()
    assert orphan_invoice.date_publication == datetime.date(2022, 10, 1)
    assert orphan_invoice.date_payment_deadline_displayed is None
    assert orphan_invoice.date_payment_deadline == datetime.date(2022, 10, 31)
    assert orphan_invoice.date_due == datetime.date(2022, 10, 31)
    assert orphan_invoice.date_debit == datetime.date(2022, 11, 15)

    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (regie.pk, campaign.pk))
    resp.form['date_payment_deadline_displayed'] = '2022-10-15'
    resp = resp.form.submit()
    draft_invoice1.refresh_from_db()
    assert draft_invoice1.date_payment_deadline_displayed == datetime.date(2022, 10, 15)
    draft_invoice2.refresh_from_db()
    assert draft_invoice2.date_payment_deadline_displayed == datetime.date(2022, 10, 15)
    invoice1.refresh_from_db()
    assert invoice1.date_payment_deadline_displayed == datetime.date(2022, 10, 15)
    invoice2.refresh_from_db()
    assert invoice1.date_payment_deadline_displayed == datetime.date(2022, 10, 15)
    orphan_invoice.refresh_from_db()
    assert orphan_invoice.date_payment_deadline_displayed is None

    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (regie.pk, campaign.pk))

    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (0, campaign.pk), status=404)

    campaign.finalized = True
    campaign.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (regie.pk, campaign.pk))
    assert 'date_debit' not in resp.context['form'].fields
    resp.form['date_publication'] = '2022-10-27'
    resp.form['date_payment_deadline_displayed'] = '2022-10-28'
    resp.form['date_payment_deadline'] = '2022-10-29'
    resp.form['date_due'] = '2022-10-30'
    resp.form.submit().follow()
    campaign.refresh_from_db()
    assert campaign.date_publication == datetime.date(2022, 10, 27)
    assert campaign.date_payment_deadline_displayed == datetime.date(2022, 10, 28)
    assert campaign.date_payment_deadline == datetime.date(2022, 10, 29)
    assert campaign.date_due == datetime.date(2022, 10, 30)
    assert campaign.date_debit == datetime.date(2022, 12, 15)
    assert campaign.invalid is False  # no impact
    draft_invoice1.refresh_from_db()
    assert draft_invoice1.date_publication == campaign.date_publication
    assert draft_invoice1.date_payment_deadline_displayed == campaign.date_payment_deadline_displayed
    assert draft_invoice1.date_payment_deadline == campaign.date_payment_deadline
    assert draft_invoice1.date_due == campaign.date_due
    assert draft_invoice1.date_debit is None
    draft_invoice2.refresh_from_db()
    assert draft_invoice2.date_publication == campaign.date_publication
    assert draft_invoice2.date_payment_deadline_displayed == campaign.date_payment_deadline_displayed
    assert draft_invoice2.date_payment_deadline == campaign.date_payment_deadline
    assert draft_invoice2.date_due == campaign.date_due
    assert draft_invoice2.date_debit == campaign.date_debit
    invoice1.refresh_from_db()
    assert invoice1.date_publication == campaign.date_publication
    assert invoice1.date_payment_deadline_displayed == campaign.date_payment_deadline_displayed
    assert invoice1.date_payment_deadline == campaign.date_payment_deadline
    assert invoice1.date_due == campaign.date_due
    assert invoice1.date_debit is None
    invoice2.refresh_from_db()
    assert invoice2.date_publication == campaign.date_publication
    assert invoice1.date_payment_deadline_displayed == campaign.date_payment_deadline_displayed
    assert invoice2.date_payment_deadline == campaign.date_payment_deadline
    assert invoice2.date_due == campaign.date_due
    assert invoice2.date_debit == campaign.date_debit
    orphan_invoice.refresh_from_db()
    assert orphan_invoice.date_publication == datetime.date(2022, 10, 1)
    assert orphan_invoice.date_payment_deadline_displayed is None
    assert orphan_invoice.date_payment_deadline == datetime.date(2022, 10, 31)
    assert orphan_invoice.date_due == datetime.date(2022, 10, 31)
    assert orphan_invoice.date_debit == datetime.date(2022, 11, 15)

    corrective_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        primary_campaign=campaign,
    )
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/dates/' % (regie.pk, corrective_campaign.pk))


def test_edit_campaign_invoice_options(app, admin_user):
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

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/edit/invoices/' % (regie.pk, campaign.pk))
    assert resp.form['invoice_model'].options == [
        ('basic', False, 'Basic'),
        ('middle', True, 'Middle'),
        ('full', False, 'Full'),
    ]
    resp.form['invoice_model'] = 'basic'
    resp.form.set('invoice_custom_text', '<p>custom text</p>')
    resp = resp.form.submit()
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/campaign/%s/#open:invoices' % (regie.pk, campaign.pk)
    )
    campaign.refresh_from_db()
    assert campaign.invoice_model == 'basic'
    assert campaign.invoice_custom_text == '<p>custom text</p>'

    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/invoices/' % (regie.pk, campaign.pk))

    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/invoices/' % (0, campaign.pk), status=404)

    campaign.finalized = True
    campaign.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/edit/invoices/' % (regie.pk, campaign.pk), status=404)

    corrective_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        primary_campaign=campaign,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/edit/invoices/' % (regie.pk, corrective_campaign.pk),
        status=404,
    )


def test_delete_campaign(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 11, 1),
        date_payment_deadline=datetime.date(2022, 11, 30),
        date_due=datetime.date(2022, 11, 30),
        date_debit=datetime.date(2022, 12, 15),
    )

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, campaign.pk))
    resp = resp.form.submit()
    assert Campaign.objects.count() == 0
    assert resp.location.endswith('/manage/invoicing/regie/%s/campaigns/' % regie.pk)

    campaign.save()
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
    DraftInvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice,
        quantity=1,
        unit_amount=1,
        pool=pool,
    )
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, campaign.pk))
    resp = resp.form.submit()
    assert Campaign.objects.count() == 0

    campaign.save()
    pool.status = 'failed'
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, campaign.pk))

    app.get('/manage/invoicing/regie/%s/campaign/%s/delete/' % (0, campaign.pk), status=404)

    pool.status = 'registered'
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, campaign.pk), status=404)

    pool.status = 'running'
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, campaign.pk), status=404)

    pool.status = 'completed'
    pool.draft = False
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, campaign.pk), status=404)

    corrective_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        primary_campaign=campaign,
    )
    app.get('/manage/invoicing/regie/%s/campaign/%s/delete/' % (regie.pk, corrective_campaign.pk))


@mock.patch('lingo.invoicing.views.campaign.unlock_events_check')
def test_unlock_check(mock_unlock, app, admin_user):
    regie = Regie.objects.create(label='Foo')
    agenda = Agenda.objects.create(label='Foo bar', regie=regie)
    agenda2 = Agenda.objects.create(label='Foo bar 2', regie=regie)
    Agenda.objects.create(label='Foo bar 3', regie=regie)
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
    )

    app = login(app)

    # no agendas
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk))
    resp = resp.form.submit()
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/campaign/%s/#open:pools' % (regie.pk, campaign.pk)
    )
    assert mock_unlock.call_args_list == []
    campaign.refresh_from_db()
    assert campaign.invalid is True

    # with agendas
    campaign.invalid = False
    campaign.save()
    campaign.agendas.add(agenda, agenda2)
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk))
    resp = resp.form.submit()
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/campaign/%s/#open:pools' % (regie.pk, campaign.pk)
    )
    assert mock_unlock.call_args_list == [
        mock.call(
            agenda_slugs=['foo-bar', 'foo-bar-2'],
            date_start=datetime.date(2022, 9, 1),
            date_end=datetime.date(2022, 10, 1),
        )
    ]
    campaign.refresh_from_db()
    assert campaign.invalid is True

    # ChronoError
    campaign.invalid = False
    campaign.save()
    mock_unlock.side_effect = ChronoError('foo baz')
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk))
    resp = resp.form.submit().follow()
    assert 'Fail to unlock events check: foo baz' in resp

    campaign.invalid = False
    campaign.save()
    pool = Pool.objects.create(
        campaign=campaign,
        draft=True,
        status='failed',
    )
    app.get('/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk))

    pool.status = 'completed'
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk))

    app.get('/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (0, campaign.pk), status=404)

    pool.status = 'registered'
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk), status=404)

    pool.status = 'running'
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk), status=404)

    pool.status = 'completed'
    pool.draft = False
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk), status=404)

    pool.draft = True
    pool.save()
    campaign.finalized = True
    campaign.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, campaign.pk), status=404)

    corrective_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        primary_campaign=campaign,
    )
    app.get('/manage/invoicing/regie/%s/campaign/%s/unlock-check/' % (regie.pk, corrective_campaign.pk))


@mock.patch('lingo.invoicing.views.campaign.mark_events_invoiced')
def test_finalize(mock_invoiced, app, admin_user):
    regie = Regie.objects.create(label='Foo')
    agenda = Agenda.objects.create(label='Foo bar', regie=regie)
    agenda2 = Agenda.objects.create(label='Foo bar 2', regie=regie)
    Agenda.objects.create(label='Foo bar 3', regie=regie)
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
        status='completed',
    )

    app = login(app)

    # no agendas
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk))
    resp = resp.form.submit()
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/campaign/%s/#open:pools' % (regie.pk, campaign.pk)
    )
    assert mock_invoiced.call_args_list == []
    campaign.refresh_from_db()
    assert campaign.finalized is True

    # with agendas
    campaign.finalized = False
    campaign.save()
    campaign.agendas.add(agenda, agenda2)
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk))
    resp = resp.form.submit()
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/campaign/%s/#open:pools' % (regie.pk, campaign.pk)
    )
    assert mock_invoiced.call_args_list == [
        mock.call(
            agenda_slugs=['foo-bar', 'foo-bar-2'],
            date_start=datetime.date(2022, 9, 1),
            date_end=datetime.date(2022, 10, 1),
        )
    ]
    campaign.refresh_from_db()
    assert campaign.finalized is True

    # ChronoError
    campaign.finalized = False
    campaign.save()
    mock_invoiced.side_effect = ChronoError('foo baz')
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk))
    resp = resp.form.submit().follow()
    assert 'Fail to mark events as invoiced: foo baz' in resp
    campaign.refresh_from_db()
    assert campaign.finalized is False

    pool.status = 'failed'
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk), status=404)

    pool.status = 'completed'
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk))

    app.get('/manage/invoicing/regie/%s/campaign/%s/finalize/' % (0, campaign.pk), status=404)

    pool.status = 'registered'
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk), status=404)

    pool.status = 'running'
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk), status=404)

    pool.status = 'completed'
    pool.draft = True
    pool.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk), status=404)

    pool.draft = False
    pool.save()
    campaign.invalid = True
    campaign.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk), status=404)

    campaign.invalid = False
    campaign.finalized = True
    campaign.save()
    app.get('/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk), status=404)

    corrective_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        primary_campaign=campaign,
    )
    Pool.objects.create(
        campaign=corrective_campaign,
        draft=False,
        status='completed',
    )
    app.get('/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, corrective_campaign.pk))


def test_finalize_with_credits(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        date_debit=datetime.date(2022, 11, 15),
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
        status='completed',
    )
    other_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        finalized=False,
    )
    other_pool = Pool.objects.create(
        campaign=other_campaign,
        draft=False,
        status='completed',
    )
    credit1 = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        pool=pool,
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit1,
        quantity=5,
        unit_amount=1,
    )
    credit2 = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit2,
        quantity=5,
        unit_amount=1,
    )
    credit3 = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:2',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit3,
        quantity=5,
        unit_amount=1,
    )
    credit4 = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:42',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit4,
        quantity=5,
        unit_amount=1,
    )
    other_credit = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        pool=other_pool,  # other pool, campaign not finalized
        payer_external_id='payer:1',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=other_credit,
        quantity=5,
        unit_amount=1,
    )
    other_credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        cancelled_at=now(),  # cancelled
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=other_credit,
        quantity=5,
        unit_amount=1,
    )
    other_credit = Credit.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        usable=False,  # not usable to pay invoices
    )
    CreditLine.objects.create(
        credit=other_credit,
        event_date=datetime.date(2022, 9, 1),
        quantity=5,
        unit_amount=1,
    )
    invoice1 = Invoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
        payer_external_id='payer:1',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=1,
        unit_amount=1,
        pool=pool,
    )
    invoice2 = Invoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
        payer_external_id='payer:1',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice2,
        quantity=5,
        unit_amount=1,
        pool=pool,
    )
    invoice3 = Invoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
        payer_external_id='payer:2',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice3,
        quantity=10,
        unit_amount=1,
        pool=pool,
    )
    invoice4 = Invoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
        payer_external_id='payer:3',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice4,
        quantity=1,
        unit_amount=1,
        pool=pool,
    )

    app = login(app)

    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk))
    with mock.patch('lingo.invoicing.models.CampaignAsyncJob.run') as mock_run:
        resp = resp.form.submit()
        assert mock_run.call_args_list == [mock.call()]

    assert CampaignAsyncJob.objects.count() == 1
    assert PoolAsyncJob.objects.count() == 0
    cjob = CampaignAsyncJob.objects.get()
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/campaign/%s/job/%s/' % (regie.pk, campaign.pk, cjob.uuid)
    )
    assert cjob.campaign == campaign
    assert cjob.params == {}
    assert cjob.action == 'assign_credits'
    assert cjob.status == 'registered'
    cjob.run()
    assert cjob.status == 'completed'
    assert cjob.total_count == 5
    assert cjob.current_count == 5

    campaign.refresh_from_db()
    assert campaign.finalized is True
    invoice1.refresh_from_db()
    assert invoice1.remaining_amount == 0
    assert invoice1.paid_amount == 1
    invoice2.refresh_from_db()
    assert invoice2.remaining_amount == 0
    assert invoice2.paid_amount == 5
    invoice3.refresh_from_db()
    assert invoice3.remaining_amount == 5
    assert invoice3.paid_amount == 5
    invoice4.refresh_from_db()
    assert invoice4.remaining_amount == 1
    assert invoice4.paid_amount == 0
    credit1.refresh_from_db()
    assert credit1.remaining_amount == 0
    assert credit1.assigned_amount == 5
    credit2.refresh_from_db()
    assert credit2.remaining_amount == 4
    assert credit2.assigned_amount == 1
    credit3.refresh_from_db()
    assert credit3.remaining_amount == 0
    assert credit3.assigned_amount == 5
    credit4.refresh_from_db()
    assert credit4.remaining_amount == 5
    assert credit4.assigned_amount == 0
    other_credit.refresh_from_db()
    assert other_credit.remaining_amount == 5
    assert other_credit.assigned_amount == 0
    assert CreditAssignment.objects.count() == 4
    assignment1, assignment2, assignment3, assignment4 = CreditAssignment.objects.all().order_by('pk')
    assert assignment1.amount == 1
    assert assignment1.invoice == invoice1
    assert assignment1.credit == credit1
    assert assignment2.amount == 4
    assert assignment2.invoice == invoice2
    assert assignment2.credit == credit1
    assert assignment3.amount == 1
    assert assignment3.invoice == invoice2
    assert assignment3.credit == credit2
    assert assignment4.amount == 5
    assert assignment4.invoice == invoice3
    assert assignment4.credit == credit3
    assert Payment.objects.count() == 4
    payment1, payment2, payment3, payment4 = Payment.objects.all().order_by('pk')
    assert payment1.amount == 1
    assert payment1.payment_type.slug == 'credit'
    assert assignment1.payment == payment1
    assert payment1.invoicelinepayment_set.count() == 1
    invoicelinepayment1 = payment1.invoicelinepayment_set.get()
    assert invoicelinepayment1.line == invoice1.lines.get()
    assert invoicelinepayment1.amount == 1
    assert payment2.amount == 4
    assert payment2.payment_type.slug == 'credit'
    assert assignment2.payment == payment2
    assert payment2.invoicelinepayment_set.count() == 1
    invoicelinepayment2 = payment2.invoicelinepayment_set.get()
    assert invoicelinepayment2.line == invoice2.lines.get()
    assert invoicelinepayment2.amount == 4
    assert payment3.amount == 1
    assert payment3.payment_type.slug == 'credit'
    assert assignment3.payment == payment3
    assert payment3.invoicelinepayment_set.count() == 1
    invoicelinepayment3 = payment3.invoicelinepayment_set.get()
    assert invoicelinepayment3.line == invoice2.lines.get()
    assert invoicelinepayment3.amount == 1
    assert payment4.amount == 5
    assert payment4.payment_type.slug == 'credit'
    assert assignment4.payment == payment4
    assert payment4.invoicelinepayment_set.count() == 1
    invoicelinepayment4 = payment4.invoicelinepayment_set.get()
    assert invoicelinepayment4.line == invoice3.lines.get()
    assert invoicelinepayment4.amount == 5

    # date_due is in the past
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date() - datetime.timedelta(days=1),
        date_debit=datetime.date(2022, 11, 15),
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
        status='completed',
    )
    credit1 = Credit.objects.create(
        label='Credit from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        regie=regie,
        payer_external_id='payer:1',
        pool=pool,
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit1,
        quantity=5,
        unit_amount=1,
    )
    invoice1 = Invoice.objects.create(
        date_publication=campaign.date_publication,
        date_payment_deadline=campaign.date_payment_deadline,
        date_due=campaign.date_due,
        regie=regie,
        pool=pool,
        payer_external_id='payer:1',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=1,
        unit_amount=1,
        pool=pool,
    )

    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk))
    resp = resp.form.submit()
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/campaign/%s/#open:pools' % (regie.pk, campaign.pk)
    )
    campaign.refresh_from_db()
    assert campaign.finalized is True
    invoice1.refresh_from_db()
    assert invoice1.remaining_amount == 1
    assert invoice1.paid_amount == 0


def test_finalize_with_invoices(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        date_debit=datetime.date(2022, 11, 15),
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
        status='completed',
    )
    other_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        finalized=False,
    )
    other_pool = Pool.objects.create(
        campaign=other_campaign,
        draft=False,
        status='completed',
    )
    invoice1 = Invoice.objects.create(
        label='Invoice from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
        pool=pool,
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice1,
        quantity=5,
        unit_amount=1,
    )
    invoice2 = Invoice.objects.create(
        label='Invoice from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:1',
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
        payer_external_id='payer:2',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice3,
        quantity=5,
        unit_amount=1,
    )
    invoice4 = Invoice.objects.create(
        label='Invoice from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:42',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=invoice4,
        quantity=5,
        unit_amount=1,
    )
    other_invoice = Invoice.objects.create(
        label='Invoice from 01/09/2022',
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        pool=other_pool,  # other pool, campaign not finalized
        payer_external_id='payer:3',
    )
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=other_invoice,
        quantity=5,
        unit_amount=1,
    )
    other_invoice = Invoice.objects.create(
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2023, 4, 22),
        date_due=now().date(),
        regie=regie,
        payer_external_id='payer:3',
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
        payer_external_id='payer:3',
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
        payer_external_id='payer:3',
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
        payer_external_id='payer:3',
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
        payer_external_id='payer:3',
        collection=collection,  # collected
    )
    collected_invoice.set_number()
    collected_invoice.save()
    InvoiceLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        invoice=collected_invoice,
        quantity=2,
        unit_amount=1,
    )
    credit1 = Credit.objects.create(
        date_publication=campaign.date_publication,
        regie=regie,
        pool=pool,
        payer_external_id='payer:1',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit1,
        quantity=1,
        unit_amount=1,
    )
    credit2 = Credit.objects.create(
        date_publication=campaign.date_publication,
        regie=regie,
        pool=pool,
        payer_external_id='payer:1',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit2,
        quantity=5,
        unit_amount=1,
    )
    credit3 = Credit.objects.create(
        date_publication=campaign.date_publication,
        regie=regie,
        pool=pool,
        payer_external_id='payer:2',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit3,
        quantity=10,
        unit_amount=1,
    )
    credit4 = Credit.objects.create(
        date_publication=campaign.date_publication,
        regie=regie,
        pool=pool,
        payer_external_id='payer:3',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit4,
        quantity=1,
        unit_amount=1,
    )

    app = login(app)

    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk))
    resp = resp.form.submit()
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/campaign/%s/#open:pools' % (regie.pk, campaign.pk)
    )
    campaign.refresh_from_db()
    assert campaign.finalized is True
    credit1.refresh_from_db()
    assert credit1.remaining_amount == 0
    assert credit1.assigned_amount == 1
    credit2.refresh_from_db()
    assert credit2.remaining_amount == 0
    assert credit2.assigned_amount == 5
    credit3.refresh_from_db()
    assert credit3.remaining_amount == 5
    assert credit3.assigned_amount == 5
    credit4.refresh_from_db()
    assert credit4.remaining_amount == 1
    assert credit4.assigned_amount == 0
    invoice1.refresh_from_db()
    assert invoice1.remaining_amount == 0
    assert invoice1.paid_amount == 5
    invoice2.refresh_from_db()
    assert invoice2.remaining_amount == 4
    assert invoice2.paid_amount == 1
    invoice3.refresh_from_db()
    assert invoice3.remaining_amount == 0
    assert invoice3.paid_amount == 5
    invoice4.refresh_from_db()
    assert invoice4.remaining_amount == 5
    assert invoice4.paid_amount == 0
    other_invoice.refresh_from_db()
    assert other_invoice.remaining_amount == 5
    assert other_invoice.paid_amount == 0
    assert CreditAssignment.objects.count() == 4
    assignment1, assignment2, assignment3, assignment4 = CreditAssignment.objects.all().order_by('pk')
    assert assignment1.amount == 1
    assert assignment1.invoice == invoice1
    assert assignment1.credit == credit1
    assert assignment2.amount == 4
    assert assignment2.invoice == invoice1
    assert assignment2.credit == credit2
    assert assignment3.amount == 1
    assert assignment3.invoice == invoice2
    assert assignment3.credit == credit2
    assert assignment4.amount == 5
    assert assignment4.invoice == invoice3
    assert assignment4.credit == credit3
    assert Payment.objects.count() == 4
    payment1, payment2, payment3, payment4 = Payment.objects.all().order_by('pk')
    assert payment1.amount == 1
    assert payment1.payment_type.slug == 'credit'
    assert assignment1.payment == payment1
    assert payment1.invoicelinepayment_set.count() == 1
    invoicelinepayment1 = payment1.invoicelinepayment_set.get()
    assert invoicelinepayment1.line == invoice1.lines.get()
    assert invoicelinepayment1.amount == 1
    assert payment2.amount == 4
    assert payment2.payment_type.slug == 'credit'
    assert assignment2.payment == payment2
    assert payment2.invoicelinepayment_set.count() == 1
    invoicelinepayment2 = payment2.invoicelinepayment_set.get()
    assert invoicelinepayment2.line == invoice1.lines.get()
    assert invoicelinepayment2.amount == 4
    assert payment3.amount == 1
    assert payment3.payment_type.slug == 'credit'
    assert assignment3.payment == payment3
    assert payment3.invoicelinepayment_set.count() == 1
    invoicelinepayment3 = payment3.invoicelinepayment_set.get()
    assert invoicelinepayment3.line == invoice2.lines.get()
    assert invoicelinepayment3.amount == 1
    assert payment4.amount == 5
    assert payment4.payment_type.slug == 'credit'
    assert assignment4.payment == payment4
    assert payment4.invoicelinepayment_set.count() == 1
    invoicelinepayment4 = payment4.invoicelinepayment_set.get()
    assert invoicelinepayment4.line == invoice3.lines.get()
    assert invoicelinepayment4.amount == 5

    # regie not configured to assign credits when created
    regie.assign_credits_on_creation = False
    regie.save()
    campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        date_debit=datetime.date(2022, 11, 15),
    )
    pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
        status='completed',
    )

    credit1 = Credit.objects.create(
        date_publication=campaign.date_publication,
        regie=regie,
        pool=pool,
        payer_external_id='payer:1',
    )
    CreditLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        credit=credit1,
        quantity=1,
        unit_amount=42,
    )
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
        quantity=42,
        unit_amount=1,
    )
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/finalize/' % (regie.pk, campaign.pk))
    resp = resp.form.submit()
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/campaign/%s/#open:pools' % (regie.pk, campaign.pk)
    )
    campaign.refresh_from_db()
    assert campaign.finalized is True
    credit1.refresh_from_db()
    assert credit1.remaining_amount == 42
    assert credit1.assigned_amount == 0
    invoice1.refresh_from_db()
    assert invoice1.remaining_amount == 42
    assert invoice1.paid_amount == 0


def test_add_corrective_campaign(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    agenda1 = Agenda.objects.create(label='Foo bar 1', regie=regie)
    agenda2 = Agenda.objects.create(label='Foo bar 2', regie=regie)
    agenda3 = Agenda.objects.create(label='Foo bar 3', regie=regie)
    primary_campaign = Campaign.objects.create(
        label='My campaign',
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        date_debit=datetime.date(2022, 11, 15),
        injected_lines='all',
        adjustment_campaign=True,
        finalized=False,
        invoice_model='basic',
        invoice_custom_text='foo bar',
    )
    primary_campaign.agendas.add(agenda1, agenda2)
    AgendaUnlockLog.objects.create(campaign=primary_campaign, agenda=agenda1)
    AgendaUnlockLog.objects.create(campaign=primary_campaign, agenda=agenda2)
    AgendaUnlockLog.objects.create(campaign=primary_campaign, agenda=agenda3)
    old_updated_at = list(AgendaUnlockLog.objects.values_list('updated_at', flat=True).order_by('created_at'))

    app = login(app)

    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, primary_campaign.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/add-corrective-campaign/' % (regie.pk, primary_campaign.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agendas/add/' % (regie.pk, primary_campaign.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, primary_campaign.pk, agenda1.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, primary_campaign.pk, agenda2.pk)
        not in resp
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/add-corrective-campaign/' % (regie.pk, primary_campaign.pk),
        status=404,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agendas/add/' % (regie.pk, primary_campaign.pk),
        status=404,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, primary_campaign.pk, agenda1.pk),
        status=404,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, primary_campaign.pk, agenda2.pk),
        status=404,
    )

    primary_campaign.finalized = True
    primary_campaign.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, primary_campaign.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/add-corrective-campaign/' % (regie.pk, primary_campaign.pk)
        in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agendas/add/' % (regie.pk, primary_campaign.pk)
        in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, primary_campaign.pk, agenda1.pk)
        in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, primary_campaign.pk, agenda2.pk)
        in resp
    )
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/add-corrective-campaign/' % (regie.pk, primary_campaign.pk)
    )
    assert resp.form['agendas'].options == [
        ('', True, '---------'),
        (str(agenda1.pk), False, 'Foo bar 1'),
        (str(agenda2.pk), False, 'Foo bar 2'),
    ]
    resp.form.get('agendas', 0).value = agenda1.pk
    resp = resp.form.submit()
    corrective_campaign1 = Campaign.objects.latest('pk')
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/campaign/%s/#open:pools' % (regie.pk, corrective_campaign1.pk)
    )
    assert corrective_campaign1.label == 'My campaign - Correction'
    assert corrective_campaign1.regie == regie
    assert corrective_campaign1.date_start == datetime.date(2022, 9, 1)
    assert corrective_campaign1.date_end == datetime.date(2022, 10, 1)
    assert corrective_campaign1.date_publication == datetime.date(2022, 10, 1)
    assert corrective_campaign1.date_payment_deadline == datetime.date(2022, 10, 31)
    assert corrective_campaign1.date_due == now().date()
    assert corrective_campaign1.date_debit == datetime.date(2022, 11, 15)
    assert corrective_campaign1.injected_lines == 'all'
    assert corrective_campaign1.adjustment_campaign is True
    assert corrective_campaign1.invalid is False
    assert corrective_campaign1.finalized is False
    assert corrective_campaign1.invoice_model == 'basic'
    assert corrective_campaign1.invoice_custom_text == 'foo bar'
    assert list(corrective_campaign1.agendas.all()) == [agenda1]
    assert (
        AgendaUnlockLog.objects.filter(campaign=primary_campaign, agenda=agenda1, active=True).exists()
        is False
    )
    assert (
        AgendaUnlockLog.objects.filter(campaign=primary_campaign, agenda=agenda2, active=True).exists()
        is True
    )
    assert (
        AgendaUnlockLog.objects.filter(campaign=primary_campaign, agenda=agenda3, active=True).exists()
        is True
    )
    new_updated_at = AgendaUnlockLog.objects.values_list('updated_at', flat=True).order_by('created_at')
    for i, (old_value, new_value) in enumerate(zip(old_updated_at, new_updated_at)):
        if i > 0:
            assert old_value == new_value
        else:
            assert old_value < new_value

    # change corrective_campaign1 dates
    corrective_campaign1.date_publication = datetime.date(2022, 11, 1)
    corrective_campaign1.date_payment_deadline = datetime.date(2022, 11, 30)
    corrective_campaign1.date_due = datetime.date(2022, 12, 15)
    corrective_campaign1.date_debit = datetime.date(2022, 12, 15)
    corrective_campaign1.save()

    # not possible now from primary campaign
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, primary_campaign.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/add-corrective-campaign/' % (regie.pk, primary_campaign.pk)
        not in resp
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/add-corrective-campaign/' % (regie.pk, primary_campaign.pk),
        status=404,
    )

    # corrective_campaign1 is not finalized
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, corrective_campaign1.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/add-corrective-campaign/'
        % (regie.pk, corrective_campaign1.pk)
        not in resp
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/add-corrective-campaign/'
        % (regie.pk, corrective_campaign1.pk),
        status=404,
    )

    # corrective_campaign1 finalized
    def add_agenda(resp):
        select = copy.copy(resp.form.fields['agendas'][0])
        select.id = 'id_agendas_1'
        resp.form.fields['agendas'].append(select)
        resp.form.field_order.append(('agendas', select))

    corrective_campaign1.finalized = True
    corrective_campaign1.save()
    resp = app.get(
        '/manage/invoicing/regie/%s/campaign/%s/add-corrective-campaign/'
        % (regie.pk, corrective_campaign1.pk)
    )
    assert resp.form['agendas'].options == [
        ('', True, '---------'),
        (str(agenda1.pk), False, 'Foo bar 1'),
        (str(agenda2.pk), False, 'Foo bar 2'),
    ]
    add_agenda(resp)
    resp.form.get('agendas', 0).value = agenda1.pk
    resp.form.get('agendas', 1).value = agenda2.pk
    resp = resp.form.submit()
    corrective_campaign2 = Campaign.objects.latest('pk')
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/campaign/%s/#open:pools' % (regie.pk, corrective_campaign2.pk)
    )
    assert corrective_campaign2.label == 'My campaign - Correction'
    assert corrective_campaign2.regie == regie
    assert corrective_campaign2.date_start == datetime.date(2022, 9, 1)
    assert corrective_campaign2.date_end == datetime.date(2022, 10, 1)
    assert corrective_campaign2.date_publication == datetime.date(2022, 11, 1)
    assert corrective_campaign2.date_payment_deadline == datetime.date(2022, 11, 30)
    assert corrective_campaign2.date_due == datetime.date(2022, 12, 15)
    assert corrective_campaign2.date_debit == datetime.date(2022, 12, 15)
    assert corrective_campaign2.injected_lines == 'all'
    assert corrective_campaign2.adjustment_campaign is True
    assert corrective_campaign2.invalid is False
    assert corrective_campaign2.finalized is False
    assert corrective_campaign2.invoice_model == 'basic'
    assert corrective_campaign2.invoice_custom_text == 'foo bar'
    assert set(corrective_campaign2.agendas.all()) == set(primary_campaign.agendas.all())
    assert (
        AgendaUnlockLog.objects.filter(campaign=primary_campaign, agenda=agenda1, active=True).exists()
        is False
    )
    assert (
        AgendaUnlockLog.objects.filter(campaign=primary_campaign, agenda=agenda2, active=True).exists()
        is False
    )
    assert (
        AgendaUnlockLog.objects.filter(campaign=primary_campaign, agenda=agenda3, active=True).exists()
        is True
    )
    new_updated_at = AgendaUnlockLog.objects.values_list('updated_at', flat=True).order_by('created_at')
    for i, (old_value, new_value) in enumerate(zip(old_updated_at, new_updated_at)):
        if i > 1:
            assert old_value == new_value
        else:
            assert old_value < new_value

    # not possible now for primary campaign and previous corrective campaign
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, primary_campaign.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/add-corrective-campaign/' % (regie.pk, primary_campaign.pk)
        not in resp
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/add-corrective-campaign/' % (regie.pk, primary_campaign.pk),
        status=404,
    )
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, corrective_campaign1.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/add-corrective-campaign/'
        % (regie.pk, corrective_campaign1.pk)
        not in resp
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/add-corrective-campaign/'
        % (regie.pk, corrective_campaign1.pk),
        status=404,
    )

    # test corrective campaign creation from logs
    corrective_campaign2.finalized = True
    corrective_campaign2.save()
    AgendaUnlockLog.objects.all().delete()
    AgendaUnlockLog.objects.create(campaign=primary_campaign, agenda=agenda1)
    AgendaUnlockLog.objects.create(campaign=primary_campaign, agenda=agenda2)
    AgendaUnlockLog.objects.create(campaign=primary_campaign, agenda=agenda3)
    old_updated_at = list(AgendaUnlockLog.objects.values_list('updated_at', flat=True).order_by('created_at'))
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, primary_campaign.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agendas/add/' % (regie.pk, primary_campaign.pk)
        in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, primary_campaign.pk, agenda1.pk)
        in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, primary_campaign.pk, agenda2.pk)
        in resp
    )

    resp = resp.click(
        href='/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, primary_campaign.pk, agenda1.pk)
    )
    resp = resp.form.submit()
    corrective_campaign3 = Campaign.objects.latest('pk')
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/campaign/%s/#open:pools' % (regie.pk, corrective_campaign3.pk)
    )
    assert corrective_campaign3.label == 'My campaign - Correction'
    assert corrective_campaign3.regie == regie
    assert corrective_campaign3.date_start == datetime.date(2022, 9, 1)
    assert corrective_campaign3.date_end == datetime.date(2022, 10, 1)
    assert corrective_campaign3.date_publication == datetime.date(2022, 11, 1)
    assert corrective_campaign3.date_payment_deadline == datetime.date(2022, 11, 30)
    assert corrective_campaign3.date_due == datetime.date(2022, 12, 15)
    assert corrective_campaign3.date_debit == datetime.date(2022, 12, 15)
    assert corrective_campaign3.injected_lines == 'all'
    assert corrective_campaign3.adjustment_campaign is True
    assert corrective_campaign3.invalid is False
    assert corrective_campaign3.finalized is False
    assert corrective_campaign3.invoice_model == 'basic'
    assert corrective_campaign3.invoice_custom_text == 'foo bar'
    assert set(corrective_campaign3.agendas.all()) == {agenda1}
    assert (
        AgendaUnlockLog.objects.filter(campaign=primary_campaign, agenda=agenda1, active=True).exists()
        is False
    )
    assert (
        AgendaUnlockLog.objects.filter(campaign=primary_campaign, agenda=agenda2, active=True).exists()
        is True
    )
    assert (
        AgendaUnlockLog.objects.filter(campaign=primary_campaign, agenda=agenda3, active=True).exists()
        is True
    )
    new_updated_at = AgendaUnlockLog.objects.values_list('updated_at', flat=True).order_by('created_at')
    for i, (old_value, new_value) in enumerate(zip(old_updated_at, new_updated_at)):
        if i > 0:
            assert old_value == new_value
        else:
            assert old_value < new_value

    corrective_campaign3.finalized = True
    corrective_campaign3.save()
    AgendaUnlockLog.objects.all().delete()
    AgendaUnlockLog.objects.create(campaign=primary_campaign, agenda=agenda1)
    AgendaUnlockLog.objects.create(campaign=primary_campaign, agenda=agenda2)
    AgendaUnlockLog.objects.create(campaign=primary_campaign, agenda=agenda3)
    old_updated_at = list(AgendaUnlockLog.objects.values_list('updated_at', flat=True).order_by('created_at'))
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, primary_campaign.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agendas/add/' % (regie.pk, primary_campaign.pk)
        in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, primary_campaign.pk, agenda1.pk)
        in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, primary_campaign.pk, agenda2.pk)
        in resp
    )

    resp = resp.click(
        href='/manage/invoicing/regie/%s/campaign/%s/corrective/agendas/add/'
        % (regie.pk, primary_campaign.pk)
    )
    resp = resp.form.submit()
    corrective_campaign4 = Campaign.objects.latest('pk')
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/campaign/%s/#open:pools' % (regie.pk, corrective_campaign4.pk)
    )
    assert corrective_campaign4.label == 'My campaign - Correction'
    assert corrective_campaign4.regie == regie
    assert corrective_campaign4.date_start == datetime.date(2022, 9, 1)
    assert corrective_campaign4.date_end == datetime.date(2022, 10, 1)
    assert corrective_campaign4.date_publication == datetime.date(2022, 11, 1)
    assert corrective_campaign4.date_payment_deadline == datetime.date(2022, 11, 30)
    assert corrective_campaign4.date_due == datetime.date(2022, 12, 15)
    assert corrective_campaign4.date_debit == datetime.date(2022, 12, 15)
    assert corrective_campaign4.injected_lines == 'all'
    assert corrective_campaign4.adjustment_campaign is True
    assert corrective_campaign4.invalid is False
    assert corrective_campaign4.finalized is False
    assert corrective_campaign4.invoice_model == 'basic'
    assert corrective_campaign4.invoice_custom_text == 'foo bar'
    assert set(corrective_campaign4.agendas.all()) == {agenda1, agenda2}
    assert (
        AgendaUnlockLog.objects.filter(campaign=primary_campaign, agenda=agenda1, active=True).exists()
        is False
    )
    assert (
        AgendaUnlockLog.objects.filter(campaign=primary_campaign, agenda=agenda2, active=True).exists()
        is False
    )
    assert (
        AgendaUnlockLog.objects.filter(campaign=primary_campaign, agenda=agenda3, active=True).exists()
        is True
    )
    new_updated_at = AgendaUnlockLog.objects.values_list('updated_at', flat=True).order_by('created_at')
    for i, (old_value, new_value) in enumerate(zip(old_updated_at, new_updated_at)):
        if i > 1:
            assert old_value == new_value
        else:
            assert old_value < new_value


def test_edit_corrective_campaign(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    agenda1 = Agenda.objects.create(label='Foo bar 1', regie=regie)
    agenda2 = Agenda.objects.create(label='Foo bar 2', regie=regie)
    agenda3 = Agenda.objects.create(label='Foo bar 3', regie=regie)
    primary_campaign = Campaign.objects.create(
        label='My campaign',
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        date_debit=datetime.date(2022, 11, 15),
        injected_lines='all',
        adjustment_campaign=True,
        finalized=True,
        invoice_model='basic',
        invoice_custom_text='foo bar',
    )
    corrective_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        primary_campaign=primary_campaign,
    )
    primary_campaign.agendas.add(agenda1, agenda2)
    AgendaUnlockLog.objects.create(campaign=primary_campaign, agenda=agenda1)
    AgendaUnlockLog.objects.create(campaign=primary_campaign, agenda=agenda2)
    AgendaUnlockLog.objects.create(campaign=primary_campaign, agenda=agenda3)
    old_updated_at = list(AgendaUnlockLog.objects.values_list('updated_at', flat=True).order_by('created_at'))

    app = login(app)

    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, primary_campaign.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agendas/add/' % (regie.pk, primary_campaign.pk)
        in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, primary_campaign.pk, agenda1.pk)
        in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, primary_campaign.pk, agenda2.pk)
        in resp
    )

    resp = resp.click(
        href='/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, primary_campaign.pk, agenda1.pk)
    )
    resp = resp.form.submit()
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/campaign/%s/#open:pools' % (regie.pk, corrective_campaign.pk)
    )
    corrective_campaign.refresh_from_db()
    assert corrective_campaign.invalid is True
    assert set(corrective_campaign.agendas.all()) == {agenda1}
    assert (
        AgendaUnlockLog.objects.filter(campaign=primary_campaign, agenda=agenda1, active=True).exists()
        is False
    )
    assert (
        AgendaUnlockLog.objects.filter(campaign=primary_campaign, agenda=agenda2, active=True).exists()
        is True
    )
    assert (
        AgendaUnlockLog.objects.filter(campaign=primary_campaign, agenda=agenda3, active=True).exists()
        is True
    )
    new_updated_at = AgendaUnlockLog.objects.values_list('updated_at', flat=True).order_by('created_at')
    for i, (old_value, new_value) in enumerate(zip(old_updated_at, new_updated_at)):
        if i > 0:
            assert old_value == new_value
        else:
            assert old_value < new_value

    corrective_campaign.invalid = False
    corrective_campaign.save()

    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, primary_campaign.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agendas/add/' % (regie.pk, primary_campaign.pk)
        in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, primary_campaign.pk, agenda1.pk)
        not in resp
    )
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, primary_campaign.pk, agenda2.pk)
        in resp
    )

    resp = resp.click(
        href='/manage/invoicing/regie/%s/campaign/%s/corrective/agendas/add/'
        % (regie.pk, primary_campaign.pk)
    )
    resp = resp.form.submit()
    assert resp.location.endswith(
        '/manage/invoicing/regie/%s/campaign/%s/#open:pools' % (regie.pk, corrective_campaign.pk)
    )
    corrective_campaign.refresh_from_db()
    assert corrective_campaign.invalid is True
    assert set(corrective_campaign.agendas.all()) == {agenda1, agenda2}
    assert (
        AgendaUnlockLog.objects.filter(campaign=primary_campaign, agenda=agenda1, active=True).exists()
        is False
    )
    assert (
        AgendaUnlockLog.objects.filter(campaign=primary_campaign, agenda=agenda2, active=True).exists()
        is False
    )
    assert (
        AgendaUnlockLog.objects.filter(campaign=primary_campaign, agenda=agenda3, active=True).exists()
        is True
    )
    new_updated_at = AgendaUnlockLog.objects.values_list('updated_at', flat=True).order_by('created_at')
    for i, (old_value, new_value) in enumerate(zip(old_updated_at, new_updated_at)):
        if i > 1:
            assert old_value == new_value
        else:
            assert old_value < new_value

    Pool.objects.create(
        campaign=corrective_campaign,
        draft=False,
        status='completed',
    )
    AgendaUnlockLog.objects.create(campaign=primary_campaign, agenda=agenda1)
    AgendaUnlockLog.objects.create(campaign=primary_campaign, agenda=agenda2)
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, primary_campaign.pk))
    urls = [
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agendas/add/' % (regie.pk, primary_campaign.pk),
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, primary_campaign.pk, agenda1.pk),
        '/manage/invoicing/regie/%s/campaign/%s/corrective/agenda/%s/add/'
        % (regie.pk, primary_campaign.pk, agenda2.pk),
    ]
    for url in urls:
        assert url in resp
        resp2 = app.get(url)
        assert resp2.location.endswith(
            '/manage/invoicing/regie/%s/campaign/%s/#open:pools' % (regie.pk, corrective_campaign.pk)
        )
        resp2 = resp2.follow()
        assert (
            '<li class="error">Not possible to update current corrective campaign, invoices have been generated.</li>'
            in resp2
        )


def test_campaign_event_amounts_ods(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    group1 = CheckTypeGroup.objects.create(label='bar')
    CheckType.objects.create(label='Foo', group=group1, kind='presence')
    group2 = CheckTypeGroup.objects.create(label='baz')
    CheckType.objects.create(label='Foo', code='FOO', group=group2, kind='presence')
    agenda1 = Agenda.objects.create(label='agenda-1', regie=regie, check_type_group=group1)
    agenda2 = Agenda.objects.create(label='agenda-2', regie=regie, check_type_group=group2)
    campaign = Campaign.objects.create(
        label='My campaign',
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        date_debit=datetime.date(2022, 11, 15),
        finalized=True,
    )
    campaign.agendas.add(agenda1, agenda2)
    pool = Pool.objects.create(
        campaign=campaign,
        draft=False,
    )
    JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        event={'agenda': 'agenda-1'},
        amount=0,
        user_external_id='user:1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='ici',
        status='error',  # error, not reported
        pricing_data={
            'booking_details': {'check_type': 'foo', 'check_type_group': 'bar', 'status': 'presence'},
            'pricing': '1.42',
        },
        booking={'extra_data': {'classe': 'CM1', 'age': '7'}},
        pool=pool,
    )
    JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 1),
        event={'agenda': 'agenda-1'},
        amount=1,
        user_external_id='user:1',
        user_first_name='UserFirst1',
        user_last_name='UserLast1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='ici',
        payer_email='email',
        payer_phone='phone',
        status='success',
        pricing_data={
            'booking_details': {'check_type': 'foo', 'check_type_group': 'bar', 'status': 'presence'},
            'pricing': '1.42',
        },
        booking={'extra_data': {'classe': 'CM1', 'age': '7'}},
        pool=pool,
    )
    JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 2),
        event={'agenda': 'agenda-1'},
        amount=1,
        user_external_id='user:1',
        user_first_name='UserFirst1',
        user_last_name='UserLast1',
        payer_external_id='payer:1',
        payer_first_name='First1',
        payer_last_name='Last1',
        payer_address='ici',
        status='success',
        pricing_data={
            'booking_details': {'check_type': None, 'check_type_group': None, 'status': 'presence'},
            'pricing': 12,
        },
        pool=pool,
    )
    JournalLine.objects.create(
        event_date=datetime.date(2022, 9, 3),
        event={'agenda': 'agenda-2'},
        amount=1,
        user_external_id='user:2',
        user_first_name='UserFirst2',
        user_last_name='UserLast2',
        payer_external_id='payer:2',
        payer_first_name='First2',
        payer_last_name='Last2',
        payer_address='ici',
        status='success',
        pricing_data={
            'booking_details': {'check_type': 'foo', 'check_type_group': 'baz', 'status': 'presence'},
            'pricing': 1,
        },
        booking={'extra_data': {'classe': 'CM1'}},
        pool=pool,
    )

    app = login(app)
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert '/manage/invoicing/regie/%s/campaign/%s/report/event-amounts/' % (regie.pk, campaign.pk) in resp
    resp = resp.click('Export invoiced amounts per event')
    resp = resp.form.submit()
    assert resp.headers['Content-Type'] == 'application/vnd.oasis.opendocument.spreadsheet'
    assert (
        resp.headers['Content-Disposition']
        == 'attachment; filename="campaign-%s-event-amounts.ods"' % campaign.pk
    )
    rows = list(get_ods_rows(resp))
    assert len(rows) == 4
    assert rows == [
        [
            'Payer external ID',
            'Payer first name',
            'Payer last name',
            'Payer address',
            'Payer email',
            'Payer phone',
            'User external ID',
            'User first name',
            'User last name',
            'Event date',
            'Activity',
            'Amount',
            'Booking status',
        ],
        [
            'payer:1',
            'First1',
            'Last1',
            'ici',
            'email',
            'phone',
            'user:1',
            'UserFirst1',
            'UserLast1',
            '09/01/2022',
            'agenda-1',
            '1.42',
            'foo',
        ],
        [
            'payer:1',
            'First1',
            'Last1',
            'ici',
            None,
            None,
            'user:1',
            'UserFirst1',
            'UserLast1',
            '09/02/2022',
            'agenda-1',
            '12',
            'P',
        ],
        [
            'payer:2',
            'First2',
            'Last2',
            'ici',
            None,
            None,
            'user:2',
            'UserFirst2',
            'UserLast2',
            '09/03/2022',
            'agenda-2',
            '1',
            'FOO',
        ],
    ]

    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/report/event-amounts/' % (regie.pk, campaign.pk))
    resp.form['extra_data_keys'] = 'classe, age'
    resp = resp.form.submit()
    assert resp.headers['Content-Type'] == 'application/vnd.oasis.opendocument.spreadsheet'
    assert (
        resp.headers['Content-Disposition']
        == 'attachment; filename="campaign-%s-event-amounts.ods"' % campaign.pk
    )
    rows = list(get_ods_rows(resp))
    assert len(rows) == 4
    assert rows == [
        [
            'Payer external ID',
            'Payer first name',
            'Payer last name',
            'Payer address',
            'Payer email',
            'Payer phone',
            'User external ID',
            'User first name',
            'User last name',
            'Event date',
            'Activity',
            'classe',
            'age',
            'Amount',
            'Booking status',
        ],
        [
            'payer:1',
            'First1',
            'Last1',
            'ici',
            'email',
            'phone',
            'user:1',
            'UserFirst1',
            'UserLast1',
            '09/01/2022',
            'agenda-1',
            'CM1',
            '7',
            '1.42',
            'foo',
        ],
        [
            'payer:1',
            'First1',
            'Last1',
            'ici',
            None,
            None,
            'user:1',
            'UserFirst1',
            'UserLast1',
            '09/02/2022',
            'agenda-1',
            None,
            None,
            '12',
            'P',
        ],
        [
            'payer:2',
            'First2',
            'Last2',
            'ici',
            None,
            None,
            'user:2',
            'UserFirst2',
            'UserLast2',
            '09/03/2022',
            'agenda-2',
            'CM1',
            None,
            '1',
            'FOO',
        ],
    ]

    # campaign not finalized
    campaign.finalized = False
    campaign.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/report/event-amounts/' % (regie.pk, campaign.pk) not in resp
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/report/event-amounts/' % (regie.pk, campaign.pk), status=404
    )

    # campaign finalized but a corrective campaign is not finalized
    campaign.finalized = True
    campaign.save()
    corrective_campaign = Campaign.objects.create(
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=datetime.date(2022, 10, 31),
        date_debit=datetime.date(2022, 11, 15),
        primary_campaign=campaign,
    )
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/report/event-amounts/' % (regie.pk, campaign.pk) not in resp
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/report/event-amounts/' % (regie.pk, campaign.pk), status=404
    )

    # corrective campaign is finalized, but not possible to run export from it
    corrective_campaign.finalized = True
    corrective_campaign.save()
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, corrective_campaign.pk))
    assert (
        '/manage/invoicing/regie/%s/campaign/%s/report/event-amounts/' % (regie.pk, corrective_campaign.pk)
        not in resp
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/report/event-amounts/' % (regie.pk, corrective_campaign.pk),
        status=404,
    )

    # ok from primary campaign
    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/' % (regie.pk, campaign.pk))
    assert '/manage/invoicing/regie/%s/campaign/%s/report/event-amounts/' % (regie.pk, campaign.pk) in resp
    app.get('/manage/invoicing/regie/%s/campaign/%s/report/event-amounts/' % (regie.pk, campaign.pk))

    app.get('/manage/invoicing/regie/%s/campaign/%s/report/event-amounts/' % (0, campaign.pk), status=404)
    app.get('/manage/invoicing/regie/%s/campaign/%s/report/event-amounts/' % (regie.pk, 0), status=404)
    other_regie = Regie.objects.create(label='Other-Foo')
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/report/event-amounts/' % (other_regie.pk, campaign.pk),
        status=404,
    )


def test_job_detail(app, admin_user):
    regie = Regie.objects.create(label='Foo')
    campaign = Campaign.objects.create(
        label='My campaign',
        regie=regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        date_debit=datetime.date(2022, 11, 15),
    )
    pool = Pool.objects.create(
        campaign=campaign,
    )
    job = CampaignAsyncJob.objects.create(
        campaign=campaign, status='completed', action='generate', total_count=4, current_count=4
    )
    pjob1 = PoolAsyncJob.objects.create(
        pool=pool,
        status='running',
        action='generate_invoices',
        total_count=300,
        current_count=42,
        campaign_job=job,
    )
    PoolAsyncJob.objects.create(
        pool=pool,
        status='running',
        action='generate_invoices',
        total_count=299,
        current_count=42,
        campaign_job=job,
    )
    PoolAsyncJob.objects.create(pool=pool, status='registered', action='finalize_invoices', campaign_job=job)

    app = login(app)

    resp = app.get('/manage/invoicing/regie/%s/campaign/%s/job/%s/' % (regie.pk, campaign.pk, job.uuid))
    assert (
        '<h2>Job &quot;Invoices generation preparation&quot; started on %s</h2>'
        % date_format(localtime(job.creation_timestamp), 'DATETIME_FORMAT')
        in resp
    )
    assert len(resp.pyquery('dl')) == 4
    assert [PyQuery(dt).text() for dt in PyQuery(resp.pyquery('dl')[0]).find('dt')] == [
        'Invoices generation preparation'
    ]
    assert [PyQuery(dd).text() for dd in PyQuery(resp.pyquery('dl')[0]).find('dd')] == [
        'Completed: 4/4 (100%)'
    ]
    assert [PyQuery(dt).text() for dt in PyQuery(resp.pyquery('dl')[1]).find('dt')] == [
        'Invoice lines generation'
    ]
    assert [PyQuery(dd).text() for dd in PyQuery(resp.pyquery('dl')[1]).find('dd')] == [
        'Running: 42/300 (14%)'
    ]
    assert [PyQuery(dt).text() for dt in PyQuery(resp.pyquery('dl')[2]).find('dt')] == [
        'Invoice lines generation'
    ]
    assert [PyQuery(dd).text() for dd in PyQuery(resp.pyquery('dl')[2]).find('dd')] == [
        'Running: 42/299 (14%)'
    ]
    assert [PyQuery(dt).text() for dt in PyQuery(resp.pyquery('dl')[3]).find('dt')] == [
        'Invoices finalization'
    ]
    assert [PyQuery(dd).text() for dd in PyQuery(resp.pyquery('dl')[3]).find('dd')] == ['Registered:']

    app.get('/manage/invoicing/regie/%s/campaign/%s/job/%s/' % (0, campaign.pk, job.uuid), status=404)
    app.get('/manage/invoicing/regie/%s/campaign/%s/job/%s/' % (regie.pk, 0, job.uuid), status=404)
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/job/%s/' % (regie.pk, campaign.pk, uuid.uuid4()), status=404
    )

    other_regie = Regie.objects.create(label='Foo')
    other_campaign = Campaign.objects.create(
        label='My campaign',
        regie=other_regie,
        date_start=datetime.date(2022, 9, 1),
        date_end=datetime.date(2022, 10, 1),
        date_publication=datetime.date(2022, 10, 1),
        date_payment_deadline=datetime.date(2022, 10, 31),
        date_due=now().date(),
        date_debit=datetime.date(2022, 11, 15),
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/job/%s/' % (other_regie.pk, other_campaign.pk, job.uuid),
        status=404,
    )
    app.get(
        '/manage/invoicing/regie/%s/campaign/%s/job/%s/' % (regie.pk, campaign.pk, pjob1.uuid), status=404
    )

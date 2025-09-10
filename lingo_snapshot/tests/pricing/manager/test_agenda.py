from unittest import mock

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from lingo.agendas.models import Agenda, CheckTypeGroup
from lingo.invoicing.models import Regie
from lingo.snapshot.models import AgendaSnapshot
from tests.utils import login

pytestmark = pytest.mark.django_db


@mock.patch('lingo.pricing.views.refresh_agendas')
def test_refresh_agendas(mock_refresh, app, admin_user):
    app = login(app)
    resp = app.get('/manage/pricing/agendas/')
    resp = resp.click('Refresh agendas')
    assert resp.location.endswith('/manage/pricing/agendas/')
    resp = resp.follow()
    assert 'Agendas refreshed.' in resp
    assert mock_refresh.call_args_list == [mock.call()]


def test_agenda_list(app, admin_user):
    agenda = Agenda.objects.create(label='Foo bar')
    archived = Agenda.objects.create(label='Foo bar', archived=True)

    app = login(app)
    resp = app.get('/manage/pricing/agendas/')
    assert '/manage/pricing/agenda/%s/' % agenda.pk in resp
    assert '/manage/pricing/agenda/%s/' % archived.pk not in resp

    resp = app.get('/manage/pricing/agendas/archived/')
    assert '/manage/pricing/agenda/%s/' % agenda.pk not in resp
    assert '/manage/pricing/agenda/%s/' % archived.pk in resp


def test_agenda_chrono_link(settings, app, admin_user):
    settings.KNOWN_SERVICES = {}
    agenda = Agenda.objects.create(label='Foo bar')

    app = login(app)
    resp = app.get('/manage/pricing/agendas/')
    assert '/manage/agendas/%s/settings/' % agenda.slug not in resp
    resp = app.get('/manage/pricing/agenda/%s/' % agenda.pk)
    assert 'Agenda options' not in resp
    assert '/manage/agendas/%s/settings/' % agenda.slug not in resp

    settings.KNOWN_SERVICES['chrono'] = {'default': {'url': 'https://chrono.dev/'}}
    resp = app.get('/manage/pricing/agendas/')
    assert 'https://chrono.dev/manage/agendas/%s/settings/' % agenda.slug in resp
    resp = app.get('/manage/pricing/agenda/%s/' % agenda.pk)
    assert 'Agenda options' in resp
    assert 'https://chrono.dev/manage/agendas/%s/settings/' % agenda.slug in resp


def test_detail_agenda_redirect(app, admin_user):
    agenda = Agenda.objects.create(label='Foo Bar')

    app = login(app)
    resp = app.get('/manage/pricing/agenda/%s/' % agenda.slug, status=302)
    assert resp.location.endswith('/manage/pricing/agenda/%s/' % agenda.pk)


def test_edit_agenda_check_type_group(app, admin_user):
    agenda = Agenda.objects.create(label='Foo bar')
    group = CheckTypeGroup.objects.create(label='Foo bar')

    app = login(app)
    resp = app.get('/manage/pricing/agenda/%s/' % agenda.pk)
    assert 'No check types configured for this agenda.' in resp
    resp = resp.click(href='/manage/pricing/agenda/%s/check-options' % agenda.pk)
    resp.form['check_type_group'] = group.pk
    resp = resp.form.submit().follow()
    agenda.refresh_from_db()
    assert agenda.check_type_group == group
    assert 'Check type group: Foo bar' in resp
    assert AgendaSnapshot.objects.count() == 1


def test_edit_agenda_invoicing_settings(app, admin_user):
    agenda = Agenda.objects.create(label='Foo bar')
    regie = Regie.objects.create(label='Foo bar')

    app = login(app)
    resp = app.get('/manage/pricing/agenda/%s/' % agenda.pk)
    resp = resp.click(href='/manage/pricing/agenda/%s/invoicing-options' % agenda.pk)
    resp.form['regie'] = regie.pk
    resp = resp.form.submit().follow()
    agenda.refresh_from_db()
    assert agenda.regie == regie
    assert 'Regie: <a href="/manage/invoicing/regie/%s/">Foo bar</a>' % regie.pk in resp
    assert AgendaSnapshot.objects.count() == 1


def test_agenda_inspect(app, admin_user):
    group = CheckTypeGroup.objects.create(label='Foo bar')
    regie = Regie.objects.create(
        label='Regie',
    )
    agenda = Agenda.objects.create(label='Foo bar', check_type_group=group, regie=regie)

    app = login(app)
    resp = app.get('/manage/pricing/agenda/%s/' % agenda.pk)
    with CaptureQueriesContext(connection) as ctx:
        resp = resp.click(href='/manage/pricing/agenda/%s/inspect/' % agenda.pk)
        assert len(ctx.captured_queries) == 5

import datetime

import pytest

from wcs.carddef import CardDef
from wcs.fields import StringField
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.http_request import HTTPRequest
from wcs.sql_criterias import Null
from wcs.workflows import Workflow

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_superuser


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()
    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()
    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_studio_home(pub):
    create_superuser(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/studio/')
    assert 'Recent errors' in resp.text


def test_listing_paginations(pub):
    FormDef.wipe()
    CardDef.wipe()
    Workflow.wipe()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.store()
    formdef2 = FormDef()
    formdef2.name = 'foo 2'
    formdef2.store()
    carddef = CardDef()
    carddef.name = 'bar'
    carddef.store()
    carddef2 = CardDef()
    carddef2.name = 'bar 2'
    carddef2.store()
    workflow = Workflow()
    workflow.name = 'blah'
    workflow.store()
    workflow2 = Workflow()
    workflow2.name = 'blah 2'
    workflow2.store()

    # FormDef errors
    for i in range(0, 21):
        error = LoggedError()
        error.summary = 'FormDef Workflow Logged Error n°%s' % i
        error.formdef_class = 'FormDef'
        error.formdef_id = formdef.id
        error.workflow_id = workflow.id
        error.first_occurence_timestamp = datetime.datetime.now()
        error.latest_occurence_timestamp = datetime.datetime.now()
        error.store()
    error = LoggedError()
    error.summary = 'FormDef 2 Workflow 2 Logged Error n°%s' % i
    error.formdef_class = 'FormDef'
    error.formdef_id = formdef2.id
    error.workflow_id = workflow2.id
    error.first_occurence_timestamp = datetime.datetime.now()
    error.latest_occurence_timestamp = datetime.datetime.now()
    error.store()

    # CardDef errors
    for i in range(0, 21):
        error = LoggedError()
        error.summary = 'CardDef Workflow Logged Error n°%s' % i
        error.formdef_class = 'CardDef'
        error.formdef_id = carddef.id
        error.workflow_id = workflow.id
        error.first_occurence_timestamp = datetime.datetime.now()
        error.latest_occurence_timestamp = datetime.datetime.now()
        error.store()
    error = LoggedError()
    error.summary = 'CardDef 2 Workflow 2 Logged Error n°%s' % i
    error.formdef_class = 'CardDef'
    error.formdef_id = carddef2.id
    error.workflow_id = workflow2.id
    error.first_occurence_timestamp = datetime.datetime.now()
    error.latest_occurence_timestamp = datetime.datetime.now()
    error.store()

    # workflow-only errors
    for i in range(0, 21):
        error = LoggedError()
        error.summary = 'Workflow Logged Error n°%s' % i
        error.workflow_id = workflow.id
        error.first_occurence_timestamp = datetime.datetime.now()
        error.latest_occurence_timestamp = datetime.datetime.now()
        error.store()
    error = LoggedError()
    error.summary = 'Workflow 2 Logged Error n°%s' % i
    error.workflow_id = workflow2.id
    error.first_occurence_timestamp = datetime.datetime.now()
    error.latest_occurence_timestamp = datetime.datetime.now()
    error.store()

    # standalone error
    error = LoggedError()
    error.summary = 'Lonely Logged Error'
    error.exception_class = 'Exception'
    error.exception_message = 'foo bar'
    error.first_occurence_timestamp = datetime.datetime.now()
    error.latest_occurence_timestamp = datetime.datetime.now()
    error.occurences_count = 17654032
    error.store()

    create_superuser(pub)
    app = login(get_app(pub))

    # all errors

    # default pagination
    resp = app.get('/backoffice/studio/logged-errors/')
    assert '1-20/67' in resp.text
    assert resp.text.count('Lonely Logged Error') == 1
    assert resp.pyquery('.extra-info').text() == 'Exception (foo bar)'
    assert '<span class="badge">17,654,032</span>' in resp.text
    assert resp.text.count('Logged Error n°') == 19
    resp = resp.click(href=r'\?offset=60')
    assert '61-67/67' in resp.text
    assert resp.text.count('Logged Error n°') == 7

    # change pagination
    resp = app.get('/backoffice/studio/logged-errors/?offset=0&limit=50')
    assert '1-50/67' in resp.text
    assert resp.text.count('Lonely Logged Error') == 1
    assert resp.text.count('Logged Error n°') == 49
    resp = resp.click('<!--Next Page-->')
    assert '51-67/67' in resp.text
    assert resp.text.count('Logged Error n°') == 17

    # formdef errors
    resp = app.get('/backoffice/forms/%s/' % formdef.id)
    assert '21 errors' in resp
    resp = app.get('/backoffice/forms/%s/logged-errors/' % formdef.id)
    assert '1-20/21' in resp.text
    assert resp.text.count('FormDef Workflow Logged Error n°') == 20
    resp = resp.click('<!--Next Page-->')
    assert '21-21/21' in resp.text
    assert resp.text.count('FormDef Workflow Logged Error n°') == 1

    # carddef errors
    resp = app.get('/backoffice/cards/%s/' % carddef.id)
    assert '21 errors' in resp
    resp = app.get('/backoffice/cards/%s/logged-errors/' % carddef.id)
    assert '1-20/21' in resp.text
    assert resp.text.count('CardDef Workflow Logged Error n°') == 20
    resp = resp.click('<!--Next Page-->')
    assert '21-21/21' in resp.text
    assert resp.text.count('CardDef Workflow Logged Error n°') == 1

    # workflows errors
    resp = app.get('/backoffice/workflows/%s/' % workflow.id)
    assert '63 errors' in resp
    resp = app.get('/backoffice/workflows/%s/logged-errors/' % workflow.id)
    assert '1-20/63' in resp.text
    assert resp.text.count('Workflow Logged Error n°') == 20
    resp = resp.click(href=r'\?offset=60')
    assert '61-63/63' in resp.text
    assert resp.text.count('Workflow Logged Error n°') == 3

    # search
    resp = app.get('/backoffice/studio/logged-errors/')
    assert '1-20/67' in resp.text
    resp.form['q'] = 'Lonely'
    resp = resp.form.submit('submit')
    assert resp.pyquery('.displayed-range').text() == '(1-1/1)'
    assert resp.pyquery('#page-links a.current').attr.href == '?q=Lonely&offset=0'


def test_backoffice_access(pub):
    FormDef.wipe()
    CardDef.wipe()
    Workflow.wipe()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.store()
    carddef = CardDef()
    carddef.name = 'bar'
    carddef.store()
    workflow = Workflow()
    workflow.name = 'blah'
    workflow.store()

    # FormDef error
    error1 = LoggedError()
    error1.summary = 'LoggedError'
    error1.formdef_class = 'FormDef'
    error1.formdef_id = formdef.id
    error1.workflow_id = workflow.id
    error1.first_occurence_timestamp = datetime.datetime.now()
    error1.store()

    # CardDef error
    error2 = LoggedError()
    error2.summary = 'LoggedError'
    error2.formdef_class = 'CardDef'
    error2.formdef_id = carddef.id
    error2.workflow_id = workflow.id
    error2.first_occurence_timestamp = datetime.datetime.now()
    error2.store()

    # workflow-only error
    error3 = LoggedError()
    error3.summary = 'LoggedError'
    error3.workflow_id = workflow.id
    error3.first_occurence_timestamp = datetime.datetime.now()
    error3.store()

    create_superuser(pub)
    app = login(get_app(pub))

    # check section link are not displayed if user has no access right

    # formdefs are not accessible to current user
    pub.cfg['admin-permissions'] = {'forms': ['X']}
    pub.write_cfg()
    resp = app.get('/backoffice/studio/logged-errors/')
    assert resp.text.count('LoggedError') == 2
    assert '<a href="%s/">' % error1.id not in resp.text
    assert '<a href="%s/">' % error2.id in resp.text
    assert '<a href="%s/">' % error3.id in resp.text

    # carddefs are not accessible to current user
    pub.cfg['admin-permissions'] = {'cards': ['X']}
    pub.write_cfg()
    resp = app.get('/backoffice/studio/logged-errors/')
    assert resp.text.count('LoggedError') == 2
    assert '<a href="%s/">' % error1.id in resp.text
    assert '<a href="%s/">' % error2.id not in resp.text
    assert '<a href="%s/">' % error3.id in resp.text

    # workflows are not accessible to current user
    pub.cfg['admin-permissions'] = {'workflows': ['X']}
    pub.write_cfg()
    resp = app.get('/backoffice/studio/logged-errors/')
    assert resp.text.count('LoggedError') == 2
    assert '<a href="%s/">' % error1.id in resp.text
    assert '<a href="%s/">' % error2.id in resp.text
    assert '<a href="%s/">' % error3.id not in resp.text

    # mix formdefs & workflows
    pub.cfg['admin-permissions'] = {'forms': ['X'], 'workflows': ['X']}
    pub.write_cfg()
    resp = app.get('/backoffice/studio/logged-errors/')
    assert resp.text.count('LoggedError') == 1
    assert '<a href="%s/">' % error1.id not in resp.text
    assert '<a href="%s/">' % error2.id in resp.text
    assert '<a href="%s/">' % error3.id not in resp.text

    # mix all
    pub.cfg['admin-permissions'] = {'forms': ['X'], 'cards': ['X'], 'workflows': ['X']}
    pub.write_cfg()
    resp = app.get('/backoffice/studio/logged-errors/', status=403)


def test_logged_error_404(pub):
    create_superuser(pub)
    app = login(get_app(pub))

    # check non-existent id
    app.get('/backoffice/studio/logged-errors/1', status=404)

    # check invalid (non-integer) id
    app.get('/backoffice/studio/logged-errors/null', status=404)


def test_logged_error_trace(pub):
    create_superuser(pub)
    app = login(get_app(pub))

    logged_error = pub.record_error('Error')
    resp = app.get(f'/backoffice/studio/logged-errors/{logged_error.id}/')
    assert 'pub.record_error(\'Error' in resp.pyquery('.stack-trace--code')[0].text
    assert '\n  locals:' in resp.text

    try:
        raise ZeroDivisionError()
    except Exception as e:
        logged_error = pub.record_error('Exception', exception=e)

    resp = app.get(f'/backoffice/studio/logged-errors/{logged_error.id}/')
    assert 'pub.record_error(\'Exception' in resp.pyquery('.stack-trace--code')[0].text
    assert '\n  locals:' in resp.text


def test_logged_error_timings(pub):
    create_superuser(pub)
    app = login(get_app(pub))

    logged_error = pub.record_error(
        'Error',
        extra_context={
            'timings': [
                {
                    'name': 'foo',
                    'duration': 3,
                    'start': 0,
                    'timings': [{'name': 'bar', 'timestamp': 1, 'duration': 2}],
                },
            ]
        },
    )
    resp = app.get(f'/backoffice/studio/logged-errors/{logged_error.id}/')
    assert [item.text() for item in resp.pyquery('#panel-timings table td').items()] == [
        'foo',
        '0.000',
        '3.000',
        'bar',
        '1.000',
        '2.000',
    ]


def test_logged_error_cleanup(pub):
    create_superuser(pub)

    FormDef.wipe()
    CardDef.wipe()
    Workflow.wipe()
    LoggedError.wipe()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.store()
    carddef = CardDef()
    carddef.name = 'bar'
    carddef.store()
    workflow = Workflow()
    workflow.name = 'blah'
    workflow.store()

    # FormDef error
    error1 = LoggedError()
    error1.summary = 'LoggedError'
    error1.formdef_class = 'FormDef'
    error1.formdef_id = formdef.id
    error1.workflow_id = workflow.id
    error1.first_occurence_timestamp = error1.latest_occurence_timestamp = datetime.datetime.now()
    error1.store()

    # CardDef error
    error2 = LoggedError()
    error2.summary = 'LoggedError'
    error2.formdef_class = 'CardDef'
    error2.formdef_id = carddef.id
    error2.workflow_id = workflow.id
    error2.first_occurence_timestamp = error2.latest_occurence_timestamp = datetime.datetime.now()
    error2.store()

    # workflow-only error
    error3 = LoggedError()
    error3.summary = 'LoggedError'
    error3.workflow_id = workflow.id
    error3.first_occurence_timestamp = error3.latest_occurence_timestamp = datetime.datetime.now()
    error3.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/studio/logged-errors/')
    resp = resp.click('Cleanup')
    resp = resp.form.submit('submit')
    assert LoggedError().count() == 3  # nothing removed

    # check there's a form error if nothing is checked
    resp = app.get('/backoffice/studio/logged-errors/')
    resp = resp.click('Cleanup')
    resp.form['types$elementformdef'].checked = False
    resp.form['types$elementcarddef'].checked = False
    resp.form['types$elementothers'].checked = False
    resp = resp.form.submit('submit')
    assert resp.pyquery('[data-widget-name="types"].widget-with-error')

    # check cleanup of only formdef errors
    error1.first_occurence_timestamp = error1.latest_occurence_timestamp = (
        datetime.datetime.now() - datetime.timedelta(days=280)
    )
    error1.store()
    error2.first_occurence_timestamp = datetime.datetime.now() - datetime.timedelta(days=120)
    error2.latest_occurence_timestamp = datetime.datetime.now() - datetime.timedelta(days=80)
    error2.store()
    error3.first_occurence_timestamp = error3.latest_occurence_timestamp = (
        datetime.datetime.now() - datetime.timedelta(days=280)
    )
    error3.store()
    resp = app.get('/backoffice/studio/logged-errors/')
    resp = resp.click('Cleanup')
    resp.form['types$elementcarddef'].checked = False
    resp.form['types$elementothers'].checked = False
    resp = resp.form.submit('submit')
    assert {x.id for x in LoggedError().select([Null('deleted_timestamp')])} == {
        error2.id,
        error3.id,
    }

    # check cleanup latest occurence value (error2 should not be cleaned)
    resp = app.get('/backoffice/studio/logged-errors/')
    resp = resp.click('Cleanup')
    resp.form['latest_occurence'] = (datetime.datetime.now() - datetime.timedelta(days=100)).strftime(
        '%Y-%m-%d'
    )
    resp = resp.form.submit('submit')
    assert {x.id for x in LoggedError().select([Null('deleted_timestamp')])} == {error2.id}

    # check with a more recent date (error2 should be cleaned this time)
    resp = app.get('/backoffice/studio/logged-errors/')
    resp = resp.click('Cleanup')
    resp.form['latest_occurence'] = (datetime.datetime.now() - datetime.timedelta(days=10)).strftime(
        '%Y-%m-%d'
    )
    resp = resp.form.submit('submit')
    assert {x.id for x in LoggedError().select([Null('deleted_timestamp')])} == set()

    # make formdefs not accessible to current user
    pub.cfg['admin-permissions'] = {'forms': ['X']}
    pub.write_cfg()
    resp = app.get('/backoffice/studio/logged-errors/')
    resp = resp.click('Cleanup')
    assert [x.attrib['name'] for x in resp.pyquery('[type="checkbox"]')] == [
        'types$elementcarddef',
        'types$elementothers',
    ]


def test_logged_error_cleanup_from_filtered_page(pub):
    create_superuser(pub)

    FormDef.wipe()
    CardDef.wipe()
    Workflow.wipe()
    LoggedError.wipe()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.store()
    carddef = CardDef()
    carddef.name = 'bar'
    carddef.store()
    workflow = Workflow()
    workflow.name = 'blah'
    workflow.store()

    # FormDef error
    error1 = LoggedError()
    error1.summary = 'LoggedError'
    error1.formdef_class = 'FormDef'
    error1.formdef_id = formdef.id
    error1.first_occurence_timestamp = error1.latest_occurence_timestamp = datetime.datetime.now()
    error1.store()

    # CardDef error
    error2 = LoggedError()
    error2.summary = 'LoggedError'
    error2.formdef_class = 'CardDef'
    error2.formdef_id = carddef.id
    error2.first_occurence_timestamp = error2.latest_occurence_timestamp = datetime.datetime.now()
    error2.store()

    # workflow-only error
    error3 = LoggedError()
    error3.summary = 'LoggedError'
    error3.workflow_id = workflow.id
    error3.first_occurence_timestamp = error3.latest_occurence_timestamp = datetime.datetime.now()
    error3.store()

    app = login(get_app(pub))
    resp = app.get(formdef.get_admin_url() + 'logged-errors/')
    resp = resp.click('Cleanup')
    resp.form['latest_occurence'] = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime(
        '%Y-%m-%d'
    )
    resp = resp.form.submit('submit')
    assert {x.id for x in LoggedError.select([Null('deleted_timestamp')])} == {
        error2.id,
        error3.id,
    }

    resp = app.get(workflow.get_admin_url() + 'logged-errors/')
    resp = resp.click('Cleanup')
    resp.form['latest_occurence'] = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime(
        '%Y-%m-%d'
    )
    resp = resp.form.submit('submit')
    assert {x.id for x in LoggedError.select([Null('deleted_timestamp')])} == {error2.id}


def test_logged_error_badge(pub):
    create_superuser(pub)
    LoggedError.wipe()

    pub.record_error('Error')

    get_app(pub).get('/api/logged-errors-recent-count', status=403)

    app = login(get_app(pub))
    resp = app.get('/backoffice/studio/')
    assert 'wcs.logged-errors.js' in resp.text
    resp = app.get('/api/logged-errors-recent-count')
    assert resp.json == {'err': 0}

    pub.record_error('Error')
    resp = app.get('/api/logged-errors-recent-count')
    assert resp.json == {'err': 0, 'msg': '1 new error has been recorded.'}

    # visiting logged errors page will reset the count
    resp = app.get('/backoffice/studio/logged-errors/')
    resp = app.get('/api/logged-errors-recent-count')
    assert resp.json == {'err': 0}


def test_logged_error_documentation(pub):
    create_superuser(pub)
    LoggedError.wipe()

    logged_error = pub.record_error('Error')

    app = login(get_app(pub))
    resp = app.get(f'/backoffice/studio/logged-errors/{logged_error.id}/')

    assert resp.pyquery('.documentation[hidden]')
    resp = app.post_json(
        f'/backoffice/studio/logged-errors/{logged_error.id}/update-documentation', {'content': '<p>doc</p>'}
    )
    assert resp.json == {'err': 0, 'empty': False, 'changed': True}
    logged_error = LoggedError.get(logged_error.id)
    assert logged_error.documentation == '<p>doc</p>'

    resp = app.get(f'/backoffice/studio/logged-errors/{logged_error.id}/')
    assert resp.pyquery('.documentation:not([hidden])')


def test_logged_error_field_link(pub):
    create_superuser(pub)
    app = login(get_app(pub))

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [StringField(id='1', label='test')]
    formdef.store()
    formdef.refresh_from_storage()

    with pub.error_context(field_label=formdef.fields[0].label, field_url=formdef.fields[0].get_admin_url()):
        logged_error = pub.record_error('Error')

    resp = app.get(f'/backoffice/studio/logged-errors/{logged_error.id}/')
    assert resp.pyquery('.logged-error-frames--context').text() == 'Field: test'
    app.get(resp.pyquery('.logged-error-frames--context a').attr.href)

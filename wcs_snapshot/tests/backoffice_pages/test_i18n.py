import pytest

from wcs.fields import EmailField
from wcs.formdef import FormDef
from wcs.i18n import TranslatableMessage
from wcs.qommon.http_request import HTTPRequest

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_user


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def pub():
    pub = create_temporary_pub()

    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {
        'language': 'en',
        'multilinguism': True,
        'languages': ['en', 'fr'],
        'default_site_language': 'http',
    }
    pub.write_cfg()
    TranslatableMessage.do_table()  # update table with selected languages
    return pub


@pytest.fixture
def agent(pub):
    return create_user(pub)


def test_backoffice_submission_user_email(pub, agent, emails):
    formdef = FormDef()
    formdef.name = 'test form'
    formdef.fields = [
        EmailField(id='1', label='string', prefill={'type': 'user', 'value': 'email'}),
    ]
    formdef.backoffice_submission_roles = agent.roles[:]
    formdef.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/submission/%s/' % formdef.slug)
    resp.form['f1'] = 'test@example.net'
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit')  # -> submit

    # check an email has been send to submitter
    assert emails.emails['New form (test form)'].email.to == ['test@example.net']

import datetime
import json
import os

import pytest

from wcs.carddef import CardDef
from wcs.fields import ItemField, ItemsField, PageField, StringField
from wcs.formdef import FormDef
from wcs.i18n import TranslatableMessage
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.ident.password_accounts import PasswordAccount
from wcs.sql import Equal
from wcs.workflows import Workflow

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login


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

    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'portal_url', 'https://portal/')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)

    TranslatableMessage.do_table()  # update table with selected languages

    return pub


@pytest.fixture
def user(pub):
    pub.user_class.wipe()
    PasswordAccount.wipe()

    user = pub.user_class()
    user.name = 'User Name'
    user.email = 'foo@localhost'
    user.name_identifiers = ['xxx']
    user.store()
    account = PasswordAccount(id='foo')
    account.set_password('foo')
    account.user_id = user.id
    account.store()
    return user


def teardown_module(module):
    clean_temporary_pub()


def test_i18n_form(pub, user, emails, http_requests):
    workflow = Workflow(name='test')
    st1 = workflow.add_status('Workflow status')
    st2 = workflow.add_status('Workflow second status')
    action = st1.add_action('choice')
    action.label = 'Jump'
    action.by = ['_submitter']
    action.status = st2.id
    action = st2.add_action('displaymsg')
    action.message = 'Action message'
    action = st2.add_action('sendmail')
    action.to = ['_submitter', 'test@example.invalid']
    action.subject = 'Mail Subject'
    action.body = 'Mail Body'
    action = st2.add_action('notification')
    action.to = ['_submitter']
    action.title = 'Notification Title'
    action.body = 'Notification Body'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test form'
    formdef.fields = [
        PageField(
            id='0',
            label='page field',
            post_conditions=[
                {
                    'condition': {'type': 'django', 'value': 'form_var_text == "test"'},
                    'error_message': 'page error message',
                },
            ],
        ),
        # label has a trailing white space to check for strip()
        StringField(id='1', label='text field ', hint='an hint text', varname='text'),
        ItemField(
            id='2',
            label='list field',
            items=['first', 'second', 'third'],
            hint='a second hint text',
        ),
        ItemsField(
            id='3',
            label='mutiple list field',
            items=['first', 'second', 'third'],
        ),
    ]
    formdef.workflow = workflow
    formdef.store()

    resp = get_app(pub).get(formdef.get_url())
    assert resp.pyquery('#form_label_f1').text() == 'text field *'

    for en, fr in (
        ('test form', 'formulaire test'),
        ('page field', 'champ page'),
        ('text field', 'champ texte'),
        ('list field', 'champ liste'),
        ('multiple list field', 'champ liste multiple'),
        ('first', 'premier'),
        ('second', 'deuxième'),
        ('third', 'troisième'),
        ('Workflow status', 'Statut de workflow'),
        ('Workflow second status', 'Deuxième statut de workflow'),
        ('Jump', 'Saut'),
        ('Action message', 'Message d’action'),
        ('Mail Subject', 'Objet du courriel'),
        ('Mail Body', 'Contenu du courriel'),
        ('Notification Title', 'Titre de notification'),
        ('Notification Body', 'Contenu de notification'),
        ('an hint text', 'un texte d’aide'),
        ('a second hint text', 'un deuxième texte d’aide'),
        ('page error message', 'message d’erreur de page'),
    ):
        msg = TranslatableMessage()
        msg.string = en
        msg.string_fr = fr
        msg.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdef.get_url())
    assert resp.pyquery('#steps li.first .wcs-step--label-text').text() == 'page field'
    assert resp.pyquery('#form_label_f1').text() == 'text field *'
    assert resp.pyquery('[data-field-id="1"] .hint').text() == 'an hint text'
    assert resp.pyquery('select [value=""]').text() == 'a second hint text'
    assert resp.pyquery('[data-field-id="3"] li:first-child span').text() == 'first'

    resp = app.get(formdef.get_url(), headers={'Accept-Language': 'fr'})
    assert resp.pyquery('#steps li.first .wcs-step--label-text').text() == 'champ page'
    assert resp.pyquery('#form_label_f1').text() == 'champ texte*'
    assert resp.pyquery('[data-field-id="1"] .hint').text() == 'un texte d’aide'
    assert resp.pyquery('select [value=""]').text() == 'un deuxième texte d’aide'
    assert resp.pyquery('[data-field-id="3"] li:first-child span').text() == 'premier'

    resp = app.get(formdef.get_url(), headers={'Accept-Language': 'fr,en;q=0.7,es;q=0.3'})
    assert resp.pyquery('h1').text() == 'formulaire test'
    assert resp.pyquery('#form_label_f1').text() == 'champ texte*'
    assert resp.pyquery('option:nth-child(3)').text() == 'deuxième'

    resp.form['f1'] = 'xxx'
    resp = resp.form.submit('submit', headers={'Accept-Language': 'fr'})
    assert 'message d’erreur de page' in resp.pyquery('.global-errors').text()

    resp.form['f1'] = 'test'
    resp.form['f2'] = 'second'
    resp.form['f3$elementfirst'] = True
    resp = resp.form.submit('submit', headers={'Accept-Language': 'fr'})
    assert resp.pyquery('#form_label_f1').text() == 'champ texte'
    assert resp.form['f2'].value == 'second'
    assert resp.form['f2'].attrs == {'type': 'hidden'}
    assert resp.pyquery('#form_f2_label').val() == 'deuxième'

    resp = resp.form.submit('submit', headers={'Accept-Language': 'fr'}).follow(
        headers={'Accept-Language': 'fr'}
    )
    assert resp.pyquery('.field-type-string .label').text() == 'champ texte'
    assert resp.pyquery('.field-type-item .value').text() == 'deuxième'
    assert resp.pyquery('#evolutions li:first-child .status').text() == 'Statut de workflow'
    assert resp.pyquery('form button').text() == 'Saut'
    resp = resp.form.submit('button1', headers={'Accept-Language': 'fr'}).follow(
        headers={'Accept-Language': 'fr'}
    )
    assert resp.pyquery('.workflow-messages').text() == 'Message d’action'

    # check two different emails have been sent (in French to user, in English to other address)
    assert emails.emails['Objet du courriel'].email.to == ['foo@localhost']
    assert emails.emails['Mail Subject'].email.to == ['test@example.invalid']

    # check the notification has been sent in French to user
    assert json.loads(http_requests.get_last('body'))['body'] == 'Contenu de notification'


def test_i18n_prefix(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test form'
    formdef.fields = [StringField(id='1', label='text field')]
    formdef.store()

    TranslatableMessage.wipe()
    msg = TranslatableMessage()
    msg.string = 'text field'
    msg.string_fr = 'champ texte'
    msg.store()

    resp = get_app(pub).get(formdef.get_url())
    assert resp.pyquery('#form_label_f1').text() == 'text field*'

    resp = get_app(pub).get(formdef.get_url(language='en'))
    assert resp.pyquery('#form_label_f1').text() == 'text field*'

    resp = get_app(pub).get(formdef.get_url(language='fr'))
    assert resp.pyquery('#form_label_f1').text() == 'champ texte*'

    resp.form['f1'] = 'test'
    resp = resp.form.submit('submit')  # -> validation page
    assert resp.pyquery('#form_label_f1').text() == 'champ texte'
    resp = resp.form.submit('submit', status=302)  # -> submit
    assert '/fr/' in resp.location
    resp = resp.follow()
    assert resp.pyquery('.field-type-string .label').text() == 'champ texte'


def test_i18n_prefix_wfedit(pub, user):
    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    editable = st1.add_action('editable', id='_editable')
    editable.by = ['_submitter', '_receiver']
    workflow.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test form'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url(language='fr'))
    resp = resp.forms[0].submit('button_editable')
    assert resp.location.startswith('http://example.net/fr/test-form/%s/wfedit-' % formdata.id)
    resp = resp.follow()
    resp = resp.forms[0].submit('submit')
    assert resp.location == 'http://example.net/fr/test-form/%s/' % formdata.id


def test_translated_card_item(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_foo|translate }}'}
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.data = {'1': 'hello world'}
    carddata.just_created()
    carddata.store()

    msg = TranslatableMessage.select([Equal('string', 'hello world')])[0]
    msg.string_fr = 'bonjour monde'
    msg.store()

    del pub._i18n_catalog['fr']
    carddata.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test form'
    formdef.fields = [ItemField(id='1', label='field', data_source={'type': 'carddef:foo'})]
    formdef.store()

    resp = get_app(pub).get(formdef.get_url(language='en'))
    assert resp.pyquery('#form_f1 option').text() == 'hello world'

    resp = get_app(pub).get(formdef.get_url(language='fr'))
    assert resp.pyquery('#form_f1 option').text() == 'bonjour monde'

    resp = resp.form.submit('submit')  # -> validation page
    assert resp.pyquery('#form_f1_label').val() == 'bonjour monde'
    resp = resp.form.submit('submit', status=302)  # -> submit
    assert '/fr/' in resp.location
    resp = resp.follow()
    assert resp.pyquery('.field-type-item .value').text() == 'bonjour monde'

    # reload with different prefix
    resp = resp.test_app.get(resp.request.url.replace('/fr/', '/en/'))
    assert resp.pyquery('.field-type-item .value').text() == 'hello world'

    # check it's stored in original language in database
    assert formdef.data_class().select()[0].data == {
        '1': '1',
        '1_display': 'hello world',
        '1_structured': {'id': 1, 'text': 'hello world', 'foo': 'hello world'},
    }

    # check with a custom view with a different digest
    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.visibility = 'datasource'
    custom_view.store()

    carddef.digest_templates = {
        'default': '{{ form_var_foo|translate }}',
        'custom-view:view': 'Test {{ form_var_foo|translate }}',
        'custom-view:other-view': 'Test {{ form_var_foo|translate }}',
    }
    carddef.store()
    carddata.store()  # update digests

    formdef.fields = [ItemField(id='1', label='field', data_source={'type': 'carddef:foo:view'})]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get(formdef.get_url(language='en'))
    assert resp.pyquery('#form_f1 option').text() == 'Test hello world'

    resp = get_app(pub).get(formdef.get_url(language='fr'))
    assert resp.pyquery('#form_f1 option').text() == 'Test bonjour monde'

    resp = resp.form.submit('submit')  # -> validation page
    assert resp.pyquery('#form_f1_label').val() == 'Test bonjour monde'
    resp = resp.form.submit('submit', status=302)  # -> submit
    assert '/fr/' in resp.location
    resp = resp.follow()
    assert resp.pyquery('.field-type-item .value').text() == 'Test bonjour monde'

    # reload with different prefix
    resp = resp.test_app.get(resp.request.url.replace('/fr/', '/en/'))
    assert resp.pyquery('.field-type-item .value').text() == 'Test hello world'

    # check it's stored in original language in database
    assert formdef.data_class().select()[0].data == {
        '1': '1',
        '1_display': 'Test hello world',
        '1_structured': {'id': 1, 'text': 'Test hello world', 'foo': 'hello world'},
    }


def test_translated_datetime(pub, user):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test form'
    formdef.fields = [
        StringField(id='1', label='text field'),
    ]
    formdef.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get('/en/test-form/')
    resp.form['f1'] = 'foobar'
    resp = resp.form.submit('submit')  # 1st draft

    resp = app.get('/en/test-form/')
    resp.form['f1'] = 'foobar'
    resp = resp.form.submit('submit')  # 2nd draft

    resp = app.get('/en/test-form/')
    # check date starts with year-
    assert (' %s-' % datetime.date.today().year) in resp.pyquery('.drafts-recall li:first-child a').text()

    resp = app.get('/fr/test-form/')
    # check date end with /year
    assert ('/%s ' % datetime.date.today().year) in resp.pyquery('.drafts-recall li:first-child a').text()


def test_form_titles(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test form'
    formdef.store()
    formdef.data_class().wipe()

    TranslatableMessage.wipe()
    msg = TranslatableMessage()
    msg.string = 'test form'
    msg.string_fr = 'formulaire de test'
    msg.store()

    resp = get_app(pub).get(formdef.get_url(language='en'))
    assert resp.pyquery('title').text() == 'test form - 1/2 - Filling'
    assert resp.pyquery('h1').text() == 'test form'
    resp = resp.form.submit('submit')  # -> validation
    assert resp.pyquery('title').text() == 'test form - 2/2 - Validating'
    assert resp.pyquery('h1').text() == 'test form'
    resp = resp.form.submit('submit').follow()
    assert resp.pyquery('title').text() == 'test form #1-1'
    assert resp.pyquery('h1').text() == 'test form'

    resp = get_app(pub).get(formdef.get_url(language='fr'))
    assert resp.pyquery('title').text().startswith('formulaire de test - 1/2')
    assert resp.pyquery('h1').text() == 'formulaire de test'
    resp = resp.form.submit('submit')  # -> validation
    assert resp.pyquery('title').text().startswith('formulaire de test - 2/2')
    assert resp.pyquery('h1').text() == 'formulaire de test'
    resp = resp.form.submit('submit').follow()
    assert resp.pyquery('title').text().startswith('formulaire de test')
    assert resp.pyquery('h1').text() == 'formulaire de test'


def test_form_validation_message(pub):
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test form'
    formdef.fields = [
        StringField(
            id='1',
            label='text field',
            required='optional',
            validation={'type': 'django', 'value': 'False', 'error_message': 'validation error'},
        ),
    ]
    formdef.store()
    formdef.data_class().wipe()

    TranslatableMessage.wipe()
    msg = TranslatableMessage()
    msg.string = 'validation error'
    msg.string_fr = 'erreur de validation'
    msg.store()

    resp = get_app(pub).get(formdef.get_url(language='en'))
    resp.form['f1'] = 'test'
    resp = resp.form.submit('submit')  # -> validation error
    assert resp.pyquery('#form_error_f1').text() == 'validation error'

    resp = get_app(pub).get(formdef.get_url(language='fr'))
    resp.form['f1'] = 'test'
    resp = resp.form.submit('submit')  # -> validation error
    assert resp.pyquery('#form_error_f1').text() == 'erreur de validation'

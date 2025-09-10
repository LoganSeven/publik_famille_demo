from unittest import mock

import pytest

from wcs import fields, sql
from wcs.backoffice.i18n import update_digests
from wcs.carddef import CardDef
from wcs.fields import StringField
from wcs.i18n import TranslatableMessage
from wcs.qommon.template import Template
from wcs.wf.form import WorkflowFormFieldsFormDef
from wcs.workflows import Workflow

from .test_sql import column_exists_in_table
from .utilities import clean_temporary_pub, create_temporary_pub, get_app


@pytest.fixture
def pub(request):
    return create_temporary_pub()


def teardown_module(module):
    clean_temporary_pub()


def test_activate_language_all_views(pub):
    with mock.patch('django.utils.translation.activate') as mocked:
        get_app(pub).get('/i18n.js')  # django view
        assert mocked.call_count == 1

        get_app(pub).get('/404', status=404)  # quixote view
        assert mocked.call_count == 2


def test_translation_columns(pub):
    _, cur = sql.get_connection_and_cursor()
    assert not column_exists_in_table(cur, 'translatable_messages', 'string_de')
    assert not column_exists_in_table(cur, 'translatable_messages', 'string_fr')
    pub.cfg['language'] = {'language': 'en', 'multilinguism': True, 'languages': ['fr', 'de']}
    pub.write_cfg()
    TranslatableMessage.do_table()  # update table with selected languages
    assert column_exists_in_table(cur, 'translatable_messages', 'string_de')
    assert column_exists_in_table(cur, 'translatable_messages', 'string_fr')
    # check it's not removed
    pub.cfg['language'] = {'language': 'en', 'multilinguism': True, 'languages': ['fr']}
    assert column_exists_in_table(cur, 'translatable_messages', 'string_de')
    assert column_exists_in_table(cur, 'translatable_messages', 'string_fr')
    cur.close()


def test_translatable_message_storage(pub):
    msg = TranslatableMessage()
    msg.string = 'test'
    msg.context = None
    msg.locations = ['a', 'b', 'c']
    msg.store()

    assert TranslatableMessage.get(msg.id).string == msg.string
    assert TranslatableMessage.get(msg.id).context == msg.context
    assert TranslatableMessage.get(msg.id).locations == msg.locations


def test_load_as_catalog(pub):
    pub.cfg['language'] = {'language': 'en', 'multilinguism': True, 'languages': ['fr', 'en']}
    pub.write_cfg()
    TranslatableMessage.do_table()
    TranslatableMessage.wipe()

    msg = TranslatableMessage()
    msg.string = 'string 1'
    msg.string_fr = 'chaine 1'
    msg.store()

    msg = TranslatableMessage()
    msg.string = 'string 2'
    msg.string_fr = 'chaine 2'
    msg.store()

    catalog = TranslatableMessage.load_as_catalog('fr')
    assert catalog == {(None, 'string 1'): 'chaine 1', (None, 'string 2'): 'chaine 2'}


def test_translate_template_tag(pub):
    pub.cfg['language'] = {'language': 'en', 'multilinguism': True, 'languages': ['fr', 'en']}
    pub.write_cfg()
    TranslatableMessage.do_table()
    TranslatableMessage.wipe()

    msg = TranslatableMessage()
    msg.string = 'string 1'
    msg.string_fr = 'chaine 1'
    msg.store()

    tmpl1 = Template('{{ "string 1"|translate }}')
    tmpl2 = Template('{{ "string 2"|translate }}')
    assert tmpl1.render() == 'string 1'
    assert tmpl2.render() == 'string 2'
    assert TranslatableMessage.count() == 1
    with pub.with_language('fr'):
        assert tmpl1.render() == 'chaine 1'
        assert tmpl2.render() == 'string 2'
        assert TranslatableMessage.count() == 2  # string 2 has been added to catalog

        # check it's not added a second time
        del pub._i18n_catalog['fr']
        assert tmpl2.render() == 'string 2'
        assert TranslatableMessage.count() == 2

    # test context
    tmpl3 = Template('{{ "string 1"|translate:"plop" }}')
    assert tmpl3.render() == 'string 1'
    with pub.with_language('fr'):
        assert tmpl3.render() == 'string 1'
        assert TranslatableMessage.count() == 3  # added to catalog

        msg = TranslatableMessage.select([sql.Equal('string', 'string 1'), sql.Equal('context', 'plop')])[0]
        msg.string_fr = 'alt chaine 1'
        msg.store()
        del pub._i18n_catalog['fr']
        assert tmpl3.render() == 'alt chaine 1'


def test_translated_digest(pub):
    pub.cfg['language'] = {'language': 'en', 'multilinguism': True, 'languages': ['fr', 'en']}
    pub.write_cfg()
    TranslatableMessage.do_table()
    TranslatableMessage.wipe()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        fields.StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_foo|translate }}'}
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.data = {'1': 'hello world'}
    carddata.just_created()
    carddata.store()
    assert carddata.digests == {
        'default': 'hello world',
        'default:fr': 'hello world',
        'default:en': 'hello world',
    }

    msg = TranslatableMessage.select([sql.Equal('string', 'hello world')])[0]
    msg.string_fr = 'bonjour monde'
    msg.store()
    del pub._i18n_catalog['fr']

    carddata.store()
    assert carddata.digests == {
        'default': 'hello world',
        'default:fr': 'bonjour monde',
        'default:en': 'hello world',
    }

    # automatic update
    msg = TranslatableMessage.select([sql.Equal('string', 'hello world')])[0]
    msg.string_fr = 'bonjour le monde'
    msg.store()
    del pub._i18n_catalog['fr']
    update_digests()
    carddata.refresh_from_storage()
    assert carddata.digests == {
        'default': 'hello world',
        'default:fr': 'bonjour le monde',
        'default:en': 'hello world',
    }

    # do not crash on missing template
    carddef.digest_templates = {'default': None}
    carddef.store()
    del pub._i18n_catalog['fr']
    update_digests()
    carddata.store()


def test_action_scan(pub):
    workflow = Workflow(name='test')
    status = workflow.add_status('Status0', 'st0')
    action = status.add_action('choice')
    action.label = 'foo'
    assert 'foo' in (x[2] for x in action.i18n_scan(''))

    action = status.add_action('displaymsg')
    action.message = 'foo'
    assert 'foo' in (x[2] for x in action.i18n_scan(''))

    action = status.add_action('form')
    action.formdef = WorkflowFormFieldsFormDef(item=action)
    action.formdef.fields.append(StringField(id='1', label='foo'))
    action.post_conditions = [
        {
            'condition': {'type': 'django', 'value': 'True'},
            'error_message': 'foo2',
        }
    ]
    assert 'foo' in (x[2] for x in action.i18n_scan(''))
    assert 'foo2' in (x[2] for x in action.i18n_scan(''))

    action = status.add_action('notification')
    action.title = 'foo'
    assert 'foo' in (x[2] for x in action.i18n_scan(''))

    action = status.add_action('sendmail')
    action.subject = 'foo'
    assert 'foo' in (x[2] for x in action.i18n_scan(''))
    action.mail_template = 'slug'
    assert 'foo' not in (x[2] for x in action.i18n_scan(''))

    action = status.add_action('register-comment')
    action.comment = 'foo'
    assert 'foo' in (x[2] for x in action.i18n_scan(''))
    action.comment_template = 'slug'
    assert 'foo' not in (x[2] for x in action.i18n_scan(''))

    action = status.add_action('sendsms')
    action.body = 'foo'
    assert 'foo' in (x[2] for x in action.i18n_scan(''))

import os

import pytest
from quixote import cleanup

from wcs.formdef import FormDef
from wcs.workflows import Workflow

from ..admin_pages.test_all import create_superuser
from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login


def setup_module(module):
    cleanup()


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def pub():
    pub = create_temporary_pub()
    pub.cfg['language'] = {'language': 'en'}
    pub.cfg['identification'] = {'methods': ['password']}
    pub.write_cfg()
    return pub


def test_display_message_migrate(pub):
    workflow = Workflow(name='display message to')
    st1 = workflow.add_status('Status1', 'st1')
    display_message = st1.add_action('displaymsg')
    display_message.level = None
    display_message.message = '<div class="errornotice">message</div>'
    workflow.store()

    workflow.migrate()
    assert workflow.possible_status[0].items[0].level == 'error'
    assert workflow.possible_status[0].items[0].message == 'message'

    # check the migration is skipped if there's an extra class
    display_message.level = None
    display_message.message = '<div class="errornotice blah">message</div>'
    workflow.store()
    workflow.migrate()
    assert not workflow.possible_status[0].items[0].level
    assert workflow.possible_status[0].items[0].message == '<div class="errornotice blah">message</div>'


def test_display_message_rich_text(pub):
    create_superuser(pub)

    workflow = Workflow(name='display message to')
    st1 = workflow.add_status('Status1', 'st1')
    display_message = st1.add_action('displaymsg')
    display_message.message = '<p>hello world</p>'
    workflow.store()

    app = login(get_app(pub))
    resp = app.get(display_message.get_admin_url())
    assert resp.pyquery('godo-editor')  # godo

    display_message.message = '<table><tr><td>hello world</td></tr></table>'
    workflow.store()
    resp = app.get(display_message.get_admin_url())
    assert resp.pyquery('textarea[data-config]')  # ckeditor

    pub.site_options.set('options', 'rich-text-wf-displaymsg', 'auto-ckeditor-textarea')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    display_message.message = '<table><tr><td>hello world</td></tr></table>'
    workflow.store()
    resp = app.get(display_message.get_admin_url())
    assert resp.pyquery('textarea[data-config]')  # ckeditor

    display_message.message = '<ul>{% for item in lists %}<li>{{ item }}</li>{% endfor %}</ul>'
    workflow.store()
    resp = app.get(display_message.get_admin_url())
    assert resp.pyquery('textarea:not([data-config])')  # plain textarea

    pub.site_options.set('options', 'rich-text-wf-displaymsg', 'auto-textarea')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    resp = app.get(display_message.get_admin_url())
    assert resp.pyquery('textarea:not([data-config])')  # plain textarea

    pub.site_options.set('options', 'rich-text-wf-displaymsg', 'auto-ckeditor')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    resp = app.get(display_message.get_admin_url())
    assert resp.pyquery('textarea[data-config]')  # ckeditor

    display_message.message = '<p>simple</p>'
    workflow.store()
    pub.site_options.set('options', 'rich-text-wf-displaymsg', 'ckeditor')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    resp = app.get(display_message.get_admin_url())
    assert resp.pyquery('textarea[data-config]')  # ckeditor

    pub.site_options.set('options', 'rich-text-wf-displaymsg', 'godo')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    resp = app.get(display_message.get_admin_url())
    assert resp.pyquery('godo-editor')  # godo

    pub.site_options.set('options', 'rich-text-wf-displaymsg', 'textarea')
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        pub.site_options.write(fd)
    resp = app.get(display_message.get_admin_url())
    assert resp.pyquery('textarea:not([data-config])')  # plain textarea


def test_display_message_global_action(pub):
    Workflow.wipe()
    FormDef.wipe()
    pub.role_class.wipe()

    role = pub.role_class(name='xxx')
    role.store()

    user = create_superuser(pub)
    user.roles = [str(role.id)]
    user.store()

    workflow = Workflow(name='display message to')
    workflow.add_status('Status')
    global_action = workflow.add_global_action('Message')
    trigger = global_action.triggers[0]
    trigger.roles = ['_receiver']
    display_message = global_action.add_action('displaymsg')
    display_message.message = '<p>hello world</p>'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.workflow_roles = {'_receiver': user.roles[0]}
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdata.get_backoffice_url())
    resp = resp.forms['wf-actions'].submit('button-action-1').follow()
    assert resp.pyquery('#messages').text() == 'hello world'

    display_message.to = ['_submitter']
    workflow.store()

    resp = resp.forms['wf-actions'].submit('button-action-1').follow()
    assert not resp.pyquery('#messages').text()

    display_message.to = ['_receiver']
    workflow.store()
    resp = resp.forms['wf-actions'].submit('button-action-1').follow()
    assert resp.pyquery('#messages').text() == 'hello world'

    display_message.message = None
    display_message.to = ['_receiver']
    workflow.store()
    resp = resp.forms['wf-actions'].submit('button-action-1').follow()
    assert len(resp.pyquery('#messages')) == 0


def test_display_message_interactive_global_action(pub):
    Workflow.wipe()
    FormDef.wipe()
    pub.role_class.wipe()

    role = pub.role_class(name='xxx')
    role.store()

    user = create_superuser(pub)
    user.roles = [str(role.id)]
    user.store()

    workflow = Workflow(name='display message to')
    workflow.add_status('Status')
    global_action = workflow.add_global_action('Message')
    trigger = global_action.triggers[0]
    trigger.roles = ['_receiver']
    display_message = global_action.add_action('displaymsg')
    display_message.message = '<p>hello world</p>'
    choice = global_action.add_action('choice')
    choice.by = [role.id]
    choice.label = 'test'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.workflow_roles = {'_receiver': user.roles[0]}
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    app = login(get_app(pub))
    resp = app.get(formdata.get_backoffice_url())
    resp = resp.forms['wf-actions'].submit('button-action-1').follow()
    assert resp.pyquery('.workflow-messages').text() == 'hello world'
    resp = resp.forms['wf-actions'].submit('button2').follow()
    assert not resp.pyquery('#messages').text()

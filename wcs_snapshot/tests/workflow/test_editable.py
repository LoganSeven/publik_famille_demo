import datetime

import pytest
from quixote import cleanup

from wcs.carddef import CardDef
from wcs.fields import CommentField, ItemsField, StringField
from wcs.formdef import FormDef
from wcs.workflows import JumpEvolutionPart, Workflow

from ..test_workflow_import import assert_import_export_works
from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import admin_user  # noqa pylint: disable=unused-import


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


def test_editable_export_import(pub):
    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    action = st1.add_action('editable')
    action.status = st1.id
    action.set_marker_on_status = True
    action.identifier = 'test'
    action.by = ['_submitter']
    assert_import_export_works(workflow, include_id=True)


def test_editable_line_details(pub):
    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    action = st1.add_action('editable')

    assert action.get_line_details() == 'not completed'

    role = pub.role_class(name='foorole')
    role.store()
    action.by = [role.id]
    assert action.get_line_details() == '"Edit Form", by foorole'

    action.label = 'foobar'
    assert action.get_line_details() == '"foobar", by foorole'


def test_editable_set_marker(pub):
    FormDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    st2 = workflow.add_status('Status2', 'st2')
    editable = st1.add_action('editable')
    editable.status = st2.id
    editable.set_marker_on_status = True
    editable.by = ['_submitter']
    back = st2.add_action('choice')
    back.label = 'go back'
    back.status = '_previous'
    back.by = ['_submitter']
    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.workflow = workflow
    formdef.store()

    resp = get_app(pub).get(formdef.get_url())
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit').follow()  # -> done
    formdata = formdef.data_class().select()[0]

    # edit
    resp = resp.form.submit(f'button{editable.id}').follow()
    resp = resp.form.submit('submit').follow()  # -> done

    formdata.refresh_from_storage()
    assert formdata.get_status().id == st2.id
    assert formdata.workflow_data.get('_markers_stack')

    # back
    resp = resp.form.submit(f'button{editable.id}').follow()
    formdata.refresh_from_storage()
    assert formdata.get_status().id == st1.id
    assert not formdata.workflow_data.get('_markers_stack')


def test_editable_exclude_self_and_live(pub):
    FormDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    editable = st1.add_action('editable')
    editable.by = ['_submitter']
    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        StringField(id='0', label='Test', varname='foo'),
        CommentField(
            id='1',
            label='X{{form_objects|count}}/{{form_objects|exclude_self|count}}Y',
            condition={'type': 'django', 'value': 'form_var_foo'},
        ),
    ]
    formdef.workflow = workflow
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f0'] = 'test'
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit').follow()  # -> done

    # edit
    resp = resp.form.submit(f'button{editable.id}').follow()
    assert resp.pyquery('.comment-field').text() == 'X1/0Y'
    live_url = resp.html.find('form').attrs['data-live-url']
    live_resp = app.post(live_url, params=resp.form.submit_fields())
    assert live_resp.json == {
        'result': {'0': {'visible': True}, '1': {'visible': True, 'content': '<p>X1/0Y</p>'}}
    }


def test_editable_form_status_and_live(pub):
    FormDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    editable = st1.add_action('editable')
    editable.by = ['_submitter']
    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        StringField(id='0', label='Test', varname='foo'),
        CommentField(
            id='1',
            label='X{{form_status}}Y',
            condition={'type': 'django', 'value': 'form_var_foo'},
        ),
    ]
    formdef.workflow = workflow
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f0'] = 'test'
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit').follow()  # -> done

    # edit
    resp = resp.form.submit(f'button{editable.id}').follow()
    assert resp.pyquery('.comment-field').text() == 'XStatus1Y'
    live_url = resp.html.find('form').attrs['data-live-url']
    live_resp = app.post(live_url, params=resp.form.submit_fields())
    assert live_resp.json == {
        'result': {'0': {'visible': True}, '1': {'visible': True, 'content': '<p>XStatus1Y</p>'}}
    }


def test_editable_jump_with_identifier(pub, admin_user):
    FormDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')

    st2 = workflow.add_status('Status2', 'st2')
    editable = st1.add_action('editable')
    editable.status = st2.id
    editable.identifier = 'editjump'
    editable.by = ['_submitter']
    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.workflow = workflow
    formdef.store()

    resp = get_app(pub).get(formdef.get_url())
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit').follow()  # -> done
    formdata = formdef.data_class().select()[0]

    # edit
    resp = resp.form.submit(f'button{editable.id}').follow()
    resp = resp.form.submit('submit').follow()  # -> done

    formdata.refresh_from_storage()
    assert formdata.get_status().id == st2.id
    assert list(formdata.iter_evolution_parts(JumpEvolutionPart))[0].identifier == 'editjump'

    resp = login(get_app(pub), username='admin', password='admin').get(workflow.get_admin_url() + 'inspect')
    assert resp.pyquery('.parameter-identifier').text() == 'Identifier of status jump: editjump'


def test_edit_empty_structured(pub):
    FormDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    editable = st1.add_action('editable')
    editable.by = ['_submitter']
    workflow.store()

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'items'
    carddef.digest_templates = {'default': '{{form_var_name}}'}
    carddef.fields = [
        StringField(id='0', label='string', varname='name'),
        StringField(id='1', label='string', varname='attr'),
    ]
    carddef.store()
    carddef.data_class().wipe()
    for i, value in enumerate(['foo', 'bar', 'baz']):
        carddata = carddef.data_class()()
        carddata.data = {
            '0': value,
            '1': 'attr%s' % i,
        }
        carddata.just_created()
        carddata.store()

    ds = {'type': 'carddef:%s' % carddef.url_name}

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [ItemsField(id='0', label='items', data_source=ds, required=False)]
    formdef.workflow = workflow
    formdef.store()
    formdef.data_class().wipe()

    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f0$element1'].checked = True
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit').follow()  # -> done
    formdata = formdef.data_class().select()[0]
    assert formdata.data == {
        '0': ['1'],
        '0_display': 'foo',
        '0_structured': [{'attr': 'attr0', 'id': 1, 'name': 'foo', 'text': 'foo'}],
    }

    # edit
    resp = resp.form.submit(f'button{editable.id}').follow()
    resp.form['f0$element1'].checked = False
    resp = resp.form.submit('submit').follow()  # -> done
    formdata.refresh_from_storage()
    for attr in ('0', '0_display', '0_structured'):
        assert formdata.data.get(attr) is None


def test_edit_jump_self_check_last_update_time(pub, freezer):
    FormDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    editable = st1.add_action('editable')
    editable.by = ['_submitter']
    editable.status = f'{st1.id}'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [StringField(id='0', label='test')]
    formdef.workflow = workflow
    formdef.store()
    formdef.data_class().wipe()

    freezer.move_to(datetime.date(2025, 3, 11))
    app = get_app(pub)
    resp = app.get(formdef.get_url())
    resp.form['f0'] = 'foo'
    resp = resp.form.submit('submit')  # -> validation page
    resp = resp.form.submit('submit').follow()  # -> done

    # edit
    freezer.move_to(datetime.date(2025, 3, 12))
    resp = resp.form.submit(f'button{editable.id}').follow()
    resp.form['f0'] = 'bar'
    resp = resp.form.submit('submit').follow()  # -> done

    freezer.move_to(datetime.date(2025, 3, 13))
    resp = resp.form.submit(f'button{editable.id}').follow()
    resp.form['f0'] = 'bar'
    resp = resp.form.submit('submit').follow()  # -> done

    formdata = formdef.data_class().select()[0]
    assert formdata.get_last_update_time().date() == datetime.date(2025, 3, 13)

import pytest
from quixote import cleanup

from wcs.audit import Audit
from wcs.formdef import FormDef
from wcs.roles import logged_users_role
from wcs.workflows import Workflow

from ..backoffice_pages.test_all import create_user as create_backoffice_user
from ..backoffice_pages.test_all import login
from ..utilities import clean_temporary_pub, create_temporary_pub, get_app


def setup_module(module):
    cleanup()


def teardown_module(module):
    clean_temporary_pub()


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()
    pub.cfg['language'] = {'language': 'en'}
    pub.cfg['identification'] = {'methods': ['password']}
    pub.write_cfg()
    return pub


def test_remove_audit(pub):
    user = create_backoffice_user(pub, is_admin=True)

    Workflow.wipe()
    workflow = Workflow(name='remove')
    st1 = workflow.add_status('st1')
    st2 = workflow.add_status('st2')
    choice = st1.add_action('choice')
    choice.by = [logged_users_role().id]
    choice.label = 'delete'
    choice.status = str(st2.id)
    choice.identifier = 'delete'
    st2.add_action('remove')
    workflow.store()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    formdata.perform_workflow()

    Audit.wipe()
    app = login(get_app(pub))
    resp = app.get(formdata.get_backoffice_url())
    resp.forms['wf-actions'].submit('button1').follow()
    assert [(x.action, x.user_id, x.data_id) for x in Audit.select(order_by='id')] == [
        ('view', str(user.id), formdata.id),
        ('deletion', str(user.id), formdata.id),
        ('listing', str(user.id), None),
    ]
    assert formdef.data_class().count() == 0

    # try from a mass action
    ids = []
    for i in range(3):
        formdata = formdef.data_class()()
        formdata.just_created()
        formdata.store()
        formdata.perform_workflow()
        ids.append(formdata.id)

    Audit.wipe()
    app = login(get_app(pub))
    resp = app.get(f'/backoffice/management/{formdef.slug}/')

    for checkbox in resp.forms[0].fields['select[]'][1:]:
        checkbox.checked = True
    resp = resp.forms[0].submit(f'button-action-st-{st1.id}-delete-{choice.id}').follow()
    assert 'Executing task &quot;delete&quot; on forms' in resp.text
    assert [(x.action, x.user_id, x.data_id) for x in Audit.select(order_by='id')] == [
        ('listing', str(user.id), None),
        ('deletion', str(user.id), ids[0]),
        ('deletion', str(user.id), ids[1]),
        ('deletion', str(user.id), ids[2]),
    ]

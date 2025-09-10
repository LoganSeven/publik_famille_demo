import pytest
from pyquery import PyQuery
from quixote import get_publisher

from wcs.carddef import CardDef
from wcs.categories import CardDefCategory
from wcs.comment_templates import CommentTemplate
from wcs.fields import BlockField
from wcs.formdef import FormDef
from wcs.mail_templates import MailTemplate
from wcs.sql_criterias import Equal
from wcs.workflows import Workflow

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_superuser


@pytest.fixture
def pub():
    pub = create_temporary_pub()
    pub.cfg['identification'] = {'methods': ['password']}
    pub.write_cfg()
    CardDefCategory.wipe()
    CardDef.wipe()
    FormDef.wipe()
    Workflow.wipe()
    pub.snapshot_class.wipe()
    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_trash_link_on_studio_page(pub, backoffice_user, backoffice_role):
    cat = CardDefCategory(name='Foo')
    cat.store()

    cat = CardDefCategory(name='Bar')
    cat.management_roles = [backoffice_role]
    cat.store()

    # partial access to studio
    app = login(get_app(pub), username='backoffice-user', password='backoffice-user')
    resp = app.get('/backoffice/studio/')
    assert 'trash/' not in resp
    resp = app.get('/backoffice/studio/trash/', status=403)

    # full access to studio
    pub.cfg['admin-permissions'] = {'workflows': [backoffice_role.id]}
    pub.write_cfg()
    resp = app.get('/backoffice/studio/')
    assert 'trash/' in resp
    resp = app.get('/backoffice/studio/trash/', status=200)


def test_trash_restore_permissions(pub, backoffice_user, backoffice_role):
    pub.cfg['admin-permissions'] = {'workflows': [backoffice_role.id]}
    pub.write_cfg()

    CardDefCategory.wipe()
    cat1 = CardDefCategory(name='Foo')
    cat1.store()

    cat2 = CardDefCategory(name='Bar')
    cat2.management_roles = [backoffice_role]
    cat2.store()

    formdef = FormDef()
    formdef.name = 'form-foo'
    formdef.store(comment='creation')
    get_publisher().snapshot_class.snap(instance=formdef, comment='deletion', force_full_store=True)
    formdef.remove_self()

    workflow = Workflow()
    workflow.name = 'workflow-bar'
    workflow.store(comment='creation')
    get_publisher().snapshot_class.snap(instance=workflow, comment='deletion', force_full_store=True)
    workflow.remove_self()

    carddef1 = CardDef()
    carddef1.name = 'card-foo'
    carddef1.category_id = cat1.id
    carddef1.store(comment='creation')
    get_publisher().snapshot_class.snap(instance=carddef1, comment='deletion', force_full_store=True)
    carddef1.remove_self()

    carddef2 = CardDef()
    carddef2.name = 'card-bar'
    carddef2.category_id = cat2.id
    carddef2.store(comment='creation')
    get_publisher().snapshot_class.snap(instance=carddef2, comment='deletion', force_full_store=True)
    carddef2.remove_self()

    pub.snapshot_class.clean()  # mark deleted objects

    app = login(get_app(pub), username='backoffice-user', password='backoffice-user')
    resp = app.get('/backoffice/studio/trash/')
    table_content = {
        tuple(PyQuery(y).text() for y in PyQuery(x).find('td')[1:]) for x in resp.pyquery('tbody tr')
    }

    assert table_content == {
        ('Workflow', 'workflow-bar', 'Restore'),  # can be restored as user has global workflow access
        ('Card model', 'card-bar', 'Restore'),  # can be restored as user has management role for category
        ('Card model', 'card-foo'),  # cannot be restored
        ('Form', 'form-foo'),  # cannot be restored
    }


def test_trash_restore_item(pub, backoffice_user, backoffice_role):
    pub.cfg['admin-permissions'] = {'workflows': [backoffice_role.id]}
    pub.write_cfg()

    workflow = Workflow()
    workflow.name = 'workflow-baz'
    workflow.store(comment='creation')
    workflow_initial_id = workflow.id
    get_publisher().snapshot_class.snap(instance=workflow, comment='deletion', force_full_store=True)
    workflow.remove_self()

    pub.snapshot_class.clean()  # mark deleted objects

    app = login(get_app(pub), username='backoffice-user', password='backoffice-user')
    resp = app.get('/backoffice/studio/trash/', status=200)
    resp = resp.click('Restore')
    resp = resp.form.submit('cancel').follow()

    resp = resp.click('Restore')
    restore_url = resp.request.url
    resp = resp.form.submit('submit').follow()
    assert resp.request.url == f'http://example.net/backoffice/workflows/{workflow_initial_id}/'

    resp = app.get('/backoffice/studio/trash/', status=200)
    assert 'There are no recently deleted items.' in resp.text

    # check restore url of restored items is no longer found
    resp = app.get(restore_url, status=404)


def test_trash_restore_item_with_missing_parts(pub, backoffice_user, backoffice_role):
    pub.cfg['admin-permissions'] = {'forms': [backoffice_role.id]}
    pub.write_cfg()

    formdef = FormDef()
    formdef.name = 'form-bar'
    formdef.fields = [
        BlockField(id='1', label='test', block_slug='test'),
    ]
    formdef.store(comment='creation')
    get_publisher().snapshot_class.snap(instance=formdef, comment='deletion', force_full_store=True)
    formdef.remove_self()

    pub.snapshot_class.clean()  # mark deleted objects

    app = login(get_app(pub), username='backoffice-user', password='backoffice-user')
    resp = app.get('/backoffice/studio/trash/', status=200)
    resp = resp.click('Restore')
    resp = resp.form.submit('submit')
    assert (
        resp.pyquery('.errornotice').text()
        == 'Can not restore snapshot (Unknown referenced objects [Unknown blocks of fields: test])'
    )


def test_trash_restore_category(pub, backoffice_user, backoffice_role):
    pub.cfg['admin-permissions'] = {'forms': [backoffice_role.id]}
    pub.write_cfg()

    CardDefCategory.wipe()
    cat = CardDefCategory(name='Foo')
    cat.store(comment='creation')
    get_publisher().snapshot_class.snap(instance=cat, comment='deletion', force_full_store=True)
    cat.remove_self()

    pub.snapshot_class.clean()  # mark deleted objects

    app = login(get_app(pub), username='backoffice-user', password='backoffice-user')
    resp = app.get('/backoffice/studio/trash/', status=200)
    table_content = {
        tuple(PyQuery(y).text() for y in PyQuery(x).find('td')[1:]) for x in resp.pyquery('tbody tr')
    }
    assert table_content == {('Category of card models', 'Foo')}

    create_superuser(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/studio/trash/', status=200)
    table_content = {
        tuple(PyQuery(y).text() for y in PyQuery(x).find('td')[1:]) for x in resp.pyquery('tbody tr')
    }
    assert table_content == {('Category of card models', 'Foo', 'Restore')}

    resp = resp.click('Restore')
    resp = resp.form.submit('submit').follow()

    cat = CardDefCategory.get_by_slug('foo')
    assert resp.request.url == f'http://example.net/backoffice/cards/categories/{cat.id}/'


@pytest.mark.parametrize('obj_class', [MailTemplate, CommentTemplate])
def test_trash_restore_comment_mail_template(pub, backoffice_user, backoffice_role, obj_class):
    pub.cfg['admin-permissions'] = {'workflows': [backoffice_role.id]}
    pub.write_cfg()

    obj_class.wipe()
    obj = obj_class(name='test')
    if obj_class is CommentTemplate:
        obj.comment = 'message'
    else:
        obj.subject = 'subject'
        obj.body = 'message'
    obj.store()

    orig_url = obj.get_admin_url()

    obj.remove_self()

    pub.snapshot_class.clean()  # mark deleted objects

    app = login(get_app(pub), username='backoffice-user', password='backoffice-user')
    resp = app.get('/backoffice/studio/trash/', status=200)
    table_content = {
        tuple(PyQuery(y).text() for y in PyQuery(x).find('td')[1:]) for x in resp.pyquery('tbody tr')
    }
    if obj_class is CommentTemplate:
        assert table_content == {('Comment template', 'test', 'Restore')}
    else:
        assert table_content == {('Mail template', 'test', 'Restore')}

    resp = resp.click('Restore')
    resp = resp.form.submit('submit').follow()
    assert resp.request.url == orig_url


def test_trash_do_not_display_test_users(pub):
    pub.snapshot_class.wipe()
    pub.test_user_class.wipe()
    test_user = pub.test_user_class(name='Test User')
    test_user.test_uuid = '42'
    test_user.store()
    test_user.remove_self()
    pub.snapshot_class.clean()
    assert pub.snapshot_class.count([Equal('deleted_object', True), Equal('object_type', 'user')]) == 1

    create_superuser(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/studio/trash/', status=200)
    table_content = {
        tuple(PyQuery(y).text() for y in PyQuery(x).find('td')[1:]) for x in resp.pyquery('tbody tr')
    }
    assert table_content == {()}

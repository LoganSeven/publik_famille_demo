import io
import os
import shutil
import xml.etree.ElementTree as ET
from unittest import mock

import pytest
from quixote.http_request import Upload

from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.categories import Category
from wcs.comment_templates import CommentTemplate
from wcs.data_sources import NamedDataSource
from wcs.fields import BlockField, CommentField, ItemField, PageField, StringField
from wcs.formdef import FormDef
from wcs.mail_templates import MailTemplate
from wcs.qommon.form import UploadedFile
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.misc import localstrftime
from wcs.testdef import TestDef
from wcs.wf.form import WorkflowFormFieldsFormDef
from wcs.workflows import Workflow, WorkflowBackofficeFieldsFormDef, WorkflowVariablesFieldsFormDef
from wcs.wscalls import NamedWsCall

from .admin_pages.test_all import create_role, create_superuser
from .utilities import clean_temporary_pub, create_temporary_pub, get_app, login


@pytest.fixture
def pub(emails):
    pub = create_temporary_pub()
    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()
    FormDef.wipe()
    CardDef.wipe()
    NamedDataSource.wipe()
    pub.snapshot_class.wipe()
    pub.user_class.wipe()
    pub.test_user_class.wipe()
    return pub


@pytest.fixture
def formdef_with_history(pub):
    formdef = FormDef()
    formdef.name = 'testform'
    formdef.fields = [
        PageField(id='0', label='Page 1'),
        StringField(id='1', label='Test'),
    ]
    formdef.store()

    for i in range(5):
        formdef.name = 'testform %s' % i
        formdef.description = 'this is a description (%s)' % i
        formdef.store()

    return formdef


def teardown_module(module):
    clean_temporary_pub()


def test_snapshot_basics(pub):
    formdef = FormDef()
    formdef.name = 'testform'
    # start with a big content
    formdef.fields = [CommentField(id='0', label='Test ' * 500)]
    formdef.store()

    # first occurence, complete snapshot stored
    assert pub.snapshot_class.count() == 1
    snapshot1 = pub.snapshot_class.get_latest('formdef', formdef.id)
    assert snapshot1.serialization is not None
    assert '>testform<' in snapshot1.serialization
    assert snapshot1.patch is None
    assert snapshot1.instance  # possible to restore

    # no changes
    formdef.store()
    assert pub.snapshot_class.count() == 1

    # patch only
    formdef.name = 'testform2'
    formdef.store()
    assert pub.snapshot_class.count() == 2

    snapshot2 = pub.snapshot_class.get_latest('formdef', formdef.id)
    assert snapshot2.serialization is None
    assert '>testform2<' in snapshot2.patch
    assert snapshot2.instance  # possible to restore

    # no diff with latest snap but label is given
    pub.snapshot_class.snap(instance=formdef, label='foo bar')
    assert pub.snapshot_class.count() == 3
    snapshot3 = pub.snapshot_class.get_latest('formdef', formdef.id)
    assert snapshot3.serialization is None
    assert '>testform2<' in snapshot3.patch
    assert snapshot2.patch == snapshot3.patch
    assert snapshot3.instance  # possible to restore

    # patch is longer as serialization, store serialization
    formdef.name = 'testform3'
    formdef.fields += [StringField(id=str(i + 1), label='Test %s' % i) for i in range(0, 10)]
    formdef.store()
    assert pub.snapshot_class.count() == 4
    snapshot4 = pub.snapshot_class.get_latest('formdef', formdef.id)
    assert snapshot4.serialization is not None
    assert '>testform3<' in snapshot4.serialization
    assert snapshot4.patch is None
    assert snapshot4.instance  # possible to restore

    # no diff with latest snap but label is given
    pub.snapshot_class.snap(instance=formdef, label='foo bar')
    assert pub.snapshot_class.count() == 5
    snapshot5 = pub.snapshot_class.get_latest('formdef', formdef.id)
    assert snapshot5.serialization is None
    assert snapshot5.patch == ''  # no difference with latest snap, which has a serialization
    assert snapshot5.instance  # possible to restore

    # add snapshots with patches
    snapshot6 = None
    for i in range(10):
        formdef.name = 'testform%s' % (i + 6)
        formdef.fields.append(StringField(id=str(i + 11), label='Test %s' % (i + 10)))
        formdef.store()
        snapshot = pub.snapshot_class.get_latest('formdef', formdef.id)
        assert snapshot.patch is None or len(snapshot.patch) < len(snapshot.get_serialization()) / 10
        snapshot6 = snapshot6 or snapshot
    assert pub.snapshot_class.count() == 15

    # patch is longer as serialization, store serialization
    formdef.name = 'testform16'
    formdef.fields += [StringField(id=str(i + 20), label='Test %s' % (i + 20)) for i in range(0, 30)]
    formdef.store()
    assert pub.snapshot_class.count() == 16
    snapshot16 = pub.snapshot_class.get_latest('formdef', formdef.id)
    assert snapshot16.serialization is not None
    assert '>testform16<' in snapshot16.serialization
    assert snapshot16.patch is None
    assert snapshot16.instance  # possible to restore

    # check that snapshot6 restoration:
    # don't take snapshot15 as latest_complete
    latest_complete = snapshot6.get_latest(snapshot6.object_type, snapshot6.object_id, complete=True)
    assert latest_complete.id == snapshot16.id
    latest_complete = snapshot6.get_latest(
        snapshot6.object_type, snapshot6.object_id, complete=True, max_timestamp=snapshot6.timestamp
    )
    assert latest_complete.id == snapshot4.id
    assert snapshot6.instance  # possible to restore
    assert [int(f.id) for f in snapshot6.instance.fields] == list(range(0, 12))


def test_snapshot_instance(pub):
    formdef = FormDef()
    formdef.name = 'testform'
    formdef.fields = []
    formdef.store()

    carddef = CardDef()
    carddef.name = 'testcard'
    carddef.fields = []
    carddef.store()

    # remove existing snapshots as they may be duplicated if table_name was
    # generated in a different second.
    pub.snapshot_class.wipe()

    carddef.name = 'testcard2'
    carddef.store()

    for i in range(10):
        formdef.name = 'testform %s' % i
        formdef.store()

    assert pub.snapshot_class.count() == 11

    snapshots = pub.snapshot_class.select_object_history(formdef)
    assert len(snapshots) == 10
    for i in range(10):
        assert snapshots[i].serialization is None  # not loaded
        assert snapshots[i].patch is None  # not loaded
        assert pub.snapshot_class.get(snapshots[i].id).instance.name == 'testform %s' % (9 - i)

    snapshots = pub.snapshot_class.select_object_history(carddef)
    assert len(snapshots) == 1

    # check that DeprecationsScan is not run on instance load
    with mock.patch(
        'wcs.backoffice.deprecations.DeprecationsScan.check_deprecated_elements_in_object'
    ) as check:
        snapshot = pub.snapshot_class.get_latest('formdef', formdef.id)
        assert snapshot.instance
        assert check.call_args_list == []


def test_snapshot_user(pub):
    user = pub.user_class()
    user.name = 'User Name'
    user.email = 'foo@localhost'
    user.store()

    carddef = CardDef()
    carddef.name = 'testcard'
    carddef.fields = []
    carddef.store()
    snapshot = pub.snapshot_class.select_object_history(carddef)[0]
    assert snapshot.user is None

    snapshot.user_id = user.id
    snapshot.store()
    snapshot = pub.snapshot_class.select_object_history(carddef)[0]
    assert str(snapshot.user) == 'User Name'

    snapshot.user_id = 'nope'
    snapshot.store()
    snapshot = pub.snapshot_class.select_object_history(carddef)[0]
    assert str(snapshot.user) == 'unknown user'


def test_form_snapshot_404(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'testform'
    formdef.fields = []
    formdef.store()

    app = login(get_app(pub))
    app.get('/backoffice/forms/%s/history/XXX/view/' % formdef.id, status=404)


def test_form_snapshot_diff(pub):
    create_superuser(pub)
    create_role(pub)

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'testform'
    formdef.fields = []
    formdef.store()
    assert pub.snapshot_class.count() == 1
    snapshot1 = pub.snapshot_class.get_latest('formdef', formdef.id)

    formdef.fields = [StringField(id=1, label='Test')]
    formdef.store()
    assert pub.snapshot_class.count() == 2
    snapshot2 = pub.snapshot_class.get_latest('formdef', formdef.id)

    formdef.fields += [StringField(id=2, label='Test bis')]
    formdef.store()
    assert pub.snapshot_class.count() == 3
    snapshot3 = pub.snapshot_class.get_latest('formdef', formdef.id)
    snapshot3.application_slug = 'foobar'
    snapshot3.application_version = '42.0'
    snapshot3.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/history/' % formdef.id)
    assert 'name="version1" value="%s"' % snapshot3.id in resp
    assert 'name="version2" value="%s"' % snapshot3.id not in resp
    assert 'name="version1" value="%s"' % snapshot2.id in resp
    assert 'name="version2" value="%s"' % snapshot2.id in resp
    assert 'name="version1" value="%s"' % snapshot1.id not in resp
    assert 'name="version2" value="%s"' % snapshot1.id in resp
    assert '(Version 42.0)' in resp.pyquery('tr:first-child').text()

    resp = app.get(
        '/backoffice/forms/%s/history/compare?version1=%s&version2=%s'
        % (formdef.id, snapshot1.id, snapshot3.id)
    )
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot1.id, snapshot1.id) in resp
    assert 'Snapshot <a href="%s/view/">%s</a> -  (Version 42.0)' % (snapshot3.id, snapshot3.id) in resp
    assert resp.text.count('diff_sub') == 1
    assert resp.text.count('diff_add') == 23
    resp = resp.click('Compare inspect')
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot1.id, snapshot1.id) in resp
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot3.id, snapshot3.id) in resp
    assert 'http://example.net/backoffice/forms/%s/fields/1/' % formdef.id in resp
    assert 'http://example.net/backoffice/forms/%s/fields/2/' % formdef.id in resp

    resp = app.get(
        '/backoffice/forms/%s/history/compare?version1=%s&version2=%s'
        % (formdef.id, snapshot3.id, snapshot1.id)
    )
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot1.id, snapshot1.id) in resp
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot3.id, snapshot3.id) in resp
    assert resp.text.count('diff_sub') == 1
    assert resp.text.count('diff_add') == 23

    resp = app.get(
        '/backoffice/forms/%s/history/compare?version1=%s&version2=%s'
        % (formdef.id, snapshot2.id, snapshot3.id)
    )
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot2.id, snapshot2.id) in resp
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot3.id, snapshot3.id) in resp
    assert resp.text.count('diff_sub') == 0
    assert resp.text.count('diff_add') == 11

    formdef.fields = [StringField(id=1, label='Test')]
    formdef.store()
    assert pub.snapshot_class.count() == 4
    snapshot4 = pub.snapshot_class.get_latest('formdef', formdef.id)

    resp = app.get(
        '/backoffice/forms/%s/history/compare?version1=%s&version2=%s'
        % (formdef.id, snapshot3.id, snapshot4.id)
    )
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot3.id, snapshot3.id) in resp
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot4.id, snapshot4.id) in resp
    assert resp.text.count('diff_sub') == 11
    assert resp.text.count('diff_add') == 0

    app.get('/backoffice/forms/%s/history/compare' % (formdef.id), status=404)
    app.get('/backoffice/forms/%s/history/compare?version1=%s' % (formdef.id, snapshot4.id), status=404)
    app.get('/backoffice/forms/%s/history/compare?version2=%s' % (formdef.id, snapshot4.id), status=404)
    app.get(
        '/backoffice/forms/%s/history/compare?version1=%s&version2=%s' % (formdef.id, snapshot3.id, 0),
        status=404,
    )
    app.get(
        '/backoffice/forms/%s/history/compare?version1=%s&version2=%s' % (formdef.id, 0, snapshot4.id),
        status=404,
    )
    app.get(
        '/backoffice/forms/%s/history/compare?version1=%s&version2=%s&mode=foobar'
        % (formdef.id, snapshot1.id, snapshot3.id),
        status=404,
    )

    # check compare on application version number
    snapshot2.application_slug = 'foobar'
    snapshot2.application_version = '41.0'
    snapshot2.store()
    # application not found
    resp = app.get(
        '/backoffice/forms/%s/history/compare?application=foobaz&version1=41.0&version2=42.0' % formdef.id
    )
    assert resp.location.endswith('/backoffice/forms/%s/history/' % formdef.id)
    # version1 not found
    resp = app.get(
        '/backoffice/forms/%s/history/compare?application=foobar&version1=40.0&version2=42.0' % formdef.id
    )
    assert resp.location.endswith('/backoffice/forms/%s/history/' % formdef.id)
    # version2 not found
    resp = app.get(
        '/backoffice/forms/%s/history/compare?application=foobar&version1=41.0&version2=43.0' % formdef.id
    )
    assert resp.location.endswith('/backoffice/forms/%s/history/' % formdef.id)
    # ok
    resp = app.get(
        '/backoffice/forms/%s/history/compare?application=foobar&version1=41.0&version2=42.0' % formdef.id
    )
    assert 'Snapshot <a href="%s/view/">%s</a> -  (Version 41.0)' % (snapshot2.id, snapshot2.id) in resp
    assert 'Snapshot <a href="%s/view/">%s</a> -  (Version 42.0)' % (snapshot3.id, snapshot3.id) in resp


def test_form_snapshot_diff_with_reference_error(pub):
    create_superuser(pub)
    create_role(pub)

    BlockDef.wipe()
    blockdef = BlockDef()
    blockdef.name = 'testblock'
    blockdef.fields = []
    blockdef.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'testform'
    formdef.fields = [
        BlockField(id='1', label='block1', varname='foo', block_slug=blockdef.slug),
    ]
    formdef.store()
    assert pub.snapshot_class.count() == 2
    snapshot1 = pub.snapshot_class.get_latest('formdef', formdef.id)

    formdef.fields.append(StringField(id=2, label='Test'))
    formdef.store()
    assert pub.snapshot_class.count() == 3

    formdef.fields = formdef.fields[1:]
    formdef.store()
    assert pub.snapshot_class.count() == 4
    snapshot3 = pub.snapshot_class.get_latest('formdef', formdef.id)

    app = login(get_app(pub))
    resp = app.get(
        '/backoffice/forms/%s/history/compare?version1=%s&version2=%s'
        % (formdef.id, snapshot1.id, snapshot3.id)
    )
    assert resp.pyquery('h2').text() == 'Compare snapshots (XML)'
    resp = app.get(
        '/backoffice/forms/%s/history/compare?version1=%s&version2=%s&mode=inspect'
        % (formdef.id, snapshot1.id, snapshot3.id)
    )
    assert resp.pyquery('h2').text() == 'Compare snapshots (Inspect)'

    BlockDef.wipe()
    resp = app.get(
        '/backoffice/forms/%s/history/compare?version1=%s&version2=%s'
        % (formdef.id, snapshot1.id, snapshot3.id)
    )
    assert resp.pyquery('h2').text() == 'Compare snapshots (XML)'
    resp = app.get(
        '/backoffice/forms/%s/history/compare?version1=%s&version2=%s&mode=inspect'
        % (formdef.id, snapshot1.id, snapshot3.id)
    )
    assert resp.pyquery('h2').text() == 'Error'
    assert 'Can not display snapshot (Unknown referenced objects)' in resp.text


def test_form_snapshot_comments(pub):
    create_superuser(pub)
    create_role(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/')

    resp = resp.click('New Form')
    resp.form['name'] = 'form title'
    resp = resp.form.submit().follow()
    # .store() then .disabled = True, then .store() again -> 2.
    assert pub.snapshot_class.count() == 2

    resp = resp.click('Confirmation Page')
    assert resp.form['confirmation'].checked
    resp.form['confirmation'].checked = False
    resp = resp.form.submit().follow()
    assert pub.snapshot_class.count() == 3
    assert (
        pub.snapshot_class.select(order_by='-timestamp')[0].comment
        == 'Changed "Confirmation Page" parameters'
    )

    resp = resp.click(href='fields/')
    resp.forms[0]['label'] = 'foobar'
    resp.forms[0]['type'] = 'string'
    resp = resp.forms[0].submit().follow()
    assert pub.snapshot_class.select(order_by='-timestamp')[0].comment == 'New field "foobar"'

    resp.forms[0]['label'] = 'foo' * 30
    resp.forms[0]['type'] = 'string'
    resp = resp.forms[0].submit().follow()
    assert (
        pub.snapshot_class.select(order_by='-timestamp')[0].comment
        == 'New field "foofoofoofoofoofoofoofoofoo(…)"'
    )


def test_form_snapshot_history(pub, formdef_with_history):
    create_superuser(pub)
    create_role(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/' % formdef_with_history.id)
    resp = resp.click('History')
    assert [x.attrib['class'] for x in resp.pyquery.find('.snapshots-list tr')] == [
        'new-day',
        'collapsed initially-collapsed',
        'collapsed initially-collapsed',
        'collapsed initially-collapsed',
        'collapsed initially-collapsed',
        'collapsed initially-collapsed',
    ]


def test_form_snapshot_export(pub, formdef_with_history):
    create_superuser(pub)
    create_role(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/%s/history/' % formdef_with_history.id)

    snapshot = pub.snapshot_class.select_object_history(formdef_with_history)[2]
    resp_export = resp.click(href='%s/export' % snapshot.id)
    assert resp_export.content_type == 'application/x-wcs-snapshot'
    assert '>testform 2<' in resp_export.text


def test_form_snapshot_restore(pub, formdef_with_history):
    create_superuser(pub)
    create_role(pub)
    app = login(get_app(pub))

    # restore as new
    resp = app.get('/backoffice/forms/%s/history/' % formdef_with_history.id)
    snapshot = pub.snapshot_class.select_object_history(formdef_with_history)[2]
    resp = resp.click(href='%s/restore' % snapshot.id)
    assert resp.form['action'].value == 'as-new'
    resp = resp.form.submit('submit')
    assert FormDef.count() == 2
    formdef = FormDef.get(resp.location.split('/')[-2])
    assert formdef.url_name != formdef_with_history.url_name
    assert not hasattr(formdef, 'snapshot_object')

    # restore over
    resp = app.get('/backoffice/forms/%s/history/' % formdef_with_history.id)
    snapshot = pub.snapshot_class.select_object_history(formdef_with_history)[2]
    resp = resp.click(href='%s/restore' % snapshot.id)
    resp.form['action'].value = 'overwrite'
    resp = resp.form.submit('submit')
    assert FormDef.count() == 2
    formdef = FormDef.get(resp.location.split('/')[-2])
    assert formdef.id == formdef_with_history.id
    assert formdef.url_name == formdef_with_history.url_name


def test_form_snapshot_restore_with_import_error(pub):
    create_superuser(pub)
    create_role(pub)
    app = login(get_app(pub))

    formdef = FormDef()
    formdef.name = 'testform'
    formdef.fields = [ItemField(id='1', label='Test', data_source={'type': 'unknown'})]
    formdef.store()
    assert pub.snapshot_class.count() == 1
    snapshot = pub.snapshot_class.select_object_history(formdef)[0]
    resp = app.get('/backoffice/forms/%s/history/%s/restore' % (formdef.id, snapshot.id))
    resp = resp.form.submit('submit')
    assert 'Can not restore snapshot (Unknown referenced objects [Unknown datasources: unknown])' in resp


def test_block_snapshot_browse(pub):
    create_superuser(pub)
    create_role(pub)

    BlockDef.wipe()
    blockdef = BlockDef()
    blockdef.name = 'testblock'
    blockdef.fields = []
    blockdef.store()
    assert pub.snapshot_class.count() == 1
    # check calling .store() without changes doesn't create snapshots
    blockdef.store()
    assert pub.snapshot_class.count() == 1

    blockdef.fields = [StringField(id='1', label='Test')]
    blockdef.store()
    assert pub.snapshot_class.count() == 2

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/blocks/%s/history/' % blockdef.id)
    snapshot = pub.snapshot_class.select_object_history(blockdef)[0]
    resp = resp.click(href='%s/view/' % snapshot.id)
    assert 'This block of fields is readonly.' in resp.text
    assert '<p>%s</p>' % localstrftime(snapshot.timestamp) in resp.text
    # check navigation links are ok
    for nav_item in resp.pyquery('.snapshots-navigation a:not(.disabled)'):
        resp.click(href=nav_item.attrib['href'], index=0)

    resp = app.get('/backoffice/forms/blocks/%s/history/%s/view/' % (blockdef.id, snapshot.id))
    resp.click(href='inspect')


def test_block_snapshot_browse_with_import_error(pub):
    create_superuser(pub)
    create_role(pub)
    app = login(get_app(pub))

    BlockDef.wipe()
    blockdef = BlockDef()
    blockdef.name = 'testblock'
    blockdef.fields = [ItemField(id='1', label='Test')]
    blockdef.store()
    assert pub.snapshot_class.count() == 1
    snapshot = pub.snapshot_class.select_object_history(blockdef)[0]
    # alter snapshot to simulate an unknown field type
    snapshot.serialization = snapshot.get_serialization().replace('<type>item</type>', '<type>foobar</type>')
    snapshot.store()
    resp = app.get('/backoffice/forms/blocks/%s/history/%s/view/' % (blockdef.id, snapshot.id), status=302)
    assert resp.location == 'http://example.net/backoffice/forms/blocks/%s/history/' % blockdef.id
    resp = resp.follow()
    assert 'Can not display snapshot (Unknown referenced objects [Unknown field types: foobar])' in resp

    resp = app.get('/backoffice/forms/blocks/%s/history/%s/inspect' % (blockdef.id, snapshot.id), status=302)
    assert resp.location == 'http://example.net/backoffice/forms/blocks/%s/history/' % blockdef.id
    resp = resp.follow()
    assert 'Can not inspect snapshot (Unknown referenced objects [Unknown field types: foobar])' in resp


def test_block_snapshot_restore_with_import_error(pub):
    create_superuser(pub)
    create_role(pub)
    app = login(get_app(pub))

    BlockDef.wipe()
    blockdef = BlockDef()
    blockdef.name = 'testblock'
    blockdef.fields = [ItemField(id='1', label='Test', data_source={'type': 'unknown'})]
    blockdef.store()
    assert pub.snapshot_class.count() == 1
    snapshot = pub.snapshot_class.select_object_history(blockdef)[0]
    resp = app.get('/backoffice/forms/blocks/%s/history/%s/restore' % (blockdef.id, snapshot.id))
    resp = resp.form.submit('submit')
    assert 'Can not restore snapshot (Unknown referenced objects [Unknown datasources: unknown])' in resp


def test_card_snapshot_browse(pub):
    create_superuser(pub)
    create_role(pub)

    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'testcard'
    carddef.fields = []
    carddef.store()
    assert pub.snapshot_class.count() == 1
    # check calling .store() without changes doesn't create snapshots
    carddef.store()
    assert pub.snapshot_class.count() == 1

    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'shared form view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.visibility = 'any'
    custom_view.store()

    # new version has custom views
    carddef.name = 'test 1'
    carddef.store()

    # delete custom views
    pub.custom_view_class.wipe()

    app = login(get_app(pub))

    resp = app.get('/backoffice/cards/%s/history/' % carddef.id)
    snapshot = pub.snapshot_class.select_object_history(carddef)[0]
    resp = resp.click(href='%s/view/' % snapshot.id)
    assert 'This card model is readonly' in resp.text
    assert '<p>%s</p>' % localstrftime(snapshot.timestamp) in resp.text
    # check navigation links are ok
    for nav_item in resp.pyquery('.snapshots-navigation a:not(.disabled)'):
        resp.click(href=nav_item.attrib['href'], index=0)
    # check option dialogs only have a cancel button
    resp = resp.click(href='options/management')
    assert [x[0].name for x in resp.form.fields.values() if x[0].tag == 'button'] == ['cancel']
    assert pub.custom_view_class.count() == 0  # custom views are not restore on preview

    # check navigation between inspect pages
    resp = app.get('/backoffice/cards/%s/history/%s/view/' % (carddef.id, snapshot.id))
    resp = resp.click(href='inspect')
    assert resp.pyquery('.snapshots-navigation')  # check snapshot navigation is visible
    resp.click('&gt;')  # go to inspect view of previous snapshot


def test_datasource_snapshot_browse(pub):
    create_superuser(pub)
    create_role(pub)

    NamedDataSource.wipe()
    datasource = NamedDataSource(name='test')
    datasource.data_source = {
        'type': 'jsonvalue',
        'value': '[{"id": "1", "text": "un"}, {"id": "2", "text": "deux"}]',
    }
    datasource.store()
    assert pub.snapshot_class.count() == 1
    # check calling .store() without changes doesn't create snapshots
    datasource.store()
    assert pub.snapshot_class.count() == 1

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/data-sources/%s/history/' % datasource.id)
    snapshot = pub.snapshot_class.select_object_history(datasource)[0]
    resp = resp.click(href='%s/view/' % snapshot.id)
    assert 'This data source is readonly' in resp.text
    assert '<p>%s</p>' % localstrftime(snapshot.timestamp) in resp.text
    with pytest.raises(IndexError):
        resp = resp.click('Edit')
    with pytest.raises(IndexError):
        resp = resp.click('Duplicate')

    resp = app.get('/backoffice/forms/data-sources/%s/history/%s/view/' % (datasource.id, snapshot.id))
    assert 'inspect' not in resp


def test_form_snapshot_browse(pub, formdef_with_history):
    create_superuser(pub)
    create_role(pub)
    app = login(get_app(pub))

    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'shared form view'
    custom_view.formdef = formdef_with_history
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.visibility = 'any'
    custom_view.store()

    # version 5 has custom views
    formdef_with_history.name = 'testform 5'
    formdef_with_history.description = 'this is a description (5)'
    formdef_with_history.store()
    assert pub.snapshot_class.count() == 7
    # check calling .store() without changes doesn't create snapshots
    formdef_with_history.store()
    assert pub.snapshot_class.count() == 7

    # delete custom views
    pub.custom_view_class.wipe()

    resp = app.get('/backoffice/forms/%s/history/' % formdef_with_history.id)
    snapshot = pub.snapshot_class.select_object_history(formdef_with_history)[0]
    resp = resp.click(href='%s/view/' % snapshot.id)
    assert 'This form is readonly' in resp.text
    assert '<p>%s</p>' % localstrftime(snapshot.timestamp) in resp.text

    assert '<a class="button disabled" href="#">&Lt;</a>' in resp.text
    assert '<a class="button disabled" href="#">&LT;</a>' in resp.text
    assert '<a class="button" href="../../%s/view/">&GT;</a>' % (snapshot.id - 1) in resp.text
    assert '<a class="button" href="../../%s/view/">&Gt;</a>' % (snapshot.id - 6) in resp.text
    # check navigation links are ok
    for nav_item in resp.pyquery('.snapshots-navigation a:not(.disabled)'):
        resp.click(href=nav_item.attrib['href'], index=0)

    resp = resp.click(href='../../%s/view/' % (snapshot.id - 1))
    assert '<a class="button" href="../../%s/view/">&Lt;</a>' % snapshot.id in resp.text
    assert '<a class="button" href="../../%s/view/">&LT;</a>' % snapshot.id in resp.text
    assert '<a class="button" href="../../%s/view/">&GT;</a>' % (snapshot.id - 2) in resp.text
    assert '<a class="button" href="../../%s/view/">&Gt;</a>' % (snapshot.id - 6) in resp.text

    resp = resp.click(href='../../%s/view/' % (snapshot.id - 6))
    assert '<a class="button" href="../../%s/view/">&Lt;</a>' % snapshot.id in resp.text
    assert '<a class="button" href="../../%s/view/">&LT;</a>' % (snapshot.id - 5) in resp.text
    assert '<a class="button disabled" href="#">&GT;</a>' in resp.text
    assert '<a class="button disabled" href="#">&Gt;</a>' in resp.text

    resp = resp.click(href='../../%s/view/' % snapshot.id)
    resp = resp.click('Description')
    assert resp.form['description'].value == 'this is a description (5)'
    assert [x[0].name for x in resp.form.fields.values() if x[0].tag == 'button'] == ['cancel']
    assert pub.custom_view_class.count() == 0  # custom views are not restore on preview

    resp = app.get('/backoffice/forms/%s/history/' % formdef_with_history.id)
    resp = resp.click(href='%s/view/' % (snapshot.id - 1))
    resp = resp.click(href='fields/', index=0)
    assert 'sortable multipage readonly' in resp.text
    assert '<a class="button" href="../../../%s/view/fields/">&Lt;</a>' % snapshot.id in resp.text
    assert '<a class="button" href="../../../%s/view/fields/">&LT;</a>' % snapshot.id in resp.text
    assert '<a class="button" href="../../../%s/view/fields/">&GT;</a>' % (snapshot.id - 2) in resp.text
    assert '<a class="button" href="../../../%s/view/fields/">&Gt;</a>' % (snapshot.id - 6) in resp.text

    # check link on sub page
    resp = resp.click(href='pages/0/')
    assert 'sortable multipage readonly' in resp.text
    assert (
        '<a class="button" href="../../../../../%s/view/fields/pages/0/">&Lt;</a>' % snapshot.id in resp.text
    )
    assert (
        '<a class="button" href="../../../../../%s/view/fields/pages/0/">&LT;</a>' % snapshot.id in resp.text
    )
    assert (
        '<a class="button" href="../../../../../%s/view/fields/pages/0/">&GT;</a>' % (snapshot.id - 2)
        in resp.text
    )
    assert (
        '<a class="button" href="../../../../../%s/view/fields/pages/0/">&Gt;</a>' % (snapshot.id - 6)
        in resp.text
    )

    resp = app.get('/backoffice/forms/%s/history/%s/view/' % (formdef_with_history.id, snapshot.id))
    resp.click(href='inspect')


def test_form_snapshot_browse_with_import_error(pub):
    create_superuser(pub)
    create_role(pub)
    app = login(get_app(pub))

    formdef = FormDef()
    formdef.name = 'testform'
    formdef.fields = [ItemField(id='1', label='Test', data_source={'type': 'unknown'})]
    formdef.store()
    assert pub.snapshot_class.count() == 1
    snapshot = pub.snapshot_class.select_object_history(formdef)[0]
    # no error for missing datasource
    resp = app.get('/backoffice/forms/%s/history/%s/view/' % (formdef.id, snapshot.id), status=200)

    # other FormdefImportError
    formdef.fields = [ItemField(id='1', label='Test')]
    formdef.store()
    assert pub.snapshot_class.count() == 2
    snapshot = pub.snapshot_class.select_object_history(formdef)[0]
    # alter snapshot to simulate an unknown field type
    snapshot.serialization = snapshot.get_serialization().replace('<type>item</type>', '<type>foobar</type>')
    snapshot.store()
    resp = app.get('/backoffice/forms/%s/history/%s/view/' % (formdef.id, snapshot.id), status=302)
    assert resp.location == 'http://example.net/backoffice/forms/%s/history/' % formdef.id
    resp = resp.follow()
    assert 'Can not display snapshot (Unknown referenced objects [Unknown field types: foobar])' in resp

    resp = app.get('/backoffice/forms/%s/history/%s/inspect' % (formdef.id, snapshot.id), status=302)
    assert resp.location == 'http://example.net/backoffice/forms/%s/history/' % formdef.id
    resp = resp.follow()
    assert 'Can not inspect snapshot (Unknown referenced objects [Unknown field types: foobar])' in resp


def test_workflow_snapshot_diff(pub):
    create_superuser(pub)
    create_role(pub)

    Workflow.wipe()
    workflow = Workflow(name='test')
    workflow.store()
    assert pub.snapshot_class.count() == 1
    snapshot1 = pub.snapshot_class.get_latest('workflow', workflow.id)

    workflow.add_status('Status1', 'st1')
    workflow.store()
    assert pub.snapshot_class.count() == 2
    snapshot2 = pub.snapshot_class.get_latest('workflow', workflow.id)

    ac1 = workflow.add_global_action('Action', 'ac1')
    trigger = ac1.triggers[0]
    assert trigger.key == 'manual'
    trigger.roles = ['foobar']
    workflow.store()
    assert pub.snapshot_class.count() == 3
    snapshot3 = pub.snapshot_class.get_latest('workflow', workflow.id)

    workflow.global_actions = []
    workflow.store()
    assert pub.snapshot_class.count() == 4
    snapshot4 = pub.snapshot_class.get_latest('workflow', workflow.id)

    app = login(get_app(pub))
    resp = app.get(
        '/backoffice/workflows/%s/history/compare?version1=%s&version2=%s&mode=inspect'
        % (workflow.id, snapshot1.id, snapshot2.id)
    )
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot1.id, snapshot1.id) in resp
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot2.id, snapshot2.id) in resp
    assert 'id="tab-statuses"' in resp
    assert 'id="tab-global-actions"' not in resp

    resp = app.get(
        '/backoffice/workflows/%s/history/compare?version1=%s&version2=%s&mode=inspect'
        % (workflow.id, snapshot2.id, snapshot3.id)
    )
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot2.id, snapshot2.id) in resp
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot3.id, snapshot3.id) in resp
    assert (
        'http://example.net/backoffice/workflows/%s/history/%s/view/global-actions/ac1/'
        % (workflow.id, snapshot3.id)
        in resp
    )
    assert (
        'http://example.net/backoffice/workflows/%s/history/%s/view/status/st1/' % (workflow.id, snapshot3.id)
        in resp
    )
    assert 'id="tab-statuses"' in resp
    assert 'id="tab-global-actions"' in resp

    resp = app.get(
        '/backoffice/workflows/%s/history/compare?version1=%s&version2=%s&mode=inspect'
        % (workflow.id, snapshot3.id, snapshot4.id)
    )
    assert 'id="tab-statuses"' in resp
    assert 'id="tab-global-actions"' in resp

    resp = app.get(
        '/backoffice/workflows/%s/history/compare?version1=%s&version2=%s&mode=inspect'
        % (workflow.id, snapshot1.id, snapshot4.id)
    )
    assert 'id="tab-statuses"' in resp
    assert 'id="tab-global-actions"' not in resp


def test_workflow_snapshot_browse(pub):
    create_superuser(pub)
    create_role(pub)

    Workflow.wipe()
    workflow = Workflow(name='test')
    workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow)
    workflow.backoffice_fields_formdef.fields = [StringField(id='bo1', label='backoffice field')]
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields = [StringField(id='0', label='foo', varname='variable field')]
    global_action = workflow.add_global_action('Action', 'ac1')
    global_action.add_action('remove')
    workflow.store()
    assert pub.snapshot_class.count() == 1
    # check calling .store() without changes doesn't create snapshots
    workflow.store()
    assert pub.snapshot_class.count() == 1

    # create a new snapshot
    workflow.name = 'new name'
    workflow.store()
    assert pub.snapshot_class.count() == 2

    app = login(get_app(pub))

    resp = app.get('/backoffice/workflows/%s/history/' % workflow.id)
    snapshot = pub.snapshot_class.select_object_history(workflow)[0]
    resp = resp.click(href='%s/view/' % snapshot.id)
    assert 'This workflow is readonly' in resp.text
    assert '<p>%s</p>' % localstrftime(snapshot.timestamp) in resp.text

    # check restore/export links of sidebar
    # latest version has its restore link disabled
    assert [(x.text, 'disabled' in x.attrib['class']) for x in resp.pyquery('#sidebar [role="button"]')] == [
        ('Restore version', True),
        ('Export version', False),
        ('Inspect version', False),
    ]
    resp = resp.click('&gt;')
    assert [(x.text, 'disabled' in x.attrib['class']) for x in resp.pyquery('#sidebar [role="button"]')] == [
        ('Restore version', False),
        ('Export version', False),
        ('Inspect version', False),
    ]
    resp.click('Restore version')
    resp_export = resp.click('Export version')
    assert 'snapshot-workflow' in resp_export.headers['Content-Disposition']

    # check actions are displayed
    resp = resp.click('Action')
    resp = resp.click('Global Action: Action')
    assert resp.pyquery('#items-list li').text() == 'Deletion'

    # check backoffice fields cannot be edited
    resp = app.get('/backoffice/workflows/%s/history/' % workflow.id)
    snapshot = pub.snapshot_class.select_object_history(workflow)[0]
    resp = resp.click(href='%s/view/' % snapshot.id)
    resp = resp.click('backoffice field')
    assert '>Submit<' not in resp

    # check workflow options fields cannot be edited
    resp = app.get('/backoffice/workflows/%s/history/' % workflow.id)
    snapshot = pub.snapshot_class.select_object_history(workflow)[0]
    resp = resp.click(href='%s/view/' % snapshot.id)
    resp = resp.click('variable field')
    assert '>Submit<' not in resp

    resp = app.get('/backoffice/workflows/%s/history/%s/view/' % (workflow.id, snapshot.id))
    resp.click(href='inspect')


def test_workflow_snapshot_browse_with_missing_role(pub):
    create_superuser(pub)
    app = login(get_app(pub))

    pub.role_class.wipe()
    Workflow.wipe()
    wf = Workflow(name='status')

    ac1 = wf.add_global_action('Action', 'ac1')
    trigger = ac1.triggers[0]
    assert trigger.key == 'manual'
    trigger.roles = ['foobar']
    wf.store()

    assert pub.role_class.count() == 0
    assert pub.snapshot_class.count() == 1
    snapshot = pub.snapshot_class.select_object_history(wf)[0]
    assert 'foobar' not in snapshot.get_serialization()  # missing role is not saved
    app.get('/backoffice/workflows/%s/history/%s/view/' % (wf.id, snapshot.id), status=200)
    assert pub.role_class.count() == 0  # missing role was created

    # check behaviour is identical with idp-managed roles.
    pub.cfg['sp'] = {'idp-manage-roles': True}
    pub.write_cfg()
    trigger.allow_as_mass_action = False  # so there's a change in snapshot
    trigger.roles = ['foobarbaz']
    wf.store()
    assert pub.snapshot_class.count() == 2
    snapshot = pub.snapshot_class.select_object_history(wf)[0]
    assert 'foobarbaz' not in snapshot.get_serialization()  # missing role is not saved
    app.get('/backoffice/workflows/%s/history/%s/view/' % (wf.id, snapshot.id), status=200)
    assert pub.role_class.count() == 0  # missing role was not created


def test_workflow_snapshot_browse_with_import_error(pub):
    create_superuser(pub)
    app = login(get_app(pub))

    Workflow.wipe()
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    display_form = st1.add_action('form', id='_x')
    display_form.varname = 'xxx'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [ItemField(id='1', label='Test')]
    wf.store()

    assert pub.snapshot_class.count() == 1
    snapshot = pub.snapshot_class.select_object_history(wf)[0]
    snapshot.serialization = snapshot.get_serialization().replace('<type>item</type>', '<type>foobar</type>')
    snapshot.store()
    resp = app.get('/backoffice/workflows/%s/history/%s/view/' % (wf.id, snapshot.id), status=302)
    assert resp.location == 'http://example.net/backoffice/workflows/%s/history/' % wf.id
    resp = resp.follow()
    assert 'Can not display snapshot (Unknown referenced objects [Unknown field types: foobar])' in resp

    resp = app.get('/backoffice/workflows/%s/history/%s/inspect' % (wf.id, snapshot.id), status=302)
    assert resp.location == 'http://example.net/backoffice/workflows/%s/history/' % wf.id
    resp = resp.follow()
    assert 'Can not inspect snapshot (Unknown referenced objects [Unknown field types: foobar])' in resp


def test_workflow_snapshot_restore(pub):
    create_superuser(pub)
    create_role(pub)

    Workflow.wipe()
    workflow = Workflow(name='test')
    workflow.store()

    # create a new snapshot
    workflow.name = 'new name'
    workflow.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/workflows/%s/history/' % workflow.id)
    snapshot = pub.snapshot_class.select_object_history(workflow)[0]
    resp = resp.click(href='%s/view/' % snapshot.id)

    # restore over
    resp = app.get('/backoffice/workflows/%s/history/' % workflow.id)
    snapshot = pub.snapshot_class.select_object_history(workflow)[-1]
    resp = resp.click(href='%s/restore' % snapshot.id)
    resp.form['action'].value = 'overwrite'
    resp = resp.form.submit('submit')
    assert Workflow.count() == 1
    assert not hasattr(Workflow.get(workflow.id), 'snapshot_object')


def test_workflow_snapshot_restore_with_import_error(pub):
    create_superuser(pub)
    app = login(get_app(pub))

    Workflow.wipe()
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    display_form = st1.add_action('form', id='_x')
    display_form.varname = 'xxx'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields = [ItemField(id='1', label='Test', data_source={'type': 'unknown'})]
    wf.store()

    assert pub.snapshot_class.count() == 1
    snapshot = pub.snapshot_class.select_object_history(wf)[0]
    resp = app.get('/backoffice/workflows/%s/history/%s/restore' % (wf.id, snapshot.id))
    resp = resp.form.submit('submit')
    assert 'Can not restore snapshot (Unknown referenced objects [Unknown datasources: unknown])' in resp


def test_workflow_snapshot_restore_with_missing_role(pub):
    create_superuser(pub)
    app = login(get_app(pub))

    pub.role_class.wipe()
    Workflow.wipe()

    role = pub.role_class(name='foobar')
    role.store()
    wf = Workflow(name='status')

    ac1 = wf.add_global_action('Action', 'ac1')
    trigger = ac1.triggers[0]
    assert trigger.key == 'manual'
    trigger.roles = [role.id]
    wf.store()

    assert pub.snapshot_class.count() == 1
    snapshot = pub.snapshot_class.select_object_history(wf)[0]
    pub.role_class.wipe()
    resp = app.get('/backoffice/workflows/%s/history/%s/restore' % (wf.id, snapshot.id), status=200)
    resp.form['action'].value = 'overwrite'
    resp = resp.form.submit('submit')
    assert pub.role_class.count() == 1  # missing role was created
    wf = Workflow.get(resp.location.split('/')[-2])
    assert wf.global_actions[0].triggers[0].roles == ['1']
    assert pub.snapshot_class.count() == 1

    pub.role_class.wipe()
    role = pub.role_class(name='foobarbaz')
    role.id = '123'
    role.store()
    pub.cfg['sp'] = {'idp-manage-roles': True}
    pub.write_cfg()
    wf.global_actions[0].triggers[0].roles = [role.id]
    wf.store()
    assert pub.snapshot_class.count() == 2
    snapshot = pub.snapshot_class.select_object_history(wf)[0]
    pub.role_class.wipe()
    resp = app.get('/backoffice/workflows/%s/history/%s/restore' % (wf.id, snapshot.id), status=200)
    resp.form['action'].value = 'overwrite'
    resp = resp.form.submit('submit')
    assert pub.role_class.count() == 0  # missing role was not created
    wf = Workflow.get(resp.location.split('/')[-2])
    assert wf.global_actions[0].triggers[0].roles == ['123']  # kept
    # no error raised due to unknown role
    app.get(
        '/backoffice/workflows/%s/global-actions/ac1/triggers/%s/'
        % (wf.id, wf.global_actions[0].triggers[0].id)
    )


def test_workflow_with_model_snapshot_browse(pub):
    create_superuser(pub)
    create_role(pub)

    Workflow.wipe()
    if os.path.exists(os.path.join(pub.app_dir, 'models')):
        shutil.rmtree(os.path.join(pub.app_dir, 'models'))
    workflow = Workflow(name='test')
    st1 = workflow.add_status('Status1', 'st1')
    export_to = st1.add_action('export_to_model')
    export_to.label = 'test'
    upload = Upload('/foo/bar', content_type='application/vnd.oasis.opendocument.text')
    file_content = b'''PK\x03\x04\x14\x00\x00\x08\x00\x00\'l\x8eG^\xc62\x0c\'\x00'''
    upload.fp = io.BytesIO()
    upload.fp.write(file_content)
    upload.fp.seek(0)
    export_to.model_file = UploadedFile('models', 'tmp', upload)

    # export/import to get models stored in the expected way
    workflow.store()
    workflow = Workflow.import_from_xml_tree(
        ET.fromstring(ET.tostring(workflow.export_to_xml(include_id=True))), include_id=True
    )
    workflow.store()
    assert len(os.listdir(os.path.join(pub.app_dir, 'models'))) == 2

    workflow = Workflow.import_from_xml_tree(
        ET.fromstring(ET.tostring(workflow.export_to_xml(include_id=True))), include_id=True
    )
    workflow.store()
    assert len(os.listdir(os.path.join(pub.app_dir, 'models'))) == 2

    app = login(get_app(pub))

    for i in range(3):
        # check document model is not overwritten
        resp = app.get('/backoffice/workflows/%s/history/' % workflow.id)
        snapshot = pub.snapshot_class.select_object_history(workflow)[0]
        resp = resp.click(href='%s/view/' % snapshot.id)
        assert 'This workflow is readonly' in resp.text
        filenames = os.listdir(os.path.join(pub.app_dir, 'models'))
        assert len(filenames) == 3 + i
        assert (
            len([f for f in filenames if f.startswith('export_to_model-snapshot') and f.endswith('.upload')])
            == 1 + i
        )

    # create a new snapshot
    workflow.name = 'new name'
    workflow.store()

    resp = app.get('/backoffice/workflows/%s/history/' % workflow.id)
    resp = resp.click(href='%s/restore' % snapshot.id)
    assert resp.form['action'].value == 'as-new'
    resp = resp.form.submit('submit')
    assert Workflow.count() == 2
    Workflow.get(2)
    assert list(workflow.get_all_items())[0].key == 'export_to_model'
    assert list(workflow.get_all_items())[0].model_file.filename == 'export_to_model-1-st1-1.upload'

    resp = app.get('/backoffice/workflows/%s/history/' % workflow.id)
    resp_export = resp.click(href='%s/export' % snapshot.id)
    assert resp_export.content_type == 'application/x-wcs-snapshot'
    workflow = Workflow.import_from_xml_tree(ET.fromstring(resp_export.text))
    workflow.store()
    assert list(workflow.get_all_items())[0].key == 'export_to_model'
    assert list(workflow.get_all_items())[0].model_file.filename != 'export_to_model-1-st1-1.upload'
    assert 'snapshot' not in list(workflow.get_all_items())[0].model_file.filename


def test_workflow_with_form_snapshot_browse(pub):
    create_superuser(pub)
    create_role(pub)

    Workflow.wipe()
    wf = Workflow(name='status')
    st1 = wf.add_status('Status1', 'st1')

    display_form = st1.add_action('form', id='_x')
    display_form.varname = 'xxx'
    display_form.formdef = WorkflowFormFieldsFormDef(item=display_form)
    display_form.formdef.fields.append(StringField(id='1', label='Test'))
    wf.store()
    snapshot = pub.snapshot_class.select_object_history(wf)[0]

    app = login(get_app(pub))
    resp = app.get(
        '/backoffice/workflows/%s/history/%s/view/status/st1/items/_x/fields/' % (wf.id, snapshot.id)
    )
    assert 'The fields are readonly' in resp.text  # ok, no error
    assert 'sortable readonly' in resp.text


def test_wscall_snapshot_browse(pub):
    create_superuser(pub)
    create_role(pub)

    NamedWsCall.wipe()
    wscall = NamedWsCall(name='test')
    wscall.store()
    assert pub.snapshot_class.count() == 1
    # check calling .store() without changes doesn't create snapshots
    wscall.store()
    assert pub.snapshot_class.count() == 1

    app = login(get_app(pub))

    resp = app.get('/backoffice/settings/wscalls/%s/history/' % wscall.id)
    snapshot = pub.snapshot_class.select_object_history(wscall)[0]
    resp = resp.click(href='%s/view/' % snapshot.id)
    assert 'This webservice call is readonly' in resp.text
    assert '<p>%s</p>' % localstrftime(snapshot.timestamp) in resp.text
    with pytest.raises(IndexError):
        resp = resp.click('Edit')

    resp = app.get('/backoffice/settings/wscalls/%s/history/%s/view/' % (wscall.id, snapshot.id))
    assert 'inspect' not in resp


def test_form_snapshot_save(pub, formdef_with_history):
    create_superuser(pub)
    create_role(pub)
    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/%s/' % formdef_with_history.id)
    resp = resp.click('Save snapshot')
    resp.form['label'] = 'test snapshot'
    resp = resp.form.submit('submit')

    # add more snapshots
    formdef = FormDef.get(id=formdef_with_history.id)
    for i in range(10, 15):
        formdef.description = 'this is a description (%s)' % i
        formdef.store()

    resp = app.get('/backoffice/forms/%s/history/' % formdef_with_history.id)
    assert [x.attrib['class'] for x in resp.pyquery.find('.snapshots-list tr')] == [
        'new-day',
        'collapsed initially-collapsed',
        'collapsed initially-collapsed',
        'collapsed initially-collapsed',
        'collapsed initially-collapsed',
        'has-label',
        'collapsed initially-collapsed',
        'collapsed initially-collapsed',
        'collapsed initially-collapsed',
        'collapsed initially-collapsed',
        'collapsed initially-collapsed',
        'collapsed initially-collapsed',
    ]


def test_snaphost_workflow_status_item_comments(pub):
    create_superuser(pub)
    create_role(pub)

    Workflow.wipe()
    workflow = Workflow(name='test')
    workflow.add_status(name='baz')
    workflow.add_status(name='hop')
    global_action = workflow.add_global_action('Action', 'ac1')
    register_comment = global_action.add_action('register-comment')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/1/')
    resp = resp.click('baz')

    resp.forms[0]['action-interaction'] = 'Webservice'
    resp = resp.forms[0].submit()
    resp = resp.follow()

    resp = resp.click(href=r'^items/1/$')
    resp.form['url'] = 'http://example.org'
    resp = resp.forms[0].submit('submit')
    resp = resp.follow()
    resp = resp.follow()

    resp = resp.click(href=r'^items/1/$')
    resp.form['label'] = 'foo'
    resp = resp.forms[0].submit('submit')
    resp = resp.follow()
    resp = resp.follow()

    resp = resp.click(href='items/1/copy')
    resp.form['status'] = 'hop'
    resp = resp.forms[0].submit('submit')
    resp = resp.follow()

    resp = resp.click(href='items/1/delete')
    resp = resp.form.submit('submit')

    resp = app.get('/backoffice/workflows/1/global-actions/ac1/items/%s/' % register_comment.id)
    resp.forms[0]['comment'] = 'xxx'
    resp = resp.form.submit('submit')

    resp = app.get('/backoffice/workflows/%s/history/' % workflow.id)
    comments = [x.text.strip() for x in resp.html.find_all('td', {'class': 'label'})]
    assert comments == [
        'Change in action "History Message (to everybody)" in global action "Action"',
        'Deletion of action "Webservice (foo)" in status "baz"',
        'Copy of action "Webservice (foo)" from status "baz" to status "hop"',
        'Change in action "Webservice (foo)" in status "baz"',
        'Change in action "Webservice" in status "baz"',
        'New action "Webservice" in status "baz"',
        '',
    ]


def test_snapshot_workflow_variable(pub):
    create_superuser(pub)
    create_role(pub)

    Workflow.wipe()
    workflow = Workflow(name='test')
    workflow.store()
    workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
    workflow.variables_formdef.fields = [
        StringField(id='0', label='foo', varname='foo'),
    ]
    workflow.store()

    app = login(get_app(pub))
    snapshot = pub.snapshot_class.get_latest('workflow', workflow.id)
    resp = app.get(
        '/backoffice/workflows/%s/history/%s/view/variables/fields/0/' % (workflow.id, snapshot.id)
    )
    assert '>Submit<' not in resp
    resp = app.get('/backoffice/workflows/%s/history/%s/view/variables/fields/' % (workflow.id, snapshot.id))
    assert 'This workflow is readonly' in resp.text
    assert 'sortable readonly' in resp.text


def test_snaphost_workflow_global_action_readonly(pub):
    create_superuser(pub)
    create_role(pub)

    Workflow.wipe()
    workflow = Workflow(name='test')
    global_action = workflow.add_global_action('Action', 'ac1')
    register_comment = global_action.add_action('register-comment')
    workflow.store()

    app = login(get_app(pub))

    resp = app.get('/backoffice/workflows/1/global-actions/ac1/items/%s/' % register_comment.id)
    resp.forms[0]['comment'] = 'xxx'
    resp = resp.form.submit('submit')

    resp = app.get('/backoffice/workflows/1/global-actions/ac1/items/%s/' % register_comment.id)
    resp.forms[0]['comment'] = 'xxx2'
    resp = resp.form.submit('submit')

    resp = app.get('/backoffice/workflows/%s/history/' % workflow.id)
    assert pub.snapshot_class.count() == 3
    snapshot_id = pub.snapshot_class.select(order_by='-id')[0].id
    resp = resp.click(href='%s/view' % snapshot_id)
    resp = resp.click('Action')
    for link in resp.pyquery('#sidebar a.button:not(.disabled)'):
        resp.click(href=link.attrib['href'])
    assert 'This workflow is readonly.' in resp.text
    resp = resp.click('Global Action: Action')
    assert 'This workflow is readonly.' in resp.text
    for link in resp.pyquery('#sidebar a.button:not(.disabled)'):
        resp.click(href=link.attrib['href'])


def test_pickle_erroneous_snapshot_object(pub):
    # check snapshot object attribute is not restored
    formdef = FormDef()
    formdef.name = 'basic formdef'
    formdef.snapshot_object = 'whatever'
    formdef.store()

    assert not hasattr(FormDef.get(formdef.id), 'snapshot_object')


def test_mail_template_snapshot_restore(pub):
    create_superuser(pub)
    create_role(pub)
    app = login(get_app(pub))
    MailTemplate.wipe()
    mail_template = MailTemplate(name='test')
    mail_template.store()
    for i in range(2):
        mail_template.name = 'test %s' % i
        mail_template.store()

    assert pub.snapshot_class.count() == 3

    # restore as new
    resp = app.get('/backoffice/workflows/mail-templates/%s/history/' % mail_template.id)
    snapshot = pub.snapshot_class.select_object_history(mail_template)[2]
    resp = resp.click(href='%s/restore' % snapshot.id)
    assert resp.form['action'].value == 'as-new'
    resp = resp.form.submit('submit')
    assert MailTemplate.count() == 2
    mail_template2 = MailTemplate.get(resp.location.split('/')[-2])
    assert mail_template2.name == 'test'
    assert mail_template2.id != mail_template.id

    # restore over
    resp = app.get('/backoffice/workflows/mail-templates/%s/history/' % mail_template.id)
    snapshot = pub.snapshot_class.select_object_history(mail_template)[2]
    resp = resp.click(href='%s/restore' % snapshot.id)
    resp.form['action'].value = 'overwrite'
    resp = resp.form.submit('submit')
    assert MailTemplate.count() == 2
    mail_template2 = MailTemplate.get(resp.location.split('/')[-2])
    assert mail_template2.id == mail_template.id

    snapshot1 = pub.snapshot_class.select_object_history(mail_template)[0]
    snapshot2 = pub.snapshot_class.select_object_history(mail_template)[1]
    app.get(
        '/backoffice/workflows/mail-templates/%s/history/compare?version1=%s&version2=%s&mode=xml'
        % (mail_template.id, snapshot1.id, snapshot2.id),
        status=200,
    )
    app.get(
        '/backoffice/workflows/mail-templates/%s/history/compare?version1=%s&version2=%s&mode=inspect'
        % (mail_template.id, snapshot1.id, snapshot2.id),
        status=404,
    )
    app.get(
        '/backoffice/workflows/mail-templates/%s/history/compare?version1=%s&version2=%s&mode=foobar'
        % (mail_template.id, snapshot1.id, snapshot2.id),
        status=404,
    )


def test_mail_template_snapshot_browse(pub):
    create_superuser(pub)
    create_role(pub)

    MailTemplate.wipe()
    mail_template = MailTemplate(name='test')
    mail_template.store()
    assert pub.snapshot_class.count() == 1
    # check calling .store() without changes doesn't create snapshots
    mail_template.store()
    assert pub.snapshot_class.count() == 1

    app = login(get_app(pub))

    resp = app.get('/backoffice/workflows/mail-templates/%s/history/' % mail_template.id)
    snapshot = pub.snapshot_class.select_object_history(mail_template)[0]
    resp = resp.click(href='%s/view/' % snapshot.id)
    assert 'This mail template is readonly' in resp.text
    assert '<p>%s</p>' % localstrftime(snapshot.timestamp) in resp.text
    with pytest.raises(IndexError):
        resp = resp.click('Edit')

    resp = app.get(
        '/backoffice/workflows/mail-templates/%s/history/%s/view/' % (mail_template.id, snapshot.id)
    )
    assert 'inspect' not in resp


def test_comment_template_snapshot_browse(pub):
    create_superuser(pub)
    create_role(pub)

    CommentTemplate.wipe()
    comment_template = CommentTemplate(name='test')
    comment_template.store()
    assert pub.snapshot_class.count() == 1
    # check calling .store() without changes doesn't create snapshots
    comment_template.store()
    assert pub.snapshot_class.count() == 1

    app = login(get_app(pub))

    resp = app.get('/backoffice/workflows/comment-templates/%s/history/' % comment_template.id)
    snapshot = pub.snapshot_class.select_object_history(comment_template)[0]
    resp = resp.click(href='%s/view/' % snapshot.id)
    assert 'This comment template is readonly' in resp.text
    assert '<p>%s</p>' % localstrftime(snapshot.timestamp) in resp.text
    with pytest.raises(IndexError):
        resp = resp.click('Edit')

    resp = app.get(
        '/backoffice/workflows/comment-templates/%s/history/%s/view/' % (comment_template.id, snapshot.id)
    )
    assert 'inspect' not in resp


def test_category_snapshot_browse(pub):
    create_superuser(pub)
    create_role(pub)

    Category.wipe()
    category = Category(name='test')
    category.position = 42
    category.store()
    assert pub.snapshot_class.count() == 1
    # check calling .store() without changes doesn't create snapshots
    category.store()
    assert pub.snapshot_class.count() == 1
    category.name = 'foobar'
    category.store()
    assert pub.snapshot_class.count() == 2

    app = login(get_app(pub))

    resp = app.get('/backoffice/forms/categories/%s/' % category.id)
    resp = resp.click('History')
    snapshot = pub.snapshot_class.select_object_history(category)[1]
    snapshot = snapshot.get_latest(
        snapshot.object_type, snapshot.object_id, complete=True, max_timestamp=snapshot.timestamp
    )
    assert snapshot.patch is None
    assert 'position' not in snapshot.serialization
    resp = resp.click(href='%s/view/' % snapshot.id)
    assert 'This category is readonly' in resp.text
    assert 'inspect' not in resp
    assert '<p>%s</p>' % localstrftime(snapshot.timestamp) in resp.text
    with pytest.raises(IndexError):
        resp.click('Edit')
    resp = app.get('/backoffice/forms/categories/%s/' % category.id)
    resp = resp.click('History')
    resp = resp.click(href='%s/restore' % snapshot.id)
    assert resp.form['action'].value == 'as-new'
    resp = resp.form.submit('submit')
    assert Category.count() == 2
    new_category = Category.get(resp.location.split('/')[-2])
    assert new_category.position == 43

    resp = app.get('/backoffice/forms/categories/%s/' % category.id)
    resp = resp.click('History')
    resp = resp.click(href='%s/restore' % snapshot.id)
    resp.form['action'].value = 'overwrite'
    resp = resp.form.submit('submit')
    assert Category.count() == 2
    category.refresh_from_storage()
    assert category.position == 42


def test_snapshots_test_results(pub):
    user = create_superuser(pub)

    formdef = FormDef()
    formdef.name = 'test title'
    formdef.fields = [
        StringField(id='1', label='String', varname='string'),
    ]
    formdef.store()

    app = login(get_app(pub))

    # make a change while there are no tests
    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['label'] = 'New label'
    resp.form.submit('submit').follow()

    resp = app.get('/backoffice/forms/1/history/')
    assert 'New label' in resp.text
    assert '/tests/results/' not in resp.text
    assert 'Tests' not in resp.text
    assert not resp.pyquery('td.test-result')

    # create test
    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data['1'] = 'a'
    formdata.user_id = user.id
    formdata.store()

    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'First test'
    testdef.store()

    # add field validation
    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['validation$type'] = 'digits'
    resp.form.submit('submit').follow()

    # test failed
    resp = app.get('/backoffice/forms/1/history/')
    assert '/tests/results/' in resp.text
    assert len(resp.pyquery('span.test-failure')) == 1
    assert len(resp.pyquery('span.test-success')) == 0

    # remove validation
    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['validation$type'] = ''
    resp.form.submit('submit').follow()

    resp = app.get('/backoffice/forms/1/history/')
    assert len(resp.pyquery('span.test-failure')) == 1
    assert len(resp.pyquery('span.test-success')) == 1

    # change field label
    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['label'] = 'xxx'
    resp.form.submit('submit').follow()

    # same result -> no new result saved
    resp = app.get('/backoffice/forms/1/history/')
    assert len(resp.pyquery('span.test-failure')) == 1
    assert len(resp.pyquery('span.test-success')) == 1

    # add new test
    testdef = TestDef.create_from_formdata(formdef, formdata)
    testdef.name = 'Second test'
    testdef.store()

    # change field label again
    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['label'] = 'yyy'
    resp.form.submit('submit').follow()

    # new test -> new result saved
    resp = app.get('/backoffice/forms/1/history/')
    assert len(resp.pyquery('span.test-failure')) == 1
    assert len(resp.pyquery('span.test-success')) == 2

    testdef.expected_error = 'xxx'
    testdef.store()

    # change field label again
    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['label'] = 'zzz'
    resp.form.submit('submit').follow()

    # new error -> new result saved
    resp = app.get('/backoffice/forms/1/history/')
    assert len(resp.pyquery('span.test-failure')) == 2
    assert len(resp.pyquery('span.test-success')) == 2

    # add hidden field
    formdef.fields.append(
        StringField(
            id='2',
            label='String',
            varname='string',
            condition={'type': 'django', 'value': 'False'},
        )
    )
    formdef.store()

    # change field label again
    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['label'] = 'xxx'
    resp.form.submit('submit').follow()

    # coverage changes -> new result saved
    resp = app.get('/backoffice/forms/1/history/')
    assert len(resp.pyquery('span.test-failure')) == 3
    assert len(resp.pyquery('span.test-success')) == 2

    # simulate old test result without coverage
    test_results = formdef.get_last_test_results()
    test_results.coverage = {}
    test_results.store()

    # change field label again
    resp = app.get('/backoffice/forms/1/fields/1/')
    resp.form['label'] = 'yyy'
    resp.form.submit('submit').follow()

    # coverage changes -> new result saved
    resp = app.get('/backoffice/forms/1/history/')
    assert len(resp.pyquery('span.test-failure')) == 4
    assert len(resp.pyquery('span.test-success')) == 2


def test_snapshot_broken_xml(pub):
    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'testform'
    formdef.fields = [CommentField(id='0', label='Test')]
    formdef.store()

    assert pub.snapshot_class.count() == 1
    snapshot1 = pub.snapshot_class.get_latest('formdef', formdef.id)
    assert snapshot1.serialization is not None
    assert '>testform<' in snapshot1.serialization
    assert snapshot1.patch is None
    assert snapshot1.instance  # possible to restore

    snapshot1.serialization = 'BROKEN XML'
    snapshot1.store()

    # previous snapshot is broken, store full serialization
    formdef.name = 'testform2'
    formdef.store()
    assert pub.snapshot_class.count() == 2
    snapshot2 = pub.snapshot_class.get_latest('formdef', formdef.id)
    assert '>testform2<' in snapshot2.serialization


def test_block_snapshot_inspect_diff(pub):
    create_superuser(pub)
    create_role(pub)

    BlockDef.wipe()
    blockdef = BlockDef()
    blockdef.name = 'testform'
    blockdef.fields = []
    blockdef.store()
    assert pub.snapshot_class.count() == 1
    snapshot1 = pub.snapshot_class.get_latest('block', blockdef.id)

    blockdef.fields = [StringField(id=1, label='Test')]
    blockdef.store()
    assert pub.snapshot_class.count() == 2
    snapshot2 = pub.snapshot_class.get_latest('block', blockdef.id)

    blockdef.fields += [StringField(id=2, label='Test bis')]
    blockdef.store()
    assert pub.snapshot_class.count() == 3
    snapshot3 = pub.snapshot_class.get_latest('block', blockdef.id)

    app = login(get_app(pub))
    resp = app.get('/backoffice/forms/blocks/%s/history/' % blockdef.id)
    assert 'name="version1" value="%s"' % snapshot3.id in resp
    assert 'name="version2" value="%s"' % snapshot3.id not in resp
    assert 'name="version1" value="%s"' % snapshot2.id in resp
    assert 'name="version2" value="%s"' % snapshot2.id in resp
    assert 'name="version1" value="%s"' % snapshot1.id not in resp
    assert 'name="version2" value="%s"' % snapshot1.id in resp

    resp = app.get(
        '/backoffice/forms/blocks/%s/history/compare?version1=%s&version2=%s'
        % (blockdef.id, snapshot1.id, snapshot3.id)
    )
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot1.id, snapshot1.id) in resp
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot3.id, snapshot3.id) in resp
    assert resp.text.count('diff_sub') == 1
    assert resp.text.count('diff_add') == 23
    resp = resp.click('Compare inspect')
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot1.id, snapshot1.id) in resp
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot3.id, snapshot3.id) in resp
    assert 'http://example.net/backoffice/forms/blocks/%s/1/' % blockdef.id in resp
    assert 'http://example.net/backoffice/forms/blocks/%s/2/' % blockdef.id in resp
    assert 'tab-usage' not in resp.text

    resp = app.get(
        '/backoffice/forms/blocks/%s/history/compare?version1=%s&version2=%s'
        % (blockdef.id, snapshot3.id, snapshot1.id)
    )
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot1.id, snapshot1.id) in resp
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot3.id, snapshot3.id) in resp
    assert resp.text.count('diff_sub') == 1
    assert resp.text.count('diff_add') == 23

    resp = app.get(
        '/backoffice/forms/blocks/%s/history/compare?version1=%s&version2=%s'
        % (blockdef.id, snapshot2.id, snapshot3.id)
    )
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot2.id, snapshot2.id) in resp
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot3.id, snapshot3.id) in resp
    assert resp.text.count('diff_sub') == 0
    assert resp.text.count('diff_add') == 11

    blockdef.fields = [StringField(id=1, label='Test')]
    blockdef.store()
    assert pub.snapshot_class.count() == 4
    snapshot4 = pub.snapshot_class.get_latest('block', blockdef.id)

    resp = app.get(
        '/backoffice/forms/blocks/%s/history/compare?version1=%s&version2=%s'
        % (blockdef.id, snapshot3.id, snapshot4.id)
    )
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot3.id, snapshot3.id) in resp
    assert 'Snapshot <a href="%s/view/">%s</a>' % (snapshot4.id, snapshot4.id) in resp
    assert resp.text.count('diff_sub') == 11
    assert resp.text.count('diff_add') == 0


def test_snapshot_cleanup(pub, freezer):
    pub.snapshot_class.wipe()
    Workflow.wipe()

    freezer.move_to('2024-06-23 12:00')
    workflow1 = Workflow(name='a')
    workflow1.store(comment='new')
    workflow_1_snapshot = pub.snapshot_class.get_latest('workflow', workflow1.id)

    workflow2 = Workflow(name='test')
    workflow2.store(comment='new')

    freezer.move_to('2024-06-23 13:00')
    workflow2.add_status('Status 1')
    st = workflow2.add_status('Status 2')
    action = st.add_action('sendmail')
    action.body = 'aaaaaaaa\n\n' * 30
    workflow2.store(comment='add sendmail')

    latest_full_snapshot = pub.snapshot_class.get_latest('workflow', workflow2.id)
    assert bool(latest_full_snapshot.serialization)

    freezer.move_to('2024-06-23 15:00')
    workflow2.name = 'test2'
    workflow2.store(comment='change name')

    latest_patch_snapshot = pub.snapshot_class.get_latest('workflow', workflow2.id)
    assert bool(latest_patch_snapshot.patch)
    assert pub.snapshot_class.count() == 4

    # snapshots are:
    # * [1] full snapshot of workflow 1
    # * [2] full snapshot of workflow 2
    # * [3] full snapshot of workflow 2, with sendmail actions
    # * [4] diff snapshot of workflow 2, with sendmail actions + name change

    pub.snapshot_class.clean()
    assert pub.snapshot_class.count() == 4

    # moving 2 months later, nothing gets removed (as the workflows are still there)
    freezer.move_to('2024-08-23 15:00')
    pub.snapshot_class.clean()
    assert pub.snapshot_class.count() == 4

    # removing second workflow
    workflow2.remove_self()
    pub.snapshot_class.clean()
    assert pub.snapshot_class.count() == 3

    # "[2] full snapshot of workflow 2" got removed
    assert {x.id for x in pub.snapshot_class.select()} == {
        workflow_1_snapshot.id,
        latest_full_snapshot.id,
        latest_patch_snapshot.id,
    }


def test_snapshot_cleanup_test_users(pub):
    pub.snapshot_class.wipe()
    pub.test_user_class.wipe()
    test_user = pub.test_user_class(name='Test User')
    test_user.test_uuid = '42'
    test_user.store()
    assert pub.snapshot_class.count() == 1
    pub.snapshot_class.clean()
    assert pub.snapshot_class.count() == 1
    assert not pub.snapshot_class.select()[0].deleted_object

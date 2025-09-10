import datetime
from collections import defaultdict

import pytest
from django.utils.timezone import make_aware

from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.comment_templates import CommentTemplate
from wcs.data_sources import NamedDataSource
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.mail_templates import MailTemplate
from wcs.qommon.http_request import HTTPRequest
from wcs.sql_criterias import Equal
from wcs.workflows import Workflow
from wcs.wscalls import NamedWsCall

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app, login
from .test_all import create_superuser


@pytest.fixture
def pub():
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
    resp = app.get('/backoffice/')
    assert 'studio' in resp.text
    resp = app.get('/backoffice/studio/')
    assert '../forms/' in resp.text
    assert '../cards/' in resp.text
    assert '../workflows/' in resp.text
    assert '../forms/data-sources/' in resp.text
    assert '../workflows/data-sources/' not in resp.text
    assert '../settings/data-sources/' not in resp.text
    assert '../forms/blocks/' in resp.text
    assert '../workflows/mail-templates/' in resp.text
    assert '../workflows/comment-templates/' in resp.text
    assert '../settings/wscalls/' in resp.text
    assert 'Recent errors' in resp.text

    pub.cfg['admin-permissions'] = {}
    for part in ('forms', 'cards', 'workflows'):
        # check section link are not displayed if user has no access right
        pub.cfg['admin-permissions'].update({part: ['x']})  # block access
        pub.write_cfg()
        if part != 'workflows':
            resp = app.get('/backoffice/studio/')
            assert '../%s/' % part not in resp.text
            assert '../forms/data-sources/' not in resp.text
            assert '../workflows/data-sources/' in resp.text
            assert '../settings/data-sources/' not in resp.text
        else:
            resp = app.get('/backoffice/studio/', status=403)  # totally closed

    resp = app.get('/backoffice/')
    assert 'backoffice/studio' not in resp.text

    # access to cards only (and settings)
    pub.cfg['admin-permissions'] = {}
    pub.cfg['admin-permissions'].update({'forms': ['x'], 'workflows': ['x']})
    pub.write_cfg()
    resp = app.get('/backoffice/studio/')
    assert '../forms/' not in resp.text
    assert '../cards/' in resp.text
    assert '../workflows/' not in resp.text
    assert '../settings/data-sources/' in resp.text
    assert '../settings/wscalls/' in resp.text

    # no access to settings
    pub.cfg['admin-permissions'].update({'settings': ['x']})
    pub.write_cfg()
    resp = app.get('/backoffice/studio/')
    assert '../forms/' not in resp.text
    assert '../cards/' in resp.text
    assert '../workflows/' not in resp.text
    assert '../settings/' not in resp.text


def test_studio_home_recent_errors(pub):
    create_superuser(pub)

    app = login(get_app(pub))
    resp = app.get('/backoffice/studio/')
    assert 'No errors' in resp.text

    def new_error():
        error = LoggedError()
        error.summary = 'Lonely Logged Error'
        error.exception_class = 'Exception'
        error.exception_message = 'foo bar'
        error.first_occurence_timestamp = datetime.datetime.now()
        error.latest_occurence_timestamp = datetime.datetime.now()
        error.occurences_count = 17654032
        error.store()
        return error

    errors = [new_error()]
    resp = app.get('/backoffice/studio/')
    assert 'No errors' not in resp.text
    assert resp.text.count('logged-errors/') == 2
    assert 'logged-errors/%s/' % errors[0].id in resp

    for i in range(5):
        errors.append(new_error())
    resp = app.get('/backoffice/studio/')
    assert resp.text.count('logged-errors/') == 6
    # five recent errors displayed
    assert 'logged-errors/%s/' % errors[0].id not in resp
    assert 'logged-errors/%s/' % errors[1].id in resp
    assert 'logged-errors/%s/' % errors[2].id in resp
    assert 'logged-errors/%s/' % errors[3].id in resp
    assert 'logged-errors/%s/' % errors[4].id in resp
    assert 'logged-errors/%s/' % errors[5].id in resp


def test_studio_home_recent_changes(pub):
    create_superuser(pub)
    user = create_superuser(pub)
    other_user = pub.user_class(name='other')
    other_user.store()

    pub.snapshot_class.wipe()
    BlockDef.wipe()
    CardDef.wipe()
    NamedDataSource.wipe()
    FormDef.wipe()
    MailTemplate.wipe()
    CommentTemplate.wipe()
    Workflow.wipe()
    NamedWsCall.wipe()

    objects = defaultdict(list)
    for i in range(6):
        for klass in [
            BlockDef,
            CardDef,
            NamedDataSource,
            FormDef,
            MailTemplate,
            CommentTemplate,
            Workflow,
            NamedWsCall,
        ]:
            obj = klass()
            obj.name = 'foo %s' % i
            obj.store()
            objects[klass.xml_root_node].append(obj)
    for klass in [
        BlockDef,
        CardDef,
        NamedDataSource,
        FormDef,
        MailTemplate,
        CommentTemplate,
        Workflow,
        NamedWsCall,
    ]:
        assert pub.snapshot_class.count(clause=[Equal('object_type', klass.xml_root_node)]) == 6
        # 2 snapshots for this one, but will be displayed only once
        objects[klass.xml_root_node][-1].name += ' bar'
        objects[klass.xml_root_node][-1].store()
        assert pub.snapshot_class.count(clause=[Equal('object_type', klass.xml_root_node)]) == 7

    app = login(get_app(pub))
    resp = app.get('/backoffice/studio/')
    assert len(resp.pyquery.find('ul.recent-changes li')) == 0

    for snapshot in pub.snapshot_class.select():
        snapshot.user_id = other_user.id
        snapshot.store()
    resp = app.get('/backoffice/studio/')
    assert len(resp.pyquery.find('ul.recent-changes li')) == 0

    for snapshot in pub.snapshot_class.select():
        snapshot.user_id = user.id
        snapshot.store()
    resp = app.get('/backoffice/studio/')
    assert len(resp.pyquery.find('ul.recent-changes li')) == 5

    # too old
    for i in range(5):
        assert 'backoffice/forms/blocks/%s/' % objects[BlockDef.xml_root_node][i].id not in resp
        assert 'backoffice/cards/%s/' % objects[CardDef.xml_root_node][i].id not in resp
        assert (
            'backoffice/settings/data-sources/%s/' % objects[NamedDataSource.xml_root_node][i].id not in resp
        )
        assert 'backoffice/forms/data-sources/%s/' % objects[NamedDataSource.xml_root_node][i].id not in resp
        assert (
            'backoffice/workflows/data-sources/%s/' % objects[NamedDataSource.xml_root_node][i].id not in resp
        )
        assert 'backoffice/forms/%s/' % objects[FormDef.xml_root_node][i].id not in resp
        assert (
            'backoffice/workflows/mail-templates/%s/' % objects[MailTemplate.xml_root_node][i].id not in resp
        )
        assert (
            'backoffice/workflows/comment-templates/%s/' % objects[CommentTemplate.xml_root_node][i].id
            not in resp
        )
        assert 'backoffice/workflows/%s/' % objects[Workflow.xml_root_node][i].id not in resp
        assert 'backoffice/settings/wscalls/%s/' % objects[NamedWsCall.xml_root_node][i].id not in resp

    # too old
    assert 'backoffice/forms/blocks/%s/' % objects[BlockDef.xml_root_node][5].id not in resp
    assert 'backoffice/cards/%s/' % objects[CardDef.xml_root_node][5].id not in resp
    assert 'backoffice/settings/data-sources/%s/' % objects[NamedDataSource.xml_root_node][5].id not in resp
    # only 5 elements
    assert (
        'backoffice/forms/data-sources/%s/' % objects[NamedDataSource.xml_root_node][5].id not in resp
    )  # not this url
    assert (
        'backoffice/workflows/data-sources/%s/' % objects[NamedDataSource.xml_root_node][5].id
        not in resp  # not this url
    )
    assert 'backoffice/forms/%s/' % objects[FormDef.xml_root_node][5].id in resp
    assert 'backoffice/workflows/mail-templates/%s/' % objects[MailTemplate.xml_root_node][5].id in resp
    assert 'backoffice/workflows/comment-templates/%s/' % objects[CommentTemplate.xml_root_node][5].id in resp
    assert 'backoffice/workflows/%s/' % objects[Workflow.xml_root_node][5].id in resp
    assert 'backoffice/settings/wscalls/%s/' % objects[NamedWsCall.xml_root_node][5].id in resp

    pub.cfg['admin-permissions'] = {}
    pub.cfg['admin-permissions'].update({'settings': ['x']})
    pub.write_cfg()

    resp = app.get('/backoffice/studio/')
    # no access to settings
    for i in range(6):
        assert (
            'backoffice/settings/data-sources/%s/' % objects[NamedDataSource.xml_root_node][i].id not in resp
        )
        assert 'backoffice/settings/wscalls/%s/' % objects[NamedWsCall.xml_root_node][i].id not in resp
    # too old
    for i in range(5):
        assert 'backoffice/forms/blocks/%s/' % objects[BlockDef.xml_root_node][i].id not in resp
        assert 'backoffice/cards/%s/' % objects[CardDef.xml_root_node][i].id not in resp
        assert 'backoffice/forms/data-sources/%s/' % objects[NamedDataSource.xml_root_node][i].id not in resp
        assert (
            'backoffice/workflows/data-sources/%s/' % objects[NamedDataSource.xml_root_node][i].id not in resp
        )
        assert 'backoffice/forms/%s/' % objects[FormDef.xml_root_node][i].id not in resp
        assert (
            'backoffice/workflows/mail-templates/%s/' % objects[MailTemplate.xml_root_node][i].id not in resp
        )
        assert (
            'backoffice/workflows/comment-templates/%s/' % objects[CommentTemplate.xml_root_node][i].id
            not in resp
        )
        assert 'backoffice/workflows/%s/' % objects[Workflow.xml_root_node][i].id not in resp
    # too old
    assert 'backoffice/forms/blocks/%s/' % objects[BlockDef.xml_root_node][5].id not in resp
    assert 'backoffice/cards/%s/' % objects[CardDef.xml_root_node][5].id not in resp
    # only 5 elements
    assert 'backoffice/forms/data-sources/%s/' % objects[NamedDataSource.xml_root_node][5].id in resp
    assert (
        'backoffice/workflows/data-sources/%s/' % objects[NamedDataSource.xml_root_node][5].id
        not in resp  # not this url
    )
    assert 'backoffice/forms/%s/' % objects[FormDef.xml_root_node][5].id in resp
    assert 'backoffice/workflows/mail-templates/%s/' % objects[MailTemplate.xml_root_node][5].id in resp
    assert 'backoffice/workflows/comment-templates/%s/' % objects[CommentTemplate.xml_root_node][5].id in resp
    assert 'backoffice/workflows/%s/' % objects[Workflow.xml_root_node][5].id in resp

    pub.cfg['admin-permissions'] = {}
    pub.cfg['admin-permissions'].update({'settings': ['x'], 'forms': ['x']})
    pub.write_cfg()

    resp = app.get('/backoffice/studio/')
    # no access to settings or forms
    for i in range(6):
        assert 'backoffice/forms/blocks/%s/' % objects[BlockDef.xml_root_node][i].id not in resp
        assert (
            'backoffice/settings/data-sources/%s/' % objects[NamedDataSource.xml_root_node][i].id not in resp
        )
        assert 'backoffice/forms/data-sources/%s/' % objects[NamedDataSource.xml_root_node][i].id not in resp
        assert 'backoffice/forms/%s/' % objects[FormDef.xml_root_node][i].id not in resp
        assert 'backoffice/settings/wscalls/%s/' % objects[NamedWsCall.xml_root_node][i].id not in resp
    # too old
    for i in range(5):
        assert 'backoffice/cards/%s/' % objects[CardDef.xml_root_node][i].id not in resp
        assert (
            'backoffice/workflows/data-sources/%s/' % objects[NamedDataSource.xml_root_node][i].id not in resp
        )
        assert (
            'backoffice/workflows/mail-templates/%s/' % objects[MailTemplate.xml_root_node][i].id not in resp
        )
        assert (
            'backoffice/workflows/comment-templates/%s/' % objects[CommentTemplate.xml_root_node][i].id
            not in resp
        )
        assert 'backoffice/workflows/%s/' % objects[Workflow.xml_root_node][i].id not in resp
    # only 5 elements
    assert 'backoffice/cards/%s/' % objects[CardDef.xml_root_node][5].id in resp
    assert 'backoffice/workflows/data-sources/%s/' % objects[NamedDataSource.xml_root_node][5].id in resp
    assert 'backoffice/workflows/mail-templates/%s/' % objects[MailTemplate.xml_root_node][5].id in resp
    assert 'backoffice/workflows/comment-templates/%s/' % objects[CommentTemplate.xml_root_node][5].id in resp
    assert 'backoffice/workflows/%s/' % objects[Workflow.xml_root_node][5].id in resp

    pub.cfg['admin-permissions'] = {}
    pub.cfg['admin-permissions'].update({'settings': ['x'], 'forms': ['x'], 'workflows': ['x']})
    pub.write_cfg()

    resp = app.get('/backoffice/studio/')
    # no access to settings, forms or workflows
    for i in range(6):
        assert 'backoffice/forms/blocks/%s/' % objects[BlockDef.xml_root_node][i].id not in resp
        assert (
            'backoffice/settings/data-sources/%s/' % objects[NamedDataSource.xml_root_node][i].id not in resp
        )
        assert 'backoffice/forms/data-sources/%s/' % objects[NamedDataSource.xml_root_node][i].id not in resp
        assert 'backoffice/forms/%s/' % objects[FormDef.xml_root_node][i].id not in resp
        assert 'backoffice/settings/wscalls/%s/' % objects[NamedWsCall.xml_root_node][i].id not in resp
        assert (
            'backoffice/workflows/data-sources/%s/' % objects[NamedDataSource.xml_root_node][i].id not in resp
        )
        assert (
            'backoffice/workflows/mail-templates/%s/' % objects[MailTemplate.xml_root_node][i].id not in resp
        )
        assert (
            'backoffice/workflows/comment-templates/%s/' % objects[CommentTemplate.xml_root_node][i].id
            not in resp
        )
        assert 'backoffice/workflows/%s/' % objects[Workflow.xml_root_node][i].id not in resp
    # too old
    assert 'backoffice/cards/%s/' % objects[CardDef.xml_root_node][0].id not in resp
    # only 5 elements
    for i in range(1, 6):
        assert 'backoffice/cards/%s/' % objects[CardDef.xml_root_node][i].id in resp

    objects[CardDef.xml_root_node][5].remove_self()
    resp = app.get('/backoffice/studio/')
    # too old
    assert 'backoffice/cards/%s/' % objects[CardDef.xml_root_node][0].id not in resp
    # only 4 elements, one was deleted
    for i in range(1, 5):
        assert 'backoffice/cards/%s/' % objects[CardDef.xml_root_node][i].id in resp
        # deleted
    assert 'backoffice/cards/%s/' % objects[CardDef.xml_root_node][5].id not in resp

    # all changes page: admin user can see all changes (depending on permissions)
    resp = resp.click(href='all-changes/')
    assert '(1-6/6)' in resp
    # he can also see changes from other users
    for snapshot in pub.snapshot_class.select():
        snapshot.user_id = other_user.id
        snapshot.store()

    pub.cfg['admin-permissions'] = {}
    pub.write_cfg()
    resp = app.get('/backoffice/studio/all-changes/')
    assert '(1-20/48)' in resp
    resp = resp.click('<!--Next Page-->')
    assert '21-40/48' in resp.text
    resp = resp.click('<!--Next Page-->')
    assert '41-48/48' in resp.text

    user.is_admin = False
    user.store()
    app.get('/backoffice/studio/all-changes/', status=403)


def test_studio_home_recent_changes_deleted_objects(pub):
    create_superuser(pub)
    user = create_superuser(pub)

    pub.snapshot_class.wipe()
    BlockDef.wipe()
    CardDef.wipe()
    NamedDataSource.wipe()
    FormDef.wipe()
    MailTemplate.wipe()
    CommentTemplate.wipe()
    Workflow.wipe()
    NamedWsCall.wipe()

    formdef = FormDef()
    formdef.name = 'test'
    formdef.store()

    formdef.name = 'test 1, second save'
    formdef.store()

    formdef2 = FormDef()
    formdef2.name = 'test2'
    formdef2.store()

    for snapshot in pub.snapshot_class.select():
        snapshot.user_id = user.id
        snapshot.store()

    app = login(get_app(pub))
    resp = app.get('/backoffice/studio/')
    assert resp.pyquery('.recent-changes li').length == 2

    formdef2.remove_self()
    resp = app.get('/backoffice/studio/')
    assert resp.pyquery('.recent-changes li').length == 1
    resp = resp.click('See all changes')
    assert resp.pyquery('.single-links li').length == 2
    assert resp.pyquery('.single-links li a[href]').length == 1
    assert resp.text.count('Recently deleted object') == 1

    pub.snapshot_class.clean()
    resp = app.get('/backoffice/studio/')
    assert resp.pyquery('.recent-changes li').length == 1
    resp = resp.click('See all changes')
    assert resp.pyquery('.single-links li').length == 1


def test_studio_workflows(pub):
    create_superuser(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/workflows/')
    resp = resp.click(r'Default \(cards\)')
    assert 'status/recorded/' in resp.text
    assert 'status/deleted/' in resp.text
    assert 'This is the default workflow,' in resp.text


def test_studio_ancient_forms(pub, freezer):
    create_superuser(pub)
    app = login(get_app(pub))
    resp = app.get('/backoffice/studio/')
    assert 'ancient-forms' not in resp.text

    formdef = FormDef()
    formdef.name = 'test'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.status = 'draft'
    formdata.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.anonymise()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    resp = app.get('/backoffice/studio/')
    assert 'ancient-forms' not in resp.text
    formdata.evolution[-1].time = make_aware(datetime.datetime(2023, 1, 1, 0, 0))
    formdata.store()

    freezer.move_to(datetime.date(2024, 10, 1))
    resp = app.get('/backoffice/studio/')
    assert (
        resp.pyquery('.ancient-forms-link').text() == '1 ancient form.\nAnonymisation should be configured.'
    )
    resp = resp.click(href='ancient-forms')
    assert resp.pyquery('table a').length == 1
    assert resp.pyquery('.ancient-forms-table--count').text() == '1'

    formdef.old_but_non_anonymised_warning = 800
    formdef.store()
    resp = app.get('/backoffice/studio/')
    assert 'ancient-forms' not in resp.text

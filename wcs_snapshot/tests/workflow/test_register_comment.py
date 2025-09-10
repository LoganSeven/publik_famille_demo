import base64
import io
import os
import shutil

import pytest
from quixote import cleanup, get_publisher

from wcs import sessions
from wcs.fields import FileField, StringField
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.upload_storage import PicklableUpload
from wcs.wf.backoffice_fields import SetBackofficeFieldsWorkflowStatusItem
from wcs.wf.register_comment import JournalEvolutionPart, RegisterCommenterWorkflowStatusItem
from wcs.workflows import (
    AttachmentEvolutionPart,
    ContentSnapshotPart,
    Workflow,
    WorkflowBackofficeFieldsFormDef,
)

from ..form_pages.test_all import create_user
from ..utilities import MockSubstitutionVariables, clean_temporary_pub, create_temporary_pub, get_app, login


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
    req = HTTPRequest(None, {'SERVER_NAME': 'example.net', 'SCRIPT_NAME': ''})
    req.response.filter = {}
    req._user = None
    pub._set_request(req)
    pub.set_app_dir(req)
    req.session = sessions.BasicSession(id=1)
    pub.set_config(req)
    return pub


def test_register_comment_migrate(pub):
    Workflow.wipe()

    workflow = Workflow(name='register message')
    st1 = workflow.add_status('Status1', 'st1')
    add_message = st1.add_action('register-comment')
    add_message.level = None
    add_message.comment = '<div class="errornotice">message</div>'
    workflow.store()

    workflow.migrate()
    assert workflow.possible_status[0].items[0].level == 'error'
    assert workflow.possible_status[0].items[0].comment == 'message'

    # check the migration is skipped if there's an extra class
    add_message.level = None
    add_message.comment = '<div class="errornotice blah">message</div>'
    workflow.store()
    workflow.migrate()
    assert not workflow.possible_status[0].items[0].level
    assert workflow.possible_status[0].items[0].comment == '<div class="errornotice blah">message</div>'


def test_register_comment_level(pub):
    FormDef.wipe()
    Workflow.wipe()

    workflow = Workflow(name='register message')
    st1 = workflow.add_status('Status1', 'st1')
    add_message = st1.add_action('register-comment')
    add_message.level = 'error'
    add_message.comment = 'hello\n\nworld'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.workflow_id = workflow.id
    formdef.store()

    resp = get_app(pub).get('/foobar/')
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit').follow()  # -> done
    assert [x.text.strip() for x in resp.pyquery('#evolutions .errornotice p')] == ['hello', 'world']

    add_message.comment = '<p>hello\n\nworld</p>'
    workflow.store()

    resp = get_app(pub).get('/foobar/')
    resp = resp.form.submit('submit')  # -> validation
    resp = resp.form.submit('submit').follow()  # -> done
    assert [x.text.strip() for x in resp.pyquery('#evolutions .errornotice p')] == ['hello\n\nworld']


def test_register_comment_legacy_value(pub):
    FormDef.wipe()
    user = create_user(pub)

    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.user_id = user.id
    formdata.store()
    formdata.perform_workflow()
    legacy_part = JournalEvolutionPart(formdata, 'x', None, None)
    legacy_part.content = 'hello\n\nworld'
    formdata.evolution[-1].add_part(legacy_part)
    formdata.store()

    app = login(get_app(pub), username='foo', password='foo')
    resp = app.get(formdata.get_url())
    assert [x.text.strip() for x in resp.pyquery('#evolutions .msg p')] == ['hello', 'world']


def test_register_comment(pub):
    pub.substitutions.feed(MockSubstitutionVariables())

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    item = RegisterCommenterWorkflowStatusItem()
    item.perform(formdata)
    formdata.evolution[-1]._display_parts = None
    assert formdata.evolution[-1].display_parts() == []

    item.comment = 'Hello world'
    item.perform(formdata)
    formdata.evolution[-1]._display_parts = None
    assert formdata.evolution[-1].display_parts()[-1] == '<p>Hello world</p>'

    item.comment = '<div>Hello world</div>'
    item.perform(formdata)
    formdata.evolution[-1]._display_parts = None
    assert formdata.evolution[-1].display_parts()[-1] == '<div>Hello world</div>'

    formdata.evolution[-1].parts = []
    formdata.store()
    item.comment = '{{ test }}'
    item.perform(formdata)
    formdata.evolution[-1]._display_parts = None
    assert formdata.evolution[-1].display_parts() == []

    item.comment = '[test]'
    item.perform(formdata)
    formdata.evolution[-1]._display_parts = None
    assert formdata.evolution[-1].display_parts()[-1] == '<p>[test]</p>'

    item.comment = '{{ bar }}'
    item.perform(formdata)
    formdata.evolution[-1]._display_parts = None
    assert formdata.evolution[-1].display_parts()[-1] == '<div>Foobar</div>'

    item.comment = '[bar]'
    item.perform(formdata)
    formdata.evolution[-1]._display_parts = None
    assert formdata.evolution[-1].display_parts()[-1] == '<p>Foobar</p>'

    item.comment = '<p>{{ foo }}</p>'
    item.perform(formdata)
    formdata.evolution[-1]._display_parts = None
    assert formdata.evolution[-1].display_parts()[-1] == '<p>1 &lt; 3</p>'

    item.comment = '<p>{{ foo|safe }}</p>'
    item.perform(formdata)
    formdata.evolution[-1]._display_parts = None
    assert formdata.evolution[-1].display_parts()[-1] == '<p>1 < 3</p>'

    item.comment = '{{ foo }}'
    item.perform(formdata)
    formdata.evolution[-1]._display_parts = None
    assert formdata.evolution[-1].display_parts()[-1] == '<div>1 &lt; 3</div>'

    item.comment = '{{ foo|safe }}'
    item.perform(formdata)
    formdata.evolution[-1]._display_parts = None
    assert formdata.evolution[-1].display_parts()[-1] == '<div>1 < 3</div>'

    item.comment = '[foo]'
    item.perform(formdata)
    formdata.evolution[-1]._display_parts = None
    assert formdata.evolution[-1].display_parts()[-1] == '<p>1 &lt; 3</p>'

    item.comment = '<div>{{ foo }}</div>'
    item.perform(formdata)
    formdata.evolution[-1]._display_parts = None
    assert formdata.evolution[-1].display_parts()[-1] == '<div>1 &lt; 3</div>'

    item.comment = '<div>[foo]</div>'
    item.perform(formdata)
    formdata.evolution[-1]._display_parts = None
    assert formdata.evolution[-1].display_parts()[-1] == '<div>1 &lt; 3</div>'


def test_register_comment_django_escaping(pub, emails):
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.data = {'1': '<p>hello</p>'}
    formdata.store()
    pub.substitutions.feed(formdata)

    item = RegisterCommenterWorkflowStatusItem()
    item.comment = '<div>{{form_var_foo}}</div>'
    item.perform(formdata)
    formdata.evolution[-1]._display_parts = None
    assert formdata.evolution[-1].display_parts()[-1] == '<div>&lt;p&gt;hello&lt;/p&gt;</div>'

    # |safe
    item = RegisterCommenterWorkflowStatusItem()
    item.comment = '<div>{{form_var_foo|safe}}</div>'
    item.perform(formdata)
    formdata.evolution[-1]._display_parts = None
    assert formdata.evolution[-1].display_parts()[-1] == '<div><p>hello</p></div>'


def test_register_comment_attachment(pub):
    pub.substitutions.feed(MockSubstitutionVariables())

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    item = RegisterCommenterWorkflowStatusItem()
    item.perform(formdata)
    formdata.evolution[-1]._display_parts = None
    assert formdata.evolution[-1].display_parts() == []

    if os.path.exists(os.path.join(get_publisher().app_dir, 'attachments')):
        shutil.rmtree(os.path.join(get_publisher().app_dir, 'attachments'))

    formdata.evolution[-1].parts = [
        AttachmentEvolutionPart('hello.txt', fp=io.BytesIO(b'hello world'), varname='testfile')
    ]
    formdata.store()
    assert len(os.listdir(os.path.join(get_publisher().app_dir, 'attachments'))) == 1
    for subdir in os.listdir(os.path.join(get_publisher().app_dir, 'attachments')):
        assert len(subdir) == 4
        assert len(os.listdir(os.path.join(get_publisher().app_dir, 'attachments', subdir))) == 1

    item.comment = '{{ attachments.testfile.url }}'

    pub.substitutions.feed(formdata)
    item.perform(formdata)
    url1 = formdata.evolution[-1].parts[-1].content
    assert LoggedError.count() == 1
    error = LoggedError.select()[0]
    assert error.kind == 'deprecated_usage'
    assert error.occurences_count == 1

    item.comment = '{{ form_attachments.testfile.url }}'  # check dotted name
    item.perform(formdata)
    url2 = formdata.evolution[-1].parts[-1].content
    assert LoggedError.count() == 1  # no new error

    item.comment = '{{ form_attachments_testfile_url }}'  # check underscored name
    item.perform(formdata)
    url3 = formdata.evolution[-1].parts[-1].content
    assert LoggedError.count() == 1  # no new error

    assert len(os.listdir(os.path.join(get_publisher().app_dir, 'attachments'))) == 1
    for subdir in os.listdir(os.path.join(get_publisher().app_dir, 'attachments')):
        assert len(subdir) == 4
        assert len(os.listdir(os.path.join(get_publisher().app_dir, 'attachments', subdir))) == 1
    assert url1 == url2 == url3

    # test with a condition
    item.comment = '{% if form_attachments.testfile %}file is there{% endif %}'
    item.perform(formdata)
    assert formdata.evolution[-1].parts[-1].content == '<div>file is there</div>'
    item.comment = '{% if form_attachments.nope %}file is there{% endif %}'
    item.perform(formdata)
    assert formdata.evolution[-1].parts[-1].content == ''

    # test with an action condition
    item.condition = {'type': 'django', 'value': 'form_attachments.testfile'}
    assert item.check_condition(formdata) is True

    item.condition = {'type': 'django', 'value': 'form_attachments.missing'}
    assert item.check_condition(formdata) is False

    pub.substitutions.feed(formdata)
    item.comment = '[attachments.testfile.url]'
    item.perform(formdata)
    url3 = formdata.evolution[-1].parts[-1].content
    assert LoggedError.count() == 1
    error = LoggedError.select()[0]
    assert error.kind == 'deprecated_usage'
    assert error.occurences_count == 2
    pub.substitutions.feed(formdata)
    item.comment = '[form_attachments.testfile.url]'
    item.perform(formdata)
    url4 = formdata.evolution[-1].parts[-1].content
    assert url3 == url4
    assert LoggedError.count() == 1
    error = LoggedError.select()[0]
    assert error.kind == 'deprecated_usage'
    assert error.occurences_count == 2


def test_register_comment_with_attachment_file(pub):
    wf = Workflow(name='comment with attachments')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        FileField(id='bo1', label='bo field 1', varname='backoffice_file1'),
    ]
    st1 = wf.add_status('Status1')
    wf.store()

    upload = PicklableUpload('test.jpeg', 'image/jpeg')
    with open(os.path.join(os.path.dirname(__file__), '..', 'image-with-gps-data.jpeg'), 'rb') as fd:
        upload.receive([fd.read()])

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        FileField(id='1', label='File', varname='frontoffice_file'),
    ]
    formdef.workflow_id = wf.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': upload}
    formdata.just_created()
    formdata.store()

    pub.substitutions.feed(formdata)

    setbo = SetBackofficeFieldsWorkflowStatusItem()
    setbo.parent = st1
    setbo.fields = [{'field_id': 'bo1', 'value': '{{ form_var_frontoffice_file_raw }}'}]
    setbo.perform(formdata)

    if os.path.exists(os.path.join(get_publisher().app_dir, 'attachments')):
        shutil.rmtree(os.path.join(get_publisher().app_dir, 'attachments'))

    comment_text = 'File is attached to the form history'

    item = RegisterCommenterWorkflowStatusItem()
    item.attachments = ['{{ form_var_backoffice_file1_raw }}']
    item.comment = comment_text
    item.perform(formdata)

    assert len(os.listdir(os.path.join(get_publisher().app_dir, 'attachments'))) == 1
    for subdir in os.listdir(os.path.join(get_publisher().app_dir, 'attachments')):
        assert len(subdir) == 4
        assert len(os.listdir(os.path.join(get_publisher().app_dir, 'attachments', subdir))) == 1

    assert len(formdata.evolution[-1].parts) == 4
    assert isinstance(formdata.evolution[-1].parts[0], ContentSnapshotPart)
    assert isinstance(formdata.evolution[-1].parts[1], ContentSnapshotPart)

    assert isinstance(formdata.evolution[-1].parts[2], AttachmentEvolutionPart)
    assert formdata.evolution[-1].parts[2].orig_filename == upload.orig_filename

    assert isinstance(formdata.evolution[-1].parts[3], JournalEvolutionPart)
    assert len(formdata.evolution[-1].parts[3].content) > 0
    comment_view = str(formdata.evolution[-1].parts[3].view())
    assert comment_view == '<p>%s</p>' % comment_text

    if os.path.exists(os.path.join(get_publisher().app_dir, 'attachments')):
        shutil.rmtree(os.path.join(get_publisher().app_dir, 'attachments'))

    formdata.evolution[-1].parts = []
    formdata.store()

    ws_response_varname = 'ws_response_afile'
    wf_data = {
        '%s_filename' % ws_response_varname: 'hello.txt',
        '%s_content_type' % ws_response_varname: 'text/plain',
        '%s_b64_content' % ws_response_varname: base64.encodebytes(b'hello world'),
    }
    formdata.update_workflow_data(wf_data)
    formdata.store()
    assert hasattr(formdata, 'workflow_data')
    assert isinstance(formdata.workflow_data, dict)


def test_register_comment_to(pub):
    workflow = Workflow(name='register comment to')
    st1 = workflow.add_status('Status1', 'st1')
    workflow.store()

    role = pub.role_class(name='foorole')
    role.store()
    role2 = pub.role_class(name='no-one-role')
    role2.store()
    user = pub.user_class(name='baruser')
    user.roles = []
    user.store()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    assert formdata.status == 'wf-st1'
    formdata.store()

    register_commenter = st1.add_action('register-comment')

    assert register_commenter.get_line_details() == 'to everybody'

    def display_parts():
        formdata.evolution[-1]._display_parts = None  # invalidate cache
        return [str(x) for x in formdata.evolution[-1].display_parts()]

    register_commenter.comment = 'all'
    register_commenter.to = None
    register_commenter.perform(formdata)
    assert len(formdata.evolution[-1].parts) == 2
    assert display_parts() == ['<p>all</p>']

    register_commenter.comment = 'to-role'
    register_commenter.to = [role.id]
    assert register_commenter.get_line_details() == 'to foorole'
    register_commenter.perform(formdata)
    assert len(formdata.evolution[-1].parts) == 3
    assert len(display_parts()) == 1
    pub._request._user = user
    assert display_parts() == ['<p>all</p>']
    user.roles = [role.id]
    assert display_parts() == ['<p>all</p>', '<p>to-role</p>']

    user.roles = []
    register_commenter.comment = 'to-submitter'
    register_commenter.to = ['_submitter']
    assert register_commenter.get_line_details() == 'to User'
    register_commenter.perform(formdata)
    assert len(formdata.evolution[-1].parts) == 4
    assert display_parts() == ['<p>all</p>']
    formdata.user_id = user.id
    assert display_parts() == ['<p>all</p>', '<p>to-submitter</p>']

    register_commenter.comment = 'to-role-or-submitter'
    register_commenter.to = [role.id, '_submitter']
    assert register_commenter.get_line_details() == 'to foorole, User'
    register_commenter.perform(formdata)
    assert len(formdata.evolution[-1].parts) == 5
    assert display_parts() == ['<p>all</p>', '<p>to-submitter</p>', '<p>to-role-or-submitter</p>']
    formdata.user_id = None
    assert display_parts() == ['<p>all</p>']
    user.roles = [role.id]
    assert display_parts() == ['<p>all</p>', '<p>to-role</p>', '<p>to-role-or-submitter</p>']
    formdata.user_id = user.id
    assert display_parts() == [
        '<p>all</p>',
        '<p>to-role</p>',
        '<p>to-submitter</p>',
        '<p>to-role-or-submitter</p>',
    ]

    register_commenter.comment = 'd1'
    register_commenter.to = [role2.id]
    assert register_commenter.get_line_details() == 'to no-one-role'
    register_commenter.perform(formdata)
    assert len(formdata.evolution[-1].parts) == 6
    assert display_parts() == [
        '<p>all</p>',
        '<p>to-role</p>',
        '<p>to-submitter</p>',
        '<p>to-role-or-submitter</p>',
    ]
    register_commenter2 = st1.add_action('register-comment')
    register_commenter2.comment = 'd2'
    register_commenter2.to = [role.id, '_submitter']
    user.roles = [role.id, role2.id]
    register_commenter2.perform(formdata)
    assert len(formdata.evolution[-1].parts) == 7
    assert '<p>d1</p>' in [str(x) for x in display_parts()]
    assert '<p>d2</p>' in [str(x) for x in display_parts()]


def test_register_comment_to_with_attachment(pub):
    workflow = Workflow(name='register comment to with attachment')
    st1 = workflow.add_status('Status1', 'st1')

    role = pub.role_class(name='foorole')
    role.store()
    role2 = pub.role_class(name='no-one-role')
    role2.store()
    user = pub.user_class(name='baruser')
    user.roles = []
    user.store()

    upload1 = PicklableUpload('all.txt', 'text/plain')
    upload1.receive([b'barfoo'])
    upload2 = PicklableUpload('to-role.txt', 'text/plain')
    upload2.receive([b'barfoo'])
    upload3 = PicklableUpload('to-submitter.txt', 'text/plain')
    upload3.receive([b'barfoo'])
    upload4 = PicklableUpload('to-role-or-submitter.txt', 'text/plain')
    upload4.receive([b'barfoo'])

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.url_name = 'foobar'
    formdef.fields = [
        FileField(id='1', label='File1', varname='file1'),
        FileField(id='2', label='File2', varname='file2'),
        FileField(id='3', label='File3', varname='file3'),
        FileField(id='4', label='File4', varname='file4'),
    ]
    formdef.workflow = workflow
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': upload1, '2': upload2, '3': upload3, '4': upload4}
    formdata.just_created()
    assert formdata.status == 'wf-st1'
    pub.substitutions.feed(formdata)

    register_commenter = st1.add_action('register-comment')

    def display_parts():
        formdata.evolution[-1]._display_parts = None  # invalidate cache
        return [str(x) for x in formdata.evolution[-1].display_parts()]

    register_commenter.comment = 'all'
    register_commenter.attachments = ['{{ form_var_file1_raw }}']
    register_commenter.to = None
    register_commenter.perform(formdata)

    register_commenter.comment = 'to-role'
    register_commenter.attachments = ['{{ form_var_file2_raw }}']
    register_commenter.to = [role.id]
    register_commenter.perform(formdata)

    register_commenter.comment = 'to-submitter'
    register_commenter.attachments = ['{{ form_var_file3_raw }}']
    register_commenter.to = ['_submitter']
    register_commenter.perform(formdata)

    register_commenter.comment = 'to-role-or-submitter'
    register_commenter.attachments = ['{{ form_var_file4_raw }}']
    register_commenter.to = [role.id, '_submitter']
    register_commenter.perform(formdata)

    assert len(formdata.evolution[-1].parts) == 9

    assert user.roles == []
    assert len(display_parts()) == 2
    assert 'all.txt' in display_parts()[0]
    assert display_parts()[1] == '<p>all</p>'

    pub._request._user = user
    user.roles = [role.id]
    assert len(display_parts()) == 6
    assert 'all.txt' in display_parts()[0]
    assert 'to-role.txt' in display_parts()[2]
    assert 'to-role-or-submitter.txt' in display_parts()[4]

    user.roles = []
    formdata.user_id = user.id
    assert len(display_parts()) == 6
    assert 'all.txt' in display_parts()[0]
    assert 'to-submitter.txt' in display_parts()[2]
    assert 'to-role-or-submitter.txt' in display_parts()[4]

    user.roles = [role.id]
    assert len(display_parts()) == 8
    assert 'all.txt' in display_parts()[0]
    assert 'to-role.txt' in display_parts()[2]
    assert 'to-submitter.txt' in display_parts()[4]
    assert 'to-role-or-submitter.txt' in display_parts()[6]


def test_register_comment_fts(pub):
    pub.substitutions.feed(MockSubstitutionVariables())

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    item = RegisterCommenterWorkflowStatusItem()
    item.comment = 'Hello\x00\nworld'
    item.perform(formdata)
    assert formdata.evolution[-1].parts[-1].content == '<p>Hello\x00\nworld</p>'  # kept
    assert formdata.evolution[-1].parts[-1].render_for_fts() == 'Hello  world'  # not kept

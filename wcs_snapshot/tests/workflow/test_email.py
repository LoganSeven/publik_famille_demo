import os
import re
from unittest import mock

import pytest
from django.core import mail
from quixote import cleanup

from wcs import sessions
from wcs.fields import FileField, StringField
from wcs.formdef import FormDef
from wcs.logged_errors import LoggedError
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.substitution import CompatibilityNamesDict
from wcs.qommon.upload_storage import PicklableUpload
from wcs.testdef import TestDef
from wcs.wf.backoffice_fields import SetBackofficeFieldsWorkflowStatusItem
from wcs.wf.sendmail import EmailEvolutionPart, SendmailWorkflowStatusItem
from wcs.workflows import Workflow, WorkflowBackofficeFieldsFormDef

from ..admin_pages.test_all import create_superuser
from ..utilities import MockSubstitutionVariables, clean_temporary_pub, create_temporary_pub, get_app, login


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
    req = HTTPRequest(None, {'SERVER_NAME': 'example.net', 'SCRIPT_NAME': ''})
    req.response.filter = {}
    req._user = None
    pub._set_request(req)
    pub.set_app_dir(req)
    req.session = sessions.BasicSession(id=1)
    pub.set_config(req)
    TestDef.wipe()
    return pub


def test_email(pub, emails):
    pub.substitutions.feed(MockSubstitutionVariables())

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    user = pub.user_class(name='foo')
    user.email = 'zorg@localhost'
    user.store()

    pub.role_class.wipe()
    role1 = pub.role_class(name='foo')
    role1.emails = ['foo@localhost']
    role1.store()

    role2 = pub.role_class(name='bar')
    role2.emails = ['bar@localhost', 'baz@localhost']
    role2.store()

    # send using an uncompleted element
    item = SendmailWorkflowStatusItem()
    item.perform(formdata)  # nothing
    pub.process_after_jobs()
    assert emails.count() == 0
    assert not any(isinstance(part, EmailEvolutionPart) for part in formdata.evolution[-1].parts)

    item.to = [role1.id]
    item.perform(formdata)  # no subject nor body
    pub.process_after_jobs()
    assert emails.count() == 0
    assert not any(isinstance(part, EmailEvolutionPart) for part in formdata.evolution[-1].parts)

    item.subject = 'foobar'
    item.perform(formdata)  # no body
    pub.process_after_jobs()
    assert emails.count() == 0
    assert not any(isinstance(part, EmailEvolutionPart) for part in formdata.evolution[-1].parts)

    hostname = 'example.net'
    expt_id = fr'<wcs-formdata-{formdef.id}-{formdata.id}\.[0-9]{{8}}\.[0-9]{{6}}[^@]+@{hostname}>'

    # send for real
    item.body = 'baz'
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.get('foobar')
    assert emails.get('foobar')['email_rcpt'] == ['foo@localhost']
    assert 'baz' in emails.get('foobar')['payload']
    headers = emails.get('foobar').email.extra_headers
    assert 'Message-ID' in headers
    assert 'In-Reply-To' not in headers
    assert 'References' not in headers
    assert re.match(expt_id, headers['Message-ID'])
    first_message_id = headers['Message-ID']
    assert any(isinstance(part, EmailEvolutionPart) for part in formdata.evolution[-1].parts)

    # template for subject or body (Django)
    emails.empty()
    item.subject = '{{ bar }}'
    item.body = '{{ foo }}'
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.get('Foobar')
    assert '1 < 3' in emails.get('Foobar')['payload']

    headers = emails.get('Foobar').email.extra_headers
    assert 'Message-ID' in headers
    assert 'In-Reply-To' in headers
    assert 'References' in headers
    assert first_message_id == headers['In-Reply-To']
    assert first_message_id == headers['References']
    assert re.match(expt_id, headers['Message-ID'])

    # template for subject or body (ezt)
    emails.empty()
    item.subject = '[bar]'
    item.body = '[foo]'
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.get('Foobar')
    assert '1 < 3' in emails.get('Foobar')['payload']
    headers = emails.get('Foobar').email.extra_headers
    assert 'Message-ID' in headers
    assert 'In-Reply-To' in headers
    assert 'References' in headers
    assert first_message_id == headers['In-Reply-To']
    assert first_message_id == headers['References']
    assert re.match(expt_id, headers['Message-ID'])

    # two recipients
    emails.empty()
    item.subject = 'foobar'
    item.to = [role1.id, role2.id]
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 2  # reply to role1 and new thread to role2

    # submitter as recipient, no known email address
    emails.empty()
    item.to = ['_submitter']
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 0

    # submitter as recipient, known email address
    emails.empty()
    formdata.user_id = user.id
    formdata.store()
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.get('foobar')['email_rcpt'] == ['zorg@localhost']

    # string as recipient
    emails.empty()
    item.to = 'xyz@localhost'
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.get('foobar')['email_rcpt'] == ['xyz@localhost']

    # string as recipient (but correctly set in a list)
    emails.empty()
    item.to = ['xyz@localhost']
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.get('foobar')['email_rcpt'] == ['xyz@localhost']

    # multiple recipients in a static string
    emails.empty()
    item.to = ['foo@localhost, bar@localhost']
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 2
    assert set().union(*[m.recipients() for m in mail.outbox]) == {'foo@localhost', 'bar@localhost'}

    # custom from email
    emails.empty()
    item.to = [role1.id]
    item.custom_from = 'foobar@localhost'
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.get('foobar')['from'] == 'foobar@localhost'

    # custom sender name defined from site-options variable
    pub.load_site_options()
    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'email_sender_name', 'SENDER NAME')
    emails.empty()
    item.to = [role1.id]
    item.custom_from = 'foobar@localhost'
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.get('foobar')['msg']['From'] == 'SENDER NAME <foobar@localhost>'


def test_email_threading(pub, emails):
    pub.substitutions.feed(MockSubstitutionVariables())

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    pub.role_class.wipe()
    role1 = pub.role_class(name='a1')
    role1.emails = ['a1@localhost']
    role1.store()

    role2 = pub.role_class(name='a2')
    role2.emails = ['a2@localhost']
    role2.store()

    role3 = pub.role_class(name='a2')
    role3.emails = ['a3@localhost']
    role3.store()

    # New thread to a1 & a2
    emails.empty()
    item = SendmailWorkflowStatusItem()
    item.body = 'foobar'
    item.subject = 'foobar'
    item.to = [role1.id, role2.id]
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert set(emails.get('foobar')['bcc']) == {'a1@localhost', 'a2@localhost'}
    assert emails.get('foobar')['to'] == 'Undisclosed recipients:;'
    headers = emails.get('foobar').email.extra_headers
    assert 'Message-ID' in headers
    assert 'In-Reply-To' not in headers
    assert 'References' not in headers
    message_id = headers['Message-ID']

    # In-Reply-To a1
    emails.empty()
    item.to = [role1.id]
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.get('foobar')['to'] == 'a1@localhost'
    headers = emails.get('foobar').email.extra_headers
    assert 'Message-ID' in headers
    assert 'In-Reply-To' in headers
    assert 'References' in headers
    assert headers['In-Reply-To'] == headers['References']
    assert headers['In-Reply-To'] == message_id

    # New thread to a3 & In-Reply-To a1
    emails.empty()
    item.to = [role1.id, role3.id]
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 2
    assert mail.outbox[0].extra_headers['Message-ID'] != mail.outbox[1].extra_headers['Message-ID']

    reply_ids = {
        email.extra_headers.get('In-Reply-To', None): set(email.recipients()) for email in mail.outbox
    }
    expt_ids = {message_id: {'a1@localhost'}, None: {'a3@localhost'}}
    assert reply_ids == expt_ids

    for email in mail.outbox:
        if 'a3@localhost' in email.recipients():
            message_id3 = email.extra_headers['Message-ID']

    # In-Reply-To a1 & a2, In-Reply-To a3
    emails.empty()
    item.to = [role1.id, role2.id, role3.id]
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 2
    assert mail.outbox[0].extra_headers['Message-ID'] != mail.outbox[1].extra_headers['Message-ID']

    reply_ids = {
        email.extra_headers.get('In-Reply-To', None): set(email.recipients()) for email in mail.outbox
    }
    expt_ids = {message_id: {'a1@localhost', 'a2@localhost'}, message_id3: {'a3@localhost'}}
    assert reply_ids == expt_ids

    # New thread to a4, In-Reply-To a1 & a2, In-Reply-To a3
    emails.empty()
    role1.emails = ['a1@localhost', 'a4@localhost']
    role1.store()
    item.to = [role1.id, role2.id, role3.id]
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 3
    assert mail.outbox[0].extra_headers['Message-ID'] != mail.outbox[1].extra_headers['Message-ID']
    assert mail.outbox[0].extra_headers['Message-ID'] != mail.outbox[2].extra_headers['Message-ID']
    assert mail.outbox[1].extra_headers['Message-ID'] != mail.outbox[2].extra_headers['Message-ID']

    reply_ids = {
        email.extra_headers.get('In-Reply-To', None): set(email.recipients()) for email in mail.outbox
    }
    expt_ids = {
        message_id: {'a1@localhost', 'a2@localhost'},
        message_id3: {'a3@localhost'},
        None: {'a4@localhost'},
    }
    assert reply_ids == expt_ids


def test_email_threading_old_evolutionpart(pub, emails):
    pub.substitutions.feed(MockSubstitutionVariables())

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    pub.role_class.wipe()
    role1 = pub.role_class(name='a1')
    role1.emails = ['a1@localhost']
    role1.store()

    role2 = pub.role_class(name='a2')
    role2.emails = ['a2@localhost']
    role2.store()

    # New thread to a1 & a2
    emails.empty()
    item = SendmailWorkflowStatusItem()
    item.body = 'foobar'
    item.subject = 'foobar'
    item.to = [role1.id, role2.id]
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert set(emails.get('foobar')['bcc']) == {'a1@localhost', 'a2@localhost'}
    assert emails.get('foobar')['to'] == 'Undisclosed recipients:;'
    headers = emails.get('foobar').email.extra_headers
    assert 'Message-ID' in headers
    assert 'In-Reply-To' not in headers
    assert 'References' not in headers

    for part in formdata.iter_evolution_parts(klass=EmailEvolutionPart):
        del part.messages_id

    # New thread to a1 & a2 : old evolution part do not stores messages_id
    emails.empty()
    item = SendmailWorkflowStatusItem()
    item.body = 'foobar2'
    item.subject = 'foobar2'
    item.to = [role1.id, role2.id]
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert set(emails.get('foobar2')['bcc']) == {'a1@localhost', 'a2@localhost'}
    assert emails.get('foobar2')['to'] == 'Undisclosed recipients:;'
    headers = emails.get('foobar2').email.extra_headers
    assert 'Message-ID' in headers
    assert 'In-Reply-To' not in headers
    assert 'References' not in headers


def test_email_django_escaping(pub, emails):
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    item = SendmailWorkflowStatusItem()
    item.to = ['foo@localhost']
    item.subject = 'Foobar'

    # explicit safe strings
    emails.empty()
    formdata.data = {'1': '1 < 3'}
    item.body = '{{ form_var_foo|safe }}'
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.get('Foobar')['payload'].strip() == '1 < 3'

    # automatic no-escaping (because text/plain)
    emails.empty()
    formdata.data = {'1': '1 < 3'}
    item.body = '{{ form_var_foo }}'
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.get('Foobar')['payload'].strip() == '1 < 3'

    # automatic escaping (because mail body is HTML)
    emails.empty()
    formdata.data = {'1': '1 < 3'}
    item.body = '<p>{{ form_var_foo }}</p>'
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.get('Foobar')
    assert '<p>1 &lt; 3</p>' in emails.get('Foobar')['payload'].strip()

    # no automatic escaping for subject (even if mail body is HTML)
    emails.empty()
    formdata.data = {'1': '1 < 3'}
    item.subject = '{{ form_var_foo }}'
    item.body = '<p>{{ form_var_foo }}</p>'
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.get('1 < 3')


def test_email_too_big(pub, emails):
    LoggedError.wipe()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        FileField(id='3', label='File', varname='file'),
    ]
    formdef.store()

    upload = PicklableUpload('test.txt', 'text/plain')
    upload.receive([b'x' * 50_000_000])
    formdata = formdef.data_class()()
    formdata.data = {'3': upload}
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    sendmail = SendmailWorkflowStatusItem()
    sendmail.subject = 'foobar'
    sendmail.body = 'test'
    sendmail.to = ['to@example.net']
    sendmail.attachments = ['{{form_var_file_raw}}']
    sendmail.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 0
    assert LoggedError.count() == 1
    logged_error = LoggedError.select()[0]
    assert logged_error.summary == 'Email too big to be sent (>50MB)'
    os.unlink(formdata.data['3'].get_fs_filename())  # clean big file


def test_email_attachments(pub, emails):
    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = [
        FileField(id='3', label='File', varname='file'),
    ]
    formdef.store()

    upload = PicklableUpload('test.jpeg', 'image/jpeg')
    with open(os.path.join(os.path.dirname(__file__), '..', 'image-with-gps-data.jpeg'), 'rb') as fd:
        upload.receive([fd.read()])
    formdata = formdef.data_class()()
    formdata.data = {'3': upload}
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    sendmail = SendmailWorkflowStatusItem()
    sendmail.subject = 'foobar'
    sendmail.body = '<p>force html</p>'
    sendmail.to = ['to@example.net']
    sendmail.attachments = ['{{ form_var_file_raw }}']
    sendmail.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.emails['foobar']['msg'].is_multipart()
    assert emails.emails['foobar']['msg'].get_content_subtype() == 'mixed'
    assert emails.emails['foobar']['msg'].get_payload()[0].get_content_type() == 'text/html'
    assert emails.emails['foobar']['msg'].get_payload()[1].get_content_type() == 'image/jpeg'

    emails.empty()
    sendmail = SendmailWorkflowStatusItem()
    sendmail.subject = 'foobar'
    sendmail.body = '<p>force html</p>'
    sendmail.to = ['to@example.net', 'too@example.net']
    sendmail.attachments = ['{{ form_var_file_raw }}']
    sendmail.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 2

    assert mail.outbox[0].message().is_multipart()
    assert mail.outbox[0].message().get_content_subtype() == 'mixed'
    assert mail.outbox[0].message().get_payload()[0].get_content_type() == 'text/html'
    assert mail.outbox[0].message().get_payload()[1].get_content_type() == 'image/jpeg'

    assert mail.outbox[1].message().is_multipart()
    assert mail.outbox[1].message().get_content_subtype() == 'mixed'
    assert mail.outbox[1].message().get_payload()[0].get_content_type() == 'text/html'
    assert mail.outbox[1].message().get_payload()[1].get_content_type() == 'image/jpeg'

    # build a backoffice field
    Workflow.wipe()
    FormDef.wipe()
    wf = Workflow(name='email with attachments')
    wf.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(wf)
    wf.backoffice_fields_formdef.fields = [
        FileField(id='bo1-1x', label='bo field 1', varname='backoffice_file1'),
        FileField(id='bo2', label='bo field 2', varname='backoffice_file2'),
    ]
    st1 = wf.add_status('Status1')
    wf.store()
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
    # store file in backoffice field form_fbo1_1x / form_var_backoffice_file_raw & bo2
    setbo = SetBackofficeFieldsWorkflowStatusItem()
    setbo.parent = st1
    setbo.fields = [
        {'field_id': 'bo1-1x', 'value': '{{ form_var_frontoffice_file_raw }}'},
        {'field_id': 'bo2', 'value': '{{ "test"|qrcode }}'},
    ]
    setbo.perform(formdata)

    # check with template with varname-less field
    emails.empty()
    sendmail.attachments = ['{{form_fbo1_1x}}']
    sendmail.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.emails['foobar']['msg'].is_multipart()
    assert emails.emails['foobar']['msg'].get_content_subtype() == 'mixed'
    assert emails.emails['foobar']['msg'].get_payload()[0].get_content_type() == 'text/html'
    assert emails.emails['foobar']['msg'].get_payload()[1].get_content_type() == 'image/jpeg'

    # check with templates
    emails.empty()
    sendmail.attachments = ['{{form_var_backoffice_file1_raw}}']
    sendmail.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.emails['foobar']['msg'].is_multipart()
    assert emails.emails['foobar']['msg'].get_content_subtype() == 'mixed'
    assert emails.emails['foobar']['msg'].get_payload()[0].get_content_type() == 'text/html'
    assert emails.emails['foobar']['msg'].get_payload()[1].get_content_type() == 'image/jpeg'

    emails.empty()
    sendmail.attachments = ['{{form_var_backoffice_file2}}']
    sendmail.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.emails['foobar']['msg'].is_multipart()
    assert emails.emails['foobar']['msg'].get_content_subtype() == 'mixed'
    assert emails.emails['foobar']['msg'].get_payload()[0].get_content_type() == 'text/html'
    assert emails.emails['foobar']['msg'].get_payload()[1].get_content_type() == 'image/png'

    emails.empty()
    sendmail.attachments = ['{% firstof form_var_frontoffice_file form_var_backoffice_file2 %}']
    sendmail.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.emails['foobar']['msg'].is_multipart()
    assert emails.emails['foobar']['msg'].get_content_subtype() == 'mixed'
    assert emails.emails['foobar']['msg'].get_payload()[0].get_content_type() == 'text/html'
    assert emails.emails['foobar']['msg'].get_payload()[1].get_content_type() == 'image/jpeg'

    # unset bo2, check it's properly ignored
    formdata.data['bo2'] = None
    formdata.store()

    emails.empty()
    sendmail.attachments = ['{{form_var_backoffice_file1}}', '{{form_var_backoffice_file2}}']
    sendmail.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.emails['foobar']['msg'].is_multipart()
    assert emails.emails['foobar']['msg'].get_content_subtype() == 'mixed'
    assert len(emails.emails['foobar']['msg'].get_payload()) == 2
    assert emails.emails['foobar']['msg'].get_payload()[0].get_content_type() == 'text/html'
    assert emails.emails['foobar']['msg'].get_payload()[1].get_content_type() == 'image/jpeg'


def test_workflow_email_line_details(pub):
    workflow = Workflow(name='email')
    st1 = workflow.add_status('Status1', 'st1')
    sendmail = SendmailWorkflowStatusItem()
    sendmail.parent = st1

    assert sendmail.get_line_details() == 'not completed'

    role = pub.role_class(name='foorole')
    role.store()
    sendmail.to = [role.id]
    assert sendmail.get_line_details() == 'to foorole'

    sendmail.to = ['test@example.net']
    assert sendmail.get_line_details() == 'to test@example.net'

    sendmail.to = ['{{ foobar }}']
    assert sendmail.get_line_details() == 'to computed value'


def test_workflow_email_to_user_function(pub, emails):
    user = pub.user_class(name='foo')
    user.email = 'foobar@localhost'
    user.name_identifiers = ['0123456789']
    user.store()

    workflow = Workflow(name='wf roles')
    st1 = workflow.add_status('Status1', 'st1')
    item1 = st1.add_action('dispatch')
    item1.role_key = '_receiver'
    item1.role_id = '{{ form_user }}'
    item2 = st1.add_action('sendmail')
    item2.to = ['_receiver']
    item2.subject = 'Foobar'
    item2.body = 'Hello'
    workflow.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.workflow_id = workflow.id
    formdef.store()

    formdata = formdef.data_class()()
    formdata.user_id = user.id
    formdata.just_created()
    formdata.store()
    pub.substitutions.feed(formdata)

    item1.perform(formdata)
    assert formdata.workflow_roles == {'_receiver': ['_user:%s' % user.id]}

    emails.empty()
    item2.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.get('Foobar')
    assert emails.get('Foobar')['email_rcpt'] == ['foobar@localhost']


def test_email_part(pub, emails):
    pub.substitutions.feed(MockSubstitutionVariables())

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.store()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    user = pub.user_class(name='foo')
    user.email = 'zorg@localhost'
    user.store()

    role1 = pub.role_class(name='bar')
    role1.emails = ['bar@localhost', 'baz@localhost']
    role1.store()

    item = SendmailWorkflowStatusItem()
    item.to = [role1.id]
    item.subject = 'foobar'
    item.body = 'baz'
    item.varname = 'zzz'
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert emails.get('foobar')

    substvars = CompatibilityNamesDict()
    substvars.update(formdata.get_substitution_variables())
    keys = substvars.get_flat_keys()
    for key in keys:
        # noqa pylint: disable=unused-variable
        var = substvars[key]  # check it doesn't raise, ignore the value

    assert substvars['form_workflow_email_zzz_subject'] == 'foobar'
    assert substvars['form_workflow_email_zzz_body']
    assert substvars['form_workflow_email_zzz_datetime']
    assert substvars['form_workflow_email_zzz_addresses']

    # check indexed access is not advertised but does work
    assert 'form_workflow_email_zzz_0_subject' not in keys
    assert substvars['form_workflow_email_zzz_0_subject'] == 'foobar'

    # run a second time
    item.subject = 'foobar2'
    item.perform(formdata)
    pub.process_after_jobs()
    keys = substvars.get_flat_keys()

    # check indexed access is now advertised
    assert 'form_workflow_email_zzz_0_subject' in keys
    assert 'form_workflow_email_zzz_1_subject' in keys
    # check indexed access
    assert substvars['form_workflow_email_zzz_0_subject'] == 'foobar'
    assert substvars['form_workflow_email_zzz_1_subject'] == 'foobar2'
    # check non-indexed access gives the latest value
    assert substvars['form_workflow_email_zzz_subject'] == 'foobar2'


def test_email_computed_recipients(pub, emails):
    pub.user_class.wipe()
    FormDef.wipe()

    user1 = pub.user_class(name='userA')
    user1.name_identifiers = ['xxy1']
    user1.email = 'user1@example.com'
    user1.store()
    user2 = pub.user_class(name='userB')
    user2.name_identifiers = ['xxy2']
    user2.email = 'user2@example.com'
    user2.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    formdatas = []
    for i in range(2):
        formdatas.append(formdef.data_class()())

    formdatas[0].user_id = user1.id
    formdatas[1].user_id = user2.id

    for formdata in formdatas:
        formdata.just_created()
        formdata.store()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    item = SendmailWorkflowStatusItem()
    item.varname = 'test'
    item.to = []
    item.subject = 'xxx'
    item.body = 'XXX'

    for recipient in [
        'user1@example.com,user2@example.com',
        '{% for obj in forms|objects:"foo" %}{{ obj|get:"form_user_email" }},{% endfor %}',
        '{{ forms|objects:"foo"|getlist:"form_user_email" }}',
        '{{ forms|objects:"foo"|getlist:"form_user_email"|list }}',
        '{{ forms|objects:"foo"|getlist:"form_user" }}',
        '{{ forms|objects:"foo"|getlist:"form_user"|list }}',
    ]:
        item.to = [recipient]
        emails.empty()
        item.perform(formdata)
        pub.process_after_jobs()
        assert emails.count() == 1
        assert set(formdata.evolution[-1].parts[-1].addresses) == {'user1@example.com', 'user2@example.com'}
        formdata.evolution[-1].parts = []

    formdata.user_id = user1.id
    pub.substitutions.feed(formdata)
    item.to = ['{{form_user}}']
    emails.empty()
    item.perform(formdata)
    pub.process_after_jobs()
    assert emails.count() == 1
    assert set(formdata.evolution[-1].parts[-1].addresses) == {'user1@example.com'}
    formdata.evolution[-1].parts = []


@pytest.mark.parametrize('req', [True, False])
def test_email_invalid_recipients(pub, req):
    if req is False:
        pub._set_request(None)

    FormDef.wipe()

    formdef = FormDef()
    formdef.name = 'baz'
    formdef.fields = []
    formdef.store()
    formdef.data_class().wipe()

    formdata = formdef.data_class()()
    formdata.just_created()
    formdata.store()

    item = SendmailWorkflowStatusItem()
    item.varname = 'test'
    item.to = ['invalid,']
    item.subject = 'xxx'
    item.body = 'XXX'

    with mock.patch('wcs.qommon.emails.EmailToSendAfterJob.execute') as send_email_job:
        item.perform(formdata)
        if req:
            pub.process_after_jobs()
        assert send_email_job.call_count == 0


def test_workflows_edit_sendmail_action(pub):
    create_superuser(pub)
    Workflow.wipe()
    workflow = Workflow(name='foo')
    st1 = workflow.add_status(name='baz')
    workflow.store()

    app = login(get_app(pub))
    resp = app.get(st1.get_admin_url())

    resp.forms[0]['action-interaction'] = 'Email'
    resp = resp.forms[0].submit()
    resp = resp.follow()

    resp = resp.click('Email')
    resp.form['subject'] = 'ok'
    resp.form['body'] = 'ok'
    resp.form['to$element0$choice'] = '__other'
    resp.form['to$element0$other$value_template'] = '{{ test }}'
    resp = resp.form.submit('submit').follow().follow()
    workflow.refresh_from_storage()
    assert workflow.possible_status[0].items[0].to == ['{{ test }}']

    resp = resp.click('Email')
    resp.form['to$element0$choice'] = '__other'
    resp.form['to$element0$other$value_template'] = 'test@example.org'
    resp = resp.form.submit('submit').follow().follow()
    workflow.refresh_from_storage()
    assert workflow.possible_status[0].items[0].to == ['test@example.org']

    resp = resp.click('Email')
    resp.form['to$element0$choice'] = '__other'
    resp.form['to$element0$other$value_template'] = 'test'
    resp = resp.form.submit('submit')
    assert resp.pyquery('.widget-with-error .error').text() == 'Value must be a template or an email address.'

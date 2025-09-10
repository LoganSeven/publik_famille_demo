import base64
import os
import pwd
import socket

import pytest

from wcs.qommon.emails import docutils  # noqa pylint: disable=unused-import
from wcs.qommon.emails import email as send_email
from wcs.qommon.emails import is_sane_address
from wcs.qommon.upload_storage import PicklableUpload

from .utilities import clean_temporary_pub, cleanup, create_temporary_pub


def setup_module(module):
    cleanup()


def teardown_module(module):
    clean_temporary_pub()


@pytest.mark.parametrize(
    'email, result',
    [
        ('test@localhost', True),
        ('test2@localhost', True),
        ('test@localhost@localhost', False),
        ('test@example.com@example.com', False),
        ('Marie-Hélène', False),
        ('', False),
        (' ', False),
        (None, False),
    ],
)
def test_is_sane_address(email, result):
    assert is_sane_address(email) == result


def test_email_signature_plain(emails):
    pub = create_temporary_pub()
    pub.cfg['emails'] = {'footer': 'Footer\nText'}
    send_email('test', mail_body='Hello', email_rcpt='test@localhost', want_html=False)
    assert emails.count() == 1
    assert not emails.emails['test']['msg'].is_multipart()
    assert b'Footer\nText' in emails.emails['test']['msg'].get_payload(decode=True)


def test_email_from(emails):
    pub = create_temporary_pub()
    send_email('test', mail_body='Hello', email_rcpt='test@localhost', want_html=False)
    assert emails.count() == 1
    assert emails.emails['test']['from'] == '%s@%s' % (pwd.getpwuid(os.getuid())[0], socket.getfqdn())

    emails.empty()
    pub.cfg['emails'] = {'from': 'foo@localhost'}
    send_email('test', mail_body='Hello', email_rcpt='test@localhost', want_html=False)
    assert emails.count() == 1
    assert emails.emails['test']['from'] == 'foo@localhost'
    assert emails.emails['test']['msg']['From'] == 'foo@localhost'

    emails.empty()
    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'global_title', 'HELLO')
    send_email('test', mail_body='Hello', email_rcpt='test@localhost', want_html=False)
    assert emails.count() == 1
    assert emails.emails['test']['from'] == 'foo@localhost'
    assert emails.emails['test']['msg']['From'] in (
        '=?utf-8?q?HELLO?= <foo@localhost>',
        'HELLO <foo@localhost>',
    )


def test_email_recipients(emails):
    pub = create_temporary_pub()
    send_email('test', mail_body='Hello', email_rcpt='test@localhost', want_html=False)
    assert emails.count() == 1
    assert emails.emails['test']['email_rcpt'] == ['test@localhost']

    pub.cfg['debug'] = {'mail_redirection': 'redirection@localhost'}
    emails.empty()
    send_email('test', mail_body='Hello', email_rcpt='test@localhost', want_html=False)
    assert emails.count() == 1
    assert emails.emails['test']['email_rcpt'] == ['redirection@localhost']

    emails.empty()
    send_email(
        'test', mail_body='Hello', email_rcpt='test@localhost', want_html=False, ignore_mail_redirection=True
    )
    assert emails.count() == 1
    assert emails.emails['test']['email_rcpt'] == ['test@localhost']

    orig_environ = os.environ.copy()
    try:
        os.environ['QOMMON_MAIL_REDIRECTION'] = 'qommon.redirection@localhost'
        emails.empty()
        send_email('test', mail_body='Hello', email_rcpt='test@localhost', want_html=False)
        assert emails.count() == 1
        assert emails.emails['test']['email_rcpt'] == ['qommon.redirection@localhost']

        emails.empty()
        send_email(
            'test',
            mail_body='Hello',
            email_rcpt='test@localhost',
            want_html=False,
            ignore_mail_redirection=True,
        )
        assert emails.count() == 1
        assert emails.emails['test']['email_rcpt'] == ['test@localhost']
    finally:
        os.environ = orig_environ

    # multiple recipients
    emails.empty()
    send_email(
        'test',
        mail_body='Hello',
        email_rcpt=['test@localhost', 'test2@localhost'],
        want_html=False,
        ignore_mail_redirection=True,
    )
    assert emails.count() == 1
    assert emails.emails['test']['email_rcpt'] == ['test@localhost', 'test2@localhost']

    # invalid recipient
    emails.empty()
    send_email(
        'test',
        mail_body='Hello',
        email_rcpt=[
            'test@localhost',
            'test@localhost@localhost',
            'test@example.fr@example.com',
            'test@.fr',
            '',
            '',
        ],
        want_html=False,
        ignore_mail_redirection=True,
    )
    assert emails.count() == 1
    assert emails.emails['test']['email_rcpt'] == ['test@localhost']
    emails.empty()
    send_email(
        'test',
        mail_body='Hello',
        email_rcpt=None,
        bcc=[
            'test@localhost',
            'test@localhost@localhost',
            'test@example.fr@example.com',
            'test@.fr',
            '',
            '',
        ],
        want_html=False,
        ignore_mail_redirection=True,
    )
    assert emails.count() == 1
    assert emails.emails['test']['email_rcpt'] == ['test@localhost']


def test_email_many_recipients(emails):
    create_temporary_pub()
    send_email(
        'test',
        mail_body='Hello',
        email_rcpt='test@localhost',
        want_html=False,
        bcc=[f'foo{x}@example.invalid' for x in range(10)],
    )
    assert emails.count() == 1
    assert len(emails.emails['test']['email_rcpt']) == 11
    assert not emails.emails['test'].email.extra_headers.get('X-Publik-Many-Recipients')
    emails.empty()

    send_email(
        'test',
        mail_body='Hello',
        email_rcpt='test@localhost',
        want_html=False,
        bcc=[f'foo{x}@example.invalid' for x in range(100)],
    )
    assert emails.count() == 1
    assert len(emails.emails['test']['email_rcpt']) == 101
    assert emails.emails['test'].email.extra_headers.get('X-Publik-Many-Recipients')


@pytest.mark.skipif('docutils is None')
def test_email_signature_rst(emails):
    pub = create_temporary_pub()
    pub.cfg['emails'] = {'footer': 'Footer\nText'}
    send_email('test', mail_body='Hello', email_rcpt='test@localhost')
    assert emails.count() == 1
    assert emails.emails['test']['msg'].is_multipart()
    assert emails.emails['test']['msg'].get_content_subtype() == 'alternative'
    assert emails.emails['test']['msg'].get_payload()[0].get_content_type() == 'text/plain'
    assert emails.emails['test']['msg'].get_payload()[1].get_content_type() == 'text/html'
    assert b'Footer\nText' in emails.emails['test']['msg'].get_payload()[0].get_payload(decode=True)
    assert b'>Footer<' in emails.emails['test']['msg'].get_payload()[1].get_payload(decode=True)


@pytest.mark.skipif('docutils is None')
def test_email_signature_rst_pipes(emails):
    pub = create_temporary_pub()
    pub.cfg['emails'] = {'footer': '| Footer\n| Text'}
    send_email('test', mail_body='Hello', email_rcpt='test@localhost')
    assert emails.count() == 1
    assert emails.emails['test']['msg'].is_multipart()
    assert emails.emails['test']['msg'].get_content_subtype() == 'alternative'
    assert emails.emails['test']['msg'].get_payload()[0].get_content_type() == 'text/plain'
    assert emails.emails['test']['msg'].get_payload()[1].get_content_type() == 'text/html'
    assert b'Footer\nText' in emails.emails['test']['msg'].get_payload()[0].get_payload(decode=True)
    assert b'>Footer<' in emails.emails['test']['msg'].get_payload()[1].get_payload(decode=True)


@pytest.mark.skipif('docutils is None')
def test_email_titles_sizes(emails):
    create_temporary_pub()
    send_email(
        'test',
        mail_body='''Hello,

-----
Title
-----

Smaller title
-------------

Some text.
               ''',
        email_rcpt='test@localhost',
    )
    assert emails.count() == 1
    assert emails.emails['test']['msg'].is_multipart()
    assert emails.emails['test']['msg'].get_content_subtype() == 'alternative'
    assert emails.emails['test']['msg'].get_payload()[0].get_content_type() == 'text/plain'
    assert emails.emails['test']['msg'].get_payload()[1].get_content_type() == 'text/html'
    assert '<h2 style="line-height: 150%;">' in emails.emails['test']['msg'].get_payload()[1].get_payload()
    assert '<h3 style="line-height: 150%;">' in emails.emails['test']['msg'].get_payload()[1].get_payload()


def test_email_plain_with_attachments(emails):
    create_temporary_pub()
    jpg = PicklableUpload('test.jpeg', 'image/jpeg')
    with open(os.path.join(os.path.dirname(__file__), 'image-with-gps-data.jpeg'), 'rb') as fd:
        jpg_content = fd.read()
    jpg.receive([jpg_content])
    txt = PicklableUpload('test.txt', 'text/plain')
    txt.receive([b'foo-text-bar'])
    odt = PicklableUpload('test.odt', 'application/vnd.oasis.opendocument.text')
    with open(os.path.join(os.path.dirname(__file__), 'template.odt'), 'rb') as fd:
        odt_content = fd.read()
    odt.receive([odt_content])

    send_email('jpg', mail_body='Hello', email_rcpt='test@localhost', want_html=False, attachments=[jpg])
    assert emails.count() == 1
    assert emails.emails['jpg']['msg'].is_multipart()
    assert emails.emails['jpg']['msg'].get_content_subtype() == 'mixed'
    assert emails.emails['jpg']['msg'].get_payload()[0].get_content_type() == 'text/plain'
    assert emails.emails['jpg']['msg'].get_payload()[1].get_content_type() == 'image/jpeg'
    assert base64.b64decode(emails.emails['jpg']['msg'].get_payload()[1].get_payload()) == jpg_content

    send_email('txt', mail_body='Hello', email_rcpt='test@localhost', want_html=False, attachments=[txt])
    assert emails.emails['txt']['msg'].is_multipart()
    assert emails.emails['txt']['msg'].get_content_subtype() == 'mixed'
    assert emails.emails['txt']['msg'].get_payload()[0].get_content_type() == 'text/plain'
    assert emails.emails['txt']['msg'].get_payload()[1].get_content_type() == 'text/plain'
    assert emails.emails['txt']['msg'].get_payload()[1].get_payload(decode=True) == b'foo-text-bar'

    send_email(
        'jpgodt', mail_body='Hello', email_rcpt='test@localhost', want_html=False, attachments=[jpg, odt]
    )
    assert emails.emails['jpgodt']['msg'].is_multipart()
    assert emails.emails['jpgodt']['msg'].get_content_subtype() == 'mixed'
    assert emails.emails['jpgodt']['msg'].get_payload()[0].get_content_type() == 'text/plain'
    assert emails.emails['jpgodt']['msg'].get_payload()[1].get_content_type() == 'image/jpeg'
    assert (
        emails.emails['jpgodt']['msg'].get_payload()[2].get_content_type()
        == 'application/vnd.oasis.opendocument.text'
    )
    assert base64.b64decode(emails.emails['jpgodt']['msg'].get_payload()[1].get_payload()) == jpg_content
    assert base64.b64decode(emails.emails['jpgodt']['msg'].get_payload()[2].get_payload()) == odt_content

    unknown = PicklableUpload('test.eo', 'x-foo/x-bar')
    unknown.receive([b'barfoo'])
    send_email(
        'unknown', mail_body='Hello', email_rcpt='test@localhost', want_html=False, attachments=[unknown]
    )
    assert emails.emails['unknown']['msg'].is_multipart()
    assert emails.emails['unknown']['msg'].get_content_subtype() == 'mixed'
    assert emails.emails['unknown']['msg'].get_payload()[0].get_content_type() == 'text/plain'
    assert emails.emails['unknown']['msg'].get_payload()[1].get_content_type() == 'x-foo/x-bar'
    assert emails.emails['unknown']['msg'].get_payload()[1].get_payload(decode=False).strip() == 'YmFyZm9v'

    send_email(
        'test-bad-attachment',
        mail_body='Hello',
        email_rcpt='test@localhost',
        want_html=False,
        attachments=['foobad'],
    )
    assert not emails.emails['test-bad-attachment']['msg'].is_multipart()

    assert emails.count() == 5


@pytest.mark.skipif('docutils is None')
def test_email_plain_and_html_with_attachments(emails):
    pub = create_temporary_pub()
    pub.cfg['emails'] = {'footer': 'Footer\nText'}
    jpg = PicklableUpload('test.jpeg', 'image/jpeg')
    with open(os.path.join(os.path.dirname(__file__), 'image-with-gps-data.jpeg'), 'rb') as fd:
        jpg.receive([fd.read()])

    send_email('test', mail_body='Hello', email_rcpt='test@localhost', attachments=[jpg])
    assert emails.count() == 1
    assert emails.emails['test']['msg'].is_multipart()
    assert emails.emails['test']['msg'].get_content_subtype() == 'mixed'
    assert emails.emails['test']['msg'].get_payload()[0].is_multipart()
    assert emails.emails['test']['msg'].get_payload()[0].get_content_subtype() == 'alternative'
    assert emails.emails['test']['msg'].get_payload()[0].get_payload()[0].get_content_type() == 'text/plain'
    assert emails.emails['test']['msg'].get_payload()[0].get_payload()[1].get_content_type() == 'text/html'
    assert emails.emails['test']['msg'].get_payload()[1].get_content_type() == 'image/jpeg'


@pytest.mark.skipif('docutils is None')
def test_email_with_enumeration(emails):
    pub = create_temporary_pub()
    pub.cfg['emails'] = {'footer': 'Footer\nText'}
    mail_body = '''
A. FooAlpha1
B. FooAlpha2

1. Num1
2. Num2

M. Francis Kuntz

'''
    send_email('test', mail_body=mail_body, email_rcpt='test@localhost')
    assert emails.count() == 1
    assert emails.emails['test']['msg'].is_multipart()
    assert emails.emails['test']['msg'].get_content_subtype() == 'alternative'
    assert emails.emails['test']['msg'].get_payload()[0].get_content_type() == 'text/plain'
    assert emails.emails['test']['msg'].get_payload()[1].get_content_type() == 'text/html'
    html = emails.emails['test']['msg'].get_payload()[1].get_payload(decode=True)
    assert html.count(b'<ol') == 1
    assert b'<ul' not in html
    assert b'arabic simple' in html
    assert b'M. Francis Kuntz' in html


@pytest.mark.skipif('docutils is None')
def test_email_with_unexpected_transition(emails):
    create_temporary_pub()
    mail_body = '''
Value:
 A

Other value:
 ?????????

Plop:
 C

bye,
'''
    send_email('test', mail_body=mail_body, email_rcpt='test@localhost')
    assert emails.count() == 1
    assert emails.emails['test']['msg'].is_multipart()
    assert emails.emails['test']['msg'].get_content_subtype() == 'alternative'
    assert emails.emails['test']['msg'].get_payload()[0].get_content_type() == 'text/plain'
    assert emails.emails['test']['msg'].get_payload()[1].get_content_type() == 'text/html'
    text = emails.emails['test']['msg'].get_payload()[0].get_payload(decode=True)
    html = emails.emails['test']['msg'].get_payload()[1].get_payload(decode=True)
    assert text.count(b'\n ?????????\n') == 1
    assert html.count(b'<dd>?????????</dd>') == 1


def test_email_report_headers(emails):
    pub = create_temporary_pub()

    pub.load_site_options()
    if not pub.site_options.has_section('variables'):
        pub.site_options.add_section('variables')
    pub.site_options.set('variables', 'email_unsubscribe_info_url', 'http://unsub-url/')
    pub.site_options.set('variables', 'email_abuse_report_url', 'http:/abuse-url/')
    send_email('test', mail_body='Hello', email_rcpt='test@localhost', want_html=False)
    assert emails.count() == 1
    assert 'List-Unsubscribe: <http://unsub-url/>' in str(emails.emails['test']['msg'])
    assert 'X-Report-Abuse: Please report abuse for this email here: http:/abuse-url/' in str(
        emails.emails['test']['msg']
    )

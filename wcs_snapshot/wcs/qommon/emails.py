# w.c.s. - web application for online forms
# Copyright (C) 2005-2010  Entr'ouvert
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

import hashlib
import os
import pwd
import re
import smtplib
import socket
from email import encoders
from email.errors import MessageError
from email.header import Header
from email.mime.application import MIMEApplication
from email.mime.audio import MIMEAudio
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.nonmultipart import MIMENonMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

try:
    import docutils
    import docutils.core
    import docutils.io
    import docutils.parsers.rst
    import docutils.parsers.rst.states
except ImportError:
    docutils = None

from django.core.mail import EmailMessage, EmailMultiAlternatives, get_connection
from django.core.mail.message import sanitize_address
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe
from quixote import get_publisher, get_request

from . import _, errors, force_str
from .admin.emails import EmailsDirectory
from .afterjobs import AfterJob
from .publisher import get_cfg, get_logger
from .template import Template

try:
    from email import Charset

    Charset.add_charset('utf-8', Charset.QP, Charset.QP, 'utf-8')
except ImportError:
    pass


if docutils:
    from docutils import statemachine

    class Body(docutils.parsers.rst.states.Body):
        def is_enumerated_list_item(self, ordinal, sequence, format):
            # customised to only allow arabic sequences, this prevents the rst
            # parser to consider M. as starting a (upper alpha / roman) sequence.
            if format == 'period' and sequence != 'arabic':
                return False
            return docutils.parsers.rst.states.Body.is_enumerated_list_item(self, ordinal, sequence, format)

        def line(self, match, context, next_state):
            # customised to ignore unexpected overlines or transitions (due
            # for example by a field filled by question marks.
            if self.state_machine.match_titles:
                return [match.string], 'Line', []
            if match.string.strip() == '::':
                raise statemachine.TransitionCorrection('text')
            # Unexpected possible title overline or transition.
            # Treating it as ordinary text.
            raise statemachine.TransitionCorrection('text')

    class CustomRstParser(docutils.parsers.rst.Parser):
        def __init__(self, *args, **kwargs):
            docutils.parsers.rst.Parser.__init__(self, *args, **kwargs)
            self.state_classes = tuple([Body] + list(self.state_classes[1:]))
            docutils.parsers.rst.states.state_classes = self.state_classes

    def custom_rststate_init(self, state_machine, debug=False):
        state_classes = tuple([Body] + list(docutils.parsers.rst.states.state_classes[1:]))
        self.nested_sm_kwargs = {'state_classes': state_classes, 'initial_state': 'Body'}
        docutils.parsers.rst.states.StateWS.__init__(self, state_machine, debug)

    docutils.parsers.rst.states.RSTState.__init__ = custom_rststate_init


def custom_template_email(key, mail_body_data, email_rcpt, **kwargs):
    if not EmailsDirectory.is_enabled(key):
        return
    mail_subject = EmailsDirectory.get_subject(key)
    mail_body = EmailsDirectory.get_body(key)
    if not mail_body_data:
        mail_body_data = {}
    if not mail_body_data.get('sitename'):
        mail_body_data['sitename'] = get_cfg('misc', {}).get('sitename')
    return template_email(
        mail_subject, mail_body, mail_body_data, email_type=key, email_rcpt=email_rcpt, **kwargs
    )


def template_email(subject, mail_body, mail_body_data, email_rcpt, email_type=None, **kwargs):
    data = get_publisher().substitutions.get_context_variables(mode='lazy')
    if mail_body_data:
        data.update(mail_body_data)
    real_subject = Template(subject, autoescape=False).render(data)
    real_mail_body = Template(mail_body, autoescape=False).render(data)
    return email(real_subject, real_mail_body, email_rcpt=email_rcpt, email_type=email_type, **kwargs)


def convert_to_mime(attachment):
    if hasattr(attachment, 'get_content'):  # qommon.form.PicklableUpload-like object
        content = attachment.get_content()
        content_type = getattr(attachment, 'content_type', None) or 'application/octet-stream'
        maintype, subtype = content_type.split('/', 1)
        charset = getattr(attachment, 'charset', None) or get_publisher().site_charset
        if maintype == 'application':
            part = MIMEApplication(content, subtype)
        elif maintype == 'image':
            part = MIMEImage(content, subtype)
        elif maintype == 'text':
            part = MIMEText(content, subtype, _charset=charset)
        elif maintype == 'audio':
            part = MIMEAudio(content, subtype)
        else:
            part = MIMENonMultipart(maintype, subtype)
            part.set_payload(content, charset=charset)
            encoders.encode_base64(part)
        if getattr(attachment, 'base_filename', None):
            part.add_header('Content-Disposition', 'attachment', filename=attachment.base_filename)
        return part
    get_logger().warning('Failed to build MIME part from %r', attachment)


def is_sane_address(email):
    if not email or '@' not in force_str(email):
        return False
    try:
        sanitize_address(email, 'utf-8')
    except (IndexError, ValueError, MessageError):
        return False
    return True


def email(*args, **kwargs):
    fire_and_forget = kwargs.pop('fire_and_forget', False)
    email = get_email(*args, **kwargs)
    if email:
        return send_email(email, fire_and_forget)


def get_email(
    subject,
    mail_body,
    email_rcpt,
    *,
    replyto=None,
    bcc=None,
    email_from=None,
    email_type=None,
    want_html=True,
    hide_recipients=False,
    smtp_timeout=None,
    attachments=(),
    extra_headers=None,
    ignore_mail_redirection=False,
):
    # noqa pylint: disable=too-many-arguments

    emails_cfg = get_cfg('emails', {})
    footer = emails_cfg.get('footer') or ''

    encoding = get_publisher().site_charset

    # in restructuredtext lines starting with a pipe were used to give
    # appropriate multiline formatting, remove them.
    footer = re.sub(r'^\|\s+', '', footer, flags=re.DOTALL | re.MULTILINE)

    text_body = str(mail_body)
    html_body = None

    if text_body.startswith('<'):
        # native HTML, keep it that way
        html_body = text_body
        text_body = None
    elif want_html:
        # body may be reStructuredText, try converting.
        try:
            htmlmail = docutils.core.publish_programmatically(
                source_class=docutils.io.StringInput,
                source=mail_body,
                source_path=None,
                destination_class=docutils.io.StringOutput,
                destination=None,
                destination_path=None,
                reader=None,
                reader_name='standalone',
                parser=CustomRstParser(),
                parser_name=None,
                writer=None,
                writer_name='html',
                settings=None,
                settings_spec=None,
                settings_overrides={
                    'input_encoding': encoding,
                    'output_encoding': encoding,
                    'embed_stylesheet': False,
                    'stylesheet': None,
                    'stylesheet_path': None,
                    'file_insertion_enabled': 0,
                    'xml_declaration': 0,
                    'report_level': 5,
                    'initial_header_level': 2,
                },
                config_section=None,
                enable_exit_status=None,
            )[0]
            # change paragraphs so manual newlines are considered.
            htmlmail = force_str(htmlmail).replace('<p>', '<p style="white-space: pre-line;">')
            htmlmail = force_str(htmlmail).replace(
                '<p style="white-space: pre-line;">---===BUTTON', '<p>---===BUTTON'
            )
            # change titles to have a more appropriate line height
            htmlmail = force_str(htmlmail).replace('<h2>', '<h2 style="line-height: 150%;">')
            htmlmail = force_str(htmlmail).replace('<h3>', '<h3 style="line-height: 150%;">')
            htmlmail = force_str(htmlmail).replace('<h4>', '<h4 style="line-height: 150%;">')
        except Exception:
            htmlmail = None

        try:
            html_body = re.findall('<body>(.*)</body>', htmlmail or '', re.DOTALL)[0]
        except IndexError:
            pass

    context = get_publisher().get_substitution_variables()
    context['email_signature'] = footer
    context['subject'] = mark_safe(subject)

    subject = render_to_string('qommon/email_subject.txt', context).strip()
    subject = subject.replace('\n', ' ')  # make sure newlines are stripped

    # handle action links/buttons
    url_button_re = re.compile(r'---===BUTTON:URL:(?P<url>https?:\/\/.*?):(?P<label>.*?)===---')
    button_re = re.compile(r'---===BUTTON:(?P<token>[a-zA-Z0-9]*):(?P<label>.*?)===---')

    def get_action_url(match):
        match_dict = match.groupdict()
        if 'url' in match_dict:
            return match_dict.get('url')
        return '%s/actions/%s/' % (get_publisher().get_frontoffice_url(), match_dict.get('token'))

    def text_button(match):
        return '[%s] %s' % (match.group('label'), get_action_url(match))

    def html_button(match):
        context = {
            'label': match.group('label'),
            'url': get_action_url(match),
        }
        return force_str(render_to_string('qommon/email_button_link.html', context))

    has_button = '---===BUTTON' in (text_body or html_body)
    text_body = url_button_re.sub(text_button, text_body) if text_body else None
    html_body = url_button_re.sub(html_button, html_body) if html_body else None
    text_body = button_re.sub(text_button, text_body) if text_body else None
    html_body = button_re.sub(html_button, html_body) if html_body else None

    if text_body:
        context['content'] = mark_safe(text_body)
        text_body = render_to_string('qommon/email_body.txt', context)

    if html_body:
        context['content'] = mark_safe(html_body)
        html_body = render_to_string('qommon/email_body.html', context)
        if has_button:
            # Merge contiguous buttons, generated HTML should have specific markers
            # (as HTML comments),
            # <!-- button link start -->
            #  <!-- button link inner start -->
            #  <!-- button link inner end -->
            # <!-- button link end -->
            # in case of contiguous buttons whatever gets between inner end and next
            # inner start will be stripped.
            def sub(match):
                return re.sub(
                    r'<!-- button link inner end -->'
                    r'.*<!-- button link end -->\s*<!-- button link start -->.*'
                    '<!-- button link inner start -->',
                    '',
                    match.group(),
                    flags=re.DOTALL | re.MULTILINE,
                )

            html_body = re.sub(
                r'<!-- button link inner end -->(.*?)<!-- button link inner start -->',
                sub,
                html_body,
                flags=re.DOTALL | re.MULTILINE,
            )

    to_emails = []
    bcc_emails = bcc or []
    bcc_emails = [x.strip() for x in bcc_emails if is_sane_address(x.strip())]
    if email_rcpt:
        if isinstance(email_rcpt, str):
            email_rcpt = [email_rcpt]
        email_rcpt = [x.strip() for x in email_rcpt if is_sane_address(x.strip())]
        if hide_recipients:
            bcc_emails += email_rcpt[:]
        else:
            to_emails += email_rcpt[:]
    if not ignore_mail_redirection:
        mail_redirection = get_cfg('debug', {}).get('mail_redirection')
        if mail_redirection:
            to_emails, bcc_emails = [mail_redirection], []
        if os.environ.get('QOMMON_MAIL_REDIRECTION'):
            # if QOMMON_MAIL_REDIRECTION is set in the environment, send all emails
            # to that address instead of the real recipients.
            to_emails, bcc_emails = [os.environ.get('QOMMON_MAIL_REDIRECTION')], []

    if not email_from:
        email_from = emails_cfg.get('from')
        if not email_from:
            email_from = '%s@%s' % (pwd.getpwuid(os.getuid())[0], socket.getfqdn())

    reply_to = None
    if emails_cfg.get('reply_to'):
        reply_to = [emails_cfg.get('reply_to')]
    if replyto:
        reply_to = [replyto]

    attachment_hashes = set()
    attachments_parts = []
    for attachment in attachments or []:
        if not isinstance(attachment, MIMEBase):
            attachment = convert_to_mime(attachment)
        if attachment:
            attachment_hash = hashlib.sha1(bytes(attachment)).hexdigest()
            if attachment_hash in attachment_hashes:
                # do not send same attachment twice
                continue
            attachment_hashes.add(attachment_hash)
            attachments_parts.append(attachment)

    if not to_emails and not bcc_emails:
        return

    extra_headers = extra_headers or {}
    for var in ('email_unsubscribe_info_url', 'portal_url'):
        unsub_url = get_publisher().get_site_option(var, 'variables')
        if unsub_url:
            extra_headers['List-Unsubscribe'] = f'<{unsub_url}>'
            break
    abuse_url = get_publisher().get_site_option('email_abuse_report_url', 'variables')
    if abuse_url:
        extra_headers['X-Report-Abuse'] = f'Please report abuse for this email here: {abuse_url}'

    email_msg_kwargs = {
        'subject': subject,
        'to': to_emails,
        'bcc': bcc_emails,
        'from_email': email_from,
        'reply_to': reply_to,
        'attachments': attachments_parts,
        'headers': {
            'X-Qommon-Id': os.path.basename(get_publisher().app_dir),
        },
    }
    if extra_headers:
        for key, value in extra_headers.items():
            email_msg_kwargs['headers'][key] = value

    if bcc_emails and len(bcc_emails) > 50:
        # add custom header so mass mailings can be routed differently
        email_msg_kwargs['headers']['X-Publik-Many-Recipients'] = 'on'

    sender_name = get_publisher().get_site_option(
        'email_sender_name', 'variables'
    ) or get_publisher().get_site_option('global_title', 'variables')
    if sender_name:
        email_msg_kwargs['headers']['From'] = formataddr((str(Header(sender_name, encoding)), email_from))

    if hide_recipients or not to_emails:
        email_msg_kwargs['headers']['To'] = 'Undisclosed recipients:;'

    if text_body and html_body:
        email_msg = EmailMultiAlternatives(body=text_body, **email_msg_kwargs)
        email_msg.attach_alternative(html_body, 'text/html')
    else:
        email_msg = EmailMessage(body=html_body or text_body, **email_msg_kwargs)
        if html_body:
            email_msg.content_subtype = 'html'

    if len(str(email_msg.message())) > errors.TooBigEmailError.limit:
        raise errors.TooBigEmailError()

    return EmailToSendAfterJob(email_msg, smtp_timeout)


def send_email(email_to_send, fire_and_forget=False):
    if not get_request():
        # we are not processing a request, no sense delaying the handling
        # (for example when running a cronjob)
        fire_and_forget = False

    if not fire_and_forget:
        email_to_send.execute()
    else:
        get_publisher().add_after_job(email_to_send)


class EmailToSendAfterJob(AfterJob):
    label = _('Sending email')

    def __init__(self, email_msg, smtp_timeout):
        super().__init__()
        self.email_msg = email_msg
        self.smtp_timeout = smtp_timeout

    def execute(self):
        publisher = get_publisher()
        emails_cfg = get_cfg('emails', {})

        try:
            if emails_cfg.get('smtp_server', None):
                kwargs = {
                    'host': emails_cfg['smtp_server'],
                    'username': emails_cfg.get('smtp_login') or '',
                    'password': emails_cfg.get('smtp_password') or '',
                    'timeout': self.smtp_timeout,
                }
                backend = get_connection(**kwargs)
                self.email_msg.connection = backend

            # noqa pylint: disable=unused-variable
            message_id = self.email_msg.extra_headers.get('Message-ID')

            self.email_msg.send()
        except TimeoutError as e:
            publisher.record_error(_('Failed to connect to SMTP server (timeout)'), exception=e)
            raise errors.EmailError('Failed to connect to SMTP server (timeout)')
        except smtplib.SMTPAuthenticationError as e:
            publisher.record_error(_('Failed to authenticate to SMTP server'), exception=e)
            raise errors.EmailError('Failed to authenticate to SMTP server')
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPNotSupportedError, smtplib.SMTPDataError):
            pass
        except smtplib.SMTPException as e:
            publisher.record_error(_('Failed to send email, SMTP error.'), exception=e)
            raise errors.EmailError('Failed to send email, SMTP error (%s).' % e)
        except OSError as e:
            publisher.record_error(_('Failed to connect to SMTP server'), exception=e)
            raise errors.EmailError('Failed to connect to SMTP server')
        finally:
            # reset to None as it cannot be pickled
            self.email_msg.connection = None

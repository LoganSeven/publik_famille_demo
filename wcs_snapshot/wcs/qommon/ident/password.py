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

import random

from quixote import get_publisher, get_request, get_response, get_session, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext

from wcs.qommon.admin.texts import TextsDirectory

from .. import _, emails, errors, get_cfg, misc
from .. import storage as st
from .. import template
from ..admin.emails import EmailsDirectory
from ..form import (
    CheckboxWidget,
    CompositeWidget,
    EmailWidget,
    Form,
    HiddenWidget,
    IntWidget,
    PasswordEntryWidget,
    PasswordWidget,
    RadiobuttonsWidget,
    SingleSelectWidget,
    StringWidget,
)
from .base import AuthMethod, NoSuchMethodForUserError
from .password_accounts import HASHING_ALGOS, PasswordAccount


def notify_admins_user_registered(account):
    identities_cfg = get_cfg('identities', {})
    admins = [x for x in get_publisher().user_class.select([st.Equal('is_admin', True)])]
    if not admins:
        return
    admin_emails = [x.email for x in admins if x.email]

    user = get_publisher().user_class().get(account.user_id)
    data = {
        'hostname': get_request().get_server(),
        'username': account.id,
        'email_as_username': str(identities_cfg.get('email-as-username', False)),
        'name': user.get_display_name(),
        'email': user.email,
    }

    emails.custom_template_email(
        'new-registration-admin-notification', data, admin_emails, fire_and_forget=True
    )


def make_password(min_len=None, max_len=None):
    passwords_cfg = get_cfg('passwords', {})
    if min_len is None:
        min_len = passwords_cfg.get('min_length', 0)
    if max_len is None:
        max_len = passwords_cfg.get('max_length', 0)
    if min_len and max_len:
        length = (min_len + max_len) / 2
    elif min_len:
        length = min_len
    elif max_len:
        length = min(max_len, 6)
    else:
        length = 6
    length = int(length)
    r = random.SystemRandom()
    return ''.join(
        [r.choice('abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ2345678923456789') for x in range(length)]
    )


class TokenDirectory(Directory):
    _q_exports = ['']

    def __init__(self, token):
        self.token = token

    def _q_index(self):
        try:
            self.token.remove_self()
        except OSError:
            # race condition, and the token already got removed (??!)
            self.token.type = None

        r = TemplateIO(html=True)
        if self.token.type is None:
            get_response().set_title(_('Invalid Token'))
            r += TextsDirectory.get_html_text('invalid-password-token')

        elif self.token.type == 'account-confirmation':
            get_response().set_title(_('Account Creation Confirmed'))
            account = PasswordAccount.get(self.token.context['username'])
            account.awaiting_confirmation = False
            account.store()

            r += TextsDirectory.get_html_text('account-created')
            passwords_cfg = get_cfg('passwords', {})
            if passwords_cfg.get('can_change', False):
                # TODO: offer a chance to change password ?
                pass

            identities_cfg = get_cfg('identities', {})
            if identities_cfg.get('notify-on-register', False):
                notify_admins_user_registered(account)

        else:
            raise errors.TraversalError()

        return r.getvalue()


class TokensDirectory(Directory):
    def _q_lookup(self, component):
        try:
            token = get_publisher().token_class.get(component)
        except KeyError:
            raise errors.TraversalError()
        return TokenDirectory(token)


class MethodDirectory(Directory):
    _q_exports = ['login', 'register', 'tokens', 'forgotten']

    tokens = TokensDirectory()

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('password/', None))
        return Directory._q_traverse(self, path)

    def login(self):
        next_url = get_request().form.get('next')
        if get_request().get_method() == 'GET':
            get_request().form = {}
        identities_cfg = get_cfg('identities', {})
        form = Form(
            enctype='multipart/form-data', id='login-form', use_tokens=False, action=get_request().get_url()
        )
        form.add_hidden('next', next_url)
        if identities_cfg.get('email-as-username', False):
            form.add(StringWidget, 'username', title=_('Email'), size=25, required=True)
        else:
            form.add(StringWidget, 'username', title=_('Username'), size=25, required=True)
        form.add(PasswordWidget, 'password', title=_('Password'), size=25, required=True)
        form.add_submit('submit', _('Log in'))
        if form.is_submitted() and not form.has_errors():
            tmp = self.login_submit(form)
            if not form.has_errors():
                return tmp

        get_response().breadcrumb.append(('login', _('Login')))
        get_response().set_title(_('Login'))
        r = TemplateIO(html=True)
        r += htmltext('<div class="ident-content">')
        r += htmltext('<div id="login">')
        r += get_session().display_message()
        r += TextsDirectory.get_html_text('top-of-login')
        r += form.render()
        r += htmltext('</div>')

        if identities_cfg.get('creation') == 'self':
            r += htmltext('<div id="register">')
            ident_methods = get_cfg('identification', {}).get('methods', [])
            if len(ident_methods) > 1:
                register_url = get_publisher().get_root_url() + 'register/password/register'
            else:
                register_url = get_publisher().get_root_url() + 'register/'
            r += TextsDirectory.get_html_text(
                'password-account-link-to-register-page', {'register_url': register_url}
            )
            r += htmltext('</div>')

        forgotten_url = get_publisher().get_root_url() + 'ident/password/forgotten'
        r += htmltext('<div id="forgotten">')
        r += htmltext('<h3>%s</h3>') % _('Lost Password?')
        r += TextsDirectory.get_html_text('password-forgotten-link') % {'forgotten_url': forgotten_url}
        r += htmltext('</div>')
        r += htmltext('</div>')  # .ident-content

        r += htmltext(
            """<script type="text/javascript">
          document.getElementById('login-form')['username'].focus();
        </script>"""
        )
        return r.getvalue()

    def login_submit(self, form):
        username = form.get_widget('username').parse()
        password = form.get_widget('password').parse()

        try:
            user = PasswordAccount.get_with_credentials(username, password)
        except Exception:
            form.set_error('username', _('Invalid credentials'))
            return

        account = PasswordAccount.get(username)
        return self.login_submit_account_user(account, user, form)

    def login_submit_account_user(self, account, user, form=None):
        if account.awaiting_confirmation:
            if form:
                form.set_error('username', _('This account is waiting for confirmation'))
            return

        if account.disabled:
            if form:
                form.set_error('username', _('This account has been disabled'))
            return

        session = get_session()
        session.username = account.id
        session.set_user(user.id)

        if form and form.get_widget('next').parse():
            after_url = form.get_widget('next').parse()
            return redirect(after_url)

        return redirect(get_publisher().get_root_url() + get_publisher().after_login_url)

    def forgotten(self, include_mode=False):
        if 't' in get_request().form:
            return self.forgotten_token()

        identities_cfg = get_cfg('identities', {})

        if include_mode:
            base_url = get_publisher().get_root_url() + 'ident/password'
            form = Form(enctype='multipart/form-data', use_tokens=False, action='%s/forgotten' % base_url)
        else:
            form = Form(enctype='multipart/form-data', use_tokens=False)

        if identities_cfg.get('email-as-username', False):
            form.add(StringWidget, 'username', title=_('Email'), size=25, required=True)
        else:
            form.add(StringWidget, 'username', title=_('Username'), size=25, required=True)
        form.add_submit('change', _('Submit Request'))

        if include_mode:
            form.clear_errors()
        if not include_mode and form.is_submitted() and not form.has_errors():
            tmp = self.forgotten_submit(form)
            if not form.has_errors() and tmp:
                return tmp

        r = TemplateIO(html=True)
        if not include_mode:
            get_response().breadcrumb.append(('forgotten', _('Forgotten password')))
            get_response().set_title(_('Forgotten password'))
            r += htmltext('<div class="ident-content">')

        r += TextsDirectory.get_html_text('password-forgotten-enter-username')
        r += form.render()
        if not include_mode:
            r += htmltext('</div>')
        return r.getvalue()

    def forgotten_submit(self, form):
        username = form.get_widget('username').parse()

        try:
            account = PasswordAccount.get(username)
            user = account.user
        except KeyError:
            user = None

        if not user or user.email is None:
            form.set_error('username', _('There is no user with that name or it has no email contact.'))
            return None

        # changing password, process:
        #  - sending mail with a token (sth like http://.../forgotten?token=xxx)
        #  - enter your new password

        token = get_publisher().token_class(3 * 86400)
        token.type = 'password-change'
        token.context = {'username': username}
        token.store()

        data = {
            'change_url': get_request().get_frontoffice_url() + '?t=%s&a=cfmpw' % token.id,
            'cancel_url': get_request().get_frontoffice_url() + '?t=%s&a=cxlpw' % token.id,
            'token': token.id,
            'time': misc.localstrftime(token.expiration),
        }

        try:
            emails.custom_template_email('change-password-request', data, user.email)
        except errors.EmailError:
            form.set_error('username', _('Failed to send email (server error)'))
            token.remove_self()
            return None

        def forgotten_token_sent():
            get_response().set_title(_('Forgotten Password'))
            r = TemplateIO(html=True)
            r += htmltext('<div class="ident-content">')
            r += TextsDirectory.get_html_text('password-forgotten-token-sent')
            r += htmltext('</div>')
            return r.getvalue()

        return forgotten_token_sent()

    def forgotten_token(self):
        tokenv = get_request().form.get('t')
        action = get_request().form.get('a')

        try:
            token = get_publisher().token_class.get(tokenv)
        except KeyError:
            return template.error_page(
                _('The token you submitted does not exist, has expired, or has been cancelled.'),
            )

        if token.type != 'password-change':
            return template.error_page(
                _('The token you submitted is not appropriate for the requested task.'),
            )

        if action == 'cxlpw':
            get_response().set_title(_('Password Change'))
            r = TemplateIO(html=True)
            r += htmltext('<div class="ident-content">')
            r += htmltext('<h1>%s</h1>') % _('Request Cancelled')
            r += htmltext('<p>%s</p>') % _('Your request has been cancelled')
            r += htmltext('<p>')
            r += htmltext(_('Continue to <a href="/">home page</a></p>'))
            r += htmltext('</p>')
            r += htmltext('</div>')
            token.remove_self()
            return r.getvalue()

        passwords_cfg = get_cfg('passwords', {})
        if action == 'cfmpw' and passwords_cfg.get('can_change', False):
            form = Form(enctype='multipart/form-data', action='forgotten')
            form.add(HiddenWidget, 't', value=tokenv)
            form.add(HiddenWidget, 'a', value=action)
            form.add(
                PasswordEntryWidget,
                'new_password',
                title=_('New Password'),
                required=True,
                formats=['cleartext'],
                **get_cfg('passwords', {}),
            )
            form.add_submit('submit', _('Submit'))
            form.add_submit('cancel', _('Cancel'))

            if form.get_submit() == 'cancel':
                token.remove_self()
                return redirect('.')

            if form.is_submitted() and not form.has_errors():
                new_password = form.get_widget('new_password').parse().get('cleartext')

            if form.is_submitted() and not form.has_errors():
                account = PasswordAccount.get(token.context['username'])
                account.hashing_algo = passwords_cfg.get('hashing_algo', 'django')
                account.set_password(new_password)
                account.store()
                token.remove_self()
                user = PasswordAccount.get_with_credentials(account.id, new_password)
                tmp = self.login_submit_account_user(account, user)
                if tmp:
                    return tmp
                return redirect('login/')

            get_response().set_title(_('Password Change'))
            get_request().form = {}
            return form.render()

        if action == 'cfmpw' and not passwords_cfg.get('can_change', False):
            # generate a new password and send it by email
            new_password = make_password()
            try:
                account = PasswordAccount.get(token.context['username'])
                user = account.user
            except KeyError:
                user = None

            account.hashing_algo = passwords_cfg.get('hashing_algo', 'django')
            account.set_password(str(new_password))
            account.store()
            token.remove_self()

            if user and user.email:
                data = {
                    'username': str(account.id),
                    'password': str(new_password),
                    'hostname': get_request().get_server(),
                }

                emails.custom_template_email('new-generated-password', data, user.email)

                return self.forgotten_token_end_page()

            # XXX: user has no email, what to tell him ?
            return redirect('login/')

    def forgotten_token_end_page(self):
        r = TemplateIO(html=True)
        r += htmltext('<div class="ident-content">')
        r += str(_('New password sent by email'))
        r += htmltext('</div>')
        return r.getvalue()

    def register(self):
        identities_cfg = get_cfg('identities', {})
        if identities_cfg.get('creation', 'admin') == 'admin':
            raise errors.TraversalError()

        passwords_cfg = get_cfg('passwords', {})
        identities_cfg = get_cfg('identities', {})
        users_cfg = get_cfg('users', {})

        form = Form(enctype='multipart/form-data', use_tokens=False)

        formdef = None
        if hasattr(get_publisher().user_class, 'get_formdef'):
            formdef = get_publisher().user_class.get_formdef()
            if formdef:
                formdef.add_fields_to_form(form)

        if not identities_cfg.get('email-as-username', False):
            form.add(
                StringWidget,
                'username',
                title=_('Username'),
                size=25,
                required=True,
                hint=_('This will be your username to connect to this site.'),
            )
        else:
            if not users_cfg.get('field_email'):
                form.add(EmailWidget, 'username', title=_('Email'), size=25, required=True)

        r = TemplateIO(html=True)

        if not passwords_cfg.get('generate', True):
            form.add(
                PasswordEntryWidget,
                'password',
                title=_('Password'),
                size=25,
                required=True,
                formats=['cleartext'],
                **get_cfg('passwords', {}),
            )

        form.add_submit('submit', _('Create Account'))

        if form.is_submitted() and not form.has_errors():
            tmp = self.register_submit(form, formdef)
            if not form.has_errors():
                return tmp

        get_response().breadcrumb.append(('register', _('New Account')))
        get_response().set_title(_('New Account'))
        r += htmltext('<div class="ident-content">')
        r += TextsDirectory.get_html_text('new-account')
        r += form.render()
        r += htmltext('</div>')
        return r.getvalue()

    def register_submit(self, form, formdef):
        passwords_cfg = get_cfg('passwords', {})
        identities_cfg = get_cfg('identities', {})
        users_cfg = get_cfg('users', {})

        if not identities_cfg.get('email-as-username', False) or not users_cfg.get('field_email'):
            username = form.get_widget('username').parse()
            username_field_key = 'username'
        else:
            data = formdef.get_data(form)
            username = data.get(users_cfg.get('field_email'))
            username_field_key = 'f%s' % users_cfg.get('field_email')

        if PasswordAccount.has_key(username):
            if username_field_key == 'username':
                form.set_error(username_field_key, _('There is already a user with that username'))
            else:
                form.set_error(username_field_key, _('There is already a user with that email address'))

        if form.has_errors():
            return

        password = None
        if passwords_cfg.get('generate', True):
            password = make_password()
            # an email will be sent afterwards
        else:
            password = form.get_widget('password').parse().get('cleartext')

        user = get_publisher().user_class()
        user.name = username
        if formdef:
            data = formdef.get_data(form)
            if identities_cfg.get('email-as-username', False) and 'email' not in data:
                data['email'] = username
            user.set_attributes_from_formdata(data)
            user.form_data = data
        else:
            if identities_cfg.get('email-as-username', False):
                user.email = username

        if not (get_publisher().user_class.exists()):
            user.is_admin = True
        user.store()

        account = PasswordAccount(id=username)
        account.hashing_algo = passwords_cfg.get('hashing_algo', 'django')
        if password:
            account.set_password(password)
        account.user_id = user.id

        if identities_cfg.get('email-confirmation', False):
            if not user.email:
                get_publisher().record_error(
                    _(
                        'Accounts are configured to require confirmation but accounts can be created without emails'
                    )
                )
            else:
                account.awaiting_confirmation = True

        account.store()

        if account.awaiting_confirmation:
            return self.confirmation_notification(account, user, password)

        if identities_cfg.get('notify-on-register', False):
            notify_admins_user_registered(account)

        if passwords_cfg.get('generate', True):
            if not user.email:
                get_publisher().record_error(
                    _(
                        'Accounts are configured to have a generated password '
                        'but accounts can be created without emails'
                    )
                )
            else:
                data = {
                    'hostname': get_request().get_server(),
                    'email': user.email,
                    'email_as_username': str(identities_cfg.get('email-as-username', False)),
                    'username': account.id,
                    'password': password,
                }
                emails.custom_template_email(
                    'new-account-generated-password', data, user.email, fire_and_forget=True
                )

        # XXX: display a message instead of immediate redirect ?
        return redirect(get_publisher().get_root_url() + 'login/')

    def confirmation_notification(self, account, user, password):
        self.email_confirmation_notification(account, user, password)

        get_response().set_title(_('Email sent'))
        r = TemplateIO(html=True)
        r += htmltext('<div class="ident-content">')
        r += TextsDirectory.get_html_text('email-sent-confirm-creation')
        r += htmltext('</div>')
        return r.getvalue()

    def email_confirmation_notification(self, account, user, password):
        passwords_cfg = get_cfg('passwords', {})

        token = get_publisher().token_class(3 * 86400)
        token.type = 'account-confirmation'
        token.context = {'username': account.id}
        token.store()

        req = get_request()
        path = get_publisher().get_root_url() + 'ident/password/tokens/%s/' % token.id
        token_url = '%s://%s%s' % (req.get_scheme(), req.get_server(), path)

        data = {
            'email': user.email,
            'website': get_cfg('sitename'),
            'token_url': token_url,
            'token': token.id,
            'username': account.id,
            'password': password,
            'admin_email': passwords_cfg.get('admin_email', ''),
        }

        emails.custom_template_email('password-subscription-notification', data, user.email)


ADMIN_TITLE = _('Username / Password')


class MethodAdminDirectory(Directory):
    title = ADMIN_TITLE
    label = _('Configure username/password identification method')

    _q_exports = ['', 'passwords', 'identities']

    def _q_index(self):
        get_response().set_title(ADMIN_TITLE)
        get_response().breadcrumb.append(('password/', ADMIN_TITLE))
        r = TemplateIO(html=True)

        r += htmltext('<h2>%s</h2>') % ADMIN_TITLE

        r += get_session().display_message()

        r += htmltext('<dl>')
        r += htmltext('<dt><a href="identities">%s</a></dt> <dd>%s</dd>') % (
            _('Identities'),
            _('Configure identities creation'),
        )
        r += htmltext('<dt><a href="passwords">%s</a></dt> <dd>%s</dd>') % (
            _('Passwords'),
            _('Configure all password things'),
        )
        r += htmltext('</dl>')
        return r.getvalue()

    def passwords(self):
        form = Form(enctype='multipart/form-data')
        passwords_cfg = get_cfg('passwords', {})
        form.add(
            CheckboxWidget,
            'can_change',
            title=_('Users can change their password'),
            value=passwords_cfg.get('can_change', False),
        )
        form.add(
            CheckboxWidget,
            'generate',
            title=_('Generate initial password'),
            value=passwords_cfg.get('generate', True),
        )
        form.add(
            IntWidget,
            'min_length',
            title=_('Minimum password length'),
            value=int(passwords_cfg.get('min_length', 0)),
        )
        form.add(
            IntWidget,
            'max_length',
            title=_('Maximum password length'),
            value=int(passwords_cfg.get('max_length', 0)),
            hint=_('0 for unlimited length'),
        )
        form.add(
            IntWidget,
            'count_uppercase',
            title=_('Minimum number of uppercase characters'),
            value=int(passwords_cfg.get('count_uppercase', 0)),
        )
        form.add(
            IntWidget,
            'count_lowercase',
            title=_('Minimum number of lowercase characters'),
            value=int(passwords_cfg.get('count_lowercase', 0)),
        )
        form.add(
            IntWidget,
            'count_digit',
            title=_('Minimum number of digits'),
            value=int(passwords_cfg.get('count_digit', 0)),
        )
        form.add(
            IntWidget,
            'count_special',
            title=_('Minimum number of special characters'),
            value=int(passwords_cfg.get('count_special', 0)),
        )
        form.add(
            EmailWidget,
            'admin_email',
            title=_('Email address (for questions...)'),
            value=passwords_cfg.get('admin_email'),
        )
        hashing_options = [(None, _('None'))]
        for key in sorted(HASHING_ALGOS.keys()):
            hashing_options.append((key, key.upper()))
        form.add(
            SingleSelectWidget,
            'hashing_algo',
            title=_('Password Hashing Algorithm'),
            value=passwords_cfg.get('hashing_algo', 'django'),
            options=hashing_options,
        )

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            self.passwords_submit(form)
            return redirect('.')

        get_response().set_title(_('Passwords'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Passwords')
        r += form.render()
        return r.getvalue()

    def passwords_submit(self, form):
        from wcs.admin.settings import cfg_submit

        cfg_submit(
            form,
            'passwords',
            (
                'can_change',
                'generate',
                'min_length',
                'max_length',
                'count_uppercase',
                'count_lowercase',
                'count_digit',
                'count_special',
                'admin_email',
                'hashing_algo',
            ),
        )

    def identities(self):
        form = Form(enctype='multipart/form-data')
        identities_cfg = get_cfg('identities', {})
        form.add(
            SingleSelectWidget,
            'creation',
            title=_('Identity Creation'),
            value=identities_cfg.get('creation', 'admin'),
            options=[
                ('admin', _('Administrator accounts')),
                ('self', _('Self-registration')),
            ],
        )
        form.add(
            CheckboxWidget,
            'email-confirmation',
            title=_('Require email confirmation for new accounts'),
            value=identities_cfg.get('email-confirmation', False),
        )
        form.add(
            CheckboxWidget,
            'notify-on-register',
            title=_('Notify Administrators on Registration'),
            value=identities_cfg.get('notify-on-register', False),
        )
        form.add(
            CheckboxWidget,
            'email-as-username',
            title=_('Use email as username'),
            value=identities_cfg.get('email-as-username', False),
        )

        if identities_cfg.get('locked') is None:
            form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            self.identities_submit(form)
            return redirect('.')

        get_response().set_title(_('Identities Interface'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Identities Interface')
        r += form.render()
        return r.getvalue()

    def identities_submit(self, form):
        from wcs.admin.settings import cfg_submit

        cfg_submit(
            form,
            'identities',
            (
                'creation',
                'email-as-username',
                'notify-on-register',
                'email-confirmation',
            ),
        )


class UsernamePasswordWidget(CompositeWidget):
    def __init__(self, name, value=None, **kwargs):
        CompositeWidget.__init__(self, name, value, **kwargs)
        if not value:
            value = {}

        self.add(
            StringWidget,
            'username',
            value.get('username'),
            title=_('Username'),
            required=kwargs.get('required'),
        )

        if value.get('password'):
            kwargs['required'] = False
        self.add(
            PasswordWidget,
            'password',
            title=_('Password'),
            required=kwargs.get('required'),
            autocomplete='off',
        )
        self.add(
            CheckboxWidget,
            'awaiting_confirmation',
            value.get('awaiting_confirmation'),
            title=_('Awaiting Confirmation'),
            required=False,
        )
        self.add(
            CheckboxWidget, 'disabled', value.get('disabled'), title=_('Disabled Account'), required=False
        )

    def _parse(self, request):
        value = {
            'username': self.get('username'),
            'password': self.get('password'),
            'awaiting_confirmation': self.get('awaiting_confirmation'),
            'disabled': self.get('disabled'),
        }
        self.value = value or None


class MethodUserDirectory(Directory):
    _q_exports = ['email']

    def __init__(self, user):
        self.user = user
        try:
            self.account = PasswordAccount.get_by_user_id(user.id)
        except KeyError:
            raise NoSuchMethodForUserError()

    def get_actions(self):
        actions = []
        if self.account.hashing_algo:
            actions.append(('email', _('Send new password by email')))
        else:
            actions.append(('email', _('Send password by email')))

        return actions

    def email(self):
        get_response().set_title(title=ADMIN_TITLE)
        r = TemplateIO(html=True)
        get_response().breadcrumb.append(('email', 'Email Password'))
        r += htmltext('<h2>%s</h2>') % _('Email Password')
        form = Form(enctype='multipart/form-data')
        options = [('create-anew', _('Generate new password'))]
        if not self.account.hashing_algo:
            options.append(('current', _('Use current password')))
        # TODO: option to send a mail with a token url, asking user to enter a
        # new password

        form.add(RadiobuttonsWidget, 'method', options=options, delim='<br/>', required=True)

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_submit() == 'cancel':
            return redirect('..')

        if form.is_submitted() and not form.has_errors():
            self.email_submit(form)
            return redirect('..')

        r += form.render()
        return r.getvalue()

    def email_submit(self, form):
        method = form.get_widget('method').parse()
        email_key = 'password-email-%s' % method
        if method == 'create-anew':
            password = make_password()
            self.account.set_password(password)
            self.account.store()
        else:
            password = self.account.password

        data = {
            'hostname': get_request().get_server(),
            'name': self.user.get_display_name(),
            'username': self.account.id,
            'password': password,
        }

        emails.custom_template_email(email_key, data, self.user.email)


class PasswordAuthMethod(AuthMethod):
    key = 'password'
    description = _('Username / password')
    method_directory = MethodDirectory
    method_admin_widget = UsernamePasswordWidget
    method_admin_directory = MethodAdminDirectory
    method_user_directory = MethodUserDirectory

    def submit(self, user, widget):
        passwords_cfg = get_cfg('passwords', {})
        value = widget.parse()
        username = value.get('username')
        if not username:
            return
        if PasswordAccount.has_key(username):
            account = PasswordAccount.get(username)
            if account.user_id != user.id:
                widget.value = None
                widget.set_widget_error('username', _('Duplicate user name'))
                return
        else:
            account = PasswordAccount(id=value.get('username'))
        if value.get('password'):
            account.hashing_algo = passwords_cfg.get('hashing_algo')
            account.set_password(value.get('password'))
        account.awaiting_confirmation = value.get('awaiting_confirmation')
        account.disabled = value.get('disabled')
        account.user_id = user.id
        try:
            old_account = PasswordAccount.get_by_user_id(user.id)
        except KeyError:
            pass
        else:
            if old_account.id != account.id:
                old_account.remove_self()
        account.store()

    def delete(self, user):
        try:
            old_account = PasswordAccount.get_by_user_id(user.id)
            old_account.remove_self()
        except KeyError:
            pass

    def get_value(self, user):
        if not user or not user.id:
            return None
        try:
            account = PasswordAccount.get_by_user_id(user.id)
        except KeyError:
            return None
        return {
            'username': account.id,
            'password': account.password,
            'awaiting_confirmation': account.awaiting_confirmation,
            'disabled': account.disabled,
        }


def is_password_enabled():
    ident_methods = get_cfg('identification', {}).get('methods', []) or []
    return 'password' in ident_methods


EmailsDirectory.register(
    'password-subscription-notification',
    _('Subscription notification for password account'),
    _('Available variables: email, website, token_url, token, admin_email, username, password'),
    category=_('Identification'),
    condition=is_password_enabled,
    default_subject=_('Subscription Confirmation'),
    default_body=_(
        '''\
We have received a request for subscription of your email address,
"{{email}}", to the {{website}} web site.

To confirm that you want to be subscribed to the web site, simply
visit this web page:

{{token_url}}

If you do not wish to be subscribed to the web site, pleasy simply
disregard this message.  If you think you are being maliciously
subscribed to the web site, or have any other questions, send them
to {{admin_email}}.
'''
    ),
)

EmailsDirectory.register(
    'change-password-request',
    _('Request for password change'),
    _('Available variables: change_url, cancel_url, token, time'),
    category=_('Identification'),
    condition=is_password_enabled,
    default_subject=_('Change Password Request'),
    default_body=_(
        """\
You have (or someone impersonating you has) requested to change your
password. To complete the change, visit the following link:

{{change_url}}

If you are not the person who made this request, or you wish to cancel
this request, visit the following link:

{{cancel_url}}

If you do nothing, the request will lapse after 3 days (precisely on
{{time}}).
"""
    ),
)


EmailsDirectory.register(
    'new-generated-password',
    _('New generated password'),
    _('Available variables: username, password, hostname'),
    category=_('Identification'),
    condition=is_password_enabled,
    default_subject=_('Your new password'),
    default_body=_(
        '''\
Hello,

You have requested a new password for {{hostname}}, here are your new
account details:

- username: {{username}}
- password: {{password}}
'''
    ),
)


EmailsDirectory.register(
    'new-account-approved',
    _('Approval of new account'),
    _('Available variables: username, password'),
    category=_('Identification'),
    condition=is_password_enabled,
    default_subject=_('Your account has been approved'),
    default_body=_(
        '''\
Your account has been approved.

Account details:

- username: {{username}}
{% if password %}- password: {{password}}{% endif %}
'''
    ),
)

EmailsDirectory.register(
    'new-registration-admin-notification',
    _('Notification of new registration to administrators'),
    _('Available variables: hostname, email_as_username, username'),
    category=_('Identification'),
    condition=is_password_enabled,
    default_subject=_('New Registration'),
    default_body=_(
        '''\
Hello,

A new account has been created on {{hostname}}.

 - name: {{name}}
 - username: {{username}}
'''
    ),
)

EmailsDirectory.register(
    'new-account-generated-password',
    _('Welcome email, with generated password'),
    _('Available variables: hostname, username, password, email_as_username'),
    category=_('Identification'),
    condition=is_password_enabled,
    default_subject=_('Welcome to {{hostname}}'),
    default_body=_(
        '''\
Welcome to {{hostname}},

Your password is: {{password}}
'''
    ),
)

EmailsDirectory.register(
    'password-email-create-anew',
    _('Email with a new password for the user'),
    _('Available variables: hostname, name, username, password'),
    category=_('Identification'),
    condition=is_password_enabled,
    default_subject=_('Your new password for {{hostname}}'),
    default_body=_(
        '''\
Hello {{name}},

Here is your new password for {{hostname}}: {{password}}
'''
    ),
)

EmailsDirectory.register(
    'password-email-current',
    _('Email with current password for the user'),
    _('Available variables: hostname, name, username, password'),
    category=_('Identification'),
    condition=is_password_enabled,
    default_subject=_('Your password for {{hostname}}'),
    default_body=_(
        '''\
Hello {{name}},

Here is your password for {{hostname}}: {{password}}
'''
    ),
)


TextsDirectory.register(
    'account-created',
    _('Text when account confirmed by user'),
    category=_('Identification'),
    condition=is_password_enabled,
    default=_(
        '''<p>
Your account has been created.
</p>'''
    ),
)

TextsDirectory.register(
    'password-forgotten-token-sent',
    _('Text when an email with a change password token has been sent'),
    category=_('Identification'),
    condition=is_password_enabled,
    default=_(
        '''<p>
A token for changing your password has been emailed to you. Follow the instructions in that email to change your password.
</p>
<p>
 <a href="login">Log In</a>
</p>'''
    ),
)

TextsDirectory.register(
    'new-password-sent-by-email',
    _('Text when new password has been sent'),
    category=_('Identification'),
    condition=is_password_enabled,
    default=_(
        '''<p>
Your new password has been sent to you by email.
</p>
<p>
 <a href="login">Login</a>
</p>'''
    ),
)

TextsDirectory.register(
    'new-account',
    _('Text on top of registration form'),
    category=_('Identification'),
    condition=is_password_enabled,
)

TextsDirectory.register(
    'password-forgotten-link',
    _('Text on login page, linking to the forgotten password request page'),
    category=_('Identification'),
    condition=is_password_enabled,
    default=_(
        '''<p>
If you have an account, but have forgotten your password, you should go
to the <a href="%(forgotten_url)s">Lost password page</a> and submit a request
to change your password.
</p>'''
    ),
)

TextsDirectory.register(
    'password-forgotten-enter-username',
    _('Text on forgotten password request page'),
    category=_('Identification'),
    condition=is_password_enabled,
    default=_(
        '''<p>
If you have an account, but have forgotten your password, enter your user name
below and submit a request to change your password.
</p>'''
    ),
)

TextsDirectory.register(
    'password-account-link-to-register-page',
    _('Text linking the login page to the account creation page'),
    hint=_('Available variable: register_url'),
    category=_('Identification'),
    condition=is_password_enabled,
    default=_(
        '''<p>
If you do not have an account, you should go to the <a href="{{register_url}}">
New Account page</a>.
</p>'''
    ),
)

TextsDirectory.register(
    'invalid-password-token',
    _('Text when an invalid password token is used'),
    category=_('Identification'),
    condition=is_password_enabled,
    default=_(
        '''<p>
Sorry, the token you used is invalid, or has already been used.
</p>'''
    ),
)

TextsDirectory.register(
    'top-of-login',
    _('Text on top of the login page'),
    category=_('Identification'),
    condition=is_password_enabled,
)

TextsDirectory.register(
    'email-sent-confirm-creation',
    _('Text when a mail for confirmation of an account creation has been sent'),
    category=_('Identification'),
    condition=is_password_enabled,
    default=_('An email has been sent to you so you can confirm your account creation.'),
)

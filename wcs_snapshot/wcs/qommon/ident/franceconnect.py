# w.c.s. - web application for online forms
# Copyright (C) 2005-2017  Entr'ouvert
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

import base64
import hashlib
import json
import urllib.parse
import uuid

from django.utils.encoding import force_bytes
from quixote import get_publisher, get_request, get_response, get_session, get_session_manager, redirect
from quixote.directory import Directory
from quixote.errors import QueryError
from quixote.html import TemplateIO, htmltext

from wcs.formdata import flatten_dict
from wcs.workflows import WorkflowStatusItem

from .. import _, get_cfg, template
from ..form import (
    CompositeWidget,
    ComputedExpressionWidget,
    Form,
    SingleSelectWidget,
    StringWidget,
    WidgetListAsTable,
)
from ..misc import http_get_page, http_post_request
from .base import AuthMethod

ADMIN_TITLE = _('FranceConnect')

# XXX: make an OIDC auth method that FranceConnect would inherit from


def base64url_decode(input):
    rem = len(input) % 4
    if rem > 0:
        input += b'=' * (4 - rem)
    return base64.urlsafe_b64decode(input)


class UserFieldMappingRowWidget(CompositeWidget):
    def __init__(self, name, value=None, **kwargs):
        CompositeWidget.__init__(self, name, value, **kwargs)
        if not value:
            value = {}

        fields = []
        users_cfg = get_cfg('users', {})
        user_formdef = get_publisher().user_class.get_formdef()
        if not user_formdef or not get_publisher().has_user_fullname_config():
            fields.append(('__name', _('Name'), '__name'))
        if not user_formdef or not users_cfg.get('field_email'):
            fields.append(('__email', _('Email'), '__email'))
        if user_formdef and user_formdef.fields:
            for field in user_formdef.fields:
                if field.varname:
                    fields.append((field.varname, field.label, field.varname))

        self.add(
            SingleSelectWidget,
            name='field_varname',
            title=_('Field'),
            value=value.get('field_varname'),
            options=fields,
            **kwargs,
        )
        self.add(ComputedExpressionWidget, name='value', title=_('Value'), value=value.get('value'))
        self.add(
            SingleSelectWidget,
            'verified',
            title=_('Is attribute verified'),
            value=value.get('verified'),
            options=[('never', _('Never')), ('always', _('Always'))],
        )

    def _parse(self, request):
        if self.get('value') and self.get('field_varname') and self.get('verified'):
            self.value = {
                'value': self.get('value'),
                'field_varname': self.get('field_varname'),
                'verified': self.get('verified'),
            }
        else:
            self.value = None


class UserFieldMappingTableWidget(WidgetListAsTable):
    readonly = False

    def __init__(self, name, **kwargs):
        super().__init__(name, element_type=UserFieldMappingRowWidget, **kwargs)


class MethodDirectory(Directory):
    _q_exports = ['login', 'logout', 'callback']

    def login(self):
        return FCAuthMethod().login()

    def logout(self):
        return FCAuthMethod().logout()

    def callback(self):
        return FCAuthMethod().callback()


class MethodAdminDirectory(Directory):
    title = ADMIN_TITLE
    label = _('Configure FranceConnect identification method')

    _q_exports = ['']

    PLATFORMS = [
        {
            'name': _('Development citizens'),
            'slug': 'dev-particulier',
            'authorization_url': 'https://fcp.integ01.dev-franceconnect.fr/api/v1/authorize',
            'token_url': 'https://fcp.integ01.dev-franceconnect.fr/api/v1/token',
            'user_info_url': 'https://fcp.integ01.dev-franceconnect.fr/api/v1/userinfo',
            'logout_url': 'https://fcp.integ01.dev-franceconnect.fr/api/v1/logout',
        },
        {
            'name': _('Development enterprise'),
            'slug': 'dev-entreprise',
            'authorization_url': 'https://fce.integ01.dev-franceconnect.fr/api/v1/authorize',
            'token_url': 'https://fce.integ01.dev-franceconnect.fr/api/v1/token',
            'user_info_url': 'https://fce.integ01.dev-franceconnect.fr/api/v1/userinfo',
            'logout_url': 'https://fce.integ01.dev-franceconnect.fr/api/v1/logout',
        },
        {
            'name': _('Production citizens'),
            'slug': 'prod-particulier',
            'authorization_url': 'https://app.franceconnect.gouv.fr/api/v1/authorize',
            'token_url': 'https://app.franceconnect.gouv.fr/api/v1/token',
            'user_info_url': 'https://app.franceconnect.gouv.fr/api/v1/userinfo',
            'logout_url': 'https://app.franceconnect.gouv.fr/api/v1/logout',
        },
    ]

    CONFIG = [
        ('client_id', _('Client ID')),
        ('client_secret', _('Client secret')),
        ('platform', _('Platform')),
        ('scopes', _('Scopes')),
        ('user_field_mappings', _('User field mappings')),
    ]

    KNOWN_ATTRIBUTES = [
        ('given_name', _('first names separated by spaces')),
        ('family_name', _('birth\'s last name')),
        ('birthdate', _('birthdate formatted as YYYY-MM-DD')),
        ('gender', _('gender \'male\' for men, and \'female\' for women')),
        ('birthplace', _('INSEE code of the place of birth')),
        ('birthcountry', _('INSEE code of the country of birth')),
        ('email', _('email')),
        ('siret', _('SIRET or SIREN number of the enterprise')),
        # Note: FranceConnect website also refer to adress and phones attributes
        # but we don't know what must be expected of their value.
    ]

    @classmethod
    def get_form(cls, instance=None):
        instance = instance or {}
        form = Form(enctype='multipart/form-data')
        for key, title in cls.CONFIG:
            attrs = {}
            default = None
            hint = None
            kwargs = {}
            widget = StringWidget

            if key == 'user_field_mappings':
                widget = UserFieldMappingTableWidget
            elif key == 'platform':
                widget = SingleSelectWidget
                kwargs['options'] = [(platform['slug'], platform['name']) for platform in cls.PLATFORMS]
            elif key == 'scopes':
                default = 'identite_pivot address email phones'
                hint = _(
                    'Space separated values among: identite_pivot, address, email, phones, '
                    'profile, birth, preferred_username, gender, birthdate, '
                    'birthcountry, birthplace'
                )
            if widget == StringWidget:
                kwargs['size'] = '80'
            form.add(
                widget,
                key,
                title=title,
                hint=hint,
                required=True,
                value=instance.get(key, default),
                attrs=attrs,
                **kwargs,
            )
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        return form

    def submit(self, form):
        cfg = {}
        for key, dummy in self.CONFIG:
            cfg[key] = form.get_widget(key).parse()
        get_publisher().cfg['fc'] = cfg
        get_publisher().write_cfg()
        return redirect('../..')

    def _q_index(self):
        fc_cfg = get_cfg('fc', {})
        form = self.get_form(fc_cfg)
        pub = get_publisher()

        if form.get_submit() == 'cancel':
            return redirect('../..')

        if 'submit' in get_request().form and form.is_submitted() and not form.has_errors():
            return self.submit(form)

        get_response().set_title(self.title)
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % self.title
        fc_callback = pub.get_frontoffice_url() + '/ident/fc/callback'
        r += htmltext('<p>')
        r += str(_('Callback URL is %s.') % fc_callback)
        r += htmltext('</p>')
        r += htmltext('<p>')
        r += str(_('Logout callback URL is %s.') % get_publisher().get_frontoffice_url())
        r += htmltext('</p>')
        r += htmltext('<p>')
        r += htmltext(
            _(
                'See <a href="https://partenaires.franceconnect.gouv.fr/fcp/fournisseur-service">'
                'FranceConnect partners\'site</a> for getting a client_id and '
                'a client_secret.'
            )
        )
        r += htmltext('</p>')
        r += form.render()
        r += htmltext('<div><p>')
        r += htmltext(
            _(
                'See <a '
                'href="https://partenaires.franceconnect.gouv.fr/fcp/fournisseur-service#identite-pivot" '
                '>FranceConnect partners\'site</a> for more '
                'informations on available scopes and attributes. Known ones '
                'are:'
            )
        )
        r += htmltext('</p>')
        r += htmltext(
            '<table class="franceconnect-attrs"><thead><tr><th>%s</th><th>%s</th></tr></thead><tbody>'
        ) % (_('Attribute'), _('Description'))
        for attribute, description in self.KNOWN_ATTRIBUTES:
            r += htmltext('<tr><td><code>%s</code></td><td>%s</td></tr>') % (attribute, description)
        r += htmltext('</tbody></table></div>')

        return r.getvalue()


class FCAuthMethod(AuthMethod):
    key = 'fc'
    description = ADMIN_TITLE
    method_directory = MethodDirectory
    method_admin_directory = MethodAdminDirectory

    def is_ok(self):
        fc_cfg = get_cfg('fc', {})
        for key, dummy in self.method_admin_directory.CONFIG:
            if not fc_cfg.get(key):
                return False
        return True

    def login(self):
        if not self.is_ok():
            return template.error_page(_('FranceConnect support is not yet configured.'))

        fc_cfg = get_cfg('fc', {})
        pub = get_publisher()
        session = get_session()

        authorization_url = self.get_authorization_url()
        client_id = fc_cfg.get('client_id')
        state = str(uuid.uuid4())
        session = get_session()
        next_url = get_request().form.get('next') or pub.get_frontoffice_url()
        session.extra_user_variables = session.extra_user_variables or {}
        session.extra_user_variables['fc_next_url_' + state] = next_url

        # generate a session id if none exists, ugly but necessary
        get_session_manager().maintain_session(session)

        nonce = hashlib.sha256(force_bytes(session.id)).hexdigest()
        fc_callback = pub.get_frontoffice_url() + '/ident/fc/callback'
        qs = urllib.parse.urlencode(
            {
                'response_type': 'code',
                'client_id': client_id,
                'redirect_uri': fc_callback,
                'scope': 'openid ' + fc_cfg.get('scopes', ''),
                'state': state,
                'nonce': nonce,
            }
        )
        redirect_url = '%s?%s' % (authorization_url, qs)
        return redirect(redirect_url)

    def get_access_token(self, code):
        publisher = get_publisher()
        session = get_session()
        fc_cfg = get_cfg('fc', {})
        client_id = fc_cfg.get('client_id')
        client_secret = fc_cfg.get('client_secret')
        redirect_uri = get_request().get_frontoffice_url().split('?')[0]
        body = {
            'grant_type': 'authorization_code',
            'redirect_uri': redirect_uri,
            'client_id': client_id,
            'client_secret': client_secret,
            'code': code,
        }
        dummy, status, data, dummy = http_post_request(
            self.get_token_url(),
            urllib.parse.urlencode(body),
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
            },
        )
        if status != 200:
            publisher.record_error(_('Status from FranceConnect token_url is not 200'))
            return None
        result = json.loads(data)
        if 'error' in result:
            publisher.record_error(_('FranceConnect code resolution failed: %s') % result['error'])
            return None
        # check id_token nonce
        id_token = result['id_token']
        access_token = result['access_token']
        payload = id_token.split('.')[1]
        payload = json.loads(base64url_decode(force_bytes(payload)))
        nonce = hashlib.sha256(force_bytes(session.id)).hexdigest()
        if payload['nonce'] != nonce:
            publisher.record_error(_('FranceConnect returned nonce did not match'))
            return None
        return access_token, id_token

    def get_user_info(self, access_token):
        publisher = get_publisher()
        dummy, status, data, dummy = http_get_page(
            self.get_user_info_url(),
            headers={
                'Authorization': 'Bearer %s' % access_token,
            },
        )
        if status != 200:
            publisher.record_error(
                _('Status from FranceConnect user_info_url is not 200 but %(status)s and data is %(data)s')
                % {'status': status, 'data': data[:100]}
            )
            return None
        return json.loads(data)

    def get_platform(self):
        fc_cfg = get_cfg('fc', {})
        slug = fc_cfg.get('platform')
        for platform in self.method_admin_directory.PLATFORMS:
            if platform['slug'] == slug:
                return platform
        raise KeyError('platform %s not found' % slug)

    def get_authorization_url(self):
        return self.get_platform()['authorization_url']

    def get_token_url(self):
        return self.get_platform()['token_url']

    def get_user_info_url(self):
        return self.get_platform()['user_info_url']

    def get_logout_url(self):
        return self.get_platform()['logout_url']

    def fill_user_attributes(self, user, user_info):
        fc_cfg = get_cfg('fc', {})
        user_field_mappings = fc_cfg.get('user_field_mappings', [])
        user_formdef = get_publisher().user_class.get_formdef()

        form_data = user.form_data or {}
        user.verified_fields = user.verified_fields or []

        for user_field_mapping in user_field_mappings:
            field_varname = user_field_mapping['field_varname']
            value = user_field_mapping['value']
            verified = user_field_mapping['verified']
            field_id = None

            try:
                value = WorkflowStatusItem.compute(value, context=user_info)
            except Exception as e:
                get_publisher().record_error(exception=e, context='FranceConnect', notify=True)
                continue
            if field_varname == '__name':
                user.name = value
            elif field_varname == '__email':
                user.email = value
                field_id = 'email'  # special value for verified email field
            else:
                for field in user_formdef.fields:
                    if field_varname == field.varname:
                        field_id = str(field.id)
                        break
                else:
                    continue
                form_data[field.id] = value
            # Update verified fields
            if field_id:
                if verified == 'always' and field_id not in user.verified_fields:
                    user.verified_fields.append(field_id)
                elif verified != 'always' and field_id in user.verified_fields:
                    user.verified_fields.remove(field_id)

        user.form_data = form_data

        if user.form_data:
            user.set_attributes_from_formdata(user.form_data)

    AUTHORIZATION_REQUEST_ERRORS = {
        'access_denied': _('user did not authorize login'),
    }

    def callback(self):
        if not self.is_ok():
            return template.error_page(_('FranceConnect support is not yet configured.'))
        pub = get_publisher()
        request = get_request()
        session = get_session()
        state = request.form.get('state', '')
        next_url = (session.extra_user_variables or {}).pop(
            'fc_next_url_' + state, ''
        ) or pub.get_frontoffice_url()

        if 'code' not in request.form:
            error = request.form.get('error')
            # if no error parameter, we stay silent
            if error:
                # we log only errors whose user is not responsible
                msg = self.AUTHORIZATION_REQUEST_ERRORS.get(error)
                pub.record_error(_('FranceConnect authentication failed: %s') % msg if msg else error)
            return redirect(next_url)
        access_token, id_token = self.get_access_token(request.form['code'])
        if not access_token:
            return redirect(next_url)
        user_info = self.get_user_info(access_token)
        if not user_info:
            return redirect(next_url)
        # Store user info in session
        flattened_user_info = user_info.copy()
        flatten_dict(flattened_user_info)
        session_var_fc_user = {}
        for key in flattened_user_info:
            session_var_fc_user['fc_' + key] = flattened_user_info[key]
        session_var_fc_user['fc_access_token'] = access_token
        session_var_fc_user['fc_id_token'] = id_token
        #  Lookup or create user
        sub = user_info['sub']
        user = None
        for user in pub.user_class.get_users_with_name_identifier(sub):
            break
        if not user:
            user = pub.user_class(sub)
            user.name_identifiers = [sub]

        self.fill_user_attributes(user, user_info)

        if not (user.name and user.email):
            # we didn't get useful attributes, forget it.
            pub.record_error(_('Failed to get name and/or email attribute from FranceConnect'))
            return redirect(next_url)

        user.store()
        session.set_user(user.id)
        session.extra_user_variables = session_var_fc_user
        return redirect(next_url)

    def logout(self):
        session = get_session()
        if (
            not session
            or not session.extra_user_variables
            or not session.extra_user_variables.get('fc_id_token')
        ):
            raise QueryError()
        id_token = session.extra_user_variables['fc_id_token']
        get_session_manager().expire_session()
        logout_url = self.get_logout_url()
        post_logout_redirect_uri = get_publisher().get_frontoffice_url()
        logout_url += '?' + urllib.parse.urlencode(
            {
                'id_token_hint': id_token,
                'post_logout_redirect_uri': post_logout_redirect_uri,
            }
        )
        return redirect(logout_url)

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

import json
import os
import re
from importlib import import_module

from quixote import get_publisher, get_request, get_response, get_session, get_session_manager, redirect
from quixote.directory import Directory
from quixote.util import StaticDirectory

from . import portfolio
from .api import ApiDirectory
from .categories import Category
from .formdef import FormDef
from .forms import root
from .forms.actions import ActionsDirectory
from .forms.preview import PreviewDirectory
from .qommon import _, errors, get_cfg, ident, misc, saml2, template
from .qommon.afterjobs import AfterJobStatusDirectory
from .qommon.myspace import MyspaceDirectory
from .qommon.upload_storage import UploadStorageError, get_storage_object


class CompatibilityDirectory(Directory):
    _q_exports = ['']

    def _q_index(self):
        return redirect('..')


class IdentDirectory(Directory):
    def _q_lookup(self, component):
        get_response().breadcrumb.append(('ident/', None))
        try:
            return ident.get_method_directory(component)
        except KeyError:
            raise errors.TraversalError()


class LoginDirectory(Directory):
    _q_exports = ['']

    def _q_index(self):
        ident_methods = get_cfg('identification', {}).get('methods', [])

        if get_request().form.get('ReturnUrl'):
            get_request().form['next'] = get_request().form.pop('ReturnUrl')

        if len(ident_methods) == 0:
            idps = get_cfg('idp', {})
            if len(idps) == 0:
                return template.error_page(_('Authentication subsystem is not yet configured.'))
            ident_methods = ['idp']  # fallback to old behaviour; saml.

        if 'IsPassive' in get_request().form and 'idp' in ident_methods:
            # if isPassive is given in query parameters, we restrict ourselves
            # to saml login.
            ident_methods = ['idp']

        # always prefer idp (saml), fallback to first configured method
        method = 'idp' if 'idp' in ident_methods else ident_methods[0]
        return ident.login(method)

    def _q_lookup(self, component):
        try:
            dir = ident.get_method_directory(component)
            # set the register page as the index page, so the url can be
            # /login/password/ instead of /login/password/login
            dir._q_exports.append('')
            dir._q_index = dir.login
            return dir
        except KeyError:
            return errors.TraversalError()


class RegisterDirectory(Directory):
    _q_exports = ['']

    def _q_index(self):
        ident_methods = get_cfg('identification', {}).get('methods', [])

        if len(ident_methods) == 0:
            idps = get_cfg('idp', {})
            if len(idps) == 0:
                return template.error_page(_('Authentication subsystem is not yet configured.'))
            ident_methods = ['idp']  # fallback to old behaviour; saml.

        # always prefer idp (saml), fallback to first configured method
        method = 'idp' if 'idp' in ident_methods else ident_methods[0]
        return ident.register(method)

    def _q_lookup(self, component):
        try:
            dir = ident.get_method_directory(component)
            if 'register' not in dir._q_exports:
                return errors.TraversalError()
            # set the register page as the index page, so the url can be
            # /register/password/ instead of /register/password/register
            dir._q_exports.append('')
            dir._q_index = dir.register
            return dir
        except KeyError:
            return errors.TraversalError()


class StaticsDirectory(Directory):
    static_directories = {
        '': ['web', 'qommon', 'django:gadjo', 'django:ckeditor'],
        'xstatic': [
            'xstatic:jquery',
            'xstatic:jquery_ui',
            'xstatic:font_awesome',
            'xstatic:godo',
            'xstatic:opensans',
            'xstatic:leaflet',
            'xstatic:leaflet_gesturehandling',
            'xstatic:mapbox_gl_leaflet',
            'xstatic:select2',
        ],
    }

    @classmethod
    def resolve_static_directories(cls, prefix):
        directories = cls.static_directories[prefix]
        for directory in directories:
            if directory[0] == '/':
                yield directory
            elif ':' not in directory:
                yield os.path.join(get_publisher().data_dir, directory)
            else:
                directory_type, value = directory.split(':')
                try:
                    if directory_type == 'xstatic':
                        module = import_module('xstatic.pkg.%s' % value)
                        yield module.BASE_DIR
                    elif directory_type == 'django':
                        module = import_module(value)
                        yield os.path.join(os.path.dirname(module.__file__), 'static')
                except ImportError:
                    pass

    def _q_traverse(self, path):
        # noqa pylint: disable=consider-iterating-dictionary
        if path[0] in self.static_directories.keys():
            prefix, rest = path[0], path[1:]
        else:
            prefix, rest = '', path

        if not rest:
            raise errors.AccessForbiddenError()

        for directory in self.resolve_static_directories(prefix):
            try:
                return StaticDirectory(directory, follow_symlinks=True)._q_traverse(rest)
            except errors.TraversalError:
                continue
        raise errors.TraversalError()


class TinyRedirectDirectory(Directory):
    def _q_lookup(self, component):
        formdata_match = re.match('^([1-9][0-9]*)-([1-9][0-9]*)$', component)
        formdef_match = re.match('^([1-9][0-9]*)$', component)
        if formdata_match:
            formdef_id, formdata_id = formdata_match.groups()
        elif formdef_match:
            formdef_id, formdata_id = formdef_match.groups()[0], None
        else:
            raise errors.TraversalError()
        try:
            formdef = FormDef.get(formdef_id)
        except KeyError:
            raise errors.TraversalError()
        if formdata_id:
            return redirect(formdef.get_url() + f'{formdata_id}/')
        return redirect(formdef.get_url())


class RootDirectory(Directory):
    _q_exports = [
        'admin',
        'backoffice',
        'forms',
        'login',
        'logout',
        'saml',
        'ident',
        'register',
        'afterjobs',
        'myspace',
        'user',
        'roles',
        ('tmp-upload', 'tmp_upload'),
        'api',
        'tryauth',
        'auth',
        'preview',
        'fargo',
        'static',
        'actions',
        ('r', 'tiny_redirect'),
    ]

    api = ApiDirectory()
    myspace = MyspaceDirectory()
    fargo = portfolio.FargoDirectory()
    static = StaticsDirectory()
    actions = ActionsDirectory()
    tiny_redirect = TinyRedirectDirectory()

    forced_language = False

    def tryauth(self):
        return root.tryauth(get_publisher().get_root_url())

    def auth(self):
        return root.auth(get_publisher().get_root_url())

    def logout(self):
        session = get_session()
        if not session:
            return redirect(get_publisher().get_root_url())
        ident_methods = get_cfg('identification', {}).get('methods', [])

        if (
            'fc' in ident_methods
            and session.extra_user_variables
            and 'fc_id_token' in session.extra_user_variables
        ):
            return get_publisher().ident_methods['fc']().logout()

        if 'idp' not in ident_methods:
            get_session_manager().expire_session()
            return redirect(get_publisher().get_root_url())

        if not get_session().lasso_identity_provider_id:
            get_session_manager().expire_session()
            return redirect(get_publisher().get_root_url())

        # add settings to disable single logout?
        #   (and to set it as none/get/soap?)
        return self.saml.slo_sp()

    def user(self):
        # endpoint for backward compatibility, new code should call /api/user/
        if get_request().is_json():
            return self.api.user._q_index()
        return redirect('myspace/')

    def roles(self):
        # endpoint for backward compatibility, new code should call /api/roles
        if not get_request().is_json():
            return redirect('/')
        return self.api.roles()

    def tmp_upload(self):
        results = []
        storage = get_request().form.get('storage')
        for v in get_request().form.values():
            if hasattr(v, 'fp'):
                try:
                    tempfile = get_session().add_tempfile(v, storage=storage)
                    results.append(
                        {
                            'name': tempfile.get('base_filename'),
                            'type': tempfile.get('content_type'),
                            'size': tempfile.get('size'),
                            'token': tempfile.get('token'),
                        }
                    )
                    if not get_storage_object(storage).has_redirect_url(None):
                        results[-1]['url'] = 'tempfile?t=%s' % tempfile.get('token')
                except UploadStorageError as e:
                    get_publisher().record_error(_('Upload storage error'), exception=e)
                    results.append({'error': _('failed to store file (system error)')})

        get_response().set_content_type('application/json')
        useragent = get_request().get_header('User-agent') or ''
        if re.findall(r'MSIE \d\.', useragent):
            # hack around MSIE version < 10 as they do not have support for
            # XmlHttpRequest 2 (hence the forced usage of an iframe to send
            # a file in the background (cf jquery.iframe-transport.js); and
            # they would propose the returned json content for download if
            # it was served with the appropriate content type :/
            get_response().set_content_type('text/plain')
        return json.dumps(results, cls=misc.JSONEncoder)

    def feed_substitution_parts(self):
        get_publisher().substitutions.feed(get_session())
        get_publisher().substitutions.feed(get_request().user)

    def _q_traverse(self, path):
        if get_publisher().site_options_exception:
            raise Exception('invalid site options') from get_publisher().site_options_exception
        if path and path[0] == 'manage':
            return redirect('/backoffice' + get_request().get_path_query().removeprefix('/manage'))

        self.forced_language = False
        self.feed_substitution_parts()

        output = self.try_passive_sso(path)
        if output:
            return output

        response = get_response()
        if not response.filter:
            response.filter = {'default_org': _('Forms')}
        if not hasattr(response, 'breadcrumb'):
            response.breadcrumb = [('', _('Home'))]

        if not self.admin:
            self.admin = get_publisher().admin_directory_class()

        if not self.backoffice:
            self.backoffice = get_publisher().backoffice_directory_class()

        if get_request().user and (
            self.backoffice.is_accessible('forms')
            or self.backoffice.is_accessible('cards')
            or self.backoffice.is_accessible('workflows')
        ):
            get_response().add_javascript(['wcs.logged-errors.js'])

        try:
            return Directory._q_traverse(self, path)
        except errors.TraversalError:
            pass

        return root.RootDirectory()._q_traverse(path)

    def try_passive_sso(self, path):
        if path and path[0] == 'api':
            # skip passive SSO for API calls
            return
        publisher = get_publisher()
        idp_session_cookie_name = publisher.get_site_option('idp_session_cookie_name')

        if not idp_session_cookie_name:
            return
        ident_methods = get_cfg('identification', {}).get('methods', [])
        idps = get_cfg('idp', {})
        if len(idps) != 1:
            return
        if ident_methods and 'idp' not in ident_methods:
            return

        request = get_request()
        cookies = request.cookies

        if request.user:
            if request.session.opened_session_value and request.session.opened_session_value != cookies.get(
                idp_session_cookie_name
            ):
                # logout current user if saved value for idp_session_cookie_name differs from the current one
                get_session_manager().expire_session()
                get_request()._user = ()
                get_publisher().session_manager.start_request()
                get_publisher().session_manager.maintain_session(get_session())
            else:
                # already logged, stop here.
                return
        elif path and path[0] in ('backoffice', 'login', 'manage'):
            # do not start passive SSO for backoffice or login URLs
            return

        session = get_session()

        if (
            idp_session_cookie_name not in cookies
            or cookies.get(idp_session_cookie_name) == session.opened_session_value
        ):
            # let the flow continue and the expected page be served.
            return

        session.opened_session_value = cookies.get(idp_session_cookie_name)
        url = request.get_url()
        query = request.get_query()
        if query:
            url += '?' + query
        return root.tryauth(url)

    def _q_lookup(self, component):
        if (
            get_publisher().has_i18n_enabled()
            and not self.forced_language
            and component in (get_cfg('language', {}).get('languages') or [])
        ):
            if component != get_publisher().current_language:
                get_publisher().activate_language(component, get_request())
            self.forced_language = True
            return self

        # is this a category ?
        try:
            category = Category.get_by_urlname(component)
        except KeyError:
            pass
        else:
            # display category unless there's a formdef with same slug
            try:
                FormDef.get_by_urlname(component)
            except KeyError:
                return root.RootDirectory(category)

        # or a form ?
        return root.RootDirectory()._q_lookup(component)

    admin = None
    backoffice = None

    saml = saml2.Saml2Directory()
    forms = CompatibilityDirectory()
    login = LoginDirectory()
    register = RegisterDirectory()
    ident = IdentDirectory()
    afterjobs = AfterJobStatusDirectory()
    preview = PreviewDirectory()

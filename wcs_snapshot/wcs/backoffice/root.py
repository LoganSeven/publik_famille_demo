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

import os

from django.utils.translation import pgettext
from quixote import get_publisher, get_request, get_response, redirect
from quixote.directory import AccessControlled, Directory

import wcs.admin.categories
import wcs.admin.forms
import wcs.admin.roles
import wcs.admin.settings
import wcs.admin.users
import wcs.admin.workflows
from wcs.formdef import FormDef

from ..qommon import _, errors, get_cfg, misc, template
from ..qommon.afterjobs import AfterJob
from .cards import CardsDirectory
from .data_management import DataManagementDirectory
from .i18n import I18nDirectory
from .journal import JournalDirectory
from .management import ManagementDirectory
from .studio import StudioDirectory
from .submission import SubmissionDirectory


class RootDirectory(AccessControlled, Directory):
    _q_exports = ['', 'pending', 'statistics', ('menu.json', 'menu_json'), 'processing', 'journal']

    forms = wcs.admin.forms.FormsDirectory()
    roles = wcs.admin.roles.RolesDirectory()
    settings = wcs.admin.settings.SettingsDirectory()
    users = wcs.admin.users.UsersDirectory()
    workflows = wcs.admin.workflows.WorkflowsDirectory()
    management = ManagementDirectory()
    journal = JournalDirectory()
    studio = StudioDirectory()
    cards = CardsDirectory()
    data = DataManagementDirectory()
    submission = SubmissionDirectory()
    i18n = I18nDirectory()

    menu_items = [
        ('submission/', _('Submission')),
        ('management/', _('Management')),
        ('data/', _('Cards')),
        ('studio/', _('Studio')),
        ('forms/', _('Forms Workshop'), {'sub': True}),
        ('cards/', _('Card Models'), {'sub': True}),
        ('workflows/', _('Workflows Workshop'), {'sub': True}),
        ('users/', _('Users'), {'check_display_function': roles.is_visible}),
        ('roles/', _('Roles'), {'check_display_function': roles.is_visible}),
        ('i18n/', _('Multilinguism'), {'check_display_function': lambda x: False}),
        ('settings/', _('Settings')),
    ]

    def _q_traverse(self, path):
        if not hasattr(self, self._q_translate(path[0]) or path[0]):
            try:
                # keep compatibility with previous versions, redirect from
                # legacy URL to new ones under management/
                FormDef.get_by_urlname(path[0], ignore_migration=True)
                url = get_request().get_path_query()
                url = url.replace('/backoffice/', '/backoffice/management/', 1)
                return redirect(url)
            except KeyError:
                pass
        get_response().add_javascript(['jquery.js', 'qommon.js', 'gadjo.js'])
        get_response().add_css_include('../xstatic/css/godo.css')
        if path and path[0] == 'categories':
            # legacy /backoffice/categories/<...>, redirect.
            return redirect('/backoffice/forms/' + '/'.join(path))
        get_response().set_backoffice_section('studio')
        return super()._q_traverse(path)

    @classmethod
    def is_accessible(cls, subdirectory, traversal=False):
        # check a backoffice directory is accessible to the current user

        if getattr(get_response(), 'filter', {}) and get_response().filter.get('admin_for_all'):
            # if admin for all is set, access is granted to everything
            return True

        if not get_request().user:
            if not (get_publisher().user_class.exists()):
                # setting up the site, access is granted to settings and users
                # sections
                return subdirectory in ('settings', 'users')
            return False

        # if the directory defines a is_accessible method, use it.
        if hasattr(getattr(cls, subdirectory, None), 'is_accessible'):
            return getattr(cls, subdirectory).is_accessible(get_request().user, traversal=traversal)

        return cls.is_global_accessible(subdirectory)

    @classmethod
    def is_global_accessible(cls, subdirectory, user=Ellipsis):
        if cls.check_admin_for_all():
            return True
        if user is Ellipsis:
            # default to user from request
            user = get_request().user
        if not user:
            return False
        user_roles = set(user.get_roles())
        authorised_roles = set(get_cfg('admin-permissions', {}).get(subdirectory) or [])
        if authorised_roles:
            # access is governed by roles set in the settings panel
            return bool(user_roles.intersection(authorised_roles))

        # as a last resort, for the other directories, the user needs to be
        # marked as admin
        return user.can_go_in_admin()

    @classmethod
    def check_admin_for_all(cls):
        admin_for_all_file_path = os.path.join(get_publisher().app_dir, 'ADMIN_FOR_ALL')
        if not os.path.exists(os.path.join(admin_for_all_file_path)):
            return False
        with open(admin_for_all_file_path) as fd:
            admin_for_all_contents = fd.read()
        if not admin_for_all_contents:
            # empty file, access is granted to everybody
            return True
        if get_request().get_environ('REMOTE_ADDR', '') in admin_for_all_contents.splitlines():
            # if the file is not empty it should contain the list of authorized
            # IP addresses.
            return True
        return False

    def _q_access(self):
        get_response().breadcrumb = []  # reinit, root the breadcrumb in the backoffice
        get_response().breadcrumb.append(('backoffice/', _('Back Office')))
        get_response().add_javascript(['jquery.js', 'qommon.admin.js'])
        req = get_request()

        if self.check_admin_for_all():
            get_response().filter['admin_for_all'] = True
            return

        if get_publisher().user_class.exists():
            user = req.user
            if not user:
                raise errors.AccessUnauthorizedError(
                    public_msg=_(
                        'Access to backoffice is restricted to authorized persons only. Please login.'
                    )
                )
            if not user.can_go_in_backoffice():
                raise errors.AccessForbiddenError()
        else:
            # empty site
            if get_cfg('idp'):  # but already configured for IdP
                raise errors.AccessUnauthorizedError()

        get_response().filter['in_backoffice'] = True

    def generate_header_menu(self, selected=None):
        s = ['<ul id="sidepage-menu">\n']
        for menu_item in self.get_menu_items():
            if 'icon' not in menu_item:
                continue
            if menu_item.get('slug') == selected:
                s.append('<li class="active">')
            else:
                s.append('<li>')
            s.append('<a href="%(url)s" class="icon-%(icon)s">%(label)s</a></li>\n' % menu_item)
        s.append('</ul>\n')
        return ''.join(s)

    def _q_index(self):
        for directory in ('studio', 'management'):
            if self.is_accessible(directory, traversal=True):
                return redirect(directory + '/')
        raise errors.AccessForbiddenError()

    def menu_json(self):
        return misc.json_response(self.get_menu_items())

    def pending(self):
        # kept as a redirection for compatibility with possible bookmarks
        return redirect('.')

    def statistics(self):
        return redirect('management/statistics')

    def _q_lookup(self, component):
        if component in [str(x[0]).strip('/') for x in self.menu_items]:
            if not self.is_accessible(component, traversal=True):
                # traversal=True will make it skip some expensive checks and
                # let directories/views further down apply their own checks.
                raise errors.AccessForbiddenError()
            return getattr(self, component)
        return super()._q_lookup(component)

    def get_menu_items(self):
        menu_items = []
        backoffice_url = get_publisher().get_backoffice_url()
        if not backoffice_url.endswith('/'):
            backoffice_url += '/'
        for item in self.menu_items:
            if len(item) == 2:
                item = list(item) + [{}]
            k, v, options = item
            slug = k.strip('/')
            if not slug:
                continue
            display_function = options.get('check_display_function')
            if display_function and not display_function(slug):
                continue
            if not self.is_accessible(slug, traversal=False):
                continue
            if callable(v):
                label = v()
            else:
                label = _(v)
            if slug == 'forms':
                label = misc.site_encode(pgettext('studio', 'Forms'))
            elif slug == 'cards':
                label = misc.site_encode(pgettext('studio', 'Card Models'))
            elif slug == 'workflows':
                label = misc.site_encode(pgettext('studio', 'Workflows'))
            menu_items.append(
                {
                    'label': label,
                    'slug': slug,
                    'url': backoffice_url + k,
                    'sub': options.get('sub') or False,
                }
            )

            if slug in (
                'home',
                'forms',
                'workflows',
                'users',
                'roles',
                'categories',
                'settings',
                'management',
                'submission',
                'studio',
                'cards',
                'data',
            ):
                menu_items[-1]['icon'] = k.strip('/')
        return menu_items

    def processing(self):
        try:
            job = AfterJob.get(get_request().form.get('job'))
        except KeyError:
            return redirect('.')

        get_response().add_javascript(['jquery.js', 'afterjob.js'])
        get_response().set_title(job.label)
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/processing.html'], context={'job': job}
        )

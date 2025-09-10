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

import copy
import hashlib
import io
import json
import mimetypes
import os

try:
    import lasso
except ImportError:
    lasso = None
import xml.etree.ElementTree as ET
import zipfile

from django.utils.encoding import force_bytes
from quixote import get_publisher, get_request, get_response, get_session, redirect
from quixote.directory import AccessControlled, Directory
from quixote.html import TemplateIO, htmltext

from wcs.blocks import BlockDef, BlockdefImportError
from wcs.carddef import CardDef
from wcs.categories import (
    BlockCategory,
    CardDefCategory,
    Category,
    CategoryImportError,
    CommentTemplateCategory,
    DataSourceCategory,
    MailTemplateCategory,
    WorkflowCategory,
)
from wcs.comment_templates import CommentTemplate
from wcs.data_sources import NamedDataSource, NamedDataSourceImportError
from wcs.fields.map import MapOptionsMixin
from wcs.formdef import FormDef
from wcs.formdef_base import FormDefBase, FormdefImportError, get_formdefs_of_all_kinds
from wcs.mail_templates import MailTemplate
from wcs.qommon import _, audit, errors, get_cfg, ident, pgettext_lazy, template
from wcs.qommon.admin.cfg import cfg_submit, hobo_kwargs
from wcs.qommon.admin.emails import EmailsDirectory
from wcs.qommon.admin.texts import TextsDirectory
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.form import (
    CheckboxesTableWidget,
    CheckboxesWidget,
    CheckboxWidget,
    ComputedExpressionWidget,
    FileWidget,
    Form,
    HtmlWidget,
    IntWidget,
    MapWidget,
    PasswordWidget,
    RadiobuttonsWidget,
    SingleSelectWidget,
    StringWidget,
    TextWidget,
)
from wcs.qommon.misc import JSONEncoder
from wcs.sql import ApiAccess
from wcs.workflows import Workflow, WorkflowImportError
from wcs.wscalls import NamedWsCall, NamedWsCallImportError

from .api_access import ApiAccessDirectory
from .data_sources import NamedDataSourcesDirectory
from .fields import FieldDefPage, FieldsDirectory
from .wscalls import NamedWsCallsDirectory


class UserFormDirectory(Directory):
    _q_exports = ['']


class IdentificationDirectory(Directory):
    _q_exports = ['']

    def _q_index(self):
        get_response().breadcrumb.append(('identification/', _('Identification')))
        identification_cfg = get_cfg('identification', {})
        form = Form(enctype='multipart/form-data')
        methods = [
            ('password', _('Simple local username / password'), 'password'),
        ]
        if lasso is not None:
            methods.insert(0, ('idp', _('Delegated to SAML identity provider'), 'idp'))
        methods.append(('fc', _('Delegated to FranceConnect'), 'fc'))
        form.add(
            CheckboxesWidget,
            'methods',
            title=_('Methods'),
            value=identification_cfg.get('methods'),
            options=methods,
            inline=False,
            required=True,
            **hobo_kwargs(),
        )

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('..')

        if form.is_submitted() and not form.has_errors():
            cfg_submit(form, 'identification', ['methods'])
            if not form.has_errors():
                return redirect('..')

        get_response().set_title(_('Identification'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Identification')
        if lasso is None:
            cls = 'infonotice'
            if identification_cfg.get('methods') and 'idp' in identification_cfg.get('methods'):
                cls = 'errornotice'
            r += htmltext('<p class="%s">%s</p>') % (
                cls,
                _(
                    'Delegated to SAML identity provider \
authentication is unavailable. Lasso must be installed to use it.'
                ),
            )
        r += form.render()
        return r.getvalue()

    def _q_lookup(self, component):
        get_response().breadcrumb.append(('identification/', _('Identification')))
        return ident.get_method_admin_directory(component)


class UserFieldDefPage(FieldDefPage):
    blacklisted_attributes = ['condition']
    is_documentable = False


class UserFieldsDirectory(FieldsDirectory):
    _q_exports = ['', 'update_order', 'new', 'mapping', 'templates']

    field_def_page_class = UserFieldDefPage
    support_import = False
    blacklisted_types = ['page', 'computed']
    field_var_prefix = '..._user_var_'

    def index_bottom(self):
        r = TemplateIO(html=True)
        r += get_session().display_message()
        r += htmltext('<div class="bo-block">')
        r += htmltext('<h2>%s</h2>') % _('Fields Mapping')
        r += htmltext('<p>%s</p>') % _(
            'These settings make it possible to assign custom user fields to standard user fields.'
        )
        r += self.mapping_form().render()
        r += htmltext('</div>')
        r += htmltext('<div class="bo-block">')
        r += htmltext('<h2>%s</h2>') % _('Templates')
        r += self.templates_form().render()
        r += htmltext('</div>')
        return r.getvalue()

    def mapping_form(self):
        users_cfg = get_cfg('users', {})
        options = [(None, _('None'), '')] + [(x.id, x.label, x.id) for x in self.objectdef.fields]
        form = Form(action='mapping', id='mapping')
        form.add(
            SingleSelectWidget,
            'field_email',
            title=_('Field for Email'),
            value=users_cfg.get('field_email'),
            options=options,
        )
        form.add(
            SingleSelectWidget,
            'field_phone',
            title=_('Field for Phone'),
            value=users_cfg.get('field_phone'),
            options=options,
        )
        form.add(
            SingleSelectWidget,
            'field_mobile',
            title=_('Field for mobile phone'),
            value=users_cfg.get('field_mobile'),
            options=options,
        )
        form.add_submit('submit', _('Submit'))
        return form

    def mapping(self):
        form = self.mapping_form()
        cfg_submit(form, 'users', ['field_email', 'field_phone', 'field_mobile'])
        return redirect('.')

    @classmethod
    def templates_form(cls, action='templates'):
        users_cfg = get_cfg('users', {})
        if not users_cfg.get('fullname_template') and users_cfg.get('field_name'):
            # migrate old value (list of field ids)
            field_name_values = users_cfg.get('field_name')
            fields = UserFieldsFormDef().fields
            field_varnames = [[x for x in fields if str(x.id) == str(y)] for y in field_name_values]
            field_varnames = [x[0].varname for x in field_varnames if x]
            users_cfg['fullname_template'] = ' '.join(
                '{{ user_var_%s|default:"" }}' % x for x in field_varnames
            )
        form = Form(action=action, id='templates')
        form.add(
            StringWidget,
            'fullname_template',
            title=_('Fullname Template'),
            value=users_cfg.get('fullname_template'),
            required=False,
            validation_function=ComputedExpressionWidget.validate_template,
            size=72,
        )
        form.add(
            TextWidget,
            'sidebar_template',
            title=_('Sidebar Template'),
            value=users_cfg.get('sidebar_template') or '{{ form_user_display_name }}',
            required=False,
            validation_function=ComputedExpressionWidget.validate_template,
            rows=4,
        )
        form.add(
            TextWidget,
            'search_result_template',
            title=_('Search Result Template'),
            value=users_cfg.get('search_result_template')
            or get_publisher().user_class.default_search_result_template,
            required=False,
            validation_function=ComputedExpressionWidget.validate_template,
            rows=7,
        )

        form.add_submit('submit', _('Submit'))
        return form

    def templates(self):
        form = self.templates_form()
        if form.has_errors():
            get_session().add_message(
                ' / '.join(
                    [
                        _('%(title)s: %(error)s') % {'title': x.title, 'error': x.get_error()}
                        for x in form.get_all_widgets()
                        if x.has_error()
                    ]
                ),
                level='error',
            )
            return redirect('.')
        old_fullname_template = get_cfg('users', {}).get('fullname_template')
        cfg_submit(form, 'users', ['fullname_template', 'sidebar_template', 'search_result_template'])
        if get_cfg('users', {}).get('fullname_template') != old_fullname_template:
            get_publisher().add_after_job(UserFullNameTemplateJob())
        return redirect('.')


class UserFullNameTemplateJob(AfterJob):
    label = _('Updating users for new full name template')

    def execute(self):
        get_publisher().user_class.update_attributes_from_formdata()


class UserFieldsFormDef(FormDefBase):
    """Class to handle custom user fields, it loads and saves from/to
    an XML string stored in the configuration (at users/formdef)"""

    may_appear_in_frontoffice = False  # won't appear in frontoffice

    @staticmethod
    def singleton(publisher=None):
        if publisher is None:
            publisher = get_publisher()
        if not getattr(publisher, '_cached_user_fields_formdef', None):
            publisher._cached_user_fields_formdef = UserFieldsFormDef(publisher)
        return publisher._cached_user_fields_formdef

    def __init__(self, publisher=None):
        if publisher is None:
            publisher = get_publisher()
        self.publisher = publisher
        users_cfg = publisher.cfg.get('users', {})
        xml_import = users_cfg.get('formdef')
        self.fields = []  # make sure fields is a list
        self.id = None  # required for XML import/export
        if xml_import:
            try:
                tree = ET.fromstring(xml_import)
            except Exception:
                pass
            else:
                obj = FormDefBase.import_from_xml_tree(tree, include_id=True)
                self.fields = obj.fields

    @property
    def name(self):
        return _('User Fields')

    def get_admin_url(self):
        base_url = get_publisher().get_backoffice_url()
        return '%s/settings/users/fields/' % base_url

    def get_workflow(self):
        return None

    def is_readonly(self):
        return False

    def store(self, comment=None):
        audit('settings', cfg_key='users')
        xml_export = self.export_to_xml(include_id=True)
        users_cfg = self.publisher.cfg.get('users', {})
        users_cfg['formdef'] = ET.tostring(xml_export)
        self.publisher.cfg['users'] = users_cfg
        self.publisher.write_cfg()
        self.publisher._cached_user_fields_formdef = None
        from wcs import sql

        sql.do_user_table()


class UsersDirectory(Directory):
    _q_exports = ['', 'fields']

    def _q_index(self):
        return redirect('fields/')

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('users/', _('Users')))
        self.fields = UserFieldsDirectory(UserFieldsFormDef())
        return Directory._q_traverse(self, path)


class FileTypeDirectory(Directory):
    _q_exports = ['', 'delete']
    filetype_id = None

    def __init__(self, filetype_id):
        self.filetypes_cfg = get_cfg('filetypes', {})
        try:
            self.filetype_id = int(filetype_id)
            self.filetype = self.filetypes_cfg[self.filetype_id]
        except (ValueError, KeyError):
            raise errors.TraversalError()

    @classmethod
    def get_form(cls, filetype=None, filetype_id=None):
        filetype = filetype or {}
        form = Form(enctype='multipart/form-data')
        form.add(StringWidget, 'label', title=_('Label'), required=True, value=filetype.get('label'))
        form.add(
            StringWidget,
            'mimetypes',
            title=_('Mime types'),
            hint=_(
                'List of MIME types, separated by commas. File extensions (starting with a dot) '
                'are allowed and will be automatically converted to the matching type.'
            ),
            required=True,
            size=70,
            value=', '.join(filetype.get('mimetypes', [])),
        )
        form.add(
            CheckboxWidget,
            'is_default',
            title=_('Use as default'),
            value=bool(filetype_id and get_cfg('misc', {}).get('default_file_type') == filetype_id),
        )
        form.add_submit('submit', _('Save'))
        form.add_submit('cancel', _('Cancel'))
        return form

    def _q_index(self):
        form = self.get_form(self.filetype, self.filetype_id)

        if form.get_widget('cancel').parse():
            return redirect('..')

        if form.get_submit() == 'submit' and not form.has_errors():
            filetype = {
                'label': form.get_widget('label').parse(),
                'mimetypes': FileTypesDirectory.parse_mimetypes(form.get_widget('mimetypes').parse()),
            }

            if form.get_widget('is_default').parse():
                misc_cfg = get_cfg('misc', {})
                misc_cfg['default_file_type'] = self.filetype_id
                get_publisher().cfg['misc'] = misc_cfg
                get_publisher().write_cfg()

            if filetype == self.filetypes_cfg.get(self.filetype_id):
                return redirect('..')

            self.filetypes_cfg[self.filetype_id] = filetype
            audit('settings', cfg_key='filetypes')
            get_publisher().write_cfg()

            job = get_publisher().add_after_job(
                FileTypeUpdateAfterJob(
                    label=_('Updating fields'),
                    return_url='/backoffice/settings/filetypes/',
                    filetype_id=self.filetype_id,
                    new_filetype=filetype,
                )
            )
            job.store()
            return redirect(job.get_processing_url())

        get_response().set_title(_('File Type'))
        get_response().breadcrumb.append((f'{self.filetype_id}', self.filetype['label']))
        r = TemplateIO(html=True)
        r += htmltext('<div id="appbar">')
        r += htmltext('<h2>%s - %s</h2>') % (_('File Type'), self.filetype['label'])
        r += htmltext('<span class="actions">')
        r += htmltext('<a href="delete" rel="popup">%s</a>') % _('Delete')
        r += htmltext('</span>')
        r += htmltext('</div>')
        r += form.render()
        return r.getvalue()

    def delete(self):
        form = Form(enctype='multipart/form-data')
        form.widgets.append(
            HtmlWidget('<p>%s</p>' % _('You are about to irrevocably delete this file type.'))
        )
        form.widgets.append(
            HtmlWidget('<p>%s</p>' % _('Fields referencing this file type will continue to work.'))
        )
        form.add_submit('delete', _('Delete'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.get_submit() == 'delete' and not form.has_errors():
            get_response().breadcrumb.append((f'{self.filetype_id}', self.filetype['label']))
            filetypes_cfg = get_cfg('filetypes', {})
            del filetypes_cfg[self.filetype_id]
            audit('settings', cfg_key='filetypes')
            get_publisher().write_cfg()
            return redirect('..')

        get_response().breadcrumb.append(('delete', _('Delete')))
        get_response().set_title(_('Delete File Type'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s %s</h2>') % (_('Deleting File Type:'), self.filetype['label'])
        r += form.render()
        return r.getvalue()


class FileTypesDirectory(Directory):
    _q_exports = ['', 'new']

    def new(self):
        get_response().breadcrumb.append(('new', _('New')))
        form = FileTypeDirectory.get_form()
        if form.get_widget('cancel').parse():
            return redirect('.')
        if form.get_submit() == 'submit' and not form.has_errors():
            filetypes_cfg = get_cfg('filetypes', {})
            if filetypes_cfg:
                new_filetype_id = max(filetypes_cfg.keys()) + 1
            else:
                new_filetype_id = 1
            new_filetype = {
                'label': form.get_widget('label').parse(),
                'mimetypes': self.parse_mimetypes(form.get_widget('mimetypes').parse()),
            }
            if form.get_widget('is_default').parse():
                misc_cfg = get_cfg('misc', {})
                misc_cfg['default_file_type'] = new_filetype_id
                get_publisher().cfg['misc'] = misc_cfg
            filetypes_cfg[new_filetype_id] = new_filetype
            get_publisher().cfg['filetypes'] = filetypes_cfg
            audit('settings', cfg_key='filetypes')
            get_publisher().write_cfg()
            return redirect('.')

        get_response().set_title(_('New file type'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('New file type')
        r += form.render()
        return r.getvalue()

    @classmethod
    def parse_mimetypes(cls, value):
        def ensure_mimetype(x):
            x = x.strip()
            if x.startswith('.'):
                mime_type = mimetypes.guess_type('foobar' + x)[0]
                if mime_type:
                    return mime_type
            return x

        return [ensure_mimetype(x) for x in value.split(',')]

    @classmethod
    def format_mimetypes(cls, types):
        if not types:
            return ''
        l = []
        ellipsis = '...'
        for mimetype in types:
            if sum(len(x) for x in l) > 80:
                # string got too long already, stop this now and we'll get an
                # ellipsis
                break
            ext = mimetypes.guess_extension(mimetype)
            if ext:
                l.append('%s (%s)' % (mimetype, ext))
            else:
                l.append(mimetype)
        else:
            # we got to the end of the list, we won't need an ellipsis
            ellipsis = ''
        return ', '.join(l) + ellipsis

    def _q_index(self):
        get_response().set_title(_('File Types'))

        filetypes_cfg = get_cfg('filetypes', {})
        misc_cfg = get_cfg('misc', {})
        for key, filetype in filetypes_cfg.items():
            filetype['id'] = key
            filetype['formatted_mime_type'] = self.format_mimetypes(filetype.get('mimetypes'))
            filetype['is_default'] = bool(key == misc_cfg.get('default_file_type'))

        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/file_types.html'],
            context={'view': self, 'file_types': filetypes_cfg.values()},
        )

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('filetypes/', _('File Types')))
        return Directory._q_traverse(self, path)

    def _q_lookup(self, component):
        return FileTypeDirectory(filetype_id=component)


class SettingsDirectory(AccessControlled, Directory):
    _q_exports_orig = [
        '',
        'users',
        'template',
        'emails',
        'debug_options',
        'language',
        ('import', 'p_import'),
        ('import-report', 'import_report'),
        'export',
        'identification',
        'sitename',
        'sms',
        'certificates',
        'texts',
        'postgresql',
        ('admin-permissions', 'admin_permissions'),
        'geolocation',
        'filetypes',
        ('user-templates', 'user_templates'),
        ('data-sources', 'data_sources'),
        'wscalls',
        ('api-access', 'api_access'),
        ('backoffice-submission', 'backoffice_submission'),
    ]

    emails = EmailsDirectory()
    identification = IdentificationDirectory()
    users = UsersDirectory()
    texts = TextsDirectory()
    filetypes = FileTypesDirectory()
    data_sources = NamedDataSourcesDirectory()
    wscalls = NamedWsCallsDirectory()
    api_access = ApiAccessDirectory()

    def _q_access(self):
        get_response().breadcrumb.append(('settings/', _('Settings')))
        get_response().set_backoffice_section('settings')

        disabled_screens_option = get_publisher().get_site_option('settings-disabled-screens') or ''
        self.disabled_screens = [x.strip() for x in disabled_screens_option.split(',') if x.strip()]

        screens = {
            'storage': ['postgresql'],
            'permissions': ['admin-permissions'],
            'import-export': ['import', 'export'],
            'misc': ['debug_options'],
        }

        q_exports = self._q_exports_orig[:]
        for screen in self.disabled_screens:
            for page in screens.get(screen, [screen]):
                q_exports = [
                    x for x in q_exports if x != page and not (isinstance(x, tuple) and x[0] == page)
                ]
        self._q_exports = q_exports

    def _q_index(self):
        get_response().set_title(_('Settings'))
        r = TemplateIO(html=True)

        hidden_screens_option = get_publisher().get_site_option('settings-hidden-screens') or ''
        hidden_screens = [x.strip() for x in hidden_screens_option.split(',')] + self.disabled_screens

        if ApiAccess.count() == 0:
            hidden_screens.append('api-access')

        def enabled(screen):
            return screen not in hidden_screens

        r += htmltext('<div class="splitcontent-left">')

        if enabled('storage') and (
            get_publisher().has_site_option('postgresql') or get_cfg('postgresql', {})
        ):
            r += htmltext('<div class="section">')
            r += htmltext('<h2>%s</h2>') % _('Storage')
            r += htmltext('<dl> <dt><a href="postgresql">%s</a></dt> <dd>%s</dd> </dl>') % (
                _('PostgreSQL Settings'),
                _('Configure access to PostgreSQL database'),
            )
            r += htmltext('</div>')

        r += htmltext('<div class="section">')
        r += htmltext('<h2>%s</h2>') % _('Security')
        r += htmltext('<dl>')

        if enabled('identification'):
            r += htmltext('<dt><a href="identification/">%s</a></dt> <dd>%s</dd>') % (
                _('Identification'),
                _('Configure identification parameters'),
            )

            identification_cfg = get_cfg('identification', {})
            for method in identification_cfg.get('methods', []):
                try:
                    method_admin = ident.get_method_admin_directory(method)
                except AttributeError:
                    continue

                r += htmltext('<dt><a href="identification/%s/">%s</a></dt> <dd>%s</dd>') % (
                    method,
                    _(method_admin.title),
                    _(method_admin.label),
                )

        if enabled('permissions'):
            roles = list(get_publisher().role_class.select())
            if roles:
                r += htmltext('<dt><a href="admin-permissions">%s</a></dt> <dd>%s</dd>') % (
                    _('Admin Permissions'),
                    _('Configure access to the administration interface'),
                )

        if enabled('api-access'):
            r += htmltext('<dt><a href="api-access">%s</a></dt> <dd>%s</dd>') % (
                _('API access'),
                _('Configure access to the API endpoints'),
            )

        r += htmltext('</dl></div>')

        if enabled('import-export'):
            r += htmltext('<div class="section">')
            r += htmltext('<h2>%s</h2>') % _('Import / Export')

            r += htmltext('<dl>')
            r += htmltext('<dt><a href="import">%s</a></dt> <dd>%s</dd>') % (
                _('Import'),
                _('Initialise with data from another site'),
            )
            r += htmltext('<dt><a href="export">%s</a></dt> <dd>%s</dd>') % (
                _('Export'),
                _('Export data for another site'),
            )
            r += htmltext('</dl>')
            r += htmltext('</div>')

        if enabled('misc'):
            r += htmltext('<div class="section">')
            r += htmltext('<h2>%s</h2>') % _('Misc')
            r += htmltext('<dl>')
            r += htmltext('<dt><a href="debug_options">%s</a></dt> <dd>%s</dd>') % (
                _('Debug Options'),
                _('Configure options useful for debugging'),
            )
            r += htmltext('</dl>')
            r += htmltext('</div>')

        r += htmltext('</div>')

        r += htmltext('<div class="splitcontent-right">')
        r += htmltext('<div class="section">')
        r += htmltext('<h2>%s</h2>') % _('Customisation')
        r += htmltext('<div>')

        r += htmltext('<dl>')
        if enabled('sitename'):
            r += htmltext('<dt><a href="sitename">%s</a></dt> <dd>%s</dd>') % (
                _('Site Name and Addresses'),
                _('Configure site name and addresses'),
            )
        if enabled('language'):
            r += htmltext('<dt><a href="language">%s</a></dt> <dd>%s</dd>') % (
                _('Language'),
                _('Configure site language'),
            )
        if enabled('geolocation'):
            r += htmltext('<dt><a href="geolocation">%s</a></dt> <dd>%s</dd>') % (
                _('Geolocation'),
                _('Configure geolocation and geocoding'),
            )
        if enabled('backoffice-submission'):
            r += htmltext('<dt><a href="backoffice-submission">%s</a></dt> <dd>%s</dd>') % (
                _('Backoffice Submission'),
                _('Configure backoffice submission related options'),
            )
        if enabled('users'):
            r += htmltext('<dt><a href="users/">%s</a></dt> <dd>%s</dd>') % (_('Users'), _('Configure users'))
        else:
            # minimal options
            r += htmltext('<dt><a href="user-templates">%s</a></dt> <dd>%s</dd>') % (
                _('Users'),
                _('Configure templates for users'),
            )
        if enabled('emails'):
            r += htmltext('<dt><a href="emails/">%s</a></dt> <dd>%s</dd>') % (
                _('Emails'),
                _('Configure email settings'),
            )
        if enabled('sms'):
            r += htmltext('<dt><a href="sms">%s</a></dt> <dd>%s</dd>') % (
                _('SMS'),
                _('Configure SMS settings'),
            )
        if enabled('texts') and self.texts.texts_dict:
            r += htmltext('<dt><a href="texts/">%s</a></dt> <dd>%s</dd>') % (
                _('Texts'),
                _('Configure text that appears on some pages'),
            )
        if enabled('filetypes'):
            r += htmltext('<dt><a href="filetypes/">%s</a></dt> <dd>%s</dd>') % (
                _('File Types'),
                _('Configure known file types'),
            )
        r += htmltext('<dt><a href="data-sources/">%s</a></dt> <dd>%s</dd>') % (
            _('Data sources'),
            _('Configure data sources'),
        )
        r += htmltext('<dt><a href="wscalls/">%s</a></dt> <dd>%s</dd>') % (
            _('Webservice calls'),
            _('Configure webservice calls'),
        )
        r += htmltext('</dl>')
        r += htmltext('</div>')
        r += htmltext('</div>')

        r += htmltext('</div>')
        return r.getvalue()

    @classmethod
    def get_admin_permission_sections(cls):
        admin_sections = [
            ('forms', _('Forms')),
            ('cards', _('Card Models')),
            ('workflows', _('Workflows')),
            ('users', _('Users')),
            ('roles', _('Roles')),
            ('categories', _('Categories')),
            ('settings', _('Settings')),
            ('journal', _('Audit Journal')),
        ]
        if get_publisher().has_i18n_enabled():
            admin_sections.append(('i18n', _('Internationalization')))
        return admin_sections

    def admin_permissions(self):
        permissions_cfg = get_cfg('admin-permissions', {})
        form = Form(enctype='multipart/form-data')

        permissions = [_('Backoffice')]
        admin_sections = self.get_admin_permission_sections()

        permission_keys = []
        for k, v in admin_sections:
            permissions.append(_(v))
            permission_keys.append(k)

        rows = []
        value = []
        roles = [x for x in get_publisher().role_class.select(order_by='name') if not x.is_internal()]
        for role in roles:
            rows.append(role.name)
            value.append([role.allows_backoffice_access])
            for k in permission_keys:
                authorised_roles = [str(x) for x in permissions_cfg.get(k) or []]
                value[-1].append(bool(str(role.id) in authorised_roles))
        colrows_hash = hashlib.md5(force_bytes('%r-%r' % (rows, permissions))).hexdigest()

        form.add_hidden('hash', colrows_hash)
        form.add(CheckboxesTableWidget, 'permissions', rows=rows, columns=permissions)
        form.get_widget('permissions').set_value(value)

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.get_widget('hash').parse() != colrows_hash:
            # The columns and rows are made of indices; permissions could be
            # wrongly assigned if there were some changes to the columns and
            # rows between the form being displayed and submitted.
            form.get_widget('permissions').set_error(
                _('Changes were made to roles or permissions while the table was displayed.')
            )

        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('admin-permissions', _('Admin Permissions')))
            get_response().set_title(_('Admin Permissions'))
            r = TemplateIO(html=True)
            r += htmltext('<div class="admin-permissions">')
            r += htmltext('<h2>%s</h2>') % _('Admin Permissions')
            r += form.render()
            r += htmltext('</div>')
            return r.getvalue()

        value = form.get_widget('permissions').parse()
        permissions = {}
        for key in permission_keys:
            permissions[key] = []
        for i, role in enumerate(roles):
            permission_row = value[i]
            if role.allows_backoffice_access != permission_row[0]:
                role.allows_backoffice_access = permission_row[0]
                role.store()
            for j, key in enumerate(permission_keys):
                if permission_row[j + 1]:
                    permissions[key].append(role.id)
        get_publisher().cfg['admin-permissions'] = permissions
        audit('settings', cfg_key='admin-permissions')
        get_publisher().write_cfg()
        return redirect('.')

    def export(self):
        if get_request().form.get('download'):
            return self.export_download()

        form = Form(enctype='multipart/form-data')
        options = [
            ('formdefs', _('Forms')),
            ('carddefs', _('Card Models')),
            ('workflows', _('Workflows')),
            ('blockdefs', _('Blocks of Fields')),
            ('datasources', _('Data sources')),
            ('mail-templates', _('Mail templates')),
            ('comment-templates', _('Comment templates')),
            ('wscalls', _('Webservice calls')),
            ('apiaccess', _('API access')),
            ('roles', _('Roles')),
            ('categories', _('Form Categories')),
            ('carddef_categories', _('Card Model Categories')),
            ('workflow_categories', _('Workflow Categories')),
            ('block_categories', _('Blocks of Fields Categories')),
            ('mail_template_categories', _('Mail Templates Categories')),
            ('comment_template_categories', _('Comment Templates Categories')),
            ('data_source_categories', _('Data Sources Categories')),
            ('settings', _('Settings (customisation sections)')),
        ]
        options = [(x[0], x[1], x[0]) for x in options]
        if get_cfg('sp', {}).get('idp-manage-roles'):
            options = [x for x in options if x[0] != 'roles']
        form.add(
            CheckboxesWidget,
            'items',
            title=_('Items to export'),
            inline=False,
            required=True,
            options=options,
            value=[x[0] for x in options if x[0] != 'settings'],
        )
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_submit() == 'cancel':
            return redirect('.')

        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('export', _('Export')))
            get_response().set_title(_('Export'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('Export')
            r += form.render()
            return r.getvalue()

        dirs = [x for x in form.get_widget('items').parse() if x != 'settings']
        export_settings = 'settings' in form.get_widget('items').parse()
        exporter_job = SiteExporterJob(
            dirs,
            settings=export_settings,
            done_action_url=get_request().get_url() + '?download=%(job_id)s',
            done_action_label=_('Download Export'),
            done_button_attributes={'download': 'export.wcs'},
        )

        job = get_publisher().add_after_job(exporter_job)
        job.store()
        return redirect(job.get_processing_url())

    def export_download(self):
        job_id = get_request().form.get('download')
        try:
            job = AfterJob.get(job_id)
        except KeyError:
            return redirect('.')

        response = get_response()
        response.set_content_type('application/x-wcs')
        response.set_header('content-disposition', 'attachment; filename=export.wcs')
        return job.file_content

    def p_import(self):
        form = Form(enctype='multipart/form-data')
        form.add(FileWidget, 'file', title=_('File'), required=True)
        has_content = bool(FormDef.count() or CardDef.count() or Workflow.count())
        if has_content:
            form.widgets.append(
                HtmlWidget(
                    '<div class="warningnotice"><p><strong>%s</strong></p>'
                    % _(
                        'This site has existing forms, cards or workflows, beware re-importing '
                        'content is dangerous and will probably break existing data and configuration.'
                    )
                )
            )
            form.add(CheckboxWidget, 'confirm', title=_('Do it anyway'), required=True)
            form.widgets.append(HtmlWidget('</div>'))
            form.add_submit('submit', _('Submit'), attrs={'data-ask-for-confirmation': 'true'})
        else:
            form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_submit() == 'cancel':
            return redirect('.')

        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('import', _('Import')))
            get_response().set_title(_('Import'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('Import')
            r += htmltext('<div class="section form-inner-container">')
            r += form.render()
            r += htmltext('</div>')
            return r.getvalue()

        job = SiteImportAfterJob(form.get_widget('file').parse().fp)
        job = get_publisher().add_after_job(job)
        job.store()
        return redirect(job.get_processing_url())

    def import_report(self):
        get_response().set_title(_('Import report'))
        get_response().breadcrumb.append(('import-report', _('Import report')))
        try:
            job = AfterJob.get(get_request().form.get('job'))
        except KeyError:
            raise errors.TraversalError()
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/settings/import.html'],
            context={'results': job.results},
        )

    def sitename(self):
        form = Form(enctype='multipart/form-data')
        misc_cfg = get_cfg('misc', {})
        form.add(
            StringWidget,
            'sitename',
            title=_('Site Name'),
            value=misc_cfg.get('sitename', ''),
            **hobo_kwargs(),
        )
        form.add(
            StringWidget,
            'frontoffice-url',
            size=32,
            title=_('Site base URL'),
            value=misc_cfg.get('frontoffice-url', ''),
            **hobo_kwargs(),
        )
        form.add(
            StringWidget,
            'homepage-redirect-url',
            size=32,
            title=_('Homepage redirection'),
            value=misc_cfg.get('homepage-redirect-url', ''),
        )

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('sitename', _('Site Name and Addresses')))
            get_response().set_title(_('Site Name and Addresses'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('Site Name and Addresses')
            r += form.render()
            return r.getvalue()

        cfg_submit(form, 'misc', ['sitename', 'frontoffice-url', 'homepage-redirect-url'])
        return redirect('.')

    def sms(self):
        get_response().breadcrumb.append(('sms', _('SMS')))
        get_response().set_title(_('SMS'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('SMS Options')
        sms_cfg = get_cfg('sms', {})
        form = Form(enctype='multipart/form-data')
        form.add(
            StringWidget,
            'sender',
            title=_('Sender (number or name)'),
            value=sms_cfg.get('sender'),
            **hobo_kwargs(),
        )
        form.add(
            StringWidget,
            'passerelle_url',
            title=_('URL'),
            value=sms_cfg.get('passerelle_url'),
            **hobo_kwargs(),
        )
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.get_submit() and not form.has_errors():
            cfg_submit(form, 'sms', ['sender', 'passerelle_url'])
            return redirect('.')

        r += form.render()
        return r.getvalue()

    def postgresql(self):
        postgresql_cfg = get_cfg('postgresql', {})
        if not get_publisher().has_site_option('postgresql') and not postgresql_cfg:
            raise errors.TraversalError()
        form = Form(enctype='multipart/form-data')
        form.add(
            StringWidget,
            'database',
            title=_('Database Name'),
            required=True,
            value=postgresql_cfg.get('database'),
            **hobo_kwargs(),
        )
        form.add(
            StringWidget,
            'user',
            title=_('User'),
            required=False,
            value=postgresql_cfg.get('user'),
            **hobo_kwargs(hint=_('User name used to authenticate')),
        )
        form.add(
            PasswordWidget,
            'password',
            title=_('Password'),
            required=False,
            value=postgresql_cfg.get('password'),
            **hobo_kwargs(hint=_('Password used to authenticate')),
        )
        form.add(
            StringWidget,
            'host',
            title=_('Host'),
            required=False,
            value=postgresql_cfg.get('host'),
            **hobo_kwargs(hint=_('Database host address')),
        )
        try:
            port = int(postgresql_cfg.get('port'))
        except (ValueError, TypeError):
            port = None
        form.add(
            IntWidget,
            'port',
            title=_('Port'),
            required=False,
            value=port,
            **hobo_kwargs(hint=_('Connection port number')),
        )
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            postgresql_cfg = copy.copy(get_cfg('postgresql', {}))
            cfg_submit(form, 'postgresql', ['database', 'user', 'password', 'host', 'port'])
            try:
                get_publisher().initialize_sql()
            except Exception as e:
                postgresql_cfg['postgresql'] = postgresql_cfg
                form.set_error('database', str(e))
            else:
                return redirect('.')

        get_response().breadcrumb.append(('postgresql', _('PostgreSQL Settings')))
        get_response().set_title(_('PostgreSQL Settings'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('PostgreSQL Settings')
        r += form.render()
        return r.getvalue()

    def geolocation(self):
        misc_cfg = get_cfg('misc', {})
        form = Form(enctype='multipart/form-data')
        form.add(
            MapWidget,
            'default-position',
            title=_('Default Map Position'),
            value=misc_cfg.get('default-position'),
            default_zoom='9',
            required=False,
        )
        zoom_levels = [(x[0], x[1], x[0]) for x in MapOptionsMixin.get_zoom_levels()]
        form.add(
            SingleSelectWidget,
            'default-zoom-level',
            title=_('Default zoom level'),
            value=get_publisher().get_default_zoom_level(),
            options=zoom_levels,
            required=False,
        )

        has_system_settings = bool(
            get_publisher().get_site_option('reverse_geocoding_service_url')
            or get_publisher().get_site_option('geocoding_service_url')
        )
        if has_system_settings:
            form.widgets.append(
                HtmlWidget(
                    '<div class="warningnotice"><p>%s</p>'
                    % _(
                        'System settings are currently forcing geocoding URLs, '
                        'this parameter won\'t have any effect.'
                    )
                )
            )

        form.add(
            StringWidget,
            'geocoding-services-base-url',
            title=_('Geocoding services base URL'),
            value=misc_cfg.get('geocoding-services-base-url')
            or get_publisher().get_site_option('nominatim_url'),
            required=False,
            hint=_('It will be suffixed by /search for geocoding and /reverse for reverse-geocoding.'),
        )

        if has_system_settings:
            form.widgets.append(HtmlWidget('</div>'))

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            cfg_submit(
                form, 'misc', ['default-position', 'default-zoom-level', 'geocoding-services-base-url']
            )
            return redirect('.')

        get_response().breadcrumb.append(('geolocation', _('Geolocation Settings')))
        get_response().set_title(_('Geolocation Settings'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Geolocation Settings')
        r += form.render()
        r += htmltext(
            '''<script>
$('#form_default-zoom-level').on('change', function() {
  var map = $('.qommon-map')[0].leaflet_map;
  var new_zoom = parseInt($(this).val());
  if (! isNaN(new_zoom)) {
    map.setZoom(parseInt($(this).val()));
  }
});
</script>'''
        )
        return r.getvalue()

    def user_templates(self):
        form = UserFieldsDirectory.templates_form(action='user-templates')
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            old_fullname_template = get_cfg('users', {}).get('fullname_template')
            cfg_submit(form, 'users', ['fullname_template', 'sidebar_template', 'search_result_template'])
            if get_cfg('users', {}).get('fullname_template') != old_fullname_template:
                get_publisher().add_after_job(UserFullNameTemplateJob())
            return redirect('.')

        get_response().breadcrumb.append(('user-templates', _('Users')))
        get_response().set_title(_('Users'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Users')
        r += form.render()
        return r.getvalue()

    def language(self):
        form = Form(enctype='multipart/form-data')
        language_cfg = get_cfg('language', {})

        kwargs = {
            'options': [
                ('en', _('English')),
                ('fr', _('French')),
                ('de', _('German')),
            ],
        }
        if language_cfg.get('multilinguism'):
            kwargs['readonly'] = 'readonly'
            kwargs['hint'] = _(
                'As multilinguism is enabled it is not possible to change the primary language.'
            )
            kwargs['options'] = [x for x in kwargs['options'] if x[0] == language_cfg.get('language')]
        form.add(
            SingleSelectWidget,
            'language',
            title=_('Language'),
            value=language_cfg.get('language'),
            required=True,
            **kwargs,
        )
        form.add(
            CheckboxWidget,
            'multilinguism',
            title=_('Enable multilinguism support'),
            value=language_cfg.get('multilinguism', False),
            attrs={'data-dynamic-display-parent': 'true'},
        )
        form.add(
            RadiobuttonsWidget,
            'default_site_language',
            title=_('Default language'),
            value=language_cfg.get('default_site_language', 'site'),
            options=[('site', _('Site Language')), ('http', _('From HTTP Accept-Language header'))],
            attrs={
                'data-dynamic-display-child-of': 'multilinguism',
                'data-dynamic-display-checked': 'true',
            },
        )
        form.add(
            CheckboxesWidget,
            'languages',
            title=_('Supported languages'),
            value=language_cfg.get('languages'),
            options=[
                ('en', _('English')),
                ('fr', _('French')),
                ('de', _('German')),
            ],
            inline=True,
            attrs={
                'data-dynamic-display-child-of': 'multilinguism',
                'data-dynamic-display-checked': 'true',
            },
        )

        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('language', _('Language')))
            get_response().set_title(_('Language'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('Language')
            r += form.render()
            return r.getvalue()

        if not form.get_widget('languages').parse():
            # force empty value to be an empty list
            form.get_widget('languages').value = []

        cfg_submit(form, 'language', ['language', 'multilinguism', 'default_site_language', 'languages'])
        if form.get_widget('multilinguism').parse():
            if form.get_widget('language').parse() not in form.get_widget('languages').parse():
                get_publisher().cfg['language']['languages'].append(form.get_widget('language').parse())
                get_publisher().write_cfg()
            from wcs.i18n import TranslatableMessage

            TranslatableMessage.do_table()
        return redirect('.')

    def debug_options(self):
        form = Form(enctype='multipart/form-data')
        debug_cfg = get_cfg('debug', {})
        form.add(
            StringWidget,
            'error_email',
            title=_('Email for Tracebacks'),
            value=debug_cfg.get('error_email', ''),
        )
        form.add(
            CheckboxWidget,
            'debug_mode',
            title=_('Enable debug mode'),
            value=debug_cfg.get('debug_mode', False),
        )
        form.add(
            StringWidget,
            'mail_redirection',
            title=_('Mail redirection'),
            value=debug_cfg.get('mail_redirection', ''),
            hint=_('If set, send all emails to that address instead of the real recipients'),
        )
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')

        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('debug_options', _('Debug Options')))
            get_response().set_title(_('Debug Options'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('Debug Options')
            r += form.render()
            return r.getvalue()

        cfg_submit(
            form,
            'debug',
            ('error_email', 'debug_mode', 'mail_redirection'),
        )
        return redirect('.')

    def backoffice_submission(self):
        form = Form(enctype='multipart/form-data')
        submission_channels_cfg = get_cfg('submission-channels', {})
        backoffice_submission_cfg = get_cfg('backoffice-submission', {})
        form.add(
            RadiobuttonsWidget,
            'sidebar_menu_entry',
            title=_('Sidebar menu entry'),
            value=backoffice_submission_cfg.get('sidebar_menu_entry', 'visible'),
            options=[
                ('visible', pgettext_lazy('sidebar_menu_entry', 'Visible'), 'visible'),
                ('hidden', pgettext_lazy('sidebar_menu_entry', 'Hidden'), 'hidden'),
            ],
            extra_css_class='widget-inline-radio',
        )
        form.add(
            RadiobuttonsWidget,
            'default_screen',
            title=_('Default submission screen'),
            value=backoffice_submission_cfg.get('default_screen', 'new'),
            options=[
                ('new', _('New submission'), 'new'),
                ('pending', _('Pending submissions'), 'pending'),
                ('custom', _('Custom URL'), 'custom'),
            ],
            attrs={'data-dynamic-display-parent': 'true'},
            extra_css_class='widget-inline-radio',
        )
        form.add(
            StringWidget,
            'redirect',
            title=_('URL for backoffice submission'),
            hint=_('Leave empty to use native screen.'),
            value=backoffice_submission_cfg.get('redirect', ''),
            size=80,
            attrs={
                'data-dynamic-display-child-of': 'default_screen',
                'data-dynamic-display-value': 'custom',
            },
        )
        form.add(
            CheckboxWidget,
            'include-in-global-listing',
            title=_('Include submission channel column in global listing'),
            value=submission_channels_cfg.get('include-in-global-listing'),
        )
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')

        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('backoffice-submission', _('Backoffice Submission')))
            get_response().set_title(_('Backoffice submission settings'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('Backoffice submission settings')
            r += form.render()
            return r.getvalue()

        cfg_submit(form, 'submission-channels', ('include-in-global-listing',))
        cfg_submit(form, 'backoffice-submission', ('sidebar_menu_entry', 'default_screen', 'redirect'))
        return redirect('.')


class FileTypeUpdateAfterJob(AfterJob):
    def done_action_url(self):
        return self.kwargs['return_url']

    def done_button_attributes(self):
        return {'data-redirect-auto': 'true'}

    def done_action_label(self):
        return _('Back to settings')

    def execute(self):
        self.report_lines = []
        formdefs = get_formdefs_of_all_kinds()
        self.total_count = len(formdefs) + Workflow.count()
        self.store()
        filetype_id = self.kwargs['filetype_id']
        new_filetype = self.kwargs['new_filetype']

        def update_document_type(obj):
            if getattr(obj, 'document_type', None) and obj.document_type['id'] == filetype_id:
                old_filetype = obj.document_type.copy()
                del old_filetype['id']
                if old_filetype != new_filetype:
                    obj.document_type = new_filetype.copy()
                    obj.document_type['id'] = filetype_id
                return True
            return False

        for formdef in get_formdefs_of_all_kinds():
            # look for file fields to update them with the new mimetypes.
            changed = False
            for field in formdef.fields or []:
                changed |= update_document_type(field)
            if changed:
                formdef.store(comment=_('Automatic update of file types'))
            self.increment_count()

        for workflow in Workflow.select(ignore_errors=True, ignore_migration=True):
            changed = False
            for item in workflow.get_all_items():
                if item.key == 'addattachment':
                    changed |= update_document_type(item)
            if changed:
                workflow.store(comment=_('Automatic update of file types'))
            self.increment_count()


class SiteExporterJob(AfterJob):
    label = _('Exporting site elements')

    def __init__(self, dirs, settings, **kwargs):
        super().__init__(**kwargs)
        self.app_dir = get_publisher().app_dir
        self.dirs = dirs
        self.settings = settings

    @classmethod
    def get_xml_exports_directories(cls):
        return {
            'apiaccess': ApiAccess,
            'formdefs': FormDef,
            'carddefs': CardDef,
            'workflows': Workflow,
            'blockdefs': BlockDef,
            'roles': get_publisher().role_class,
            'mail-templates': MailTemplate,
            'comment-templates': CommentTemplate,
            'wscalls': NamedWsCall,
            'categories': Category,
            'carddef_categories': CardDefCategory,
            'workflow_categories': WorkflowCategory,
            'block_categories': BlockCategory,
            'mail_template_categories': MailTemplateCategory,
            'comment_template_categories': CommentTemplateCategory,
            'data_source_categories': DataSourceCategory,
        }

    def get_export_file(self):
        c = io.BytesIO()

        xml_exports_directories = self.get_xml_exports_directories()

        with zipfile.ZipFile(c, 'w') as z:
            for xml_export_dir, obj_class in xml_exports_directories.items():
                if xml_export_dir in self.dirs:
                    if xml_export_dir in ('formdefs', 'carddefs', 'workflows', 'blockdefs', 'roles'):
                        xml_export_dir += '_xml'
                    for obj in obj_class.select():
                        node = obj.export_to_xml(include_id=True)
                        ET.indent(node)
                        z.writestr(
                            os.path.join(xml_export_dir, str(obj.id)),
                            b'<?xml version="1.0"?>\n' + ET.tostring(node),
                        )
                        self.increment_count()
            if 'datasources' in self.dirs:
                for ds in NamedDataSource.select():
                    if ds.external == 'agenda':
                        continue
                    node = ds.export_to_xml(include_id=True)
                    ET.indent(node)
                    z.writestr(
                        os.path.join('datasources', str(ds.id)),
                        ET.tostring(node),
                    )
                    self.increment_count()

            if self.settings:
                cfg = copy.copy(get_publisher().cfg)
                cfg.pop('postgresql', None)  # remove as it may be sensitive
                if cfg.get('admin-permissions'):
                    cfg['admin-permissions-export'] = {}
                    for key, role_ids in cfg.get('admin-permissions').items():
                        cfg['admin-permissions-export'][key] = []
                        for role_id in role_ids or []:
                            role = get_publisher().role_class.get(role_id, ignore_errors=True)
                            if role:
                                cfg['admin-permissions-export'][key].append(
                                    {
                                        'id': role.id,
                                        'uuid': role.uuid,
                                        'slug': role.slug,
                                        'name': role.name,
                                    }
                                )
                z_info = zipfile.ZipInfo.from_file(os.path.join(self.app_dir, 'config.pck'), 'config.json')
                z.writestr(z_info, json.dumps(cfg, indent=2, cls=JSONEncoder))
                self.increment_count()
                for f in os.listdir(self.app_dir):
                    if f.startswith('idp-') and os.path.splitext(f)[-1] in ('.pem', '.xml'):
                        z.write(os.path.join(self.app_dir, f), f)
                        self.increment_count()
                if os.path.exists(os.path.join(self.app_dir, 'config')):
                    for f in os.listdir(os.path.join(self.app_dir, 'config')):
                        z.write(os.path.join(self.app_dir, 'config', f), os.path.join('config', f))
                        self.increment_count()
        return c.getvalue()

    def execute(self):
        self.file_content = self.get_export_file()
        self.total_count = self.current_count
        self.store()


class SiteImportAfterJob(AfterJob):
    label = _('Importing site elements')

    def __init__(self, fp, **kwargs):
        super().__init__(site_import_zip_content=fp.read())

    def execute(self):
        error = None
        try:
            results = get_publisher().import_zip(
                io.BytesIO(self.kwargs['site_import_zip_content']), overwrite_settings=False
            )
            results['mail_templates'] = results['mail-templates']
            results['comment_templates'] = results['comment-templates']
        except zipfile.BadZipfile:
            results = None
            error = _('Not a valid export file')
        except (BlockdefImportError, FormdefImportError, WorkflowImportError) as e:
            results = None
            msg = _(e.msg) % e.msg_args
            if e.details:
                msg += ' [%s]' % e.details
            error = _('Failed to import objects (%s); site import did not complete.') % msg
        except (NamedDataSourceImportError, NamedWsCallImportError) as e:
            results = None
            error = _('Failed to import objects (%s); site import did not complete.') % str(e)
        except CategoryImportError as e:
            results = None
            error = str(e)

        self.results = results
        if error:
            self.mark_as_failed(_('Error: %s') % error)
        else:
            self.store()

    def done_action_url(self):
        return '/backoffice/settings/import-report?job=%s' % self.id

    def done_action_label(self):
        return _('Import report')

    def done_button_attributes(self):
        return {'data-redirect-auto': 'true'}

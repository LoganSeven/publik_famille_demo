# w.c.s. - web application for online forms
# Copyright (C) 2005-2024  Entr'ouvert
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
import collections
import copy
import datetime
import decimal
import functools
import itertools
import json
import pickle
import re
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import ExitStack
from operator import itemgetter

from django.utils.encoding import force_bytes, force_str
from django.utils.module_loading import import_string
from django.utils.timezone import localtime
from quixote import get_publisher, get_session
from quixote.html import TemplateIO, htmltext

from wcs.qommon.misc import localstrftime

from . import data_sources, fields
from .categories import Category
from .qommon import _, get_cfg, ngettext, pgettext_lazy
from .qommon.admin.emails import EmailsDirectory
from .qommon.errors import UnknownReferencedErrorMixin
from .qommon.form import Form, HtmlWidget
from .qommon.misc import JSONEncoder, get_as_datetime, get_dependencies_from_template, xml_node_text
from .qommon.storage import Contains, Equal, NotEqual, StoredObjectMixin
from .qommon.substitution import Substitutions
from .qommon.template import Template
from .qommon.upload_storage import PicklableUpload
from .roles import logged_users_role
from .utils import add_timing_group, add_timing_mark

DRAFTS_DEFAULT_LIFESPAN = 100  # days
DRAFTS_DEFAULT_MAX_PER_USER = 5


class FormdefImportError(Exception):
    def __init__(self, msg, msg_args=None, details=None):
        self.msg = msg
        self.msg_args = msg_args or ()
        self.details = details


class FormdefImportUnknownReferencedError(UnknownReferencedErrorMixin, FormdefImportError):
    pass


class FormdefImportRecoverableError(FormdefImportError):
    pass


class FormDefDoesNotExist(AttributeError):
    error_message = _('No such form: %s')

    def get_error_message(self):
        return self.error_message % self


class FormField:
    # only used to unpickle form fields from older (<200603) versions
    def __setstate__(self, dict):
        type = dict['type']
        self.real_field = fields.get_field_class_by_type(type)(**dict)


def lax_int(s, default=-1):
    try:
        return int(s)
    except (ValueError, TypeError):
        return default


class FormDefForm(Form):
    ERROR_NOTICE = _('There were errors processing the form and you cannot go to the next page.')

    def __init__(self):
        super().__init__(enctype='multipart/form-data', use_tokens=False)
        self.attrs['data-warn-on-unsaved-content'] = 'true'

    def _render_error_notice_content(self, errors):
        t = TemplateIO(html=True)
        t += super()._render_error_notice_content(errors)
        widget_with_errors = []
        for widget in self.get_all_widgets():
            if hasattr(widget, 'field') and widget.has_error() and not getattr(widget, 'is_hidden', False):
                widget_with_errors.append(widget)
        if widget_with_errors:
            t += htmltext('<p id="field-error-links">')
            t += str(
                ngettext(
                    'The following field has an error:',
                    'The following fields have an error:',
                    len(widget_with_errors),
                )
            )
            t += ' '
            for i, widget in enumerate(widget_with_errors):
                t += htmltext('<a data-field-name="%s" href="#form_label_%s">%s</a>') % (
                    widget.get_name_for_id(),
                    widget.get_name_for_id(),
                    widget.title,
                )
                if i < len(widget_with_errors) - 1:
                    t += htmltext('<span class="list-comma">%s</span>') % _(', ')
            t += htmltext('</p>')
        return t.getvalue()


class FormDefBase(StoredObjectMixin):
    # noqa pylint: disable=too-many-public-methods
    _names = 'formdefs'
    backoffice_class = 'wcs.admin.forms.FormDefPage'
    data_sql_prefix = 'formdata'
    pickle_module_name = 'formdef'
    xml_root_node = 'formdef'
    backoffice_section = 'forms'
    verbose_name = _('Form')
    verbose_name_plural = _('Forms')
    item_name = pgettext_lazy('item', 'form')
    item_name_plural = pgettext_lazy('item', 'forms')
    fields_count_total_soft_limit = 200
    fields_count_total_hard_limit = 400

    name = None
    description = None
    keywords = None
    url_name = None
    table_name = None  # for SQL only
    fields = None
    category_id = None
    workflow_id = None
    workflow_options = None
    workflow_roles = None
    roles = None
    required_authentication_contexts = None
    backoffice_submission_roles = None
    discussion = False
    confirmation = True
    detailed_emails = True
    disabled = False
    only_allow_one = False
    enable_tracking_codes = False
    tracking_code_verify_fields = None
    disabled_redirection = None
    always_advertise = False
    has_captcha = False
    skip_from_360_view = False
    management_sidebar_items = {'__default__'}
    submission_sidebar_items = {'__default__'}
    include_download_all_button = False
    appearance_keywords = None
    digest_templates = None
    lateral_template = None
    submission_lateral_template = None
    id_template = None
    drafts_lifespan = None
    drafts_max_per_user = None
    user_support = None
    submission_user_association = 'any'
    documentation = None
    workflow_migrations = None
    old_but_non_anonymised_warning = None

    geolocations = None
    history_pane_default_mode = 'expanded'
    sql_integrity_errors = None

    # store reverse relations
    reverse_relations = None

    # store fields in a separate pickle chunk
    lightweight = True

    # prefixes for formdata variables
    var_prefixes = ['form']

    # users are allowed to access formdata where they're submitter.
    user_allowed_to_access_own_data = True

    submission_user_association_available_options = ['any', 'any-required', 'roles']

    # this controls the availability of the "only in frontoffice" required mode
    may_appear_in_frontoffice = True

    # declarations for serialization
    TEXT_ATTRIBUTES = [
        'name',
        'url_name',
        'description',
        'keywords',
        'publication_date',
        'expiration_date',
        'disabled_redirection',
        'appearance_keywords',
        'lateral_template',
        'submission_lateral_template',
        'id_template',
        'drafts_lifespan',
        'drafts_max_per_user',
        'user_support',
        'documentation',
        'submission_user_association',
        'history_pane_default_mode',
        'old_but_non_anonymised_warning',
    ]
    BOOLEAN_ATTRIBUTES = [
        'discussion',
        'detailed_emails',
        'disabled',
        'only_allow_one',
        'enable_tracking_codes',
        'confirmation',
        'always_advertise',
        'has_captcha',
        'skip_from_360_view',
    ]

    category_class = 'wcs.categories.Category'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields = []

    def __eq__(self, other):
        return bool(
            isinstance(other, self.__class__)
            and self.xml_root_node == other.xml_root_node
            and self.id == other.id
        )

    def __hash__(self):
        # allow creating set of formdefs
        return hash((type(self), self.xml_root_node, self.id))

    def migrate(self):
        changed = False

        if self.__dict__.get('fields') is Ellipsis:
            # don't run migration on lightweight objects
            return

        if isinstance(self.category_id, int):
            self.category_id = str(self.category_id)
            changed = True

        if isinstance(self.workflow_id, int):
            self.workflow_id = str(self.workflow_id)
            changed = True

        if self.roles:
            for role in self.roles:
                if isinstance(role, int):
                    self.roles = [str(x) for x in self.roles]
                    changed = True
                    break

        if self.workflow_roles:
            workflow_roles_list = self.workflow_roles.items()
            for role_id in self.workflow_roles.values():
                if isinstance(role_id, int):
                    self.workflow_roles = {x: str(y) for x, y in workflow_roles_list}
                    changed = True
                    break

        if self.include_download_all_button:  # 2023-12-30
            self.management_sidebar_items = self.get_default_management_sidebar_items()
            self.management_sidebar_items.add('download-files')
            self.include_download_all_button = False
            changed = True

        for f in self.fields or []:
            changed |= f.migrate()

        if changed:
            self.store(comment=_('Automatic update'), snapshot_store_user=False)

    @classmethod
    def remove_object(cls, id):
        id = str(id)
        super().remove_object(id)
        from wcs.formdef import FormDef
        from wcs.testdef import TestDef

        from . import sql

        sql.SearchableFormDef.update(removed_obj_type=cls.xml_root_node, removed_obj_id=id)
        if cls is FormDef:
            # recreate global views so they don't reference formdata from
            # deleted formefs
            conn, cur = sql.get_connection_and_cursor()
            with sql.atomic():
                sql.clean_global_views(conn, cur)
            cur.close()

        for testdef in TestDef.select([Equal('object_type', cls.get_table_name()), Equal('object_id', id)]):
            TestDef.remove_object(testdef.id)

    def get_default_management_sidebar_items(self):
        return {
            'general',
            'submission-context',
            'user',
            'geolocation',
            'custom-template',
            'pending-forms',
        }

    def get_management_sidebar_available_items(self):
        return [
            ('general', _('General Information')),
            ('download-files', _('Button to download all files')),
            ('submission-context', _('Submission context')),
            ('user', _('Associated User')),
            ('geolocation', _('Geolocation')),
            ('custom-template', _('Custom template')),
            ('pending-forms', _('User Pending Forms')),
        ]

    def management_sidebar_items_labels(self):
        # return ordered labels
        management_sidebar_items = self.get_management_sidebar_items()
        for key, label in self.get_management_sidebar_available_items():
            if key in management_sidebar_items:
                yield label

    def get_management_sidebar_items(self):
        if self.management_sidebar_items == {'__default__'}:
            return self.get_default_management_sidebar_items()
        return self.management_sidebar_items or []

    def get_default_submission_sidebar_items(self):
        return {
            'general',
            'submission-context',
            'user',
            'custom-template',
        }

    def get_submission_sidebar_available_items(self):
        return [
            ('general', _('General Information')),
            ('submission-context', _('Submission context')),
            ('user', _('Associated User')),
            ('custom-template', _('Custom template')),
        ]

    def submission_sidebar_items_labels(self):
        # return ordered labels
        submission_sidebar_items = self.get_submission_sidebar_items()
        for key, label in self.get_submission_sidebar_available_items():
            if key in submission_sidebar_items:
                yield label

    def get_submission_sidebar_items(self):
        if self.submission_sidebar_items == {'__default__'}:
            return self.get_default_submission_sidebar_items()
        return self.submission_sidebar_items or []

    def get_old_but_non_anonymised_warning_delay(self):
        if self.old_but_non_anonymised_warning is None:
            return int(get_publisher().get_site_option('default-old-but-non-anonymised-warning-delay'))
        return self.old_but_non_anonymised_warning

    @property
    def use_test_data_class(self):
        return bool(self in (get_publisher().test_formdefs or []))

    @property
    def data_class_name(self):
        if self.use_test_data_class:
            return '_test_wcs_%s' % self.url_name.title()

        return '_wcs_%s' % self.url_name.title()

    def data_class(self, mode=None):
        if 'formdef' not in sys.modules:
            sys.modules['formdef'] = sys.modules[__name__]
        if hasattr(sys.modules['formdef'], self.data_class_name):
            data_class = getattr(sys.modules['formdef'], self.data_class_name)
            # only use existing data class if it has a reference to this actual
            # formdef
            if data_class._formdef is self:
                return data_class

        from . import sql

        if self.use_test_data_class:
            table_name = sql.get_formdef_test_table_name(self)
        else:
            table_name = sql.get_formdef_table_name(self)

        cls = type(self.data_class_name, (sql.SqlFormData,), {'_formdef': self, '_table_name': table_name})
        setattr(sys.modules['formdef'], self.data_class_name, cls)
        setattr(sys.modules['wcs.formdef'], self.data_class_name, cls)

        return cls

    def get_new_field_id(self):
        return str(uuid.uuid4())

    def get_order_by(self, order_by):
        if not order_by:
            return order_by
        direction = ''
        if order_by.startswith('-'):
            order_by = order_by[1:]
            direction = '-'
        for field in self.iter_fields(include_block_fields=True):
            if getattr(field, 'block_field', None):
                if field.key == 'items':
                    # not yet
                    continue
            if order_by not in [field.contextual_varname, 'f%s' % field.contextual_id]:
                continue
            if field.contextual_varname == order_by:
                order_by = 'f%s' % field.contextual_id
            if getattr(field, 'block_field', None) and 'f%s' % field.contextual_id == order_by:
                # field of block field, sort on the first element
                order_by = "f%s->'data'->0->>'%s%s'" % (
                    field.block_field.id,
                    field.id,
                    '_display' if field.store_display_value else '',
                )
            elif field.store_display_value:
                order_by += '_display'
            break
        if order_by == 'digest':
            order_by = "digests->>'default'"
        order_by = '%s%s' % (direction, order_by)
        if order_by == 'criticality_level':
            order_by = [order_by, 'receipt_time']
        elif order_by == '-criticality_level':
            order_by = [order_by, '-receipt_time']
        return order_by

    def has_admin_access(self, user):
        # return True if user 1/ is global administrator for this type of object, or
        # 2/ has one of the management roles defined in its category.
        if get_publisher().get_backoffice_root().is_global_accessible(self.backoffice_section):
            return True
        if not user:
            return False
        if not self.category_id:
            return False
        management_roles = {x.id for x in getattr(self.category, 'management_roles') or []}
        user_roles = set(user.get_roles())
        return management_roles.intersection(user_roles)

    def has_creation_permission(self, user):
        if not self.backoffice_submission_roles or not user:
            return False
        for role in user.get_roles():
            if role in self.backoffice_submission_roles:
                return True
        return False

    def store(
        self,
        comment=None,
        *,
        snapshot_store_user=True,
        application=None,
        application_ignore_change=False,
        **kwargs,
    ):
        assert not self.is_readonly()
        if self.url_name is None:
            # set url name if it's not yet there
            self.url_name = self.get_new_slug()

        object_only = kwargs.pop('object_only', False)

        if not object_only:
            self.update_relations()

        self.storage_store(**kwargs)
        if object_only:
            return

        if get_publisher().snapshot_class:
            get_publisher().snapshot_class.snap(
                instance=self,
                comment=comment,
                store_user=snapshot_store_user,
                application=application,
                application_ignore_change=application_ignore_change,
            )

        if get_publisher().has_postgresql_config():
            self.update_storage()
            self.store_related_custom_views()
            self.update_searchable_formdefs_table()
            self.update_category_reference()

    def update_category_reference(self):
        if getattr(self, '_onload_category_id', None) != self.category_id:
            from . import sql

            sql.update_global_view_formdef_category(self)
            self._onload_category_id = self.category_id

    def has_captcha_enabled(self):
        return self.has_captcha and get_publisher().has_site_option('formdef-captcha-option')

    def update_storage(self):
        from . import sql

        actions = sql.do_formdef_tables(self, rebuild_views=True, rebuild_global_views=True)
        if actions:
            cls = self.data_class()
            for action in actions:
                getattr(cls, action)()

    def update_searchable_formdefs_table(self):
        from . import sql

        sql.SearchableFormDef.update(obj=self)

    def update_relations(self):
        from wcs.carddef import CardDef
        from wcs.formdef import FormDef

        self_ref = '%s:%s' % (self.xml_root_node, self.url_name)
        self_relations_by_ref = self.build_relations_by_ref()
        reverse_relations = []

        # cross each formdef and cardef and check relations
        for objdef in itertools.chain(
            FormDef.select(ignore_errors=True, ignore_migration=True),
            CardDef.select(ignore_errors=True, ignore_migration=True),
        ):
            objdef_ref = '%s:%s' % (objdef.xml_root_node, objdef.url_name)
            if objdef.xml_root_node == self.xml_root_node and objdef.id == self.id:
                # don't build relations twice
                objdef_relations_by_ref = self_relations_by_ref
            else:
                objdef_relations_by_ref = objdef.build_relations_by_ref()
            reverse_relations += objdef_relations_by_ref.get(self_ref, [])

            old_objdef_reverse_relations = copy.deepcopy(objdef.reverse_relations)
            # remove relations with self in objdef's reverse_relations
            new_objdef_reverse_relations = [
                r for r in (objdef.reverse_relations or []) if r['obj'] != self_ref
            ]
            # and update objdef's reverse_relations from self_relations_by_ref
            new_objdef_reverse_relations += self_relations_by_ref.get(objdef_ref, [])
            # sort objectdef's reverse_relations
            new_objdef_reverse_relations = sorted(
                new_objdef_reverse_relations, key=itemgetter('obj', 'varname', 'type')
            )
            if old_objdef_reverse_relations != new_objdef_reverse_relations:
                objdef.reverse_relations = new_objdef_reverse_relations
                objdef.store(object_only=True)
        # sort self's reverse_relations and set
        self.reverse_relations = sorted(reverse_relations, key=itemgetter('obj', 'varname', 'type'))

    def build_relations_by_ref(self):
        # build relations to other carddefs, to be stored in some object reverse field
        self_ref = '%s:%s' % (self.xml_root_node, self.url_name)
        relations_by_ref = collections.defaultdict(list)

        def _check_field(field):
            data_source = getattr(field, 'data_source', None)
            if not data_source or not data_source.get('type', '').startswith('carddef:'):
                return
            # reverse relation of data_source['type'] to this object
            obj_ref = ':'.join(data_source['type'].split(':')[:2])  # remove possible custom-view
            relations_by_ref[obj_ref].append(
                {
                    'varname': field.contextual_varname or '',
                    'type': field.key,
                    'obj': self_ref,
                }
            )

        for field in self.iter_fields(include_block_fields=True):
            if field.key in ['item', 'items', 'computed']:
                _check_field(field)

        # remove duplicated items
        return {
            k: list(map(dict, {tuple(sorted(d.items())) for d in v})) for k, v in relations_by_ref.items()
        }

    def store_related_custom_views(self):
        for view in getattr(self, '_custom_views', []):
            if not view.id:
                existing_views = get_publisher().custom_view_class.select(
                    [
                        Equal('formdef_type', self.xml_root_node),
                        Equal('formdef_id', str(self.id)),
                        Equal('visibility', view.visibility),
                        Equal('slug', view.slug),
                    ]
                )
                if existing_views:
                    view.id = existing_views[0].id
            view.formdef = self
            view.store()

    def get_all_fields(self):
        return (self.fields or []) + self.workflow.get_backoffice_fields()

    def get_all_fields_dict(self):
        return {x.id: x for x in self.get_all_fields()}

    def get_total_count_data_fields(self):
        count = len([x for x in self.fields or [] if not x.is_no_data_field and not x.key == 'block'])
        for field in self.fields or []:
            if not field.key == 'block':
                continue
            try:
                count += len([x for x in field.block.fields or [] if not x.is_no_data_field]) * (
                    lax_int(field.default_items_count, 1) or 1
                )
            except KeyError:
                continue
        return count

    def iter_fields(self, include_block_fields=False, with_backoffice_fields=True, with_no_data_fields=True):
        def _iter_fields(fields, block_field=None):
            latest_page_field = None
            for field in fields:
                if field.key == 'page':
                    latest_page_field = field
                elif getattr(field, 'is_backoffice_field', False):
                    latest_page_field = None
                if with_no_data_fields is False and field.is_no_data_field:
                    continue
                # add contextual_id/contextual_varname attributes
                # they are id/varname for normal fields
                # but in case of blocks they are concatenation of block id/varname + field id/varname
                field.parent_page_field = latest_page_field
                field.contextual_id = field.id
                field.contextual_varname = None
                if block_field:
                    field.block_field = block_field
                    field.parent_page_field = block_field.parent_page_field
                    field.contextual_id = '%s-%s' % (field.block_field.id, field.id)
                    field.is_backoffice_field = getattr(block_field, 'is_backoffice_field', False)
                    if field.varname and field.block_field.varname:
                        field.contextual_varname = '%s_%s' % (
                            field.block_field.varname,
                            field.varname,
                        )
                else:
                    field.contextual_varname = field.varname
                yield field
                if field.key == 'block' and include_block_fields:
                    try:
                        field.block  # load block
                    except KeyError:
                        # blockdef not found
                        continue
                    yield from _iter_fields(field.block.fields, block_field=field)
                    field._block = None  # reset cache

        if with_backoffice_fields:
            fields = self.get_all_fields()
        else:
            fields = self.fields or []
        yield from _iter_fields(fields)

    def get_data_fields(self):
        return [field for field in self.fields or [] if not field.is_no_data_field]

    def get_widget_fields(self):
        return [field for field in self.fields or [] if isinstance(field, fields.WidgetField)]

    @property
    def default_digest_template(self):
        return (self.digest_templates or {}).get('default')

    def get_category(self):
        if self.category_id:
            category_class = import_string(self.category_class)
            try:
                return category_class.get(self.category_id)
            except KeyError:
                return None
        else:
            return None

    def set_category(self, category):
        if category:
            self.category_id = category.id
        elif self.category_id:
            self.category_id = None

    category = property(get_category, set_category)

    def get_drafts_lifespan(self):
        return int(self.drafts_lifespan or DRAFTS_DEFAULT_LIFESPAN)

    def get_drafts_max_per_user(self):
        return int(self.drafts_max_per_user or DRAFTS_DEFAULT_MAX_PER_USER)

    _workflow = None

    def get_workflow(self):
        if self._workflow and (
            str(self._workflow.id) == str(self.workflow_id)
            or (self.workflow_id is None and self._workflow.id in ('_carddef_default', '_default'))
        ):
            return self._workflow
        from wcs.workflows import Workflow

        if self.workflow_id:
            try:
                self._workflow = Workflow.get(self.workflow_id)
            except KeyError:
                return Workflow.get_unknown_workflow()
            return self._workflow

        self._workflow = self.get_default_workflow()
        return self._workflow

    @classmethod
    def get_default_workflow(cls):
        from wcs.workflows import Workflow

        return Workflow.get_default_workflow()

    def set_workflow(self, workflow):
        if workflow and workflow.id not in ['_carddef_default', '_default']:
            self.workflow_id = str(workflow.id)
            self._workflow = workflow
        elif self.workflow_id:
            self.workflow_id = None
            self._workflow = None

    workflow = property(get_workflow, set_workflow)

    def get_dependencies(self):
        yield self.category
        if self.workflow_id and self.workflow.id not in ['_carddef_default', '_default']:
            yield self.workflow
        for field in self.fields or []:
            yield from field.get_dependencies()
        role_class = get_publisher().role_class
        for role_id in itertools.chain(self.roles or [], self.backoffice_submission_roles or []):
            yield role_class.get(role_id, ignore_errors=True)
        for role_id in (self.workflow_roles or {}).values():
            yield role_class.get(role_id, ignore_errors=True)
        for view in get_publisher().custom_view_class.select_shared_for_formdef(formdef=self):
            yield from view.get_dependencies()
        for template in list((self.digest_templates or {}).values()) + [
            self.lateral_template,
            self.submission_lateral_template,
        ]:
            yield from get_dependencies_from_template(template)

        from .testdef import TestDef

        for testdef in TestDef.select_for_objectdef(self):
            yield from testdef.get_dependencies()

    @property
    def keywords_list(self):
        if not self.keywords:
            return []
        return [x.strip() for x in self.keywords.split(',')]

    @property
    def appearance_keywords_list(self):
        if not get_publisher().has_site_option('formdef-appearance-keywords'):
            return []
        if not self.appearance_keywords:
            return []
        return [x.strip() for x in self.appearance_keywords.split()]

    def get_variable_options(self, option_prefix='form_option_'):
        variables = {}
        if not self.workflow.variables_formdef:
            return variables
        workflow_options = self.workflow_options or {}
        for field in self.workflow.variables_formdef.fields:
            if not field.varname:
                continue
            option_name = option_prefix + field.varname
            variables[option_name] = getattr(field, 'default_value', None)
            if workflow_options.get(field.varname) is not None:
                variables[option_name] = workflow_options.get(field.varname)
                if hasattr(field, 'get_json_value') and not field.key == 'file':
                    # (file values will be properly serialized later on)
                    variables[option_name] = field.get_json_value(variables[option_name])
            if field.store_display_value:
                if '%s_display' % field.varname in workflow_options:
                    variables[option_name + '_raw'] = variables[option_name]
                    variables[option_name] = workflow_options.get('%s_display' % field.varname)
            if field.store_structured_value:
                if '%s_structured' % field.varname in workflow_options:
                    variables[option_name + '_structured'] = workflow_options.get(
                        '%s_structured' % field.varname
                    )
        return variables

    def get_variable_options_for_form(self):
        variables = {}
        if not self.workflow.variables_formdef:
            return variables
        if not self.workflow_options:
            return {}
        for field in self.workflow.variables_formdef.fields:
            if not field.varname:
                continue
            variables[str(field.id)] = self.workflow_options.get(field.varname)
        return variables

    def set_variable_options(self, data):
        variables = {}
        for field in self.workflow.variables_formdef.fields:
            if not field.varname:
                continue
            variables[field.varname] = data.get(field.id)
            if field.store_display_value:
                variables[field.varname + '_display'] = data.get(field.id + '_display')
            if field.store_structured_value:
                variables[field.varname + '_structured'] = data.get(field.id + '_structured')
        if not self.workflow_options:
            self.workflow_options = {}
        self.workflow_options.update(variables)

    @classmethod
    def get_by_urlname(cls, url_name, ignore_migration=False, ignore_errors=False, use_cache=False):
        return cls.get_on_index(
            url_name,
            'slug',
            ignore_migration=ignore_migration,
            ignore_errors=ignore_errors,
            use_cache=use_cache,
        )

    get_by_slug = get_by_urlname

    @property
    def slug(self):
        return self.url_name

    @slug.setter
    def slug(self, value):
        self.url_name = value

    def get_url(self, backoffice=False, preview=False, include_category=False, language=None):
        if backoffice:
            base_url = get_publisher().get_backoffice_url() + '/management'
        elif preview:
            base_url = get_publisher().get_frontoffice_url() + '/preview'
        else:
            base_url = get_publisher().get_frontoffice_url()
            if language and get_publisher().has_i18n_enabled():
                base_url += '/' + language
            if include_category and self.category_id:
                return '%s/%s/%s/' % (base_url, self.category.slug, self.url_name)
        return '%s/%s/' % (base_url, self.url_name)

    def get_api_url(self):
        base_url = get_publisher().get_frontoffice_url()
        return '%s/api/forms/%s/' % (base_url, self.url_name)

    def get_admin_url(self):
        base_url = get_publisher().get_backoffice_url()
        return '%s/forms/%s/' % (base_url, self.id)

    def get_backoffice_url(self):
        return self.get_url(backoffice=True)

    def get_preview_url(self):
        return self.get_url(preview=True)

    def get_field_admin_url(self, field):
        return self.get_admin_url() + 'fields/%s/' % field.id

    def get_submission_url(self, backoffice=False):
        if backoffice:
            return self.get_backoffice_submission_url()
        return self.get_url()

    def get_backoffice_submission_url(self):
        base_url = get_publisher().get_backoffice_url() + '/submission'
        return '%s/%s/' % (base_url, self.url_name)

    def get_display_id_format(self):
        return self.id_template or '{{formdef_id}}-{{form_number_raw}}'

    def get_by_id_criteria(self, value):
        if self.id_template:
            return Equal('id_display', str(value))
        try:
            if int(value) >= 2**31:
                # out of range for postgresql integer type; would raise DataError.
                raise OverflowError
        except ValueError:
            # value not an integer, it could be id_display
            return Equal('id_display', str(value))
        return Equal('id', value)

    def get_by_multiple_id_criteria(self, values, operator='eq'):
        assert values, 'get_by_multiple_id_criteria() called with empty values'
        from wcs.sql_criterias import NotContains

        criteria_class = {'eq': Contains, 'ne': NotContains}[operator]
        if self.id_template:
            return criteria_class('id_display', values)
        try:
            if int(values[0]) >= 2**31:
                # out of range for postgresql integer type; would raise DataError.
                raise ValueError
        except ValueError:
            # value not an integer, it could be id_display
            return criteria_class('id_display', values)
        return criteria_class('id', values)

    def get_submission_lateral_block(self):
        context = get_publisher().substitutions.get_context_variables(mode='lazy')
        if self.submission_lateral_template is None:
            new_value = None
        else:
            try:
                new_value = Template(
                    self.submission_lateral_template, autoescape=False, raises=True, record_errors=False
                ).render(context)
            except Exception as e:
                get_publisher().record_error(
                    _('Could not render submission lateral template (%s)' % e),
                    formdef=self,
                    exception=e,
                )
                return None

        return new_value

    def create_form(self, page=None, displayed_fields=None, transient_formdata=None):
        form = FormDefForm()
        if self.appearance_keywords:
            form.attrs['class'] = 'quixote %s' % self.appearance_keywords
        if self.keywords:
            form.attrs['data-keywords'] = ' '.join(self.keywords_list)
        self.add_fields_to_form(
            form, page=page, displayed_fields=displayed_fields, transient_formdata=transient_formdata
        )
        return form

    def get_computed_fields_from_page(self, page):
        on_page = page is None
        for field in self.fields:
            if field.key == 'page':
                if on_page:
                    break
                if page.id == field.id:
                    on_page = True
                continue
            if not on_page:
                continue
            if field.key == 'computed':
                yield field

    @add_timing_group(_('add fields to form (add_fields_to_form)'))
    def add_fields_to_form(
        self,
        form,
        page=None,
        displayed_fields=None,
        form_data=None,  # a dictionary, to fill fields
        transient_formdata=None,
    ):  # a FormData
        on_page = page is None
        hidden_varnames = set()

        # attribute to tell BlockSubWidget it has to update its current data
        # (see [HAS_TRANSIENT_DATA] in blocks.py)
        get_publisher().has_transient_formdata = bool(transient_formdata)

        for field in self.fields:
            add_timing_mark(
                _('field "%(label)s" (%(identifier)s)')
                % {'label': field.get_type_label(), 'identifier': field.varname or field.id},
                url=self.get_field_admin_url(field),
            )
            field.formdef = self
            if field.key == 'page':
                if on_page:
                    break
                if page.id == field.id:
                    on_page = True
                continue
            if not on_page:
                continue
            visible = field.is_visible(form_data, self)
            if not visible:
                if not getattr(form, 'has_live_form_support', True) or not field.has_live_conditions(
                    self, hidden_varnames=hidden_varnames
                ):
                    # ignore field.varname when checking later conditions for liveness
                    if field.varname:
                        hidden_varnames.add(field.varname)
                    # no live conditions so field can be skipped
                    continue
            if isinstance(displayed_fields, list):
                displayed_fields.append(field)
            value = None
            if form_data:
                value = form_data.get(field.id)
            if not field.add_to_form:
                continue
            with ExitStack() if visible else get_publisher().disable_logged_errors():
                widget = field.add_to_form(form, value)
            widget.is_hidden = not (visible)
            widget.field = field
            if transient_formdata and not widget.is_hidden:
                transient_formdata.data.update(self.get_field_data(field, widget))
                # invalidate cache as comment fields (and other things?) may
                # have accessed variables in non-lazy mode and caused a cache
                # with now-obsolete values.
                get_publisher().substitutions.invalidate_cache()
                widget._parsed = False
                widget.error = None

        get_publisher().has_transient_formdata = False

    def get_page(self, page_no):
        return [x for x in self.fields if x.key == 'page'][page_no]

    def page_count(self):
        return len([x for x in self.fields if x.key == 'page']) or 1

    def create_view_form(self, dict=None, use_tokens=True, visible=True):
        dict = dict or {}
        form = Form(enctype='multipart/form-data', use_tokens=use_tokens)
        if not visible:
            form.attrs['style'] = 'display: none;'
        if self.keywords:
            form.attrs['data-keywords'] = ' '.join(self.keywords_list)

        form_fields = self.fields
        if form_fields and form_fields[0].key != 'page':
            # add fake initial page in case it's missing
            form_fields = [fields.PageField(label='', type='page')] + form_fields

        # 1st pass to group fields on different pages
        pages = []
        current_page = {}
        for field in form_fields:
            if field.key == 'page':
                current_page = {'page': field, 'fields': []}
                current_page['disabled'] = not field.is_visible(dict, self)
                pages.append(current_page)
                continue

            if current_page['disabled']:
                continue

            if field.key == 'title' and (
                not current_page['fields'] and current_page['page'].label == field.label
            ):
                # don't include first title of a page if that title has the
                # same text as the page.
                continue

            if field.key in ('title', 'subtitle', 'comment') and not field.include_in_validation_page:
                # don't render field that wouldn't be displayed.
                continue

            if not field.is_visible(dict, self):
                continue

            current_page['fields'].append(field)

        # 2nd pass to create view form
        for page in pages:
            visible_contents = False
            if page['fields'] and any(x.include_in_validation_page for x in page['fields']):
                visible_contents = True
                form.widgets.append(HtmlWidget(htmltext('<div class="page">')))
                if page['page'].label:
                    form.widgets.append(HtmlWidget(htmltext('<h3>%s</h3>') % page['page'].label))
                form.widgets.append(HtmlWidget(htmltext('<div>')))

            for field in page['fields']:
                value = dict.get(field.id)
                if not field.add_to_view_form:
                    continue
                if not field.include_in_validation_page:
                    form.widgets.append(HtmlWidget(htmltext('<div style="display: none;">')))
                    field.add_to_view_form(form, value)
                    form.widgets.append(HtmlWidget(htmltext('</div>')))
                else:
                    field.add_to_view_form(form, value)

            if visible_contents:
                form.widgets.append(HtmlWidget(htmltext('</div></div>')))

        return form

    def set_live_condition_sources(self, form, fields):
        live_condition_fields = {}

        fields_ids = {str(x.id) for x in fields}
        block_fields = []

        for field in self.iter_fields(include_block_fields=True):
            block_field = getattr(field, 'block_field', None)
            if (block_field and str(field.block_field.id) not in fields_ids) or (
                not block_field and str(field.id) not in fields_ids
            ):
                continue

            if block_field:
                block_fields.append(field)

            if field.condition:
                field.varnames = field.get_condition_varnames(formdef=self)
                for varname in field.varnames:
                    if varname not in live_condition_fields:
                        live_condition_fields[varname] = []
                    live_condition_fields[varname].append(field)

            varnames = []

            if field.key in ('item', 'items', 'time-range') and field.data_source:
                data_source = data_sources.get_object(field.data_source)
                if data_source.type in ('json', 'jsonvalue', 'geojson') or data_source.type.startswith(
                    'carddef:'
                ):
                    varnames.extend(data_source.get_referenced_varnames(formdef=self))
                    if block_field:
                        for varname in data_source.get_referenced_varnames(formdef=block_field.block):
                            varnames.append(f'{block_field.id} {varname}')

            if field.prefill and field.prefill.get('type') == 'string':
                varnames.extend(
                    field.get_referenced_varnames(formdef=self, value=field.prefill.get('value', ''))
                )

            if field.key == 'comment':
                varnames.extend(field.get_referenced_varnames(formdef=self, value=field.label))
                if block_field:
                    for varname in field.get_referenced_varnames(
                        formdef=block_field.block, value=field.label
                    ):
                        varnames.append(f'{block_field.id} {varname}')

            for varname in varnames:
                if varname not in live_condition_fields:
                    live_condition_fields[varname] = []
                live_condition_fields[varname].append(field)

        for field in fields + block_fields:
            if field.varname in live_condition_fields:
                widget = form.get_widget('f%s' % field.id)
                if widget:
                    widget.live_condition_source = True
                    widget.live_condition_fields = live_condition_fields[field.varname]
                elif field.key == 'computed':
                    field.live_condition_source = True
                    field.live_condition_fields = live_condition_fields[field.varname]
            block_field = getattr(field, 'block_field', None)
            if block_field:
                varname = f'{block_field.id} {field.varname}'
                if varname in live_condition_fields:
                    for widget in form.get_widget(f'f{block_field.id}').get_row_widgets(f'f{field.id}'):
                        if widget:
                            widget.live_condition_source = True
                            widget.live_condition_fields = live_condition_fields[varname]

    @classmethod
    def get_field_data(cls, field, widget, raise_on_error=False):
        d = {}
        d[field.id] = widget.parse()
        if isinstance(d.get(field.id), str) and field.convert_value_from_str:
            d[field.id] = field.convert_value_from_str(d[field.id])
        field.set_value(d, d[field.id], raise_on_error=raise_on_error)
        if getattr(widget, 'cleanup', None):
            widget.cleanup()
        return d

    def get_data(self, form, raise_on_error=False, pages=None):
        d = {}
        current_page_id = None
        for field in self.fields:
            if field.key == 'page':
                current_page_id = field.id
                continue
            if pages not in (None, [None]) and current_page_id not in [x.id for x in pages]:
                continue
            widget = form.get_widget('f%s' % field.id)
            if widget:
                d.update(self.get_field_data(field, widget, raise_on_error=raise_on_error))
            elif pages is not None:
                # reset
                d[f'{field.id}'] = d[f'{field.id}_display'] = d[f'{field.id}_structured'] = None
        return d

    def export_to_json(self, include_id=False, indent=None, with_user_fields=False, with_block_schemas=False):
        from wcs.carddef import CardDef

        root = {}
        root['name'] = self.name
        if include_id and self.id:
            root['id'] = str(self.id)
        if self.category:
            root['category'] = self.category.name
            root['category_id'] = str(self.category.id)
        if self.workflow:
            root['workflow'] = self.workflow.get_json_export_dict(include_id=include_id)

        more_attributes = ['tracking_code_verify_fields']

        for attribute in self.TEXT_ATTRIBUTES + self.BOOLEAN_ATTRIBUTES + more_attributes:
            if not hasattr(self, attribute):
                continue
            root[attribute] = getattr(self, attribute)
            if isinstance(root[attribute], time.struct_time):
                root[attribute] = time.strftime('%Y-%m-%dT%H:%M:%S', root[attribute])

        root['fields'] = []
        if self.fields:
            for field in self.fields:
                root['fields'].append(field.export_to_json(include_id=include_id))
                if with_block_schemas and field.key == 'block':
                    root['fields'][-1]['schema'] = field.block.get_json_export_dict(include_id=include_id)

        if self.geolocations:
            root['geolocations'] = self.geolocations.copy()

        if self.workflow_options:
            root['options'] = self.get_variable_options(option_prefix='')

        if self.required_authentication_contexts:
            root['required_authentication_contexts'] = self.required_authentication_contexts[:]

        for attr in ('management_sidebar_items', 'submission_sidebar_items'):
            if getattr(self, attr):
                root[attr] = sorted(getattr(self, attr))

        if isinstance(self, CardDef):
            all_carddefs = CardDef.select(ignore_errors=True)
            all_carddefs = [c for c in all_carddefs if c]
            all_carddefs_by_slug = {c.url_name: c for c in all_carddefs}

            def get_field_label(obj, field_varname):
                card_slug = obj.split(':')[1]
                carddef = all_carddefs_by_slug.get(card_slug)
                if not carddef:
                    return
                for field in carddef.iter_fields(include_block_fields=True):
                    if field.contextual_varname == field_varname:
                        if getattr(field, 'block_field', None):
                            return '%s - %s' % (field.block_field.label, field.label)
                        return field.label

            card_relations = []
            current_objdef_ref = '%s:%s' % (self.xml_root_node, self.url_name)
            for objdef, relations in self.build_relations_by_ref().items():
                if not objdef.startswith('carddef:'):
                    continue
                try:
                    CardDef.get_by_urlname(objdef.split(':')[1])
                except KeyError:
                    continue
                for relation in relations:
                    if not relation['varname']:
                        continue
                    card_relations.append(
                        {
                            'varname': relation['varname'],
                            'label': get_field_label(current_objdef_ref, relation['varname']),
                            'type': relation['type'],
                            'obj': objdef,
                            'reverse': False,
                        }
                    )

            for relation in self.reverse_relations or []:
                if not relation['obj'].startswith('carddef:'):
                    continue
                if not relation['varname']:
                    continue
                rel = relation.copy()
                rel.update(
                    {
                        'reverse': True,
                        'label': get_field_label(relation['obj'], relation['varname']),
                    }
                )
                card_relations.append(rel)

            root['relations'] = sorted(card_relations, key=itemgetter('varname'))
            if with_user_fields:
                root['user'] = {
                    'fields': [
                        {
                            'varname': 'name',
                            'label': _('Full name'),
                            'type': 'string',
                        },
                        {
                            'varname': 'email',
                            'label': _('Email'),
                            'type': 'email',
                        },
                    ]
                }
                user_formdef = get_publisher().user_class.get_formdef()
                if user_formdef:
                    root['user']['fields'] += [
                        {
                            'varname': f.varname or '',
                            'label': f.label,
                            'type': f.key,
                        }
                        for f in user_formdef.fields
                    ]

        return json.dumps(root, indent=indent, sort_keys=True, cls=JSONEncoder)

    @classmethod
    def import_from_json(cls, fd, include_id=False):
        formdef = cls()

        def unicode2str(v):
            if isinstance(v, dict):
                return {unicode2str(k): unicode2str(v) for k, v in v.items()}
            if isinstance(v, list):
                return [unicode2str(x) for x in v]
            if isinstance(v, str):
                return force_str(v)
            return v

        # we have to make sure all strings are str object, not unicode.
        value = unicode2str(json.load(fd))

        if include_id and 'id' in value:
            formdef.id = value.get('id')

        if include_id and 'category_id' in value:
            formdef.category_id = value.get('category_id')
        elif 'category' in value:
            category = value.get('category')
            for c in Category.select():
                if c.name == category:
                    formdef.category_id = c.id
                    break

        if include_id and 'workflow_id' in value:
            formdef.workflow_id = value.get('workflow_id')
        elif (
            include_id
            and 'workflow' in value
            and isinstance(value['workflow'], dict)
            and 'id' in value['workflow']
        ):
            formdef.workflow_id = value['workflow'].get('id')
        elif 'workflow' in value:
            if isinstance(value['workflow'], str):
                workflow = value.get('workflow')
            else:
                workflow = value['workflow'].get('name')
            from wcs.workflows import Workflow

            for w in Workflow.select():
                if w.name == workflow:
                    formdef.workflow_id = w.id
                    break

        more_attributes = ['tracking_code_verify_fields']
        for attribute in cls.TEXT_ATTRIBUTES + cls.BOOLEAN_ATTRIBUTES + more_attributes:
            if attribute in value:
                setattr(formdef, attribute, value.get(attribute))

        formdef.fields = []
        for i, field in enumerate(value.get('fields', [])):
            try:
                field_o = fields.get_field_class_by_type(field.get('type'))()
            except KeyError:
                raise FormdefImportError(_('Unknown field type'), details=field.findtext('type'))
            field_o.init_with_json(field, include_id=True)
            if not field_o.id:
                # this assumes all fields will have id, or none of them
                field_o.id = str(i)
            formdef.fields.append(field_o)

        if value.get('options'):
            formdef.workflow_options = value.get('options')
            for option_key, option_value in formdef.workflow_options.items():
                if isinstance(option_value, dict) and 'filename' in option_value:
                    new_value = PicklableUpload(
                        orig_filename=option_value['filename'],
                        content_type=option_value['content_type'],
                    )
                    new_value.receive([base64.decodebytes(force_bytes(option_value['content']))])
                    formdef.workflow_options[option_key] = new_value

        if value.get('geolocations'):
            formdef.geolocations = value.get('geolocations')

        if value.get('required_authentication_contexts'):
            formdef.required_authentication_contexts = [
                str(x) for x in value.get('required_authentication_contexts')
            ]

        for attr in ('management_sidebar_items', 'submission_sidebar_items'):
            if value.get(attr):
                setattr(formdef, attr, {str(x) for x in value.get(attr)})

        return formdef

    def export_to_xml(self, include_id=False, include_tests=False):
        root = ET.Element(self.xml_root_node)
        if include_id and self.id:
            root.attrib['id'] = str(self.id)
        for text_attribute in list(self.TEXT_ATTRIBUTES):
            if not hasattr(self, text_attribute) or not getattr(self, text_attribute):
                continue
            ET.SubElement(root, text_attribute).text = str(getattr(self, text_attribute))
        for boolean_attribute in self.BOOLEAN_ATTRIBUTES:
            if not hasattr(self, boolean_attribute):
                continue
            value = getattr(self, boolean_attribute)
            if value:
                value = 'true'
            else:
                value = 'false'
            ET.SubElement(root, boolean_attribute).text = value

        category_class = import_string(self.category_class)
        category_class.object_category_xml_export(self, root, include_id=include_id)

        workflow = None
        if self.workflow_id:
            from wcs.workflows import Workflow

            workflow = Workflow.get(self.workflow_id, ignore_errors=True, ignore_migration=True)
        if not workflow:
            workflow = self.get_default_workflow()
        elem = ET.SubElement(root, 'workflow')
        elem.text = workflow.name
        if workflow.slug:
            elem.attrib['slug'] = str(workflow.slug)
        if include_id:
            elem.attrib['workflow_id'] = str(workflow.id)

        if self.tracking_code_verify_fields is not None:
            verify_fields = ET.SubElement(root, 'tracking_code_verify_fields')
            for field_id in self.tracking_code_verify_fields:
                ET.SubElement(verify_fields, 'field_id').text = str(field_id)

        fields = ET.SubElement(root, 'fields')
        for field in self.fields or []:
            fields.append(field.export_to_xml(include_id=include_id))

        from wcs.workflows import get_role_name_and_slug

        def add_role_element(roles_root, role_id):
            if not role_id:
                return
            try:
                role_name, role_slug = get_role_name_and_slug(role_id)
            except KeyError:
                # skip broken/missing roles
                return
            sub = ET.SubElement(roles_root, 'role')
            if role_slug:
                sub.attrib['slug'] = role_slug
            if include_id:
                sub.attrib['role_id'] = str(role_id)
            sub.text = role_name
            return sub

        roles_elements = [
            ('roles', 'user-roles'),
            ('backoffice_submission_roles', 'backoffice-submission-roles'),
        ]
        for attr_name, node_name in roles_elements:
            if not getattr(self, attr_name, None):
                continue
            roles = ET.SubElement(root, node_name)
            for role_id in getattr(self, attr_name):
                add_role_element(roles, role_id)

        if self.workflow_roles:
            roles = ET.SubElement(root, 'roles')
            for role_key, role_id in self.workflow_roles.items():
                sub = add_role_element(roles, role_id)
                if sub is not None:
                    sub.attrib['role_key'] = role_key

        def make_xml_value(element, value):
            if isinstance(value, str):
                element.text = value
            elif hasattr(value, 'base_filename'):
                element.attrib['type'] = 'file'
                ET.SubElement(element, 'filename').text = value.base_filename
                ET.SubElement(element, 'content_type').text = value.content_type or 'application/octet-stream'
                ET.SubElement(element, 'content').text = force_str(base64.b64encode(value.get_content()))
            elif isinstance(value, time.struct_time):
                element.text = time.strftime('%Y-%m-%d', value)
                element.attrib['type'] = 'date'
            elif isinstance(value, bool):
                element.text = 'true' if value else 'false'
                element.attrib['type'] = 'bool'
            elif isinstance(value, int):
                element.attrib['type'] = 'int'
                element.text = str(value)
            elif isinstance(value, float):
                element.attrib['type'] = 'float'
                element.text = str(value)
            elif isinstance(value, decimal.Decimal):
                element.attrib['type'] = 'decimal'
                element.text = str(value)
            elif isinstance(value, (set, tuple, list)):
                element.attrib['type'] = 'list'
                for child_value in value:
                    sub_element = ET.SubElement(element, 'item')
                    make_xml_value(sub_element, child_value)
            elif isinstance(value, dict):
                element.attrib['type'] = 'dict'
                for child_key, child_value in value.items():
                    if re.match(r'^[\.\w_-]+$', child_key):  # only allow valid node names
                        sub_element = ET.SubElement(element, child_key)
                        make_xml_value(sub_element, child_value)
            else:
                assert value is None, 'option variable of unknown type (%s)' % type(value)

        options = ET.SubElement(root, 'options')
        for option in sorted(self.workflow_options or []):
            element = ET.SubElement(options, 'option')
            element.attrib['varname'] = option
            option_value = self.workflow_options.get(option)
            make_xml_value(element, option_value)

        custom_views_element = ET.SubElement(root, 'custom_views')
        if hasattr(self, '_custom_views'):
            # it has just been loaded, it's reexported as part as the overwrite
            # confirmation dialog, do not get custom views from database.
            custom_views = self._custom_views
        else:
            custom_views = []
            for view in get_publisher().custom_view_class.select_shared_for_formdef(formdef=self):
                custom_views.append(view)
        for view in custom_views:
            custom_view_node = view.export_to_xml(include_id=include_id)
            if custom_view_node is not None:
                custom_views_element.append(custom_view_node)

        geolocations = ET.SubElement(root, 'geolocations')
        for geoloc_key, geoloc_label in (self.geolocations or {}).items():
            element = ET.SubElement(geolocations, 'geolocation')
            element.attrib['key'] = geoloc_key
            element.text = geoloc_label

        if self.required_authentication_contexts:
            element = ET.SubElement(root, 'required_authentication_contexts')
            for auth_context in self.required_authentication_contexts:
                ET.SubElement(element, 'method').text = force_str(auth_context)

        for attr in ('management_sidebar_items', 'submission_sidebar_items'):
            if getattr(self, attr):
                element = ET.SubElement(root, attr)
                for item in sorted(getattr(self, attr)):
                    ET.SubElement(element, 'item').text = force_str(item)

        if self.digest_templates:
            digest_templates = ET.SubElement(root, 'digest_templates')
            for key, value in sorted(self.digest_templates.items()):
                if not value:
                    continue
                sub = ET.SubElement(digest_templates, 'template')
                sub.attrib['key'] = key
                sub.text = value

        if self.workflow_migrations:
            workflow_migrations = ET.SubElement(root, 'workflow-migrations')
            for v in sorted(self.workflow_migrations.values(), key=lambda x: x['timestamp']):
                migration = ET.SubElement(workflow_migrations, 'migration')
                ET.SubElement(migration, 'timestamp').text = v['timestamp']
                migration.attrib['old-workflow'] = v['old_workflow']
                migration.attrib['new-workflow'] = v['new_workflow']
                migration.attrib['timestamp'] = v['timestamp']
                status_mapping = ET.SubElement(migration, 'status-mapping')
                for old_status, new_status in v['status_mapping'].items():
                    remap = ET.SubElement(status_mapping, 'map')
                    remap.attrib['old'] = old_status
                    remap.attrib['new'] = new_status

        if include_tests:
            from .testdef import TestDef

            testdefs = TestDef.select_for_objectdef(self)
            if testdefs:
                elem = ET.SubElement(root, 'testdefs')
                for testdef in testdefs:
                    elem.append(testdef.export_to_xml())
        return root

    def export_for_application(self):
        etree = self.export_to_xml(include_id=True, include_tests=True)
        ET.indent(etree)
        content = ET.tostring(etree)
        content_type = 'text/xml'
        return content, content_type

    def store_for_application(self):
        self.finish_tests_xml_import()

    @classmethod
    def import_from_xml(
        cls,
        fd,
        include_id=False,
        fix_on_error=False,
        check_datasources=True,
        check_deprecated=False,
        ignore_missing_dependencies=False,
    ):
        try:
            tree = ET.parse(fd)
        except Exception:
            raise ValueError()
        formdef = cls.import_from_xml_tree(
            tree,
            include_id=include_id,
            fix_on_error=fix_on_error,
            check_datasources=check_datasources,
            check_deprecated=check_deprecated,
            ignore_missing_dependencies=ignore_missing_dependencies,
        )

        if formdef.url_name:
            try:
                cls.get_by_urlname(formdef.url_name, ignore_migration=True)
            except KeyError:
                pass
            else:
                formdef._import_orig_slug = formdef.url_name
                formdef.url_name = formdef.get_new_slug()

        # check if all field id are unique
        known_field_ids = set()
        for field in formdef.fields:
            if field.id in known_field_ids:
                raise FormdefImportRecoverableError(_('Duplicated field identifiers'))
            known_field_ids.add(field.id)

        return formdef

    @classmethod
    def import_from_xml_tree(
        cls,
        tree,
        include_id=False,
        fix_on_error=False,
        snapshot=False,
        check_datasources=True,
        check_deprecated=False,
        ignore_missing_dependencies=False,
    ):
        from wcs.backoffice.deprecations import DeprecatedElementsDetected, DeprecationsScan
        from wcs.carddef import CardDef

        formdef = cls()
        if tree.find('name') is None or not tree.find('name').text:
            raise FormdefImportError(_('Missing name'))

        # if the tree we get is actually a ElementTree for real, we get its
        # root element and go on happily.
        if not ET.iselement(tree):
            tree = tree.getroot()

        if tree.tag != cls.xml_root_node:
            raise FormdefImportError(
                _('Provided XML file is invalid, it starts with a <%(seen)s> tag instead of <%(expected)s>')
                % {'seen': tree.tag, 'expected': cls.xml_root_node}
            )

        if include_id and tree.attrib.get('id'):
            formdef.id = tree.attrib.get('id')
        for text_attribute in list(cls.TEXT_ATTRIBUTES):
            value = tree.find(text_attribute)
            if value is None or value.text is None:
                continue
            setattr(formdef, text_attribute, xml_node_text(value))

        for boolean_attribute in cls.BOOLEAN_ATTRIBUTES:
            value = tree.find(boolean_attribute)
            if value is None:
                continue
            setattr(formdef, boolean_attribute, value.text == 'true')

        if formdef.old_but_non_anonymised_warning:
            formdef.old_but_non_anonymised_warning = int(formdef.old_but_non_anonymised_warning)

        formdef.fields = []
        unknown_field_types = set()
        unknown_fields_blocks = set()
        for i, field in enumerate(tree.find('fields')):
            field_type = field.findtext('type')
            if field_type == 'block':
                field_type = 'block:%s' % field.findtext('block_slug')
            try:
                field_o = fields.get_field_class_by_type(field_type)()
            except KeyError:
                if field_type.startswith('block:'):
                    unknown_fields_blocks.add(field_type.removeprefix('block:'))
                else:
                    unknown_field_types.add(field_type)
                continue
            field_o.init_with_xml(field, include_id=True)
            if field_type.startswith('block:'):
                field_o.block_slug = field_type.removeprefix('block:')
            if fix_on_error or not field_o.id:
                # this assumes all fields will have id, or none of them
                field_o.id = str(i + 1)
            formdef.fields.append(field_o)

        if tree.find('tracking_code_verify_fields') is not None:
            formdef.tracking_code_verify_fields = [
                xml_node_text(verify_field_id)
                for verify_field_id in tree.findall('tracking_code_verify_fields/field_id')
            ]

        formdef.workflow_options = {}

        def get_value_from_xml(element):
            if element.attrib.get('type') == 'int':
                return int(xml_node_text(element))
            if element.attrib.get('type') == 'float':
                return float(xml_node_text(element))
            if element.attrib.get('type') == 'decimal':
                return decimal.Decimal(xml_node_text(element))
            if element.attrib.get('type') == 'date':
                return time.strptime(element.text, '%Y-%m-%d')
            if element.attrib.get('type') == 'bool':
                return bool(element.text == 'true')
            if element.attrib.get('type') == 'file' or element.findall('filename'):
                value = PicklableUpload(
                    orig_filename=xml_node_text(element.find('filename')),
                    content_type=xml_node_text(element.find('content_type')),
                )
                value.receive([base64.decodebytes(force_bytes(xml_node_text(element.find('content'))))])
                return value
            if element.attrib.get('type') == 'list':
                return [get_value_from_xml(x) for x in element.findall('item')]
            if element.attrib.get('type') == 'dict':
                return {x.tag: get_value_from_xml(x) for x in element.findall('*')}
            if element.text:
                return xml_node_text(element)

        for option in tree.findall('options/option'):
            formdef.workflow_options[option.attrib.get('varname')] = get_value_from_xml(option)

        formdef._custom_views = []
        for view in tree.findall('custom_views/%s' % get_publisher().custom_view_class.xml_root_node):
            view_o = get_publisher().custom_view_class()
            view_o.init_with_xml(view, include_id=include_id)
            formdef._custom_views.append(view_o)

        category_class = import_string(cls.category_class)
        category_class.object_category_xml_import(formdef, tree, include_id=include_id)

        if tree.find('workflow') is not None:
            from wcs.workflows import Workflow

            workflow_node = tree.find('workflow')
            if include_id and workflow_node.attrib.get('workflow_id'):
                workflow_id = workflow_node.attrib.get('workflow_id')
                if Workflow.has_key(workflow_id):
                    formdef.workflow_id = workflow_id
            else:
                workflow_slug = workflow_node.attrib.get('slug')
                if workflow_slug:
                    formdef.workflow = Workflow.get_by_slug(workflow_slug)
                else:
                    workflow = xml_node_text(workflow_node)
                    for w in Workflow.select(ignore_errors=True, ignore_migration=True):
                        if w and w.name == workflow:
                            formdef.workflow_id = w.id
                            break

        roles_elements = [
            ('roles', 'user-roles'),
            ('backoffice_submission_roles', 'backoffice-submission-roles'),
        ]
        for attr_name, node_name in roles_elements:
            if tree.find(node_name) is None:
                continue
            roles_node = tree.find(node_name)
            roles = []
            setattr(formdef, attr_name, roles)
            for child in roles_node:
                role_id = get_publisher().role_class.get_role_by_node(child, include_id=include_id)
                if role_id:
                    roles.append(role_id)

        if tree.find('roles') is not None:
            roles_node = tree.find('roles')
            formdef.workflow_roles = {}
            for child in roles_node:
                role_key = child.attrib['role_key']
                role_id = get_publisher().role_class.get_role_by_node(child, include_id=include_id)
                formdef.workflow_roles[role_key] = role_id

        if tree.find('geolocations') is not None:
            geolocations_node = tree.find('geolocations')
            formdef.geolocations = {}
            for child in geolocations_node:
                geoloc_key = child.attrib['key']
                geoloc_value = xml_node_text(child)
                formdef.geolocations[geoloc_key] = geoloc_value

        if tree.find('required_authentication_contexts') is not None:
            node = tree.find('required_authentication_contexts')
            formdef.required_authentication_contexts = []
            for child in node:
                formdef.required_authentication_contexts.append(str(child.text))

        for attr in ('management_sidebar_items', 'submission_sidebar_items'):
            if tree.find(attr) is not None:
                node = tree.find(attr)
                attr_value = set()
                setattr(formdef, attr, attr_value)
                for child in node:
                    attr_value.add(str(child.text))

        if tree.find('digest_templates') is not None:
            digest_templates_node = tree.find('digest_templates')
            formdef.digest_templates = {}
            for child in digest_templates_node:
                key = child.attrib['key']
                value = xml_node_text(child)
                formdef.digest_templates[key] = value

        if tree.find('workflow-migrations') is not None:
            workflow_migrations = tree.find('workflow-migrations')
            formdef.workflow_migrations = {}
            for child in workflow_migrations:
                old_workflow = child.attrib['old-workflow']
                new_workflow = child.attrib['new-workflow']
                timestamp = xml_node_text(child.find('timestamp'))
                formdef.workflow_migrations[f'{old_workflow} {new_workflow}'] = migration = {
                    'old_workflow': old_workflow,
                    'new_workflow': new_workflow,
                    'timestamp': timestamp,
                    'status_mapping': {},
                }
                for remap in child.findall('status-mapping/map'):
                    migration['status_mapping'][remap.attrib['old']] = remap.attrib['new']

        formdef.xml_testdefs = tree.find('testdefs')

        unknown_datasources = set()
        if check_datasources:
            # check if datasources are defined
            for field in formdef.fields:
                data_source = getattr(field, 'data_source', None)
                if data_source:
                    data_source_id = data_source.get('type')
                    if isinstance(data_sources.get_object(data_source), data_sources.StubNamedDataSource):
                        unknown_datasources.add(data_source_id)
                    elif data_source_id and data_source_id.startswith('carddef:'):
                        parts = data_source_id.split(':')
                        # check if carddef exists
                        url_name = parts[1]
                        if formdef.xml_root_node == 'carddef' and formdef.url_name == url_name:
                            # reference to itself, it's ok
                            continue
                        try:
                            CardDef.get_by_urlname(url_name)
                        except KeyError:
                            unknown_datasources.add(data_source_id)
                            continue

                        if len(parts) == 2 or parts[2] == '_with_user_filter':
                            continue

                        lookup_criterias = [
                            Equal('formdef_type', 'carddef'),
                            Equal('visibility', 'datasource'),
                            Equal('slug', parts[2]),
                        ]
                        try:
                            get_publisher().custom_view_class.select(lookup_criterias)[0]
                        except IndexError:
                            unknown_datasources.add(data_source_id)

        if (
            unknown_field_types or unknown_fields_blocks or unknown_datasources
        ) and not ignore_missing_dependencies:
            details = collections.defaultdict(set)
            if unknown_field_types:
                details[_('Unknown field types')].update(unknown_field_types)
            if unknown_fields_blocks:
                details[_('Unknown blocks of fields')].update(unknown_fields_blocks)
            if unknown_datasources:
                details[_('Unknown datasources')].update(unknown_datasources)
            raise FormdefImportUnknownReferencedError(_('Unknown referenced objects'), details=details)

        if check_deprecated:
            # check for deprecated elements
            job = DeprecationsScan()
            try:
                job.check_deprecated_elements_in_object(formdef)
            except DeprecatedElementsDetected as e:
                raise FormdefImportError(str(e))

        return formdef

    def finish_tests_xml_import(self):
        from .testdef import TestDef

        for testdef in TestDef.select_for_objectdef(self):
            TestDef.remove_object(testdef.id)

        for testdef in self.xml_testdefs or []:
            obj = TestDef.import_from_xml_tree(testdef, self)
            obj.store(comment=_('Creation on form import'))

        del self.xml_testdefs

    def get_detailed_email_form(self, formdata, url):
        r = ''
        if formdata.user_id and formdata.user:
            r = '%s\n  %s\n\n' % (_('User name:'), formdata.user.name)
        return r + formdata.get_rst_summary(url)

    def get_user_prefilled_data(self, formdata, field_id):
        # look up in submitted form for one that would hold the user
        # email (the one set to be prefilled by user email)

        if not formdata.data:
            return None

        def is_user_field(field):
            if not getattr(field, 'prefill', None):
                return False
            if field.prefill.get('type') != 'user':
                return False
            if field.prefill.get('value') != field_id:
                return False
            return True

        # check first in "normal" fields
        for field in self.fields:
            if not is_user_field(field):
                continue

            v = formdata.data.get(field.id)
            if v:
                return v

        # then check in block fields
        for field in self.fields:
            if field.key != 'block':
                continue
            for subfield in field.block.fields:
                if not is_user_field(subfield):
                    continue
                v = formdata.data.get(field.id)
                if not (v and v.get('data')):
                    continue
                for data in v.get('data'):
                    w = data.get(subfield.id)
                    if w:
                        return w

    def get_submitter_email(self, formdata):
        users_cfg = get_cfg('users', {})
        field_email_id = users_cfg.get('field_email') or 'email'
        value = self.get_user_prefilled_data(formdata, field_email_id)
        if value:
            return value

        # if nothing was found, get email from user profile
        if formdata.user and formdata.user.email and formdata.user.is_active:
            return formdata.user.email

        return None

    def get_submitter_phone(self, formdata):
        users_cfg = get_cfg('users', {})
        for field_phone_key in ('field_mobile', 'field_phone'):
            field_phone_id = users_cfg.get(field_phone_key)
            if field_phone_id:
                value = self.get_user_prefilled_data(formdata, field_phone_id)
                if value:
                    return value

        # if nothing was found, get phone from user profile
        if formdata.user and formdata.user.is_active:
            return formdata.user.get_formatted_phone()

        return None

    def get_static_substitution_variables(self, minimal=False):
        d = {
            'form_name': self.name,
            'form_slug': self.url_name,
            'form_class_name': self.__class__.__name__,  # reserved for logged errors
        }
        if not minimal:
            from wcs.variables import LazyFormDef

            d['form_objects'] = LazyFormDef(self).objects
        if self.category:
            d.update(self.category.get_substitution_variables(minimal=minimal))
        d.update(self.get_variable_options())
        return d

    def get_substitution_variables(self, minimal=False):
        from wcs.variables import LazyFormDef

        from .qommon.substitution import CompatibilityNamesDict

        return CompatibilityNamesDict({'form': LazyFormDef(self)})

    def get_detailed_evolution(self, formdata):
        if not formdata.evolution:
            return None

        details = []
        evo = formdata.evolution[-1]
        if evo.who:
            evo_who = None
            if evo.who == '_submitter':
                if formdata.user_id:
                    evo_who = formdata.user_id
            else:
                evo_who = evo.who
            if evo_who:
                user_who = get_publisher().user_class.get(evo_who, ignore_errors=True)
                if user_who:
                    details.append(_('User name'))
                    details.append('  %s' % user_who.name)
        if evo.status:
            details.append(_('Status'))
            details.append('  %s' % formdata.get_status_label())
        comment = evo.get_plain_text_comment()
        if comment:
            details.append('\n%s\n' % comment)
        return '\n\n----\n\n' + '\n'.join([str(x) for x in details])

    def is_of_concern_for_role_id(self, role_id):
        if not self.workflow_roles:
            return False
        return role_id in self.workflow_roles.values()

    def is_of_concern_for_user(self, user, formdata=None):
        if not self.workflow_roles:
            self.workflow_roles = {}

        user_roles = set(user.get_roles())

        # if the formdef itself has some function attributed to the user, grant
        # access.
        for role_id in self.workflow_roles.values():
            if role_id in user_roles:
                return True

        # if there was some redispatching of function, values will be different
        # in formdata, check them.
        if formdata and formdata.workflow_roles:
            for role_id in formdata.workflow_roles.values():
                if role_id is None:
                    continue
                if isinstance(role_id, list):
                    role_ids = set(role_id)
                else:
                    role_ids = {role_id}
                if user_roles.intersection(role_ids):
                    return True

        # if no formdata was given, lookup if there are some existing formdata
        # where the user has access.
        if not formdata:
            data_class = self.data_class()
            for role_id in user.get_roles():
                if data_class.get_ids_with_indexed_value('workflow_roles', role_id):
                    return True

        return False

    def is_user_allowed_read(self, user, formdata=None):
        if not user:
            if formdata and get_session() and get_session().is_anonymous_submitter(formdata):
                return True
            return False
        if user.is_admin:
            return True

        user_roles = set(user.get_roles())
        user_roles.add(logged_users_role().id)

        def ensure_role_are_strings(roles):
            # makes sure all roles are defined as strings, as different origins
            # (formdef, user, workflow status...) may define them differently.
            return {str(x) for x in roles if x}

        user_roles = ensure_role_are_strings(user_roles)

        if self.user_allowed_to_access_own_data and formdata and formdata.is_submitter(user):
            return True
        if self.is_of_concern_for_user(user):
            if not formdata:
                return True

        if formdata:
            # current status
            concerned_roles = ensure_role_are_strings(formdata.get_concerned_roles())
            if '_submitter' in concerned_roles and formdata.is_submitter(user):
                return True
            if user_roles.intersection(concerned_roles):
                return True

        return False

    def is_user_allowed_read_status_and_history(self, user, formdata=None):
        if user and user.is_admin:
            return True

        if not self.workflow_roles:
            self.workflow_roles = {}
        form_roles = [x for x in self.workflow_roles.values() if x]
        if formdata and formdata.workflow_roles:
            for x in formdata.workflow_roles.values():
                if isinstance(x, list):
                    form_roles.extend(x)
                elif x:
                    form_roles.append(x)
        return self.is_user_allowed_read(user, formdata=formdata)

    @property
    def publication_date(self):
        return self.__dict__.get('publication_date')

    @publication_date.setter
    def publication_date(self, value):
        self.__dict__['publication_date'] = value
        if hasattr(self, 'publication_datetime'):
            del self.publication_datetime

    @property
    def expiration_date(self):
        return self.__dict__.get('expiration_date')

    @expiration_date.setter
    def expiration_date(self, value):
        self.__dict__['expiration_date'] = value
        if hasattr(self, 'expiration_datetime'):
            del self.expiration_datetime

    @functools.cached_property
    def publication_datetime(self):
        try:
            return get_as_datetime(self.publication_date)
        except (TypeError, ValueError):
            return None

    @functools.cached_property
    def expiration_datetime(self):
        try:
            return get_as_datetime(self.expiration_date)
        except (TypeError, ValueError):
            return None

    def is_disabled(self):
        if self.disabled:
            return True
        if self.publication_datetime and self.publication_datetime > datetime.datetime.now():
            return True
        if self.expiration_datetime and self.expiration_datetime < datetime.datetime.now():
            return True
        return False

    class _EmptyClass:  # helper for instance creation without calling __init__
        pass

    def __copy__(self, memo=None, deepcopy=False):
        formdef_copy = self._EmptyClass()
        formdef_copy.__class__ = self.__class__
        if deepcopy:
            formdef_copy.__dict__ = copy.deepcopy(self.__dict__, memo=memo)
        else:
            formdef_copy.__dict__ = copy.copy(self.__dict__)
        return formdef_copy

    def __deepcopy__(self, memo=None):
        return self.__copy__(memo=memo, deepcopy=True)

    # don't pickle computed attributes
    def __getstate__(self):
        odict = copy.copy(self.__dict__)
        if '_workflow' in odict:
            del odict['_workflow']
        if '_start_page' in odict:
            del odict['_start_page']
        if self.lightweight and 'fields' in odict:
            # will be stored independently
            del odict['fields']
        if '_custom_views' in odict:
            del odict['_custom_views']
        if '_import_orig_slug' in odict:
            del odict['_import_orig_slug']
        if '_onload_category_id' in odict:
            del odict['_onload_category_id']
        if 'xml_testdefs' in odict:
            del odict['xml_testdefs']
        if 'publication_datetime' in odict:
            del odict['publication_datetime']
        if 'expiration_datetime' in odict:
            del odict['expiration_datetime']
        return odict

    def __setstate__(self, dict):
        super().__setstate__(dict)
        self.__dict__ = dict
        self._workflow = None
        self._start_page = None
        if hasattr(self, 'snapshot_object'):
            # don't restore snapshot object that would have been stored erroneously
            delattr(self, 'snapshot_object')

    @classmethod
    def storage_load(cls, fd, **kwargs):
        o = super().storage_load(fd)
        o._onload_category_id = o.category_id  # keep track of category, to update wcs_all_forms if changed
        if kwargs.get('lightweight'):
            o.fields = Ellipsis
            return o
        if cls.lightweight:
            try:
                o.fields = pickle.load(fd)
            except EOFError:
                pass  # old format
        for field in o.fields or []:
            field._formdef = o  # keep formdef reference
        return o

    @classmethod
    def storage_dumps(cls, object):
        assert getattr(object, 'fields', None) is not Ellipsis, 'storing a lightweight object is not allowed'
        # use two separate pickle chunks to store the formdef, the first field
        # is everything but fields (excluded via __getstate__) while the second
        # chunk contains the fields.
        return pickle.dumps(object, protocol=2) + pickle.dumps(object.fields, protocol=2)

    def change_workflow(self, new_workflow, status_mapping=None, user_id=None, snapshot_comment=None):
        old_workflow = self.get_workflow()

        from . import sql

        formdata_count = self.data_class().count([sql.StrictNotEqual('status', 'draft')])
        status_changes = False
        if formdata_count:
            assert status_mapping, 'status mapping is required if there are formdatas'

            mapping = {'draft': 'draft'}
            if '_all' in status_mapping:  # remapping everything to same status
                for formdata in self.data_class().select_iterator(
                    [NotEqual('status', 'draft')], ignore_errors=True, itersize=200
                ):
                    mapping[formdata.status] = 'wf-%s' % status_mapping['_all']
            else:
                assert all(
                    status.id in status_mapping for status in old_workflow.possible_status
                ), 'a status was not mapped'
                for old_status, new_status in status_mapping.items():
                    mapping['wf-%s' % old_status] = 'wf-%s' % new_status

            status_changes = any(x[0] != x[1] for x in mapping.items())
            if status_changes:
                # if there are status changes, update all formdatas (except drafts)
                sql.formdef_remap_statuses(self, mapping)

        self.workflow = new_workflow
        if new_workflow.has_action('geolocate') and not self.geolocations:
            self.geolocations = {'base': str(_('Geolocation'))}
        removed_functions = set()
        for function_key in list((self.workflow_roles or {}).keys()):
            if function_key not in new_workflow.roles:
                del self.workflow_roles[function_key]
                removed_functions.add(function_key)

        # keep track of workflow changes
        if not self.workflow_migrations:
            self.workflow_migrations = {}
        if old_workflow.slug:
            self.workflow_migrations[f'{old_workflow.slug} {new_workflow.slug}'] = {
                'timestamp': localtime().isoformat(),
                'old_workflow': old_workflow.slug,
                'new_workflow': new_workflow.slug,
                'status_mapping': status_mapping or {},
            }

        self.store(comment=snapshot_comment or _('Workflow change'), snapshot_store_user=user_id)
        if formdata_count:
            # instruct formdef to update its security rules
            self.data_class().rebuild_security()
            if removed_functions or status_changes:
                # status changes require to update jump markers and change in functions
                # requires to update all formdatas to remove old keys
                mapping_without_prefix = {x.removeprefix('wf-'): y for x, y in mapping.items()}
                for formdata in self.data_class().select_iterator(ignore_errors=True, itersize=200):
                    changed = False
                    for function_key in removed_functions:
                        if function_key in (formdata.workflow_roles or {}):
                            del formdata.workflow_roles[function_key]
                            changed = True
                    if (
                        status_changes
                        and formdata.workflow_data
                        and '_markers_stack' in formdata.workflow_data
                    ):
                        current_markers_stack = formdata.workflow_data['_markers_stack']
                        formdata.workflow_data['_markers_stack'] = [
                            {'status_id': mapping_without_prefix.get(x['status_id'])}
                            for x in current_markers_stack
                        ]
                        if formdata.workflow_data['_markers_stack'] != current_markers_stack:
                            changed = True
                    if changed:
                        formdata.store()

    def i18n_scan(self):
        location = '%s/%s/' % (self.backoffice_section, self.id)
        yield location, None, self.name
        yield location, None, self.description
        for field in self.fields or []:
            yield from field.i18n_scan(base_location=location + 'fields/')

    def get_last_test_results(self, extra_criterias=None, order_by='-id'):
        from wcs.testdef import TestDef, TestResults

        criterias = [Equal('object_type', self.get_table_name()), Equal('object_id', str(self.id))]

        if not TestDef.count(criterias):
            return

        if extra_criterias:
            criterias.extend(extra_criterias)

        test_results = TestResults.select(criterias, order_by=order_by)

        if not test_results:
            return

        result = test_results[0]
        result.formatted_timestamp = localstrftime(result.timestamp)
        return result


EmailsDirectory.register(
    'new_user',
    _('Notification of creation to user'),
    enabled=False,
    category=_('Workflow'),
    default_subject=_('New form ({{ form_name }})'),
    default_body=_(
        '''\
Hello,

This mail is a reminder about the form you just submitted.
{% if form_user %}
You can consult it with this link: {{ form_url }}
{% endif %}

{% if form_details %}
For reference, here are the details:

{{ form_details }}
{% endif %}
'''
    ),
)

EmailsDirectory.register(
    'change_user',
    _('Notification of change to user'),
    category=_('Workflow'),
    default_subject=_('Form status change ({{ form_name }})'),
    default_body=_(
        '''\
Hello,

{% if form_status_changed %}
Status of the form you submitted just changed (from "{{ form_previous_status }}" to "{{ form_status }}").
{% endif %}

{% if form_user %}
You can consult it with this link: {{ form_url }}
{% endif %}

{% if form_comment %}New comment: {{ form_comment }}{% endif %}

{% if form_evolution %}
{{ form_evolution }}
{% endif %}
'''
    ),
)


EmailsDirectory.register(
    'new_receiver',
    _('Notification of creation to receiver'),
    enabled=False,
    category=_('Workflow'),
    default_subject=_('New form ({{ form_name }})'),
    default_body=_(
        '''\
Hello,

A new form has been submitted, you can see it with this link:
{{ form_url_backoffice }}

{% if form_details %}
For reference, here are the details:

{{ form_details }}
{% endif %}
'''
    ),
)


EmailsDirectory.register(
    'change_receiver',
    _('Notification of change to receiver'),
    category=_('Workflow'),
    default_subject=_('Form status change ({{ form_name }})'),
    default_body=_(
        '''\
Hello,

A form just changed, you can consult it with this link:
{{ form_url_backoffice }}

{% if form_status_changed %}
Status of the form just changed (from "{{ form_previous_status }}" to "{{ form_status }}").
{% endif %}

{% if form_comment %}New comment: {{ form_comment }}{% endif %}

{% if form_evolution %}
{{ form_evolution }}
{% endif %}
'''
    ),
)

Substitutions.register('form_name', category=_('Form'), comment=_('Form Name'))


def get_formdefs_of_all_kinds(**kwargs):
    from wcs.admin.settings import UserFieldsFormDef
    from wcs.blocks import BlockDef
    from wcs.carddef import CardDef
    from wcs.formdef import FormDef
    from wcs.wf.form import FormWorkflowStatusItem
    from wcs.workflows import Workflow

    select_kwargs = {
        'ignore_errors': True,
        'ignore_migration': True,
    }
    select_kwargs.update(kwargs)
    formdefs = [UserFieldsFormDef()]
    formdefs += FormDef.select(**select_kwargs)
    formdefs += BlockDef.select(**select_kwargs)
    formdefs += CardDef.select(**select_kwargs)
    for workflow in Workflow.select(**select_kwargs):
        for element in itertools.chain(workflow.possible_status, workflow.global_actions or []):
            for item in element.items:
                if isinstance(item, FormWorkflowStatusItem) and item.formdef:
                    formdefs.append(item.formdef)
        if workflow.variables_formdef:
            formdefs.append(workflow.variables_formdef)
        if workflow.backoffice_fields_formdef:
            formdefs.append(workflow.backoffice_fields_formdef)
    return formdefs

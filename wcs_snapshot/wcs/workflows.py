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

import base64
import collections
import copy
import datetime
import glob
import itertools
import os
import random
import re
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from importlib import import_module

from django.utils.encoding import force_str
from django.utils.timezone import is_aware, localtime, make_aware, now
from quixote import get_publisher, get_request, get_response, get_session
from quixote.html import TemplateIO, htmlescape, htmltext

import wcs.qommon.storage as st
from wcs.api_utils import is_url_signed
from wcs.clamd_file import PickableClamD
from wcs.qommon.storage import StorableObject, StoredObjectMixin, atomic_write
from wcs.sql import SqlWorkflow
from wcs.sql_criterias import (
    Contains,
    Equal,
    LessOrEqual,
    Null,
    Or,
    StatusReachedTimeoutCriteria,
    StrictNotEqual,
)

from .conditions import Condition
from .fields import FileField
from .formdata import Evolution
from .formdef_base import FormDefBase, FormdefImportError, FormdefImportUnknownReferencedError
from .mail_templates import MailTemplate
from .qommon import _, ezt, get_cfg, misc, pgettext_lazy, template
from .qommon.afterjobs import AfterJob
from .qommon.errors import UnknownReferencedErrorMixin
from .qommon.form import (
    CheckboxWidget,
    ComputedExpressionWidget,
    ConditionWidget,
    Form,
    SingleSelectWidget,
    SingleSelectWidgetWithOther,
    StringWidget,
    VarnameWidget,
    WidgetList,
    WidgetListOfRoles,
)
from .qommon.humantime import seconds2humanduration
from .qommon.misc import file_digest, get_as_datetime, get_dependencies_from_template, xml_node_text
from .qommon.substitution import CompatibilityNamesDict
from .qommon.template import Template, TemplateError
from .qommon.upload_storage import PicklableUpload, get_storage_object
from .roles import get_user_roles, logged_users_role

if not __name__.startswith('wcs.') and __name__ != '__main__':
    raise ImportError('Import of workflows module must be absolute (import wcs.workflows)')


def lax_int(s):
    try:
        return int(s)
    except (ValueError, TypeError):
        return -1


def perform_items(
    items, formdata, depth=20, user=None, global_action=False, continued=False, check_progress=True
):
    if not continued:
        assert not check_progress or not formdata.workflow_processing_timestamp
        formdata.workflow_processing_timestamp = now()
        if formdata.id:
            formdata.store_processing_change()
    if depth == 0:  # prevents infinite loops
        formdata.record_workflow_event('aborted-too-many-jumps')
        get_publisher().record_error(_('Too many jumps in workflow'), formdata=formdata)
        return
    url = None
    old_status = formdata.status
    wf_old_status = formdata.get_status()
    had_jump = False
    loop_items = None
    if wf_old_status and not global_action:
        loop_items = wf_old_status.get_loop_items(formdata=formdata)
    do_break = False
    with get_publisher().substitutions.freeze():
        if loop_items is not None:
            formdata.record_workflow_event('loop-start')
        for i, loop_item in enumerate(loop_items if loop_items is not None else [True]):
            # loop_items is None is loop_items_template is not defined: we want to perform actions
            # loop_items is an empty list if compilation failed: don't perform actions
            if loop_item and loop_items:
                # only feed with status_loop if defined
                get_publisher().substitutions.feed(
                    wf_old_status.get_status_loop(index=i, items=loop_items, item=loop_item)
                )
            for item in items or []:
                if getattr(item.perform, 'noop', False):
                    continue
                if not item.check_condition(formdata):
                    continue
                if formdata.is_workflow_test():
                    if formdata.testdef:
                        formdata.testdef.add_to_coverage(item)

                    if not hasattr(item, 'perform_in_tests'):
                        continue

                had_jump |= item.key == 'jump'
                formdata.record_workflow_action(action=item)
                perform_method = item.perform if not formdata.is_workflow_test() else item.perform_in_tests
                try:
                    url = perform_method(formdata) or url
                except AbortActionException as e:
                    url = url or e.url
                    do_break = True
                    break
                if formdata.status != old_status:
                    do_break = True
                    break
            if do_break:
                break
        if loop_items is not None:
            formdata.record_workflow_event('loop-end')

    if not global_action:
        loop_target_status = None
        if wf_old_status:
            loop_target_status = wf_old_status.get_loop_target_status(formdata=formdata)
        if not do_break and loop_target_status:
            formdata.status = 'wf-%s' % loop_target_status.id

    if formdata.status != old_status:
        formdata.record_workflow_event('continuation')
    if formdata.status != old_status or (global_action and had_jump):
        if not formdata.evolution:
            formdata.evolution = []
        evo = Evolution(formdata)
        if global_action:
            evo.set_user(formdata=formdata, user=user)
        evo.time = localtime()
        evo.status = formdata.status
        formdata.evolution.append(evo)
        formdata.store()
        # performs the items of the new status
        wf_status = formdata.get_status()
        url = perform_items(wf_status.items, formdata, depth=depth - 1, continued=True) or url
    if not continued:
        formdata.workflow_processing_timestamp = None
        if formdata.id and formdata.formdef.data_class().exists([Equal('id', formdata.id)]):
            formdata.store_processing_change()
    if url:
        # hack around webtest as it checks type(url) is str and
        # this won't work on django safe strings (isinstance would work);
        # adding '' makes sure we get a "real" str object.
        url = url + ''
    return url


@contextmanager
def push_perform_workflow(formdata):
    # stash workflow execution contexts
    pub = get_publisher()
    if not hasattr(pub, 'workflow_execution_stack'):
        pub.workflow_execution_stack = []
    formdata_key = f'{formdata.formdef.xml_root_node}-{formdata.formdef.id}-{formdata.id}'
    pub.workflow_execution_stack.append({'key': formdata_key, 'context': {}})
    yield
    formdata_popped_key = pub.workflow_execution_stack.pop().get('key')
    assert formdata_key == formdata_popped_key


class WorkflowImportError(Exception):
    def __init__(self, msg, msg_args=None, details=None):
        self.msg = msg
        self.msg_args = msg_args or ()
        self.details = details


class WorkflowImportUnknownReferencedError(UnknownReferencedErrorMixin, WorkflowImportError):
    pass


class AbortActionException(Exception):
    def __init__(self, url=None):
        self.url = url


class AbortOnRemovalException(AbortActionException):
    def __init__(self, formdata):
        from wcs.carddef import CardDef

        if len(get_publisher().workflow_execution_stack) > 1:
            # no custom behaviour when called from external workflows
            url = None
        elif get_request() and get_response().filter.get('in_backoffice'):
            # if in backoffice, display a session message and redirect to listing
            if isinstance(formdata.formdef, CardDef):
                get_session().add_message(_('The card has been deleted.'), level='info')
            else:
                get_session().add_message(_('The form has been deleted.'), level='info')
            url = '..'
        else:
            # otherwise, redirect to homepage
            url = get_publisher().get_frontoffice_url()
        super().__init__(url=url)


class RedisplayFormException(Exception):
    def __init__(self, form, error=None):
        self.form = form
        # don't display errors after "add block" button has been clicked.
        form.clear_errors()
        if error:
            # add explicit error
            form.add_global_errors([error])


class ReplayException(Exception):
    pass


def get_role_dependencies(roles):
    for role_id in roles or []:
        if not role_id:
            continue
        role_id = str(role_id)
        if role_id.startswith('_') or role_id == 'logged-users':
            continue
        yield get_publisher().role_class.get(role_id, ignore_errors=True)


class AttachmentSubstitutionProxy:
    def __init__(self, formdata, attachment_evolution_part):
        self.formdata = formdata
        self.attachment_evolution_part = attachment_evolution_part

    def __str__(self):
        return self.base_filename

    @property
    def filename(self):
        return self.attachment_evolution_part.orig_filename

    @property
    def base_filename(self):
        return self.filename

    @property
    def content_type(self):
        return self.attachment_evolution_part.content_type

    @property
    def content(self):
        fp = self.attachment_evolution_part.get_file_pointer()
        if fp:
            return fp.read()
        return b''

    @property
    def file_digest(self):
        return self.attachment_evolution_part.get_file_digest()

    @property
    def b64_content(self):
        return base64.b64encode(self.content)

    @property
    def url(self):
        return '%sattachment?f=%s' % (
            self.formdata.get_url(),
            os.path.basename(self.attachment_evolution_part.filename),
        )

    def get_content(self):
        return self.content

    def get(self, key):  # for |getlist
        assert key in ('file_digest',)
        return getattr(self, key, None)

    def inspect_keys(self):
        return ['url', 'base_filename', 'content_type']


class NamedAttachmentsSubstitutionProxy:
    include_in_inspect = True  # force display in inspect

    def __init__(self, formdata, parts):
        self.formdata = formdata
        self.parts = parts

    def __len__(self):
        return len(self.parts)

    def __str__(self):
        return str(self[-1])

    def __getattr__(self, name):
        try:
            return super().__getattr__(name)
        except AttributeError:
            return getattr(self[-1], name)

    def inspect_keys(self):
        yield from self[-1].inspect_keys()
        if len(self.parts) > 1:
            # only advertise indexed keys if there are multiple elements
            yield from [str(x) for x in range(len(self.parts))]

    def __getitem__(self, i):
        if isinstance(i, int) or (isinstance(i, str) and misc.is_ascii_digit(i)):
            return AttachmentSubstitutionProxy(self.formdata, self.parts[int(i)])
        try:
            return self.__getattr__(i)
        except AttributeError:
            raise KeyError(i)


class AttachmentsSubstitutionProxy:
    def __init__(self, formdata, deprecated_usage=False):
        self.formdata = formdata
        self.deprecated_usage = deprecated_usage

    def inspect_keys(self):
        if self.deprecated_usage:
            return []

        return {
            part.varname
            for part in self.formdata.iter_evolution_parts(AttachmentEvolutionPart)
            if getattr(part, 'varname', None)
        }

    def __getattr__(self, name):
        if name.startswith('__'):
            raise AttributeError(name)

        def has_varname_attachment(part):
            return isinstance(part, AttachmentEvolutionPart) and getattr(part, 'varname', None) == name

        parts = [part for part in self.formdata.iter_evolution_parts() if has_varname_attachment(part)]
        if parts:
            if self.deprecated_usage:
                error_summary = _('Usage of "attachments" detected in "attachments_%s" expression') % name
                get_publisher().record_deprecated_usage(error_summary, formdata=self.formdata)
            return NamedAttachmentsSubstitutionProxy(self.formdata, parts)
        raise AttributeError(name)

    def __getstate__(self):
        # do not deepcopy/pickle formdata, store a reference and restore it in __setstate__.
        return {
            'deprecated_usage': self.deprecated_usage,
            'formdef_type': self.formdata.formdef.xml_root_node,
            'formdef_id': self.formdata.formdef.id,
            'formdata_id': self.formdata.id,
        }

    def __setstate__(self, state):
        from wcs.carddef import CardDef
        from wcs.formdef import FormDef

        self.deprecated_usage = state.get('deprecated_usage')
        # restore formdata from database
        if state.get('formdef_type') == 'carddef':
            obj_class = CardDef
        else:
            obj_class = FormDef

        formdata_id = state.get('formdata_id')
        if formdata_id:
            self.formdata = obj_class.get(state.get('formdef_id')).data_class().get(formdata_id)


class EvolutionPart:
    to = None
    is_hidden = None
    view = None

    def render_for_fts(self):
        if not self.view or self.to:
            # don't include parts with no content or restricted visibility
            return ''
        illegal_fts_chars = re.compile(r'[\x00-\x1F]')
        return illegal_fts_chars.sub(' ', misc.html2text(self.view() or ''))


class AttachmentEvolutionPart(EvolutionPart, PickableClamD):
    orig_filename = None
    base_filename = None
    content_type = None
    charset = None
    varname = None
    render_for_fts = None
    storage = None
    storage_attrs = None
    display_in_history = True

    def __init__(
        self,
        base_filename,
        fp,
        orig_filename=None,
        content_type=None,
        charset=None,
        varname=None,
        storage=None,
        storage_attrs=None,
        to=None,
    ):
        self.base_filename = base_filename
        self.orig_filename = orig_filename or base_filename
        self.content_type = content_type
        self.charset = charset
        self.fp = fp
        self.varname = varname
        self.storage = storage
        self.storage_attrs = storage_attrs
        self.to = to

    @classmethod
    def from_upload(cls, upload, varname=None, to=None):
        return AttachmentEvolutionPart(
            upload.base_filename,
            getattr(upload, 'fp', None),
            upload.orig_filename,
            upload.content_type,
            upload.charset,
            varname=varname,
            storage=getattr(upload, 'storage', None),
            storage_attrs=getattr(upload, 'storage_attrs', None),
            to=to,
        )

    def get_file_path(self):
        if os.path.isabs(self.filename):
            return self.filename
        return os.path.join(get_publisher().app_dir, self.filename)

    def get_file_pointer(self):
        if self.filename.startswith('uuid-'):
            return None
        return open(self.get_file_path(), 'rb')  # pylint: disable=consider-using-with

    def get_content(self):
        with self.get_file_pointer() as fd:
            return fd.read()

    def get_file_digest(self):
        with self.get_file_pointer() as fd:
            return misc.file_digest(fd)

    def __getstate__(self):
        odict = self.__dict__.copy()
        if not odict.get('fp') and 'filename' not in odict:
            # we need a filename as an identifier: create one from nothing
            # instead of file_digest(self.fp) (see below)
            odict['filename'] = 'uuid-%s' % uuid.uuid4()
            self.filename = odict['filename']
            return odict

        if 'fp' in odict:
            del odict['fp']

        # there is no filename, or it was a temporary one: create it
        if 'filename' not in odict or odict['filename'].startswith('uuid-'):
            if not getattr(self, 'fp', None):
                return odict
            must_close_file = False
            if self.fp.closed:
                assert isinstance(self.fp.name, str)
                self.fp = open(self.fp.name, 'rb')  # pylint: disable=consider-using-with
                must_close_file = True
            filename = file_digest(self.fp)
            # create subdirectory with digest prefix as name
            dirname = os.path.join('attachments', filename[:4])
            os.makedirs(os.path.join(get_publisher().app_dir, dirname), exist_ok=True)
            odict['filename'] = os.path.join(dirname, filename)
            self.filename = odict['filename']
            self.fp.seek(0)
            atomic_write(self.get_file_path(), self.fp)
            if must_close_file:
                self.fp.close()
        elif os.path.isabs(odict['filename']):
            # current value is an absolute path, update it quietly to be a relative path
            pub_app_path_prefix = os.path.join(get_publisher().app_dir, '')
            if os.path.exists(odict['filename']) and odict['filename'].startswith(pub_app_path_prefix):
                odict['filename'] = odict['filename'][len(pub_app_path_prefix) :]

        return odict

    def is_hidden(self):
        return bool(not self.display_in_history)

    def view(self, **kwargs):
        show_link = True
        if self.has_redirect_url():
            is_in_backoffice = bool(get_request() and get_request().is_in_backoffice())
            show_link = bool(self.get_redirect_url(backoffice=is_in_backoffice))
        if show_link:
            text = '<p class="wf-attachment"><a href="attachment?f=%s">%s</a>' % (
                os.path.basename(self.filename),
                self.orig_filename,
            )
        else:
            text = '<p class="wf-attachment">%s' % self.orig_filename
        text += self.get_view_clamd_status()
        text += '</p>'
        return htmltext(text)

    def get_json_export_dict(self, anonymise=False, include_files=True):
        if not include_files or anonymise:
            return None
        d = {
            'type': 'workflow-attachment',
            'content_type': self.content_type,
            'filename': self.base_filename,
            'to': self.to,
        }
        fd = self.get_file_pointer()
        if fd:
            d['content'] = base64.encodebytes(fd.read())
            fd.close()
        return d

    @classmethod
    def get_substitution_variables(cls, formdata):
        return {
            'attachments': AttachmentsSubstitutionProxy(formdata, deprecated_usage=True),
            'form_attachments': AttachmentsSubstitutionProxy(formdata),
        }

    # mimic PicklableUpload methods:

    def can_thumbnail(self):
        return get_storage_object(getattr(self, 'storage', None)).can_thumbnail(self)

    def has_redirect_url(self):
        return get_storage_object(getattr(self, 'storage', None)).has_redirect_url(self)

    def get_redirect_url(self, backoffice=False):
        return get_storage_object(getattr(self, 'storage', None)).get_redirect_url(
            self, backoffice=backoffice
        )


class ActionsTracingEvolutionPart(EvolutionPart):
    # legacy, for migration
    event = None
    event_args = None
    actions = None
    external_workflow_id = None
    external_status_id = None
    external_item_id = None


class ContentSnapshotPart(EvolutionPart):
    user_id = None
    render_for_fts = None
    source = None

    def __init__(self, formdata, old_data, user=None, source=None):
        self.datetime = now()
        self.formdef_type = formdata.formdef.xml_root_node
        self.formdef_id = formdata.formdef.id
        self.old_data = old_data
        self.new_data = copy.deepcopy(formdata.data)
        self.source = source
        if isinstance(user, get_publisher().user_class):
            self.user_id = user.id
        elif isinstance(user, (int, str)):
            self.user_id = user

    @classmethod
    def take(cls, formdata, old_data, user=None, source=None):
        part = cls(formdata, old_data, user=user, source=source)
        if part.has_changes:
            formdata.evolution[-1].add_part(part)
        return part

    def __getstate__(self):
        odict = copy.copy(self.__dict__)
        if '_formdef' in odict:
            del odict['_formdef']
        return odict

    def __setstate__(self, dict):
        self.__dict__ = dict
        if hasattr(self, '_formdef'):
            delattr(self, '_formdef')

    @property
    def has_changes(self):
        return self.old_data != self.new_data

    @property
    def formdef(self):
        from wcs.carddef import CardDef
        from wcs.formdef import FormDef

        if not hasattr(self, '_formdef'):
            formdef_class = CardDef if self.formdef_type == 'carddef' else FormDef
            self._formdef = formdef_class.get(self.formdef_id, ignore_errors=True)
        return self._formdef

    def view(self, **kwargs):
        is_in_backoffice = bool(get_request() and get_request().is_in_backoffice())
        if not is_in_backoffice:
            return

        if not self.formdef:
            return

        def get_field_value_and_display(field, data):
            value = data.get(field.id)
            if field.store_display_value:
                return value, data.get('%s_display' % field.id)
            if field.convert_value_to_str:
                return value, field.convert_value_to_str(value)
            return value, value

        def diff_field(field, old_data, new_data, block_field=None, block_item_num=0):
            old_value, old_value_display = get_field_value_and_display(field, old_data)
            new_value, new_value_display = get_field_value_and_display(field, new_data)
            if isinstance(old_value, PicklableUpload) and isinstance(new_value, PicklableUpload):
                if old_value.get_fs_filename() == new_value.get_fs_filename():
                    return
            if field.key in ['password', 'computed']:
                return
            if old_value == new_value:
                return

            field_id = field.id
            if block_field:
                field_id = '%s_%s' % (block_field.id, field.id)

            if old_value is None:
                old_value_display = htmltext('<i>—</i>')
                if field.key == 'map':
                    new_value_display = htmltext('<i>%s</i>') % _('new value')
            elif new_value is None:
                new_value_display = htmltext('<i>—</i>')
                if field.key == 'map':
                    old_value_display = htmltext('<i>%s</i>') % _('old value')
            else:
                if field.key == 'map':
                    old_value_display = htmltext('<i>%s</i>') % _('old value')
                    new_value_display = htmltext('<i>%s</i>') % _('new value')
            yield (
                htmltext('<tr data-field-id="%s"%s><td>%s</td><td>%s</td><td>%s</td></tr>')
                % (
                    field_id,
                    (
                        (htmltext(' class="block-item-field" data-element-num="%s"') % block_item_num)
                        if block_field
                        else ''
                    ),
                    field.label,
                    old_value_display,
                    new_value_display,
                )
            )

        def diff_fields(fields, old_data, new_data, block_field=None, block_item_num=0):
            for field in fields:
                if field.key == 'block':
                    try:
                        block_old_data = (old_data.get(field.id) or {}).get('data') or []
                    except AttributeError:
                        block_old_data = []
                    try:
                        block_new_data = (new_data.get(field.id) or {}).get('data') or []
                    except AttributeError:
                        block_new_data = []
                    len_old = len(block_old_data)
                    len_new = len(block_new_data)
                    block_diffs = []
                    for i in range(max(len_old, len_new)):
                        try:
                            item_old_data = block_old_data[i]
                        except IndexError:
                            item_old_data = {}
                        try:
                            item_new_data = block_new_data[i]
                        except IndexError:
                            item_new_data = {}
                        block_diffs.append(
                            list(
                                diff_fields(
                                    field.block.fields,
                                    item_old_data,
                                    item_new_data,
                                    block_field=field,
                                    block_item_num=i,
                                )
                            )
                        )
                    if any(block_diffs):
                        yield htmltext('<tr data-field-id="%s"><td colspan="3">%s</td></tr>') % (
                            field.id,
                            field.label,
                        )
                        for i, item_diff in enumerate(block_diffs):
                            if item_diff:
                                status = _('updated')
                                if i >= len_old:
                                    status = _('added')
                                if i >= len_new:
                                    status = _('removed')
                                yield htmltext(
                                    '<tr data-block-id="%s" data-element-num="%s" class="block-item"><td colspan="3">%s (%s)</td></tr>'
                                ) % (
                                    field.id,
                                    i,
                                    _('element number %s') % (i + 1),
                                    status,
                                )
                                yield from item_diff
                    continue
                yield from diff_field(
                    field, old_data, new_data, block_field=block_field, block_item_num=block_item_num
                )

        value_diffs = list(diff_fields(self.formdef.fields or [], self.old_data, self.new_data))
        if value_diffs:
            user = None
            if self.user_id:
                try:
                    user = get_publisher().user_class.get(self.user_id)
                except KeyError:
                    pass
            return template.render(
                ['wcs/backoffice/content-snapshot-part.html'],
                {
                    'datetime': self.datetime,
                    'localtime': localtime(self.datetime),
                    'value_diffs': htmltext('\n').join(value_diffs),
                    'old_data': self.old_data,
                    'user': user,
                },
            )


class DuplicateGlobalActionNameError(Exception):
    pass


class DuplicateStatusNameError(Exception):
    pass


class WorkflowVariablesFieldsFormDef(FormDefBase):
    """Class to handle workflow variables, it loads and saves from/to
    the workflow object 'variables_formdef' attribute."""

    lightweight = False
    fields_count_total_soft_limit = 40
    fields_count_total_hard_limit = 80

    may_appear_in_frontoffice = False  # won't appear in frontoffice

    def __init__(self, workflow):
        self.id = None
        self.workflow = workflow
        if self.workflow.is_readonly():
            self.readonly = True
        if workflow.variables_formdef:
            self.documentation = workflow.variables_formdef.documentation
            self.fields = self.workflow.variables_formdef.fields or []
        else:
            self.fields = []

    @property
    def name(self):
        return _('Options of workflow "%s"') % self.workflow.name

    def get_admin_url(self):
        return f'{self.workflow.get_admin_url()}variables/fields/'

    def get_field_admin_url(self, field):
        return self.get_admin_url() + '%s/' % field.id

    def get_new_field_id(self):
        return str(uuid.uuid4())

    def store(self, comment=None, *args, **kwargs):
        for field in self.fields:
            if hasattr(field, 'widget_class'):
                if not field.varname:
                    field.varname = misc.simplify(field.label, space='_')
        self.workflow.variables_formdef = self if self.fields else None
        self.workflow.store(comment=comment, *args, **kwargs)

    def is_readonly(self):
        return self.workflow.is_readonly()

    def migrate(self):
        changed = False
        for field in self.fields or []:
            changed |= field.migrate()
            if getattr(field, 'prefill', None):  # 2024-03-11
                # prefill attribute is no longer advertised for workflow variables,
                # reset its value if it had one, so ancient python prefills do not
                # persist.
                field.prefill = None
                changed = True
        return changed


class WorkflowBackofficeFieldsFormDef(FormDefBase):
    """Class to handle workflow backoffice fields, it loads and saves from/to
    the workflow object 'backoffice_fields_formdef' attribute."""

    lightweight = False
    fields_count_total_soft_limit = 40
    fields_count_total_hard_limit = 80
    may_appear_in_frontoffice = False  # won't appear in frontoffice

    field_prefix = 'bo'

    def __init__(self, workflow):
        self.id = None
        self.workflow = workflow
        if workflow.backoffice_fields_formdef:
            self.documentation = workflow.backoffice_fields_formdef.documentation
            self.fields = self.workflow.backoffice_fields_formdef.fields or []
        else:
            self.fields = []

    @property
    def name(self):
        return _('Backoffice fields of workflow "%s"') % self.workflow.name

    def get_admin_url(self):
        return f'{self.workflow.get_admin_url()}backoffice-fields/fields/'

    def get_field_admin_url(self, field):
        return self.get_admin_url() + '%s/' % field.id

    def get_new_field_id(self):
        return '%s%s' % (self.field_prefix, str(uuid.uuid4()))

    def store(self, comment=None):
        self.workflow.backoffice_fields_formdef = self
        self.workflow.store(comment=comment)

    def is_readonly(self):
        return self.workflow.is_readonly()


class Workflow(SqlWorkflow, StoredObjectMixin):
    _names = 'workflows'
    xml_root_node = 'workflow'
    backoffice_class = 'wcs.admin.workflows.WorkflowPage'
    category_class = 'wcs.categories.WorkflowCategory'
    verbose_name = _('Workflow')
    verbose_name_plural = _('Workflows')

    id = None
    name = None
    slug = None
    documentation = None
    possible_status = None
    roles = None
    variables_formdef = None
    backoffice_fields_formdef = None
    global_actions = None
    criticality_levels = None
    category_id = None
    status_remapping = None

    def __init__(self, name=None):
        self.name = name
        self.possible_status = []
        self.roles = {'_receiver': force_str(_('Recipient'))}
        self.global_actions = []
        self.criticality_levels = []

    def migrate(self):
        changed = False

        if 'roles' not in self.__dict__ or self.roles is None:
            self.roles = {'_receiver': force_str(_('Recipient'))}
            changed = True

        if not self.slug:
            self.slug = self.get_new_slug()
            changed = True

        if self.possible_status is None:
            # somehow broken
            self.possible_status = []

        for status in self.possible_status:
            changed |= status.migrate()

        if self.backoffice_fields_formdef and self.backoffice_fields_formdef.fields:
            for field in self.backoffice_fields_formdef.fields:
                changed |= field.migrate()

        if self.variables_formdef:
            changed |= self.variables_formdef.migrate()

        if not self.global_actions:
            self.global_actions = []

        for global_action in self.global_actions:
            changed |= global_action.migrate()

        for level in self.criticality_levels or []:
            changed |= level.migrate()

        if changed:
            self.store(migration_update=True, comment=_('Automatic update'), snapshot_store_user=False)

    @property
    def category(self):
        from wcs.categories import WorkflowCategory

        return WorkflowCategory.get(self.category_id, ignore_errors=True)

    @category.setter
    def category(self, category):
        if category:
            self.category_id = category.id
        elif self.category_id:
            self.category_id = None

    def get_sorted_functions(self):
        workflow_roles = list((self.roles or {}).items())
        workflow_roles.sort(key=lambda x: '' if x[0] == '_receiver' else misc.simplify(x[1]))
        return workflow_roles

    def store(
        self,
        comment=None,
        *args,
        migration_update=False,
        snapshot_store_user=True,
        application=None,
        **kwargs,
    ):
        assert not self.is_readonly()
        must_update = False
        has_geolocation = False
        if self.id:
            old_self = self.get(self.id, ignore_errors=True, ignore_migration=True)
            if old_self:
                old_endpoints = {x.id for x in old_self.get_endpoint_status()}
                if old_endpoints != {x.id for x in self.get_endpoint_status()}:
                    must_update = True
                old_criticality_levels = len(old_self.criticality_levels or [0])
                if old_criticality_levels != len(self.criticality_levels or [0]):
                    must_update = True
                try:
                    old_backoffice_fields = old_self.backoffice_fields_formdef.fields
                except AttributeError:
                    old_backoffice_fields = []
                try:
                    new_backoffice_fields = self.backoffice_fields_formdef.fields
                except AttributeError:
                    new_backoffice_fields = []
                if {x.id for x in old_backoffice_fields} != {x.id for x in new_backoffice_fields}:
                    must_update = True

                # if a geolocation action has been added tables may have to be updated
                if self.has_action('geolocate') and not old_self.has_action('geolocate'):
                    must_update = True
                    has_geolocation = True

        elif self.backoffice_fields_formdef:
            must_update = True

        if self.slug is None:
            # set slug if it's not yet there
            self.slug = self.get_new_slug()

        object_only = kwargs.pop('object_only', False)
        super().store(*args, **kwargs)
        if object_only:
            return

        # keep internal formdefs workflow_id in sync
        if self.backoffice_fields_formdef:
            self.backoffice_fields_formdef.workflow_id = self.id
        if self.variables_formdef:
            self.variables_formdef.workflow_id = self.id

        if get_publisher().snapshot_class:
            get_publisher().snapshot_class.snap(
                instance=self, comment=comment, store_user=snapshot_store_user, application=application
            )

        if not migration_update:
            job = ReindexOnWorkflowChange(
                workflow=self, must_update=must_update, has_geolocation=has_geolocation
            )
            if get_response():
                job.store()
                job.abort_similar()
                get_publisher().add_after_job(job)
                from wcs.admin.tests import TestsAfterJob

                for formdef in itertools.chain(self.formdefs(), self.carddefs()):
                    get_publisher().add_after_job(
                        TestsAfterJob(
                            formdef,
                            reason=_('Workflow: %s') % comment if comment else _('Change in workflow'),
                            triggered_by='workflow-change',
                        )
                    )
            else:
                job.id = job.DO_NOT_STORE
                job.execute()

    def get_admin_url(self):
        base_url = get_publisher().get_backoffice_url()
        workflow_url = '%s/workflows/%s/' % (base_url, self.id)
        snapshot_object = getattr(self, 'snapshot_object', None)
        if snapshot_object:
            workflow_url += f'history/{snapshot_object.id}/view/'
        return workflow_url

    def get_dependencies(self):
        yield self.category
        if self.variables_formdef and self.variables_formdef.fields:
            for field in self.variables_formdef.fields:
                yield from field.get_dependencies()
        if self.backoffice_fields_formdef and self.backoffice_fields_formdef.fields:
            for field in self.backoffice_fields_formdef.fields:
                yield from field.get_dependencies()
        if self.possible_status:
            for status in self.possible_status:
                yield from status.get_dependencies()
        if self.global_actions:
            for action in self.global_actions:
                yield from action.get_dependencies()

    def i18n_scan(self):
        location = 'workflows/%s/' % self.id
        if self.backoffice_fields_formdef and self.backoffice_fields_formdef.fields:
            for field in self.backoffice_fields_formdef.fields:
                yield from field.i18n_scan(location + 'backoffice-fields/')
        if self.possible_status:
            for status in self.possible_status:
                yield from status.i18n_scan(location + 'status/')
        if self.global_actions:
            for action in self.global_actions:
                yield from action.i18n_scan(location + 'global-actions/')

    @classmethod
    def has_key(cls, key):
        if key in ('_default', '_carddef_default'):
            return True
        return super().has_key(key)

    @classmethod
    def get(cls, id, ignore_errors=False, ignore_migration=False, column=None):
        if id == '_default':
            return cls.get_default_workflow()
        if id == '_carddef_default':
            from wcs.carddef import CardDef

            return CardDef.get_default_workflow()
        return super().get(id, ignore_errors=ignore_errors, ignore_migration=ignore_migration, column=column)

    def add_status(self, name, id=None):
        if [x for x in self.possible_status if x.name == name]:
            raise DuplicateStatusNameError()
        status = WorkflowStatus(name)
        status.parent = self

        if id is None:
            used_ids = [x.id for x in self.possible_status] + [x for x in (self.status_remapping or {})]
            if used_ids:
                status.id = str(max(lax_int(x) for x in used_ids) + 1)
            else:
                status.id = '1'
        else:
            status.id = id
        self.possible_status.append(status)
        return status

    def get_status(self, id):
        if id and id.startswith('wf-'):
            id = id[3:]
        for status in self.possible_status:
            if status.id == id:
                return status
        raise KeyError()

    def has_status(self, id):
        try:
            self.get_status(id)
            return True
        except KeyError:
            return False

    def get_possible_target_options(self):
        destinations = [
            (x.id, x.name, x.id, {'data-goto-url': x.get_admin_url()}) for x in self.possible_status or []
        ]

        # look for existing jumps that are dropping a mark
        statuses = self.possible_status or []
        global_actions = self.global_actions or []
        for status in statuses + global_actions:
            for item in status.items:
                if getattr(item, 'set_marker_on_status', False):
                    destinations.append(('_previous', _('Previously Marked Status'), '_previous', {}))
                    break
            else:
                continue
            break
        return destinations

    def get_backoffice_fields(self):
        def mark(field):
            field.is_backoffice_field = True
            field._formdef = self.backoffice_fields_formdef
            return field

        if self.backoffice_fields_formdef:
            return [mark(x) for x in self.backoffice_fields_formdef.fields] or []

        return []

    def get_all_items(self):
        for status in self.possible_status or []:
            yield from status.items or []
        for action in self.global_actions or []:
            yield from action.items or []

    def get_all_global_action_triggers(self):
        for action in self.global_actions or []:
            yield from action.triggers or []

    def has_action(self, action_type):
        return any(x.key == action_type for x in self.get_all_items())

    def add_global_action(self, name, id=None):
        if [x for x in self.global_actions if x.name == name]:
            raise DuplicateGlobalActionNameError()
        action = WorkflowGlobalAction(name)
        action.parent = self
        action.append_trigger('manual')

        if id is None:
            if self.global_actions:
                action.id = str(max(lax_int(x.id) for x in self.global_actions) + 1)
            else:
                action.id = '1'
        else:
            action.id = id
        self.global_actions.append(action)
        return action

    def get_global_manual_mass_actions(self):
        actions = []
        for action in self.global_actions or []:
            roles = []
            statuses = []
            for trigger in action.triggers or []:
                if not trigger.key == 'manual':
                    continue
                if not trigger.allow_as_mass_action:
                    continue
                roles.extend(trigger.roles or [])
                statuses.extend(trigger.get_statuses_ids())
                action.require_confirmation = trigger.require_confirmation
                action.confirmation_text = trigger.confirmation_text
            functions = [x for x in roles if x in self.roles]
            roles = [x for x in roles if x not in self.roles]
            if functions or roles:
                actions.append(
                    {'action': action, 'roles': roles, 'functions': functions, 'statuses': statuses}
                )
        return actions

    def get_status_manual_mass_actions(self):
        class StatusAction:
            def __init__(self, action):
                self.status_id = action.parent.id
                self.id = 'st-%s-%s-%s' % (self.status_id, action.identifier, action.id)
                self.name = action.get_label()
                self.status_action = True
                self.require_confirmation = action.require_confirmation
                self.confirmation_text = action.confirmation_text
                self.action = action

            def is_interactive(self):
                return False

        def get_actions(workflow):
            for status in workflow.possible_status or []:
                yield from status.items or []

        actions = []
        choices = [x for x in get_actions(self) if x.key == 'choice' and x.identifier]

        for action in choices:
            roles = action.by or []
            functions = [x for x in roles if x in (self.roles or [])]
            roles = [x for x in roles if x not in (self.roles or [])]
            if functions or roles:
                status_action = StatusAction(action)
                actions.append(
                    {
                        'action': status_action,
                        'roles': roles,
                        'functions': functions,
                        'statuses': [action.parent.id],
                    }
                )

        return actions

    def get_global_actions_for_user(self, formdata, user):
        actions = []
        for action in self.global_actions or []:
            if action.check_executable(formdata, user):
                actions.append(action)
        return actions

    def get_subdirectories(self, formdata):
        wf_status = formdata.get_status()
        if not wf_status:  # draft
            return []
        directories = []
        for action in self.global_actions:
            for trigger in action.triggers or []:
                directories.extend(trigger.get_subdirectories(formdata))
        directories.extend(wf_status.get_subdirectories(formdata))
        return directories

    def __setstate__(self, dict):
        self.__dict__.update(dict)
        for s in (self.possible_status or []) + (self.global_actions or []):
            s.parent = self
            triggers = getattr(s, 'triggers', None) or []
            for i, item in enumerate(s.items + triggers):
                item.parent = s
                if not item.id:
                    item.id = '%d' % (i + 1)
        if self.variables_formdef:
            self.variables_formdef.workflow = self
        if self.backoffice_fields_formdef:
            self.backoffice_fields_formdef.workflow = self
            self.backoffice_fields_formdef.__class__ = WorkflowBackofficeFieldsFormDef

    def get_waitpoint_status(self):
        return [x for x in self.possible_status if x.is_waitpoint()]

    def get_endpoint_status(self):
        return [x for x in self.possible_status if x.is_endpoint()]

    def get_not_endpoint_status(self):
        return [x for x in self.possible_status if not x.is_endpoint()]

    def remove_self(self):
        for form in self.formdefs():
            form.workflow_id = None
            form.store()
        StorableObject.remove_self(self)

    def export_to_xml(self, include_id=False):
        root = ET.Element('workflow')
        if include_id and self.id and not str(self.id).startswith('_'):
            root.attrib['id'] = str(self.id)
        for attr in ('name', 'slug', 'documentation'):
            value = getattr(self, attr, None)
            if value:
                ET.SubElement(root, attr).text = value

        from wcs.categories import WorkflowCategory

        WorkflowCategory.object_category_xml_export(self, root, include_id=include_id)

        roles_node = ET.SubElement(root, 'roles')
        if self.roles:
            for role_id, role_label in sorted(self.roles.items()):
                role_node = ET.SubElement(roles_node, 'role')
                role_node.attrib['id'] = role_id
                role_node.text = role_label

        possible_status = ET.SubElement(root, 'possible_status')
        for status in self.possible_status:
            possible_status.append(status.export_to_xml(include_id=include_id))

        if self.global_actions:
            global_actions = ET.SubElement(root, 'global_actions')
            for action in self.global_actions:
                global_actions.append(action.export_to_xml(include_id=include_id))

        if self.criticality_levels:
            criticality_levels = ET.SubElement(root, 'criticality_levels')
            for level in self.criticality_levels:
                criticality_levels.append(level.export_to_xml())

        if self.variables_formdef:
            variables = ET.SubElement(root, 'variables')
            formdef = ET.SubElement(variables, 'formdef')
            ET.SubElement(formdef, 'name').text = '-'  # required by formdef xml import
            if self.variables_formdef.documentation:
                ET.SubElement(formdef, 'documentation').text = self.variables_formdef.documentation
            fields = ET.SubElement(formdef, 'fields')
            for field in self.variables_formdef.fields:
                fields.append(field.export_to_xml(include_id=include_id))

        if self.backoffice_fields_formdef:
            variables = ET.SubElement(root, 'backoffice-fields')
            formdef = ET.SubElement(variables, 'formdef')
            ET.SubElement(formdef, 'name').text = '-'  # required by formdef xml import
            if self.backoffice_fields_formdef.documentation:
                ET.SubElement(formdef, 'documentation').text = self.backoffice_fields_formdef.documentation
            fields = ET.SubElement(formdef, 'fields')
            for field in self.backoffice_fields_formdef.fields:
                fields.append(field.export_to_xml(include_id=include_id))

        if self.status_remapping:
            status_remapping = ET.SubElement(root, 'status-remapping')
            for v in self.status_remapping.values():
                remap = ET.SubElement(status_remapping, 'remap')
                remap.attrib['status'] = v['status']
                remap.attrib['action'] = v['action']
                remap.attrib['timestamp'] = v['timestamp']

        return root

    @classmethod
    def import_from_xml(
        cls,
        fd,
        include_id=False,
        check_datasources=True,
        check_deprecated=False,
        ignore_missing_dependencies=False,
    ):
        try:
            tree = ET.parse(fd)
        except Exception:
            raise ValueError()
        workflow = cls.import_from_xml_tree(
            tree,
            include_id=include_id,
            check_datasources=check_datasources,
            check_deprecated=check_deprecated,
            ignore_missing_dependencies=ignore_missing_dependencies,
        )

        if workflow.slug and cls.get_by_slug(workflow.slug):
            # slug already in use, reset so a new one will be generated on store()
            workflow.slug = None

        return workflow

    @classmethod
    def import_from_xml_tree(
        cls,
        tree,
        include_id=False,
        snapshot=False,
        check_datasources=True,
        check_deprecated=False,
        ignore_missing_dependencies=False,
    ):
        from wcs.backoffice.deprecations import DeprecatedElementsDetected, DeprecationsScan
        from wcs.categories import WorkflowCategory
        from wcs.formdef import FormDef

        workflow = cls()
        if tree.find('name') is None or not tree.find('name').text:
            raise WorkflowImportError(_('Missing name'))

        # if the tree we get is actually a ElementTree for real, we get its
        # root element and go on happily.
        if not ET.iselement(tree):
            tree = tree.getroot()

        if tree.tag != 'workflow':
            raise WorkflowImportError(
                _('Provided XML file is invalid, it starts with a <%(seen)s> tag instead of <%(expected)s>')
                % {'seen': tree.tag, 'expected': 'workflow'}
            )

        if include_id and tree.attrib.get('id'):
            workflow.id = tree.attrib.get('id')

        workflow.name = xml_node_text(tree.find('name'))

        for attribute in ('slug', 'documentation'):
            if tree.find(attribute) is not None:
                setattr(workflow, attribute, xml_node_text(tree.find(attribute)))

        WorkflowCategory.object_category_xml_import(workflow, tree, include_id=include_id)

        if tree.find('roles') is not None:
            workflow.roles = {}
            for role_node in tree.findall('roles/role'):
                workflow.roles[role_node.attrib['id']] = xml_node_text(role_node)

        unknown_referenced_objects_details = collections.defaultdict(set)
        workflow.possible_status = []
        for status in tree.find('possible_status'):
            status_o = WorkflowStatus()
            status_o.parent = workflow
            try:
                status_o.init_with_xml(
                    status,
                    include_id=include_id,
                    snapshot=snapshot,
                    check_datasources=check_datasources,
                )
            except WorkflowImportUnknownReferencedError as e:
                for k, v in e._details.items():
                    unknown_referenced_objects_details[k].update(v)
            except FormdefImportError as e:
                raise WorkflowImportError(e.msg, details=e.details)
            else:
                workflow.possible_status.append(status_o)

        workflow.global_actions = []
        global_actions = tree.find('global_actions')
        if global_actions is not None:
            for action in global_actions:
                action_o = WorkflowGlobalAction()
                action_o.parent = workflow
                try:
                    action_o.init_with_xml(
                        action, include_id=include_id, snapshot=snapshot, check_datasources=check_datasources
                    )
                except FormdefImportUnknownReferencedError as e:
                    for k, v in e._details.items():
                        unknown_referenced_objects_details[k].update(v)
                except FormdefImportError as e:
                    raise WorkflowImportError(e.msg, details=e.details)
                else:
                    workflow.global_actions.append(action_o)

        workflow.criticality_levels = []
        criticality_levels = tree.find('criticality_levels')
        if criticality_levels is not None:
            for level in criticality_levels:
                level_o = WorkflowCriticalityLevel()
                level_o.init_with_xml(level)
                workflow.criticality_levels.append(level_o)

        variables = tree.find('variables')
        if variables is not None:
            formdef = variables.find('formdef')
            try:
                imported_formdef = FormDef.import_from_xml_tree(
                    formdef, include_id=True, snapshot=snapshot, check_datasources=check_datasources
                )
            except FormdefImportUnknownReferencedError as e:
                for k, v in e._details.items():
                    unknown_referenced_objects_details[k].update(v)
            except FormdefImportError as e:
                raise WorkflowImportError(e.msg, details=e.details)
            else:
                workflow.variables_formdef = WorkflowVariablesFieldsFormDef(workflow=workflow)
                workflow.variables_formdef.documentation = imported_formdef.documentation
                workflow.variables_formdef.fields = imported_formdef.fields

        variables = tree.find('backoffice-fields')
        if variables is not None:
            formdef = variables.find('formdef')
            try:
                imported_formdef = FormDef.import_from_xml_tree(
                    formdef, include_id=True, snapshot=snapshot, check_datasources=check_datasources
                )
            except FormdefImportUnknownReferencedError as e:
                for k, v in e._details.items():
                    unknown_referenced_objects_details[k].update(v)
            except FormdefImportError as e:
                raise WorkflowImportError(e.msg, details=e.details)
            else:
                workflow.backoffice_fields_formdef = WorkflowBackofficeFieldsFormDef(workflow=workflow)
                workflow.backoffice_fields_formdef.documentation = imported_formdef.documentation
                workflow.backoffice_fields_formdef.fields = imported_formdef.fields

        if tree.find('status-remapping') is not None:
            workflow.status_remapping = {}
            for remap_node in tree.findall('status-remapping/remap'):
                workflow.status_remapping[remap_node.attrib['status']] = {
                    'action': remap_node.attrib.get('action'),
                    'status': remap_node.attrib.get('status'),
                    'timestamp': remap_node.attrib.get('timestamp'),
                }

        if unknown_referenced_objects_details and not ignore_missing_dependencies:
            raise WorkflowImportUnknownReferencedError(
                _('Unknown referenced objects'), details=unknown_referenced_objects_details
            )

        if check_deprecated:
            # check for deprecated elements
            job = DeprecationsScan()
            try:
                job.check_deprecated_elements_in_object(workflow)
            except DeprecatedElementsDetected as e:
                raise WorkflowImportError(str(e))

        return workflow

    def get_list_of_roles(
        self,
        include_logged_in_users=True,
        include_signed_calls=False,
        include_submitter=True,
        current_values=None,
    ):
        t = []
        if include_submitter:
            t.append(('_submitter', pgettext_lazy('role', 'User'), '_submitter'))
        for workflow_role in self.roles.items():
            t.append(list(workflow_role) + [workflow_role[0]])
        if include_logged_in_users:
            t.append((logged_users_role().id, logged_users_role().name, logged_users_role().id))
        if include_signed_calls:
            t.append(('_signed_calls', _('Signed API calls'), '_signed_calls'))
        include_roles = not (get_publisher().has_site_option('workflow-functions-only'))
        if not include_roles and current_values:
            known_ids = {x[0] for x in t}
            has_separator = False
            for value in current_values:
                if value not in known_ids:
                    role = get_publisher().role_class.get(value, ignore_errors=True)
                    if role:
                        known_ids.add(value)
                        if not has_separator:
                            t.append(('', '----', ''))
                            has_separator = True
                        label = '❗ %s (%s)' % (role.name, _('direct role, legacy'))
                        t.append((value, label, value))

        if include_roles and get_user_roles():
            # use empty string instead of None so it's not automatically
            # picked as default value by the browser
            t.append(('', '----', ''))
            existing_labels = {str(x[1]) for x in t}
            t.extend(
                [
                    (x[0], x[1] if x[1] not in existing_labels else '%s [%s]' % (x[1], _('role')), x[2])
                    for x in get_user_roles()
                ]
            )
        return t

    def get_add_role_label(self):
        if get_publisher().has_site_option('workflow-functions-only'):
            return _('Add Function')
        return _('Add Function or Role')

    def render_list_of_roles(self, roles):
        return render_list_of_roles(self, roles)

    def get_json_export_dict(self, include_id=False):
        root = {}
        root['name'] = self.name
        if include_id and self.id:
            root['id'] = str(self.id)
        roles = root['functions'] = {}
        for role, label in self.roles.items():
            roles[role] = label
        statuses = root['statuses'] = []
        endpoint_status_ids = [s.id for s in self.get_endpoint_status()]
        waitpoint_status_ids = [s.id for s in self.get_waitpoint_status()]
        for status in self.possible_status:
            statuses.append(
                {
                    'id': status.id,
                    'name': status.name,
                    'forced_endpoint': status.forced_endpoint,
                    'endpoint': status.id in endpoint_status_ids,
                    'waitpoint': status.id in waitpoint_status_ids,
                }
            )
        root['fields'] = []
        for field in self.get_backoffice_fields():
            root['fields'].append(field.export_to_json(include_id=include_id))

        root['actions'] = {}
        for trigger in self.get_all_global_action_triggers():
            if trigger.key == 'webservice' and trigger.identifier:
                root['actions'][f'global-action:{trigger.identifier}'] = {
                    'label': f'{trigger.parent.name} ({trigger.identifier})'
                }

        for status in self.possible_status:
            for item in status.items:
                if item.key == 'jump' and item.trigger:
                    root['actions'][f'jump:{item.trigger}'] = {
                        'label': f'{item.parent.name} ({item.trigger})'
                    }
                elif item.key == 'editable' and get_publisher().has_site_option(
                    'api-include-editable-action'
                ):
                    item_label = item.label or _('Edit')
                    root['actions'][f'link:edit:{item.parent.id}-{item.id}'] = {
                        'label': f'{item_label} ({item.parent.name})'
                    }

        return root

    @classmethod
    def get_unknown_workflow(cls):
        workflow = Workflow(name=_('Unknown'))
        workflow.id = '_unknown'
        return workflow

    @classmethod
    def get_default_workflow(cls):
        from .qommon.admin.emails import EmailsDirectory

        # force_str() is used on lazy gettext calls as the default workflow is used
        # in tests as the basis for other ones and lazy gettext would fail pickling.
        workflow = Workflow(name=force_str(_('Default')))
        workflow.id = '_default'
        workflow.slug = '_default'
        workflow.roles = {'_receiver': force_str(_('Recipient'))}
        just_submitted_status = workflow.add_status(force_str(_('Just Submitted')), 'just_submitted')
        just_submitted_status.set_visibility_mode('restricted')
        new_status = workflow.add_status(force_str(_('New')), 'new')
        new_status.colour = '#66FF00'
        rejected_status = workflow.add_status(force_str(_('Rejected')), 'rejected')
        rejected_status.colour = '#FF3300'
        accepted_status = workflow.add_status(force_str(_('Accepted')), 'accepted')
        accepted_status.colour = '#66CCFF'
        finished_status = workflow.add_status(force_str(_('Finished')), 'finished')
        finished_status.colour = '#CCCCCC'

        if EmailsDirectory.is_enabled('new_receiver'):
            notify_new_receiver_email = just_submitted_status.add_action(
                'sendmail', id='_notify_new_receiver_email'
            )
            notify_new_receiver_email.to = ['_receiver']
            notify_new_receiver_email.subject = EmailsDirectory.get_subject('new_receiver')
            notify_new_receiver_email.body = EmailsDirectory.get_body('new_receiver')

        if EmailsDirectory.is_enabled('new_user'):
            notify_new_user_email = just_submitted_status.add_action('sendmail', id='_notify_new_user_email')
            notify_new_user_email.to = ['_submitter']
            notify_new_user_email.subject = EmailsDirectory.get_subject('new_user')
            notify_new_user_email.body = EmailsDirectory.get_body('new_user')

        jump_to_new = just_submitted_status.add_action('jump', id='_jump_to_new')
        jump_to_new.status = new_status.id

        if EmailsDirectory.is_enabled('change_receiver'):
            for status in (accepted_status, rejected_status, finished_status):
                notify_change_receiver_email = status.add_action(
                    'sendmail', id='_notify_change_receiver_email'
                )
                notify_change_receiver_email.to = ['_receiver']
                notify_change_receiver_email.subject = EmailsDirectory.get_subject('change_receiver')
                notify_change_receiver_email.body = EmailsDirectory.get_body('change_receiver')

        if EmailsDirectory.is_enabled('change_user'):
            for status in (accepted_status, rejected_status, finished_status):
                notify_change_user_email = status.add_action('sendmail', id='_notify_change_user_email')
                notify_change_user_email.to = ['_submitter']
                notify_change_user_email.subject = EmailsDirectory.get_subject('change_user')
                notify_change_user_email.body = EmailsDirectory.get_body('change_user')

        for status in (new_status, accepted_status):
            commentable = status.add_action('commentable', id='_commentable')
            commentable.by = ['_submitter', '_receiver']

        accept = new_status.add_action('choice', id='_accept')
        accept.label = force_str(_('Accept'))
        accept.by = ['_receiver']
        accept.status = accepted_status.id

        reject = new_status.add_action('choice', id='_reject')
        reject.label = force_str(_('Reject'))
        reject.by = ['_receiver']
        reject.status = rejected_status.id

        finish = accepted_status.add_action('choice', id='_finish')
        finish.label = force_str(_('Finish'))
        finish.by = ['_receiver']
        finish.status = finished_status.id

        return workflow

    def is_default(self):
        return str(self.id).startswith('_')

    def is_readonly(self):
        return self.is_default() or super().is_readonly()

    def formdefs(self, **kwargs):
        from wcs.formdef import FormDef

        order_by = kwargs.pop('order_by', 'name')
        criterias = [Equal('workflow_id', str(self.id))]
        if self.id in (None, '_default'):
            criterias = [Or(criterias + [Null('workflow_id')])]
        return list(FormDef.select(criterias, order_by=order_by, **kwargs))

    def carddefs(self, **kwargs):
        from wcs.carddef import CardDef

        order_by = kwargs.pop('order_by', 'name')
        criterias = [Equal('workflow_id', str(self.id))]
        if self.id in (None, '_carddef_default'):
            criterias = [Or(criterias + [Null('workflow_id')])]
        return list(CardDef.select(criterias, order_by=order_by, **kwargs))

    def mail_templates(self):
        slugs = [x.mail_template for x in self.get_all_items() if x.key == 'sendmail' and x.mail_template]
        criterias = [st.Contains('slug', slugs)]
        return list(MailTemplate.select(criterias, order_by='name'))

    def has_admin_access(self, user):
        if get_publisher().get_backoffice_root().is_global_accessible('workflows'):
            return True
        if not user:
            return False
        if not self.category_id:
            return False
        management_roles = {x.id for x in getattr(self.category, 'management_roles') or []}
        user_roles = set(user.get_roles())
        return management_roles.intersection(user_roles)


class XmlSerialisable:
    node_name = None
    key = None

    def export_to_xml(self, include_id=False):
        node = ET.Element(self.node_name)
        if self.key:
            node.attrib['type'] = self.key
        if include_id and getattr(self, 'id', None):
            node.attrib['id'] = self.id
        for attribute in self.get_parameters() + ('documentation',):
            if getattr(self, '%s_export_to_xml' % attribute, None):
                getattr(self, '%s_export_to_xml' % attribute)(node, include_id=include_id)
                continue
            if hasattr(self, attribute) and getattr(self, attribute) is not None:
                el = ET.SubElement(node, attribute)
                val = getattr(self, attribute)
                if isinstance(val, dict):
                    for k, v in val.items():
                        ET.SubElement(el, k).text = force_str(v, errors='replace')
                elif isinstance(val, list):
                    if attribute[-1] == 's':
                        atname = attribute[:-1]
                    else:
                        atname = 'item'
                    for v in val:
                        ET.SubElement(el, atname).text = force_str(str(v), errors='replace')
                elif isinstance(val, str):
                    el.text = force_str(val, errors='replace')
                else:
                    el.text = str(val)
        return node

    def init_with_xml(self, elem, include_id=False, snapshot=False, check_datasources=True):
        if include_id and elem.attrib.get('id'):
            self.id = elem.attrib.get('id')
        for attribute in self.get_parameters() + ('documentation',):
            el = elem.find(attribute)
            if getattr(self, '%s_init_with_xml' % attribute, None):
                getattr(self, '%s_init_with_xml' % attribute)(el, include_id=include_id, snapshot=snapshot)
                continue
            if el is None:
                continue
            if list(el):
                if isinstance(getattr(self, attribute), list):
                    v = [xml_node_text(x) or '' for x in el]
                elif isinstance(getattr(self, attribute), dict):
                    v = {}
                    for e in el:
                        v[e.tag] = xml_node_text(e)
                else:
                    # ???
                    raise AssertionError
                setattr(self, attribute, v)
            else:
                if el.text is None:
                    setattr(self, attribute, None)
                elif el.text in ('False', 'True') and not isinstance(getattr(self, attribute), str):
                    # booleans
                    setattr(self, attribute, el.text == 'True')
                elif isinstance(getattr(self, attribute), int):
                    setattr(self, attribute, int(el.text))
                else:
                    setattr(self, attribute, xml_node_text(el))

    def _roles_export_to_xml(self, attribute, item, include_id=False, include_missing=False):
        if not hasattr(self, attribute) or not getattr(self, attribute):
            return
        el = ET.SubElement(item, attribute)
        for role_id in getattr(self, attribute):
            if role_id is None:
                continue
            role_id = str(role_id)
            try:
                role_name, role_slug = get_role_name_and_slug(role_id)
            except KeyError:
                if not include_missing:
                    # skip broken/missing roles
                    continue
                role_name = role_id
                role_slug = None
            sub = ET.SubElement(el, 'item')
            if role_slug:
                sub.attrib['slug'] = role_slug
            sub.attrib['role_id'] = role_id
            sub.text = role_name

    def _roles_init_with_xml(self, attribute, elem, include_id=False, snapshot=False):
        if elem is None:
            setattr(self, attribute, [])
        else:
            imported_roles = []
            for child in elem:
                imported_roles.append(
                    self._get_role_id_from_xml(child, include_id=include_id, snapshot=snapshot)
                )
            setattr(self, attribute, imported_roles)

    def _role_export_to_xml(self, attribute, item, include_id=False, include_missing=False):
        if not hasattr(self, attribute) or not getattr(self, attribute):
            return
        role_id = str(getattr(self, attribute))
        try:
            role_name, role_slug = get_role_name_and_slug(role_id)
        except KeyError:
            if include_missing:
                # skip broken/missing roles
                return
            role_name, role_slug = role_id, role_id
        sub = ET.SubElement(item, attribute)
        if role_slug:
            sub.attrib['slug'] = role_slug
        if include_id:
            sub.attrib['role_id'] = role_id
        sub.text = role_name

    def _get_role_id_from_xml(self, elem, include_id=False, snapshot=False):
        if elem is None:
            return None

        value = xml_node_text(elem) or ''

        # look for known static values
        if value.startswith('_') or value == 'logged-users':
            return value

        # if we import using id, look at the role_id attribute
        if include_id and 'role_id' in elem.attrib:
            role_id = force_str(elem.attrib['role_id'])
            if get_publisher().role_class.get(role_id, ignore_errors=True):
                return role_id
            if WorkflowStatusItem.get_expression(role_id)['type'] == 'template':
                return role_id

        # if not using id, look up on the slug or name
        role_slug = elem.attrib.get('slug')
        role = get_publisher().role_class.resolve(uuid=None, slug=role_slug, name=value)
        if role:
            return role.id

        # if a computed value is possible and value looks like
        # an expression, use it
        if WorkflowStatusItem.get_expression(value)['type'] == 'template':
            return value

        # if the roles are managed by the idp, don't try further.
        if get_publisher() and get_cfg('sp', {}).get('idp-manage-roles') is True:
            if snapshot:
                return elem.attrib['role_id']  # snaphots always store the id
            raise WorkflowImportUnknownReferencedError(
                _('Unknown referenced role'), details={_('Unknown roles'): {value}}
            )

        # and if there's no match, create a new role
        role = get_publisher().role_class()
        role.name = value
        role.store()
        return role.id

    def _role_init_with_xml(self, attribute, elem, include_id=False, snapshot=False):
        setattr(
            self,
            attribute,
            self._get_role_id_from_xml(elem, include_id=include_id, snapshot=snapshot),
        )


class WorkflowGlobalActionTrigger(XmlSerialisable):
    node_name = 'trigger'

    def get_workflow(self):
        # self.parent: the status or global action,
        # self.parent.parent: the workflow
        return self.parent.parent

    def get_admin_url(self):
        return '%striggers/%s/' % (self.parent.get_admin_url(), self.id)

    def get_computed_strings(self):
        return []

    def submit_admin_form(self, form):
        for f in self.get_parameters():
            widget = form.get_widget(f)
            if widget:
                value = widget.parse()
                if hasattr(self, '%s_parse' % f):
                    value = getattr(self, '%s_parse' % f)(value)
                setattr(self, f, value)

    def get_subdirectories(self, formdata):
        return []

    def __getstate__(self):
        odict = self.__dict__.copy()
        if 'parent' in odict:
            del odict['parent']
        return odict


class WorkflowGlobalActionManualTrigger(WorkflowGlobalActionTrigger):
    key = 'manual'
    roles = None
    statuses = None
    allow_as_mass_action = True
    require_confirmation = False
    confirmation_text = None

    def get_parameters(self):
        return ('roles', 'statuses', 'allow_as_mass_action', 'require_confirmation', 'confirmation_text')

    def get_statuses_ids(self):
        statuses = self.statuses or []
        for status in self.get_workflow().possible_status or []:
            if status in statuses:
                yield status.id
            if '_endpoint_status' in statuses and status.is_endpoint():
                yield status.id
            if '_waitpoint_status' in statuses and (status.is_waitpoint() and not status.is_endpoint()):
                yield status.id
            if '_transition_status' in statuses and not (status.is_waitpoint() or status.is_endpoint()):
                yield status.id

    def get_status_option_label(self, status_id):
        if status_id == '_endpoint_status':
            return _('from final status')
        if status_id == '_waitpoint_status':
            return _('from pause status')
        if status_id == '_transition_status':
            return _('from transition status')
        try:
            return _('from status "%s"') % self.get_workflow().get_status(status_id).name
        except KeyError:
            return None

    def render_as_line(self):
        parts = [_('Manual')]
        status_labels = [self.get_status_option_label(x) for x in self.statuses or []]
        if status_labels:
            parts.append(_(' or ').join([str(x) for x in status_labels if x]))
        if self.roles:
            parts.append(_('by %s') % render_list_of_roles(self.get_workflow(), self.roles))
        else:
            parts.append(_('not assigned'))
        return ', '.join([str(x) for x in parts])

    def render_as_short_line(self):
        return _('Manual')

    def get_inspect_view(self):
        r = TemplateIO(html=True)
        r += htmlescape(self.render_as_line())
        r += htmltext('<ul>')
        r += htmltext('<li>%s%s %s</li>') % (
            _('Allow as mass action'),
            _(':'),
            _('Yes') if self.allow_as_mass_action else _('No'),
        )
        r += htmltext('<li>%s%s %s</li>') % (
            _('Require confirmation'),
            _(':'),
            _('Yes') if self.require_confirmation else _('No'),
        )
        if self.require_confirmation and self.confirmation_text:
            r += htmltext('<li>%s%s %s</li>') % (
                _('Custom text for confirmation popup'),
                _(':'),
                self.confirmation_text,
            )

        r += htmltext('</ul>')
        return r.getvalue()

    def form(self, workflow):
        form = Form(enctype='multipart/form-data')
        options = [(None, '---', None)]
        options += workflow.get_list_of_roles(include_logged_in_users=False)
        form.add(
            WidgetList,
            'roles',
            title=_('By'),
            element_type=SingleSelectWidget,
            value=self.roles,
            add_element_label=workflow.get_add_role_label(),
            element_kwargs={'render_br': False, 'options': options},
        )
        status_options = [
            (None, '---', ''),
            ('_waitpoint_status', _('Pause status'), '_wait_status'),
            ('_endpoint_status', _('Final status'), '_endpoint_status'),
            ('_transition_status', _('Transition status'), '_transition_status'),
            (None, '---', ''),
        ]
        status_options += [(str(x.id), x.name, str(x.id)) for x in self.get_workflow().possible_status]
        form.add(
            WidgetList,
            'statuses',
            title=_('Only display to following statuses'),
            element_type=SingleSelectWidget,
            value=self.statuses,
            add_element_label=_('Add a status'),
            element_kwargs={'render_br': False, 'options': status_options},
        )
        form.add(
            CheckboxWidget,
            'allow_as_mass_action',
            title=_('Allow as mass action'),
            value=self.allow_as_mass_action,
        )
        form.add(
            CheckboxWidget,
            'require_confirmation',
            title=_('Require confirmation'),
            value=self.require_confirmation,
            attrs={'data-dynamic-display-parent': 'true'},
        )
        form.add(
            StringWidget,
            'confirmation_text',
            title=_('Custom text for confirmation popup'),
            size=100,
            value=self.confirmation_text,
            attrs={
                'data-dynamic-display-child-of': 'require_confirmation',
                'data-dynamic-display-checked': 'true',
            },
        )

        return form

    def roles_export_to_xml(self, item, include_id=False):
        self._roles_export_to_xml('roles', item, include_id=include_id)

    def roles_init_with_xml(self, elem, include_id=False, snapshot=False):
        self._roles_init_with_xml('roles', elem, include_id=include_id, snapshot=snapshot)

    def statuses_init_with_xml(self, elem, include_id=False, snapshot=False):
        if not elem:
            return
        value = [xml_node_text(x) or '' for x in elem]
        setattr(self, 'statuses', value)

    def get_dependencies(self):
        yield from get_role_dependencies(self.roles)


class WorkflowGlobalActionTimeoutTriggerMarker(EvolutionPart):
    def __init__(self, timeout_id):
        self.timeout_id = timeout_id
        self.datetime = now()


class WorkflowGlobalActionTimeoutTrigger(WorkflowGlobalActionTrigger):
    key = 'timeout'
    anchor = None
    anchor_expression = ''
    anchor_template = ''
    anchor_status_first = None
    anchor_status_latest = None
    timeout = None

    def get_parameters(self):
        return (
            'anchor',
            'anchor_expression',
            'anchor_template',
            'anchor_status_first',
            'anchor_status_latest',
            'timeout',
        )

    def get_anchor_labels(self):
        options = [
            ('creation', _('Creation')),
            ('1st-arrival', _('First arrival in status')),
            ('latest-arrival', _('Latest arrival in status')),
            ('finalized', _('Arrival in final status')),
            ('anonymisation', _('Anonymisation')),
            ('template', _('String / Template')),
        ]
        return collections.OrderedDict(options)

    def properly_configured(self):
        workflow = self.get_workflow()
        if not (self.anchor and self.timeout):
            return False
        if self.anchor == '1st-arrival' and self.anchor_status_first:
            try:
                workflow.get_status(self.anchor_status_first)
            except KeyError:
                return False
        if self.anchor == 'latest-arrival' and self.anchor_status_latest:
            try:
                workflow.get_status(self.anchor_status_latest)
            except KeyError:
                return False
        return True

    def render_as_line(self):
        if self.properly_configured():
            return _('Automatic, %(timeout)s, relative to: %(anchor)s') % {
                'anchor': self.get_anchor_labels().get(self.anchor).lower(),
                'timeout': _('%s days') % self.timeout,
            }
        return _('Automatic (not configured)')

    def render_as_short_line(self):
        return _('Automatic')

    def get_inspect_view(self):
        r = TemplateIO(html=True)
        r += htmlescape(self.render_as_line())
        if self.anchor == 'template':
            r += htmltext('<ul>')
            r += htmltext('<li>%s%s %s</li>') % (
                _('String / Template with reference date'),
                _(':'),
                self.anchor_template,
            )
            r += htmltext('</ul>')
        return r.getvalue()

    def form(self, workflow):
        form = Form(enctype='multipart/form-data')
        options = self.get_anchor_labels()

        options = list(options.items())
        form.add(
            SingleSelectWidget,
            'anchor',
            title=_('Reference Date'),
            options=options,
            value=self.anchor,
            required=True,
            attrs={'data-dynamic-display-parent': 'true'},
        )

        form.add(
            StringWidget,
            'anchor_template',
            title=_('String / Template with reference date'),
            size=80,
            value=self.anchor_template,
            hint=_('This should be a date; it will only apply to open forms.'),
            attrs={
                'data-dynamic-display-child-of': 'anchor',
                'data-dynamic-display-value': _('String / Template'),
            },
        )

        possible_status = [(None, _('Current Status'), None)]
        possible_status.extend([('wf-%s' % x.id, x.name, x.id) for x in workflow.possible_status])
        form.add(
            SingleSelectWidget,
            'anchor_status_first',
            title=_('Status'),
            options=possible_status,
            value=self.anchor_status_first,
            attrs={
                'data-dynamic-display-child-of': 'anchor',
                'data-dynamic-display-value': _('First arrival in status'),
            },
        )
        form.add(
            SingleSelectWidget,
            'anchor_status_latest',
            title=_('Status'),
            options=possible_status,
            value=self.anchor_status_latest,
            attrs={
                'data-dynamic-display-child-of': 'anchor',
                'data-dynamic-display-value': _('Latest arrival in status'),
            },
        )

        def validate_timeout(value):
            if Template.is_template_string(value):
                return ComputedExpressionWidget.validate_template(value)
            match = re.match(r'^(-?[1-9]\d*|0)$', value or '')
            if not match or not match.group() == value:
                raise ValueError(_('wrong format'))
            if not (365 * -100 < float(value) < 365 * 100):  # ±100 years should be enough
                raise ValueError(_('invalid value, out of bounds'))

        form.add(
            StringWidget,
            'timeout',
            title=_('Delay (in days)'),
            value=self.timeout,
            validation_function=validate_timeout,
            required=True,
            hint=_(
                '''
                     Number of days relative to the reference date.  If the
                     reference date is computed from an expression, a negative
                     delay is accepted to trigger the action before the
                     date. This can be a template.'''
            ),
        )

        return form

    def get_timeout(self):
        timeout = self.timeout
        if Template.is_template_string(timeout):
            variables = get_publisher().substitutions.get_context_variables(mode='lazy')
            timeout = Template(self.timeout, autoescape=False).render(variables)
        return int(misc.parse_decimal(timeout, do_raise=True))

    def must_trigger(self, formdata, endpoint_status_ids):
        if formdata.status in endpoint_status_ids:
            if not (
                (self.anchor == '1st-arrival' and self.anchor_status_first in endpoint_status_ids)
                or (self.anchor == 'latest-arrival' and self.anchor_status_latest in endpoint_status_ids)
                or self.anchor in ('finalized', 'anonymisation')
            ):
                # don't trigger on finalized formdata (unless explicit anchor point)
                return False
        anchor_date = None
        if self.anchor == 'creation':
            anchor_date = formdata.receipt_time
        elif self.anchor == '1st-arrival':
            anchor_status = self.anchor_status_first or formdata.status
            for evolution in formdata.evolution:
                if evolution.status == anchor_status:
                    anchor_date = evolution.last_jump_datetime or evolution.time
                    break
        elif self.anchor == 'latest-arrival':
            anchor_status = self.anchor_status_latest or formdata.status
            latest_no_status_evolution = None
            for evolution in reversed(formdata.evolution):
                if evolution.status == anchor_status:
                    if latest_no_status_evolution:
                        evolution = latest_no_status_evolution
                    anchor_date = evolution.last_jump_datetime or evolution.time
                    break
                if evolution.status:
                    latest_no_status_evolution = None
                elif latest_no_status_evolution is None:
                    latest_no_status_evolution = evolution
        elif self.anchor == 'finalized':
            if formdata.status in endpoint_status_ids:
                for evolution in reversed(formdata.evolution):
                    if not evolution.status:
                        continue
                    if evolution.status in endpoint_status_ids:
                        anchor_date = evolution.time
                    else:
                        break
        elif self.anchor == 'anonymisation':
            anchor_date = formdata.anonymised
        elif self.anchor == 'template' and self.anchor_template:
            variables = get_publisher().substitutions.get_context_variables(mode='lazy')
            anchor_date = Template(self.anchor_template, autoescape=False).render(variables)

        if formdata.anonymised and self.anchor != 'anonymisation':
            # do not run on anonymised data (unless explicitely asked)
            return False

        # convert anchor_date to datetime.datetime()
        if isinstance(anchor_date, datetime.datetime):
            pass
        elif isinstance(anchor_date, datetime.date):
            anchor_date = datetime.datetime(
                year=anchor_date.year, month=anchor_date.month, day=anchor_date.day
            )
        elif isinstance(anchor_date, time.struct_time):
            anchor_date = datetime.datetime.fromtimestamp(time.mktime(anchor_date))
        elif isinstance(anchor_date, str) and anchor_date:
            try:
                anchor_date = get_as_datetime(anchor_date)
            except ValueError as e:
                with get_publisher().error_context(anchor_date=anchor_date):
                    get_publisher().record_error(
                        _('Error computing anchor date from value'),
                        exception=e,
                        context=_('Timeouts'),
                        notify=True,
                    )
                anchor_date = None
        elif anchor_date:
            # timestamp
            try:
                anchor_date = datetime.datetime.fromtimestamp(anchor_date)
            except TypeError as e:
                with get_publisher().error_context(anchor_date=anchor_date):
                    get_publisher().record_error(
                        _('Error computing anchor date from timestamp'),
                        exception=e,
                        context=_('Timeouts'),
                        notify=True,
                    )
                anchor_date = None

        if not anchor_date:
            return False

        try:
            timeout = self.get_timeout()
        except ValueError as e:
            # get the variables in the locals() namespace so they are
            # displayed within the trace.
            expression = self.timeout  # noqa pylint: disable=unused-variable
            with get_publisher().error_context(template=self.timeout):
                get_publisher().record_error(
                    _('Error computing timeout'), exception=e, context=_('Timeouts'), notify=True
                )
            return False

        anchor_date = anchor_date + datetime.timedelta(days=int(timeout))

        if not is_aware(anchor_date):
            anchor_date = make_aware(anchor_date, is_dst=True)

        return bool(localtime() > anchor_date)

    @classmethod
    def apply(cls, workflow):
        triggers = []
        for action in workflow.global_actions or []:
            triggers.extend(
                [
                    (action, x)
                    for x in action.triggers or []
                    if isinstance(x, WorkflowGlobalActionTimeoutTrigger) and x.properly_configured()
                ]
            )
        if not triggers:
            return

        not_endpoint_status = workflow.get_not_endpoint_status()
        not_endpoint_status_ids = ['wf-%s' % x.id for x in not_endpoint_status]
        endpoint_status = workflow.get_endpoint_status()
        endpoint_status_ids = ['wf-%s' % x.id for x in endpoint_status]

        # check "optimized" trigger with dedicated SQL criteria
        optimized_triggers = [
            (action, trigger)
            for action, trigger in triggers
            if 'form_var' not in str(trigger.timeout)
            and (
                (trigger.anchor in ('finalized', 'creation'))
                or (trigger.anchor in '1st-arrival' and trigger.anchor_status_first)
                or (trigger.anchor in 'latest-arrival' and trigger.anchor_status_latest)
            )
        ]
        triggers = [
            (action, trigger) for action, trigger in triggers if (action, trigger) not in optimized_triggers
        ]
        for action, trigger in optimized_triggers:
            formdef_timeouts = {}
            for formdef in itertools.chain(workflow.formdefs(), workflow.carddefs()):
                get_publisher().reset_formdata_state()
                get_publisher().substitutions.feed(formdef)
                try:
                    formdef_timeouts[f'{formdef.xml_root_node}/{formdef.id}'] = trigger.get_timeout()
                except ValueError:
                    # failed to get timeout
                    formdef_timeouts[f'{formdef.xml_root_node}/{formdef.id}'] = None
            if None in formdef_timeouts.values():
                # some invalid timeouts, go back to non-optimized path
                triggers.append((action, trigger))
                continue

            for formdef in itertools.chain(workflow.formdefs(), workflow.carddefs()):
                data_class = formdef.data_class()
                criterias = [StrictNotEqual('status', 'draft'), Null('anonymised')]
                # now we need the criteria for our timeout
                trigger_timeout = formdef_timeouts[f'{formdef.xml_root_node}/{formdef.id}']
                if trigger.anchor == 'creation':
                    # limit to forms/cards that are old enough
                    min_date = localtime() - datetime.timedelta(days=int(trigger_timeout))
                    criterias.append(LessOrEqual('receipt_time', min_date))
                else:
                    # limit to forms/cards with appropriate status in their history
                    if trigger.anchor == 'finalized':
                        status_ids = endpoint_status_ids
                    elif trigger.anchor == '1st-arrival':
                        status_ids = [trigger.anchor_status_first]
                    elif trigger.anchor == 'latest-arrival':
                        status_ids = [trigger.anchor_status_latest]
                    criterias.append(StatusReachedTimeoutCriteria(data_class, status_ids, trigger_timeout))
                cls.run_trigger_check(
                    formdef,
                    triggers=[(action, trigger)],
                    criterias=criterias,
                    endpoint_status_ids=endpoint_status_ids,
                )

        # check if triggers are defined relative to terminal status
        run_on_finalized = False
        run_on_anonymised = False
        for action, trigger in triggers:
            if trigger.anchor == 'finalized':
                run_on_finalized = True
            elif (
                trigger.anchor == 'creation'
                and workflow.possible_status
                and workflow.possible_status[0] in endpoint_status
            ):
                run_on_finalized = True
            elif (
                trigger.anchor == '1st-arrival'
                and trigger.anchor_status_first
                and workflow.get_status(trigger.anchor_status_first) in endpoint_status
            ):
                run_on_finalized = True
            elif (
                trigger.anchor == 'latest-arrival'
                and trigger.anchor_status_latest
                and workflow.get_status(trigger.anchor_status_latest) in endpoint_status
            ):
                run_on_finalized = True
            elif trigger.anchor == 'anonymisation':
                run_on_finalized = True
                run_on_anonymised = True

        criterias = [StrictNotEqual('status', 'draft')]
        if not run_on_anonymised:
            # do not run on anonymised
            criterias.append(Null('anonymised'))
        if not run_on_finalized:
            # limit to formdata that are not finalized
            criterias.append(Contains('status', not_endpoint_status_ids))

        for formdef in itertools.chain(workflow.formdefs(), workflow.carddefs()):
            cls.run_trigger_check(formdef, triggers, criterias, endpoint_status_ids)

    @classmethod
    def run_trigger_check(cls, formdef, triggers, criterias, endpoint_status_ids):
        data_class = formdef.data_class()
        for formdata in data_class.select_iterator(
            clause=criterias + [Null('workflow_processing_timestamp')], itersize=200
        ):
            get_publisher().reset_formdata_state()
            get_publisher().substitutions.feed(formdef)
            get_publisher().substitutions.feed(formdata)

            seen_triggers = []
            for part in formdata.iter_evolution_parts(WorkflowGlobalActionTimeoutTriggerMarker):
                seen_triggers.append(part.timeout_id)

            for action, trigger in triggers:
                if trigger.id in seen_triggers:
                    continue  # already triggered
                if trigger.must_trigger(formdata, endpoint_status_ids):
                    if not formdata.evolution:
                        continue
                    formdata.refresh_from_storage_if_updated()
                    if formdata.workflow_processing_timestamp:
                        continue
                    formdata.evolution[-1].add_part(WorkflowGlobalActionTimeoutTriggerMarker(trigger.id))
                    formdata.store()
                    formdata.record_workflow_event(
                        'global-action-timeout', global_action_id=action.id, trigger_id=trigger.id
                    )
                    with push_perform_workflow(formdata):
                        perform_items(action.items, formdata)
                    break

    def get_computed_strings(self):
        yield self.anchor_expression
        yield self.anchor_template
        yield self.timeout

    def get_dependencies(self):
        return []


class WorkflowGlobalActionWebserviceTrigger(WorkflowGlobalActionManualTrigger):
    key = 'webservice'
    identifier = None
    roles = None

    def get_parameters(self):
        return ('identifier', 'roles')

    def render_as_line(self):
        if self.identifier:
            return _('External call (%s)') % self.identifier
        return _('External call (not configured)')

    def render_as_short_line(self):
        return _('External call')

    def get_inspect_view(self):
        r = TemplateIO(html=True)
        r += htmlescape(self.render_as_line())
        r += htmltext('<ul>')
        possible_roles = self.get_workflow().get_list_of_roles(
            include_logged_in_users=True, include_signed_calls=True
        )
        if self.roles:
            option_value = ', '.join([str(x[1]) for x in possible_roles if x[0] in self.roles])
        else:
            option_value = _('None (Open API)')
        r += htmltext('<li>%s%s %s</li>') % (
            _('Roles required to trigger using HTTP hook'),
            _(':'),
            option_value,
        )
        r += htmltext('</ul>')
        return r.getvalue()

    def form(self, workflow):
        form = Form(enctype='multipart/form-data')
        form.add(VarnameWidget, 'identifier', title=_('Identifier'), required=True, value=self.identifier)
        options = workflow.get_list_of_roles(include_logged_in_users=True, include_signed_calls=True)
        form.add(
            WidgetListOfRoles,
            'roles',
            title=_('Roles required to trigger using HTTP hook'),
            value=self.roles,
            first_element_empty_label=_('None (Open API)'),
            roles=options,
            add_element_label=workflow.get_add_role_label(),
        )
        return form

    def get_subdirectories(self, formdata):
        from wcs.forms.workflows import WorkflowGlobalActionWebserviceHooksDirectory

        return [('hooks', WorkflowGlobalActionWebserviceHooksDirectory(formdata))]

    def get_dependencies(self):
        return []

    def check_executable(self, formdata, user):
        if not self.roles:
            return True

        for role in self.roles:  # noqa pylint: disable=not-an-iterable
            if role == logged_users_role().id and (user or is_url_signed()):
                return True
            if role == '_submitter':
                if formdata.is_submitter(user):
                    return True
                continue
            if not user:
                continue
            if formdata.get_function_roles(role).intersection(user.get_roles()):
                return True

        if '_signed_calls' in self.roles and is_url_signed():
            return True

        return False


class SerieOfActionsMixin:
    items = None
    has_live_form_support = True  # support for live evaluation in forms

    def add_action(self, type, id=None, prepend=False, init_with_default_values=True):
        if not self.items:
            self.items = []
        for klass in item_classes:
            if klass.key == type:
                o = klass()
                if id:
                    o.id = id
                elif self.items:
                    o.id = str(max(lax_int(x.id) for x in self.items) + 1)
                else:
                    o.id = '1'
                o.parent = self
                if init_with_default_values:
                    o.init_with_default_values()
                if prepend:
                    self.items.insert(0, o)
                else:
                    self.items.append(o)
                return o
        raise KeyError(type)

    def get_item(self, id):
        for item in self.items:
            if item.id == id:
                return item
        raise KeyError(id)

    def get_dependencies(self):
        for action in self.items or []:
            yield from action.get_dependencies()

    def migrate(self):
        changed = False
        remove_obsolete_actions = False
        for item in self.items:
            if isinstance(item, NoLongerAvailableAction):
                remove_obsolete_actions = True
            changed |= item.migrate()
        if remove_obsolete_actions:
            self.items = [x for x in self.items if not isinstance(x, NoLongerAvailableAction)]
            changed = True
        return changed

    def get_action_form(self, filled, user, displayed_fields=None):
        form = Form(action='', enctype='multipart/form-data', use_tokens=False)
        form.has_live_form_support = self.has_live_form_support
        form.attrs['id'] = 'wf-actions'
        form.add_hidden('_ts', str(filled.last_update_time.timestamp()))
        form.add_hidden('origin', get_request().form.get('origin') or '')
        for item in self.items:
            if not item.check_auth(filled, user):
                continue
            if not item.check_condition(filled):
                continue
            item.fill_form(form, filled, user, displayed_fields=displayed_fields)

        if form.widgets or form.submit_widgets:
            return form

        return None

    def get_active_items(self, form, filled, user):
        for item in self.items:
            if hasattr(item, 'by'):
                for role in item.by or []:
                    if role == logged_users_role().id:
                        break
                    if role == '_submitter':
                        if filled.is_submitter(user):
                            break
                        continue
                    if user is None:
                        continue
                    if filled.get_function_roles(role).intersection(user.get_roles()):
                        break
                else:
                    continue
            if not item.check_condition(filled):
                continue
            yield item

    def get_messages(self, formdata=None, position='top'):
        messages = []
        for item in self.items or []:
            if not hasattr(item, 'get_message'):
                continue
            if not item.check_condition(formdata):
                continue
            message = item.get_message(formdata, position=position)
            if message:
                messages.append(message)
        return messages

    def handle_form(self, form, filled, user, evo, check_replay=True):
        if check_replay and form.get('_ts') != str(filled.last_update_time.timestamp()):
            raise ReplayException()
        evo.time = localtime()
        evo.set_user(formdata=filled, user=user, check_submitter=get_request().is_in_frontoffice())
        if not filled.evolution:
            filled.evolution = []

        next_url = None

        for item in self.get_active_items(form, filled, user):
            next_url = item.submit_form(form, filled, user, evo)
            if next_url is True:
                break
            if next_url:
                if not form.has_errors():
                    if evo.parts or evo.status or evo.comment or evo.status:
                        # add evolution entry only if there's some content
                        # within, i.e. do not register anything in the case of
                        # a single edit action (where the evolution should be
                        # appended only after successful edit).
                        filled.evolution.append(evo)
                        if evo.status:
                            filled.status = evo.status
                        filled.store()
                return next_url

        return next_url


class WorkflowGlobalAction(SerieOfActionsMixin):
    id = None
    name = None
    triggers = None
    backoffice_info_text = None
    documentation = None
    has_live_form_support = False

    def __init__(self, name=None):
        self.name = name
        self.items = []

    def __getstate__(self):
        odict = self.__dict__.copy()
        if 'parent' in odict:
            del odict['parent']
        return odict

    def is_interactive(self):
        for item in self.items or []:
            if item.is_interactive():
                return True
        return False

    def get_global_interactive_form_url(self, formdef=None, ids=None):
        token = get_publisher().token_class(size=32)
        token.type = 'global-interactive-action'
        token.context = {
            'action_id': self.id,
            'form_slug': formdef.slug,
            'form_type': formdef.xml_root_node,
            'form_ids': ids,
            'return_url': get_request().get_path_query(),
        }
        token.store()
        if get_request().is_in_backoffice():
            return formdef.get_url(backoffice=True) + 'actions/%s/#' % token.id

        return '/actions/%s/#' % token.id

    def handle_form(self, form, filled, user, check_replay=True):
        evo = Evolution(filled)
        url = super().handle_form(form, filled, user, evo, check_replay=check_replay)
        if isinstance(url, str):
            return url
        filled.evolution.append(evo)
        if evo.status:
            filled.status = evo.status
        filled.store()

    def get_admin_url(self):
        return '%sglobal-actions/%s/' % (self.parent.get_admin_url(), self.id)

    def append_trigger(self, type):
        trigger_types = {
            'manual': WorkflowGlobalActionManualTrigger,
            'timeout': WorkflowGlobalActionTimeoutTrigger,
            'webservice': WorkflowGlobalActionWebserviceTrigger,
        }
        o = trigger_types.get(type)()
        if not self.triggers:
            self.triggers = []
        o.id = str(uuid.uuid4())
        o.parent = self
        self.triggers.append(o)
        return o

    def export_to_xml(self, include_id=False):
        status = ET.Element('action')

        for attr in ('id', 'name', 'backoffice_info_text', 'documentation'):
            value = getattr(self, attr, None)
            if value:
                ET.SubElement(status, attr).text = str(value)

        items = ET.SubElement(status, 'items')
        for item in self.items:
            items.append(item.export_to_xml(include_id=include_id))

        triggers = ET.SubElement(status, 'triggers')
        for trigger in self.triggers or []:
            triggers.append(trigger.export_to_xml(include_id=include_id))

        return status

    def init_with_xml(self, elem, include_id=False, snapshot=False, check_datasources=True):
        for attr in ('id', 'name', 'backoffice_info_text', 'documentation'):
            node = elem.find(attr)
            if node is not None:
                setattr(self, attr, xml_node_text(node))

        self.items = []
        for item in elem.find('items'):
            item_type = item.attrib['type']
            self.add_action(item_type)
            item_o = self.items[-1]
            item_o.parent = self
            item_o.init_with_xml(
                item, include_id=include_id, snapshot=snapshot, check_datasources=check_datasources
            )

        self.triggers = []
        for trigger in elem.find('triggers'):
            trigger_type = trigger.attrib['type']
            self.append_trigger(trigger_type)
            trigger_o = self.triggers[-1]
            if trigger.attrib.get('id'):
                trigger_o.id = trigger.attrib['id']
            trigger_o.parent = self
            trigger_o.init_with_xml(trigger, include_id=include_id, snapshot=snapshot)

    def get_dependencies(self):
        yield from super().get_dependencies()
        for trigger in self.triggers or []:
            yield from trigger.get_dependencies()

    def i18n_scan(self, base_location):
        location = '%s%s/' % (base_location, self.id)
        for trigger in self.triggers or []:
            if isinstance(trigger, WorkflowGlobalActionManualTrigger):
                yield location, None, self.name
                break
        for action in self.items or []:
            yield from action.i18n_scan(location)

    def check_executable(self, formdata, user):
        # check action is executable for given formdata and user (appropriate status and roles)
        current_status_id = (formdata.status or '').removeprefix('wf-')
        for trigger in self.triggers or []:
            self.trigger = trigger  # attach trigger to action, to have trigger options available in form
            if trigger.key == 'manual':
                if trigger.statuses and current_status_id not in trigger.get_statuses_ids():
                    continue
                if '_submitter' in (trigger.roles or []) and formdata.is_submitter(user):
                    return True
                if not user:
                    continue
                roles = set()
                for role_id in trigger.roles or []:
                    if role_id == '_submitter':
                        continue
                    roles |= formdata.get_function_roles(role_id)
                if roles.intersection(user.get_roles()):
                    return True
        return False


class WorkflowCriticalityLevel:
    id = None
    name = None
    colour = None

    def __init__(self, name=None, colour=None):
        self.name = name
        self.colour = colour
        self.id = str(random.randint(0, 100000))

    def export_to_xml(self, include_id=False):
        level = ET.Element('criticality-level')
        ET.SubElement(level, 'id').text = self.id or ''
        ET.SubElement(level, 'name').text = self.name
        if self.colour:
            ET.SubElement(level, 'colour').text = self.colour
        return level

    def init_with_xml(self, elem, include_id=False, snapshot=False):
        self.id = xml_node_text(elem.find('id'))
        self.name = xml_node_text(elem.find('name'))
        if elem.find('colour') is not None:
            self.colour = xml_node_text(elem.find('colour'))

    def migrate(self):
        if self.colour and not self.colour.startswith('#'):  # 2023-09-30
            self.colour = f'#{self.colour}'
            return True
        return False


class WorkflowStatus(SerieOfActionsMixin):
    id = None
    name = None
    visibility = None
    forced_endpoint = False
    colour = '#FFFFFF'
    backoffice_info_text = None
    documentation = None
    extra_css_class = ''
    loop_items_template = None
    after_loop_status = None

    has_live_form_support = True

    def __init__(self, name=None):
        self.name = name
        self.items = []

    def __eq__(self, other):
        if other is None:
            return False
        # this assumes both status are from the same workflow
        if isinstance(other, str):
            other_id = other
        else:
            other_id = other.id
        return self.id == other_id

    def migrate(self):
        changed = super().migrate()
        if self.colour and not self.colour.startswith('#'):  # 2023-09-30
            self.colour = f'#{self.colour}'
            changed = True
        if self.visibility and self.visibility not in (['__restricted__'], ['__hidden__']):  # 2024-01-29
            self.set_visibility_mode(self.get_visibility_mode())
            changed = True
        return changed

    def get_action_form(self, filled, user, displayed_fields=None):
        form = super().get_action_form(filled, user, displayed_fields=displayed_fields)
        if form is None:
            form = Form(enctype='multipart/form-data', use_tokens=False)
            form.attrs['id'] = 'wf-actions'
            form.add_hidden('_ts', str(filled.last_update_time.timestamp()))

        for action in filled.formdef.workflow.get_global_actions_for_user(filled, user):
            form.add_submit('button-action-%s' % action.id, get_publisher().translate(action.name))
            widget = form.get_widget('button-action-%s' % action.id)
            if widget:
                widget.backoffice_info_text = action.backoffice_info_text
                widget.ignore_form_errors = True
                widget.attrs['formnovalidate'] = 'formnovalidate'
                if action.trigger.require_confirmation:
                    get_response().add_javascript(['jquery.js', '../../i18n.js', 'qommon.js'])
                    widget.attrs = {'data-ask-for-confirmation': action.trigger.confirmation_text or 'true'}

        if form.widgets or form.submit_widgets:
            return form

        return None

    def get_workflow(self):
        return self.parent

    def get_admin_url(self):
        return self.get_workflow().get_admin_url() + 'status/%s/' % self.id

    def evaluate_live_form(self, form, filled, user):
        for item in self.get_active_items(form, filled, user):
            item.evaluate_live_form(form, filled, user)

    def handle_form(self, form, filled, user, check_replay=True):
        # check for global actions
        if check_replay and form.get('_ts') != str(filled.last_update_time.timestamp()):
            raise ReplayException()
        for action in filled.formdef.workflow.get_global_actions_for_user(filled, user):
            if form.get_submit() == 'button-action-%s' % action.id:
                if action.is_interactive():
                    return action.get_global_interactive_form_url(formdef=filled.formdef, ids=[filled.id])
                filled.record_workflow_event('global-action-button', global_action_id=action.id)
                return filled.perform_global_action(action.id, user)

        evo = Evolution(filled)
        url = super().handle_form(form, filled, user, evo, check_replay=check_replay)

        button = form.get_widget(form.get_submit())  # get clicked button
        if hasattr(button, 'action_id'):
            # some actions won't have a button name (e.g. a click on a "add block row" button),
            # and some actual buttons won't have an action_id ("editable" action).
            filled.record_workflow_event('button', action_item_id=button.action_id)

        if isinstance(url, str):
            return url
        if form.has_errors():
            return

        filled.evolution.append(evo)

        if evo.status:
            filled.status = evo.status
        filled.store()

        if get_publisher().has_site_option('perform-workflow-as-job'):
            return filled.perform_workflow_as_job()

        return filled.perform_workflow()

    def get_subdirectories(self, formdata):
        subdirectories = []
        for item in self.items:
            if item.directory_name:
                subdirectories.append((item.directory_name, item.directory_class(formdata, item, self)))
        return subdirectories

    def get_visibility_mode(self):
        if not self.visibility:
            return 'all'
        if self.visibility == ['__hidden__']:
            return 'hidden'
        # self.visibility will be ['__restricted__'] or a list of function slugs,
        # in both case it is advertised as restricted in the UI.
        return 'restricted'

    def get_visibility_mode_str(self):
        return {
            'all': '',
            'hidden': _('This status is hidden.'),
            'restricted': _('This status is hidden from the user.'),
        }.get(self.get_visibility_mode())

    def set_visibility_mode(self, mode):
        if mode == 'all':
            self.visibility = None
        elif mode == 'restricted':
            self.visibility = ['__restricted__']
        elif mode == 'hidden':
            self.visibility = ['__hidden__']

    def get_visibility_restricted_roles(self):
        if not self.visibility:  # no restriction -> visible
            return []
        if self.visibility == ['__restricted__']:
            return list(self.get_workflow().roles.keys())  # to all functions
        return self.visibility[:]

    def is_visible(self, formdata, user):
        if not self.visibility:  # no restriction -> visible
            return True
        if get_request() and get_request().is_in_frontoffice():
            # always hide in front
            return False
        if user and user.is_admin:
            return True

        if user:
            user_roles = set(user.get_roles())
            user_roles.add(logged_users_role().id)
        else:
            user_roles = set()

        visibility_roles = self.get_visibility_restricted_roles()
        for item in self.items or []:
            if not hasattr(item, 'by') or not item.by:
                continue
            visibility_roles.extend(item.by)

        for role in visibility_roles:
            if role != '_submitter':
                if formdata.get_function_roles(role).intersection(user_roles):
                    return True
        return False

    def is_endpoint(self):
        # an endpoint status is a status that marks the end of the workflow; it
        # can either be computed automatically (if there's no way out of the
        # status) or be set manually (to mark the expected end while still
        # allowing to go back and re-enter the workflow).
        if self.forced_endpoint:
            return True
        if self.after_loop_status and self.loop_items_template:
            return False
        endpoint = True
        for item in self.items:
            endpoint = endpoint and item.endpoint
            if endpoint is False:
                break
        return endpoint

    def is_waitpoint(self):
        # a waitpoint status is a status waiting for an event (be it user
        # interaction or something else), but can also be an endpoint (where
        # the user would wait, infinitely).
        waitpoint = False
        endpoint = True
        if self.after_loop_status and self.loop_items_template:
            return self.forced_endpoint
        if self.forced_endpoint:
            endpoint = True
        else:
            for item in self.items:
                endpoint = item.endpoint and endpoint
                waitpoint = item.waitpoint or waitpoint
        return bool(endpoint or waitpoint)

    def get_contrast_color(self):
        colour = self.colour or 'ffffff'
        return misc.get_foreground_colour(colour)

    def get_status_label(self):
        label = self.name
        if self.loop_items_template:
            label += ' (%s)' % _('With loop')
        return label

    def get_loop_target_status(self, formdata=None):
        if not self.after_loop_status or not self.loop_items_template:
            return None
        if self.after_loop_status == '_previous':
            if not formdata:  # when generating workflow SVG
                return
            return formdata.pop_previous_marked_status()

        try:
            return self.get_workflow().get_status(self.after_loop_status)
        except KeyError:
            if formdata:
                # do not log when rendering the workflow diagram
                message = _(
                    'reference to invalid status in workflow %(workflow)s, status %(status)s, status after loop'
                ) % {
                    'workflow': self.get_workflow().name,
                    'status': self.name,
                }
                get_publisher().record_error(message, workflow=self.get_workflow())

    def get_loop_jump_label(self):
        return _('Jump after loop')

    def get_loop_items(self, formdata):
        if not self.loop_items_template:
            return None

        with get_publisher().complex_data():
            try:
                value = WorkflowStatusItem.compute(
                    self.loop_items_template,
                    formdata=formdata,
                    raises=True,
                    allow_complex=True,
                )
            except Exception:
                # already logged by self.compute
                value = []  # so actions are not run
            else:
                value = get_publisher().get_cached_complex_data(value, loop_context=True)
                try:
                    value is None or iter(value)
                except TypeError:
                    get_publisher().record_error(_('Invalid value to be looped on (%r)') % value)
                    value = None
        if value is None:
            # empty block field returns None
            value = []
        return value

    @classmethod
    def get_status_loop(cls, index, items, item):
        return CompatibilityNamesDict(
            {
                'status_loop': {
                    'index0': index,
                    'index': index + 1,
                    'items': items,
                    'current_item': item,
                    'first': (index == 0),
                    'last': (index == len(items) - 1),
                }
            }
        )

    def __getstate__(self):
        odict = self.__dict__.copy()
        if 'parent' in odict:
            del odict['parent']
        return odict

    def export_to_xml(self, include_id=False):
        status = ET.Element('status')

        for attr in (
            'id',
            'name',
            'colour',
            'extra_css_class',
            'backoffice_info_text',
            'loop_items_template',
            'after_loop_status',
            'documentation',
        ):
            value = getattr(self, attr, None)
            if value:
                ET.SubElement(status, attr).text = str(value)

        if self.forced_endpoint:
            ET.SubElement(status, 'forced_endpoint').text = 'true'

        visibility_node = ET.SubElement(status, 'visibility')
        for role in self.visibility or []:
            ET.SubElement(visibility_node, 'role').text = str(role)

        items = ET.SubElement(status, 'items')
        for item in self.items:
            items.append(item.export_to_xml(include_id=include_id))

        return status

    def init_with_xml(self, elem, include_id=False, snapshot=False, check_datasources=True):
        for attr in (
            'id',
            'name',
            'colour',
            'extra_css_class',
            'backoffice_info_text',
            'loop_items_template',
            'after_loop_status',
            'documentation',
        ):
            node = elem.find(attr)
            if node is not None:
                setattr(self, attr, xml_node_text(node))

        if elem.find('forced_endpoint') is not None:
            self.forced_endpoint = elem.find('forced_endpoint').text == 'true'

        self.visibility = []
        for visibility_role in elem.findall('visibility/role'):
            self.visibility.append(visibility_role.text)

        self.items = []
        unknown_referenced_objects_details = collections.defaultdict(set)
        for item in elem.find('items'):
            item_type = item.attrib['type']
            self.add_action(item_type, init_with_default_values=False)
            item_o = self.items[-1]
            item_o.parent = self
            try:
                item_o.init_with_xml(
                    item,
                    include_id=include_id,
                    snapshot=snapshot,
                    check_datasources=check_datasources,
                )
            except (WorkflowImportUnknownReferencedError, FormdefImportUnknownReferencedError) as e:
                for k, v in e._details.items():
                    unknown_referenced_objects_details[k].update(v)
            except FormdefImportError as e:
                raise WorkflowImportError(e.msg, details=e.details)

        if unknown_referenced_objects_details:
            raise WorkflowImportUnknownReferencedError(
                _('Unknown referenced objects'), details=unknown_referenced_objects_details
            )

    def i18n_scan(self, base_location):
        location = '%s%s/' % (base_location, self.id)
        yield location, None, self.name
        for action in self.items or []:
            yield from action.i18n_scan(location)

    def get_dependencies(self):
        yield from super().get_dependencies()
        yield from get_dependencies_from_template(self.loop_items_template)

    def __repr__(self):
        return '<%s %s %r>' % (self.__class__.__name__, self.id, self.name)


def noop_mark(func):
    # mark method as not executing anything
    func.noop = True
    return func


class WorkflowStatusItem(XmlSerialisable):
    # noqa pylint: disable=too-many-public-methods
    node_name = 'item'
    description = 'XX'
    category = None  # (key, label)
    id = None
    condition = None
    documentation = None

    endpoint = True  # means it's not possible to interact, and/or cause a status change
    waitpoint = False  # means it's possible to wait (user interaction, or other event)
    ok_in_global_action = True  # means it can be used in a global action
    directory_name = None
    directory_class = None
    support_substitution_variables = False

    def __init__(self, parent=None):
        self.parent = parent

    @classmethod
    def init(cls):
        pass

    @classmethod
    def is_available(cls, workflow=None):
        return True

    def init_with_default_values(self):
        pass

    def get_workflow(self):
        # self.parent: the status or global action,
        # self.parent.parent: the workflow
        return self.parent.parent

    @classmethod
    def is_disabled(cls):
        disabled_workflow_actions = (
            get_publisher().get_site_option('disabled-workflow-actions') or ''
        ).split(',')
        disabled_workflow_actions = [f.strip() for f in disabled_workflow_actions if f.strip()]
        return cls.key in disabled_workflow_actions

    def migrate(self):
        return False

    def render_as_line(self):
        label = self.description
        details = self.get_line_details()
        if details:
            label += ' (%s)' % details
        if self.condition and self.condition.get('value'):
            label += ' (%s)' % _('conditional')
        return label

    def render_as_short_line(self):
        label = self.description
        if hasattr(self, 'get_line_short_details'):
            details = self.get_line_short_details()
            if details:
                label += ' %s' % details
        return label

    def get_line_details(self):
        return ''

    def get_admin_url(self):
        if self.parent:
            return self.parent.get_admin_url() + 'items/%s/' % self.id
        return ''

    def get_inspect_details(self):
        return getattr(self, 'label', '')

    def render_list_of_roles(self, roles):
        return self.get_workflow().render_list_of_roles(roles)

    def get_list_of_roles(self, include_logged_in_users=True, include_submitter=True, current_values=None):
        return self.get_workflow().get_list_of_roles(
            include_logged_in_users=include_logged_in_users,
            include_submitter=include_submitter,
            current_values=current_values,
        )

    def get_add_role_label(self):
        return self.get_workflow().get_add_role_label()

    def get_dependencies(self):
        yield from get_role_dependencies(getattr(self, 'by', None))
        yield from get_role_dependencies(getattr(self, 'to', None))
        for string in self.get_computed_strings():
            yield from get_dependencies_from_template(string)
        if getattr(self, 'condition', None):
            condition = self.condition
            if condition:
                if condition.get('type') == 'django':
                    yield from get_dependencies_from_template(condition.get('value'))

    @noop_mark
    def perform(self, formdata):
        pass

    def fill_form(self, form, formdata, user, **kwargs):
        pass

    def is_interactive(self):
        return False

    def evaluate_live_form(self, form, formdata, user):
        pass

    def submit_form(self, form, formdata, user, evo):
        pass

    def check_auth(self, formdata, user):
        if not hasattr(self, 'by'):
            return True

        for role in self.by or []:
            if user and role == logged_users_role().id:
                return True
            if role == '_submitter':
                t = formdata.is_submitter(user)
                if t is True:
                    return True
                continue
            if not user:
                continue
            if formdata.get_function_roles(role).intersection(user.get_roles()):
                return True

        return False

    def check_condition(self, formdata, record_errors=True):
        context = {'formdata': formdata, 'status_item': self}
        try:
            return Condition(self.condition, context, record_errors=record_errors).evaluate(
                source_label=str(self.description),
                source_url=self.get_admin_url(),
            )
        except RuntimeError:
            return False

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        if 'condition' in parameters:
            form.add(
                ConditionWidget,
                '%scondition' % prefix,
                title=_('Condition of execution of the action'),
                value=self.condition,
                size=40,
                advanced=True,
            )

        if 'attachments' in parameters:
            attachments_options, attachments = self.get_attachments_options()
            if len(attachments_options) > 1:
                form.add(
                    WidgetList,
                    '%sattachments' % prefix,
                    title=_('Attachments'),
                    element_type=SingleSelectWidgetWithOther,
                    value=attachments,
                    add_element_label=_('Add attachment'),
                    element_kwargs={'render_br': False, 'options': attachments_options},
                )
            else:
                form.add(
                    WidgetList,
                    '%sattachments' % prefix,
                    title=_('Attachments (templates)'),
                    element_type=StringWidget,
                    value=attachments,
                    add_element_label=_('Add attachment'),
                    element_kwargs={'render_br': False, 'size': 50},
                    advanced=True,
                )

    def get_parameters(self):
        return ('condition',)

    def get_computed_strings(self):
        # get list of computed strings, to check for deprecations and for dependencies
        return []

    def get_static_strings(self):
        # get list of non-computed parameter strings, for global search
        return []

    def get_inspect_parameters(self):
        return list(self.get_parameters())

    def get_parameters_view(self):
        r = TemplateIO(html=True)
        form = Form()
        parameters = [x for x in self.get_inspect_parameters() if getattr(self, x, None) is not None]
        for parameter in parameters:
            self.add_parameters_widgets(form, [parameter])
        r += htmltext('<ul>')
        for parameter in parameters:
            widget = form.get_widget(parameter)
            if not widget:
                continue
            r += htmltext('<li class="parameter-%s">' % parameter)
            if widget.get_title():
                r += htmltext('<span class="parameter">%s</span> ') % _('%s:') % widget.get_title()
            r += self.get_parameter_view_value(widget, parameter)
            r += htmltext('</li>')
        r += htmltext('</ul>')
        return r.getvalue()

    def get_backoffice_info_text_parameter_view_value(self):
        return htmltext(self.backoffice_info_text)

    def get_by_parameter_view_value(self):
        return self.render_list_of_roles(self.by)

    def get_to_parameter_view_value(self):
        return self.render_list_of_roles(self.to)

    def get_timeout_parameter_view_value(self):
        try:
            return seconds2humanduration(int(self.timeout or 0))
        except ValueError:
            return self.timeout  # probably an expression

    def get_status_parameter_view_value(self):
        for status in self.get_workflow().possible_status:
            if status.id == self.status:
                return htmltext('<a href="#status-%s">%s</a>') % (status.id, status.name)
        return _('Unknown (%s)') % self.status

    def get_parameter_view_value(self, widget, parameter):
        if hasattr(self, 'get_%s_parameter_view_value' % parameter):
            return getattr(self, 'get_%s_parameter_view_value' % parameter)()
        value = getattr(self, parameter)
        if isinstance(value, bool):
            return str(_('Yes') if value else _('No'))
        if hasattr(widget, 'options') and value:
            for option in widget.options:
                if isinstance(option, tuple):
                    if option[0] == value:
                        return str(option[1])
                else:
                    if option == value:
                        return option
            return '-'

        return str(value)

    def get_condition_parameter_view_value(self):
        value = self.condition
        if value and value.get('type') == 'django':
            return htmltext('<tt>%s</tt>') % value.get('value')

    def fill_admin_form(self, form):
        for parameter in self.get_parameters():
            self.add_parameters_widgets(form, [parameter])

    def submit_admin_form(self, form):
        for f in self.get_parameters():
            widget = form.get_widget(f)
            if widget:
                if hasattr(self, 'clean_%s' % f):
                    has_error = getattr(self, 'clean_%s' % f)(form)
                    if has_error:
                        continue
                value = widget.parse()
                if hasattr(self, '%s_parse' % f):
                    value = getattr(self, '%s_parse' % f)(value)
                setattr(self, f, value)

    @classmethod
    def get_expression(cls, var, allow_ezt=True):
        if not var:
            expression_type = 'text'
            expression_value = ''
        elif '{{' in var or '{%' in var or (allow_ezt and '[' in var):
            expression_type = 'template'
            expression_value = var
        else:
            expression_type = 'text'
            expression_value = var
        return {'type': expression_type, 'value': expression_value}

    @classmethod
    def compute(
        cls,
        var,
        *,
        render=True,
        raises=False,
        record_errors=True,
        allow_complex=False,
        allow_ezt=True,
        context=None,
        formdata=None,
        status_item=None,
    ):
        # noqa pylint: disable=too-many-arguments
        if not isinstance(var, str):
            return var

        expression = cls.get_expression(var, allow_ezt=allow_ezt)

        if not render:
            return var

        if expression['type'] == 'text':
            return expression['value']

        vars = get_publisher().substitutions.get_context_variables(
            'lazy' if expression['type'] == 'template' else None
        )
        vars.update(context or {})

        def log_exception(exception):
            if expression['type'] == 'template':
                summary = _('Failed to compute template')
            else:
                summary = _('Failed to compute Python expression')
            get_publisher().record_error(
                summary,
                formdata=formdata,
                status_item=status_item,
                expression=expression['value'],
                expression_type=expression['type'],
                exception=exception,
            )

        old_allow_complex_value = vars.get('allow_complex')
        vars['allow_complex'] = allow_complex
        # make sure complex data context manager is used
        assert (
            not vars['allow_complex'] or get_publisher().complex_data_cache is not None
        ), 'missing complex_data context manager'
        try:
            return Template(expression['value'], raises=raises, autoescape=False, record_errors=False).render(
                vars
            )
        except TemplateError as e:
            if record_errors:
                log_exception(e)
            if raises:
                raise
            return var
        finally:
            vars['allow_complex'] = old_allow_complex_value

    def get_computed_role_id(self, role_id):
        new_role_id = self.compute(str(role_id))
        if not new_role_id:
            return None
        if get_publisher().role_class.get(new_role_id, ignore_errors=True):
            return new_role_id
        # computed value, not an id, try to get role by slug
        new_role = get_publisher().role_class.get_on_index(new_role_id, 'slug', ignore_errors=True)
        if new_role:
            return new_role.id
        # fallback to role label
        for role in get_publisher().role_class.select():
            if role.name == new_role_id:
                return role.id
        return None

    def get_substitution_variables(self, formdata):
        return {}

    def get_target_status_url(self):
        if not getattr(self, 'status', None) or self.status == '_previous':
            return None

        targets = [x for x in self.get_workflow().possible_status if x.id == self.status]
        if not targets:
            return None

        return targets[0].get_admin_url()

    def get_target_status(self, formdata=None):
        """Returns a list of status this item can lead to."""
        if not getattr(self, 'status', None):
            return []

        if self.status == '_previous':
            if formdata is None:
                # must be in a formdata to compute destination, just give a
                # fake status for presentation purpose
                return [WorkflowStatus(_('Previously Marked Status'))]
            previous_status = formdata.pop_previous_marked_status()
            if previous_status:
                return [previous_status]
            return []

        targets = [x for x in self.get_workflow().possible_status if x.id == self.status]
        if not targets and formdata:  # do not log in presentation context: formdata is needed
            message = _(
                'reference to invalid status %(target)s in status %(status)s, action %(status_item)s'
            ) % {'target': self.status, 'status': self.parent.name, 'status_item': self.description}
            get_publisher().record_error(message, formdata=formdata, status_item=self)

        return targets

    def get_jump_label(self, target_id):
        '''Return the label to use on a workflow graph arrow'''
        if getattr(self, 'label', None):
            label = self.label
            if getattr(self, 'by', None):
                roles = self.get_workflow().render_list_of_roles(self.by)
                label += ' %s %s' % (_('by'), roles)
            if getattr(self, 'status', None) == '_previous':
                label += ' ' + str(_('(to last marker)'))
            if getattr(self, 'set_marker_on_status', False):
                label += ' ' + str(_('(and set marker)'))
            if getattr(self, 'condition', None):
                label += ' ' + str(_('(conditional)'))
        else:
            label = self.render_as_line()
        return label

    def add_jump_part(self, formdata, evo=None):
        if not self.identifier:
            return False
        for part in formdata.iter_evolution_parts(klass=JumpEvolutionPart, reverse=True):
            if part.identifier == self.identifier:
                return False
            break
        if evo is None:
            evo = formdata.evolution[-1]
        evo.add_part(JumpEvolutionPart(self.identifier))
        return True

    def get_backoffice_filefield_options(self):
        options = []
        for field in self.get_workflow().get_backoffice_fields():
            if field.key == 'file':
                options.append((field.id, field.label, field.id))
        return options

    def store_in_backoffice_filefield(
        self, formdata, backoffice_filefield_id, filename, content_type, content, force_not_malware=False
    ):
        filefield = [
            x
            for x in self.get_workflow().get_backoffice_fields()
            if x.id == backoffice_filefield_id and x.key == 'file'
        ]
        if filefield:
            upload = PicklableUpload(filename, content_type)
            upload.receive([content])
            if force_not_malware:
                upload.force_not_malware()
            formdata.data[backoffice_filefield_id] = upload
            formdata.store()

    def init_with_xml(self, elem, include_id=False, **kwargs):
        # always add id if present, regardless of include_id parameter
        elem_id = elem.attrib.get('id')
        if elem_id is not None:
            self.id = elem_id

        super().init_with_xml(elem, include_id=include_id, **kwargs)

    def by_export_to_xml(self, item, include_id=False):
        self._roles_export_to_xml('by', item, include_id=include_id)

    def by_init_with_xml(self, elem, include_id=False, snapshot=False):
        self._roles_init_with_xml('by', elem, include_id=include_id, snapshot=snapshot)

    def to_export_to_xml(self, item, include_id=False):
        self._roles_export_to_xml('to', item, include_id=include_id)

    def to_init_with_xml(self, elem, include_id=False, snapshot=False):
        self._roles_init_with_xml('to', elem, include_id, snapshot=snapshot)

    def condition_init_with_xml(self, node, include_id=False, snapshot=False):
        self.condition = None
        if node is None:
            return
        self.condition = {
            'type': xml_node_text(node.find('type')),
            'value': xml_node_text(node.find('value')),
        }

    def q_admin_lookup(self, workflow, status, component):
        return None

    def __getstate__(self):
        odict = self.__dict__.copy()
        if 'parent' in odict:
            del odict['parent']
        return odict

    def attachments_init_with_xml(self, elem, include_id=False, snapshot=False):
        if elem is None:
            self.attachments = None
        else:
            self.attachments = [xml_node_text(item) for item in elem.findall('attachment')]

    def get_attachments_options(self):
        attachments_options = [(None, '---', None)]
        varnameless = []
        for field in self.get_workflow().get_backoffice_fields():
            if field.key != 'file':
                continue
            if field.varname:
                codename = '{{form_var_%s_raw}}' % field.varname
            else:
                codename = '{{form_f%s}}' % field.id.replace('-', '_')  # = form_fbo<...>
                varnameless.append(codename)
            attachments_options.append((codename, field.label, codename))
        # filter: do not consider removed fields without varname
        attachments = [
            attachment
            for attachment in self.attachments or []
            if ((not attachment.startswith('{{form_fbo')) or (attachment in varnameless))
        ]
        return attachments_options, attachments

    def convert_attachments_to_uploads(self, extra_attachments=None):
        uploads = []

        attachments = []
        attachments.extend(self.attachments or [])
        attachments.extend(extra_attachments or [])

        # 1. attachments defined as templates
        with get_publisher().complex_data():
            for attachment in attachments[:]:
                if '{%' not in attachment and '{{' not in attachment:
                    continue
                attachments.remove(attachment)

                try:
                    attachment = WorkflowStatusItem.compute(attachment, allow_complex=True, raises=True)
                except Exception as e:
                    get_publisher().record_error(exception=e, context=_('Workflow attachments'), notify=True)
                else:
                    if attachment:
                        complex_value = get_publisher().get_cached_complex_data(attachment)
                        if complex_value:
                            uploads.append(complex_value)

        # 2. convert any value to a PicklableUpload
        for upload in uploads:
            if not isinstance(upload, PicklableUpload):
                try:
                    upload = FileField.convert_value_from_anything(upload)
                except ValueError as e:
                    get_publisher().record_error(exception=e, context=_('Workflow attachments'), notify=True)
                    continue

            yield upload

    def i18n_scan(self, base_location):
        return []

    def handle_markers_stack(self, formdata):
        if self.set_marker_on_status:
            if formdata.workflow_data and '_markers_stack' in formdata.workflow_data:
                markers_stack = formdata.workflow_data.get('_markers_stack')
            else:
                markers_stack = []
            markers_stack.append({'status_id': formdata.status[3:]})
            formdata.update_workflow_data({'_markers_stack': markers_stack})

    def get_workflow_test_action(self, *args, **kwargs):
        # get action to be used in workflow tests, None for skipping the action
        return None

    def __repr__(self):
        parent = getattr(self, 'parent', None)  # status or global action
        parts = [self.__class__.__name__, str(self.id)]
        if isinstance(parent, WorkflowGlobalAction):
            parts.append('in global action "%s" (%s)' % (parent.name, parent.id))
        elif isinstance(parent, WorkflowStatus):
            parts.append('in status "%s" (%s)' % (parent.name, parent.id))
        workflow = getattr(parent, 'parent', None)
        if workflow:
            parts.append('in workflow "%s" (%s)' % (workflow.name, workflow.id))
        return '<%s>' % ' '.join(parts)


class JumpEvolutionPart(EvolutionPart):
    def __init__(self, identifier):
        self.identifier = identifier


class WorkflowStatusJumpItem(WorkflowStatusItem):
    status = None
    endpoint = False
    set_marker_on_status = False
    category = 'status-change'
    identifier = None

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        if 'status' in parameters:
            form.add(
                SingleSelectWidget,
                '%sstatus' % prefix,
                title=_('Status'),
                value=self.status,
                options=[(None, '---', '', {})] + self.get_workflow().get_possible_target_options(),
            )

        if 'set_marker_on_status' in parameters:
            form.add(
                CheckboxWidget,
                '%sset_marker_on_status' % prefix,
                title=_('Set marker to jump back to current status'),
                value=self.set_marker_on_status,
                advanced=True,
            )

        if 'identifier' in parameters:
            form.add(
                VarnameWidget,
                '%sidentifier' % prefix,
                title=_('Identifier'),
                value=self.identifier,
                advanced=True,
            )

    def get_parameters(self):
        return ('status', 'set_marker_on_status', 'condition')


class NoLongerAvailableAction(WorkflowStatusItem):
    pass  # marker class, loadable from pickle files but removed in migrate()


class NoLongerAvailablePart(EvolutionPart):
    pass  # marker class, loadable from pickle files


def get_role_translation_label(workflow, role_id):
    if role_id == logged_users_role().id:
        return logged_users_role().name
    if role_id == '_submitter':
        return pgettext_lazy('role', 'User')
    if str(role_id).startswith('_'):
        return workflow.roles.get(role_id)
    try:
        return get_publisher().role_class.get(role_id).name
    except KeyError:
        return


def get_role_name_and_slug(role_id):
    role_id = str(role_id)
    if role_id.startswith('_') or role_id == 'logged-users':
        return (str(role_id), None)
    role = get_publisher().role_class.get(role_id)
    return (role.name, role.slug)


def render_list_of_roles(workflow, roles):
    t = []
    for r in roles:
        role_label = get_role_translation_label(workflow, r)
        if role_label:
            t.append(role_label)
    return ', '.join([str(x) for x in t])


item_classes = []


def register_item_class(klass):
    if klass.key not in [x.key for x in item_classes]:
        item_classes.append(klass)
        klass.init()


def get_formdata_template_context(formdata=None):
    ctx = get_publisher().substitutions.get_context_variables('lazy')
    if formdata:
        ctx['url'] = formdata.get_url()
        ctx['url_status'] = '%sstatus' % formdata.get_url()
        ctx['details'] = formdata.formdef.get_detailed_email_form(formdata, ctx['url'])
        ctx['name'] = formdata.formdef.name
        ctx['number'] = formdata.id
        if formdata.evolution and formdata.evolution[-1].comment:
            ctx['comment'] = formdata.evolution[-1].comment
        else:
            ctx['comment'] = ''
        ctx.update(formdata.get_as_dict())

        # compatibility vars
        ctx['before'] = ctx.get('form_previous_status')
        ctx['after'] = ctx.get('form_status')
        ctx['evolution'] = ctx.get('form_evolution')

    return ctx


def template_on_html_string(template):
    return template_on_formdata(None, template, ezt_format=ezt.FORMAT_HTML)


def template_on_formdata(formdata=None, template=None, **kwargs):
    assert template is not None
    if not Template.is_template_string(template):
        # no tags, no variables: don't even process formdata
        return template
    context = get_formdata_template_context(formdata)
    return template_on_context(context, template, **kwargs)


def template_on_context(context=None, template=None, **kwargs):
    assert template is not None
    if not Template.is_template_string(template):
        return template
    return Template(template, **kwargs).render(context)


class ReindexOnWorkflowChange(AfterJob):
    label = _('Reindexing cards and forms after workflow change')

    def __init__(self, workflow, must_update, has_geolocation):
        super().__init__()
        self.workflow_id = workflow.id
        self.must_update = must_update
        self.has_geolocation = has_geolocation

    def __eq__(self, other):
        return (
            isinstance(other, ReindexOnWorkflowChange)
            and other.workflow_id == self.workflow_id
            and other.must_update is self.must_update
            and other.has_geolocation is self.has_geolocation
        )

    def execute(self):
        must_update = self.must_update
        has_geolocation = self.has_geolocation
        workflow = Workflow.get(self.workflow_id)

        # instruct all related carddefs/formdefs to update.
        for formdef in itertools.chain(
            workflow.formdefs(ignore_migration=True, order_by='id'),
            workflow.carddefs(ignore_migration=True, order_by='id'),
        ):
            # always reload object so another formdef/workflow change happening
            # during the loop will be taken into account.
            formdef.refresh_from_storage()
            if must_update:
                if has_geolocation and not formdef.geolocations:
                    formdef.geolocations = {'base': str(_('Geolocation'))}
                    formdef.store(comment=_('Geolocation enabled by workflow'))
                formdef.update_storage()
            formdef.data_class().rebuild_security(must_update, increment=self.increment_count)


class FileWorkflow(StorableObject, Workflow):
    # legacy class for migration
    _names = 'workflows'
    id = None
    _reset_class = False

    def __init__(self, name=None):
        Workflow.__init__(self, name=name)


def load_extra():
    from . import wf

    for filename in glob.glob(os.path.join(wf.__path__[0], '*.py')):
        module_name = os.path.splitext(os.path.basename(filename))[0]
        if module_name == '__init__':
            continue
        module = import_module('wcs.wf.%s' % module_name)
        if hasattr(module, 'register_cronjob'):
            module.register_cronjob()

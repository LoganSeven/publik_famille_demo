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
import functools
import html
import itertools
import json
import pickle
import re
import urllib.parse

import unidecode
from django.utils.html import strip_tags
from django.utils.timezone import localtime, make_naive, now
from quixote import get_publisher, get_request, get_session
from quixote.errors import RequestError
from quixote.html import htmltext
from quixote.http_request import Upload

from wcs.sql_criterias import And, Contains, Equal, Greater, Intersects, Null, StrictNotEqual

from .qommon import _, misc
from .qommon.evalutils import make_datetime
from .qommon.fields import get_summary_display_actions, get_summary_field_details
from .qommon.storage import StorableObject
from .qommon.substitution import CompatibilityNamesDict, Substitutions
from .qommon.template import Template


class NoContentSnapshotAt(RequestError):
    pass


def get_dict_with_varnames(fields, data, formdata=None, varnames_only=False, include_files=True):
    new_data = {}
    for field in fields:
        if not hasattr(field, 'get_view_value'):
            continue
        raw_value = None
        if data is not None:
            value = data.get(field.id)
            if field.convert_value_to_str and field.keep_raw_value:
                raw_value = value
                value = field.convert_value_to_str(value)
            display_value = data.get('%s_display' % field.id)
        else:
            value = ''
            display_value = ''

        if not varnames_only:
            # add it as f$n$
            new_data['f%s' % field.id.replace('-', '_')] = value

            # also add it as 'field_' + normalized(field label)
            identifier_name = misc.simplify(field.label, space='_')
            new_data['field_' + identifier_name] = value

        # and finally add it as its manually defined variable name
        if field.varname:
            if field.store_display_value:
                new_data['var_%s_raw' % field.varname] = value
                new_data['var_%s' % field.varname] = display_value
            else:
                new_data['var_%s' % field.varname] = value
                if field.key == 'file':
                    new_data['var_%s_raw' % field.varname] = value
                    new_data['var_%s_url' % field.varname] = None
                    if value and hasattr(value, 'base_filename'):
                        if include_files is False:
                            del new_data[f'var_{field.varname}_raw']
                        new_data['var_%s' % field.varname] = value.base_filename
                        if formdata is not None:
                            new_data['var_%s_url' % field.varname] = '%s?f=%s' % (
                                formdata.get_file_base_url(),
                                field.id,
                            )
                elif raw_value is not None:
                    new_data['var_%s_raw' % field.varname] = raw_value
            if data is not None:
                structured_value = field.get_structured_value(data)
                if isinstance(structured_value, dict):
                    for k, v in structured_value.items():
                        if k in ('id', 'text'):
                            continue
                        new_data['var_%s_%s' % (field.varname, k)] = v
                if isinstance(structured_value, list):
                    for i, struct_value in enumerate(structured_value):
                        for k, v in struct_value.items():
                            if k in ('id', 'text'):
                                continue
                            new_data['var_%s_%s_%s' % (field.varname, i, k)] = v
                if field.store_structured_value:
                    new_data['var_%s_structured_raw' % field.varname] = structured_value
                    new_data['var_%s_structured' % field.varname] = structured_value
    return new_data


def flatten_dict(d):
    for k, v in list(d.items()):
        if isinstance(v, dict):
            flatten_dict(v)
            for k2, v2 in v.items():
                d['%s_%s' % (k, k2)] = v2
            del d[k]


def get_workflow_roles_substitution_variables(workflow_roles, prefix=''):
    d = {}
    for role_type, role_ids in workflow_roles.items():
        if not role_ids:
            continue

        _prefix = '%s%s_' % (prefix, role_type.replace('-', '_').strip('_'))
        if not isinstance(role_ids, list):
            role_ids = [role_ids]
        users_and_roles = [
            (
                get_publisher().user_class.get(str(role_id).split(':')[1], ignore_errors=True)
                if ':' in str(role_id)
                else get_publisher().role_class.cached_get(role_id, ignore_errors=True)
            )
            for role_id in role_ids
        ]
        users_and_roles = [x for x in users_and_roles if x]

        roles = [x for x in users_and_roles if isinstance(x, get_publisher().role_class)]

        if roles:
            d.update(roles[0].get_substitution_variables(_prefix))

        d[f'{_prefix}name'] = ', '.join([x.name for x in users_and_roles])
        d[f'{_prefix}names'] = [x.name for x in users_and_roles]
        d[f'{_prefix}role_slugs'] = [x.slug for x in roles]
        d[f'{_prefix}role_uuids'] = [x.uuid for x in roles]

    return d


class Evolution:
    who = None
    status = None
    time = None
    last_jump_datetime = None
    comment = None
    parts = None

    def __init__(self, formdata):
        self._formdata = formdata  # keep track of parent formdata

    @property
    def formdata(self):
        return self._formdata

    def get_author_name(self):
        user_id = self.who
        if self.who == '_submitter':
            user_id = self.formdata.user_id
        try:
            return get_publisher().user_class.get(user_id).display_name
        except KeyError:
            return None

    def get_author_qualification(self):
        if self.who == '_submitter' and not self.formdata.is_submitter(get_request().user):
            return _('Original Submitter')
        return None

    def add_part(self, part):
        if not self.parts:
            self.parts = []
        self.parts.append(part)

    _display_parts = None  # cache

    def display_parts(self):
        if self._display_parts is not None:
            return self._display_parts

        if not self.parts:
            return []

        l = []
        for p in self.parts:
            if not p.view:
                continue
            if p.is_hidden and p.is_hidden():
                continue
            if hasattr(p, 'to') and not self.formdata.is_for_current_user(p.to):
                continue
            text = p.view(formdata=self.formdata)
            if text:
                l.append(text)
        self._display_parts = l
        return self._display_parts

    def get_plain_text_comment(self):
        from wcs.wf.comment import WorkflowCommentPart

        for part in reversed(self.parts or []):
            if isinstance(part, WorkflowCommentPart):
                return part.get_as_plain_text()
        return self.comment

    def get_json_export_dict(
        self,
        formdata_user,
        anonymise=False,
        include_files=True,
        prefetched_users=None,
        pickle_evolution_parts=False,
    ):
        data = {
            'time': self.time,
            'last_jump_datetime': self.last_jump_datetime,
            'who_id': self.who,
        }
        if self.status:
            data['status'] = self.status[3:]
        if self.who != '_submitter':
            try:
                if prefetched_users is not None:
                    user = prefetched_users.get(str(self.who))
                else:
                    user = get_publisher().user_class.get(self.who)
            except KeyError:
                user = None
            if user is not None:
                data['who'] = user.get_json_export_dict()
        elif not anonymise and formdata_user:
            data['who'] = formdata_user.get_json_export_dict()
        if self.comment and not anonymise:
            data['comment'] = self.comment
        if pickle_evolution_parts:
            # store parts using pickle/base64 so their full internal state can be restored in tests
            parts = [x for x in self.parts or [] if not getattr(x, 'live', False)]
            data['parts'] = base64.b64encode(pickle.dumps(parts))
        else:
            parts = []
            for part in self.parts or []:
                if hasattr(part, 'get_json_export_dict'):
                    d = part.get_json_export_dict(anonymise=anonymise, include_files=include_files)
                    if d:
                        parts.append(d)

            if parts:
                data['parts'] = parts
        return data

    @classmethod
    def import_from_json_dict(cls, data, formdata):
        from wcs.sql import pickle_loads

        evo = cls(formdata)
        evo.time = data['time']
        evo.last_jump_datetime = data['last_jump_datetime']
        evo.who = data['who_id']
        evo.comment = data.get('comment')
        evo.parts = pickle_loads(base64.b64decode(data['parts']))
        return evo

    @property
    def datetime(self):
        return self.time

    def set_user(self, formdata, user, check_submitter=True):
        if formdata.is_submitter(user) and check_submitter:
            self.who = '_submitter'
        elif user is None:
            self.who = None
        else:
            self.who = user.id

    def get_status(self):
        status = self.status
        if not self.status:
            # look for the previous evolution with a status
            for evolution in reversed(self.formdata.evolution[: self.formdata.evolution.index(self)]):
                status = evolution.status
                if status:
                    break
        return self.formdata.get_status(status=status)

    def get_status_label(self):
        status = self.get_status()
        return get_publisher().translate(status.name) if status else _('Unknown')

    def is_hidden(self, user=None):
        status = self.get_status()
        if status:
            return not status.is_visible(self.formdata, user or get_request().user)
        return True

    def __repr__(self):
        parts = [
            self.__class__.__name__,
            'id:%s' % getattr(self, '_sql_id', None),
        ]

        status = self.get_status()
        if status:
            parts.append('in status "%s" (%s)' % (status.name, status.id))

        return '<%s>' % ' '.join(parts)


class FormData(StorableObject):
    # noqa pylint: disable=too-many-public-methods
    _names = 'XX'

    uuid = None
    id_display = None

    user_id = None
    user_label = None  # taken from data, for anonymous users
    receipt_time = None
    status = None
    anonymised = None
    page_no = 0  # page to use when restoring from draft
    page_id = None
    evolution = None
    data = None
    editable_by = None
    tracking_code = None
    backoffice_submission = False
    submission_agent_id = None
    submission_context = None
    submission_channel = None
    criticality_level = 0
    digests = None

    prefilling_data = None
    workflow_data = None
    workflow_roles = None
    geolocations = None
    statistics_data = None
    relations_data = None
    test_result_id = None
    workflow_processing_timestamp = None
    workflow_processing_afterjob_id = None

    _formdef = None

    def get_formdef(self):
        assert self._formdef
        return self._formdef

    formdef = property(get_formdef)

    def __init__(self, id=None):
        self.id = id

    def migrate(self):
        changed = False
        if (
            not self.submission_agent_id
            and self.submission_context
            and self.submission_context.get('agent_id')
        ):
            # 2020-07-13
            self.submission_agent_id = str(self.submission_context.get('agent_id'))
            changed = True

        if changed:
            self.store()

    def clean_live_evolution_items(self):
        for evolution in reversed(self.evolution or []):
            if getattr(evolution, 'parts', None):
                # cleanup evolution, remove parts that have only been added for
                # the live evaluation
                evolution.parts = [x for x in evolution.parts or [] if not getattr(x, 'live', False)]

    def refresh_from_storage(self):
        obj = self.get(self.id)
        self.__dict__ = obj.__dict__

    def refresh_from_storage_if_updated(self):
        objects = self.select(
            clause=[Greater('last_update_time', self.last_update_time), Equal('id', self.id)]
        )
        if objects:
            self.__dict__ = objects[0].__dict__
            return True
        return False

    def get_natural_key(self):
        if self.formdef.id_template:
            return self.id_display
        return self.id

    @property
    def identifier(self):
        value = self.get_natural_key()
        return str(value) if value is not None else None

    def is_workflow_test(self):
        return bool(self.test_result_id)

    @classmethod
    def get_by_id(cls, value, ignore_errors=False):
        if cls._formdef.id_template:
            try:
                return cls.select(
                    [StrictNotEqual('status', 'draft'), Null('anonymised'), Equal('id_display', str(value))],
                    limit=1,
                )[0]
            except IndexError:
                if ignore_errors:
                    return None
                raise KeyError(value)
        return cls.get(value, ignore_errors=ignore_errors)

    def get_user(self):
        if self.user_id and self.user_id != 'ultra-user':
            return get_publisher().user_class.get(self.user_id, ignore_errors=True)
        return None

    def set_user(self, user):
        if user:
            self.user_id = user.id
        else:
            self.user_id = None

    user = property(get_user, set_user)

    def set_user_from_json(self, json_user):
        formdata_user = None
        for name_id in json_user.get('NameID') or []:
            formdata_user = get_publisher().user_class.get_users_with_name_identifier(name_id)
            if formdata_user:
                break
        else:
            if json_user.get('email'):
                formdata_user = get_publisher().user_class.get_users_with_email(json_user.get('email'))
        if formdata_user:
            self.user_id = formdata_user[0].id

    def get_user_label(self):
        user = self.user
        if user:
            return user.get_display_name()
        return self.user_label

    def get_submitter_language(self):
        return (self.submission_context or {}).get('language')

    def has_empty_data(self):
        empty = True
        for key in self.data or {}:
            empty &= self.data.get(key) is None
        return empty

    def get_all_file_data(self, with_history=False):
        from wcs.wf.form import WorkflowFormEvolutionPart
        from wcs.workflows import ContentSnapshotPart

        def check_field_data(field_data):
            if misc.is_upload(field_data):
                yield field_data
            elif isinstance(field_data, dict) and isinstance(field_data.get('data'), list):
                for subfield_rowdata in field_data.get('data'):
                    if isinstance(subfield_rowdata, dict):
                        for block_field_data in subfield_rowdata.values():
                            if misc.is_upload(block_field_data):
                                yield block_field_data

        for field_data in itertools.chain((self.data or {}).values(), (self.workflow_data or {}).values()):
            yield from check_field_data(field_data)
        for part in self.iter_evolution_parts():
            if misc.is_attachment(part):
                yield part
            elif isinstance(part, WorkflowFormEvolutionPart):
                for field_data in (part.data or {}).values():
                    yield from check_field_data(field_data)
            elif isinstance(part, ContentSnapshotPart) and with_history:
                # look into old and new values (belt and suspenders)
                for field_data in list((part.old_data or {}).values()) + list((part.new_data or {}).values()):
                    yield from check_field_data(field_data)

    @classmethod
    def get_actionable_ids_criteria(cls, user_roles):
        statuses = ['wf-%s' % x.id for x in cls._formdef.workflow.get_not_endpoint_status()]
        return And([Intersects('actions_roles_array', user_roles), Contains('status', statuses)])

    @classmethod
    def get_actionable_ids(cls, user_roles):
        return cls.keys([cls.get_actionable_ids_criteria(user_roles)])

    @classmethod
    def get_submission_channels(cls):
        return collections.OrderedDict(
            [
                ('mail', _('Mail')),
                ('email', _('Email')),
                ('phone', _('Phone')),
                ('counter', _('Counter')),
                ('fax', _('Fax')),
                ('web', _('Web')),
                ('social-network', _('Social Network')),
            ]
        )

    def get_submission_channel_label(self):
        return str(self.get_submission_channels().get(self.submission_channel) or _('Web'))

    def get_parent(self):
        if not self.submission_context:
            return None
        object_type = self.submission_context.get('orig_object_type', 'formdef')
        objectdef_id = self.submission_context.get('orig_formdef_id')
        objectdata_id = self.submission_context.get('orig_formdata_id')
        if not (object_type and objectdef_id and objectdata_id):
            return None
        if object_type == 'carddef':
            from .carddef import CardDef

            objectdef_class = CardDef
        else:
            from .formdef import FormDef

            objectdef_class = FormDef
        try:
            return objectdef_class.cached_get(objectdef_id).data_class().cached_get(objectdata_id)
        except KeyError:
            return None

    def just_created(self, save_content_snapshot=True):
        from wcs.workflows import ContentSnapshotPart

        # it should not be possible to have a formdef/carddef with a workflow without any status.
        assert self.formdef.workflow.possible_status

        self.receipt_time = localtime()
        self.status = 'wf-%s' % self.formdef.workflow.possible_status[0].id
        # we add the initial status to the history, this makes it more readable
        # afterwards (also this gets the (previous_status) code to work in all
        # cases)
        evo = Evolution(self)
        evo.who = '_submitter'
        evo.time = self.receipt_time
        evo.status = self.status
        self.evolution = [evo]
        if save_content_snapshot:
            evo.add_part(ContentSnapshotPart(formdata=self, old_data={}))

    @classmethod
    def force_valid_id_characters(cls, value):
        value = re.sub(r'[^\w\s\'\-_]', '', unidecode.unidecode(value)).strip()
        value = re.sub(r'[\s\']+', '-', value)
        return value

    def set_user_label_field(self):
        if not self.user_id and get_publisher().has_user_fullname_config():
            form_user_data = {}
            for field in self.formdef.iter_fields(
                include_block_fields=True, with_backoffice_fields=False, with_no_data_fields=False
            ):
                if not hasattr(field, 'prefill'):
                    continue
                if field.get_prefill_configuration().get('type') == 'user':
                    block = getattr(field, 'block_field', None)
                    if block:
                        sub_data = self.data.get(block.id)
                        if not (sub_data and sub_data.get('data')):
                            continue
                        for sub_line_data in sub_data.get('data'):
                            sub_field_data = sub_line_data.get(field.id)
                            if sub_field_data:
                                form_user_data[field.get_prefill_configuration()['value']] = sub_field_data
                    elif self.data.get(field.id):
                        form_user_data[field.get_prefill_configuration()['value']] = self.data.get(field.id)
            user_object = get_publisher().user_class()
            user_object.form_data = form_user_data
            user_object.set_attributes_from_formdata(form_user_data)
            if user_object.name != self.user_label:
                self.user_label = user_object.name
                return True
        return False

    def get_auto_field_context(self):
        context = self.get_substitution_variables()
        context['formdef_id'] = self.formdef.id

        def error(attribute):
            raise Exception(str(_('"%s" is not available in digests') % attribute))

        for varname in ('cards', 'forms', 'data_source', 'webservice'):
            context[varname] = functools.partial(error, attribute=varname)

        return context

    def set_id_display_field(self):
        if not self.formdef.id_template and self.id_display:
            # unless a specific template is defined, only set id_display once
            # as it may have been set automatically by interpreting a webservice
            # response.
            return False

        template = self.formdef.get_display_id_format().strip()
        context = self.get_auto_field_context()

        try:
            new_value = Template(template, autoescape=False, raises=True, record_errors=False).render(context)
        except Exception as e:
            summary = _('Could not render custom id (%s)') % e
            get_publisher().record_error(
                summary,
                formdata=self,
                exception=e,
            )
            new_value = 'error-%s-%s' % (self.formdef.id, self.id)

        new_value = self.force_valid_id_characters(new_value)
        if not new_value:
            # empty string, fallback
            summary = _('Custom identifier template produced an empty string')
            get_publisher().record_error(summary, formdata=self)
            new_value = 'error-%s-%s' % (self.formdef.id, self.id)

        if new_value != self.id_display:
            self.id_display = new_value
            return True

        return False

    def set_digests_field(self):
        if not self.formdef.digest_templates:
            return False

        changed = False

        context = self.get_auto_field_context()

        digests = self.digests or {}
        i18n_enabled = bool(get_publisher() and get_publisher().has_i18n_enabled())
        cached_results = {'': {}}
        for language in get_publisher().get_enabled_languages():
            cached_results[language] = {}
        for key, template in self.formdef.digest_templates.items():
            if template in cached_results['']:
                new_value = cached_results[''].get(template)
            elif template is None:
                new_value = None
            else:
                try:
                    new_value = Template(template, autoescape=False, raises=True, record_errors=False).render(
                        context
                    )
                except Exception as e:
                    if key == 'default':
                        summary = _('Could not render digest (default) (%s)') % e
                    else:
                        summary = _('Could not render digest (custom view "%(view)s") (%(error)s)') % {
                            'view': key.removeprefix('custom-view:'),
                            'error': e,
                        }
                    get_publisher().record_error(
                        summary,
                        formdata=self,
                        exception=e,
                    )
                    new_value = 'ERROR'

            cached_results[''][template] = new_value
            if new_value != (self.digests or {}).get(key):
                changed = True
                digests[key] = new_value

            if i18n_enabled and template and '|translate' in template and new_value != 'ERROR':
                # generate additional digests if there are translatable parts
                for language in get_publisher().get_enabled_languages():
                    lang_key = f'{key}:{language}'
                    if template in cached_results[language]:
                        new_value = cached_results[language].get(template)
                    else:
                        with get_publisher().with_language(language):
                            try:
                                new_value = Template(template, autoescape=False).render(context)
                            except Exception:
                                continue
                            cached_results[language][template] = new_value
                    if new_value != (self.digests or {}).get(lang_key):
                        changed = True
                        digests[lang_key] = new_value

        self.digests = digests

        return changed

    def set_statistics_data_field(self):
        if self.is_workflow_test():
            return False

        done_datetime = (self.statistics_data or {}).get('done-datetime')
        if not done_datetime:
            current_status = self.get_status()
            if current_status and current_status.is_endpoint():
                done_datetime = self.get_status_datetime(current_status)
                if done_datetime:
                    done_datetime = done_datetime.isoformat()

        new_statistics_data = {
            'done-datetime': done_datetime,
        }
        for field in self.formdef.iter_fields(include_block_fields=True, with_no_data_fields=False):
            if not field.include_in_statistics:
                continue

            if new_statistics_data.get(field.varname):
                continue  # ignore fields with duplicated varname if we already have data

            block = getattr(field, 'block_field', None)
            if block:
                sub_data = self.data.get(block.id) or {}
                items = set()
                for data in sub_data.get('data', []):
                    values = data.get(field.id)
                    if not isinstance(values, list):
                        values = [values]
                    items.update(values)
                values = list(items)
            else:
                values = self.data.get(field.id)
                if not isinstance(values, list):
                    values = [values]
            new_statistics_data[field.varname] = [x for x in values if x is not None]

        if new_statistics_data != self.statistics_data:
            self.statistics_data = new_statistics_data
            return True

        return False

    def set_relations_data_field(self):
        if self.is_workflow_test():
            return False

        new_relations_data = collections.defaultdict(set)
        for relation in self.iter_target_datas():
            formdata = relation[0]
            if isinstance(formdata, str):
                continue
            key = f'{formdata.formdef.xml_root_node}:{formdata.formdef.slug}'
            new_relations_data[key].add(str(formdata.id))
        new_relations_data = {k: list(v) for k, v in sorted(new_relations_data.items())}
        if new_relations_data != self.relations_data:
            self.relations_data = new_relations_data
            return True
        return False

    def set_auto_fields(self):
        changed = set()
        if self.set_user_label_field():
            changed.add('user_label')
        if self.set_id_display_field():
            changed.add('id_display')
        if self.set_digests_field():
            changed.add('digests')
        if self.set_statistics_data_field():
            changed.add('statistics_data')
        if self.set_relations_data_field():
            changed.add('relations_data')
        return changed

    def get_lateral_block(self):
        context = get_publisher().substitutions.get_context_variables(mode='lazy')
        context['formdef_id'] = self.formdef.id
        if self.formdef.lateral_template is None:
            new_value = None
        else:
            try:
                new_value = Template(
                    self.formdef.lateral_template, autoescape=False, raises=True, record_errors=False
                ).render(context)
            except Exception as e:
                get_publisher().record_error(
                    _('Could not render lateral template (%s)') % e,
                    formdata=self,
                    exception=e,
                )
                return None
        return new_value

    # criticality levels are stored as [0, 101, 102, 103...], this makes it
    # easier to group "uncritical" formdatas (=0) together when sorting.
    def get_current_criticality_level(self):
        levels = len(self.formdef.workflow.criticality_levels or [0])
        current_level = self.criticality_level or 0
        if current_level >= 100 + levels:
            # too high, probably because the workflow was changed and there is
            # fewer levels than before
            current_level = 100 + levels - 1
        return current_level

    def increase_criticality_level(self):
        levels = len(self.formdef.workflow.criticality_levels or [0])
        current_level = self.get_current_criticality_level()
        if current_level == 0:
            current_level = 100
        if current_level < (100 + levels - 1):
            self.criticality_level = current_level + 1
            self.store()

    def decrease_criticality_level(self):
        current_level = self.get_current_criticality_level()
        if current_level == 0:
            return
        self.criticality_level = current_level - 1
        if self.criticality_level <= 100:
            self.criticality_level = 0
        self.store()

    def set_criticality_level(self, level):
        levels = len(self.formdef.workflow.criticality_levels or [0])
        level = min(levels - 1, level)
        if level > 0:
            self.criticality_level = 100 + level
        else:
            self.criticality_level = 0
        self.store()

    def get_criticality_level_object(self):
        levels = self.formdef.workflow.criticality_levels or []
        if not levels:
            raise IndexError()
        current_level = self.get_current_criticality_level()
        if current_level > 0:
            current_level = current_level - 100
        return levels[current_level]

    def perform_workflow(self, check_progress=True):
        get_publisher().substitutions.feed(self)
        wf_status = self.get_status()
        from wcs.workflows import perform_items, push_perform_workflow

        with push_perform_workflow(self):
            return perform_items(wf_status.items, self, check_progress=check_progress)

    def perform_workflow_as_job(self):
        from wcs.formdef_jobs import PerformWorkflowJob

        job = PerformWorkflowJob(label=_('Processing'), formdata=self)
        job.store()
        self.workflow_processing_timestamp = now()
        self.workflow_processing_afterjob_id = job.id
        self.store()
        get_publisher().add_after_job(job)

    def perform_global_action(self, action_id, user):
        from wcs.workflows import perform_items, push_perform_workflow

        for action in self.formdef.workflow.get_global_actions_for_user(formdata=self, user=user):
            if action.id != action_id:
                continue
            with push_perform_workflow(self):
                return perform_items(action.items, formdata=self, user=user, global_action=True)

    def get_workflow_messages(self, position='top', user=None):
        wf_status = self.get_visible_status(user=user)
        if not wf_status:
            return []
        return wf_status.get_messages(formdata=self, position=position)

    def get_status(self, status=None):
        if not status:
            status = self.status
        if status is None:
            return None
        if not self.formdef:
            return None
        if status.startswith('wf-'):
            status = status[3:]
        try:
            wf_status = [x for x in self.formdef.workflow.possible_status if x.id == status][0]
        except IndexError:
            return None
        return wf_status

    def get_status_label(self, status=None):
        if self.is_draft(status):
            return _('Draft')
        wf_status = self.get_status(status)
        if not wf_status:
            return _('Unknown')
        return wf_status.name

    def get_visible_status(self, user=Ellipsis):
        if user is Ellipsis:
            user = get_request().user
        if not self.evolution:
            return self.get_status()
        for evo in reversed(self.evolution):
            if not evo.status:
                continue
            wf_status = self.get_status(evo.status)
            if not wf_status:
                continue
            if not wf_status.is_visible(self, user):
                continue
            return wf_status
        return None

    def get_visible_evolution_parts(self, user=None):
        last_seen_status = None
        last_seen_author = None

        include_authors_in_form_history = (
            get_publisher().get_site_option('include_authors_in_form_history', 'variables') != 'False'
        )
        include_authors = get_request().is_in_backoffice() or include_authors_in_form_history

        for evolution_part in self.evolution or []:
            if evolution_part.is_hidden(user=user):
                continue
            if (evolution_part.status is None or last_seen_status == evolution_part.status) and (
                (evolution_part.who is None or last_seen_author == evolution_part.who) or not include_authors
            ):
                if not evolution_part.comment and not evolution_part.display_parts():
                    # don't include evolution item if there are no visible changes
                    # (same status, same author or hidden authors, no comment and no
                    # visible parts).
                    continue
            last_seen_status = evolution_part.status or last_seen_status
            last_seen_author = evolution_part.who or last_seen_author
            yield evolution_part

    def get_workflow_form(self, user, displayed_fields=None):
        if self.anonymised:
            return None
        wf_status = self.get_status()
        if not wf_status:
            return None
        return wf_status.get_action_form(self, user, displayed_fields=displayed_fields)

    def handle_workflow_form(self, user, form):
        wf_status = self.get_status()
        if not wf_status:
            return None
        return wf_status.handle_form(form, self, user)

    def evaluate_live_workflow_form(self, user, form):
        wf_status = self.get_status()
        if not wf_status:
            return None
        wf_status.evaluate_live_form(form, self, user)

    def pop_previous_marked_status(self):
        if not self.workflow_data or '_markers_stack' not in self.workflow_data:
            return None
        try:
            marker_data = self.workflow_data['_markers_stack'].pop()
            status_id = marker_data['status_id']
        except IndexError:
            return None
        try:
            return self.formdef.workflow.get_status(status_id)
        except KeyError:
            return None

    def jump_status(self, status_id, user_id=None):
        from wcs.wf.jump import WorkflowTriggeredEvolutionPart
        from wcs.workflows import ContentSnapshotPart, JumpEvolutionPart

        if status_id == '_previous':
            previous_status = self.pop_previous_marked_status()
            if not previous_status:
                summary = _('Failed to compute previous status')
                get_publisher().record_error(summary, formdata=self)
                return False
            status_id = previous_status.id

        if not self.formdef.workflow.has_status(status_id):
            # do not jump to undefined or missing status
            return False

        status = 'wf-%s' % status_id
        if not self.evolution:
            self.evolution = []
        elif (
            self.status == status
            and self.evolution[-1].status == status
            and not self.evolution[-1].comment
            and not [
                x
                for x in self.evolution[-1].parts or []
                if not isinstance(x, (ContentSnapshotPart, WorkflowTriggeredEvolutionPart, JumpEvolutionPart))
            ]
        ):
            # if status do not change and last evolution is empty,
            # just update last jump time on last evolution, do not add one
            # (ContentSnapshotPart and WorkflowTriggeredEvolutionPart are ignored
            # as they contain their own datetime attribute).
            self.evolution[-1].last_jump_datetime = localtime()
            self.store_last_jump()
            return True
        evo = Evolution(self)
        evo.time = localtime()
        evo.status = status
        evo.who = user_id
        self.evolution.append(evo)
        self.status = status
        self.store()
        return True

    def get_url(self, backoffice=False, include_category=False, language=None):
        base_url = self.formdef.get_url(
            backoffice=backoffice, include_category=include_category, language=language
        )
        if not self.id:
            return base_url
        return f'{base_url}{self.identifier}/'

    def get_backoffice_url(self):
        return self.get_url(backoffice=True)

    def get_api_url(self):
        return '%s%s/' % (self.formdef.get_api_url(), self.identifier)

    def get_file_base_url(self):
        return '%sdownload' % self.get_url()

    def get_temporary_access_url(self, duration, bypass_checks=False, backoffice=False):
        token = get_publisher().token_class(expiration_delay=duration, size=64)
        token.type = 'temporary-access-url'
        token.context = {
            'form_slug': self.formdef.slug,
            'form_type': self.formdef.xml_root_node,
            'form_number_raw': self.id,
            'bypass_checks': bypass_checks,
            'backoffice': backoffice,
        }
        token.store()
        return urllib.parse.urljoin(get_publisher().get_frontoffice_url(), f'/code/{token.id}/load')

    def get_short_url(self):
        assert self.id
        return urllib.parse.urljoin(get_publisher().get_frontoffice_url(), f'/r/{self.formdef.id}-{self.id}')

    def get_display_id(self):
        return str(self.id_display or self.id)

    def get_function_roles(self, role_name):
        # receive a function name or role identifier and return a set of role identifiers
        if role_name == '_submitter':
            raise Exception('_submitter is not a valid role')
        if str(role_name).startswith('_'):
            role_id = None
            if self.workflow_roles:
                role_id = self.workflow_roles.get(role_name)
            if not role_id and self.formdef.workflow_roles:
                role_id = self.formdef.workflow_roles.get(role_name)
            if role_id is None:
                return set()
            if isinstance(role_id, list):
                return set(role_id)
            return {str(role_id)}
        return {str(role_name)}

    def get_handling_role_id(self):
        # TODO: look at current status and return the role(s) actually
        # concerned by the handling of the formdata
        for role_id in self.get_function_roles('_receiver'):
            return role_id

    def get_handling_role(self):
        try:
            return get_publisher().role_class.get(self.get_handling_role_id())
        except KeyError:
            return None

    def get_field_view_value(self, field, max_length=None):
        class StatusFieldValue:
            def __init__(self, status):
                self.status = status

            def get_ods_style_name(self):
                return 'StatusStyle-%s' % misc.simplify(self.status.name) if self.status else None

            def get_ods_colour(self, colour):
                return {'black': '#000000', 'white': '#ffffff'}.get(colour, colour)

            def get_ods_style_bg_colour(self):
                return self.get_ods_colour(self.status.colour) if self.status else 'transparent'

            def get_ods_style_fg_colour(self):
                return self.get_ods_colour(self.status.get_contrast_color()) if self.status else '#000000'

            def __str__(self):
                return str(get_publisher().translate(self.status.name) if self.status else _('Unknown'))

        def get_value(field, data, **kwargs):
            # return the value of the given field, with special handling for "fake"
            # field types that are shortcuts to internal properties.
            if field.key == 'id':
                return self.get_display_id()
            if field.key == 'display_name':
                return self.get_display_name()
            if field.key == 'time':
                return misc.localstrftime(self.receipt_time)
            if field.key == 'last_update_time':
                return misc.localstrftime(self.last_update_time)
            if field.key == 'user-label':
                return self.get_user_label() or '-'
            if field.key == 'status':
                return StatusFieldValue(self.get_status())
            if field.key == 'user-visible-status':
                return StatusFieldValue(self.get_visible_status(user=None))
            if field.key == 'submission_channel':
                return self.get_submission_channel_label()
            if field.key == 'submission-agent':
                try:
                    agent_user = self.submission_agent_id
                    return get_publisher().user_class.get(agent_user).display_name
                except (KeyError, TypeError):
                    return '-'
            if field.key == 'anonymised':
                return _('Yes') if self.anonymised else _('No')
            if field.key == 'digest':
                return self.default_digest
            if field.key == 'card-id-field':
                field_value = data.get(field.id.removesuffix('_raw'))
                return field_value or ''

            field_id = field.id
            field_value = data.get(field_id)
            if field_value is None:
                return ''
            if getattr(field, 'is_related_field', False):
                field = field.related_field
                if field.key == 'file':
                    # always return filename as we don't check for access rights
                    return field_value.base_filename
            if field.key in ['date', 'bool', 'numeric']:
                return field.get_view_value(field_value)
            if field.key == 'file' and max_length is None:
                return field_value.base_filename
            if max_length is not None:
                # if max_length is set the target is a backoffice listing/table,
                # return an html value, appropriately shortened.
                field_value = data.get('%s_display' % field_id, field_value)
                return field.get_view_short_value(field_value, max_length, **kwargs)
            # otherwise return the actual "raw" field value
            return field_value

        if getattr(field, 'block_field', None):
            data = self.data.get(field.block_field.id) or {}
            return htmltext(', ').join(
                get_value(field, d, parent_field=field.block_field, parent_field_index=i)
                for i, d in enumerate(data.get('data') or [])
            )
        return get_value(field, self.data)

    def update_workflow_data(self, dict):
        if not self.workflow_data:
            self.workflow_data = {}
        self.workflow_data.update(dict)

    def get_as_dict(self):
        return get_dict_with_varnames(self.formdef.get_all_fields(), self.data, self)

    def is_at_endpoint_status(self):
        endpoint_status_ids = ['wf-%s' % x.id for x in self.formdef.workflow.get_endpoint_status()]
        return self.status in endpoint_status_ids

    def get_static_substitution_variables(self, minimal=False):
        d = {}

        if self.id:
            receipt_time = make_naive(self.receipt_time) if self.receipt_time else None
            d.update(
                {
                    'form_receipt_date': misc.strftime(misc.date_format(), receipt_time),
                    'form_receipt_time': misc.strftime('%H:%M', receipt_time),
                    'form_identifier': self.identifier,
                    'form_number': str(self.get_display_id()),
                    'form_number_raw': '%s' % self.id,
                    'form_url': self.get_url(),
                    'form_url_backoffice': self.get_url(backoffice=True),
                    'form_uri': '%s/%s/' % (self.formdef.url_name, self.id),
                    'form_criticality_level': self.criticality_level,
                    'form_digest': self.default_digest,
                    'form_display_name': self.get_display_name(),
                }
            )
            if self.receipt_time:
                # always get receipt time as a datetime object
                d['form_receipt_datetime'] = make_datetime(self.receipt_time)
            if self.last_update_time:
                d['form_last_update_datetime'] = make_datetime(self.last_update_time)
            if self.formdef.workflow.criticality_levels:
                try:
                    level = self.get_criticality_level_object()
                except IndexError:
                    pass
                else:
                    d['form_criticality_label'] = level.name

        d['form_status'] = self.get_status_label()

        if self.id and self.formdef.workflow and self.status:
            d['form_status_is_endpoint'] = self.is_at_endpoint_status()

        if self.tracking_code:
            d['form_tracking_code'] = self.tracking_code
        elif not self.status and self.data:
            if 'future_tracking_code' in self.data:
                d['form_tracking_code'] = self.data['future_tracking_code']
            elif 'draft_formdata_id' in self.data:
                try:
                    d['form_tracking_code'] = (
                        self.formdef.data_class().get(self.data['draft_formdata_id']).tracking_code
                    )
                except KeyError:
                    pass

        d['form_submission_backoffice'] = self.backoffice_submission
        d['form_submission_channel'] = self.submission_channel
        d['form_submission_channel_label'] = self.get_submission_channel_label()
        if self.submission_context:
            d['form_submission_context'] = self.submission_context

        # formdef and category variables
        d.update(self.formdef.get_static_substitution_variables(minimal=minimal))
        d.pop('form_objects', None)  # never include LazyFormDefObjectsManager

        if minimal:
            d = copy.deepcopy(d)
            flatten_dict(d)
            return d

        if self.id:
            d.update(
                {
                    'form_status_url': '%sstatus' % self.get_url(),
                    'form_details': self.get_form_details(),
                }
            )

        user = self.get_user()
        if user:
            d.update(user.get_substitution_variables(prefix='form_'))

        for k, v in self.get_as_dict().items():
            d['form_' + k] = v

        # include substitution variables for workflow roles; this will
        # typically give variables such as form_role_receiver_name and
        # form_role_receiver_emails.
        workflow_roles = {}
        if self.formdef.workflow_roles:
            workflow_roles.update(self.formdef.workflow_roles)
        if self.workflow_roles:
            workflow_roles.update(self.workflow_roles)

        d.update(get_workflow_roles_substitution_variables(workflow_roles, prefix='form_role_'))

        if self.evolution and self.evolution[-1].comment:
            d['form_comment'] = self.evolution[-1].comment
        else:
            d['form_comment'] = ''

        d['form_previous_status'] = ''
        d['form_status_changed'] = False
        if self.evolution:
            first_evolution_in_current_status = None
            for evolution in reversed(self.evolution):
                if evolution.status and evolution.status != self.status:
                    d['form_previous_status'] = self.get_status_label(evolution.status)
                    break
                if evolution.status:
                    first_evolution_in_current_status = evolution
            if (
                d['form_status'] != d['form_previous_status']
                and self.evolution[-1].status
                and first_evolution_in_current_status is self.evolution[-1]
                and not self.evolution[-1].last_jump_datetime
            ):
                # mark status has changed if the previous status was different
                # and we are not on a change done on the same status.
                d['form_status_changed'] = True

        d['form_evolution'] = self.formdef.get_detailed_evolution(self)

        if self.formdef.workflow and self.status:
            wf_status = self.get_status()
            if wf_status:
                for item in wf_status.items:
                    d.update(item.get_substitution_variables(self))

        # Add variables from evolution parts classes
        evolution_parts_classes = {
            part.__class__ for evolution in self.evolution or [] for part in evolution.parts or []
        }
        for klass in evolution_parts_classes:
            if hasattr(klass, 'get_substitution_variables'):
                d.update(klass.get_substitution_variables(self))

        if self.geolocations:
            for k, v in self.geolocations.items():
                d['form_geoloc_%s_lat' % k] = v.get('lat')
                d['form_geoloc_%s_lon' % k] = v.get('lon')
                d['form_geoloc_%s' % k] = v

        lazy = self.get_substitution_variables()
        del lazy['form']
        del lazy['attachments']
        d.update(lazy)

        d = copy.deepcopy(d)
        flatten_dict(d)

        return d

    def get_form_details(self):
        return FormDetails(formdata=self)

    def get_as_lazy(self):
        from wcs.variables import LazyFormData

        return LazyFormData(self)

    def get_substitution_variables(self, minimal=False):
        from wcs.workflows import AttachmentsSubstitutionProxy

        variables = CompatibilityNamesDict(
            {
                'form': self.get_as_lazy(),
                'attachments': AttachmentsSubstitutionProxy(self, deprecated_usage=True),
            }
        )
        if self.formdef.category:
            variables.update(self.formdef.category.get_substitution_variables(minimal=minimal))
        if minimal:
            return variables

        if self.workflow_data:
            d = {}
            # pass over workflow data to:
            #  - attach an extra url attribute to uploaded files
            #  - ignore "private" attributes
            #  - ignore attributes that will conflict with (parts of) the
            #    "form" namespace
            for k, v in self.workflow_data.items():
                if k[0] == '_' or k.startswith('form_var_') or k == 'form':
                    continue
                d[k] = v
            # recompute _url variable of attached files
            form_url = self.get_url()
            for k, v in self.workflow_data.items():
                if isinstance(v, Upload):
                    try:
                        formvar, fieldvar = re.match('(.*)_var_(.*)_raw$', k).groups()
                    except AttributeError:
                        continue
                    d[k.rsplit('_', 1)[0] + '_url'] = '%sfiles/form-%s-%s/%s' % (
                        form_url,
                        formvar,
                        fieldvar,
                        self.workflow_data['%s_var_%s' % (formvar, fieldvar)],
                    )

            d = copy.deepcopy(d)
            flatten_dict(d)
            variables.update({k: v for k, v in d.items() if CompatibilityNamesDict.valid_key_regex.match(k)})

        return variables

    @classmethod
    def get_substitution_variables_list(cls):
        variables = []
        # advertise the existence of field variables
        variables.append((_('Form'), 'form_var_...', _('Form Field Data')))
        user_variables = get_publisher().user_class.get_substitution_variables_list(prefix='form_')
        for dummy, name, dummy in user_variables:
            variables.append((_('Form'), name, _('Form Submitter Field')))
        return variables

    @classmethod
    def rebuild_security(cls, update_all=False):
        with get_publisher().substitutions.temporary_feed(cls._formdef):
            cls.rebuild_indexes(indexes=['concerned_roles', 'actions_roles'])

    def is_submitter(self, user):
        if self.user_id and user and str(self.user_id) == str(user.id):
            return True
        if get_session() and get_session().is_anonymous_submitter(self):
            return True
        return False

    def is_for_current_user(self, to):
        if not to:
            return True
        if not get_request():
            return False
        user = get_request().user
        for role in to or []:
            if role == '_submitter':
                if self.is_submitter(user):
                    return True
            elif user:
                if self.get_function_roles(role).intersection(user.get_roles()):
                    return True
        return False

    def is_draft(self, status=None):
        if status is None:
            status = self.status
        return status == 'draft'

    @property
    def workflow_merged_roles_dict(self):
        merged_dict = {}
        for k, v in (self.workflow_roles or {}).items():
            if k not in merged_dict:
                merged_dict[k] = []
            if isinstance(v, (int, str)):
                v = [str(v)]
            merged_dict[k].extend(v)
        for k, v in (self.formdef.workflow_roles or {}).items():
            if k not in merged_dict and v:
                merged_dict[k] = [v]
        return merged_dict

    @workflow_merged_roles_dict.setter
    def workflow_merged_roles_dict(self, value):
        # do not do anything, this setter is just there as the SQL retrieval will
        # try to set the property.
        pass

    def get_concerned_roles(self):
        if self.is_draft():
            # drafts are only visible to submitter
            return ['_submitter']

        status_action_roles = set()

        # make sure the handling roles always gets access to the formdata, till
        # the very end (where it may be that there is no workflow status item
        # at all).
        for function_key in self.formdef.workflow.roles.keys():
            for handling_role in self.get_function_roles(function_key):
                status_action_roles.add(handling_role)

        wf_status = self.get_status()
        if not wf_status:
            status_action_roles.add('_submitter')
        else:
            status_action_roles |= set(self.get_actions_roles())
        return status_action_roles

    concerned_roles = property(get_concerned_roles)

    def get_actions_roles(self, condition_kwargs=None):
        if self.is_draft():
            return []

        wf_status = self.get_status()
        if not wf_status:
            return []

        status_action_roles = set()
        for item in wf_status.items or []:
            if not hasattr(item, 'by') or not item.by:
                continue
            if item.key == 'jump':
                # automatic jump has a 'by' attribute but it's only for triggers,
                # it's not a real interactive action.
                continue
            with get_publisher().substitutions.freeze():
                # limit variables to formdata variables (exclude things like variables
                # from session as they're not appropriate accross users)
                get_publisher().substitutions.reset()
                get_publisher().substitutions.feed(get_publisher())
                get_publisher().substitutions.feed(self)
                if not item.check_condition(self, **(condition_kwargs or {})):
                    continue
            for role in item.by:
                if role == '_submitter':
                    status_action_roles.add(role)
                else:
                    for real_role in self.get_function_roles(role):
                        status_action_roles.add(real_role)

        return status_action_roles

    actions_roles = property(get_actions_roles)

    def get_last_update_time(self):
        if hasattr(self, '_last_update_time'):
            return self._last_update_time
        if self.evolution and self.evolution[-1].last_jump_datetime:
            return self.evolution[-1].last_jump_datetime
        if self.evolution and self.evolution[-1].time:
            return self.evolution[-1].time
        return self.receipt_time

    def set_last_update_time(self, value):
        assert isinstance(value, (type(None), datetime.datetime))
        self._last_update_time = value

    last_update_time = property(get_last_update_time, set_last_update_time)

    def anonymise(self, mode='final'):
        from wcs.workflows import ContentSnapshotPart

        anonymisable_fields = []
        for field in self.formdef.iter_fields(include_block_fields=True, with_no_data_fields=False):
            if field.anonymise == 'no':
                continue
            if mode in ('final', field.anonymise):
                anonymisable_fields.append(field)
                if hasattr(field, 'block_field') and self.data.get(field.block_field.id):
                    for row_data in self.data[field.block_field.id].get('data') or []:
                        field.set_value(row_data, None)
                else:
                    field.set_value(self.data, None)

        if mode != 'final':
            # delete field values from ContentSnapshotParts
            for part in self.iter_evolution_parts(ContentSnapshotPart):
                for field in anonymisable_fields:
                    if hasattr(field, 'block_field'):
                        if part.old_data.get(field.block_field.id):
                            for row_data in part.old_data[field.block_field.id].get('data') or []:
                                if field.id in row_data:
                                    field.set_value(row_data, None)
                                    self._store_all_evolution = True
                        if part.new_data.get(field.block_field.id):
                            for row_data in part.new_data[field.block_field.id].get('data') or []:
                                if field.id in row_data:
                                    field.set_value(row_data, None)
                                    self._store_all_evolution = True
                    else:
                        if field.id in part.old_data:
                            field.set_value(part.old_data, None)
                            self._store_all_evolution = True
                        if field.id in part.new_data:
                            field.set_value(part.new_data, None)
                            self._store_all_evolution = True

            self.store()
            return

        self.anonymised = localtime()
        self.user_id = None
        self.user_label = None
        self.editable_by = None
        self.workflow_data = None
        self.workflow_roles = None
        self.submission_context = None

        if self.evolution:
            for evo in self.evolution:
                evo.who = None
                evo.parts = None
                evo.comment = None
                evo.parts = None
        self._store_all_evolution = True
        self.store()

        self.remove_tracking_code()

    def unlink_user(self):
        if self.user_id:
            self.user_id = None
            self.store()

    def remove_tracking_code(self):
        if self.tracking_code is not None:
            from wcs.tracking_code import TrackingCode

            TrackingCode.remove_object(self.tracking_code)
            self.tracking_code = None
            self.store()

    def get_display_name(self):
        if self.formdef.id_template:
            return _('%(name)s - %(id)s') % {
                'name': get_publisher().translate(self.formdef.name),
                'id': self.get_display_id(),
            }
        return _('%(name)s #%(id)s') % {
            'name': get_publisher().translate(self.formdef.name),
            'id': self.get_display_id(),
        }

    @property
    def default_digest(self):
        return (self.digests or {}).get('default')

    def get_display_label(self, digest_key='default', include_form_name=True):
        if include_form_name:
            base = self.get_display_name()
        else:
            base = self.get_display_id()
        digest = (self.digests or {}).get(digest_key)
        return '%s (%s)' % (base, digest) if digest else base

    def get_auto_geoloc(self):
        # use proper geolocation if it exists
        if self.geolocations:
            for v in self.geolocations.values():
                if v:
                    return v
        # fallback to 1st map field
        for field in self.formdef.get_all_fields():
            if field.key == 'map' and self.data.get(field.id):
                return field.get_json_value(self.data[field.id])
        return None

    def get_status_datetime(self, status, latest=False):
        evolutions = reversed(self.evolution) if latest else self.evolution
        for evo in evolutions:
            if evo.status and evo.get_status() == status:
                return evo.time

    @classmethod
    def get_json_data_dict(
        cls,
        data,
        fields,
        formdata=None,
        include_files=True,
        anonymise=False,
        include_unnamed_fields=False,
        parent_field=None,
        parent_field_index=None,
    ):
        new_data = {}
        seen = set()
        for field in fields:
            if anonymise and field.anonymise == 'final':
                continue
            if field.is_no_data_field:
                continue
            if not field.varname and not include_unnamed_fields:
                continue
            if field.varname in seen:
                # skip fields with a varname that is used by another non-empty
                # field.
                continue
            if data is not None:
                value = data.get(field.id)
                if value is not None and hasattr(field, 'get_json_value'):
                    value = field.get_json_value(
                        value,
                        formdata=formdata,
                        include_file_content=include_files,
                        parent_field=parent_field,
                        parent_field_index=parent_field_index,
                    )
            else:
                value = None

            if value and field.varname:
                seen.add(field.varname)

            if not field.varname:
                # include unnamed fields in a dedicated key
                if '_unnamed' not in new_data:
                    new_data['_unnamed'] = {}
                store_dict = new_data['_unnamed']
                store_key = str(field.id)
            else:
                store_dict = new_data
                store_key = field.varname

            if field.store_display_value:
                store_dict[store_key + '_raw'] = value
                store_dict[store_key] = data.get('%s_display' % field.id)
            else:
                store_dict[store_key] = value
            if field.store_structured_value:
                if data.get('%s_structured' % field.id):
                    store_dict[store_key + '_structured'] = data.get('%s_structured' % field.id)
            if field.key == 'block' and 'digests' in (data.get(field.id) or {}):
                store_dict[store_key + '_digests'] = data.get(field.id)['digests']
        return new_data

    def get_json_dict(self, data, fields, include_files=True, anonymise=False, include_unnamed_fields=False):
        return self.get_json_data_dict(
            data,
            fields,
            formdata=self,
            include_files=include_files,
            anonymise=anonymise,
            include_unnamed_fields=include_unnamed_fields,
        )

    def get_json_export_dict(
        self,
        *,
        include_files=True,
        anonymise=False,
        user=None,
        digest_key='default',
        prefetched_users=None,
        prefetched_roles=None,
        include_evolution=True,
        include_roles=True,
        include_submission=True,
        include_fields=True,
        include_user=True,
        include_unnamed_fields=False,
        include_workflow=True,
        include_workflow_data=True,
        include_actions=True,
        values_at=None,
    ):
        # noqa pylint: disable=too-many-arguments
        data = {}
        data['uuid'] = self.uuid
        data['id'] = self.identifier
        data['internal_id'] = str(self.id)
        data['display_id'] = self.get_display_id()
        data['display_name'] = self.get_display_name()
        data['digests'] = self.digests
        data['text'] = self.get_display_label(digest_key=digest_key)
        data['url'] = self.get_url()
        data['receipt_time'] = (
            make_naive(self.receipt_time.replace(microsecond=0)) if self.receipt_time else None
        )
        data['last_update_time'] = (
            make_naive(self.last_update_time.replace(microsecond=0)) if self.last_update_time else None
        )

        formdata_user = None
        if include_user or include_fields or include_workflow or include_evolution:
            try:
                if prefetched_users is not None:
                    formdata_user = prefetched_users.get(str(self.user_id))
                else:
                    formdata_user = get_publisher().user_class.get(self.user_id)
            except KeyError:
                pass

        _data = self.data
        if values_at and (include_fields or include_workflow):
            from wcs.workflows import ContentSnapshotPart

            matching_part = None
            for evo in reversed(self.evolution or []):
                for part in reversed(evo.parts or []):
                    if isinstance(part, ContentSnapshotPart):
                        if part.datetime < values_at:
                            matching_part = part
                        break
                if matching_part:
                    break
            if not matching_part:
                raise NoContentSnapshotAt('No data found for this datetime.')
            _data = matching_part.new_data

        if include_user and formdata_user and not anonymise:
            from .carddef import CardDef

            data['user'] = formdata_user.get_json_export_dict(full=isinstance(self.formdef, CardDef))

        if include_fields:
            data['criticality_level'] = self.criticality_level
            data['api_url'] = self.get_api_url()
            data['backoffice_url'] = self.get_backoffice_url()

            data['fields'] = self.get_json_dict(
                _data,
                self.formdef.fields,
                include_files=include_files,
                anonymise=anonymise,
                include_unnamed_fields=include_unnamed_fields,
            )

        if include_workflow:
            data['workflow'] = {}
            wf_status = self.get_visible_status(formdata_user)
            if wf_status:
                data['workflow']['status'] = {
                    'id': wf_status.id,
                    'name': wf_status.name,
                    'endpoint': wf_status.is_endpoint(),
                    'first_arrival_datetime': self.get_status_datetime(wf_status),
                    'latest_arrival_datetime': self.get_status_datetime(wf_status, latest=True),
                }
            wf_real_status = self.get_status()
            if wf_real_status:
                data['workflow']['real_status'] = {
                    'id': wf_real_status.id,
                    'name': wf_real_status.name,
                    'endpoint': wf_real_status.is_endpoint(),
                    'first_arrival_datetime': self.get_status_datetime(wf_real_status),
                    'latest_arrival_datetime': self.get_status_datetime(wf_real_status, latest=True),
                }
            if self.formdef.workflow.get_backoffice_fields():
                data['workflow']['fields'] = self.get_json_dict(
                    _data,
                    self.formdef.workflow.get_backoffice_fields(),
                    include_files=include_files,
                    anonymise=anonymise,
                    include_unnamed_fields=include_unnamed_fields,
                )
        if include_workflow_data:
            # Workflow data have unknown purpose, do not store them in anonymised export
            if self.workflow_data and not anonymise:
                if 'workflow' not in data:
                    data['workflow'] = {}
                data['workflow']['data'] = self.workflow_data

        # include actions
        if include_actions:
            actions = {}
            data['actions'] = actions

            for trigger in self.formdef.workflow.get_all_global_action_triggers():
                if (
                    trigger.key == 'webservice'
                    and trigger.identifier
                    and trigger.check_executable(self, user)
                ):
                    actions[f'global-action:{trigger.identifier}'] = (
                        f'{self.get_api_url()}hooks/{trigger.identifier}/'
                    )

            status = self.get_status()
            if status:
                for item in self.get_status().items:
                    if (
                        item.key == 'jump'
                        and item.trigger
                        and item.check_auth(self, user)
                        and item.check_condition(self, trigger=item.trigger)
                    ):
                        actions[f'jump:{item.trigger}'] = f'{self.get_api_url()}jump/trigger/{item.trigger}/'
                    elif (
                        item.key == 'editable'
                        and item.check_auth(self, user)
                        and item.check_condition(self)
                        and get_publisher().has_site_option('api-include-editable-action')
                    ):
                        url = self.get_url(
                            backoffice=True,
                            include_category=True,
                            language=get_publisher().current_language,
                        )
                        actions[f'link:edit:{item.parent.id}-{item.id}'] = f'{url}wfedit-{item.id}'

        if include_roles:
            # add a roles dictionary, with workflow functions and two special
            # entries for concerned/actions roles.
            data['roles'] = {}
            workflow_roles = {}
            if self.formdef.workflow_roles:
                workflow_roles.update(self.formdef.workflow_roles)
            if self.workflow_roles:
                workflow_roles.update(self.workflow_roles)
            for workflow_role in workflow_roles:
                value = workflow_roles.get(workflow_role)
                if not isinstance(value, list):
                    value = [value]
                data['roles'][workflow_role] = value
            data['roles']['concerned'] = self.get_concerned_roles()
            data['roles']['actions'] = self.get_actions_roles()

            for role_key in data['roles']:
                # exclude special _submitter value
                role_list = [x for x in data['roles'][role_key] if x != '_submitter']
                # get role objects
                if prefetched_roles is not None:
                    role_list = [prefetched_roles.get(str(x)) for x in role_list]
                else:
                    role_list = [get_publisher().role_class.get(x, ignore_errors=True) for x in role_list]
                # export as json dicts
                role_list = [x.get_json_export_dict() for x in role_list if x is not None]
                data['roles'][role_key] = role_list

        if include_submission:
            data['submission'] = {
                'backoffice': self.backoffice_submission,
                'channel': self.submission_channel or 'web',
            }
            try:
                if prefetched_users is not None:
                    agent = prefetched_users.get(str(self.submission_agent_id))
                else:
                    agent = get_publisher().user_class.get(self.submission_agent_id)
            except KeyError:
                agent = None
            if agent:
                data['submission']['agent'] = agent.get_json_export_dict()
            parent = self.get_parent()
            if parent:
                data['submission']['parent'] = {
                    'url': parent.get_url(),
                    'backoffice_url': parent.get_backoffice_url(),
                    'api_url': parent.get_api_url(),
                }

        if self.evolution and include_evolution:
            evolution = data['evolution'] = []
            for evo in self.evolution:
                evolution.append(
                    evo.get_json_export_dict(
                        formdata_user=formdata_user,
                        anonymise=anonymise,
                        include_files=include_files,
                        prefetched_users=prefetched_users,
                    )
                )

        if include_fields and self.geolocations:
            data['geolocations'] = {}
            for k, v in self.geolocations.items():
                data['geolocations'][k] = v.copy()

        return data

    def export_to_json(
        self,
        *,
        anonymise=False,
        user=None,
        include_evolution=True,
        include_files=True,
        include_roles=True,
        include_submission=True,
        include_fields=True,
        include_user=True,
        include_unnamed_fields=False,
        include_workflow=True,
        include_workflow_data=True,
        include_actions=True,
        values_at=None,
    ):
        # noqa pylint: disable=too-many-arguments
        data = self.get_json_export_dict(
            anonymise=anonymise,
            user=user,
            include_evolution=include_evolution,
            include_files=include_files,
            include_roles=include_roles,
            include_submission=include_submission,
            include_fields=include_fields,
            include_user=include_user,
            include_unnamed_fields=include_unnamed_fields,
            include_workflow=include_workflow,
            include_workflow_data=include_workflow_data,
            include_actions=include_actions,
            values_at=values_at,
        )
        return json.dumps(data, cls=misc.JSONEncoder)

    def get_object_key(self):
        return '%s-%s-%s' % (self.formdef.xml_root_node, self.formdef.url_name, self.id)

    def feed_session(self):
        # this gives a chance to fields to initialize things that would rely on
        # current data ahead of times
        for field in self.formdef.fields:
            field.feed_session(self.data.get(field.id), self.data.get('%s_display' % field.id))

    def get_summary_field_details(
        self,
        fields=None,
        include_unset_required_fields=False,
        data=None,
        parent_field=None,
        parent_field_index=None,
    ):
        return get_summary_field_details(
            self,
            fields=fields,
            include_unset_required_fields=include_unset_required_fields,
            data=data,
            parent_field=parent_field,
            parent_field_index=parent_field_index,
        )

    @property
    def workflow_traces_class(self):
        if self._table_name.startswith('test_'):
            from wcs.workflow_traces import TestWorkflowTrace

            return TestWorkflowTrace

        from wcs.workflow_traces import WorkflowTrace

        return WorkflowTrace

    def get_workflow_traces(self):
        return self.workflow_traces_class.select_for_formdata(formdata=self)

    def record_workflow_event(self, event, **kwargs):
        self.workflow_traces_class(formdata=self, event=event, event_args=kwargs).store()

    def record_workflow_action(self, action):
        self.workflow_traces_class(formdata=self, action=action).store()

    def iter_evolution_parts(self, klass=None, reverse=False):
        if klass is None:
            from wcs.workflows import EvolutionPart

            klass = EvolutionPart
        evolutions = self.evolution or []
        if reverse:
            for evo in reversed(evolutions):
                yield from (x for x in reversed(evo.parts or []) if isinstance(x, klass))
        else:
            for evo in evolutions:
                yield from (x for x in evo.parts or [] if isinstance(x, klass))

    def iter_target_datas(self, objectdef=None, object_type=None, status_item=None):
        # objectdef, object_type and status_item are provided when called from a workflow action
        from wcs.wf.create_formdata import LinkedFormdataEvolutionPart

        from .carddef import CardDef
        from .formdef import FormDef

        parent = self.get_parent()
        if parent and object_type:
            # looking for a parent of a specific type (workflow action)
            parent_identifier = '%s:%s' % (parent.formdef.xml_root_node, parent.formdef.url_name)
            if parent_identifier == object_type:
                yield parent
        elif parent:
            # looking for any parent (inspect page)
            yield (parent, _('Parent'))

        data_ids = []
        # search linked objects in data sources
        for field in self.get_formdef().get_all_fields():
            linked_id = self.data.get(field.id)
            if not linked_id:
                continue
            data_source = getattr(field, 'data_source', None)
            if not data_source:
                continue
            if field.key == 'items':
                linked_ids = linked_id
            else:
                linked_ids = [linked_id]
            data_source_type = data_source['type']
            if data_source_type.count(':') == 2:
                # custom view, only keep object type and object slug
                data_source_type = ':'.join(data_source_type.split(':')[:2])
            origin = _('Data Source')
            if field.varname:
                origin = '%s - %s' % (origin, _('in field with identifier: %s') % field.varname)
            for linked_id in linked_ids:
                if object_type:
                    # looking for a data_source of a specific type (workflow action)
                    if data_source_type == object_type:
                        data_ids.append((data_source_type, linked_id, origin, 'get_by_id'))
                else:
                    # looking for any data_source (inspect page)
                    data_ids.append((data_source_type, linked_id, origin, 'get_by_id'))

        # search in evolution
        for part in self.iter_evolution_parts(LinkedFormdataEvolutionPart):
            if not part.formdef:  # removed formdef
                continue
            part_identifier = '%s:%s' % (part.formdef.xml_root_node, part.formdef.url_name)
            get_method = 'get_by_id' if part.formdata_id_is_natural else 'get'
            if object_type:
                # looking for an object of a specific type (workflow action)
                if part_identifier == object_type:
                    data_ids.append((part_identifier, part.formdata_id, _('Evolution'), get_method))
            else:
                # looking for any object (inspect page)
                data_ids.append((part_identifier, part.formdata_id, _('Evolution'), get_method))

        for slug, target_id, origin, get_method in data_ids:
            if object_type:
                # workflow action
                try:
                    yield getattr(objectdef.data_class(), get_method)(target_id)
                except KeyError:
                    # linked object may be missing
                    pass
            else:
                # inspect page
                try:
                    obj_type, slug = slug.split(':')
                    if obj_type == 'formdef':
                        obj_class = FormDef
                    elif obj_type == 'carddef':
                        obj_class = CardDef
                    try:
                        _objectdef = obj_class.get_by_urlname(slug, use_cache=True)
                    except KeyError:
                        yield (
                            _('Linked object def by id %(object_id)s') % {'object_id': slug},
                            _('%s - not found') % origin,
                        )
                    else:
                        yield (getattr(_objectdef.data_class(), get_method)(target_id), origin)
                except ValueError:
                    pass
                except KeyError:
                    yield (
                        _('Linked "%(object_name)s" object by id %(object_id)s')
                        % {'object_name': _objectdef.name, 'object_id': target_id},
                        _('%s - not found') % origin,
                    )

    def get_summary_display_actions(self, fields=None, form_url='', include_unset_required_fields=False):
        yield from get_summary_display_actions(
            self,
            fields=fields,
            form_url=form_url,
            include_unset_required_fields=include_unset_required_fields,
        )

    def get_rst_summary(self, form_url=''):
        r = ''

        for field_action in self.get_summary_display_actions(None, include_unset_required_fields=False):
            if field_action['action'] == 'close-page':
                r += '\n'
            elif field_action['action'] == 'open-page':
                r += field_action['value'] + '\n'
                r += '=' * len(field_action['value']) + '\n\n'
            elif field_action['action'] == 'title':
                r += field_action['value'] + '\n'
                r += '-' * len(field_action['value']) + '\n\n'
            elif field_action['action'] == 'subtitle':
                r += f'**{field_action["value"]}**\n\n'
            elif field_action['action'] == 'comment':
                r += html.unescape(strip_tags(field_action['value'])) + '\n\n'
            elif field_action['action'] == 'open-field':
                r += '\n'
            elif field_action['action'] == 'close-field':
                r += '\n'
            elif field_action['action'] == 'label':
                label = field_action['value'].rstrip(': ')
                r += (_('%s:') % label) + '\n'
            elif field_action['action'] == 'value':
                value = field_action['value']
                if value:
                    r += field_action['field_value_info']['field'].get_rst_view_value(
                        field_action['field_value_info']['value'], indent='  '
                    )
                    r += '\n'

        return r

    def __getattr__(self, attr):
        try:
            return self.__dict__[attr]
        except KeyError:
            # give direct access to values from the data dictionary
            if attr[0] == 'f':
                field_id = attr[1:]
                if field_id in self.__dict__['data']:
                    return self.__dict__['data'][field_id]
                # if field id is not in data dictionary it may still be a valid
                # field, never initialized, check requested field id against
                # existing fields ids.
                formdef_fields = self.formdef.get_all_fields()
                if field_id in [x.id for x in formdef_fields]:
                    return None
            raise AttributeError(attr)


class FormDetails:
    # lazy object compatibility to keep form_details as part of static variables while
    # generating it only if/when accessed.
    def __init__(self, formdata):
        self.formdata = formdata
        self._cache = None

    def get_value(self):
        if self._cache:
            return self._cache
        self._cache = (
            self.formdata.formdef.get_detailed_email_form(self.formdata, self.formdata.get_url()) or ''
        )
        return self._cache

    def __str__(self):
        return self.get_value()

    def __nonzero__(self):
        return bool(self.get_value())

    def __contains__(self, value):
        # to deal with usage of "... in form_details"
        return value in self.get_value()

    def __eq__(self, other):
        return str(self) == str(other)

    def __deepcopy__(self, memo=None):
        return copy.copy(self)

    def __getattr__(self, attr):
        if attr in ('_cache', 'get_value'):
            return super().__getattr__(attr)
        # to deal with calls like form_details.replace(...)
        return getattr(self.get_value(), attr)


Substitutions.register('form_receipt_date', category=_('Form'), comment=_('Form Receipt Date'))
Substitutions.register('form_receipt_time', category=_('Form'), comment=_('Form Receipt Time'))
Substitutions.register('form_number', category=_('Form'), comment=_('Form Number'))
Substitutions.register('form_details', category=_('Form'), comment=_('Form Details'))
Substitutions.register('form_url', category=_('Form'), comment=_('Form URL'))
Substitutions.register('form_url_backoffice', category=_('Form'), comment=_('Form URL (backoffice)'))
Substitutions.register('form_tracking_code', category=_('Form'), comment=_('Form Tracking Code'))
Substitutions.register('form_user_display_name', category=_('Form'), comment=_('Form Submitter Name'))
Substitutions.register('form_user_email', category=_('Form'), comment=_('Form Submitter Email'))
Substitutions.register_dynamic_source(FormData)

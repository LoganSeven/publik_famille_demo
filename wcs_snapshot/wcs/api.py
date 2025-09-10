# w.c.s. - web application for online forms
# Copyright (C) 2005-2013  Entr'ouvert
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
import copy
import datetime
import json
import re
import urllib.parse

from django.http import HttpResponse, JsonResponse
from django.utils.encoding import force_bytes
from django.utils.timezone import localtime, make_aware, make_naive
from quixote import get_publisher, get_request, get_response, get_session, redirect
from quixote.directory import Directory
from quixote.errors import MethodNotAllowedError, RequestError
from quixote.html import TemplateIO, htmltext

import wcs.qommon.storage as st
from wcs.api_utils import get_query_flag, get_user_from_api_query_string, is_url_signed, sign_url_auto_orig
from wcs.carddef import CardDef
from wcs.categories import Category
from wcs.conditions import Condition, ValidationError
from wcs.data_sources import NamedDataSource
from wcs.data_sources import get_object as get_data_source_object
from wcs.data_sources import request_json_items
from wcs.formdef import FormDef
from wcs.forms.common import FileDirectory, FormStatusPage
from wcs.logged_errors import LoggedError
from wcs.qommon import get_cfg
from wcs.qommon.afterjobs import AfterJob
from wcs.roles import logged_users_role
from wcs.sql_criterias import (
    Contains,
    ElementIntersects,
    Equal,
    FtsMatch,
    Greater,
    ILike,
    Intersects,
    NotContains,
    Nothing,
    Null,
    Or,
    StrictNotEqual,
)
from wcs.tracking_code import TrackingCode
from wcs.workflows import ContentSnapshotPart
from wcs.wscalls import UnflattenKeysException, unflatten_keys

from .backoffice.data_management import CardPage as BackofficeCardPage
from .backoffice.management import FormPage as BackofficeFormPage
from .backoffice.management import ManagementDirectory
from .backoffice.submission import SubmissionDirectory
from .qommon import _, misc, ngettext
from .qommon.errors import (
    AccessForbiddenError,
    HttpResponse200Error,
    TraversalError,
    UnknownNameIdAccessForbiddenError,
)
from .qommon.template import Template, TemplateError


def posted_json_data_to_formdata_data(formdef, data):
    data = copy.deepcopy(data)
    # remap fields from varname to field id
    for field in formdef.get_all_fields():
        if not field.varname:
            continue
        if field.varname not in data:
            continue
        raw = '%s_raw' % field.varname
        structured = '%s_structured' % field.varname
        if field.store_display_value and raw in data:
            data[field.id] = data.pop(raw)
            data['%s_display' % field.id] = data.pop(field.varname)
        else:
            data[field.id] = data.pop(field.varname)
        if field.store_structured_value and structured in data:
            data['%s_structured' % field.id] = data.pop(structured)

    # merge unnamed fields if they exist
    if '_unnamed' in data:
        unnamed_data = data.pop('_unnamed')
        for k in unnamed_data.keys():
            data[k] = unnamed_data.get('%s_raw' % k, unnamed_data.get(k))  # prefer raw value

    # create a temporary formdata so datasources using previous fields in
    # parameters can find their values.
    transient_formdata = formdef.data_class()()
    transient_formdata.data = data

    with get_publisher().substitutions.temporary_feed(transient_formdata, force_mode='lazy'):
        # complete/adapt field values
        for field in formdef.get_all_fields():
            structured = '%s_structured' % field.id
            display = '%s_display' % field.id
            if data.get(field.id) is None:
                continue
            if hasattr(field, 'from_json_value'):
                data[field.id] = field.from_json_value(data[field.id])
            # only fill display/structured if both are absent
            if display not in data and structured not in data:
                if field.store_display_value:
                    display_value = field.store_display_value(data, field.id)
                    if display_value is not None:
                        data[display] = display_value
                if field.store_structured_value:
                    structured_value = field.store_structured_value(data, field.id)
                    if structured_value is not None:
                        data[structured] = structured_value
    return data


def get_formdata_dict(formdata, user, consider_status_visibility=True):
    if consider_status_visibility and not formdata.is_draft():
        status = formdata.get_visible_status(user=user)
    else:
        status = formdata.get_status()

    status_name = None
    if formdata.is_draft():
        status_name = _('Draft')
    elif status:
        status_name = status.name

    d = {
        'name': formdata.formdef.name,
        'url': formdata.get_url(),
        'datetime': misc.strftime('%Y-%m-%d %H:%M:%S', formdata.receipt_time),
        'status': status_name,
        'status_css_class': status.extra_css_class if status else None,
        'keywords': formdata.formdef.keywords_list,
        'draft': formdata.is_draft(),
    }
    if formdata.last_update_time:
        d['last_update_time'] = misc.strftime('%Y-%m-%d %H:%M:%S', formdata.last_update_time)

    if formdata.is_draft():
        d['form_number_raw'] = d['form_number'] = None
        d['title'] = _('%(name)s (draft)') % {'name': formdata.formdef.name}
    else:
        d['title'] = _('%(name)s #%(id)s (%(status)s)') % {
            'name': formdata.formdef.name,
            'id': formdata.get_display_id(),
            'status': status_name or _('unknown'),
        }

    d.update(formdata.get_static_substitution_variables(minimal=True))
    if get_request().form.get('full') == 'on':
        d.update(formdata.get_json_export_dict(include_files=False, user=user))
    if d.get('form_receipt_datetime'):
        d['form_receipt_datetime'] = make_naive(d['form_receipt_datetime'].replace(microsecond=0))
    if d.get('form_last_update_datetime'):
        d['form_last_update_datetime'] = make_naive(d['form_last_update_datetime'].replace(microsecond=0))

    return d


class ApiFormdataPage(FormStatusPage):
    _q_exports_orig = ['', 'download']

    def _q_index(self):
        if get_request().get_method() == 'POST':
            return self.post()
        return self.json()

    def post(self):
        get_response().set_content_type('application/json')
        api_user = get_user_from_api_query_string()

        if self.formdata.is_draft():
            raise AccessForbiddenError(_('Formdata is not editable (still a draft).'))

        # check the formdata is currently editable
        wf_status = self.formdata.get_status()
        for item in wf_status.items:
            if not item.key == 'editable':
                continue
            if not item.check_auth(self.formdata, api_user):
                continue

            json_input = get_request().json
            if not isinstance(json_input, dict):
                raise RequestError(_('Payload is not a dict.'))
            if 'data' not in json_input:
                raise RequestError(_('Missing data entry in payload.'))
            data = posted_json_data_to_formdata_data(self.formdef, json_input['data'])
            old_data = copy.deepcopy(self.formdata.data)
            self.formdata.data.update(data)
            self.formdata.store()

            if self.formdata.jump_status(item.status):
                self.formdata.record_workflow_event('api-post-edit-action', action_item_id=item.id)
                self.formdata.perform_workflow()
            ContentSnapshotPart.take(formdata=self.formdata, old_data=old_data, user=api_user)
            self.formdata.store()

            return json.dumps({'err': 0, 'data': {'id': str(self.formdata.get_natural_key())}})

        raise AccessForbiddenError(_('Formdata is not editable by given user.'))

    def check_receiver(self):
        api_user = get_user_from_api_query_string()
        if not api_user:
            if get_request().user and get_request().user.is_admin:
                return  # grant access to admins, to ease debug
            raise AccessForbiddenError(_('User not authenticated.'))
        if not self.formdef.is_user_allowed_read_status_and_history(api_user, self.filled):
            raise AccessForbiddenError(_('Unsufficient roles.'))


class ApiFormPageMixin:
    allowed_signature_only_api = ['geojson', 'list']

    def __init__(self, component):
        try:
            self.formdef = self.formdef_class.get_by_urlname(component)
        except KeyError:
            raise TraversalError()
        self._view = None

    def check_access(self, api_name=None):
        if get_request().user and get_request().user.is_admin:
            return  # grant access to admins, to ease debug

        if get_request().has_anonymised_data_api_restriction() and is_url_signed():
            # when requesting anonymous data, a signature is enough
            return

        api_user = get_user_from_api_query_string(api_name=api_name)

        if (
            api_user is None
            and (api_name in self.allowed_signature_only_api or self.allowed_signature_only_api == ['*'])
            and is_url_signed()
        ):
            # signed but no user specified, grant access to (some) API
            class ApiAdminUser:
                id = Ellipsis  # make sure it fails all over the place if used
                is_admin = True
                is_api_user = True
                get_roles = lambda x: []

            get_request()._user = ApiAdminUser()
            return True

        if not api_user:
            raise AccessForbiddenError(_('User not authenticated.'))
        if not self.formdef.is_of_concern_for_user(api_user):
            raise AccessForbiddenError(_('Unsufficient roles.'))

    def _q_lookup(self, component):
        if component == 'ics':
            return self.ics()

        if not misc.is_ascii_digit(component) and not self._view:
            for view in self.get_custom_views(
                [StrictNotEqual('visibility', 'owner'), Equal('slug', component)]
            ):
                # /api/cards/<carddef-slug>/<custom view>/<optional card id>/
                self._view = view
                return self

        # check access for all paths (except webooks), to block access to
        # formdata that would otherwise be accessible if the user is the
        # submitter.
        if not self.is_webhook:
            self.check_access()
        try:
            formdata = self.formdef.data_class().get_by_id(component)
        except KeyError:
            raise TraversalError()
        return ApiFormdataPage(self.formdef, formdata, custom_view=self._view)

    def _q_traverse(self, path):
        if path[0] in ('list', 'ods', 'geojson', 'filter-options') and len([x for x in path if x]) < 3:
            # /api/cards/<carddef-slug>/<mode: one of list, ods, geojson>/
            # /api/cards/<carddef-slug>/<mode: one of list, ods, geojson>/<custom view>/
            if path[-1]:
                # always consider a trailing slash
                path.append('')
            if path[1]:
                for view in self.get_custom_views(
                    [StrictNotEqual('visibility', 'owner'), Equal('slug', path[1])]
                ):
                    self._view = view
                    break
                else:
                    path = ['not-found']
            path = [path[0]]

        if len(path) >= 2 and path[1] == 'ics':
            for view in self.get_custom_views(
                [StrictNotEqual('visibility', 'owner'), Equal('slug', path[0])]
            ):
                self._view = view
                path = path[1:]

        if misc.is_ascii_digit(path[-1]):
            # allow trailing / after <id>
            path.append('')

        self.is_webhook = False
        if len(path) > 1:
            # webhooks have their own access checks, request cannot be blocked
            # at this point.
            self.is_webhook = bool(path[1] == 'hooks')

        return super()._q_traverse(path)


class ApiFormPage(ApiFormPageMixin, BackofficeFormPage):
    _q_exports = [('list', 'json'), 'geojson', 'ods']  # restrict to API endpoints


class ApiCardPage(ApiFormPageMixin, BackofficeCardPage):
    _q_exports = [  # restricted to API endpoints
        ('list', 'json'),
        ('import-csv', 'import_csv'),
        ('import-json', 'import_json'),
        'geojson',
        'ods',
        ('@schema', 'schema'),
        'submit',
        ('filter-options', 'filter_options'),
    ]
    allowed_signature_only_api = ['*']

    def schema(self):
        if is_url_signed() or self.formdef.has_admin_access(get_user_from_api_query_string()):
            get_response().set_content_type('application/json')
            return self.formdef.export_to_json(
                include_id=get_query_flag('include-id'), with_user_fields=True, with_block_schemas=True
            )
        raise AccessForbiddenError()

    def submit(self):
        get_response().set_content_type('application/json')

        user = get_user_from_api_query_string()
        if user and user.is_api_user:
            pass  # API users are ok
        else:
            get_request()._user = user
        json_input = get_request().json
        formdata = self.formdef.data_class()()

        if not user:
            raise AccessForbiddenError(_('User not authenticated.'))
        if not self.formdef.has_creation_permission(user):
            raise AccessForbiddenError(_('User is not allowed to create card.'))

        if not isinstance(json_input, dict):
            raise RequestError(_('Invalid payload.'))

        if 'data' in json_input:
            # the published API expects data in 'data'.
            data = json_input['data']
        elif 'fields' in json_input:
            # but the API also supports data in 'fields', to match the json
            # output produded by wf/wscall.py.
            data = json_input['fields']
            if 'workflow' in json_input and json_input['workflow'].get('fields'):
                # handle workflow fields, put them all in the same data dictionary.
                data.update(json_input['workflow']['fields'])
            if 'extra' in json_input:
                data.update(json_input['extra'])
        else:
            data = {}

        if not isinstance(data, dict):
            raise RequestError(_('Invalid data parameter.'))

        formdata.data = posted_json_data_to_formdata_data(self.formdef, data)

        if 'user' in json_input:
            if not isinstance(json_input['user'], dict):
                raise RequestError(_('Invalid user parameter.'))
            formdata.set_user_from_json(json_input['user'])
        elif user and not user.is_api_user:
            formdata.user_id = user.id

        formdata.store()
        formdata.refresh_from_storage()
        formdata.just_created()
        formdata.update_workflow_data({'_source_ip': get_session()._remote_address})
        formdata.store()
        formdata.record_workflow_event('api-created')
        formdata.perform_workflow()
        formdata.store()
        return json.dumps(
            {
                'err': 0,
                'data': {
                    'id': str(formdata.id),
                    'url': formdata.get_url(),
                    'backoffice_url': formdata.get_url(backoffice=True),
                    'api_url': formdata.get_api_url(),
                },
            }
        )

    def import_csv(self):
        return self.import_file('csv')

    def import_json(self):
        return self.import_file('json')

    def import_file(self, file_format):
        if get_request().get_method() not in ('POST', 'PUT'):
            raise MethodNotAllowedError(allowed_methods=['POST', 'PUT'])
        get_request()._user = get_user_from_api_query_string()
        if not (get_request()._user and self.formdef.has_creation_permission(get_request()._user)):
            raise AccessForbiddenError(_('User is not allowed to import cards.'))

        afterjob = bool(get_request().form.get('async') == 'on')
        do_update = bool(get_request().form.get('update') == 'on')  # legacy

        update_mode = get_request().form.get('update-mode', 'update' if do_update else 'skip')
        if update_mode not in ('update', 'skip'):
            raise RequestError(_('Invalid update-mode parameter value.'))

        delete_mode = get_request().form.get('delete-mode', 'keep')
        if delete_mode not in ('delete', 'keep'):
            raise RequestError(_('Invalid delete-mode parameter value.'))

        get_response().set_content_type('application/json')
        try:
            if file_format == 'csv':
                if get_request().get_method() == 'POST':
                    try:
                        content = base64.decodebytes(force_bytes(get_request().json['file']['content']))
                    except (ValueError, KeyError):
                        raise HttpResponse200Error(
                            _('Invalid format (must be {"file": {"content": base64}}).'),
                            err_code='invalid-format',
                        )
                else:
                    content = get_request().stdin.read()
                job = self.import_csv_submit(
                    content, update_mode=update_mode, delete_mode=delete_mode, afterjob=afterjob, api=True
                )
            elif file_format == 'json':
                job = self.import_json_submit(
                    get_request().json,
                    update_mode=update_mode,
                    delete_mode=delete_mode,
                    afterjob=afterjob,
                    api=True,
                )
        except ValueError as e:
            raise RequestError(str(e))
        if job is None:
            return json.dumps({'err': 0})
        return json.dumps(
            {
                'err': 0,
                'data': {
                    'job': {
                        'id': str(job.id),
                        'url': get_publisher().get_frontoffice_url() + '/api/jobs/%s/' % job.id,
                    }
                },
            }
        )

    def filter_options(self):
        self.view_type = 'json'
        return super().filter_options()


class CardFileByTokenDirectory(Directory):
    def _q_lookup(self, component):
        get_request().ignore_session = True
        try:
            token = get_session().get_token('card-file-by-token', component)
        except KeyError:
            raise TraversalError()

        context = token.data
        carddef = CardDef.get_by_urlname(context['carddef_slug'])
        data = carddef.data_class().get(context['data_id'])
        for field_data in data.get_all_file_data(with_history=True):
            if not hasattr(field_data, 'file_digest'):
                continue
            if field_data.file_digest() == context['file_digest']:
                return FileDirectory.serve_file(field_data)
        raise TraversalError()


class SignUrlTokenDirectory(Directory):
    def _q_lookup(self, component):
        get_request().ignore_session = True
        try:
            token = get_session().get_token('sign-url-token', component)
        except KeyError:
            raise TraversalError()
        return redirect(sign_url_auto_orig(token.data['url']))


class ApiFormsDirectory(Directory):
    _q_exports = ['', 'geojson']

    def check_access(self):
        if not is_url_signed():
            api_user = get_user_from_api_query_string()
            if api_user and api_user.is_api_user:
                # API users are ok
                return

            # grant access to admins, to ease debug
            if not (get_request().user and get_request().user.is_admin):
                raise AccessForbiddenError(_('User not authenticated.'))
            ignore_roles = get_query_flag('ignore-roles')
            if ignore_roles and not get_request().user.can_go_in_backoffice():
                raise AccessForbiddenError(
                    _('User is not allowed access to backoffice, cannot ignore roles.')
                )

    @classmethod
    def get_related_filter_criteria(cls):
        related_filter = get_request().form.get('related')
        if related_filter:
            try:
                formdef_type, formdef_slug, formdata_id = related_filter.split(':')
            except ValueError:
                return Nothing()
            try:
                formdef = {'carddef': CardDef, 'formdef': FormDef}[formdef_type].get_by_slug(formdef_slug)
                if formdef.id_template:
                    formdata_id = str(formdef.data_class().get_by_id(formdata_id).id)
            except KeyError:
                return Nothing()
            key = f'{formdef_type}:{formdef_slug}'
            return ElementIntersects('relations_data', key, [formdata_id])

    def _q_index(self):
        self.check_access()
        get_request()._user = get_user_from_api_query_string() or get_request().user

        if get_request().form.get('full') == 'on':
            raise RequestError(_('No such parameter "full".'))

        if not (FormDef.exists()):
            # early return, this avoids running a query against a missing SQL view.
            get_response().set_content_type('application/json')
            return json.dumps({'data': []}, cls=misc.JSONEncoder)

        from wcs import sql

        management_directory = ManagementDirectory()
        criterias = management_directory.get_global_listing_criterias()
        if get_query_flag('ignore-roles'):
            roles_criterias = criterias
            criterias = management_directory.get_global_listing_criterias(ignore_user_roles=True)

        if not get_query_flag('include-anonymised', default=False):
            criterias.append(Null('anonymised'))

        related_filter_criteria = self.get_related_filter_criteria()
        if related_filter_criteria is not None:
            criterias.append(related_filter_criteria)

        limit = misc.get_int_or_400(
            get_request().form.get('limit', get_publisher().get_site_option('default-page-size') or 20)
        )
        offset = misc.get_int_or_400(get_request().form.get('offset', 0))
        order_by = misc.get_order_by_or_400(
            get_request().form.get(
                'order_by', get_publisher().get_site_option('default-sort-order') or '-receipt_time'
            )
        )

        formdatas = sql.AnyFormData.select(criterias, order_by=order_by, limit=limit, offset=offset)
        if get_query_flag('ignore-roles'):
            # When ignoring roles formdatas will be returned even if they are
            # not readable by the user, an additional attribute (readable) is
            # added to differentiate readable and non-readable formdatas.
            #
            # A full SQL query is run as it will benefit from cached
            # concerned_roles/action_roles.
            limited_formdatas = [
                (x.formdef.id, x.id)
                for x in sql.AnyFormData.select(
                    roles_criterias, order_by=order_by, limit=limit, offset=offset
                )
            ]
            output = []
            for formdata in formdatas:
                readable = bool((formdata.formdef.id, formdata.id) in limited_formdatas)
                if not readable and formdata.formdef.skip_from_360_view:
                    continue
                formdata_dict = get_formdata_dict(
                    formdata, user=get_request().user, consider_status_visibility=False
                )
                formdata_dict['readable'] = readable
                output.append(formdata_dict)
        else:
            output = [
                get_formdata_dict(x, user=get_request().user, consider_status_visibility=False)
                for x in formdatas
            ]

        get_response().set_content_type('application/json')
        return json.dumps({'data': output}, cls=misc.JSONEncoder)

    def geojson(self):
        self.check_access()
        get_request()._user = get_user_from_api_query_string() or get_request().user
        return ManagementDirectory().geojson()

    def _q_lookup(self, component):
        return ApiFormPage(component)


class ApiCardsDirectory(Directory):
    _q_exports = [('@list', 'list')]

    def list(self):
        views = get_publisher().custom_view_class.select([StrictNotEqual('visibility', 'owner')])

        def get_custom_views(carddef):
            custom_views = []
            for view in views:
                if view.match(user=None, formdef=carddef):
                    custom_views.append({'id': view.slug, 'text': view.title})
            custom_views.sort(key=lambda x: misc.simplify(x['text']))
            return custom_views

        get_response().set_content_type('application/json')
        if not is_url_signed():
            user = get_user_from_api_query_string() or get_request().user
            if not get_publisher().get_backoffice_root().is_global_accessible('cards', user=user):
                raise AccessForbiddenError(_('Unsigned request or API user has no access to cards.'))
        carddefs = CardDef.select(order_by='name', ignore_errors=True, lightweight=True)
        data = [
            {
                'id': x.url_name,
                'text': x.name,
                'title': x.name,
                'slug': x.url_name,
                'url': x.get_url(),
                'category_slug': x.category.url_name if x.category else None,
                'category_name': x.category.name if x.category else None,
                'description': x.description or '',
                'keywords': x.keywords_list,
                'custom_views': get_custom_views(x),
            }
            for x in carddefs
        ]
        return json.dumps({'data': data, 'err': 0})

    def _q_lookup(self, component):
        return ApiCardPage(component)


class ApiFormdefDirectory(Directory):
    _q_exports = ['schema', 'submit']

    def __init__(self, formdef):
        self.formdef = formdef

    def schema(self):
        if is_url_signed() or self.formdef.has_admin_access(get_user_from_api_query_string()):
            get_response().set_content_type('application/json')
            return self.formdef.export_to_json(
                include_id=get_query_flag('include-id'), with_block_schemas=True
            )
        raise AccessForbiddenError()

    def submit(self):
        # expects json as input
        #  {
        #   "meta": {
        #      "attr": "value"
        #   },
        #   "data": {
        #      "0": "value",
        #      "1": "value",
        #      ...
        #   }
        #  }
        get_response().set_content_type('application/json')
        if self.formdef.is_disabled():
            raise AccessForbiddenError(_('Disabled form.'))
        user = get_user_from_api_query_string()
        if user and user.is_api_user:
            pass  # API users are ok
        elif not is_url_signed():
            raise AccessForbiddenError(_('Unsigned API call.'))
        json_input = get_request().json
        formdata = self.formdef.data_class()()

        if not isinstance(json_input, dict):
            raise RequestError(_('Invalid payload.'))

        if 'data' in json_input:
            # the published API expects data in 'data'.
            data = json_input['data']
        elif 'fields' in json_input:
            # but the API also supports data in 'fields', to match the json
            # output produded by wf/wscall.py.
            data = json_input['fields']
            if 'workflow' in json_input and json_input['workflow'].get('fields'):
                # handle workflow fields, put them all in the same data dictionary.
                data.update(json_input['workflow']['fields'])
            if 'extra' in json_input:
                data.update(json_input['extra'])
        else:
            data = {}

        if not isinstance(data, dict):
            raise RequestError(_('Invalid data parameter.'))

        formdata.data = posted_json_data_to_formdata_data(self.formdef, data)

        meta = json_input.get('meta') or {}
        if meta.get('backoffice-submission') or user and user.is_api_user:
            if not user:
                raise AccessForbiddenError(_('User not authenticated.'))
            if not self.formdef.backoffice_submission_roles:
                raise AccessForbiddenError(
                    _('No backoffice submission roles on form.'), err_code='no-backoffice-submission-role'
                )
            if not set(user.get_roles()).intersection(self.formdef.backoffice_submission_roles):
                raise AccessForbiddenError(
                    _('User is not allowed to perform backoffice submission.'),
                    err_code='user-not-allowed-backoffice-submission',
                )
            formdata.backoffice_submission = True
        if not meta.get('backoffice-submission'):
            if 'user' in json_input:
                if not isinstance(json_input['user'], dict):
                    raise RequestError(_('Invalid user parameter.'))
                formdata.set_user_from_json(json_input['user'])
            elif user and not user.is_api_user:
                formdata.user_id = user.id

        if json_input.get('context'):
            formdata.submission_context = json_input['context']
            formdata.submission_channel = formdata.submission_context.pop('channel', None)
            formdata.user_id = formdata.submission_context.pop('user_id', None)

        if self.formdef.only_allow_one and formdata.user_id:
            user_id = formdata.user_id
            user_forms = self.formdef.data_class().select([Equal('user_id', str(user_id))])
            if [x for x in user_forms if not x.is_draft()]:
                raise AccessForbiddenError(_('Only one formdata by user is allowed.'))

        if meta.get('backoffice-submission') and not user.is_api_user:
            # keep track of the agent that did the submit
            formdata.submission_agent_id = str(user.id)

        formdata.store()

        response = {
            'err': 0,
            'data': {
                'id': str(formdata.id),
                'url': formdata.get_url(),
                'backoffice_url': formdata.get_url(backoffice=True),
                'api_url': formdata.get_api_url(),
            },
        }

        if self.formdef.enable_tracking_codes:
            code = TrackingCode()
            code.formdata = formdata  # this will .store() the code
            response['tracking_code'] = formdata.tracking_code
        if meta.get('draft'):
            formdata.status = 'draft'
            formdata.receipt_time = localtime()
            formdata.store()
        else:
            formdata.refresh_from_storage()
            formdata.just_created()
            formdata.update_workflow_data({'_source_ip': get_session()._remote_address})
            formdata.store()
            formdata.record_workflow_event('api-created')
            formdata.perform_workflow()
            formdata.store()
        return json.dumps(response)


class ApiFormdefsDirectory(Directory):
    _q_exports = ['']

    def __init__(self, category=None):
        self.category = category

    def get_list_forms(self, user, list_all_forms=False, formdefs=None, backoffice_submission=False):
        list_forms = []

        if not user and backoffice_submission:
            return list_forms

        if get_request().form.get('q'):
            from wcs import sql

            object_ids = sql.SearchableFormDef.search(FormDef.xml_root_node, get_request().form.get('q'))
            if formdefs is None:
                formdefs = FormDef.get_ids(object_ids, ignore_errors=True, lightweight=True)
            else:
                formdefs = [x for x in formdefs if str(x.id) in object_ids]
        elif formdefs is None:
            formdefs = FormDef.select(order_by='name', ignore_errors=True, lightweight=True)

        include_disabled = get_query_flag('include-disabled')
        category_slugs = (get_request().form.get('category_slugs') or '').split(',')
        category_slugs = [c.strip() for c in category_slugs if c.strip()]

        if not include_disabled:
            if backoffice_submission:
                formdefs = [x for x in formdefs if not x.is_disabled()]
            else:
                formdefs = [x for x in formdefs if not x.is_disabled() or x.disabled_redirection]

        if self.category:
            formdefs = [x for x in formdefs if str(x.category_id) == str(self.category.id)]
        elif category_slugs:
            formdefs = [x for x in formdefs if x.category and (x.category.url_name in category_slugs)]

        include_count = get_query_flag('include-count')

        for formdef in formdefs:
            authentication_required = False
            if formdef.roles and not list_all_forms and not backoffice_submission:
                if not user:
                    if not formdef.always_advertise:
                        continue
                    authentication_required = True
                elif logged_users_role().id not in formdef.roles:
                    for q in user.get_roles():
                        if q in formdef.roles:
                            break
                    else:
                        if not formdef.always_advertise:
                            continue
                        authentication_required = True
            elif backoffice_submission:
                if not formdef.backoffice_submission_roles:
                    continue
                for role in user.get_roles():
                    if role in formdef.backoffice_submission_roles:
                        break
                else:
                    continue
            elif formdef.roles and user is None and list_all_forms:
                # anonymous API call, mark authentication as required
                authentication_required = True

            formdict = {
                'title': formdef.name,
                'slug': formdef.url_name,
                'url': formdef.get_url(),
                'description': formdef.description or '',
                'keywords': formdef.keywords_list,
                'authentication_required': authentication_required,
                'always_advertise': formdef.always_advertise,
            }
            if formdef.required_authentication_contexts:
                formdict['required_authentication_contexts'] = formdef.required_authentication_contexts
            if backoffice_submission:
                formdict['backoffice_submission_url'] = formdef.get_backoffice_submission_url()

            formdict['redirection'] = bool(formdef.is_disabled() and formdef.disabled_redirection)

            if include_count:
                # we include the count of submitted forms so it's possible to sort
                # them by "popularity"
                from wcs import sql

                # 4 * number of submitted forms of last 2 days
                # + 2 * number of submitted forms of last 8 days
                # + 1 * number of submitted forms of last 30 days
                # exclude drafts
                criterias = [Equal('formdef_id', formdef.id), StrictNotEqual('status', 'draft')]
                d_now = datetime.datetime.now()
                count = 4 * sql.get_period_total(
                    period_start=d_now - datetime.timedelta(days=2),
                    include_start=True,
                    criterias=criterias,
                )
                count += 2 * sql.get_period_total(
                    period_start=d_now - datetime.timedelta(days=8),
                    include_start=True,
                    period_end=d_now - datetime.timedelta(days=2),
                    include_end=False,
                    criterias=criterias,
                )
                count += sql.get_period_total(
                    period_start=d_now - datetime.timedelta(days=30),
                    include_start=True,
                    period_end=d_now - datetime.timedelta(days=8),
                    include_end=False,
                    criterias=criterias,
                )
                formdict['count'] = count

            formdict['functions'] = {}
            formdef_workflow_roles = formdef.workflow_roles or {}
            for wf_role_id, wf_role_label in formdef.workflow.roles.items():
                workflow_function = {'label': wf_role_label}
                role_id = formdef_workflow_roles.get(wf_role_id)
                if role_id:
                    try:
                        workflow_function['role'] = (
                            get_publisher().role_class.get(role_id).get_json_export_dict()
                        )
                    except KeyError:
                        pass
                formdict['functions'][wf_role_id] = workflow_function

            if formdef.category:
                formdict['category'] = formdef.category.name
                formdict['category_slug'] = formdef.category.url_name

            list_forms.append(formdict)

        return list_forms

    def _q_index(self):
        try:
            user = get_user_from_api_query_string()
        except UnknownNameIdAccessForbiddenError:
            # if authenticating the user via the query string failed, return
            # results for the anonymous case; user is set to 'False' as a
            # signed URL with a None user is considered like an appropriate
            # webservice call.
            user = False
        url_signed = is_url_signed()
        if user and user.is_api_user:
            pass  # API users are ok
        elif not url_signed:
            if not (get_request().user and get_request().user.is_admin):
                raise AccessForbiddenError(_('User not authenticated.'))
            user = get_request().user

        list_all_forms = (user and user.is_admin) or (url_signed and user is None)
        backoffice_submission = get_query_flag('backoffice-submission')

        list_forms = self.get_list_forms(user, list_all_forms, backoffice_submission=backoffice_submission)

        get_response().set_content_type('application/json')
        return json.dumps({'err': 0, 'data': list_forms})

    def _q_lookup(self, component):
        try:
            formdef = FormDef.get_by_urlname(component)
        except KeyError:
            raise TraversalError()
        return ApiFormdefDirectory(formdef)


class ApiCategoryDirectory(Directory):
    _q_exports = ['formdefs']

    def __init__(self, category):
        self.category = category
        self.formdefs = ApiFormdefsDirectory(category)


class ApiCategoriesDirectory(Directory):
    _q_exports = ['']

    def __init__(self):
        pass

    def _q_index(self):
        try:
            user = get_user_from_api_query_string() or get_request().user
        except UnknownNameIdAccessForbiddenError:
            # the name id was unknown, return the categories for anonymous
            # users.
            user = None
        list_all_forms = (user and user.is_admin) or (is_url_signed() and user is None)
        backoffice_submission = get_request().form.get('backoffice-submission') == 'on'
        list_categories = []
        categories = Category.select()
        Category.sort_by_position(categories)
        all_formdefs = FormDef.select(order_by='name', ignore_errors=True, lightweight=True)
        for category in categories:
            d = {}
            d['title'] = category.name
            d['slug'] = category.url_name
            d['url'] = category.get_url()
            if category.description:
                d['description'] = str(category.get_description_html_text())
            formdefs = ApiFormdefsDirectory(category).get_list_forms(
                user,
                formdefs=all_formdefs,
                list_all_forms=list_all_forms,
                backoffice_submission=backoffice_submission,
            )
            if not formdefs:
                # don't advertise empty categories
                continue
            keywords = {}
            for formdef in formdefs:
                for keyword in formdef['keywords']:
                    keywords[keyword] = True
            d['keywords'] = list(keywords.keys())
            if get_request().form.get('full') == 'on':
                d['forms'] = formdefs
            list_categories.append(d)
        get_response().set_content_type('application/json')
        return json.dumps({'data': list_categories})

    def _q_lookup(self, component):
        try:
            return ApiCategoryDirectory(Category.get_by_urlname(component))
        except KeyError:
            raise TraversalError()


class ApiUserDirectory(Directory):
    _q_exports = ['', 'forms', 'drafts', 'preferences']

    def __init__(self, user=None):
        self.user = user

    def _q_index(self):
        get_response().set_content_type('application/json')
        user = self.user or get_user_from_api_query_string() or get_request().user
        if not user:
            raise AccessForbiddenError(_('User not authenticated.'))
        if user.is_api_user:
            raise AccessForbiddenError(_('Restricted API access.'))

        user_roles = user.get_roles_objects()
        role_prefetch = {str(role.id): role for role in user_roles}
        user_info = user.get_substitution_variables(prefix='', role_prefetch=role_prefetch)
        del user_info['user']
        user_info['id'] = user.id
        user_info['user_roles'] = [x.get_json_export_dict() for x in user_roles if x]
        return json.dumps(user_info, cls=misc.JSONEncoder)

    def get_user_forms(self, user, include_drafts=False, include_non_drafts=True):
        if not (FormDef.exists()):
            # early return, this avoids running a query against a missing SQL view.
            return []

        category_slugs = (get_request().form.get('category_slugs') or '').split(',')
        category_slugs = [c.strip() for c in category_slugs if c.strip()]
        if category_slugs:
            categories = Category.select([st.Contains('slug', category_slugs)])

        from wcs import sql

        order_by = 'receipt_time'
        if get_request().form.get('sort') == 'desc':
            order_by = '-receipt_time'
        if get_query_flag('include-accessible'):
            user_roles = user.get_roles()
            criterias = [
                Or(
                    [
                        Intersects('concerned_roles_array', user_roles),
                        Equal('user_id', str(user.id)),
                    ]
                )
            ]
        else:
            criterias = [Equal('user_id', str(user.id))]
        if category_slugs:
            criterias.append(Contains('category_id', [c.id for c in categories]))

        status_criteria = get_request().form.get('status') or 'all'
        if status_criteria == 'open':
            criterias.append(Equal('is_at_endpoint', False))
        elif status_criteria == 'done':
            criterias.append(Equal('is_at_endpoint', True))
        elif status_criteria == 'all':
            pass
        else:
            raise RequestError(_('Invalid status parameter value.'))

        related_filter_criteria = ApiFormsDirectory.get_related_filter_criteria()
        if related_filter_criteria is not None:
            criterias.append(related_filter_criteria)

        if include_drafts:
            disabled_formdef_ids = [formdef.id for formdef in FormDef.select() if formdef.is_disabled()]
            if disabled_formdef_ids:
                criterias.append(
                    Or(
                        [
                            StrictNotEqual('status', 'draft'),
                            NotContains('formdef_id', disabled_formdef_ids),
                        ]
                    )
                )
        else:
            criterias.append(StrictNotEqual('status', 'draft'))

        if not include_non_drafts:
            criterias.append(Equal('status', 'draft'))

        user_forms = sql.AnyFormData.select(
            criterias,
            limit=misc.get_int_or_400(get_request().form.get('limit')),
            offset=misc.get_int_or_400(get_request().form.get('offset')),
            order_by=order_by,
        )
        if get_request().form.get('full') == 'on':
            # load full objects
            formdefs = {x.formdef_id: x.formdef for x in user_forms}
            formdef_user_forms = {}
            for formdef_id, formdef in formdefs.items():
                formdef_user_forms.update(
                    {
                        (formdef_id, x.id): x
                        for x in formdef.data_class().select(
                            [Contains('id', [x.id for x in user_forms if x.formdef_id == formdef_id])]
                        )
                    }
                )
            # and put them back in order
            sorted_user_forms_tuples = [(x.formdef_id, x.id) for x in user_forms]
            user_forms = [formdef_user_forms.get(x) for x in sorted_user_forms_tuples]
        else:
            # prefetch evolutions to avoid individual loads when computing
            # formdata.get_visible_status().
            sql.AnyFormData.load_all_evolutions(user_forms)
        return user_forms

    def drafts(self):
        return self.forms(include_drafts=True, include_non_drafts=False)

    def forms(self, include_drafts=False, include_non_drafts=True):
        include_drafts = include_drafts or get_query_flag('include-drafts')

        get_response().set_content_type('application/json')
        try:
            user = self.user or get_user_from_api_query_string() or get_request().user
        except UnknownNameIdAccessForbiddenError:
            raise HttpResponse200Error(_('Unknown NameID.'), err_code='unknown-name-id')
        if not user:
            raise HttpResponse200Error(_('No user specified.'), err_code='missing-user')
        if user.is_api_user:
            raise AccessForbiddenError(_('Restricted API access.'))

        query_user = get_user_from_api_query_string() or get_request().user
        if query_user and query_user.is_api_user and query_user.api_access.restrict_to_anonymised_data:
            raise AccessForbiddenError(_('Restricted API access.'))

        forms = self.get_user_forms(
            user, include_drafts=include_drafts, include_non_drafts=include_non_drafts
        )

        if self.user:
            # call to /api/users/<id>/forms, this returns the forms of the
            # given user filtered according to the permissions of the caller
            # (from query string or session).
            if query_user and query_user.id != self.user.id:
                if not query_user.is_api_user and not query_user.can_go_in_backoffice():
                    raise AccessForbiddenError(_('User not allowed to query data from other users.'))
                # mark forms that are readable by querying user
                user_roles = set(query_user.get_roles())
                # use concerned_roles_array attribute that was saved in the
                # table.
                for form in forms:
                    form.readable = bool(set(form.concerned_roles_array).intersection(user_roles))
                # ignore confidential forms
                forms = [x for x in forms if x.readable or not x.formdef.skip_from_360_view]

        result = []
        for form in forms:
            if form.is_draft():
                if not include_drafts:
                    continue
                if form.formdef.is_disabled():
                    # the form or its draft support has been disabled
                    continue
            elif not include_non_drafts:
                continue
            formdata_dict = get_formdata_dict(form, user)
            if not formdata_dict:
                # skip hidden forms
                continue
            formdata_dict['readable'] = getattr(form, 'readable', True)
            result.append(formdata_dict)

        return json.dumps({'err': 0, 'data': result}, cls=misc.JSONEncoder)

    def preferences(self):
        if get_request().get_method() != 'POST':
            raise MethodNotAllowedError(allowed_methods=['POST'])
        get_response().set_content_type('application/json')
        user = self.user or get_request().user
        if not user:
            raise AccessForbiddenError(_('User not authenticated.'))
        if int(get_request().environ.get('CONTENT_LENGTH')) > 1000:
            # protect against storing "huge" blobs
            raise RequestError(_('Too much data.'))
        user.update_preferences(get_request().json)
        return json.dumps({'err': 0})


class ApiUsersDirectory(Directory):
    _q_exports = ['']

    def can_create_cards(self, user):
        if user.roles:
            return CardDef.exists([Intersects('backoffice_submission_roles', [str(x) for x in user.roles])])
        return False

    def _q_index(self):
        get_response().set_content_type('application/json')

        api_user = get_user_from_api_query_string()
        if api_user and api_user.is_api_user and api_user.api_access.restrict_to_anonymised_data:
            raise AccessForbiddenError(_('Restricted API access.'))

        if not (
            (is_url_signed() and not (api_user and api_user.is_api_user))
            or (
                get_request().user
                and (
                    get_request().user.can_go_in_admin()
                    or SubmissionDirectory().is_accessible(get_request().user)
                    or self.can_create_cards(get_request().user)
                )
            )
        ):
            # request must be signed, or user must be an administrator or
            # allowed to submit forms (as they have a form to select an user).
            raise AccessForbiddenError(_('Unauthenticated/unsigned request or no access to users.'))

        criterias = [Null('deleted_timestamp')]
        query = get_request().form.get('q')
        if query:
            from wcs.admin.settings import UserFieldsFormDef

            formdef = UserFieldsFormDef()
            criteria_fields = [
                ILike('name', query),
                ILike('ascii_name', misc.simplify(query, ' ')),
                ILike('email', query),
                Intersects('name_identifiers', [query]),
            ]
            for field in formdef.fields:
                if field.key in ('string', 'text', 'email'):
                    criteria_fields.append(ILike('f%s' % field.id, query))
            criteria_fields.append(FtsMatch(query))
            criterias.append(Or(criteria_fields))

        roles = get_request().form.get('roles')
        if roles:
            criterias.append(Intersects('roles', roles.split(',')))

        role_prefetch = {str(role.id): role for role in get_publisher().role_class.select()}

        users_cfg = get_cfg('users', {})
        template_string = (
            users_cfg.get('search_result_template')
            or get_publisher().user_class.default_search_result_template
        )
        template = None
        if Template.is_template_string(template_string):
            template = Template(template_string)

        def as_dict(user):
            user_info = user.get_substitution_variables(prefix='', role_prefetch=role_prefetch)
            del user_info['user']
            user_info['user_id'] = user.id
            user_roles = user.get_roles_objects(role_prefetch=role_prefetch)
            user_info['user_roles'] = [x.get_json_export_dict() for x in user_roles if x]
            # add attributes to be usable as datasource
            user_info['id'] = user.id
            user_info['text'] = user_info['user_display_name']
            try:
                user_info['description'] = template.render(user_info)
            except TemplateError:
                pass
            return user_info

        limit = misc.get_int_or_400(get_request().form.get('limit'))
        users = get_publisher().user_class.select(order_by='name', clause=criterias, limit=limit)
        data = [as_dict(x) for x in users]
        return json.dumps({'data': data, 'err': 0}, cls=misc.JSONEncoder)

    def _q_lookup(self, component):
        api_user = get_user_from_api_query_string()
        if api_user and api_user.is_api_user:
            # API users are ok except if they are restricted to anonymised data
            if api_user.api_access.restrict_to_anonymised_data:
                raise AccessForbiddenError(_('Restricted API access.'))
        elif not (is_url_signed() or (get_request().user and get_request().user.can_go_in_admin())):
            raise AccessForbiddenError(_('Unsigned request or user has no access to backoffice.'))

        user_class = get_publisher().user_class
        try:
            int(component)  # makes sure this is an id
            user = user_class.get(component)
        except (KeyError, ValueError):
            try:
                user = user_class.get_users_with_name_identifier(component)[0]
            except IndexError:
                raise TraversalError()
        return ApiUserDirectory(user)


class ApiTrackingCodeDirectory(Directory):
    def _q_lookup(self, component):
        # /api/code/$code
        # * allows signed requests
        # * allows HTTP basic auth requests with API user with no role restriction
        get_response().set_content_type('application/json')

        user = get_user_from_api_query_string()
        if not (user and user.is_api_user and not user.roles):  # HTTP auth
            if not is_url_signed():  # signed request
                raise AccessForbiddenError(_('Missing signature.'))
        try:
            tracking_code = TrackingCode.get(component)
        except KeyError:
            raise TraversalError()
        try:
            formdata = tracking_code.formdata
        except KeyError:
            raise TraversalError()
        if formdata.formdef.enable_tracking_codes is False:
            raise TraversalError()
        # return load_url with a temporary access URL so the caller can directly
        # redirect the user to the formdata.
        data = {
            'err': 0,
            'url': formdata.get_url(backoffice=get_query_flag('backoffice')),
            'load_url': formdata.get_temporary_access_url(
                duration=300, backoffice=get_query_flag('backoffice')
            ),
        }
        return json.dumps(data)


class AutocompleteDirectory(Directory):
    def _q_lookup(self, component):
        get_request().ignore_session = True
        try:
            autocomplete_context = get_session().get_token('autocomplete', component)
            if autocomplete_context.data.get('url') == '':
                # this is a datasource without a json url
                # (typically a Python source)
                raise KeyError()
        except KeyError:
            raise AccessForbiddenError()
        get_response().set_content_type('application/json')

        info = autocomplete_context.data

        if 'edited_test_id' not in info:
            return self.get_json(info)

        from wcs.testdef import TestDef

        testdef = TestDef.get(info['edited_test_id'])
        with testdef.use_test_objects(results=testdef.get_last_dependencies_results()):
            return self.get_json(info)

    def get_json(self, info):
        if 'url' in info:
            named_data_source = None
            cache_duration = 0
            if info.get('data_source'):
                named_data_source = NamedDataSource.get(info['data_source'])
                if named_data_source.cache_duration:
                    cache_duration = int(named_data_source.cache_duration)
            url = info['url']
            url += urllib.parse.quote(get_request().form.get('q', ''))
            get_response().set_content_type('application/json')
            entries = request_json_items(
                url,
                named_data_source and named_data_source.extended_data_source,
                cache_duration=cache_duration,
            )
            if entries is not None:
                return json.dumps({'err': 0, 'data': entries})
            return json.dumps({'err': 1, 'data': []})

        # carddef_ref in info
        carddef_ref = info['carddef_ref']
        custom_view = None
        if 'dynamic_custom_view' in info:
            custom_view = get_publisher().custom_view_class.get(info['dynamic_custom_view'])
            custom_view.filters = info['dynamic_custom_view_filters']
        query = get_request().form.get('q', '')
        limit = misc.get_int_or_400(get_request().form.get('page_limit'))
        with_related = info.get('with_related')
        with_related_urls = query and limit and with_related
        values = CardDef.get_data_source_items(
            carddef_ref,
            custom_view=custom_view,
            query=query,
            limit=limit,
            with_related_urls=with_related_urls,
        )
        keys = ['id', 'text']
        if with_related_urls:
            keys += ['edit_related_url', 'view_related_url']
        return json.dumps({'data': [{key: x.get(key, '') for key in keys} for x in values]})


class GeoJsonDirectory(Directory):
    def _q_lookup(self, component):
        url = None
        try:
            data_source = get_data_source_object({'type': component}, ignore_errors=False)
        except KeyError:
            try:
                context = get_session().get_token('geojson', component)
            except KeyError:
                raise TraversalError()
            info = context.data
            try:
                data_source = get_data_source_object({'type': info['slug']}, ignore_errors=False)
            except KeyError:
                raise TraversalError()
            url = info['url']
        get_response().set_content_type('application/json')
        return json.dumps(data_source.get_geojson_data(force_url=url))


class AfterJobDirectory(Directory):
    _q_exports = ['']

    def __init__(self, afterjob):
        self.afterjob = afterjob

    def _q_index(self):
        get_response().set_content_type('application/json')
        data = {
            'status': self.afterjob.status,
            'label': self.afterjob.label,
            'creation_time': self.afterjob.creation_time,
            'completion_time': self.afterjob.completion_time,
            'completion_status': self.afterjob.get_completion_status(),
        }
        if self.afterjob.status == 'failed':
            data['failure_label'] = self.afterjob.failure_label
        if hasattr(self.afterjob, 'result_data'):
            data['job_result_data'] = self.afterjob.result_data

        return json.dumps({'err': 0, 'data': data}, cls=misc.JSONEncoder)


class AfterJobsDirectory(Directory):
    _q_exports = []

    def _q_lookup(self, component):
        api_user = get_user_from_api_query_string()
        if api_user and api_user.is_api_user:
            pass  # API users are ok
        elif not (is_url_signed() or (get_request().user and get_request().user.is_admin)):
            raise AccessForbiddenError(_('Unsigned request or user is not admin.'))
        try:
            afterjob = AfterJob.get(component, ignore_errors=False)
        except KeyError:
            raise TraversalError()
        return AfterJobDirectory(afterjob)


class ApiDirectory(Directory):
    _q_exports = [
        'forms',
        'roles',
        ('reverse-geocoding', 'reverse_geocoding'),
        'formdefs',
        'categories',
        'user',
        'users',
        'code',
        'autocomplete',
        'cards',
        'geojson',
        'jobs',
        ('card-file-by-token', 'card_file_by_token'),
        ('preview-payload-structure', 'preview_payload_structure'),
        ('sign-url-token', 'sign_url_token'),
        ('logged-errors-recent-count', 'logged_errors_recent_count'),
    ]

    cards = ApiCardsDirectory()
    forms = ApiFormsDirectory()
    formdefs = ApiFormdefsDirectory()
    categories = ApiCategoriesDirectory()
    user = ApiUserDirectory()
    users = ApiUsersDirectory()
    code = ApiTrackingCodeDirectory()
    autocomplete = AutocompleteDirectory()
    geojson = GeoJsonDirectory()
    jobs = AfterJobsDirectory()
    card_file_by_token = CardFileByTokenDirectory()
    sign_url_token = SignUrlTokenDirectory()

    def roles(self):
        get_response().set_content_type('application/json')
        if not (is_url_signed() or (get_request().user and get_request().user.can_go_in_admin())):
            raise AccessForbiddenError(_('Unsigned request or user has no access to backoffice.'))
        list_roles = []
        for role in get_publisher().role_class.select():
            if not role.is_internal():
                list_roles.append(role.get_json_export_dict())
        get_response().set_content_type('application/json')
        return json.dumps({'err': 0, 'data': list_roles})

    def preview_payload_structure(self):
        get_response().raw = True

        if not (get_request().user and get_request().user.can_go_in_admin()):
            raise AccessForbiddenError(_('User has no access to backoffice.'))

        def parse_payload():
            payload = {}
            for param, value in get_request().form.items():
                # skip elements which are not part of payload
                if 'post_data$element' not in param:
                    continue
                prefix, order, field = re.split(r'(\d+)(?!\d)', param)  # noqa pylint: disable=unused-variable
                # skip elements that aren't ordered
                if not order:
                    continue

                if order not in payload:
                    payload[order] = []

                if field == 'key':
                    # skip empty keys
                    if not value:
                        continue
                    # insert key on first position
                    payload[order].insert(0, value)
                else:
                    payload[order].append(value)
            return dict([v for v in payload.values() if len(v) > 1])

        def format_payload(o, html=htmltext(''), last_element=True):
            if isinstance(o, (list, tuple)):
                html += htmltext('[<span class="payload-preview--obj">')
                while True:
                    try:
                        head, tail = o[0], o[1:]
                    except IndexError:
                        break
                    html = format_payload(head, html=html, last_element=len(tail) < 1)
                    o = tail
                html += htmltext('</span>]')
            elif isinstance(o, dict):
                html += htmltext('{<span class="payload-preview--obj">')
                for i, (k, v) in enumerate(o.items()):
                    html += htmltext('<span class="payload-preview--key">"%s"</span>: ' % k)
                    html = format_payload(v, html=html, last_element=i == len(o) - 1)
                html += htmltext('</span>}')
            else:
                # check if it's empty string, a template with text around or just text
                if not o or re.sub('^({[{|%]).+([%|}]})$', '', o):
                    # and add double quotes
                    html += htmltext('<span class="payload-preview--value">"%s"</span>' % o)
                else:
                    html += htmltext('<span class="payload-preview--template-value">%s</span>' % o)
            # last element doesn't need separator
            if not last_element:
                html += htmltext('<span class="payload-preview--item-separator">,</span>')
            return html

        payload = parse_payload()
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Payload structure preview')
        r += htmltext('<div class="payload-preview">')
        try:
            unflattened_payload = unflatten_keys(payload)
            r += htmltext('<div class="payload-preview--structure">')
            r += format_payload(unflattened_payload)
            r += htmltext('</div>')
        except UnflattenKeysException as e:
            r += htmltext('<div class="errornotice"><p>%s</p><p>%s %s</p></div>') % (
                _('Unable to preview payload.'),
                _('Following error occured: '),
                e,
            )
        r += htmltext('</div>')
        return r.getvalue()

    def logged_errors_recent_count(self):
        get_request().ignore_session = True
        get_response().set_content_type('application/json')
        backoffice_root = get_publisher().get_backoffice_root()
        if not get_request().user or not (
            backoffice_root.is_accessible('forms')
            or backoffice_root.is_accessible('cards')
            or backoffice_root.is_accessible('workflows')
        ):
            raise AccessForbiddenError()
        creation_time = get_session().latest_errors_visit or make_aware(
            datetime.datetime.fromtimestamp(get_session().get_creation_time())
        )
        clauses = LoggedError.get_permission_criterias()
        clauses.append(Greater('latest_occurence_timestamp', creation_time))
        clauses.append(Null('deleted_timestamp'))
        logged_errors_count = LoggedError.count(clauses)
        if not logged_errors_count:
            return json.dumps({'err': 0})
        return json.dumps(
            {
                'msg': str(
                    ngettext(
                        '%s new error has been recorded.',
                        '%s new errors have been recorded.',
                        logged_errors_count,
                    )
                    % logged_errors_count
                ),
                'err': 0,
            }
        )

    def _q_traverse(self, path):
        get_request().is_json_marker = True
        return super()._q_traverse(path)


def reverse_geocoding(request, *args, **kwargs):
    if not ('lat' in request.GET and 'lon' in request.GET):
        raise RequestError(_('Missing lat/lon parameters.'))
    lat = request.GET['lat']
    lon = request.GET['lon']
    return HttpResponse(misc.get_reverse_geocoding_data(lat, lon), content_type='application/json')


def geocoding(request, *args, **kwargs):
    if 'q' not in request.GET:
        raise RequestError(_('Missing q parameter.'))
    q = request.GET['q']
    url = get_publisher().get_geocoding_service_url()
    if '?' in url:
        url += '&'
    else:
        url += '?'
    url += 'format=json&q=%s' % urllib.parse.quote(q.encode('utf-8'))
    url += '&accept-language=%s' % (get_publisher().get_site_language() or 'en')
    return HttpResponse(misc.urlopen(misc.get_variadic_url(url)).read(), content_type='application/json')


def validate_condition(request, *args, **kwargs):
    condition = {}
    condition['type'] = request.GET.get('type') or ''
    condition['value'] = request.GET.get('value_' + condition['type']) or ''
    hint = {'msg': ''}
    try:
        Condition(condition).validate()
    except ValidationError as e:
        hint['msg'] = str(e)
    else:
        if request.GET.get('warn-on-datetime') == 'true' and condition['type'] == 'django':
            variables = re.compile(r'\b(today|now)\b')
            filters = re.compile(r'\|age_in_(years|months|days|hours)')
            if variables.search(condition['value']) or filters.search(condition['value']):
                hint['msg'] = _(
                    'Warning: conditions are only evaluated when entering the action, '
                    'you may need to set a timeout if you want it to be evaluated regularly.'
                )
    return JsonResponse(hint)


class ProvisionAfterJob(AfterJob):
    def __init__(self, json_data, **kwargs):
        super().__init__(**kwargs)
        self.json_data = json_data

    def execute(self):
        from wcs.ctl.management.commands.hobo_notify import Command as CmdHoboNotify

        CmdHoboNotify().process_notification(self.json_data)


def provisionning(request):
    if not is_url_signed():
        raise AccessForbiddenError()

    sync = request.GET.get('sync') == '1'

    if sync:
        from wcs.ctl.management.commands.hobo_notify import Command as CmdHoboNotify

        CmdHoboNotify().process_notification(get_request().json)
    else:
        job = ProvisionAfterJob(json_data=get_request().json)
        job.run(spool=True)
    return JsonResponse({'err': 0})

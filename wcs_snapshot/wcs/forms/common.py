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

import datetime
import json
import os
import time
import urllib.parse

from django.utils.timezone import is_naive, make_aware
from quixote import get_publisher, get_request, get_response, get_session, redirect
from quixote.directory import Directory
from quixote.errors import RequestError
from quixote.html import TemplateIO, htmltag, htmltext
from quixote.http_request import Upload
from quixote.util import randbytes

from wcs import data_sources
from wcs.api_utils import get_query_flag, get_user_from_api_query_string, is_url_signed, sign_url_auto_orig
from wcs.blocks_widgets import BlockSubWidget, BlockWidget
from wcs.clamd import AccessForbiddenMalwareError
from wcs.fields import FileField
from wcs.qommon.admin.texts import TextsDirectory
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.fields import display_fields
from wcs.qommon.upload_storage import get_storage_object
from wcs.sql_criterias import Equal
from wcs.utils import record_timings
from wcs.wf.editable import EditableWorkflowStatusItem
from wcs.workflows import RedisplayFormException, ReplayException

from ..qommon import _, audit, errors, misc, template
from ..utils import add_timing_group, add_timing_mark


class FileDirectory(Directory):
    _q_exports = []
    _lookup_methods = ['lookup_file_field']

    def __init__(self, formdata, reference, thumbnails=False):
        self.formdata = formdata
        self.reference = reference
        self.thumbnails = thumbnails

    def lookup_file_field(self, filename):
        try:
            if '$' in self.reference:
                # path to block field contents
                fn2, idx, sub = self.reference.split('$', 2)
                return self.formdata.data[fn2]['data'][int(idx)][sub]
            return self.formdata.data[self.reference]
        except (KeyError, ValueError):
            return None

    def _q_lookup(self, component):
        if component == 'thumbnail':
            self.thumbnails = True
            return self
        for lookup_method_name in self._lookup_methods:
            lookup_method = getattr(self, lookup_method_name)
            file = lookup_method(filename=component)
            if file:
                break
        else:
            # no such file
            raise errors.TraversalError()

        if component and component not in (file.base_filename, urllib.parse.quote(file.base_filename)):
            raise errors.TraversalError()

        if not hasattr(file, 'has_redirect_url'):
            # not an appropriate file object
            raise errors.TraversalError()

        if file.has_redirect_url():
            redirect_url = file.get_redirect_url(backoffice=get_request().is_in_backoffice())
            if not redirect_url:
                raise errors.TraversalError()
            redirect_url = sign_url_auto_orig(redirect_url)
            audit('redirect remote stored file', obj=self.formdata, extra_label=component)
            return redirect(redirect_url)

        if not file.allow_download(formdata=self.formdata):
            raise AccessForbiddenMalwareError(file)

        if not self.thumbnails:
            # do not log access to thumbnails as they will already be accounted for as
            # a view of the formdata/carddata containing them.
            audit('download file', obj=self.formdata, extra_label=component)

        return self.serve_file(file, thumbnail=self.thumbnails)

    @classmethod
    def serve_file(cls, file, thumbnail=False):
        response = get_response()

        if misc.is_svg_filetype(file.content_type) and thumbnail:
            thumbnail = False

        if thumbnail:
            if file.can_thumbnail():
                if file.content_type:
                    try:
                        content = misc.get_thumbnail(file.get_fs_filename(), content_type=file.content_type)
                        response.set_content_type('image/png')
                        return content
                    except misc.ThumbnailError:
                        raise errors.TraversalError()
            else:
                raise errors.TraversalError()

        # force potential HTML upload to be used as-is (not decorated with theme)
        # and with minimal permissions
        response.raw = True
        response.set_header(
            'Content-Security-Policy',
            'default-src \'none\'; img-src %s;' % get_request().build_absolute_uri(),
        )

        if file.content_type:
            response.set_content_type(file.content_type)
        else:
            response.set_content_type('application/octet-stream')
        if file.charset:
            response.set_charset(file.charset)
        if file.base_filename:
            # remove invalid characters from filename
            filename = file.base_filename.translate(str.maketrans({x: '_' for x in '"\n\r'}))
            content_disposition = 'attachment'
            if file.content_type.startswith('image/') and not file.content_type.startswith('image/svg'):
                content_disposition = 'inline'
            elif file.content_type == 'application/pdf':
                content_disposition = 'inline'
            response.set_header('content-disposition', '%s; filename="%s"' % (content_disposition, filename))

        return file.get_content()


class FilesDirectory(Directory):
    def __init__(self, formdata):
        self.formdata = formdata

    def _q_lookup(self, component):
        return FileDirectory(self.formdata, reference=component)


class FormTemplateMixin:
    def get_formdef_template_variants(self, template_names):
        template_part_names = [(os.path.dirname(x), os.path.basename(x)) for x in template_names]
        for dirname, basename in template_part_names:
            for keyword in self.formdef.appearance_keywords_list:
                yield os.path.join(dirname, 'appearance-' + keyword, basename)
            if self.formdef.category_id:
                yield os.path.join(dirname, 'category-' + self.formdef.category.url_name, basename)
            yield os.path.join(dirname, basename)


class FormStatusPage(Directory, FormTemplateMixin):
    _q_exports_orig = [
        '',
        'download',
        'json',
        'action',
        'live',
        'tempfile',
        'tsupdate',
        ('check-workflow-progress', 'check_workflow_progress'),
        'scan',
    ]
    _q_extra_exports = []
    form_page_class = None

    do_not_call_in_templates = True
    summary_templates = ['wcs/formdata_summary.html']
    history_templates = ['wcs/formdata_history.html']
    status_templates = ['wcs/formdata_status.html']

    replay_detailed_message = _(
        'Another action has been performed on this form in the meantime and data may have been changed.'
    )

    def __init__(self, formdef, filled, register_workflow_subdirs=True, custom_view=None, parent_view=None):
        get_publisher().substitutions.feed(filled)
        self.formdef = formdef
        self.formdata = filled
        self.filled = filled
        self.custom_view = custom_view
        self.parent_view = parent_view
        self._q_exports = self._q_exports_orig[:]
        for q in self._q_extra_exports:
            if q not in self._q_exports:
                self._q_exports.append(q)

        if self.formdata and self.formdef.workflow and register_workflow_subdirs:
            for name, directory in self.formdef.workflow.get_subdirectories(self.filled):
                self._q_exports.append(name)
                setattr(self, name, directory)

    def check_auth(self, api_call=False):
        if api_call:
            user = get_user_from_api_query_string() or get_request().user
            if get_request().has_anonymised_data_api_restriction() and (not user or not user.is_api_user):
                if is_url_signed() or (get_request().user and get_request().user.is_admin):
                    return None
                raise errors.AccessUnauthorizedError()
        else:
            user = get_request().user

        mine = self.filled.is_submitter(user)

        self.check_receiver()
        return mine

    def json(self):
        self.check_auth(api_call=True)
        anonymise = get_request().has_anonymised_data_api_restriction()

        if self.custom_view:
            # call to management view to get list of possible ids,
            # and check this one is allowed
            from wcs.backoffice.management import FormDefUI, FormPage

            listing_page = FormPage(formdef=self.formdef, view=self.custom_view)
            selected_filter = listing_page.get_filter_from_query(default='all')
            selected_filter_operator = listing_page.get_filter_operator_from_query()
            criterias = listing_page.get_criterias_from_query()
            criterias.append(Equal('id', self.filled.id))
            user = get_user_from_api_query_string() or get_request().user if not anonymise else None
            item_ids = FormDefUI(self.formdef).get_listing_item_ids(
                selected_filter, selected_filter_operator, criterias=criterias, user=user
            )
            if str(self.filled.id) not in [str(x) for x in item_ids]:
                raise errors.TraversalError(_('ID not available in filtered view'))

        values_at = get_request().form.get('at')
        if values_at:
            try:
                values_at = datetime.datetime.fromisoformat(values_at)
                if is_naive(values_at):
                    values_at = make_aware(values_at)
            except ValueError:
                raise RequestError(_('Invalid value "%s" for "at".') % values_at)
        return self.export_to_json(
            anonymise=anonymise,
            include_evolution=get_query_flag('include-evolution', default=True),
            include_files=get_query_flag('include-files-content', default=True),
            include_roles=get_query_flag('include-roles', default=True),
            include_submission=get_query_flag('include-submission', default=True),
            include_fields=get_query_flag('include-fields', default=True),
            include_user=get_query_flag('include-user', default=True),
            include_unnamed_fields=False,
            include_workflow=get_query_flag('include-workflow', default=True),
            include_workflow_data=get_query_flag('include-workflow-data', default=True),
            include_actions=get_query_flag('include-actions', default=False),
            values_at=values_at,
        )

    def tsupdate(self):
        # return new timestamp value used for replay protection
        get_request().ignore_session = True
        get_response().set_content_type('application/json')
        return json.dumps({'ts': str(self.filled.last_update_time.timestamp())})

    def check_workflow_progress(self):
        self.check_auth()
        get_request().ignore_session = True
        get_response().set_content_type('application/json')
        response = {'err': 0}
        if self.filled.workflow_processing_timestamp:
            response['status'] = 'processing'
        else:
            response['status'] = 'idle'
        if get_request().form.get('job'):
            try:
                afterjob = AfterJob.get(get_request().form.get('job'))
            except KeyError:
                pass
            else:
                response['job'] = {'status': afterjob.status, 'url': afterjob.get_api_status_url()}
                response['url'] = afterjob.url
        return json.dumps(response)

    def scan(self):
        get_request().ignore_session = True
        self.check_auth()
        get_response().set_content_type('application/json')
        return json.dumps(
            {
                'err': 0,
                'data': [
                    file_data.clamd_json()
                    for file_data in self.formdata.get_all_file_data(with_history=False)
                ],
            }
        )

    def tempfile(self):
        # allow for file uploaded via a file widget in a workflow form
        # to be downloaded back from widget
        return self.parent_view.tempfile()

    def workflow_messages(self, position='top'):
        if self.formdef.workflow:
            workflow_messages = self.filled.get_workflow_messages(position=position, user=get_request().user)
            if workflow_messages:
                r = TemplateIO(html=True)
                if position == 'top':
                    r += htmltext('<div id="receipt-intro" class="workflow-messages %s">' % position)
                else:
                    r += htmltext('<div class="workflow-messages %s">' % position)
                for workflow_message in workflow_messages:
                    r += htmltext(workflow_message)
                r += htmltext('</div>')
                return r.getvalue()
        return ''

    def actions_workflow_messages(self):
        return self.workflow_messages(position='actions')

    def bottom_workflow_messages(self):
        return self.workflow_messages(position='bottom')

    def recorded_message(self):
        r = TemplateIO(html=True)
        # behaviour if workflow doesn't display any message
        if self.filled.receipt_time is not None:
            tm = misc.localstrftime(self.filled.receipt_time)
        else:
            tm = '???'

        if self.formdef.only_allow_one:
            r += TextsDirectory.get_html_text('form-recorded-allow-one', vars={'date': tm})
        else:
            r += TextsDirectory.get_html_text(
                'form-recorded', vars={'date': tm, 'number': self.filled.get_display_id()}
            )

        return r.getvalue()

    def get_handling_role_info_text(self):
        handling_role = self.filled.get_handling_role()
        if not (handling_role and handling_role.details):
            return ''
        r = TemplateIO(html=True)
        endpoint_status = self.formdef.workflow.get_endpoint_status()
        r += htmltext('<p>')
        if self.filled.status in ['wf-%s' % x.id for x in endpoint_status]:
            r += str(_('Your case has been handled by:'))
        else:
            r += str(_('Your case is handled by:'))
        r += htmltext('</p>')
        r += htmltext('<p id="receiver">')
        r += htmltext(handling_role.details.replace('\n', '<br />'))
        r += htmltext('</p>')
        return r.getvalue()

    def _q_index(self):
        if (
            self.formdef.category_id
            and self.parent_view
            and self.parent_view.ensure_parent_category_in_url
            and get_request().get_method() == 'GET'
            and not self.parent_view.parent_category
        ):
            url = self.filled.get_url(include_category=True)
            if get_request().get_query():
                url += '?' + get_request().get_query()
            return redirect(url)

        if self.filled.is_draft():
            return self.restore_draft()

        mine = self.check_auth()
        if not mine and not get_request().is_in_backoffice():
            # Access authorized but the form doesn't belong to the user; if the
            # user has access to the backoffice, redirect.
            # Unless ?debug=whatever is set.
            if get_request().user.can_go_in_backoffice() and not get_request().form.get('debug'):
                return redirect(self.filled.get_url(backoffice=True))

        get_request().view_name = 'status'

        user = get_request().user
        form = self.get_workflow_form(user)
        try:
            response = self.check_submitted_form(form)
        except RedisplayFormException:
            pass
        else:
            if response:
                return response

        if form:
            form.add_media()

        get_response().add_javascript(['jquery.js', 'qommon.forms.js', 'qommon.map.js', 'popup.js'])
        get_response().set_title(get_publisher().translate(self.formdef.name))
        get_response().filter['page_title'] = self.filled.get_display_label()
        context = {
            'view': self,
            'mine': mine,
            'formdata': self.filled,
            'workflow_form': form,
        }

        return template.QommonTemplateResponse(
            templates=list(self.get_formdef_template_variants(self.status_templates)), context=context
        )

    def get_restore_draft_magictoken(self):
        # restore draft into session
        session = get_session()
        filled = self.filled
        if not (get_request().is_in_backoffice() and filled.backoffice_submission):
            if not self.filled.is_submitter(get_request().user):
                raise errors.AccessUnauthorizedError()

        magictoken = randbytes(8)
        filled.feed_session()
        form_data = filled.data
        for field in filled.formdef.fields:
            if field.id not in form_data:
                continue
            if form_data[field.id] is None:
                # remove keys that were not set, this is required when we restore a
                # draft from SQL (where all columns are always defined).
                del form_data[field.id]
                continue
            if field.key == 'file' and isinstance(form_data[field.id], Upload):
                # add back file to session
                tempfile = session.add_tempfile(form_data[field.id], storage=field.storage)
                form_data[field.id].token = tempfile['token']
        form_data['prefilling_data'] = filled.prefilling_data or {}
        form_data['is_recalled_draft'] = True
        form_data['draft_formdata_id'] = filled.id
        form_data['page_no'] = filled.page_no
        session.add_magictoken(magictoken, form_data)

        # restore computed fields data
        computed_data = {}
        for field in self.formdef.fields:
            if field.key != 'computed':
                continue
            if field.id in form_data:
                computed_data[field.id] = form_data[field.id]
        if computed_data:
            session.add_magictoken('%s-computed' % magictoken, computed_data)
        return magictoken

    def restore_draft(self):
        # redirect to draft
        magictoken = self.get_restore_draft_magictoken()
        return redirect('../?mt=%s' % magictoken)

    def get_workflow_form(self, user):
        submitted_fields = []
        form = self.filled.get_workflow_form(user, displayed_fields=submitted_fields)
        if form and form.is_submitted():
            with get_publisher().substitutions.temporary_feed(self.filled, force_mode='lazy'):
                # remove fields that could be required but are not visible
                self.filled.evaluate_live_workflow_form(user, form)
                get_publisher().substitutions.invalidate_cache()
                get_publisher().substitutions.feed(self.filled)
                # recreate form to get live data source items
                form = self.filled.get_workflow_form(user, displayed_fields=submitted_fields)
                if form:
                    for field in submitted_fields:
                        if (
                            not field.is_visible(self.filled.data, self.formdef)
                            and 'f%s' % field.id in form._names
                        ):
                            del form._names['f%s' % field.id]

        if form:
            form.attrs['data-live-url'] = self.filled.get_url() + 'live'
            form.attrs['data-js-features'] = 'true'
        return form

    def check_submitted_form(self, form):
        if form and form.is_submitted():
            submit_button_name = form.get_submit()
            if submit_button_name:
                submit_button = form.get_widget(submit_button_name)
                if getattr(submit_button, 'ignore_form_errors', False):
                    form.clear_errors()
            if form.has_errors():
                return
            url = self.submit(form)
            if url is None:
                url = get_request().get_frontoffice_url()
                status = self.filled.get_status()
                top_alert = False
                for item in status.items or []:
                    if (
                        item.key == 'displaymsg'
                        and item.position == 'top'
                        and self.filled.is_for_current_user(item.to)
                    ):
                        top_alert = True
                        break
                if top_alert or get_session().message:
                    # prevent an existing anchor client side to take effect
                    url += '#'
                else:
                    url += '#action-zone'
            response = get_response()
            response.set_status(303)
            response.headers['location'] = url
            response.content_type = 'text/plain'
            return 'Your browser should redirect you'

    def export_to_json(
        self,
        *,
        anonymise=False,
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
        get_response().set_content_type('application/json')
        user = get_user_from_api_query_string() or get_request().user
        return self.filled.export_to_json(
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

    def history(self):
        if not self.filled.evolution:
            return
        if not self.formdef.is_user_allowed_read_status_and_history(get_request().user, self.filled):
            return

        include_authors_in_form_history = (
            get_publisher().get_site_option('include_authors_in_form_history', 'variables') != 'False'
        )
        include_authors = get_request().is_in_backoffice() or include_authors_in_form_history
        return template.render(
            list(self.get_formdef_template_variants(self.history_templates)),
            {
                'formdata': self.filled,
                'include_authors': include_authors,
                'view': self,
            },
        )

    def check_receiver(self):
        user = get_request().user
        if not user:
            if not self.filled.formdef.is_user_allowed_read(None, self.filled):
                raise errors.AccessUnauthorizedError()
        if self.filled.formdef is None:
            raise errors.AccessForbiddenError()
        if not self.filled.formdef.is_user_allowed_read(user, self.filled):
            raise errors.AccessForbiddenError()
        return user

    def should_fold_summary(self, mine, request_user):
        # fold the summary if the form has already been seen by the user, i.e.
        # if it's user own form or if the user is present in the formdata log
        # (evolution).
        if mine or (request_user and self.filled.is_submitter(request_user)):
            return True
        if request_user and self.filled.evolution:
            for evo in self.filled.evolution:
                if str(evo.who) == str(request_user.id) or (
                    evo.who == '_submitter' and self.filled.is_submitter(request_user)
                ):
                    return True
        return False

    def should_fold_history(self):
        return bool(self.formdef.history_pane_default_mode == 'collapsed')

    def receipt(self, always_include_user=False, form_url='', mine=True):
        request_user = user = get_request().user
        if not always_include_user and get_request().user and get_request().user.id == self.filled.user_id:
            user = None
        else:
            try:
                user = get_publisher().user_class.get(self.filled.user_id)
            except KeyError:
                user = None

        return template.render(
            list(self.get_formdef_template_variants(self.summary_templates)),
            {
                'formdata': self.filled,
                'should_fold_summary': self.should_fold_summary(mine, request_user),
                'fields': self.display_fields(form_url=form_url),
                'view': self,
                'user': user,
                'section_title': _('Summary'),
                'section_id': 'sect-dataview',
                'div_id': 'summary',
                'enable_compact_dataview': get_publisher().has_site_option('enable-compact-dataview'),
            },
        )

    def display_fields(self, fields=None, form_url='', include_unset_required_fields=False):
        return display_fields(
            self.filled,
            fields=fields,
            form_url=form_url,
            include_unset_required_fields=include_unset_required_fields,
        )

    def backoffice_fields_section(self):
        backoffice_fields = self.formdef.workflow.get_backoffice_fields()
        if not backoffice_fields:
            return
        content = self.display_fields(backoffice_fields, include_unset_required_fields=True)
        if not content:
            return
        return template.render(
            ['wcs/backoffice/backoffice_fields_section.html'],
            {
                'section_title': _('Backoffice Data'),
                'section_id': 'sect-backoffice-data',
                'should_fold_summary': False,
                'fields': content,
                'enable_compact_dataview': get_publisher().has_site_option('enable-compact-dataview'),
            },
        )

    def status(self):
        if get_request().get_query() == 'unlock':
            # mark user as active visitor of the object, then redirect to self,
            # the unlocked form will appear.
            get_session().mark_visited_object(self.filled)
            return redirect('./#lock-notice')

        user = self.check_receiver()
        form = self.get_workflow_form(user)
        try:
            response = self.check_submitted_form(form)
        except RedisplayFormException:
            pass
        else:
            if response:
                get_session().unmark_visited_object(self.filled)
                return response

        get_response().add_javascript(['jquery.js', 'qommon.forms.js'])
        audit('view', obj=self.filled)
        get_response().set_title(self.filled.get_display_name())
        r = TemplateIO(html=True)
        attrs = {}
        if self.filled.workflow_processing_timestamp:
            attrs['data-workflow-processing'] = 'true'
        if self.filled.workflow_processing_afterjob_id:
            attrs['data-workflow-processing-afterjob-id'] = self.filled.workflow_processing_afterjob_id
        r += htmltag('div', id='formdata-page', **attrs)

        r += get_session().display_message()
        r += htmltext(self.workflow_messages())
        r += self.receipt(always_include_user=True, mine=False)
        r += self.backoffice_fields_section()

        r += self.history()

        bottom_workflow_messages = self.bottom_workflow_messages()
        if bottom_workflow_messages or form:
            r += htmltext('<span id="action-zone"></span>')

        r += htmltext(bottom_workflow_messages)

        if self.filled.workflow_processing_timestamp:
            form = None
            locked = True
            r += htmltext('<div class="busy-processing"><div class="loader"></div>')
            r += htmltext('<p>%s</p></div>') % _('Processing...')

        locked = False
        if form:
            all_visitors = get_session().get_object_visitors(self.filled)
            visitors = [x for x in all_visitors if x[0] != get_session().user]
            if visitors:
                current_timestamp = time.time()
                visitor_users = []
                for visitor_id, visitor_timestamp in visitors:
                    try:
                        visitor_name = get_publisher().user_class.get(visitor_id).display_name
                    except KeyError:
                        continue
                    minutes_ago = int((current_timestamp - visitor_timestamp) / 60)
                    if minutes_ago < 1:
                        time_ago = _('less than a minute ago')
                    else:
                        time_ago = _('less than %s minutes ago') % (minutes_ago + 1)
                    visitor_users.append('%s (%s)' % (visitor_name, time_ago))
                if visitor_users:
                    r += htmltext('<div id="lock-notice" class="infonotice"><p>')
                    r += str(
                        _('Be warned forms of this user are also being looked at by: %s.')
                        % ', '.join(visitor_users)
                    )
                    r += ' '
                    r += htmltext('</p>')
                    me_in_visitors = bool(get_session().user in [x[0] for x in all_visitors])
                    if not me_in_visitors:
                        locked = True
                        r += htmltext('<p class="action"><a href="?unlock">%s</a></p>') % _(
                            '(unlock actions)'
                        )
                    r += htmltext('</div>')
            if not locked:
                r += htmltext(self.actions_workflow_messages())
                if form.widgets:
                    r += htmltext('<div class="section"><div>')
                r += form.render()
                if form.widgets:
                    r += htmltext('</div></div>')
                get_session().mark_visited_object(self.filled)

        if not locked:
            if (self.filled.get_status() and self.filled.get_status().backoffice_info_text) or (
                form
                and any(getattr(button, 'backoffice_info_text', None) for button in form.get_submit_widgets())
            ):
                r += htmltext('<div class="backoffice-description bo-block">')
                if self.filled.get_status().backoffice_info_text:
                    r += htmltext(self.filled.get_status().backoffice_info_text)
                if form:
                    for button in form.get_submit_widgets():
                        if not getattr(button, 'backoffice_info_text', None):
                            continue
                        r += htmltext('<div class="action-info-text" data-button-name="%s">' % button.name)
                        r += htmltext(button.backoffice_info_text)
                        r += htmltext('</div>')
                r += htmltext('</div>')

        r += htmltext('</div>')  # formdata-page
        r += htmltext('<div id="formdata-bottom-links">')
        r += self.get_bottom_links()
        r += htmltext('</div>')
        return r.getvalue()

    def get_bottom_links(self):
        if get_request().form.get('origin') == 'global':
            return htmltext('<a href="/backoffice/management/listing">%s</a>') % _('Go back to listing')
        return htmltext('<a href="..">%s</a>') % _('Go back to listing')

    def submit(self, form):
        user = get_request().user
        next_url = None
        if self.filled.workflow_processing_timestamp:
            raise RedisplayFormException(
                form=form,
                error={
                    'summary': _('Error: actions are currently running.'),
                    'details': self.replay_detailed_message,
                },
            )

        try:
            next_url = self.filled.handle_workflow_form(user, form)
        except ReplayException:
            widget = form.get_widget('_ts')
            # update with new timestamp as the page is refreshed
            widget.set_value(str(self.filled.last_update_time.timestamp()))
            raise RedisplayFormException(
                form=form,
                error={
                    'summary': _('Error: parallel execution.'),
                    'details': self.replay_detailed_message,
                },
            )

        if isinstance(next_url, AfterJob):
            return

        if next_url:
            return next_url
        if form.has_errors():
            return
        try:
            self.check_auth()
        except errors.AccessError:
            # the user no longer has access to the form; redirect to a
            # different page
            if 'backoffice/' in [x[0] for x in get_response().breadcrumb]:
                user = get_request().user
                if user and (user.is_admin or self.formdef.is_of_concern_for_user(user)):
                    # user has access to the formdef, redirect to the
                    # listing.
                    return '..'
                return get_publisher().get_backoffice_url()
            return get_publisher().get_root_url()

    def download(self):
        if not is_url_signed():
            self.check_receiver()
        file = None
        if get_request().form and get_request().form.get('hash'):
            # look in all known formdata files for file with given hash
            file_digest = get_request().form.get('hash')
            for field_data in self.filled.get_all_file_data(with_history=True):
                if not hasattr(field_data, 'file_digest'):
                    continue
                if field_data.file_digest() == file_digest:
                    thumbnail = bool(get_request().form.get('thumbnail') and field_data.can_thumbnail())
                    if not field_data.allow_download(formdata=self.filled):
                        raise AccessForbiddenMalwareError(field_data)
                    if not thumbnail:
                        # do not log access to thumbnails as they will already be accounted for as
                        # a view of the formdata/carddata containing them.
                        audit(
                            'download file',
                            obj=self.filled,
                            extra_label=str(field_data),
                            file_digest=file_digest,
                        )
                    return FileDirectory.serve_file(field_data, thumbnail=thumbnail)
        elif get_request().form and get_request().form.get('f'):
            try:
                fn = get_request().form['f']
                if '$' in fn:
                    # path to block field contents
                    fn2, idx, sub = fn.split('$', 2)
                    file = self.filled.data[fn2]['data'][int(idx)][sub]
                else:
                    file = self.filled.data[fn]
            except (KeyError, ValueError):
                pass

        if not hasattr(file, 'content_type'):
            raise errors.TraversalError()

        if file.has_redirect_url():
            redirect_url = file.get_redirect_url(backoffice=get_request().is_in_backoffice())
            if not redirect_url:
                raise errors.TraversalError()
            redirect_url = sign_url_auto_orig(redirect_url)
            audit('redirect remote stored file', obj=self.filled)
            return redirect(redirect_url)

        file_url = 'files/%s/' % fn

        if get_request().form.get('thumbnail') == '1':
            if file.can_thumbnail():
                file_url += 'thumbnail/'
            else:
                raise errors.TraversalError()

        if is_url_signed():
            # serve file directly, no redirect to URL with path ending with filename
            file_directory = FileDirectory(
                self.filled,
                reference=fn,
                thumbnails=bool(get_request().form.get('thumbnail') and file.can_thumbnail()),
            )
            return file_directory._q_lookup(component=None)

        if getattr(file, 'base_filename'):
            file_url += urllib.parse.quote(file.base_filename)

        return redirect(file_url)

    @classmethod
    @add_timing_group('live_process_fields')
    def live_process_fields(cls, form, formdata, displayed_fields):
        if form is None:
            return json.dumps({'result': {}})

        result = {}

        # create a dict with modified field id as keys and a split version to match blocks
        # as they pass this as modified_field_id (qommon.forms.js):
        # `${data.modified_block} ${data.modified_field} ${data.modified_block_row}`
        modified_field_ids = {x: x.split() for x in get_request().form.get('modified_field_id[]') or []}

        modified_field_varnames = set()
        if 'init' in modified_field_ids:
            # when page is initialized, <select> will get their first option
            # automatically selected, so mark them all as modified.
            for field in displayed_fields:
                if field.key == 'item' and field.display_mode == 'list' and field.varname:
                    modified_field_varnames.add(field.varname)
        if 'user' in modified_field_ids:
            if get_request().is_in_frontoffice():
                # not allowed in frontoffice.
                raise errors.AccessForbiddenError()
            # user selection in sidebar
            formdata.user_id = get_request().form.get('user_id')
            modified_field_varnames.add('__user__')
        for field in displayed_fields:
            if field.id in modified_field_ids:
                if field.varname:
                    modified_field_varnames.add(field.varname)
                if field.key == 'block':
                    # if block was modified the list of modified field ids will also contain
                    # entries for the field in the block that was modified. (in a typical case
                    # there will be a single one but as there is some delay before calling to
                    # /live there may be multiple ones)
                    for modified_field_id_parts in modified_field_ids.values():
                        if modified_field_id_parts[0] == field.id and len(modified_field_id_parts) > 1:
                            dummy, row_field_id, row_field_no = modified_field_id_parts
                            sub_field = [x for x in field.block.fields if x.id == row_field_id][0]
                            modified_field_varnames.add(f'{field.id} {row_field_no} {sub_field.varname}')
                break

        def get_all_field_widgets(form):
            for widget in form.widgets:
                if not getattr(widget, 'field', None):
                    continue
                yield (None, None, widget.field, widget)
                if isinstance(widget, BlockWidget):
                    block_row = 0
                    for subwidget in widget.widgets:
                        if isinstance(subwidget, BlockSubWidget):
                            for field_widget in subwidget.widgets:
                                yield (widget.field, block_row, field_widget.field, field_widget)
                            block_row += 1

        # get dictionary with blocks data, from workflow form, or defaults to formdata
        blocks_formdata_data = getattr(form, 'blocks_formdata_data', formdata.data)
        for block, block_row, field, widget in get_all_field_widgets(form):
            t0 = time.time()
            if block:
                entry = {
                    'block_id': block.id,
                    'field_id': field.id,
                    'block_row': f'element{block_row}',
                    'row': block_row,
                }
                current_entry_key = f'{block.id}-{field.id}-{block_row}'
                try:
                    block_data = blocks_formdata_data.get(block.id)['data'][block_row]
                except (IndexError, TypeError):
                    block_data = {}
            else:
                entry = {}
                current_entry_key = field.id

            result[current_entry_key] = entry

            # visibility
            if block:
                with block.block.evaluation_context(block_data, block_row):
                    entry['visible'] = field.is_visible({}, formdef=None)
            else:
                entry['visible'] = field.is_visible(formdata.data, formdata.formdef)

            # item/items options
            if field.key in ('item', 'items', 'time-range') and field.data_source:
                data_source = data_sources.get_object(field.data_source)
                if data_source.type in ('json', 'jsonvalue', 'geojson') or data_source.type.startswith(
                    'carddef:'
                ):
                    if block:
                        varnames = [
                            f'{block.id} element{block_row} {x}'
                            for x in data_source.get_referenced_varnames(block.block)
                        ]
                    else:
                        varnames = data_source.get_referenced_varnames(field.formdef)
                    if (not modified_field_varnames or modified_field_varnames.intersection(varnames)) and (
                        field.display_mode == 'autocomplete'
                        and data_source.can_jsonp()
                        and field.key != 'items'
                    ):
                        # computed earlier, in perform_more_widget_changes, when the field
                        # was added to the form
                        entry['source_url'] = field.url
                    elif modified_field_varnames.intersection(varnames):
                        rel_t0 = time.time()
                        if block:
                            with block.block.evaluation_context(block_data, block_row):
                                entry['items'] = field.get_extended_options()
                        elif field.key == 'time-range':
                            entry['items'] = widget.get_day_options_json()
                        else:
                            entry['items'] = field.get_extended_options()
                        if field.display_mode == 'timetable':
                            # timetables require additional attributes
                            # but reduce payload weight by removing the API URLs
                            for options in entry['items']:
                                options.pop('api', None)
                        add_timing_mark(f'item-options-{field.id}', relative_start=rel_t0)

            # value (prefill or comment content)
            if field.key == 'comment':
                if block:
                    with block.block.evaluation_context(block_data, block_row):
                        widget.content = widget.field.get_text()
                entry['content'] = widget.content
            elif field.key == 'block':
                # do not apply live updates to prefilled blocks as this would imply too
                # much javascript (for example new block rows may have to be created).
                pass
            elif field.get_prefill_configuration().get('type') == 'string':
                if 'request.GET' in (field.get_prefill_configuration().get('value') or ''):
                    # Prefilling with a value from request.GET cannot be compatible with
                    # live updates of prefill values. Skip those. (a "computed data" field
                    # should be used as replacement).
                    if field.id in result:
                        del result[field.id]
                    continue
                if block:
                    update_prefill_key = f'{block.id}-{field.id}-element{block_row}'
                else:
                    update_prefill_key = field.id
                update_prefill = bool('prefilled_%s' % update_prefill_key in get_request().form)
                locked = False
                if update_prefill:
                    if block:
                        with block.block.evaluation_context(block_data, block_row):
                            value, locked = field.get_prefill_value()
                    else:
                        value, locked = field.get_prefill_value()
                    if field.key == 'bool':
                        value = field.convert_value_from_str(value)
                    elif field.key == 'date' and value:
                        try:
                            value = field.convert_value_from_anything(value)
                            text_content = field.convert_value_to_str(value)
                            # convert date to Y-m-d as expected by the <input type=date> field
                            value = field.get_json_value(value)
                        except ValueError:
                            text_content = None
                        entry['text_content'] = text_content
                    elif field.key == 'item' and value:
                        id_value = field.get_id_by_option_text(value)
                        if id_value:
                            value = id_value
                        if field.display_mode == 'autocomplete':
                            entry['display_value'] = field.get_display_value(value)
                    elif field.key == 'file' and value:
                        file_storage = field.storage
                        try:
                            file_object = FileField.convert_value_from_anything(value)
                        except ValueError:
                            file_object = None
                            value = None
                        if get_storage_object(file_storage).has_redirect_url(None):
                            # do not return anything if the file is not locally stored.
                            value = None
                        elif file_object:
                            tempfile = get_session().add_tempfile(file_object, file_storage)
                            value = {
                                'name': tempfile.get('base_filename'),
                                'type': tempfile.get('content_type'),
                                'size': tempfile.get('size'),
                                'token': tempfile.get('token'),
                                'url': 'tempfile?t=%s' % tempfile.get('token'),
                            }
                    entry['content'] = value
                    entry['locked'] = locked
            elif field.get_prefill_configuration().get('type') == 'user':
                if 'user' in modified_field_ids:
                    value, locked = field.get_prefill_value(user=formdata.user)
                    entry['content'] = value
                    entry['locked'] = locked
            timing_name = _('field "%(label)s" (%(identifier)s)') % {
                'label': field.get_type_label(),
                'identifier': (
                    f'field-block-{block.id}--{field.id}-row-{block_row}' if block else f'field-{field.id}'
                ),
            }
            add_timing_mark(timing_name, relative_start=t0)

        return json.dumps({'result': result})

    @record_timings(name='/live call', record_if_over=5)
    def live(self):
        get_request().ignore_session = True
        # live evaluation of fields
        get_response().set_content_type('application/json')

        def result_error(reason):
            return json.dumps({'result': 'error', 'reason': reason})

        session = get_session()
        if not session:
            return result_error('missing session')

        displayed_fields = []
        user = get_request().user
        add_timing_mark('live get_workflow_form')
        form = self.filled.get_workflow_form(user, displayed_fields=displayed_fields)
        if form is None:
            return result_error('no more form')

        add_timing_mark('live evaluate_live_workflow_form')
        with get_publisher().keep_all_block_rows():
            self.filled.evaluate_live_workflow_form(user, form)
        get_publisher().substitutions.unfeed(lambda x: x is self.filled)
        get_publisher().substitutions.feed(self.filled)
        # reevaluate workflow form according to possible new content
        displayed_fields = []
        add_timing_mark('live second get_workflow_form')
        form = self.filled.get_workflow_form(user, displayed_fields=displayed_fields)
        return self.live_process_fields(form, self.filled, displayed_fields)

    def _q_lookup(self, component):
        if component == 'files':
            self.check_receiver()
            return FilesDirectory(self.filled)
        if component.startswith('wfedit-'):
            return self.wfedit(component[len('wfedit-') :])
        return Directory._q_lookup(self, component)

    def _q_traverse(self, path):
        get_response().breadcrumb.append((self.filled.identifier + '/', self.filled.get_display_id()))
        return super()._q_traverse(path)

    def wfedit(self, action_id):
        wf_status = self.filled.get_status()
        for item in wf_status.items:
            if item.id != action_id:
                continue
            if not isinstance(item, EditableWorkflowStatusItem):
                break
            if not item.check_auth(self.filled, get_request().user):
                break
            f = self.form_page_class(self.formdef.url_name)
            f.edit_mode = True
            f.edited_data = self.filled
            f.edit_action = item
            f.edit_mode_cancel_url = get_request().form.pop('cancelurl', None) or f.edit_mode_cancel_url
            f.edit_mode_return_url = get_request().form.pop('ReturnURL', None) or f.edit_mode_return_url
            f.action_url = 'wfedit-%s' % item.id
            if get_request().is_in_backoffice():
                get_session().mark_visited_object(self.filled)
            get_response().breadcrumb = get_response().breadcrumb[:-1]
            get_response().breadcrumb.append((f.action_url, _('Edit')))
            return f._q_index()

        raise errors.AccessForbiddenError()


class TempfileDirectoryMixin:
    user = None

    def check_access(self):
        pass

    def tempfile(self):
        get_request().ignore_session = True
        self.check_access()
        if self.user and not self.user.id == get_session().user:
            self.check_receiver()
        try:
            t = get_request().form['t']
            tempfile = get_session().get_tempfile(t)
        except KeyError:
            raise errors.TraversalError()
        if tempfile is None:
            raise errors.TraversalError()
        response = get_response()

        # force potential HTML upload to be used as-is (not decorated with theme)
        # and with minimal permissions
        response.raw = True
        response.set_header(
            'Content-Security-Policy',
            'default-src \'none\'; img-src %s;' % get_request().build_absolute_uri(),
        )

        if tempfile['content_type'] and not tempfile['content_type'].startswith('image/'):
            response.set_header('content-disposition', 'attachment')
        if tempfile['content_type']:
            response.set_content_type(tempfile['content_type'])
        else:
            response.set_content_type('application/octet-stream')
        if tempfile['charset']:
            response.set_charset(tempfile['charset'])

        if get_request().form.get('thumbnail') == '1' and not misc.is_svg_filetype(tempfile['content_type']):
            try:
                thumbnail = misc.get_thumbnail(
                    get_session().get_tempfile_path(t), content_type=tempfile['content_type']
                )
            except misc.ThumbnailError:
                pass
            else:
                response.set_content_type('image/png')
                return thumbnail
        return get_session().get_tempfile_content(t).get_content()

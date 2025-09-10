# w.c.s. - web application for online forms
# Copyright (C) 2005-2015  Entr'ouvert
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
import urllib.parse

from django.utils.safestring import mark_safe
from django.utils.timezone import localtime, make_aware
from quixote import get_publisher, get_request, get_response, get_session, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext

from wcs.backoffice.pagination import pagination_links
from wcs.categories import Category
from wcs.formdata import FormData
from wcs.formdef import FormDef
from wcs.forms.common import FormStatusPage
from wcs.forms.root import FormPage as PublicFormFillPage
from wcs.forms.root import RequiredUserException
from wcs.sql_criterias import Contains, Equal, Intersects, StrictNotEqual
from wcs.tracking_code import TrackingCode

from ..qommon import _, errors, get_cfg, misc, template
from ..qommon.form import Form, HtmlWidget


class RemoveDraftDirectory(Directory):
    def __init__(self, parent_directory):
        self.parent_directory = parent_directory
        self.formdef = parent_directory.formdef

    def _q_lookup(self, component):
        try:
            formdata = self.formdef.data_class().get(component)
        except KeyError:
            raise errors.TraversalError()
        if not formdata.is_draft():
            raise errors.AccessForbiddenError()
        if not formdata.backoffice_submission:
            raise errors.AccessForbiddenError()

        self.parent_directory.check_access()
        if self.parent_directory.edit_mode:
            raise errors.AccessForbiddenError()

        get_response().set_title(_('Discard'))

        form = Form(enctype='multipart/form-data')
        form.widgets.append(HtmlWidget('<p>%s</p>' % _('You are about to discard this form.')))
        form.add_submit('delete', _('Discard'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('../..')
        if not form.is_submitted() or form.has_errors():
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % (_('Discarding Form'))
            r += form.render()
            return r.getvalue()

        if formdata.tracking_code:
            TrackingCode.remove_object(formdata.tracking_code)
        return_url = '../..'
        if formdata.submission_context:
            if formdata.submission_context.get('return_url'):
                return_url = formdata.submission_context['return_url']
            if formdata.submission_context.get('cancel_url'):
                return_url = formdata.submission_context['cancel_url']

        formdata.remove_self()
        return redirect(return_url)


class SubmissionFormStatusPage(FormStatusPage):
    _q_exports_orig = ['', 'download', 'live']

    def _q_index(self):
        if not self.filled.is_draft():
            get_session().add_message(_('This form has already been submitted.'))
            return redirect(get_publisher().get_backoffice_url() + '/submission/')
        return super()._q_index()

    def restore_draft(self):
        # redirect to draft and keep extra query parameters so {{request.GET}} can be used in form.
        params = {'mt': self.get_restore_draft_magictoken()}
        params.update(get_request().form or {})
        return redirect('../?' + urllib.parse.urlencode(params))


class FormFillPage(PublicFormFillPage):
    _q_exports = [
        '',
        'tempfile',
        'autosave',
        'code',
        ('remove', 'remove_draft'),
        'live',
        ('lateral-block', 'lateral_block'),
        ('go-to-backoffice', 'go_to_backoffice'),
    ]

    filling_templates = ['wcs/backoffice/formdata_filling.html']
    popup_filling_templates = ['wcs/formdata_popup_filling.html']
    validation_templates = ['wcs/backoffice/formdata_validation.html']
    steps_templates = ['wcs/formdata_steps.html']
    has_channel_support = True
    has_user_support = True
    ensure_parent_category_in_url = False
    required_user_message = _('The form must be associated to an user.')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.selected_submission_channel = None
        self.selected_user_id = None
        self.remove_draft = RemoveDraftDirectory(self)

    def get_empty_formdata(self):
        formdata = self.formdef.data_class()()
        formdata.data = {}
        formdata.backoffice_submission = True
        formdata.submission_agent_id = str(get_request().user.id)
        formdata.submission_context = {}
        formdata.status = 'draft'
        formdata.receipt_time = localtime()
        return formdata

    def _q_index(self, *args, **kwargs):
        # if NameID, return URL or submission channel are in query string,
        # create a new draft with these parameters, and redirect to it
        submission_channel = get_request().form.get('channel')
        name_id = get_request().form.get('NameID')
        cancel_url = get_request().form.get('cancelurl')
        return_url = get_request().form.get('ReturnURL')
        caller = get_request().form.get('caller')
        if not self.edit_mode and any((name_id, submission_channel, return_url, cancel_url, caller)):
            formdata = self.get_empty_formdata()
            formdata.submission_context['submission-locked'] = {}
            formdata.submission_channel = submission_channel or ''
            if submission_channel:
                formdata.submission_context['submission-locked']['channel'] = submission_channel
            formdata.submission_agent_id = str(get_request().user.id)
            if name_id:
                users = list(get_publisher().user_class.get_users_with_name_identifier(name_id))
                if users:
                    formdata.user_id = users[0].id
                    formdata.submission_context['submission-locked']['user_id'] = formdata.user_id
                else:
                    get_session().add_message(
                        _('The target user was not found, this form is anonymous.'),
                        level='warning',
                    )
            if return_url:
                formdata.submission_context['return_url'] = return_url
            if cancel_url:
                formdata.submission_context['cancel_url'] = cancel_url
            if submission_channel == 'phone' and caller:
                formdata.submission_context['caller'] = caller
            formdata.store()
            self.set_tracking_code(formdata)
            redirect_url = '%s/' % formdata.id
            extra_query_params = {
                x: y
                for x, y in get_request().form.items()
                if x not in ('channel', 'NameID', 'ReturnURL', 'cancelurl', 'caller')
            }
            if extra_query_params:
                redirect_url += '?' + urllib.parse.urlencode(extra_query_params)
            return redirect(redirect_url)

        self.selected_submission_channel = get_request().form.get('submission_channel')
        self.selected_user_id = get_request().form.get('user_id')
        return super()._q_index(*args, **kwargs)

    def is_missing_user(self):
        return self.formdef.submission_user_association not in ('none', 'any') and not self.selected_user_id

    def page(self, page, *args, **kwargs):
        page_error_messages = kwargs.pop('page_error_messages', None) or []
        if self.is_missing_user() and not kwargs.get('arrival'):
            page_error_messages.append(self.required_user_message)
        return super().page(page, *args, **kwargs, page_error_messages=page_error_messages)

    def validating(self, *args, **kwargs):
        if self.is_missing_user():
            page_error_messages = kwargs.pop('page_error_messages', None) or []
            return self.page(self.pages[-1], page_change=False, page_error_messages=page_error_messages)
        return super().validating(*args, **kwargs)

    def lateral_block(self):
        get_response().raw = True
        response = self.get_lateral_block()
        return response

    def get_default_return_url(self):
        return '%s/submission/' % get_publisher().get_backoffice_url()

    def get_transient_formdata(self, magictoken=Ellipsis):
        formdata = super().get_transient_formdata(magictoken=magictoken)
        if (
            'selected_submission_channel' in (formdata.submission_context or {})
            and not self.selected_submission_channel
        ):
            self.selected_submission_channel = formdata.submission_context.get('selected_submission_channel')
        if 'selected_user_id' in (formdata.submission_context or {}) and not self.selected_user_id:
            self.selected_user_id = formdata.submission_context.get('selected_user_id')
        if 'submission-locked' in (formdata.submission_context or {}):
            if 'channel' in formdata.submission_context['submission-locked']:
                self.selected_submission_channel = formdata.submission_context['submission-locked']['channel']
            if 'user_id' in formdata.submission_context['submission-locked']:
                self.selected_user_id = formdata.submission_context['submission-locked']['user_id']
        if get_request().form.get('user_id'):
            # when used via /live endpoint
            formdata.user_id = get_request().form['user_id']
        elif formdata.user_id and not self.selected_user_id:
            self.selected_user_id = formdata.user_id
        elif self.selected_user_id:
            formdata.user_id = self.selected_user_id
        formdata.backoffice_submission = True
        formdata.submission_channel = self.selected_submission_channel
        return formdata

    @classmethod
    def get_status_page_class(cls):
        return SubmissionFormStatusPage

    def check_authentication_context(self):
        pass

    def check_access(self):
        if self.edit_mode:
            return True
        if not self.formdef.backoffice_submission_roles:
            raise errors.AccessUnauthorizedError()
        for role in get_request().user.get_roles():
            if role in self.formdef.backoffice_submission_roles:
                break
        else:
            raise errors.AccessUnauthorizedError()

    def check_unique_submission(self):
        return None

    def modify_filling_context(self, context, page, data):
        context['sidebar'] = self.get_sidebar(data)
        if not self.formdef.only_allow_one:
            return
        try:
            formdata = self.formdef.data_class().get(data['draft_formdata_id'])
        except KeyError:  # it may not exist
            return

        data_class = self.formdef.data_class()
        context['user_has_already_one_such_form'] = bool(
            data_class.count([StrictNotEqual('status', 'draft'), Equal('user_id', formdata.user_id)])
        )

    def modify_validation_context(self, context, data):
        context['sidebar'] = self.get_sidebar(data)

    def get_sidebar(self, data):
        r = TemplateIO(html=True)

        sidebar_items = self.formdef.get_submission_sidebar_items()

        formdata = None
        if self.edit_mode:
            formdata = self.edited_data
        else:
            draft_formdata_id = data.get('draft_formdata_id')
            if draft_formdata_id:
                try:
                    formdata = self.formdef.data_class().get(draft_formdata_id)
                except KeyError:  # it may not exist
                    pass

        if 'general' in sidebar_items:
            if self.formdef.enable_tracking_codes and not self.edit_mode:
                r += htmltext('<h3>%s</h3>') % _('Tracking Code')
                if formdata and formdata.tracking_code:
                    r += htmltext('<p>%s</p>') % formdata.tracking_code
                else:
                    r += htmltext('<p>-</p>')

        if formdata:
            if self.has_channel_support and self.selected_submission_channel:
                formdata.submission_channel = self.selected_submission_channel
            if self.has_user_support and self.selected_user_id and not self.edit_mode:
                formdata.user_id = self.selected_user_id

        from .management import FormBackOfficeStatusPage

        if self.on_validation_page or self.edit_mode:
            if formdata:
                r += FormBackOfficeStatusPage(self.formdef, formdata).get_extra_context_bar(parent=self)
        else:
            if (
                'submission-context' in sidebar_items
                and formdata
                and formdata.submission_context
                and set(formdata.submission_context.keys()).difference({'return_url', 'cancel_url'})
            ):
                r += FormBackOfficeStatusPage(self.formdef, formdata).get_extra_submission_context_bar()

            channel_selection = True
            user_selection = True
            if formdata:
                locked = (formdata.submission_context or {}).get('submission-locked', set())
                channel_selection = bool('channel' not in locked)
                user_selection = bool('user_id' not in locked)

            if 'submission-context' in sidebar_items and self.has_channel_support:
                if channel_selection:
                    r += htmltext('<div class="submit-channel-selection" style="display: none;">')
                    r += htmltext('<h3>%s</h3>') % _('Channel')
                    r += htmltext('<select>')
                    for channel_key, channel_label in [('', '-')] + list(
                        FormData.get_submission_channels().items()
                    ):
                        selected = ''
                        if self.selected_submission_channel == channel_key:
                            selected = 'selected="selected"'
                        r += htmltext('<option value="%s" %s>' % (channel_key, selected))
                        r += htmltext('%s</option>') % channel_label
                    r += htmltext('</select>')
                    r += htmltext('</div>')
                else:
                    # no channel selection, just displayed
                    r += FormBackOfficeStatusPage(self.formdef, formdata).get_extra_submission_channel_bar()

            if 'user' in sidebar_items and self.has_user_support:
                if user_selection:
                    r += FormBackOfficeStatusPage(
                        self.formdef, formdata
                    ).get_extra_submission_user_selection_bar(parent=self)
                else:
                    r += FormBackOfficeStatusPage(self.formdef, formdata).get_extra_submission_user_id_bar(
                        parent=self
                    )

        if 'custom-template' in sidebar_items and self.formdef.submission_lateral_template:
            r += htmltext(
                '<div data-async-url="%slateral-block?ctx=%s"></div>'
                % (
                    self.formdef.get_backoffice_submission_url(),
                    formdata.id if formdata else '',
                )
            )
        return r.getvalue()

    def get_lateral_block(self):
        r = TemplateIO(html=True)
        formdata = self.get_empty_formdata()
        if get_request().form.get('ctx'):
            try:
                formdata = self.formdef.data_class().get(get_request().form.get('ctx'))
                if not formdata.is_draft():
                    formdata = None
            except KeyError:  # it may not exist
                pass
        get_publisher().substitutions.feed(formdata)
        lateral_block = self.formdef.get_submission_lateral_block()
        if lateral_block:
            r += htmltext('<div class="lateral-block">')
            r += htmltext(lateral_block)
            r += htmltext('</div>')
        return r.getvalue()

    def create_view_form(self, *args, **kwargs):
        form = super().create_view_form(*args, **kwargs)
        if self.has_channel_support:
            form.add_hidden('submission_channel', self.selected_submission_channel)
        if self.has_user_support:
            form.add_hidden('user_id', self.selected_user_id)
        return form

    def create_form(self, *args, **kwargs):
        form = super().create_form(*args, **kwargs)
        form.attrs['data-live-url'] = self.formdef.get_backoffice_submission_url() + 'live'
        if not get_publisher().has_site_option('backoffice-autosave'):
            form.attrs['data-autosave'] = 'false'
        if self.has_channel_support:
            form.add_hidden('submission_channel', self.selected_submission_channel)
        if self.has_user_support:
            form.add_hidden('user_id', self.selected_user_id)
        return form

    def form_side(self, data=None, magictoken=None):
        r = TemplateIO(html=True)
        get_response().filter['sidebar'] = self.get_sidebar(data)
        if not self.edit_mode and not getattr(self, 'is_popup', False):
            draft_formdata_id = data.get('draft_formdata_id')
            if draft_formdata_id:
                get_response().add_javascript(['popup.js'])
                r += htmltext('<a rel="popup" href="remove/%s">%s</a>') % (
                    draft_formdata_id,
                    _('Discard this form'),
                )
        return mark_safe(str(r.getvalue()))

    def submitted(self, form, *args):
        if self.is_missing_user():
            raise RequiredUserException()
        filled = self.get_current_draft() or self.formdef.data_class()()
        if filled.id and filled.status != 'draft':
            get_session().add_message(self.already_submitted_message)
            return redirect(self.get_default_return_url())
        filled.just_created()
        filled.data = self.formdef.get_data(form)
        magictoken = get_request().form['magictoken']
        computed_values = get_session().get_by_magictoken('%s-computed' % magictoken, {})
        filled.data.update(computed_values)
        filled.backoffice_submission = True
        if not filled.submission_context:
            filled.submission_context = {}
        filled.submission_context.pop('selected_user_id', None)
        filled.submission_context.pop('selected_submission_channel', None)
        filled.submission_context.pop('submission-locked', None)
        if self.has_channel_support and self.selected_submission_channel:
            filled.submission_channel = self.selected_submission_channel
        if self.has_user_support and self.selected_user_id:
            filled.user_id = self.selected_user_id
        filled.submission_agent_id = str(get_request().user.id)
        filled.store()

        self.set_tracking_code(filled)
        get_session().remove_magictoken(get_request().form.get('magictoken'))
        self.clean_submission_context()
        filled.refresh_from_storage()
        filled.record_workflow_event('backoffice-created')
        if get_publisher().has_site_option('perform-workflow-as-job'):
            url = None
            filled.perform_workflow_as_job()
        else:
            url = filled.perform_workflow()
        return self.redirect_after_submitted(url, filled)

    def redirect_after_submitted(self, url, filled):
        if url:
            pass  # always redirect to an URL the workflow returned
        elif not self.formdef.is_of_concern_for_user(self.user, filled):
            # if the agent is not allowed to see the submitted formdef,
            # redirect to the defined return URL or to the submission
            # homepage
            if filled.submission_context and filled.submission_context.get('return_url'):
                url = filled.submission_context['return_url']
            else:
                get_session().add_message(_('Submitted form has been recorded.'), level='success')
                url = self.get_default_return_url()
        else:
            url = filled.get_url(backoffice=True)

        return redirect(url)

    def cancelled(self):
        url = self.get_default_return_url()
        formdata = self.get_current_draft() or self.formdef.data_class()()
        if formdata.submission_context:
            if formdata.submission_context.get('return_url'):
                url = formdata.submission_context.get('return_url')
            if formdata.submission_context.get('cancel_url'):
                url = formdata.submission_context.get('cancel_url')
        if formdata.id:
            formdata.remove_self()
        return redirect(url)

    def save_draft(self, data, page_no=None, where=None):
        formdata = super().save_draft(data, page_no=page_no, where=where)
        formdata.backoffice_submission = True
        if not formdata.submission_context:
            formdata.submission_context = {}
        formdata.submission_context['selected_submission_channel'] = self.selected_submission_channel
        formdata.submission_context['selected_user_id'] = self.selected_user_id
        if self.selected_user_id and not formdata.user_id:
            formdata.user_id = self.selected_user_id
        formdata.submission_channel = self.selected_submission_channel
        formdata.submission_agent_id = str(get_request().user.id)
        formdata.store()
        return formdata


class SubmissionDirectory(Directory):
    _q_exports = ['', 'new', 'pending', 'count']

    def _q_traverse(self, path):
        get_response().set_backoffice_section('submission')
        get_response().breadcrumb.append(('submission/', _('Submission')))
        return super()._q_traverse(path)

    def is_accessible(self, user, traversal=False):
        if not user.can_go_in_backoffice() and not getattr(user, 'is_api_user', False):
            return False
        if traversal is False and get_cfg('backoffice-submission', {}).get('sidebar_menu_entry') == 'hidden':
            return False
        # check user has at least one role set for backoffice submission
        if user.roles:
            return FormDef.exists([Intersects('backoffice_submission_roles', [str(x) for x in user.roles])])
        return False

    def get_submittable_formdefs(self, prefetch=True):
        user = get_request().user

        agent_ids = set()
        list_forms = []
        for formdef in FormDef.select(order_by='name', ignore_errors=True):
            if formdef.is_disabled():
                continue
            if not formdef.backoffice_submission_roles:
                continue
            for role in user.get_roles():
                if role in formdef.backoffice_submission_roles:
                    break
            else:
                continue
            list_forms.append(formdef)

            if prefetch:
                # prefetch formdatas
                data_class = formdef.data_class()
                formdef._formdatas = data_class.select(
                    [Equal('status', 'draft'), Equal('backoffice_submission', True)]
                )
                formdef._formdatas.sort(
                    key=lambda x: x.receipt_time or make_aware(datetime.datetime(1900, 1, 1))
                )
                agent_ids.update([x.submission_agent_id for x in formdef._formdatas if x.submission_agent_id])

        if prefetch:
            # prefetch agents
            self.prefetched_agents = {
                str(x.id): x
                for x in get_publisher().user_class.get_ids(list(agent_ids), ignore_errors=True)
                if x is not None
            }

        return list_forms

    def get_categories(self, list_formdefs):
        cats = Category.select()
        Category.sort_by_position(cats)
        for cat in cats:
            cat.formdefs = [x for x in list_formdefs if str(x.category_id) == str(cat.id)]
        misc_cat = Category(name=_('Misc'))
        misc_cat.formdefs = [x for x in list_formdefs if not x.category]
        cats.append(misc_cat)
        return cats

    def _q_index(self):
        redirect_url = get_cfg('backoffice-submission', {}).get('redirect')
        default_submission_screen = get_cfg('backoffice-submission', {}).get('default_screen')
        if redirect_url and default_submission_screen in ('custom', None):
            redirect_url = misc.get_variadic_url(
                redirect_url, get_publisher().substitutions.get_context_variables(mode='lazy')
            )
            if redirect_url:
                return redirect(redirect_url)
        if default_submission_screen == 'pending':
            return redirect('pending')
        return redirect('new')

    def new(self):
        get_response().set_title(_('Submission'))
        get_response().breadcrumb.append(('new', _('New submission')))

        list_forms = self.get_submittable_formdefs(prefetch=False)

        context = {'categories': self.get_categories(list_forms)}
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/submission.html'], context=context, is_django_native=True
        )

    def pending(self):
        get_response().breadcrumb.append(('pending', _('Pending submissions')))
        get_response().set_title(_('Pending submissions'))
        get_response().add_javascript(['wcs.listing.js'])

        limit = misc.get_int_or_400(
            get_request().form.get('limit', get_publisher().get_site_option('default-page-size') or 20)
        )
        offset = misc.get_int_or_400(get_request().form.get('offset', 0))
        order_by = misc.get_order_by_or_400(
            get_request().form.get(
                'order_by', get_publisher().get_site_option('default-sort-order') or '-receipt_time'
            )
        )
        include_submission_channel = misc.get_cfg('submission-channels', {}).get('include-in-global-listing')
        mine = bool(get_request().form.get('mine') == 'true')

        list_formdefs = self.get_submittable_formdefs(prefetch=False)
        criterias = [
            Equal('status', 'draft'),
            Equal('backoffice_submission', True),
            Contains('formdef_id', [x.id for x in list_formdefs]),
        ]
        if mine:
            criterias.append(Equal('submission_agent_id', str(get_request().user.id)))

        r = TemplateIO(html=True)

        r += htmltext('<table id="listing" class="main">')
        r += htmltext('<thead><tr>')
        if include_submission_channel:
            r += htmltext('<th data-field-sort-key="submission_channel"><span>%s</span></th>') % _('Channel')
        r += htmltext('<th data-field-sort-key="formdef_name"><span>%s</span></th>') % _('Form')
        r += htmltext('<th data-field-sort-key="receipt_time"><span>%s</span></th>') % _('Created')
        r += htmltext('<th><span>%s</span></th>') % _('Submission Agent')
        r += htmltext('<th><span>%s</span></th>') % _('Associated User')
        r += htmltext('</tr></thead>')
        r += htmltext('<tbody>\n')

        from wcs.sql import AnyFormData

        total_count = AnyFormData.count(criterias)
        formdatas = AnyFormData.select(criterias, order_by=order_by, limit=limit, offset=offset)

        # prefetch agents and users
        user_ids = set()
        user_ids.update([x.submission_agent_id for x in formdatas if x.submission_agent_id])
        user_ids.update([x.user_id for x in formdatas if x.user_id])
        self.prefetched_users = {
            str(x.id): x
            for x in get_publisher().user_class.get_ids(list(user_ids), ignore_errors=True)
            if x is not None
        }

        for formdata in formdatas:
            url = f'{formdata.formdef.url_name}/{formdata.id}/'
            r += htmltext(f'<tr data-link="{url}">')
            if include_submission_channel:
                r += htmltext('<td>%s</td>') % formdata.get_submission_channel_label()
            r += htmltext(f'<td><a href="{url}">{formdata.get_display_name()}')
            if formdata.default_digest:
                r += htmltext(' <small>%s</small>') % formdata.default_digest
            r += htmltext('</a></td>')
            r += htmltext('<td class="cell-time">%s</td>') % misc.localstrftime(formdata.receipt_time)
            agent_user = self.prefetched_users.get(formdata.submission_agent_id)
            if agent_user:
                r += htmltext('<td class="cell-user">%s</td>') % agent_user.get_display_name()
            else:
                r += htmltext('<td class="cell-user cell-no-user">-</td>')
            user = self.prefetched_users.get(formdata.user_id)
            if user:
                r += htmltext('<td class="cell-user">%s</td>') % user.get_display_name()
            else:
                r += htmltext('<td class="cell-user cell-no-user">-</td>')
            r += htmltext('</tr>\n')

        r += htmltext('</tbody></table>')

        if (offset > 0) or (total_count > limit > 0):
            r += pagination_links(offset, limit, total_count)

        if get_request().form.get('ajax') == 'true':
            get_request().ignore_session = True
            get_response().raw = True
            return r.getvalue()

        rt = TemplateIO(html=True)
        rt += htmltext('<div id="appbar">')
        rt += htmltext('<div><h2 class="appbar--title">%s</h2></div>') % _('Pending submissions')
        rt += htmltext('<span class="actions">')
        rt += htmltext('<a href="new">%s</a>') % _('New submission')
        rt += htmltext('<span class="buttons-group" id="btn-submissions-filter">')
        klasses = ('', 'active') if mine else ('active', '')
        rt += htmltext(f'<button data-value="false" class="{klasses[0]}">%s</button>') % _('All submissions')
        rt += htmltext(f'<button data-value="true" class="{klasses[1]}">%s</button>') % _('My submissions')
        rt += htmltext('</span>')
        rt += htmltext('</span>')
        rt += htmltext('</div>')
        rt += get_session().display_message()
        rt += r.getvalue()
        form = Form(use_tokens=False, id='listing-settings', method='get', action='pending')
        form.add_hidden('mine', get_request().form.get('mine') or '')
        form.add_hidden('offset', offset)
        form.add_hidden('limit', limit)
        form.add_hidden('order_by', order_by)
        rt += form.render()

        return rt.getvalue()

    def count(self):
        formdefs = self.get_submittable_formdefs()
        count = 0
        mode = get_request().form.get('mode')
        for formdef in formdefs:
            formdatas = formdef._formdatas
            if mode == 'empty':
                formdatas = [x for x in formdatas if x.has_empty_data()]
            elif mode == 'existing':
                formdatas = [x for x in formdatas if not x.has_empty_data()]
            count += len(formdatas)
        return misc.json_response({'count': count})

    def _q_lookup(self, component):
        return FormFillPage(component)

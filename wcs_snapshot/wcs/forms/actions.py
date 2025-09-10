# w.c.s. - web application for online forms
# Copyright (C) 2005-2018  Entr'ouvert
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

from django.utils.timezone import now
from quixote import get_publisher, get_request, get_response, get_session, redirect
from quixote.directory import Directory
from quixote.errors import PublishError

from wcs.carddef import CardDef
from wcs.formdef import FormDef
from wcs.forms.common import FormTemplateMixin
from wcs.qommon import _, errors, misc, template
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.form import Form
from wcs.qommon.http_request import HTTPRequest
from wcs.wf.jump import jump_and_perform
from wcs.workflows import RedisplayFormException, perform_items, push_perform_workflow


class InvalidActionLink(PublishError):
    status_code = 404
    title = _('Error')
    description = _('This action link is no longer valid.')


class MissingOrExpiredToken(InvalidActionLink):
    description = _('This action link has already been used or has expired.')


class MissingFormdata(InvalidActionLink):
    description = _('This action link is no longer valid as the attached form has been removed.')


class ActionsDirectory(Directory):
    def _q_lookup(self, component):
        try:
            token = get_publisher().token_class.get(component)
        except KeyError:
            raise MissingOrExpiredToken()
        if token.type == 'action':
            return ActionDirectory(token)
        if token.type == 'global-interactive-action':
            return GlobalInteractiveActionDirectory(token)
        raise errors.TraversalError()


class ActionDirectory(Directory, FormTemplateMixin):
    _q_exports = ['']
    templates = ['wcs/action.html']
    do_not_call_in_templates = True

    def __init__(self, token):
        self.token = token
        formdef_type = self.token.context.get('form_type', 'formdef')
        if formdef_type == 'carddef':
            formdef_class = CardDef
        elif formdef_type == 'formdef':
            formdef_class = FormDef
        else:
            raise errors.TraversalError()

        self.formdef = formdef_class.get_by_urlname(self.token.context['form_slug'])
        try:
            self.formdata = self.formdef.data_class().get(self.token.context['form_number_raw'])
        except KeyError:
            raise MissingFormdata()
        self.action = None
        status = self.formdata.get_status()
        if not status or not status.items:
            # unknown status or workflow change and no actions anymore
            raise InvalidActionLink()
        for item in status.items:
            if getattr(item, 'identifier', None) == self.token.context['action_id']:
                self.action = item
                break
        else:
            raise MissingOrExpiredToken()

    def _q_index(self):
        get_response().set_title(self.formdef.name)
        form = Form()
        form.add_submit('submit', misc.site_encode(self.token.context['label']))
        if form.is_submitted() and not form.has_errors():
            return self.submit()
        context = {
            'view': self,
            'form': form,
            'html_form': form,
            'message': self.token.context.get('message'),
        }
        return template.QommonTemplateResponse(
            templates=list(self.get_formdef_template_variants(self.templates)), context=context
        )

    def submit(self):
        self.formdata.record_workflow_event('email-button', action_item_id=self.action.id)
        url = jump_and_perform(self.formdata, self.action)
        done_message = self.token.context.get('done_message')
        self.token.remove_self()
        if url:
            return redirect(url)
        context = {
            'view': self,
            'done': True,
            'done_message': done_message,
        }
        return template.QommonTemplateResponse(
            templates=list(self.get_formdef_template_variants(self.templates)), context=context
        )


class GlobalInteractiveActionDirectory(Directory, FormTemplateMixin):
    _q_exports = ['']

    def __init__(self, token):
        self.token = token
        formdef_type = self.token.context.get('form_type', 'formdef')
        if formdef_type == 'carddef':
            formdef_class = CardDef
        elif formdef_type == 'formdef':
            formdef_class = FormDef
        else:
            raise errors.TraversalError()

        self.formdef = formdef_class.get_by_urlname(self.token.context['form_slug'])

        try:
            self.formdata = self.formdef.data_class().get(self.token.context['form_ids'][0])
        except KeyError:
            raise MissingFormdata()

        self.action = None
        for action in self.formdef.workflow.global_actions or []:
            if action.id == self.token.context['action_id']:
                self.action = action
                break
        else:
            raise MissingOrExpiredToken()

    def _q_index(self):
        get_response().set_title(self.formdef.name)

        if len(self.token.context['form_ids']) == 1:
            get_publisher().substitutions.feed(self.formdata)

        form = self.action.get_action_form(self.formdata, user=get_request().user, displayed_fields=[])
        if not form:
            # empty form, nothing to do
            get_session().add_message(_('Configuration error: no available action.'))
            get_publisher().record_error(
                _('Configuration error in global interactive action (%s), check roles and functions.')
                % self.action.name,
                formdata=self.formdata,
            )
            return redirect(self.token.context['return_url'])

        form.add_submit('cancel', _('Cancel'))
        if form.get_submit() == 'cancel':
            return redirect(self.token.context['return_url'])

        if not form.is_submitted() or form.has_errors() or form.get_submit() is True:
            # display form if it has not been submitted, or has errors, or the clicked
            # button doesn't match a submit button (for example a "add row" button in a
            # block of fields)
            return self.display_form(form)

        if len(self.token.context['form_ids']) > 1:
            # mass action
            job = get_publisher().add_after_job(
                GlobalInteractiveMassActionAfterJob(
                    label=_('Executing task "%s" on forms') % self.action.name,
                    formdef=self.formdef,
                    request_form=get_request().form,
                    user_id=get_request().user.id,
                    action_id=self.action.id,
                    item_ids=self.token.context['form_ids'],
                    session_id=get_session().id,
                    return_url=self.token.context['return_url'],
                )
            )
            job.store()
            self.token.remove_self()
            return redirect(job.get_processing_url())

        try:
            url = GlobalInteractiveMassActionAfterJob.execute_one(
                get_publisher(), self.formdata, self.action, get_request().user, afterjob=False
            )
        except RedisplayFormException as e:
            return self.display_form(e.form)

        if not url:
            url = self.formdata.get_url(backoffice=bool(get_request().is_in_backoffice()))
        self.token.remove_self()
        return redirect(url)

    def display_form(self, form):
        if get_request().is_in_backoffice():
            get_response().breadcrumb.append(('', self.action.name))
            template_name = 'wcs/backoffice/global-interactive-action.html'
        else:
            template_name = 'wcs/global-interactive-action.html'
        messages = self.action.get_messages()
        get_response().add_javascript(['jquery.js', 'qommon.forms.js'])
        form.attrs['data-js-features'] = 'true'
        context = {
            'html_form': form,
            'action': self.action,
            'ids': self.token.context['form_ids'],
            'formdata': self.formdata,
            'workflow_messages': messages,
        }
        return template.QommonTemplateResponse(templates=[template_name], context=context)


class GlobalInteractiveMassActionAfterJob(AfterJob):
    def __init__(self, formdef, **kwargs):
        super().__init__(formdef_class=formdef.__class__, formdef_id=formdef.id, **kwargs)

    def execute(self):
        self.total_count = len(self.kwargs['item_ids'])

        # restore request form
        publisher = get_publisher()
        req = HTTPRequest(None, {'SERVER_NAME': publisher.tenant.hostname, 'SCRIPT_NAME': ''})
        req.form = self.kwargs['request_form']

        formdef = self.kwargs['formdef_class'].get(self.kwargs['formdef_id'])
        data_class = formdef.data_class()

        for action in formdef.workflow.global_actions or []:
            if action.id == self.kwargs['action_id']:
                break
        else:
            # maybe action got removed from workflow?
            return

        if not hasattr(self, 'processed_ids'):
            self.processed_ids = {}

        user = publisher.user_class.get(self.kwargs['user_id'])
        for item_id in self.kwargs['item_ids']:
            if item_id in self.processed_ids:
                continue
            publisher._set_request(req)
            req.session = publisher.session_class.get(self.kwargs['session_id'])
            formdata = data_class.get(item_id)
            self.execute_one(publisher, formdata, action, user)
            self.processed_ids[item_id] = now()
            self.increment_count()
        self.store()

    @classmethod
    def execute_one(cls, publisher, formdata, action, user, afterjob=True):
        publisher.reset_formdata_state()
        publisher.substitutions.feed(user)
        publisher.substitutions.feed(formdata.formdef)
        publisher.substitutions.feed(formdata)

        request_form = copy.copy(get_request().form)

        status = formdata.status
        form = action.get_action_form(formdata, user=user)
        if form is None:
            return
        get_request().form = request_form  # cancel fields overwritten by prefills
        form.method = 'get'
        url = action.handle_form(form, formdata, user=user, check_replay=False)
        if afterjob:
            # reset request to avoid emails being created as afterjobs
            publisher._set_request(None)
        with push_perform_workflow(formdata):
            if formdata.status == status:
                # if there's no status change run non-interactive items from global action
                formdata.record_workflow_event('global-interactive-action', global_action_id=action.id)
                url = perform_items(action.items, formdata, global_action=True) or url
            else:
                # run actions from new status
                wf_status = formdata.get_status()
                formdata.record_workflow_event('global-interactive-action', global_action_id=action.id)
                formdata.record_workflow_event('continuation')
                url = perform_items(wf_status.items, formdata) or url
        # return url, it is used when the action is performed on a single item
        return url

    def done_action_url(self):
        return self.kwargs['return_url']

    def done_action_label(self):
        return _('Back to Listing')

    def done_button_attributes(self):
        return {'data-redirect-auto': 'true'}

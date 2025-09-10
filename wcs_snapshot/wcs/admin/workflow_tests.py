# w.c.s. - web application for online forms
# Copyright (C) 2005-2023  Entr'ouvert
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
import json
import uuid

from quixote import get_publisher, get_request, get_response, get_session, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext

from wcs.admin.tests import TestEditPage
from wcs.forms.common import FormStatusPage
from wcs.qommon import _, template
from wcs.qommon.errors import TraversalError
from wcs.qommon.form import Form, SingleSelectWidget
from wcs.workflow_tests import get_test_action_class_by_type, get_test_action_options


class WorkflowTestActionPage(Directory):
    _q_exports = ['', 'delete', 'duplicate', 'fields', ('edit-form', 'edit_form')]

    def __init__(self, testdef, formdef, component):
        self.testdef = testdef
        self.formdef = formdef
        try:
            self.action = [x for x in testdef.workflow_tests.actions if x.id == component][0]
        except IndexError:
            raise TraversalError()

    def _q_traverse(self, path):
        get_response().breadcrumb.append((str(self.action.id) + '/', str(self.action)))
        return Directory._q_traverse(self, path)

    def _q_index(self):
        form = Form(enctype='multipart/form-data')

        self.action.fill_admin_form(form, self.formdef)

        if not form.widgets:
            form.add_global_errors([htmltext(self.action.empty_form_error)])
        else:
            if not self.testdef.is_readonly():
                form.add_submit('submit', self.action.edit_button_label)
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('..')

        if not form.get_submit() == 'submit' or form.has_errors():
            get_response().set_title(_('Edit action'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % (_('Edit action'))
            r += form.render()
            return r.getvalue()

        for widget in form.widgets:
            if hasattr(self.action, '%s_parse' % widget.name):
                value = getattr(self.action, '%s_parse' % widget.name)(widget.value)
            else:
                value = widget.parse()

            setattr(self.action, widget.name, value)

        self.testdef.store(comment=_('Change in workflow test action "%s"') % self.action.label)
        return redirect(self.action.edit_redirect_url)

    def delete(self):
        form = Form(enctype='multipart/form-data')
        form.add_submit('delete', _('Delete'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')
        if not form.is_submitted() or form.has_errors():
            get_response().set_title(_('Delete'))
            get_response().breadcrumb.append(('delete', _('Delete')))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s %s</h2>') % (_('Deleting action:'), self.action)
            r += form.render()
            return r.getvalue()

        self.testdef.workflow_tests.actions = [
            x for x in self.testdef.workflow_tests.actions if x.id != self.action.id
        ]
        self.testdef.store(comment=_('Deletion of workflow test action "%s"') % self.action.label)
        return redirect('..')

    def duplicate(self):
        new_action = copy.deepcopy(self.action)
        new_action.id = self.testdef.workflow_tests.get_new_action_id()
        new_action.uuid = str(uuid.uuid4())
        action_position = self.testdef.workflow_tests.actions.index(self.action)
        self.testdef.workflow_tests.actions.insert(action_position + 1, new_action)
        self.testdef.store(comment=_('Duplication of workflow test action "%s"') % self.action.label)
        return redirect('..')

    @property
    def fields(self):
        if self.action.key != 'fill-form':
            raise TraversalError

        return FillFormFieldsPage(self.testdef, self.formdef, self.action)

    @property
    def edit_form(self):
        if self.action.key != 'edit-form':
            raise TraversalError

        testdef = copy.deepcopy(self.testdef)
        testdef.data['fields'].update(self.action.form_data)
        filled = testdef.build_formdata(self.formdef, include_fields=True)

        page = EditFormPage(self.action, self.formdef, self.testdef, filled)

        try:
            page.edit_action = self.action.get_workflow_edit_action(self.formdef)
        except KeyError:
            raise TraversalError

        return page


class FillFormFieldsPage(Directory):
    _q_exports = ['', 'live']

    last_test_result = None

    def __init__(self, testdef, formdef, fill_form_action):
        self.testdef = testdef
        self.formdef = formdef
        self.action = fill_form_action

        self.form_action = self.action.get_workflow_form_action(self.formdef)
        self.form_action.prefix_form_fields = lambda: None

        get_request().edited_test_id = self.testdef.id

        if self.action.feed_last_test_result:
            self.last_test_result = self.testdef.get_last_test_result(self.formdef)

    def _q_traverse(self, path):
        results = self.testdef.get_last_dependencies_results()
        if self.last_test_result:
            results.append(self.last_test_result)

        with self.testdef.use_test_objects(results=results):
            return Directory._q_traverse(self, path)

    def _q_index(self):
        from wcs.testdef import TestDef

        form = self.get_fields_form()

        if form.get_widget('cancel').parse():
            return redirect('..')

        if not form.get_submit() == 'submit' or form.has_errors():
            get_response().set_title(_('Fields'))
            get_response().breadcrumb.append(('fields', _('Fields')))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('Fields')
            if self.action.feed_last_test_result and not self.last_test_result:
                r += htmltext('<div class="infonotice"><p>%s</p></div>') % _(
                    'Last test result could no be used, please check it exists.'
                )
            r += form.render()
            return r.getvalue()

        form_data = self.form_action.formdef.get_data(form)
        self.action.form_data = TestDef.serialize_form_data(self.form_action.formdef, form_data)

        self.testdef.store()
        return redirect(self.testdef.get_admin_url() + 'workflow/')

    def get_fields_form(self):
        form = Form(enctype='multipart/form-data')
        form.attrs['data-js-features'] = 'true'

        page = FormStatusPage(self.testdef.formdef, self.get_formdata())

        form = page.get_workflow_form(user=None)
        form.attrs['data-live-url'] = self.action.get_admin_url() + 'fields/live'

        if 'submit' not in form._names:
            form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        return form

    def get_formdata(self):
        from wcs.testdef import TestDef

        if self.last_test_result:
            formdata = self.formdef.data_class().get(self.last_test_result.formdata_id)
        else:
            formdata = self.testdef.build_formdata(self.testdef.formdef, include_fields=True)
            formdata.just_created()

        form_data = TestDef.deserialize_form_data(self.form_action.formdef, self.action.form_data)

        # override workflow form methods to skip checks on status and user roles
        def get_workflow_form(user, displayed_fields=None):
            form = Form(enctype='multipart/form-data', use_tokens=False)
            self.form_action.fill_form(
                form, formdata, user=None, displayed_fields=displayed_fields, form_data=form_data
            )
            return form

        def evaluate_live_workflow_form(user, form):
            self.form_action.evaluate_live_form(form, formdata, user=None)

        formdata.get_workflow_form = get_workflow_form
        formdata.evaluate_live_workflow_form = evaluate_live_workflow_form

        return formdata

    def live(self):
        return FormStatusPage(self.testdef.formdef, self.get_formdata()).live()


class EditFormPage(TestEditPage):
    last_test_result = None

    def __init__(self, edit_form_action, *args, **kwargs):
        self.edit_form_action = edit_form_action
        super().__init__(*args, **kwargs)

        if self.edit_form_action.feed_last_test_result:
            self.last_test_result = self.testdef.get_last_test_result(self.formdef)

            if self.last_test_result:
                with self.testdef.use_test_objects(results=[self.last_test_result]):
                    formdata = self.formdef.data_class().get(self.last_test_result.formdata_id)

                formdata.data.update(self.edited_data.data)
                self.edited_data = formdata

    def get_test_results(self):
        if self.last_test_result:
            return [self.last_test_result]

        return super().get_test_results()

    def create_form(self, *args, **kwargs):
        form = super().create_form(*args, **kwargs)
        form.attrs['data-live-url'] = self.edit_form_action.get_admin_url() + 'edit-form/live'
        return form

    def modify_filling_context(self, context, *args, **kwargs):
        super(TestEditPage, self).modify_filling_context(context, *args, **kwargs)
        get_response().filter['sidebar'] = None


class WorkflowTestsDirectory(Directory):
    _q_exports = ['', 'options', 'update_order', 'new']

    def __init__(self, testdef, formdef):
        self.testdef = testdef
        self.formdef = formdef

    def _q_traverse(self, path):
        get_response().set_title(_('Workflow tests'))
        get_response().breadcrumb.append(('workflow/', _('Workflow tests')))
        return Directory._q_traverse(self, path)

    def _q_lookup(self, component):
        return WorkflowTestActionPage(self.testdef, self.formdef, component)

    def _q_index(self):
        context = {
            'testdef': self.testdef,
            'has_sidebar': bool(not self.testdef.is_readonly()),
            'sidebar_form': self.get_sidebar_form(),
        }

        get_response().add_javascript(
            ['popup.js', 'jquery.js', 'jquery-ui.js', 'biglist.js', 'select2.js', 'widget_list.js']
        )
        get_response().set_title(self.testdef.name)

        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/workflow-tests.html'], context=context, is_django_native=True
        )

    def get_sidebar_form(self):
        form = Form(enctype='multipart/form-data', action='new')
        form.add(
            SingleSelectWidget,
            'type',
            title=_('Type'),
            required=True,
            options=get_test_action_options(),
            value='assert-status',
        )
        form.add_submit('submit', _('Add'))
        return form

    def options(self):
        form = Form(enctype='multipart/form-data')

        user_options = [('', '---', '')] + [
            (str(x.test_uuid), str(x), str(x.test_uuid))
            for x in get_publisher().test_user_class.select(order_by='name')
        ]
        form.add(
            SingleSelectWidget,
            'agent',
            title=_('Backoffice user'),
            value=self.testdef.agent_id,
            options=user_options,
            **{'data-autocomplete': 'true'},
        )

        if not self.testdef.is_readonly():
            form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')

        if not form.is_submitted() or form.has_errors():
            get_response().set_title(_('Options'))
            get_response().breadcrumb.append(('options', _('Options')))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % (_('Options'))
            r += form.render()
            return r.getvalue()

        self.testdef.agent_id = form.get_widget('agent').parse()
        self.testdef.store(comment=_('Change in workflow test options'))
        return redirect('.')

    def new(self):
        form = Form(enctype='multipart/form-data')
        form.add_hidden('type')

        if not form.is_submitted() or form.has_errors():
            get_session().add_message(_('Submitted form was not filled properly.'))
            return redirect('.')

        action_type = form.get_widget('type').parse()
        action_class = get_test_action_class_by_type(action_type)
        self.testdef.workflow_tests.add_action(action_class)
        self.testdef.store(comment=_('New test action "%s"') % action_class.label)

        return redirect('.')

    def update_order(self):
        get_response().set_content_type('application/json')
        request = get_request()

        if 'element' not in request.form:
            return json.dumps({'success': 'ko'})
        if 'order' not in request.form:
            return json.dumps({'success': 'ko'})

        new_order = request.form['order'].strip(';').split(';')
        new_actions = []

        # build new ordered actions list
        for y in new_order:
            for i, x in enumerate(self.testdef.workflow_tests.actions):
                if x.id != y:
                    continue
                new_actions.append(x)
                break

        # check new actions list composition
        if set(self.testdef.workflow_tests.actions) != set(new_actions):
            return json.dumps({'success': 'ko'})

        self.testdef.workflow_tests.actions = new_actions
        self.testdef.store(comment=_('Change in workflow test actions order'))

        return json.dumps(
            {
                'success': 'ok',
            }
        )

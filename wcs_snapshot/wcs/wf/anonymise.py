# w.c.s. - web application for online forms
# Copyright (C) 2005-2011  Entr'ouvert
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

from quixote import get_publisher, get_request, get_session

from wcs.qommon import _
from wcs.qommon.form import RadiobuttonsWidget
from wcs.workflows import WorkflowStatusItem, register_item_class


class AnonymiseWorkflowStatusItem(WorkflowStatusItem):
    description = _('Anonymisation')
    key = 'anonymise'
    category = 'formdata-action'
    mode = 'final'

    def migrate(self):
        changed = super().migrate()
        if getattr(self, 'unlink_user', False):  # 2023-06-13
            self.mode = 'unlink_user'
            self.unlink_user = None
            changed = True
        return changed

    def get_line_details(self):
        has_intermediate = get_publisher().has_site_option('enable-intermediate-anonymisation')
        labels = {
            'final': _('final') if has_intermediate else '',
            'intermediate': _('intermediate'),
            'unlink_user': _('only user unlinking'),
        }
        return labels.get(self.mode)

    def perform(self, formdata):
        if self.mode == 'unlink_user':
            if get_request() and formdata.is_submitter(get_request().user):
                get_session().mark_anonymous_formdata(formdata)
            formdata.unlink_user()
            formdata.remove_tracking_code()
        else:
            # self.mode is 'intermediate' or 'final'
            formdata.anonymise(self.mode)

    def get_parameters(self):
        return ('mode', 'condition')

    def get_mode_options(self):
        options = [
            ('final', _('Final'), 'final'),
            (
                'unlink_user',
                _('Only unlink user from the form/card. If existing the tracking code will be deleted.'),
                'unlink_user',
            ),
        ]
        if get_publisher().has_site_option('enable-intermediate-anonymisation'):
            options.insert(1, ('intermediate', _('Intermediate'), 'intermediate'))
        return options

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix, formdef, **kwargs)
        if 'mode' in parameters:
            form.add(
                RadiobuttonsWidget,
                '%smode' % prefix,
                title=_('Anonymisation type'),
                options=self.get_mode_options(),
                value=self.mode,
                default_value=self.__class__.mode,
            )

    def perform_in_tests(self, formdata):
        self.perform(formdata)
        formdata.anonymisation_performed = True


register_item_class(AnonymiseWorkflowStatusItem)

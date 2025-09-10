# w.c.s. - web application for online forms
# Copyright (C) 2005-2022  Entr'ouvert
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

from wcs.qommon import _
from wcs.qommon.form import CheckboxWidget
from wcs.tracking_code import TrackingCode
from wcs.workflows import WorkflowStatusItem, register_item_class


class RemoveTrackingCodeWorkflowStatusItem(WorkflowStatusItem):
    description = _('Remove Tracking Code')
    key = 'remove_tracking_code'
    category = 'formdata-action'

    replace = False

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        if 'replace' in parameters:
            form.add(
                CheckboxWidget,
                '%sreplace' % prefix,
                title=_('Replace with a new tracking code'),
                value=self.replace,
                hint=_('This only works if form supports tracking codes.'),
            )

    def get_line_details(self):
        if self.replace:
            return _('replace with a new one')

    def get_parameters(self):
        return ('replace', 'condition')

    def perform(self, formdata):
        if formdata.tracking_code:
            TrackingCode.remove_object(formdata.tracking_code)
        if self.replace and formdata.formdef.enable_tracking_codes:
            code = TrackingCode()
            code.formdata = formdata  # this will .store() code and formdata
        else:
            formdata.tracking_code = None
            formdata.store()


register_item_class(RemoveTrackingCodeWorkflowStatusItem)

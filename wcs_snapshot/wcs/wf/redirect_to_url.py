# w.c.s. - web application for online forms
# Copyright (C) 2005-2017  Entr'ouvert
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

from wcs.workflows import WorkflowStatusItem, register_item_class

from ..qommon import _
from ..qommon.form import ComputedExpressionWidget


class RedirectToUrlWorkflowStatusItem(WorkflowStatusItem):
    description = _('Web Redirection')
    key = 'redirect_to_url'
    category = 'formdata-action'
    endpoint = True
    support_substitution_variables = True

    url = None

    def get_line_details(self):
        if self.url:
            return _('to %s') % self.url
        return _('not configured')

    def get_parameters(self):
        return ('url', 'condition')

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        if 'url' in parameters:
            widget = form.add(
                ComputedExpressionWidget,
                '%surl' % prefix,
                title=_('URL'),
                value=self.url,
                hint=_('Common variables are available with the {{variable}} syntax.'),
            )
            widget.extra_css_class = 'grid-1-1'

    def get_computed_strings(self):
        yield from super().get_computed_strings()
        yield self.url

    def perform(self, formdata):
        if not self.url:
            # action not yet configured: don't redirect
            return
        url = self.compute(self.url)
        if not url:
            return  # don't redirect
        return url

    def perform_in_tests(self, formdata):
        url = self.perform(formdata)
        formdata.redirect_to_url = url
        return url


register_item_class(RedirectToUrlWorkflowStatusItem)

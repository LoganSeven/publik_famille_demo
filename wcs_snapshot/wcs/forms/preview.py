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

from quixote import get_publisher, get_response
from quixote.directory import AccessControlled, Directory
from quixote.html import TemplateIO, htmltext

from ..qommon import _, errors
from .root import FormPage


class PreviewFormPage(FormPage):
    _q_exports = ['', 'tempfile', 'live']
    preview_mode = True

    def check_access(self):
        pass

    def check_disabled(self):
        return False

    def create_form(self, *args, **kwargs):
        form = super().create_form(*args, **kwargs)
        form.attrs['data-autosave'] = 'false'
        return form

    def submitted(self, *args, **kwargs):
        get_response().set_title(self.formdef.name)
        r = TemplateIO(html=True)
        r += htmltext('<div class="warningnotice"><p>')
        r += str(_('This was only a preview: form was not actually submitted.'))
        r += htmltext(' <a href=".">%s</a>') % _('Start another preview.')
        r += htmltext('</p></div>')
        return r.getvalue()


class PreviewDirectory(AccessControlled, Directory):
    def _q_access(self):
        if not get_publisher().get_backoffice_root().is_accessible('forms'):
            raise errors.AccessForbiddenError()

    def _q_lookup(self, component):
        return PreviewFormPage(component)

# lingo - payment and billing system
# Copyright (C) 2022-2023  Entr'ouvert
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import ckeditor.views
import ckeditor.widgets
from django.forms.utils import flatatt
from django.template.loader import render_to_string
from django.utils.encoding import force_str
from django.utils.html import conditional_escape
from django.utils.safestring import mark_safe
from django.utils.translation import get_language


def ckeditor_render(self, name, value, attrs=None, renderer=None):
    if value is None:
        value = ''
    final_attrs = {'name': name}
    if getattr(self, 'attrs', None):
        final_attrs.update(self.attrs)
    if attrs:
        final_attrs.update(attrs)
    if not self.config.get('language'):
        self.config['language'] = get_language()

    # Force to text to evaluate possible lazy objects
    external_plugin_resources = [
        [force_str(a), force_str(b), force_str(c)] for a, b, c in self.external_plugin_resources
    ]

    return mark_safe(
        render_to_string(
            'ckeditor/widget.html',
            {
                'final_attrs': flatatt(final_attrs),
                'value': conditional_escape(force_str(value)),
                'id': final_attrs['id'],
                'config': ckeditor.widgets.json_encode(self.config),
                'external_plugin_resources': ckeditor.widgets.json_encode(external_plugin_resources),
            },
        )
    )


ckeditor.widgets.CKEditorWidget.render = ckeditor_render

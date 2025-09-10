# w.c.s. - web application for online forms
# Copyright (C) 2005-2024  Entr'ouvert
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


import json

from quixote import get_request, get_response
from quixote.html import htmltext

from wcs.qommon import _, template
from wcs.qommon.form import RichTextWidget


class DocumentableMixin:
    def get_documentable_button(self):
        return htmltext(template.render('wcs/backoffice/includes/documentation-editor-link.html', {}))

    def get_documentable_zone(self):
        return htmltext('<span class="actions">%s</span>') % template.render(
            'wcs/backoffice/includes/documentation.html',
            {'element': self.documented_element, 'object': self.documented_object},
        )

    def update_documentation(self):
        get_request().ignore_session = True
        get_response().set_content_type('application/json')
        try:
            content = get_request().json['content']
        except (KeyError, TypeError):
            return json.dumps({'err': 1})
        content = RichTextWidget('').clean_html(content) or None
        changed = False
        if content != self.documented_element.documentation:
            changed = True
            self.documented_element.documentation = content
            self.documented_object.store(_('Documentation update'))
        return json.dumps(
            {'err': 0, 'empty': not bool(self.documented_element.documentation), 'changed': changed}
        )


class DocumentableFieldMixin:
    def documentation_part(self):
        if not self.field.documentation:
            get_response().filter['sidebar_attrs'] = 'hidden'
        return template.render(
            'wcs/backoffice/includes/documentation.html',
            {'element': self.documented_element, 'object': self.documented_object},
        )

# lingo - payment and billing system
# Copyright (C) 2023  Entr'ouvert
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

from django.http import HttpResponse
from weasyprint import HTML

from lingo.utils.pdf import write_pdf


class PDFMixin:
    def html(self):
        return self.object.html()

    def get_filename(self):
        return self.object.formatted_number

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        result = self.html()
        if 'html' in request.GET:
            return HttpResponse(result)
        html = HTML(string=result)
        pdf = write_pdf(html)
        response = HttpResponse(pdf, content_type='application/pdf')
        if 'inline' not in request.GET:
            response['Content-Disposition'] = 'attachment; filename="%s.pdf"' % self.get_filename()
        return response

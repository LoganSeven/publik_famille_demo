# lingo - payment and billing system
# Copyright (C) 2022-2025  Entr'ouvert
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

import datetime
import decimal
import xml.etree.ElementTree as ET
import zipfile

from django.template.defaultfilters import yesno
from django.utils.formats import date_format, get_format
from django.utils.translation import gettext_lazy as _

NS = {
    'fo': 'urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0',
    'office': 'urn:oasis:names:tc:opendocument:xmlns:office:1.0',
    'style': 'urn:oasis:names:tc:opendocument:xmlns:style:1.0',
    'number': 'urn:oasis:names:tc:opendocument:xmlns:datastyle:1.0',
    'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
    'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
    'xlink': 'http://www.w3.org/1999/xlink',
}

for prefix, uri in NS.items():
    ET.register_namespace(prefix, uri)


class Workbook:
    def __init__(self, encoding='utf-8'):
        self.sheets = []
        self.encoding = encoding

    def add_sheet(self, name):
        sheet = WorkSheet(self, name)
        self.sheets.append(sheet)
        return sheet

    def writerow(self, values, **kwargs):
        if not self.sheets:
            self.add_sheet(_('Sheet 1'))
        self.sheets[-1].writerow(values, **kwargs)

    def get_content_node(self):
        root = ET.Element('{%s}document-content' % NS['office'])
        root.attrib['{%s}version' % NS['office']] = '1.4'
        ET.SubElement(root, '{%s}scripts' % NS['office'])
        ET.SubElement(root, '{%s}font-face-decls' % NS['office'])
        automatic_styles_node = ET.SubElement(root, '{%s}automatic-styles' % NS['office'])

        body = ET.SubElement(root, '{%s}body' % NS['office'])
        spreadsheet = ET.SubElement(body, '{%s}spreadsheet' % NS['office'])
        for sheet in self.sheets:
            spreadsheet.append(sheet.get_node(automatic_styles_node=automatic_styles_node))
        return root

    def get_styles_node(self):
        root = ET.Element('{%s}document-styles' % NS['office'])
        root.attrib['{%s}version' % NS['office']] = '1.4'
        ET.SubElement(root, '{%s}font-face-decls' % NS['office'])
        automatic_styles = ET.SubElement(root, '{%s}styles' % NS['office'])
        style = ET.SubElement(automatic_styles, '{%s}style' % NS['style'])
        style.attrib['{%s}name' % NS['style']] = 'Default'
        style.attrib['{%s}family' % NS['style']] = 'table-cell'

        def define_date_style(name, format_string):
            node = ET.SubElement(automatic_styles, '{%s}date-style' % NS['number'])
            node.attrib['{%s}name' % NS['style']] = name + 'NumberFormat'
            char_map = {
                'Y': ('year', {'style': 'long'}),
                'y': ('year', {'style': 'short'}),
                'F': ('month', {'style': 'long', 'textual': 'true'}),
                'M': ('month', {'style': 'short', 'textual': 'true'}),
                'N': ('month', {'style': 'short', 'textual': 'true'}),
                'm': ('month', {'style': 'long', 'textual': 'false'}),
                'n': ('month', {'style': 'short', 'textual': 'false'}),
                'd': ('day', {'style': 'long'}),
                'j': ('day', {'style': 'short'}),
                'l': ('day-of-week', {'style': 'long'}),
                'D': ('day-of-week', {'style': 'short'}),
                'H': ('hours', {'style': 'long'}),
                'G': ('hours', {'style': 'short'}),
                'i': ('minutes', {'style': 'long'}),
                's': ('seconds', {'style': 'long'}),
                'a': ('am-pm', {}),
            }

            for char in format_string:
                if char in char_map:
                    elem, attrs = char_map.get(char)
                    subnode = ET.SubElement(node, '{%s}%s' % (NS['number'], elem))
                    for k, v in attrs.items():
                        subnode.attrib['{%s}%s' % (NS['number'], k)] = v
                else:
                    ET.SubElement(node, '{%s}text' % NS['number']).text = char

            style = ET.SubElement(automatic_styles, '{%s}style' % NS['style'])
            style.attrib['{%s}name' % NS['style']] = name
            style.attrib['{%s}family' % NS['style']] = 'table-cell'
            style.attrib['{%s}data-style-name' % NS['style']] = name + 'NumberFormat'
            style.attrib['{%s}parent-style-name' % NS['style']] = 'Default'

        define_date_style('Date', get_format('SHORT_DATE_FORMAT'))
        define_date_style('DateTime', get_format('SHORT_DATETIME_FORMAT'))

        style = ET.SubElement(automatic_styles, '{%s}style' % NS['style'])
        style.attrib['{%s}name' % NS['style']] = 'TableHeader'
        style.attrib['{%s}family' % NS['style']] = 'table-cell'
        style.attrib['{%s}parent-style-name' % NS['style']] = 'Default'
        text_props = ET.SubElement(style, '{%s}text-properties' % NS['style'])
        text_props.attrib['{%s}font-weight' % NS['fo']] = 'bold'

        return root

    def get_styles(self):
        tree = self.get_styles_node()
        ET.indent(tree)
        return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(tree, 'utf-8') + b'\n'

    def get_content(self):
        tree = self.get_content_node()
        ET.indent(tree)
        return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(tree, 'utf-8') + b'\n'

    def save(self, output):
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as z:
            # mimetype must be written first and with no extra attributes,
            # hence the use of zipfile.ZipInfo
            z.writestr(
                zipfile.ZipInfo('mimetype', date_time=datetime.datetime.now().timetuple()[:6]),
                'application/vnd.oasis.opendocument.spreadsheet',
                compress_type=zipfile.ZIP_STORED,
            )
            z.writestr('content.xml', self.get_content())
            z.writestr('styles.xml', self.get_styles())
            z.writestr(
                'META-INF/manifest.xml',
                '''<?xml version="1.0" encoding="UTF-8"?>
    <manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" manifest:version="1.4">
     <manifest:file-entry manifest:full-path="/" manifest:version="1.4" manifest:media-type="application/vnd.oasis.opendocument.spreadsheet"/>
     <manifest:file-entry manifest:full-path="styles.xml" manifest:media-type="text/xml"/>
     <manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
    </manifest:manifest>''',
            )


class WorkSheet:
    def __init__(self, workbook, name):
        self.rows = []
        self.row_attribs = []
        self.name = name
        self.workbook = workbook
        self.extra_styles = {}

    def writerow(self, values, **kwargs):
        self.rows.append(values)
        self.row_attribs.append(kwargs)

    def get_node(self, automatic_styles_node):
        if not self.rows:
            # empty file, create a spreadsheet with a single empty row
            self.rows.append([''])
            self.row_attribs.append([''])

        root = ET.Element('{%s}table' % NS['table'])
        root.attrib['{%s}name' % NS['table']] = self.name

        self.max_columns = max(len(x) for x in self.rows)
        for i in range(self.max_columns):
            column = ET.SubElement(root, '{%s}table-column' % NS['table'])
            column.attrib['{%s}style-name' % NS['table']] = f'co{i + 1}'
            style_node = ET.SubElement(automatic_styles_node, '{%s}style' % NS['style'])
            style_node.attrib['{%s}name' % NS['style']] = f'co{i + 1}'
            style_node.attrib['{%s}family' % NS['style']] = 'table-column'
            column_props = ET.SubElement(style_node, '{%s}table-column-properties' % NS['style'])
            column_props.attrib['{%s}column-width' % NS['style']] = '4cm'

        for row, row_attribs in zip(self.rows, self.row_attribs):
            row_node = ET.SubElement(root, '{%s}table-row' % NS['table'])
            if not row:
                # no columns here, add a single empty cell
                ET.SubElement(row_node, '{%s}table-cell' % NS['table'])
                continue
            for cell in row:
                cell_node = ET.SubElement(row_node, '{%s}table-cell' % NS['table'])
                cell_node.attrib['{%s}value-type' % NS['office']] = 'string'
                p = ET.SubElement(cell_node, '{%s}p' % NS['text'])

                if isinstance(cell, datetime.date):
                    p.text = date_format(cell, 'SHORT_DATE_FORMAT')
                    cell_node.attrib['{%s}value-type' % NS['office']] = 'date'
                    cell_node.attrib['{%s}date-value' % NS['office']] = cell.strftime('%Y-%m-%d')
                    cell_node.attrib['{%s}style-name' % NS['table']] = 'Date'
                elif isinstance(cell, datetime.datetime):
                    p.text = date_format(cell, 'SHORT_DATETIME_FORMAT')
                    cell_node.attrib['{%s}value-type' % NS['office']] = 'date'
                    cell_node.attrib['{%s}date-value' % NS['office']] = cell.strftime('%Y-%m-%dT%H:%M:%S')
                    cell_node.attrib['{%s}style-name' % NS['table']] = 'DateTime'
                elif isinstance(cell, bool):
                    p.text = yesno(cell)
                    cell_node.attrib['{%s}value-type' % NS['office']] = 'boolean'
                    cell_node.attrib['{%s}value' % NS['office']] = str(cell).lower()
                elif isinstance(cell, (int, float, decimal.Decimal)):
                    p.text = str(cell)
                    cell_node.attrib['{%s}value-type' % NS['office']] = 'float'
                    cell_node.attrib['{%s}value' % NS['office']] = str(cell)
                else:
                    p.text = str(cell if cell is not None else '')

                if row_attribs.get('headers'):
                    cell_node.attrib['{%s}style-name' % NS['table']] = 'TableHeader'

        return root

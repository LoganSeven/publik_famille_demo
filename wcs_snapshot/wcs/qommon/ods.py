# w.c.s. - web application for online forms
# Copyright (C) 2005-2013  Entr'ouvert
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

import datetime
import re
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

from django.utils.encoding import force_str

from .evalutils import make_date, make_datetime
from .form import ValidationWidget
from .misc import date_format, datetime_format, strftime

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


def clean_text(value):
    for i in range(0x20):  # remove control characters
        char = chr(i)
        if char in ('\t', '\r', '\n'):
            # only allow tab, carriage return and line feed.
            continue
        value = value.replace(char, '')
    # fffe and ffff are also invalid characters
    return value.replace('\ufffe', '').replace('\uffff', '')


def is_number(value):
    if value and value.strip() != '0' and value.startswith(('0', '+')):
        # avoid phone numbers
        return False
    if isinstance(value, str):
        value = value.strip()
        # replace comma by dot, to handle decimal separator
        value = value.replace(',', '.', 1)
        if not re.match(r'^\d+(\.\d+)?$', value):
            # check we have digits and an optional decimal separator.
            # this avoids _ used as a number separator (ex: 1_000_000) to be
            # accepted.
            return False
    try:
        float(value)
    except ValueError:
        return False
    return True


def is_digits_but_not_number(field):
    validation_type = (getattr(field, 'validation', None) or {}).get('type')
    validation_properties = ValidationWidget.validation_methods.get(validation_type)
    return bool(
        validation_properties and validation_properties.get('display_as_string_in_spreadsheets') is True
    )


def get_as_string_number(value):
    # to be called after value has been checked by is_number()
    if isinstance(value, str):
        # replace comma by dot, to handle decimal separator
        return value.replace(',', '.', 1).strip()
    return str(value)


class Workbook:
    def __init__(self, encoding='utf-8'):
        self.sheets = []
        self.encoding = encoding

    def add_sheet(self, name):
        sheet = WorkSheet(self, name)
        self.sheets.append(sheet)
        return sheet

    def get_content_node(self):
        root = ET.Element('{%s}document-content' % NS['office'])
        root.attrib['{%s}version' % NS['office']] = '1.4'
        ET.SubElement(root, '{%s}scripts' % NS['office'])
        ET.SubElement(root, '{%s}font-face-decls' % NS['office'])

        body = ET.SubElement(root, '{%s}body' % NS['office'])
        spreadsheet = ET.SubElement(body, '{%s}spreadsheet' % NS['office'])
        for sheet in self.sheets:
            spreadsheet.append(sheet.get_node())
        return root

    def get_styles_node(self):
        root = ET.Element('{%s}document-styles' % NS['office'])
        root.attrib['{%s}version' % NS['office']] = '1.4'
        ET.SubElement(root, '{%s}font-face-decls' % NS['office'])
        automatic_styles = ET.SubElement(root, '{%s}styles' % NS['office'])

        style = ET.SubElement(automatic_styles, '{%s}style' % NS['style'])
        style.attrib['{%s}name' % NS['style']] = 'Default'
        style.attrib['{%s}family' % NS['style']] = 'table-cell'

        def define_date_style(name, strftime_string):
            node = ET.SubElement(automatic_styles, '{%s}date-style' % NS['number'])
            node.attrib['{%s}name' % NS['style']] = name + 'NumberFormat'
            for part in re.findall(r'%?.', strftime_string):
                if part == '%Y':
                    ET.SubElement(node, '{%s}year' % NS['number']).attrib['{%s}style' % NS['number']] = 'long'
                elif part == '%m':
                    ET.SubElement(node, '{%s}month' % NS['number']).attrib[
                        '{%s}style' % NS['number']
                    ] = 'long'
                elif part == '%d':
                    ET.SubElement(node, '{%s}day' % NS['number']).attrib['{%s}style' % NS['number']] = 'long'
                elif part == '%H':
                    ET.SubElement(node, '{%s}hours' % NS['number']).attrib[
                        '{%s}style' % NS['number']
                    ] = 'long'
                elif part == '%M':
                    ET.SubElement(node, '{%s}minutes' % NS['number']).attrib[
                        '{%s}style' % NS['number']
                    ] = 'long'
                elif part == '%S':
                    ET.SubElement(node, '{%s}seconds' % NS['number']).attrib[
                        '{%s}style' % NS['number']
                    ] = 'long'
                else:
                    ET.SubElement(node, '{%s}text' % NS['number']).text = part

            style = ET.SubElement(automatic_styles, '{%s}style' % NS['style'])
            style.attrib['{%s}name' % NS['style']] = name
            style.attrib['{%s}family' % NS['style']] = 'table-cell'
            style.attrib['{%s}data-style-name' % NS['style']] = name + 'NumberFormat'
            style.attrib['{%s}parent-style-name' % NS['style']] = 'Default'

            style = ET.SubElement(automatic_styles, '{%s}style' % NS['style'])
            style.attrib['{%s}name' % NS['style']] = name + 'Column'
            style.attrib['{%s}family' % NS['style']] = 'table-column'
            ET.SubElement(style, '{%s}table-column-properties' % NS['style']).attrib[
                '{%s}column-width' % NS['style']
            ] = '80mm'

        define_date_style('Date', date_format())
        define_date_style('DateTime', datetime_format())

        for sheet in self.sheets:
            for extra_style_name, extra_style_props in sheet.extra_styles.items():
                if not extra_style_props:
                    continue
                node = ET.SubElement(automatic_styles, '{%s}style' % NS['style'])
                node.attrib['{%s}name' % NS['style']] = extra_style_name
                node.attrib['{%s}family' % NS['style']] = 'table-cell'
                cell_props = ET.SubElement(node, '{%s}table-cell-properties' % NS['style'])
                if 'background-color' in extra_style_props:
                    cell_props.attrib['{%s}background-color' % NS['fo']] = extra_style_props.get(
                        'background-color'
                    )
                text_props = ET.SubElement(node, '{%s}text-properties' % NS['style'])
                if 'color' in extra_style_props:
                    text_props.attrib['{%s}color' % NS['fo']] = extra_style_props.get('color')

        return root

    def get_styles(self):
        return ET.tostring(self.get_styles_node(), 'utf-8')

    def get_content(self):
        return ET.tostring(self.get_content_node(), 'utf-8')

    def save(self, output):
        with zipfile.ZipFile(output, 'w') as z:
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
        self.cells = {}
        self.name = name
        self.workbook = workbook
        self.extra_styles = {}

    def write(self, row, column, value, **kwargs):
        if row not in self.cells:
            self.cells[row] = {}
        self.cells[row][column] = WorkCell(value, **kwargs)

    def get_node(self):
        root = ET.Element('{%s}table' % NS['table'])
        root.attrib['{%s}name' % NS['table']] = self.name
        ET.SubElement(root, '{%s}table-column' % NS['table'])
        if not self.cells.keys():
            # empty file, create a spreadsheet with a single empty row
            self.cells[0] = {}
        for i in range(0, max(self.cells.keys()) + 1):
            row = ET.SubElement(root, '{%s}table-row' % NS['table'])
            if not self.cells.get(i).keys():
                # no columns here, add a single empty cell
                ET.SubElement(row, '{%s}table-cell' % NS['table'])
                continue
            for j in range(0, max(self.cells.get(i).keys()) + 1):
                cell = self.cells.get(i, {}).get(j, None)
                if not cell:
                    ET.SubElement(row, '{%s}table-cell' % NS['table'])
                else:
                    cell_node = cell.get_node()
                    style_name = cell.get_style_name()
                    if style_name:
                        cell_node.attrib['{%s}style-name' % NS['table']] = style_name
                        self.extra_styles[style_name] = cell.get_style_properties()
                    row.append(cell_node)
        return root


class WorkCell:
    value_type = None
    native_value = None
    url = None
    style_name = None
    style_properties = None

    def __init__(self, value, formdata=None, data_field=None, native_value=None):
        if value is None:
            value = ''
        self.value = clean_text(force_str(value, 'utf-8')).strip()

        if not data_field:
            return

        if not native_value:
            return

        if data_field.key == 'file':
            self.value_type = 'file'
            self.value = native_value
            self.url = '%sfiles/%s/%s' % (
                formdata.get_url(backoffice=True),
                data_field.id,
                urllib.parse.quote(native_value),
            )
        elif data_field.key in ('time', 'last_update_time'):
            self.value_type = 'datetime'
            self.native_value = strftime('%Y-%m-%dT%H:%M:%S', make_datetime(native_value))
            self.value = strftime(datetime_format(), make_datetime(native_value))
        elif data_field.key == 'date':
            self.value_type = 'date'
            self.native_value = strftime('%Y-%m-%d', make_date(native_value))
            self.value = strftime(date_format(), make_datetime(native_value))
        elif is_number(self.value) and not is_digits_but_not_number(data_field):
            self.value_type = 'float'
            self.native_value = get_as_string_number(self.value)

        if hasattr(native_value, 'get_ods_style_name'):
            self.style_name = native_value.get_ods_style_name()
            self.style_properties = {
                'background-color': native_value.get_ods_style_bg_colour(),
                'color': native_value.get_ods_style_fg_colour(),
            }

    def get_node(self):
        root = ET.Element('{%s}table-cell' % NS['table'])
        root.attrib['{%s}value-type' % NS['office']] = 'string'
        p = ET.SubElement(root, '{%s}p' % NS['text'])
        p.text = self.value

        if self.value_type == 'file':
            a = ET.SubElement(p, '{%s}a' % NS['text'])
            a.attrib['{%s}href' % NS['xlink']] = self.url
            a.text = self.value
            p.text = None
        elif self.value_type in ('date', 'datetime'):
            root.attrib['{%s}value-type' % NS['office']] = 'date'
            root.attrib['{%s}date-value' % NS['office']] = self.native_value
        elif self.value_type == 'float':
            root.attrib['{%s}value-type' % NS['office']] = 'float'
            root.attrib['{%s}value' % NS['office']] = self.native_value

        return root

    def get_style_name(self):
        if self.value_type == 'datetime':
            return 'DateTime'
        if self.value_type == 'date':
            return 'Date'
        return self.style_name

    def get_style_properties(self):
        return self.style_properties

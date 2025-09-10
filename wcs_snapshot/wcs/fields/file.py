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

import base64
import os
import urllib.parse
import xml.etree.ElementTree as ET

from django.utils.encoding import force_bytes, force_str
from quixote import get_publisher, get_request
from quixote.html import TemplateIO, htmltag, htmltext

from wcs.qommon import _, get_cfg, misc
from wcs.qommon.form import CheckboxWidget, FileSizeWidget, FileWithPreviewWidget, SingleSelectWidget
from wcs.qommon.misc import ellipsize, get_document_type_value_options
from wcs.qommon.ods import NS as OD_NS
from wcs.qommon.ods import clean_text as od_clean_text
from wcs.qommon.upload_storage import PicklableUpload

from .base import WidgetField, register_field_class


class FileField(WidgetField):
    key = 'file'
    description = _('File Upload')
    allow_complex = True

    document_type = None
    max_file_size = None
    automatic_image_resize = True
    allow_portfolio_picking = False
    storage = 'default'

    widget_class = FileWithPreviewWidget
    extra_attributes = [
        'file_type',
        'max_file_size',
        'allow_portfolio_picking',
        'automatic_image_resize',
        'storage',
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.document_type = self.document_type or {}

    @property
    def file_type(self):
        file_type = (self.document_type or {}).get('mimetypes', [])
        default_file_type_id = get_cfg('misc', {}).get('default_file_type')
        if not file_type and default_file_type_id:
            filetypes_cfg = get_cfg('filetypes', {})
            file_type = filetypes_cfg.get(default_file_type_id, {}).get('mimetypes')
        return file_type

    def fill_admin_form(self, form, formdef):
        super().fill_admin_form(form, formdef)
        options = get_document_type_value_options(self.document_type)
        form.add(
            SingleSelectWidget,
            'document_type',
            title=_('File type suggestion'),
            value=self.document_type,
            options=options,
            advanced=True,
        )
        form.add(
            FileSizeWidget,
            'max_file_size',
            title=_('Max file size'),
            value=self.max_file_size,
            advanced=True,
        )
        form.add(
            CheckboxWidget,
            'automatic_image_resize',
            title=_('Automatically resize uploaded images'),
            value=self.automatic_image_resize,
            advanced=True,
            default_value=self.__class__.automatic_image_resize,
        )

        from wcs.portfolio import has_portfolio

        if has_portfolio():
            form.add(
                CheckboxWidget,
                'allow_portfolio_picking',
                title=_('Allow user to pick a file from a portfolio'),
                value=self.allow_portfolio_picking,
                advanced=True,
            )
        storages = get_publisher().get_site_storages()
        if storages:
            storage_options = [('default', '---', {})]
            storage_options += [(key, value['label'], key) for key, value in storages.items()]
            form.add(
                SingleSelectWidget,
                'storage',
                title=_('File storage system'),
                value=self.storage,
                options=storage_options,
                advanced=True,
            )

    def get_admin_attributes(self):
        return WidgetField.get_admin_attributes(self) + [
            'document_type',
            'max_file_size',
            'allow_portfolio_picking',
            'automatic_image_resize',
            'storage',
        ]

    @classmethod
    def convert_value_from_anything(cls, value):
        if not value:
            return None
        from wcs.variables import LazyFieldVarFile

        if isinstance(value, LazyFieldVarFile):
            value = value.get_value()  # unbox
        if hasattr(value, 'base_filename'):
            upload = PicklableUpload(value.base_filename, value.content_type or 'application/octet-stream')
            if hasattr(value, 'get_content'):
                upload.receive([value.get_content()])
            else:
                # native quixote Upload object
                upload.receive([value.fp.read()])
                value.fp.seek(0)
            return upload
        from wcs.workflows import NamedAttachmentsSubstitutionProxy

        if isinstance(value, NamedAttachmentsSubstitutionProxy):
            upload = PicklableUpload(value.filename, value.content_type)
            upload.receive([value.content])
            return upload

        value = misc.unlazy(value)
        if isinstance(value, str) and urllib.parse.urlparse(value).scheme in ('http', 'https'):
            try:
                response, dummy, data, dummy = misc.http_get_page(value, raise_on_http_errors=True)
            except misc.ConnectionError:
                pass
            else:
                value = {
                    'filename': os.path.basename(urllib.parse.urlparse(value).path) or _('file.bin'),
                    'content': data,
                    'content_type': response.headers.get('content-type'),
                }

        if isinstance(value, dict):
            # if value is a dictionary we expect it to have a content or
            # b64_content key and a filename keys and an optional
            # content_type key.
            if 'b64_content' in value:
                value_content = base64.decodebytes(force_bytes(value['b64_content']))
            elif 'content' in value and value.get('content_is_base64'):
                value_content = base64.decodebytes(force_bytes(value['content']))
            else:
                value_content = value.get('content')
            if 'filename' in value and value_content:
                content_type = value.get('content_type') or 'application/octet-stream'
                if content_type.startswith('text/'):
                    charset = 'utf-8'
                else:
                    charset = None
                upload = PicklableUpload(value['filename'], content_type, charset)
                upload.receive([force_bytes(value_content)])
                return upload
        raise ValueError('invalid data for file type (%r)' % value)

    def get_view_short_value(self, value, max_len=30, **kwargs):
        return self.get_view_value(value, include_image_thumbnail=False, max_len=max_len, **kwargs)

    def get_prefill_value(self, user=None, force_string=True):
        return super().get_prefill_value(user=user, force_string=False)

    def get_rst_view_value(self, value, indent=''):
        return indent + str(value or '')

    def get_download_query_string(self, **kwargs):
        if kwargs.get('hash'):
            return 'hash=%s' % kwargs.get('hash')
        if kwargs.get('parent_field'):
            return 'f=%s$%s$%s' % (kwargs['parent_field'].id, kwargs['parent_field_index'], self.id)
        if kwargs.get('file_value'):
            return 'hash=%s' % kwargs.get('file_value').file_digest()
        return 'f=%s' % self.id

    def get_value_info(self, data, wf_form=False):
        value, value_details = super().get_value_info(data)
        if wf_form and value:
            value_details['hash'] = value.file_digest()
        return (value, value_details)

    def get_view_value(self, value, include_image_thumbnail=True, max_len=None, **kwargs):
        show_link = True
        if not hasattr(value, 'has_redirect_url'):  # wrong type
            return ''
        if value.has_redirect_url():
            is_in_backoffice = bool(get_request() and get_request().is_in_backoffice())
            show_link = bool(value.get_redirect_url(backoffice=is_in_backoffice))
        t = TemplateIO(html=True)
        t += htmltext('<div class="file-field">')
        if show_link or include_image_thumbnail:
            download_qs = self.get_download_query_string(**kwargs)
        if show_link:
            attrs = {
                'href': '[download]?%s' % download_qs,
            }
            if kwargs.get('label_id'):
                attrs['aria-describedby'] = kwargs.get('label_id')
            if max_len:
                attrs['title'] = value
            t += htmltag('a', **attrs)
        if include_image_thumbnail and value.can_thumbnail():
            t += htmltext('<img alt="" src="[download]?%s&thumbnail=1"/>') % download_qs
        filename = str(value)
        if max_len and len(filename) > max_len:
            basename, ext = os.path.splitext(filename)
            basename = ellipsize(basename, max_len - 5)
            filename = basename + ext
        t += htmltext('<span>%s</span>') % filename
        if show_link:
            t += htmltext('</a>')
        t += htmltext(value.get_view_clamd_status())
        t += htmltext('</div>')
        return t.getvalue()

    def get_download_url(self, formdata, **kwargs):
        return '%s?%s' % (formdata.get_file_base_url(), self.get_download_query_string(**kwargs))

    def get_opendocument_node_value(self, value, formdata=None, **kwargs):
        show_link = True
        if value.has_redirect_url():
            is_in_backoffice = bool(get_request() and get_request().is_in_backoffice())
            show_link = bool(value.get_redirect_url(backoffice=is_in_backoffice))
        if show_link and formdata:
            node = ET.Element('{%s}a' % OD_NS['text'])
            node.attrib['{%s}href' % OD_NS['xlink']] = self.get_download_url(formdata, **kwargs)
        else:
            node = ET.Element('{%s}span' % OD_NS['text'])
        node.text = od_clean_text(force_str(value))
        return node

    def get_csv_value(self, element, **kwargs):
        return [str(element) if element else '']

    def get_json_value(self, value, formdata=None, include_file_content=True, **kwargs):
        out = value.get_json_value(include_file_content=include_file_content)
        if formdata:
            out['url'] = self.get_download_url(formdata, file_value=value, **kwargs)
            if value and misc.can_thumbnail(value.content_type):
                out['thumbnail_url'] = out['url'] + '&thumbnail=1'
        out['field_id'] = self.id
        return out

    def from_json_value(self, value):
        if value and 'filename' in value and 'content' in value:
            try:
                content = base64.b64decode(value['content'])
            except ValueError:
                return None
            content_type = value.get('content_type', 'application/octet-stream')
            if content_type.startswith('text/'):
                charset = 'utf-8'
            else:
                charset = None
            upload = PicklableUpload(value['filename'], content_type, charset)
            upload.receive([content])
            return upload
        return None

    def perform_more_widget_changes(self, form, kwargs, edit=True):
        if not edit:
            value = get_request().get_field(self.field_key)
            if value and hasattr(value, 'token'):
                get_request().form[self.field_key + '$token'] = value.token

    def export_to_xml(self, include_id=False):
        # convert some sub-fields to strings as export_to_xml() only supports
        # dictionnaries with strings values
        if self.document_type and self.document_type.get('mimetypes'):
            old_value = self.document_type['mimetypes']
            self.document_type['mimetypes'] = '|'.join(self.document_type['mimetypes'])
        result = super().export_to_xml(include_id=include_id)
        if self.document_type and self.document_type.get('mimetypes'):
            self.document_type['mimetypes'] = old_value
        return result

    def init_with_xml(self, elem, include_id=False, snapshot=False):
        super().init_with_xml(elem, include_id=include_id)
        # translate fields flattened to strings
        if self.document_type and self.document_type.get('mimetypes'):
            self.document_type['mimetypes'] = self.document_type['mimetypes'].split('|')
        if self.document_type and self.document_type.get('fargo'):
            self.document_type['fargo'] = self.document_type['fargo'] == 'True'


register_field_class(FileField)

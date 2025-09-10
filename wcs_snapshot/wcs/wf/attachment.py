# w.c.s. - web application for online forms
# Copyright (C) 2005-2010  Entr'ouvert
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

import os
import urllib.parse
import xml.etree.ElementTree as ET

from quixote import get_request, redirect

from wcs.clamd import AccessForbiddenMalwareError, add_clamd_scan_job
from wcs.forms.common import FileDirectory, FormStatusPage
from wcs.portfolio import has_portfolio, push_document
from wcs.workflows import AttachmentEvolutionPart, WorkflowStatusItem, register_item_class

from ..qommon import _
from ..qommon.errors import TraversalError
from ..qommon.form import (
    CheckboxWidget,
    FileSizeWidget,
    FileWithPreviewWidget,
    SingleSelectWidget,
    StringWidget,
    VarnameWidget,
    WidgetList,
    WysiwygTextWidget,
)
from ..qommon.misc import get_document_type_value_options, xml_node_text


def lookup_wf_attachment(self, filename):
    # supports for URLs such as /$formdata/$id/files/attachment/test.txt
    # and /$formdata/$id/files/attachment-$file-reference/test.txt
    if self.reference.split('-')[0] != 'attachment':
        return
    if '-' in self.reference:
        file_reference = self.reference.split('-', 1)[1]
    else:
        file_reference = None

    filenames = [filename, urllib.parse.unquote(filename)]
    for p in self.formdata.iter_evolution_parts(AttachmentEvolutionPart):
        if file_reference and os.path.basename(p.filename or '') != file_reference:
            continue
        if (p.base_filename or '') in filenames:
            return p


def form_attachment(self):
    self.check_receiver()

    try:
        fn = get_request().form['f']
    except (KeyError, ValueError):
        raise TraversalError()

    for evo in self.filled.evolution:
        if evo.parts:
            for p in evo.parts:
                if not isinstance(p, AttachmentEvolutionPart):
                    continue
                if os.path.basename(p.filename) == fn:
                    if not p.allow_download(self.filled):
                        raise AccessForbiddenMalwareError(p)
                    is_in_backoffice = bool(get_request() and get_request().is_in_backoffice())
                    return redirect(
                        '%sfiles/attachment-%s/%s'
                        % (
                            self.filled.get_url(backoffice=is_in_backoffice),
                            fn,
                            urllib.parse.quote(p.base_filename or ''),
                        )
                    )

    raise TraversalError()


class AddAttachmentWorkflowStatusItem(WorkflowStatusItem):
    description = _('Attachment')
    key = 'addattachment'
    category = 'interaction'
    endpoint = False
    waitpoint = True
    ok_in_global_action = True

    title = None
    display_title = True
    button_label = None
    display_button = True
    required = False
    hint = None
    by = []
    backoffice_info_text = None
    varname = None
    backoffice_filefield_id = None
    attach_to_history = True  # legacy choice
    allow_portfolio_picking = False
    push_to_portfolio = False
    document_type = None
    max_file_size = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.document_type = self.document_type or {}

    @classmethod
    def init(cls):
        FormStatusPage._q_extra_exports.append('attachment')
        FormStatusPage.attachment = form_attachment
        if 'lookup_wf_attachment' not in FileDirectory._lookup_methods:
            FileDirectory._lookup_methods.append('lookup_wf_attachment')
            FileDirectory.lookup_wf_attachment = lookup_wf_attachment

    def get_line_details(self):
        if self.by:
            return _('by %s') % self.render_list_of_roles(self.by)
        return _('not completed')

    def is_interactive(self):
        return True

    def fill_form(self, form, formdata, user, **kwargs):
        if self.display_title:
            title = self.title or _('Upload File')
        else:
            title = None
        file_type = (self.document_type or {}).get('mimetypes')
        form.add(
            FileWithPreviewWidget,
            'attachment%s' % self.id,
            title=title,
            required=self.required,
            hint=self.hint,
            file_type=file_type,
            max_file_size=self.max_file_size,
            allow_portfolio_picking=self.allow_portfolio_picking,
        )
        if self.display_button:
            form.add_submit('button%s' % self.id, self.button_label or _('Upload File'))
            form.get_widget('button%s' % self.id).backoffice_info_text = self.backoffice_info_text
            form.get_widget('button%s' % self.id).action_id = self.id

    def submit_form(self, form, formdata, user, evo):
        if form.get_widget('attachment%s' % self.id):
            f = form.get_widget('attachment%s' % self.id).parse()
            if f is None:
                if self.required:
                    form.set_error('attachment%s' % self.id, _('Missing file'))
                return
            outstream = f.fp
            filename = f.base_filename
            content_type = f.content_type or 'application/octet-stream'
            if self.push_to_portfolio:
                push_document(formdata.get_user(), filename, outstream)
            if self.backoffice_filefield_id:
                outstream.seek(0)
                self.store_in_backoffice_filefield(
                    formdata, self.backoffice_filefield_id, filename, content_type, outstream.read()
                )
            f.fp.seek(0)
            evo_part = AttachmentEvolutionPart.from_upload(f, varname=self.varname)
            evo_part.display_in_history = self.attach_to_history
            evo.add_part(evo_part)
            add_clamd_scan_job(formdata)

    def get_parameters(self):
        parameters = (
            'by',
            'required',
            'title',
            'display_title',
            'button_label',
            'display_button',
            'hint',
            'backoffice_info_text',
            'backoffice_filefield_id',
            'varname',
            'attach_to_history',
            'document_type',
            'max_file_size',
        )
        if has_portfolio():
            parameters += (
                'allow_portfolio_picking',
                'push_to_portfolio',
            )
        parameters += ('condition',)
        return parameters

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        if 'by' in parameters:
            form.add(
                WidgetList,
                '%sby' % prefix,
                title=_('By'),
                element_type=SingleSelectWidget,
                value=self.by,
                add_element_label=self.get_add_role_label(),
                element_kwargs={
                    'render_br': False,
                    'options': [(None, '---', None)] + self.get_list_of_roles(),
                },
            )
        if 'required' in parameters:
            form.add(CheckboxWidget, '%srequired' % prefix, title=_('Required'), value=self.required)
        if 'title' in parameters:
            form.add(
                StringWidget,
                '%stitle' % prefix,
                size=40,
                title=_('Title'),
                value=self.title or _('Upload File'),
            )
        if 'display_title' in parameters:
            form.add(
                CheckboxWidget, '%sdisplay_title' % prefix, title=_('Display Title'), value=self.display_title
            )
        if 'button_label' in parameters:
            form.add(
                StringWidget,
                '%sbutton_label' % prefix,
                title=_('Button Label'),
                value=self.button_label or _('Upload File'),
            )
        if 'display_button' in parameters:
            form.add(
                CheckboxWidget,
                '%sdisplay_button' % prefix,
                title=_('Display Button'),
                value=self.display_button,
            )
        if 'hint' in parameters:
            form.add(StringWidget, '%shint' % prefix, size=40, title=_('Hint'), value=self.hint)
        if 'backoffice_info_text' in parameters:
            form.add(
                WysiwygTextWidget,
                '%sbackoffice_info_text' % prefix,
                title=_('Information Text for Backoffice'),
                value=self.backoffice_info_text,
            )
        if 'backoffice_filefield_id' in parameters:
            options = self.get_backoffice_filefield_options()
            if options:
                form.add(
                    SingleSelectWidget,
                    '%sbackoffice_filefield_id' % prefix,
                    title=_('Store in a backoffice file field'),
                    value=self.backoffice_filefield_id,
                    options=[(None, '---', None)] + options,
                )
        if 'varname' in parameters:
            form.add(
                VarnameWidget,
                '%svarname' % prefix,
                title=_('Identifier'),
                value=self.varname,
                hint=_('This is used to get attachment in expressions.'),
            )
        if 'attach_to_history' in parameters:
            form.add(
                CheckboxWidget,
                '%sattach_to_history' % prefix,
                title=_('Include in form history'),
                value=self.attach_to_history,
            )
        if 'allow_portfolio_picking' in parameters:
            form.add(
                CheckboxWidget,
                '%sallow_portfolio_picking' % prefix,
                title=_('Allow user to pick a file from a portfolio'),
                value=self.allow_portfolio_picking,
            )
        if 'push_to_portfolio' in parameters:
            form.add(
                CheckboxWidget,
                '%spush_to_portfolio' % prefix,
                title=_('Push to portfolio'),
                value=self.push_to_portfolio,
            )
        if 'document_type' in parameters:
            options = get_document_type_value_options(self.document_type)
            form.add(
                SingleSelectWidget,
                'document_type',
                title=_('File type suggestion'),
                value=self.document_type,
                options=options,
                advanced=True,
            )
        if 'max_file_size' in parameters:
            form.add(
                FileSizeWidget,
                'max_file_size',
                title=_('Max file size'),
                value=self.max_file_size,
                advanced=True,
            )

    def document_type_export_to_xml(self, xml_item, include_id=False):
        if not self.document_type:
            return
        node = ET.SubElement(xml_item, 'document_type')
        ET.SubElement(node, 'id').text = str(self.document_type['id'])
        ET.SubElement(node, 'label').text = str(self.document_type['label'])
        for mimetype in self.document_type.get('mimetypes') or []:
            ET.SubElement(node, 'mimetype').text = mimetype
        return node

    def document_type_init_with_xml(self, node, include_id=False, snapshot=False):
        self.document_type = {}
        if node is None:
            return
        self.document_type['id'] = xml_node_text(node.find('id'))
        self.document_type['label'] = xml_node_text(node.find('label'))
        self.document_type['mimetypes'] = []
        for mimetype_node in node.findall('mimetype') or []:
            self.document_type['mimetypes'].append(xml_node_text(mimetype_node))


register_item_class(AddAttachmentWorkflowStatusItem)

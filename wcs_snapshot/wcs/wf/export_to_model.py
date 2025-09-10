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

import base64
import collections
import io
import os
import random
import shutil
import subprocess
import tempfile
import time
import zipfile
from xml.etree import ElementTree as ET

from django.template.defaultfilters import filesizeformat
from django.utils.encoding import force_bytes, force_str
from quixote import get_publisher, get_request, get_response
from quixote.directory import Directory
from quixote.html import htmltext
from quixote.http_request import Upload

from wcs.fields import FileField
from wcs.portfolio import has_portfolio, push_document
from wcs.qommon.errors import ConfigurationError
from wcs.workflows import (
    AttachmentEvolutionPart,
    WorkflowGlobalAction,
    WorkflowStatusItem,
    get_formdata_template_context,
    register_item_class,
    template_on_context,
    template_on_formdata,
)

from ..qommon import _, ezt, misc
from ..qommon.form import (
    CheckboxWidget,
    ComputedExpressionWidget,
    FileWidget,
    HtmlWidget,
    RadiobuttonsWidget,
    SingleSelectWidget,
    StringWidget,
    UploadedFile,
    VarnameWidget,
    WidgetList,
    WysiwygTextWidget,
)
from ..qommon.template import Template, TemplateError

OO_TEXT_NS = 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'
OO_OFFICE_NS = 'urn:oasis:names:tc:opendocument:xmlns:office:1.0'
OO_STYLE_NS = 'urn:oasis:names:tc:opendocument:xmlns:style:1.0'
OO_DRAW_NS = 'urn:oasis:names:tc:opendocument:xmlns:drawing:1.0'
OO_FO_NS = 'urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0'
XLINK_NS = 'http://www.w3.org/1999/xlink'
USER_FIELD_DECL = '{%s}user-field-decl' % OO_TEXT_NS
USER_FIELD_GET = '{%s}user-field-get' % OO_TEXT_NS
SECTION_NODE = '{%s}section' % OO_TEXT_NS
SECTION_NAME = '{%s}name' % OO_TEXT_NS
STRING_VALUE = '{%s}string-value' % OO_OFFICE_NS
DRAW_FRAME = '{%s}frame' % OO_DRAW_NS
DRAW_NAME = '{%s}name' % OO_DRAW_NS
DRAW_IMAGE = '{%s}image' % OO_DRAW_NS
XLINK_HREF = '{%s}href' % XLINK_NS
NAME = '{%s}name' % OO_TEXT_NS

try:
    subprocess.check_call(['which', 'libreoffice'], stdout=subprocess.DEVNULL)

    def transform_to_pdf(instream):
        temp_dir = tempfile.mkdtemp()
        try:
            with tempfile.NamedTemporaryFile(dir=temp_dir) as infile:
                while True:
                    chunk = instream.read(100000)
                    if not chunk:
                        break
                    infile.write(chunk)
                infile.flush()
                for dummy in range(3):
                    lo_output = subprocess.run(
                        [
                            'libreoffice',
                            '-env:UserInstallation=file://%s' % temp_dir,
                            '--headless',
                            '--convert-to',
                            'pdf:writer_pdf_Export:{"PDFUACompliance":{"type":"boolean","value":"true"}}',
                            infile.name,
                            '--outdir',
                            temp_dir,
                        ],
                        check=True,
                        capture_output=True,
                    )
                    if os.path.exists(infile.name + '.pdf'):
                        break
                    # sometimes libreoffice fails and sometimes it's ok
                    # afterwards.
                    time.sleep(0.5)
                if not os.path.exists(infile.name + '.pdf'):
                    raise Exception(
                        'libreoffice failed to produce pdf (stdout: %r, stderr: %r)'
                        % (lo_output.stdout, lo_output.stderr)
                    )
            with open(infile.name + '.pdf', 'rb') as fd:
                pdf_stream = io.BytesIO(fd.read())
            return pdf_stream
        except subprocess.CalledProcessError:
            raise Exception('libreoffice is failing')
        finally:
            shutil.rmtree(temp_dir)

except subprocess.CalledProcessError:
    transform_to_pdf = None


def transform_opendocument(instream, outstream, process):
    """Take a file-like object containing an ODT, ODS, or any open-office
    format, parse context.xml with element tree and apply process to its root
    node.
    """
    with zipfile.ZipFile(instream, mode='r') as zin, zipfile.ZipFile(outstream, mode='w') as zout:
        new_images = {}
        assert 'content.xml' in zin.namelist()
        for filename in zin.namelist():
            # first pass to process meta.xml, content.xml and styles.xml
            if filename not in ('meta.xml', 'content.xml', 'styles.xml'):
                continue
            content = zin.read(filename)
            root = ET.fromstring(content)
            process(root, new_images)
            content = ET.tostring(root)
            if (
                root.find(f'{{{OO_OFFICE_NS}}}body/{{{OO_OFFICE_NS}}}spreadsheet')
                and b'xmlns:of=' not in content
            ):
                # force xmlns:of namespace inclusion in spreadsheet files, as it may be
                # required for proper handling of table:formula attributes.
                # (there is no easy way to have ElementTree include namespace declarations
                # if there are no elements of that namespace)
                content = content.replace(
                    b':document-content ',
                    b':document-content xmlns:of="urn:oasis:names:tc:opendocument:xmlns:of:1.2" ',
                    1,
                )
            if root.find(f'{{{OO_OFFICE_NS}}}body/{{{OO_OFFICE_NS}}}text') and b'xmlns:ooow=' not in content:
                # ditto for xmlns:ooow namespace in text documents, required to handle
                # masked paragraphs and section conditions.
                content = content.replace(
                    b':document-content ',
                    b':document-content xmlns:ooow="http://openoffice.org/2004/writer" ',
                    1,
                )

            zout.writestr(filename, content)

        for filename in zin.namelist():
            # second pass to copy/replace other files
            if filename in ('meta.xml', 'content.xml', 'styles.xml'):
                continue
            if filename in new_images:
                content = new_images[filename].get_content()
            else:
                content = zin.read(filename)
            zout.writestr(filename, content)


def is_opendocument(stream):
    try:
        with zipfile.ZipFile(stream) as z:
            if 'mimetype' in z.namelist():
                return z.read('mimetype').startswith(b'application/vnd.oasis.opendocument.')
    except zipfile.BadZipfile:
        return False
    finally:
        stream.seek(0)


class ExportToModelDirectory(Directory):
    _q_exports = ['']

    def __init__(self, formdata, wfstatusitem, wfstatus):
        self.formdata = formdata
        self.wfstatusitem = wfstatusitem

    def _q_index(self):
        if not (self.wfstatusitem.model_file or self.wfstatusitem.model_file_template):
            raise ConfigurationError(_('No model defined for this action'))
        response = get_response()
        model_file = self.wfstatusitem.get_model_file()
        try:
            response_content = self.wfstatusitem.apply_template_to_formdata(self.formdata, model_file).read()
        except UploadValidationError:
            raise ConfigurationError(_('Invalid model defined for this action'))
        if self.wfstatusitem.convert_to_pdf:
            response.content_type = 'application/pdf'
        else:
            response.content_type = model_file.content_type
        response.set_header('location', '..')

        filename = self.wfstatusitem.get_filename(model_file)
        if self.wfstatusitem.convert_to_pdf:
            filename = filename.rsplit('.', 1)[0] + '.pdf'
        if response.content_type != 'text/html':
            response.set_header('content-disposition', 'attachment; filename="%s"' % filename)
        return response_content


class UploadValidationError(Exception):
    pass


class ModelFileWidget(FileWidget):
    def __init__(self, name, value=None, directory=None, filename=None, validation=None, **kwargs):
        super().__init__(name, value=value, **kwargs)
        self.existing_value = self.value = value
        self.directory = directory or 'uploads'
        self.filename = filename
        self.validation = validation

    def _parse(self, request):
        super()._parse(request=request)
        if self.value:
            try:
                self.validation(self.value)
            except UploadValidationError as e:
                self.error = str(e)
            else:
                self.value = UploadedFile(self.directory, self.filename, self.value)
        else:
            # keep existing value
            self.value = self.existing_value


class ExportToModel(WorkflowStatusItem):
    description = _('Document Creation')
    key = 'export_to_model'
    category = 'interaction'
    support_substitution_variables = True
    ok_in_global_action = True
    filename = None

    waitpoint = True

    label = None
    model_file_mode = 'file'  # or 'template'
    model_file = None
    model_file_template = None
    attach_to_history = False
    directory_class = ExportToModelDirectory
    by = ['_receiver']
    backoffice_info_text = None
    varname = None
    convert_to_pdf = bool(transform_to_pdf)
    push_to_portfolio = False
    method = 'interactive'
    backoffice_filefield_id = None

    def get_line_details(self):
        if self.model_file and self.model_file_mode == 'file':
            return _('with model named %(file_name)s of %(size)s') % {
                'file_name': self.model_file.base_filename,
                'size': filesizeformat(self.model_file.size),
            }
        if self.model_file_template and self.model_file_mode == 'template':
            return _('with model from template')
        return _('no model set')

    def is_interactive(self):
        return bool(self.method == 'interactive')

    @property
    def endpoint(self):
        return not self.is_interactive()

    def has_configured_model_file(self):
        return (self.model_file_mode == 'file' and self.model_file) or (
            self.model_file_mode == 'template' and self.model_file_template
        )

    def fill_form(self, form, formdata, user, **kwargs):
        if self.method != 'interactive' or not self.has_configured_model_file():
            return
        label = self.label
        if not label:
            label = _('Create Document')
        form.add_submit('button%s' % self.id, label, **{'class': 'download'})
        widget = form.get_widget('button%s' % self.id)
        widget.backoffice_info_text = self.backoffice_info_text
        widget.prevent_jump_on_submit = True
        widget.action_id = self.id

    def submit_form(self, form, formdata, user, evo):
        if self.method != 'interactive':
            return
        if not self.has_configured_model_file():
            return
        if form.get_submit() == 'button%s' % self.id:
            if not evo.comment:
                evo.comment = str(_('Form exported in a model'))
            self.perform_real(formdata, evo)
            in_backoffice = get_request() and get_request().is_in_backoffice()
            if self.attach_to_history:
                return
            base_url = formdata.get_url(backoffice=in_backoffice)
            return base_url + self.get_directory_name()

    def model_file_validation(self, upload, allow_rtf=False):
        if hasattr(upload, 'fp'):
            fp = upload.fp
        elif hasattr(upload, 'get_file'):
            fp = upload.get_file()
        else:
            raise UploadValidationError('unknown upload object %r' % upload)

        # RTF
        if allow_rtf or not get_publisher().has_site_option('disable-rtf-support'):
            if upload.content_type and upload.content_type == 'application/rtf':
                return 'rtf'
            if (
                upload.content_type and upload.content_type == 'application/octet-stream'
            ) or upload.content_type is None:
                if upload.base_filename and upload.base_filename.endswith('.rtf'):
                    return 'rtf'
            if fp.read(10).startswith(b'{\\rtf'):
                fp.seek(0)
                return 'rtf'

        # OpenDocument
        fp.seek(0)
        if upload.content_type and upload.content_type.startswith('application/vnd.oasis.opendocument.'):
            return 'opendocument'
        if (
            upload.content_type and upload.content_type == 'application/octet-stream'
        ) or upload.content_type is None:
            if upload.base_filename and upload.base_filename.rsplit('.', 1) in ('odt', 'ods', 'odc', 'odb'):
                return 'opendocument'
        if is_opendocument(fp):
            return 'opendocument'

        # XML
        fp.seek(0)
        xml_content = fp.read()
        fp.seek(0)
        if (upload.content_type and upload.content_type in ('text/xml', 'application/xml')) or (
            upload.base_filename and upload.base_filename.endswith('.xml') and xml_content.startswith(b'<')
        ):
            # check XML content is valid UTF-8
            try:
                xml_content.decode('utf-8')
            except UnicodeDecodeError:
                raise UploadValidationError(_('XML model files must be UTF-8.'))
            return 'xml'
        raise UploadValidationError(_('Only OpenDocument and XML files can be used.'))

    def model_file_content_validation(self, upload):
        for string in self.get_model_file_template_strings(upload):
            if string:
                try:
                    Template(string, raises=True, record_errors=False)
                except TemplateError as e:
                    raise UploadValidationError(str(e))
        upload.fp.seek(0)

    def get_parameters(self):
        parameters = ('model_file_mode', 'model_file', 'model_file_template')
        if transform_to_pdf is not None:
            parameters += ('convert_to_pdf',)
        parameters += ('varname', 'backoffice_filefield_id', 'attach_to_history')
        if has_portfolio():
            parameters += ('push_to_portfolio',)
        parameters += ('method', 'by', 'label', 'backoffice_info_text', 'filename', 'condition')
        return parameters

    def get_inspect_parameters(self):
        parameters = super().get_inspect_parameters()
        if self.method != 'interactive':
            parameters.remove('by')
            parameters.remove('label')
            parameters.remove('backoffice_info_text')
        if self.model_file_mode == 'file':
            parameters.remove('model_file_template')
        elif self.model_file_mode == 'template':
            parameters.remove('model_file')
        return parameters

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        in_global_action = isinstance(self.parent, WorkflowGlobalAction)
        methods = collections.OrderedDict(
            [('interactive', _('Interactive (button)')), ('non-interactive', _('Non interactive'))]
        )
        if 'model_file_mode' in parameters:
            form.add(
                HtmlWidget,
                name='note',
                title=htmltext('<div class="infonotice">%s</div>')
                % _(
                    'You can use variables in your model using '
                    'the {{variable}} syntax, available variables '
                    'depends on the form.'
                ),
            )
            form.add(
                RadiobuttonsWidget,
                '%smodel_file_mode' % prefix,
                title=_('Model'),
                options=[('file', _('File'), 'file'), ('template', _('Template'), 'template')],
                value=self.model_file_mode,
                default_value=self.__class__.model_file_mode,
                attrs={'data-dynamic-display-parent': 'true'},
                extra_css_class='widget-inline-radio',
            )
        if 'model_file' in parameters:
            ids = (self.get_workflow().id, self.parent.id, self.id)
            filename = 'export_to_model-%s-%s-%s.upload' % ids
            widget_name = '%smodel_file' % prefix
            if formdef and formdef.workflow_options and formdef.workflow_options.get(widget_name) is not None:
                value = formdef.workflow_options.get(widget_name)
            else:
                value = self.model_file
            if value:
                hint = htmltext('<div>%s: <a href="?file=%s">%s</a></div>') % (
                    _('Current value'),
                    widget_name,
                    value.base_filename,
                )
            else:
                hint = None
            form.add(
                ModelFileWidget,
                widget_name,
                directory='models',
                filename=filename,
                hint=hint,
                validation=self.model_file_content_validation,
                value=value,
                attrs={
                    'data-dynamic-display-child-of': '%smodel_file_mode' % prefix,
                    'data-dynamic-display-value': 'file',
                },
            )
        if 'model_file_template' in parameters:
            form.add(
                ComputedExpressionWidget,
                name='%smodel_file_template' % prefix,
                title=_('Template to obtain model file'),
                value=self.model_file_template,
                attrs={
                    'data-dynamic-display-child-of': '%smodel_file_mode' % prefix,
                    'data-dynamic-display-value': 'template',
                },
            )
        if 'convert_to_pdf' in parameters:
            form.add(
                CheckboxWidget,
                '%sconvert_to_pdf' % prefix,
                title=_('Convert generated file to PDF'),
                value=self.convert_to_pdf,
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
        if 'attach_to_history' in parameters:
            form.add(
                CheckboxWidget,
                '%sattach_to_history' % prefix,
                title=_('Include generated file in the form history'),
                value=self.attach_to_history,
            )
        if 'varname' in parameters:
            form.add(
                VarnameWidget,
                '%svarname' % prefix,
                title=_('Identifier'),
                value=self.varname,
                hint=_('This is used to get generated document in expressions.'),
            )
        if 'push_to_portfolio' in parameters:
            form.add(
                CheckboxWidget,
                '%spush_to_portfolio' % prefix,
                title=_('Push generated file to portfolio'),
                value=self.push_to_portfolio,
            )

        if 'method' in parameters and in_global_action:
            form.add_hidden('%smethod' % prefix, 'non-interactive')
        elif 'method' in parameters:
            form.add(
                RadiobuttonsWidget,
                '%smethod' % prefix,
                title=_('Method'),
                options=list(methods.items()),
                value=self.method,
                attrs={'data-dynamic-display-parent': 'true'},
                extra_css_class='widget-inline-radio',
            )

        if 'by' in parameters and not in_global_action:
            options = [(None, '---', None)] + self.get_list_of_roles()
            form.add(
                WidgetList,
                '%sby' % prefix,
                title=_('By'),
                element_type=SingleSelectWidget,
                value=self.by,
                add_element_label=self.get_add_role_label(),
                attrs={
                    'data-dynamic-display-child-of': '%smethod' % prefix,
                    'data-dynamic-display-value': methods.get('interactive'),
                },
                element_kwargs={'render_br': False, 'options': options},
            )

        if 'label' in parameters and not in_global_action:
            form.add(
                StringWidget,
                '%slabel' % prefix,
                title=_('Button Label'),
                value=self.label,
                attrs={
                    'data-dynamic-display-child-of': '%smethod' % prefix,
                    'data-dynamic-display-value': methods.get('interactive'),
                },
            )

        if 'backoffice_info_text' in parameters and not in_global_action:
            form.add(
                WysiwygTextWidget,
                '%sbackoffice_info_text' % prefix,
                title=_('Information Text for Backoffice'),
                value=self.backoffice_info_text,
                attrs={
                    'data-dynamic-display-child-of': '%smethod' % prefix,
                    'data-dynamic-display-value': methods.get('interactive'),
                },
            )
        if 'filename' in parameters:
            form.add(
                ComputedExpressionWidget,
                name='%sfilename' % prefix,
                title=_('File name'),
                value=self.filename,
            )

    def submit_admin_form(self, form):
        super().submit_admin_form(form)
        if not form.has_errors() and not self.is_interactive():
            self.by = []

    def get_model_file_parameter_view_value(self):
        return htmltext('<a href="status/%s/items/%s/?file=model_file">%s</a>') % (
            self.parent.id,
            self.id,
            self.model_file.base_filename,
        )

    def get_filename(self, model_file):
        filename = None
        if self.filename:
            filename = self.compute(self.filename)
        if not filename:
            filename = model_file.base_filename
        filename = filename.replace('/', '-')
        return filename

    def get_directory_name(self):
        return misc.simplify(self.label or 'export_to_model', space='_')

    directory_name = property(get_directory_name)

    def apply_template_to_formdata(self, formdata, model_file):
        kind = self.model_file_validation(model_file)
        if kind == 'rtf' and not get_publisher().has_site_option('disable-rtf-support'):
            outstream = self.apply_rtf_template_to_formdata(formdata, model_file)
        elif kind == 'opendocument':
            outstream = self.apply_od_template_to_formdata(formdata, model_file)
        elif kind == 'xml':
            outstream = self.apply_text_template_to_formdata(formdata, model_file)
        else:
            raise Exception('unsupported model kind %r' % kind)
        if kind == 'xml':
            outstream.seek(0)
            try:
                ET.parse(outstream)
            except ET.ParseError as e:
                get_publisher().record_error(
                    _('The rendered template is not a valid XML document.'), formdata=formdata, exception=e
                )
                # we do not reraise to let people see the result of the
                # templating to debug the XML correctness
            finally:
                outstream.seek(0)
        else:
            if self.convert_to_pdf:
                if transform_to_pdf is None:
                    raise Exception('libreoffice is missing')
                return transform_to_pdf(outstream)
        return outstream

    def apply_text_template_to_formdata(self, formdata, model_file):
        with model_file.get_file() as fp:
            return io.BytesIO(
                force_bytes(
                    template_on_formdata(
                        formdata,
                        fp.read().decode(errors='surrogateescape'),
                        record_errors=False,
                    )
                )
            )

    def apply_rtf_template_to_formdata(self, formdata, model_file):
        try:
            # force ezt_only=True because an RTF file may contain {{ characters
            # and would be seen as a Django template
            with model_file.get_file() as fp:
                return io.BytesIO(
                    force_bytes(
                        template_on_formdata(
                            formdata,
                            force_str(fp.read()),
                            ezt_format=ezt.FORMAT_RTF,
                            ezt_only=True,
                            record_errors=False,
                        )
                    )
                )
        except TemplateError as e:
            get_publisher().record_error(
                _('Error in template for export to model'), formdata=formdata, exception=e
            )
            raise ConfigurationError(_('Error in template: %s') % str(e))

    def apply_od_template_to_formdata(self, formdata, model_file):
        context = get_formdata_template_context(formdata)

        def process_styles(root):
            styles_node = root.find('{%s}styles' % OO_OFFICE_NS)
            if styles_node is None:
                return
            style_names = {x.attrib.get('{%s}name' % OO_STYLE_NS) for x in styles_node}
            for style_name in [
                'Page_20_Title',
                'Form_20_Title',
                'Form_20_Subtitle',
                'Field_20_Label',
                'Field_20_Value',
            ]:
                # if any style name is defined, don't alter styles
                if style_name in style_names:
                    return
            for i, style_name in enumerate(
                ['Field_20_Label', 'Field_20_Value', 'Form_20_Subtitle', 'Form_20_Title', 'Page_20_Title']
            ):
                style_node = ET.SubElement(styles_node, '{%s}style' % OO_STYLE_NS)
                style_node.attrib['{%s}name' % OO_STYLE_NS] = style_name
                style_node.attrib['{%s}display-name' % OO_STYLE_NS] = style_name.replace('_20_', ' ')
                style_node.attrib['{%s}family' % OO_STYLE_NS] = 'paragraph'
                para_props = ET.SubElement(style_node, '{%s}paragraph-properties' % OO_STYLE_NS)
                if 'Value' not in style_name:
                    para_props.attrib['{%s}margin-top' % OO_FO_NS] = '0.5cm'
                else:
                    para_props.attrib['{%s}margin-left' % OO_FO_NS] = '0.25cm'
                if 'Title' in style_name:
                    text_props = ET.SubElement(style_node, '{%s}text-properties' % OO_STYLE_NS)
                    text_props.attrib['{%s}font-size' % OO_FO_NS] = '%s%%' % (90 + i * 10)
                    text_props.attrib['{%s}font-weight' % OO_FO_NS] = 'bold'

        def process_root(root, new_images):
            if root.tag == '{%s}document-styles' % OO_OFFICE_NS:
                return process_styles(root)

            # cache for keeping computed user-field-decl value around
            user_field_values = {}

            def process_text(t):
                return template_on_context(context, force_str(t), autoescape=False, record_errors=False)

            nodes = []
            for node in root.iter():
                nodes.append(node)
            for node in nodes:
                got_blank_lines = False
                if node.tag == SECTION_NODE and 'form_details' in node.attrib.get(SECTION_NAME, ''):
                    # custom behaviour for a section named form_details
                    # (actually any name containing form_details), create
                    # real odt markup.
                    children = [x for x in node]
                    for child in children:
                        node.remove(child)
                    self.insert_form_details(node, formdata)

                # apply template to user-field-decl and update user-field-get
                if node.tag == USER_FIELD_DECL and STRING_VALUE in node.attrib:
                    node.attrib[STRING_VALUE] = process_text(node.attrib[STRING_VALUE])
                    if NAME in node.attrib:
                        user_field_values[node.attrib[NAME]] = node.attrib[STRING_VALUE]
                if (
                    node.tag == USER_FIELD_GET
                    and NAME in node.attrib
                    and node.attrib[NAME] in user_field_values
                ):
                    node.text = user_field_values[node.attrib[NAME]]

                if node.tag == DRAW_FRAME:
                    name = node.attrib.get(DRAW_NAME)
                    # variable image
                    pub = get_publisher()
                    with pub.complex_data():
                        try:
                            variable_image = self.compute(name, allow_complex=True)
                        except Exception:
                            continue
                        complex_variable_image = get_publisher().get_cached_complex_data(variable_image)
                    if not hasattr(complex_variable_image, 'get_content'):
                        continue
                    image = [x for x in node if x.tag == DRAW_IMAGE][0]
                    new_images[image.attrib.get(XLINK_HREF)] = complex_variable_image

                for attr in ('text', 'tail'):
                    if not getattr(node, attr):
                        continue
                    old_value = getattr(node, attr)
                    setattr(node, attr, process_text(old_value))
                    new_value = getattr(node, attr)
                    if old_value != new_value and '\n\n' in new_value:
                        got_blank_lines = True
                if got_blank_lines:
                    # replace blank lines by forced line breaks (it would be
                    # better to be smart about the document format and create
                    # real paragraphs if we were inside a paragraph but then
                    # we would also need to copy its style and what not).
                    current_tail = node.tail or ''
                    node.tail = None
                    as_str = force_str(ET.tostring(node)).replace(
                        '\n\n', 2 * ('<nsa:line-break xmlns:nsa="%(ns)s"/>' % {'ns': OO_TEXT_NS})
                    )
                    as_node = ET.fromstring(as_str)
                    node.text = as_node.text
                    children = [x for x in node]
                    for child in children:
                        node.remove(child)
                    for child in as_node:
                        node.append(child)
                    node.tail = current_tail

        outstream = io.BytesIO()
        with model_file.get_file() as fp:
            transform_opendocument(fp, outstream, process_root)
        outstream.seek(0)
        return outstream

    def insert_form_details(self, node, formdata):
        section_node = node

        for field_action in formdata.get_summary_display_actions(None, include_unset_required_fields=False):
            if field_action['action'] == 'open-page':
                page_title = ET.SubElement(section_node, '{%s}h' % OO_TEXT_NS)
                page_title.attrib['{%s}outline-level' % OO_TEXT_NS] = '1'
                page_title.attrib['{%s}style-name' % OO_TEXT_NS] = 'Page_20_Title'
                page_title.text = field_action['value']
            elif field_action['action'] == 'title':
                title = ET.SubElement(section_node, '{%s}h' % OO_TEXT_NS)
                title.attrib['{%s}outline-level' % OO_TEXT_NS] = '2'
                title.attrib['{%s}style-name' % OO_TEXT_NS] = 'Form_20_Title'
                title.text = field_action['value']
            elif field_action['action'] == 'subtitle':
                title = ET.SubElement(section_node, '{%s}h' % OO_TEXT_NS)
                title.attrib['{%s}outline-level' % OO_TEXT_NS] = '3'
                title.attrib['{%s}style-name' % OO_TEXT_NS] = 'Form_20_Subtitle'
                title.text = field_action['value']
            elif field_action['action'] == 'comment':
                # comment can be free form HTML, ignore them.
                pass
            elif field_action['action'] == 'label':
                if not field_action['field'].get_opendocument_node_value:
                    # unsupported field type
                    continue
                label_p = ET.SubElement(section_node, '{%s}p' % OO_TEXT_NS)
                label_p.attrib['{%s}style-name' % OO_TEXT_NS] = 'Field_20_Label'
                label_p.text = field_action['value']
            elif field_action['action'] == 'value':
                if not field_action['field_value_info']['field'].get_opendocument_node_value:
                    # unsupported field type
                    continue
                value = field_action['value']
                if value is None:
                    unset_value_p = ET.SubElement(section_node, '{%s}p' % OO_TEXT_NS)
                    unset_value_p.attrib['{%s}style-name' % OO_TEXT_NS] = 'Field_20_Value'
                    unset_value_i = ET.SubElement(unset_value_p, '{%s}span' % OO_TEXT_NS)
                    unset_value_i.text = _('Not set')
                else:
                    node_value = field_action['field_value_info']['field'].get_opendocument_node_value(
                        field_action['field_value_info']['value'], formdata
                    )
                    if node_value is None:
                        continue
                    if isinstance(node_value, list):
                        for node in node_value:
                            section_node.append(node)
                            node.attrib['{%s}style-name' % OO_TEXT_NS] = 'Field_20_Value'
                    elif node_value.tag in ('{%s}span' % OO_TEXT_NS, '{%s}a' % OO_TEXT_NS):
                        value_p = ET.SubElement(section_node, '{%s}p' % OO_TEXT_NS)
                        value_p.attrib['{%s}style-name' % OO_TEXT_NS] = 'Field_20_Value'
                        value_p.append(node_value)
                    else:
                        node_value.attrib['{%s}style-name' % OO_TEXT_NS] = 'Field_20_Value'
                        section_node.append(node_value)

    def model_file_export_to_xml(self, xml_item, include_id=False):
        if not self.model_file:
            return
        el = ET.SubElement(xml_item, 'model_file')
        ET.SubElement(el, 'base_filename').text = self.model_file.base_filename
        ET.SubElement(el, 'content_type').text = self.model_file.content_type
        with self.model_file.get_file() as fp:
            ET.SubElement(el, 'b64_content').text = force_str(base64.encodebytes(fp.read()))

    def model_file_init_with_xml(self, elem, include_id=False, snapshot=False):
        if elem is None:
            return
        base_filename = elem.find('base_filename').text
        content_type = elem.find('content_type').text
        if elem.find('b64_content') is not None:
            content = base64.decodebytes(force_bytes(elem.find('b64_content').text or ''))
        if elem.find('content') is not None:
            content = elem.find('content').text

        if self.get_workflow().id and not snapshot:
            ids = (self.get_workflow().id, self.parent.id, self.id)
        elif snapshot:
            # use snapshot prefix so they can eventually be cleaned
            # automatically
            ids = ('snapshot%i' % random.randint(0, 1000000), self.parent.id, self.id)
        else:
            # hopefully this will be random enough.
            ids = ('i%i' % random.randint(0, 1000000), self.parent.id, self.id)
        filename = 'export_to_model-%s-%s-%s.upload' % ids

        upload = Upload(base_filename, content_type)
        upload.fp = io.BytesIO()
        upload.fp.write(content)
        upload.fp.seek(0)
        self.model_file = UploadedFile('models', filename, upload)

    def get_computed_strings(self):
        yield from super().get_computed_strings()
        yield self.filename
        if self.model_file:
            yield from self.get_model_file_template_strings(model_file=self.model_file, allow_rtf=True)

    def get_model_file_template_strings(self, model_file, allow_rtf=False):
        try:
            kind = self.model_file_validation(model_file, allow_rtf=allow_rtf)
        except FileNotFoundError:
            return

        if hasattr(model_file, 'get_file_pointer'):
            model_file_fp = model_file.get_file_pointer()
        else:
            model_file_fp = model_file.fp

        if kind in ('rtf', 'xml'):
            yield model_file_fp.read().decode(errors='surrogateescape')
        elif kind == 'opendocument':
            with zipfile.ZipFile(model_file_fp, mode='r') as zin:
                content = zin.read('content.xml')
                root = ET.fromstring(content)
                fields_in_use = [
                    x.attrib.get('{%s}name' % OO_TEXT_NS)
                    for x in root.findall('.//{%s}user-field-get' % OO_TEXT_NS)
                ]
                for node in root.iter():
                    if node.tag == DRAW_FRAME:
                        yield node.attrib.get(DRAW_NAME)
                    elif (
                        node.tag == USER_FIELD_DECL
                        and STRING_VALUE in node.attrib
                        and node.attrib.get('{%s}name' % OO_TEXT_NS) in fields_in_use
                    ):
                        yield node.attrib[STRING_VALUE]
                    text = getattr(node, 'text', None)
                    if Template.is_template_string(text):
                        yield text
                    text = getattr(node, 'tail', None)
                    if Template.is_template_string(text):
                        yield text

        if hasattr(model_file, 'get_file_pointer'):
            model_file_fp.close()

    def perform(self, formdata):
        if self.method == 'interactive':
            return
        self.perform_real(formdata, formdata.evolution[-1])

    def get_model_file(self):
        if self.model_file_mode == 'file':
            return self.model_file
        with get_publisher().complex_data():
            try:
                model_file = self.compute(
                    self.model_file_template, allow_complex=True, record_errors=False, raises=True
                )
            except Exception as e:
                get_publisher().record_error(
                    _('Failed to evaluate template for action'), exception=e, status_item=self
                )
                return None
            model_file = get_publisher().get_cached_complex_data(model_file)
        try:
            model_file = FileField.convert_value_from_anything(model_file)
        except ValueError:
            get_publisher().record_error(
                _('Invalid value obtained for model file (%r)') % model_file, status_item=self
            )
            return None
        return model_file

    def perform_real(self, formdata, evo):
        if not self.has_configured_model_file():
            return
        model_file = self.get_model_file()
        if not model_file:
            return
        try:
            outstream = self.apply_template_to_formdata(formdata, model_file)
        except UploadValidationError as e:
            get_publisher().record_error(str(e), formdata=formdata, exception=e)
            return
        filename = self.get_filename(model_file)
        content_type = model_file.content_type
        if self.convert_to_pdf:
            filename = filename.rsplit('.', 1)[0] + '.pdf'
            content_type = 'application/pdf'
        if self.push_to_portfolio:
            push_document(formdata.get_user(), filename, outstream)
        if self.attach_to_history:
            part = AttachmentEvolutionPart(
                filename, outstream, content_type=content_type, varname=self.varname
            )
            part.force_not_malware()
            evo.add_part(part)
            formdata.store()
        if self.backoffice_filefield_id:
            outstream.seek(0)
            self.store_in_backoffice_filefield(
                formdata,
                self.backoffice_filefield_id,
                filename,
                content_type,
                outstream.read(),
                force_not_malware=True,
            )

    def perform_in_tests(self, formdata):
        self.perform(formdata)


register_item_class(ExportToModel)

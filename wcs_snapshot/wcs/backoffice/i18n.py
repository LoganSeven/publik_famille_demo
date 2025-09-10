# w.c.s. - web application for online forms
# Copyright (C) 2005-2022  Entr'ouvert
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

import io
import xml.etree.ElementTree as ET
import zipfile

from quixote import get_publisher, get_request, get_response, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext
from quixote.http_request import parse_query

from wcs.backoffice.pagination import pagination_links
from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.categories import Category
from wcs.formdef import FormDef
from wcs.mail_templates import MailTemplate
from wcs.qommon import _, errors, get_cfg, misc, ods, template
from wcs.qommon.afterjobs import AfterJob
from wcs.qommon.form import CheckboxWidget, FileWidget, Form, RadiobuttonsWidget, TextWidget
from wcs.sql_criterias import ArrayPrefixMatch, Equal, FtsMatch, ILike, Or
from wcs.workflows import Workflow


class I18nDirectory(Directory):
    do_not_call_in_templates = True
    _q_exports = ['', 'scan', 'export', ('import', 'p_import')]

    supported_languages = [
        ('en', _('English')),
        ('fr', _('French')),
        ('de', _('German')),
    ]

    def get_enabled_languages(self):
        enabled_languages = get_cfg('language', {}).get('languages') or []
        return [x for x in self.supported_languages if x[0] in enabled_languages]

    def get_supported_languages(self):
        return [x for x in self.get_enabled_languages() if x[0] != get_cfg('language', {}).get('language')]

    def get_selected_language(self):
        return get_request().form.get('lang') or self.get_supported_languages()[0][0]

    def _q_index(self):
        from wcs.i18n import TranslatableMessage

        if not get_publisher().has_i18n_enabled():
            raise errors.TraversalError()
        get_response().set_title(_('Multilinguism'))
        get_response().breadcrumb.append(('i18n/', _('Multilinguism')))

        if not self.get_supported_languages():
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('Multilinguism')
            r += htmltext('<div class="pk-error"><p>%s</p>') % _('No languages selected.')
            r += htmltext('<p><a class="pk-button" href="../settings/language">%s</a></p>') % _(
                'Open settings'
            )
            r += htmltext('</div>')
            return r.getvalue()

        if TranslatableMessage.count() == 0:
            return self.scan()

        criterias = []
        criterias.append(Equal('translatable', not (bool(get_request().form.get('non_translatable')))))
        if get_request().form.get('q'):
            search_term = get_request().form.get('q')
            criterias.append(Or([ILike('string', search_term), FtsMatch(search_term, extra_normalize=False)]))
        if get_request().form.get('formdef'):
            kind, kind_id = get_request().form.get('formdef').split('/')
            formdef_class = FormDef if kind == 'forms' else CardDef
            formdef = formdef_class.get(kind_id, lightweight=True)
            criterias.append(
                Or(
                    [
                        ArrayPrefixMatch('locations', f'{kind}/{kind_id}/'),
                        ArrayPrefixMatch('locations', f'workflows/{formdef.workflow_id}/'),
                    ]
                )
            )

        offset = misc.get_int_or_400(get_request().form.get('offset', 0))
        limit = misc.get_int_or_400(get_request().form.get('limit', 20))
        total_count = TranslatableMessage.count(criterias)
        context = {
            'has_sidebar': False,
            'q': get_request().form.get('q'),
            'view': self,
            'selected_language': self.get_selected_language(),
            'supported_languages': self.get_supported_languages(),
            'pagination_links': pagination_links(offset, limit, total_count, load_js=False),
            'messages': TranslatableMessage.select(criterias, offset=offset, limit=limit, order_by='string'),
            'query': get_request().get_query(),
            'selected_formdef': get_request().form.get('formdef'),
            'non_translatable': get_request().form.get('non-translatable'),
            'formdefs': FormDef.select(lightweight=True, order_by='name'),
            'carddefs': CardDef.select(lightweight=True, order_by='name'),
        }
        get_response().add_javascript(['popup.js'])
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/i18n.html'], context=context, is_django_native=True
        )

    def scan(self):
        job = get_publisher().add_after_job(
            I18nScanAfterJob(
                label=_('Scanning for translatable text'),
                user_id=get_request().user.id,
                return_url='/backoffice/i18n/',
            )
        )
        job.store()
        return redirect(job.get_processing_url())

    def export(self):
        if 'download' in get_request().form:
            try:
                job = AfterJob.get(get_request().form.get('download'))
            except KeyError:
                return redirect('.')
            if not job.status == 'completed':
                raise errors.TraversalError()
            response = get_response()
            response.set_content_type(job.content_type)
            response.set_header('content-disposition', 'attachment; filename=%s' % job.file_name)
            return job.file_content

        form = Form()
        form.add_hidden('query_string', get_request().get_query())
        formats = [
            ('ods', _('OpenDocument (.ods)'), 'ods'),
            ('xliff', _('XLIFF'), 'xliff'),
        ]
        form.add(
            RadiobuttonsWidget,
            'format',
            options=formats,
            value='ods',
            required=True,
            extra_css_class='widget-inline-radio',
        )
        form.add_submit('submit', _('Export'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('./?' + (form.get_widget('query_string').parse() or ''))

        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('export', _('Export')))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('Export Options')
            r += form.render()
            return r.getvalue()

        get_request().form = parse_query(form.get_widget('query_string').parse() or '', 'utf-8')
        job = ExportAfterJob(
            file_format=form.get_widget('format').parse(),
            lang=self.get_selected_language(),
            q=get_request().form.get('q'),
        )
        job.store()
        get_publisher().add_after_job(job)
        return redirect(job.get_processing_url())

    def p_import(self):
        form = Form(enctype='multipart/form-data', use_tokens=False)
        form.add_hidden('query_string', get_request().get_query())
        form.add(FileWidget, 'file', title=_('File'), required=True)
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('./?' + (form.get_widget('query_string').parse() or ''))

        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('import', _('Import File')))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % _('Import File')
            r += form.render()
            return r.getvalue()

        job = ImportAfterJob(
            lang=self.get_selected_language(),
            file_content=form.get_widget('file').parse().fp.read(),
            return_url='/backoffice/i18n/?' + (form.get_widget('query_string').parse() or ''),
        )
        job.store()
        get_publisher().add_after_job(job)
        return redirect(job.get_processing_url())

    def _q_lookup(self, component):
        if component in [x[0] for x in self.get_enabled_languages()]:
            return LanguageDirectory(component)
        raise errors.TraversalError()


class LanguageDirectory(Directory):
    def __init__(self, lang):
        self.lang = lang

    def _q_lookup(self, component):
        from wcs.i18n import TranslatableMessage

        try:
            msg = TranslatableMessage.get(component)
        except KeyError:
            raise errors.TraversalError()
        return MessageDirectory(self.lang, msg)


class MessageDirectory(Directory):
    _q_exports = ['']

    def __init__(self, lang, msg):
        self.lang = lang
        self.msg = msg

    def _q_index(self):
        form = Form(enctype='multipart/form-data', action='%s' % get_request().get_path_query())
        rows = min(10, max(2, self.msg.string.count('\n')))
        attr = 'string_%s' % self.lang
        form.add(TextWidget, 'translation', value=getattr(self.msg, attr), rows=rows)
        form.add(
            CheckboxWidget,
            'non_translatable',
            title=_('Mark as non-translatable'),
            value=not (self.msg.translatable),
        )
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('../../?' + get_request().get_query())

        if form.is_submitted() and not form.has_errors():
            setattr(self.msg, attr, form.get_widget('translation').parse())
            self.msg.translatable = not (form.get_widget('non_translatable').parse())
            self.msg.store()
            update_digests()
            return redirect('../../?' + get_request().get_query())

        get_response().set_title(_('Multilinguism'))
        context = {'html_form': form, 'msg': self.msg}
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/i18n-message.html'], context=context
        )


class I18nScanAfterJob(AfterJob):
    def done_action_url(self):
        return self.kwargs['return_url']

    def done_action_label(self):
        return _('Go to multilinguism page')

    def done_button_attributes(self):
        return {'data-redirect-auto': 'true'}

    def execute(self):
        from wcs.i18n import TranslatableMessage

        objects = []
        for klass in (FormDef, CardDef, BlockDef, Workflow, MailTemplate, Category):
            objects.extend(klass.select(ignore_errors=True, ignore_migration=True))
        self.total_count = len(objects) * 2  # one for discovery, one for storing
        self.store()

        strings = {(x.context, x.string): x for x in TranslatableMessage.select()}
        for string in strings.values():
            string.locations = []

        for obj in objects:
            if obj is None:
                self.increment_count()
                continue
            for location, context, string in obj.i18n_scan():
                string = string.strip() if string else string
                if not string:
                    continue
                msg = strings.get((context, string))
                if not msg:
                    msg = TranslatableMessage()
                    msg.context = context
                    msg.string = string
                    msg.locations = []
                    strings[(context, string)] = msg
                msg.locations.append(location)
            self.increment_count()

        total_strings = len(strings)
        for i, string in enumerate(strings.values()):
            string.store()
            self.increment_count(len(objects) / total_strings)


class ExportAfterJob(AfterJob):
    label = _('Exporting translatable strings')

    def __init__(self, file_format, lang, q):
        super().__init__()
        self.file_format = file_format
        self.lang = lang
        self.q = q

    def execute(self):
        from wcs.i18n import TranslatableMessage

        criterias = []
        if self.q:
            criterias.append(Or([ILike('string', self.q), FtsMatch(self.q, extra_normalize=False)]))

        self.total_count = TranslatableMessage.count(criterias)

        if self.file_format == 'ods':
            workbook = ods.Workbook(encoding='utf-8')
            ws = workbook.add_sheet('')
        elif self.file_format == 'xliff':
            ET.register_namespace('xliff', 'urn:oasis:names:tc:xliff:document:2.0')
            root = ET.Element('{urn:oasis:names:tc:xliff:document:2.0}xliff')
            root.attrib['version'] = '2.0'
            root.attrib['srcLang'] = get_cfg('language', {}).get('language')
            root.attrib['trgLang'] = self.lang
            file_node = ET.SubElement(root, '{urn:oasis:names:tc:xliff:document:2.0}file')
            file_node.attrib['id'] = 'f1'
            unit_node = ET.SubElement(file_node, '{urn:oasis:names:tc:xliff:document:2.0}file')
            unit_node.attrib['id'] = '1'

        for i, message in enumerate(TranslatableMessage.select(criterias)):
            source = message.string
            target = message.translations().get(self.lang) or ''
            if self.file_format == 'ods':
                ws.write(i, 0, source)
                ws.write(i, 1, target)
            elif self.file_format == 'xliff':
                segment = ET.SubElement(unit_node, '{urn:oasis:names:tc:xliff:document:2.0}segment')
                ET.SubElement(segment, '{urn:oasis:names:tc:xliff:document:2.0}source').text = source
                ET.SubElement(segment, '{urn:oasis:names:tc:xliff:document:2.0}target').text = target
            self.increment_count()

        output = io.BytesIO()

        if self.file_format == 'ods':
            workbook.save(output)
            self.file_name = 'catalog.ods'
            self.content_type = 'application/vnd.oasis.opendocument.spreadsheet'
        elif self.file_format == 'xliff':
            ET.indent(root)
            output.write(ET.tostring(root, 'utf-8'))
            self.file_name = 'catalog.xliff'
            self.content_type = 'text/xml'

        self.file_content = output.getvalue()
        self.store()

    def done_action_url(self):
        return '/backoffice/i18n/export?download=%s' % self.id

    def done_action_label(self):
        return _('Download Export')


class ImportAfterJob(AfterJob):
    label = _('Importing translated strings')

    def __init__(self, lang, file_content, **kwargs):
        super().__init__(**kwargs)
        self.lang = lang
        self.file_content = file_content

    def add_string(self, source, target):
        from wcs.i18n import TranslatableMessage

        if not (source and target):
            return

        try:
            msg = TranslatableMessage.select([Equal('string', source)])[0]
        except IndexError:
            msg = TranslatableMessage()
            msg.string = source

        setattr(msg, 'string_%s' % self.lang, target)
        msg.store()

    def execute(self):
        if self.file_content.startswith(b'PK'):  # assume ods
            instream = io.BytesIO(self.file_content)
            with zipfile.ZipFile(instream, mode='r') as zin, zin.open('content.xml') as content_xml:
                doc = ET.parse(content_xml)
                rows = doc.findall('.//{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table-row')
                self.total_count = len(rows)
                for row in rows:
                    self.increment_count()
                    cells = row.findall('{urn:oasis:names:tc:opendocument:xmlns:table:1.0}table-cell')[:2]
                    if len(cells) != 2:
                        continue
                    source = cells[0].find('{urn:oasis:names:tc:opendocument:xmlns:text:1.0}p').text
                    target = cells[1].find('{urn:oasis:names:tc:opendocument:xmlns:text:1.0}p').text
                    self.add_string(source, target)
            update_digests()
        elif b'urn:oasis:names:tc:xliff:document' in self.file_content[:1000]:
            doc = ET.parse(io.BytesIO(self.file_content))
            segments = doc.findall('.//{urn:oasis:names:tc:xliff:document:2.0}segment')
            self.total_count = len(segments)
            for segment in segments:
                self.increment_count()
                source = segment.find('{urn:oasis:names:tc:xliff:document:2.0}source').text
                target = segment.find('{urn:oasis:names:tc:xliff:document:2.0}target').text
                self.add_string(source, target)
            update_digests()
        else:
            self.mark_as_failed(_('Unknown file format'))
        self.store()

    def done_action_url(self):
        return self.kwargs['return_url']

    def done_action_label(self):
        return _('Go to multilinguism page')

    def done_button_attributes(self):
        return {'data-redirect-auto': 'true'}


def update_digests():
    # for all carddefs, check if |translate in digest templates, and rebuild if necessary.
    from wcs.formdef_jobs import UpdateDigestAfterJob

    carddefs = []
    for carddef in CardDef.select():
        for template in (carddef.digest_templates or {}).values():
            if template and '|translate' in template:
                carddefs.append(carddef)
                break
    if carddefs and get_response():
        get_publisher().add_after_job(UpdateDigestAfterJob(formdefs=carddefs))
    elif carddefs:
        UpdateDigestAfterJob(formdefs=carddefs).execute()

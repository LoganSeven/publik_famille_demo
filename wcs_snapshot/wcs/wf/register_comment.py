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

import re

from quixote import get_publisher
from quixote.html import htmltext

from wcs.comment_templates import CommentTemplate
from wcs.workflows import (
    AttachmentEvolutionPart,
    EvolutionPart,
    WorkflowStatusItem,
    register_item_class,
    template_on_formdata,
)

from ..qommon import _, ezt
from ..qommon.form import SingleSelectWidget, TextWidget, WidgetListOfRoles
from ..qommon.template import TemplateError


class JournalEvolutionPart(EvolutionPart):
    content = None
    to = None
    level = None

    def __init__(self, formdata, message, to, level):
        if not message:
            return
        self.to = to
        self.level = level
        if '{{' in message or '{%' in message:
            # django template
            content = template_on_formdata(formdata, message, record_errors=False)
            if content and not content.startswith('<'):
                # add <div> to mark the string as processed as HTML
                content = '<div>%s</div>' % content
            content = htmltext(content)
        elif message.startswith('<'):
            # treat it as html, escape strings from ezt variables
            content = template_on_formdata(formdata, message, ezt_format=ezt.FORMAT_HTML, record_errors=False)
            content = htmltext(content)
        else:
            # treat is as plain text, with empty lines to mark paragraphs
            content = template_on_formdata(formdata, message, record_errors=False)
            content = (
                htmltext('<p>')
                + htmltext('\n').join([(x or htmltext('</p><p>')) for x in content.splitlines()])
                + htmltext('</p>')
            )
        if self.level:
            self.content = str(htmltext('<div class="%snotice">%s</div>') % (self.level, content))
        else:
            self.content = str(content)

    def view(self, **kwargs):
        if not self.content:
            return ''
        if self.content.startswith('<'):
            return htmltext(self.content)

        # legacy, use empty lines to mark paragraphs
        return (
            htmltext('<p>')
            + htmltext('\n').join([(x or htmltext('</p><p>')) for x in self.content.splitlines()])
            + htmltext('</p>')
        )

    def get_json_export_dict(self, anonymise=False, include_files=True):
        d = {
            'type': 'workflow-comment',
            'to': self.to,
        }
        if not anonymise:
            d['content'] = self.content
        return d


class RegisterCommenterWorkflowStatusItem(WorkflowStatusItem):
    description = _('History Message')
    key = 'register-comment'
    category = 'interaction'

    comment = None
    comment_template = None
    to = None
    level = None
    attachments = None

    def get_line_details(self):
        if self.to:
            return _('to %s') % self.render_list_of_roles(self.to)
        return _('to everybody')

    def get_dependencies(self):
        yield from super().get_dependencies()
        yield CommentTemplate.get_by_slug(self.comment_template)

    def get_inspect_parameters(self):
        parameters = super().get_inspect_parameters()
        if self.comment_template:
            parameters.remove('comment')
        return parameters

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        subject_body_attrs = {}
        if 'comment' in parameters:
            if CommentTemplate.count():
                subject_body_attrs = {
                    'data-dynamic-display-value': '',
                    'data-dynamic-display-child-of': '%scomment_template' % prefix,
                }
        if 'comment' in parameters:
            form.add(
                TextWidget,
                '%scomment' % prefix,
                title=_('Message'),
                value=self.comment,
                cols=80,
                rows=10,
                attrs=subject_body_attrs,
            )
        if 'comment_template' in parameters and CommentTemplate.count():
            form.add(
                SingleSelectWidget,
                '%scomment_template' % prefix,
                title=_('Comment Template'),
                value=self.comment_template,
                options=[(None, '', '')] + CommentTemplate.get_as_options_list(),
                attrs={'data-dynamic-display-parent': 'true'},
            )
        if 'level' in parameters:
            form.add(
                SingleSelectWidget,
                '%slevel' % prefix,
                title=_('Level'),
                value=self.level,
                options=[
                    (None, ''),
                    ('success', _('Success')),
                    ('info', _('Information')),
                    ('warning', _('Warning')),
                    ('error', _('Error')),
                ],
            )
        if 'to' in parameters:
            form.add(
                WidgetListOfRoles,
                '%sto' % prefix,
                title=_('To'),
                value=self.to or [],
                add_element_label=self.get_add_role_label(),
                first_element_empty_label=_('Everybody'),
                roles=self.get_list_of_roles(include_logged_in_users=False),
            )

    def get_parameters(self):
        return ('to', 'comment_template', 'comment', 'level', 'attachments', 'condition')

    def attach_uploads_to_formdata(self, formdata, uploads, to):
        if not formdata.evolution[-1].parts:
            formdata.evolution[-1].parts = []
        for upload in uploads:
            fp = None
            try:
                # useless but required to restore upload.fp from serialized state,
                # needed by AttachmentEvolutionPart.from_upload()
                fp = upload.get_file_pointer()
                formdata.evolution[-1].add_part(AttachmentEvolutionPart.from_upload(upload, to=to))
            except Exception as e:
                get_publisher().record_error(exception=e, context=_('Comment attachment'), notify=True)
            finally:
                if fp and isinstance(fp.name, str):
                    fp.close()
            continue

    def get_computed_strings(self):
        yield from super().get_computed_strings()
        if not self.comment_template:
            yield self.comment
        yield from (self.attachments or [])

    def migrate(self):
        changed = super().migrate()
        if not self.level:  # 2023-08-15
            match = re.match(
                r'^<div class="(error|info|warning|success)notice">(.*)</div>$', (self.comment or '').strip()
            )
            if match:
                self.level, self.comment = match.groups(0)
                changed = True
        return changed

    def perform(self, formdata):
        if not formdata.evolution:
            return

        if self.comment_template:
            comment_template = CommentTemplate.get_by_slug(self.comment_template)
            if comment_template:
                comment = comment_template.comment
                extra_attachments = comment_template.attachments
            else:
                message = _(
                    'reference to invalid comment template %(comment_template)s in status %(status)s'
                ) % {
                    'status': self.parent.name,
                    'comment_template': self.comment_template,
                }
                get_publisher().record_error(message, formdata=formdata, status_item=self)
                return
        else:
            comment = self.comment
            extra_attachments = None

        # process attachments first, they might be used in the comment
        # (with substitution vars)
        if self.attachments or extra_attachments:
            uploads = self.convert_attachments_to_uploads(extra_attachments)
            self.attach_uploads_to_formdata(formdata, uploads, self.to)
            formdata.store()  # store and invalidate cache, so references can be used in the comment message.

        # the comment can use attachments done above
        if comment:
            part = self.get_journal_evolution_part(formdata, comment)
            if part:
                formdata.evolution[-1].add_part(part)
                formdata.store()

    def get_journal_evolution_part(self, formdata, comment):
        try:
            return JournalEvolutionPart(formdata, get_publisher().translate(comment), self.to, self.level)
        except TemplateError as e:
            get_publisher().record_error(
                _('Error in template, comment could not be generated'), formdata=formdata, exception=e
            )

    def i18n_scan(self, base_location):
        location = '%sitems/%s/' % (base_location, self.id)
        if not self.comment_template:
            yield location, None, self.comment

    def perform_in_tests(self, formdata):
        self.perform(formdata)

        evo = formdata.evolution[-1]
        if evo.parts and isinstance(evo.parts[-1], JournalEvolutionPart):
            formdata.history_messages.append(evo.parts[-1].content)


register_item_class(RegisterCommenterWorkflowStatusItem)

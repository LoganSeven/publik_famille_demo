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

import html
import xml.etree.ElementTree as ET

from django.utils.html import strip_tags
from quixote.html import htmltext

from wcs.qommon import _
from wcs.qommon.form import (
    CheckboxWidget,
    MiniRichTextWidget,
    SingleSelectWidget,
    StringWidget,
    VarnameWidget,
    WidgetList,
    WysiwygTextWidget,
)
from wcs.qommon.misc import xml_node_text
from wcs.workflows import EvolutionPart, WorkflowStatusItem, register_item_class


class WorkflowCommentPart(EvolutionPart):
    def __init__(self, comment, varname=None):
        self.comment = comment
        self.varname = varname

    def get_as_plain_text(self):
        return html.unescape(strip_tags(self.comment.replace('</p><p>', '\n\n').replace('<br>', '\n')))

    def view(self, **kwargs):
        return htmltext('<div class="comment">%s</div>' % self.comment)

    def get_json_export_dict(self, anonymise=False, include_files=True):
        if anonymise:
            return None
        d = {
            'type': 'workflow-comment',
            'identifier': self.varname,
            'comment': self.comment,
            'comment_plain_text': self.get_as_plain_text(),
        }
        return d


class CommentableWorkflowStatusItem(WorkflowStatusItem):
    description = _('Comment')
    key = 'commentable'
    category = 'interaction'
    endpoint = False
    waitpoint = True
    ok_in_global_action = True

    required = False
    varname = None
    label = None
    button_label = 0  # hack to handle legacy commentable items
    hint = None
    by = []
    backoffice_info_text = None

    def get_line_details(self):
        if self.by:
            return _('by %s') % self.render_list_of_roles(self.by)
        return _('not completed')

    def is_interactive(self):
        return True

    def fill_form(self, form, formdata, user, **kwargs):
        if 'comment' not in [x.name for x in form.widgets]:
            if self.label is None:
                title = _('Comment')
            else:
                title = self.label
            form.add(
                MiniRichTextWidget,
                'comment',
                title=title,
                required=self.required,
                hint=self.hint,
                cols=40,
                rows=7,
                class_='comment',
            )
            if self.button_label == 0:
                form.add_submit('button%s' % self.id, _('Add Comment'))
            elif self.button_label:
                form.add_submit('button%s' % self.id, self.button_label)
            if form.get_widget('button%s' % self.id):
                form.get_widget('button%s' % self.id).backoffice_info_text = self.backoffice_info_text
                form.get_widget('button%s' % self.id).action_id = self.id

    def submit_form(self, form, formdata, user, evo):
        widget = form.get_widget('comment')
        if widget and widget.parse() and not getattr(widget, 'processed', False):
            widget.processed = True
            comment = widget.parse()
            comment_part = WorkflowCommentPart(comment, varname=self.varname)
            evo.add_part(comment_part)
            if self.varname:
                formdata.update_workflow_data({'comment_%s' % self.varname: comment_part.get_as_plain_text()})
            if formdata.is_workflow_test() and formdata.testdef:
                formdata.testdef.add_to_coverage(self)

    def submit_admin_form(self, form):
        for f in self.get_parameters():
            widget = form.get_widget(f)
            setattr(self, f, widget.parse())

    def fill_admin_form(self, form):
        if self.by and not isinstance(self.by, list):
            self.by = None
        return super().fill_admin_form(form)

    def get_parameters(self):
        return (
            'label',
            'button_label',
            'hint',
            'by',
            'varname',
            'backoffice_info_text',
            'required',
            'condition',
        )

    def add_parameters_widgets(self, form, parameters, prefix='', formdef=None, **kwargs):
        super().add_parameters_widgets(form, parameters, prefix=prefix, formdef=formdef, **kwargs)
        if 'label' in parameters:
            if self.label is None:
                self.label = str(_('Comment'))
            form.add(StringWidget, '%slabel' % prefix, size=40, title=_('Label'), value=self.label)
        if 'button_label' in parameters:
            if self.button_label == 0:
                self.button_label = str(_('Add Comment'))
            form.add(
                StringWidget,
                '%sbutton_label' % prefix,
                title=_('Button Label'),
                hint=_('(empty to disable the button)'),
                value=self.button_label,
            )
        if 'hint' in parameters:
            form.add(StringWidget, '%shint' % prefix, size=40, title=_('Hint'), value=self.hint)
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
        if 'varname' in parameters:
            form.add(
                VarnameWidget,
                '%svarname' % prefix,
                title=_('Identifier'),
                value=self.varname,
                hint=_('This will make the comment available in a variable named comment_ + identifier.'),
            )
        if 'required' in parameters:
            form.add(CheckboxWidget, '%srequired' % prefix, title=_('Required'), value=self.required)
        if 'backoffice_info_text' in parameters:
            form.add(
                WysiwygTextWidget,
                '%sbackoffice_info_text' % prefix,
                title=_('Information Text for Backoffice'),
                value=self.backoffice_info_text,
            )

    def button_label_export_to_xml(self, xml_item, include_id=False):
        if self.button_label == 0:
            pass
        elif self.button_label is None:
            # button_label being None is a special case meaning "no button", it
            # should be handled differently than the "not filled" case
            el = ET.SubElement(xml_item, 'button_label')
        else:
            el = ET.SubElement(xml_item, 'button_label')
            el.text = self.button_label

    def button_label_init_with_xml(self, element, include_id=False, snapshot=False):
        if element is None:
            return
        # this can be None if element is self-closing, <button_label />, which
        # then maps to None, meaning "no button".
        self.button_label = xml_node_text(element)


register_item_class(CommentableWorkflowStatusItem)

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

import copy
import json
import re

from quixote import get_publisher, get_request, get_response, get_session, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmlescape, htmltag, htmltext

from wcs import fields
from wcs.admin import utils
from wcs.carddef import CardDef
from wcs.fields import BlockField, get_field_options
from wcs.formdef import FormDef
from wcs.qommon import _, errors, get_cfg, misc, template
from wcs.qommon.admin.menu import command_icon
from wcs.qommon.form import CheckboxWidget, Form, HtmlWidget, OptGroup, SingleSelectWidget, StringWidget
from wcs.qommon.substitution import CompatibilityNamesDict

from .documentable import DocumentableFieldMixin, DocumentableMixin


class FieldDefPage(Directory, DocumentableMixin, DocumentableFieldMixin):
    _q_exports = ['', 'delete', 'duplicate', ('update-documentation', 'update_documentation')]

    large = False
    page_id = None
    blacklisted_attributes = []
    is_documentable = True

    def __init__(self, objectdef, field_id):
        self.objectdef = objectdef
        try:
            self.field = [x for x in self.objectdef.fields if x.id == field_id][0]
        except IndexError:
            raise errors.TraversalError()
        if not self.field.label:
            self.field.label = str(_('None'))
        self.documented_object = objectdef
        self.documented_element = self.field
        label = misc.ellipsize(self.field.unhtmled_label, 40)
        last_breadcrumb_url_part, last_breadcrumb_label = get_response().breadcrumb[-1]
        get_response().breadcrumb = get_response().breadcrumb[:-1]
        get_response().breadcrumb.append(
            (last_breadcrumb_url_part + '#fieldId_' + field_id, last_breadcrumb_label)
        )
        get_response().breadcrumb.append((field_id + '/', label))

    def form(self):
        form = Form(enctype='multipart/form-data', use_tabs=True)
        self.field.fill_admin_form(form, formdef=self.objectdef)
        form.widgets = [
            x for x in form.widgets if getattr(x, 'name', None) not in self.blacklisted_attributes
        ]
        if not self.objectdef.is_readonly():
            form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        return form

    def get_sidebar(self):
        if not self.is_documentable:
            return None
        r = TemplateIO(html=True)
        r += self.documentation_part()
        return r.getvalue()

    def _q_index(self):
        form = self.form()
        redo = False
        old_display_locations = (self.field.display_locations or []).copy()

        if form.get_submit() == 'cancel':
            return redirect('../#fieldId_%s' % self.field.id)

        if form.get_widget('items') and form.get_widget('items').get_widget('add_element').parse():
            form.clear_errors()
            redo = True

        if form.is_submitted():
            try:
                self.field.check_admin_form(form)
            except AttributeError:
                # informational fields don't have that method
                pass
            if form.has_errors():
                redo = True

        if redo or not form.get_submit() == 'submit':
            get_response().set_title(self.objectdef.name)
            get_response().filter['sidebar'] = self.get_sidebar()  # noqa pylint: disable=assignment-from-none
            r = TemplateIO(html=True)
            r += htmltext('<div id="appbar" class="field-edit">')
            r += htmltext('<h2 class="field-edit--title">%s</h2>') % misc.ellipsize(
                self.field.unhtmled_label, 80
            )
            if self.is_documentable:
                r += htmltext('<span class="actions">%s</span>') % template.render(
                    'wcs/backoffice/includes/documentation-editor-link.html', {}
                )
            r += htmltext('</div>')
            if isinstance(self.field, BlockField):
                try:
                    block_field = self.field.block
                except KeyError:
                    r += htmltext('<h3 class="field-edit--subtitle">%s</h3>') % self.field.get_type_label()
                else:
                    r += htmltext('<h3 class="field-edit--subtitle">%s - <a href="%s">%s</a></h3>') % (
                        _('Block of fields'),
                        block_field.get_admin_url(),
                        block_field.name,
                    )
            else:
                r += htmltext('<h3 class="field-edit--subtitle">%s</h3>') % self.field.description
            existing_varnames = {
                x.varname for x in self.objectdef.fields if x.varname if x.id != self.field.id
            }
            r += htmltext(
                '<script id="other-fields-varnames">%s</script>' % json.dumps(list(existing_varnames))
            )
            for widget in form.widgets:
                if hasattr(widget, 'get_widget'):
                    add_element_widget = widget.get_widget('add_element')
                    if add_element_widget and add_element_widget.parse():
                        form.initial_tab = widget.tab
                        break
            r += form.render()
            return r.getvalue()

        self.submit(form)

        if 'statistics' in (self.field.display_locations or []) and 'statistics' not in old_display_locations:
            self.schedule_statistics_data_update()
            get_session().add_message(_('Statistics data will be collected in the background.'), level='info')

        if form.get_widget('items') is None and self.field.key == 'item':
            return redirect('.')

        prefill_type = self.field.prefill.get('type') if self.field.prefill else None
        prefill_value = self.field.prefill.get('value') if self.field.prefill else None
        users_cfg = get_cfg('users', {})
        field_email = users_cfg.get('field_email') or 'email'
        if self.field.key != 'email' and prefill_type == 'user' and prefill_value == field_email:
            get_session().add_message(
                _("\"%s\" is not an email field. Are you sure you want to prefill it with user's email?")
                % self.field.label,
                level='warning',
            )
            return redirect('..')

        return redirect('../#fieldId_%s' % self.field.id)

    def schedule_statistics_data_update(self):
        from wcs.formdef_jobs import UpdateStatisticsDataAfterJob

        get_publisher().add_after_job(UpdateStatisticsDataAfterJob(formdefs=[self.objectdef]))

    def submit(self, form):
        for f in self.field.get_admin_attributes():
            widget = form.get_widget(f)
            if not widget:
                continue
            setattr(self.field, f.replace('-', '_'), widget.parse())
        self.objectdef.store(comment=_('Modification of field "%s"') % self.field.ellipsized_label)

    def get_deletion_extra_warning(self):
        return {'level': 'warning', 'message': _('Warning: this field data will be permanently deleted.')}

    def redirect_field_anchor(self, field):
        anchor = '#fieldId_%s' % field.id if field else ''
        if self.page_id:
            # check page_id is (still) a valid page number
            if self.page_id in (x.id for x in self.objectdef.fields):
                return redirect('../%s' % anchor)
            return redirect('../../../%s' % anchor)
        return redirect('../../fields/%s' % anchor)

    def delete(self):
        form = Form(enctype='multipart/form-data')
        ellipsized_field_label = misc.ellipsize(self.field.unhtmled_label, 60)
        if self.field.key == 'page':
            remove_top_title = _('Delete Page')
            remove_title = _('Deleting Page: %s') % ellipsized_field_label
            remove_message = _("You are about to remove the \"%s\" page.") % ellipsized_field_label
        else:
            remove_top_title = _('Delete Field')
            remove_title = _('Deleting Field: %s') % ellipsized_field_label
            remove_message = _("You are about to remove the \"%s\" field.") % ellipsized_field_label
        form.widgets.append(HtmlWidget('<p>%s</p>' % remove_message))
        if self.field.key not in ('page', 'subtitle', 'title', 'comment'):
            warning = self.get_deletion_extra_warning()
            if warning:
                form.widgets.append(HtmlWidget('<div class="%(level)snotice">%(message)s</div>' % warning))
        current_field_index = self.objectdef.fields.index(self.field)
        to_be_deleted = []
        if self.field.key == 'page':
            # get fields of the page and store indexes for deletion
            for index in range(current_field_index + 1, len(self.objectdef.fields)):
                field = self.objectdef.fields[index]
                if field.key == 'page':
                    # next page found; break
                    break
                to_be_deleted.append(index)
            to_be_deleted.reverse()
            # add delete_fields checkbox only if the page has fields
            if to_be_deleted:
                form.add(
                    CheckboxWidget,
                    'delete_fields',
                    title=_('Also remove all fields from the page'),
                    attrs={'data-dynamic-display-parent': 'true'},
                )
                form.widgets.append(
                    HtmlWidget(
                        '<div class="warningnotice" '
                        'data-dynamic-display-child-of="delete_fields" '
                        'data-dynamic-display-checked="true">%s</div>'
                        % _('Warning: the page fields data will be permanently deleted.')
                    )
                )
        form.add_submit('delete', _('Delete'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return self.redirect_field_anchor(self.field)
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('delete', _('Delete')))
            get_response().set_title(title=remove_top_title)
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % remove_title
            r += form.render()
            return r.getvalue()

        # delete page fields if requested
        delete_fields = form.get_widget('delete_fields')
        if delete_fields and delete_fields.parse():
            for index in to_be_deleted:
                del self.objectdef.fields[index]
        # delete current field
        del self.objectdef.fields[current_field_index]
        self.objectdef.store(comment=_('Deletion of field "%s"') % self.field.ellipsized_label)
        # redirect to the field that was above this one
        if self.objectdef.fields:
            if current_field_index == 0:
                above_field = self.objectdef.fields[0]
            else:
                above_field = self.objectdef.fields[current_field_index - 1]
        else:
            above_field = None
        return self.redirect_field_anchor(above_field)

    def duplicate(self):
        if self.field.key == 'page':
            return self.duplicate_page(self.get_page_fields())
        field_pos = self.objectdef.fields.index(self.field)
        fields = self.objectdef.fields
        new_field = copy.deepcopy(self.field)
        # allocate a new id
        new_field.id = self.objectdef.get_new_field_id()
        fields.insert(field_pos + 1, new_field)
        self.objectdef.store(comment=_('Duplication of field "%s"') % self.field.unhtmled_label)
        return self.redirect_field_anchor(new_field)

    def get_page_fields(self):
        current_field_index = self.objectdef.fields.index(self.field)
        page_fields = []
        # get fields of the page
        for index in range(current_field_index + 1, len(self.objectdef.fields)):
            field = self.objectdef.fields[index]
            if field.key == 'page':
                # next page found; break
                break
            page_fields.append(field)
        return page_fields

    def duplicate_page(self, page_fields):
        form = Form(enctype='multipart/form-data')
        ellipsized_field_label = misc.ellipsize(self.field.unhtmled_label, 60)
        duplicate_top_title = _('Duplicate Page')
        duplicate_title = _('Duplicating Page: %s') % ellipsized_field_label
        duplicate_message = _("You are about to duplicate the \"%s\" page.") % ellipsized_field_label
        form.widgets.append(HtmlWidget('<p>%s</p>' % duplicate_message))
        if page_fields:
            form.add(CheckboxWidget, 'duplicate_fields', title=_('Also duplicate all fields of the page'))
        form.add_submit('submit', _('Duplicate'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return self.redirect_field_anchor(self.field)
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('duplicate', _('Duplicate')))
            get_response().set_title(title=duplicate_top_title)
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s</h2>') % duplicate_title
            r += form.render()
            return r.getvalue()

        duplicate_fields = form.get_widget('duplicate_fields')
        to_be_duplicated = [self.field]
        if duplicate_fields and duplicate_fields.parse():
            # duplicate page fields if requested
            to_be_duplicated += page_fields
        new_fields = []
        # duplicate fields
        for field in to_be_duplicated:
            new_field = copy.deepcopy(field)
            # allocate a new id
            new_field.id = self.objectdef.get_new_field_id()
            new_fields.append(new_field)
        # insert new fields
        last_duplicated_field_index = self.objectdef.fields.index(([self.field] + page_fields)[-1])
        self.objectdef.fields = (
            self.objectdef.fields[: last_duplicated_field_index + 1]
            + new_fields
            + self.objectdef.fields[last_duplicated_field_index + 1 :]
        )
        # and store them
        self.objectdef.store(comment=_('Duplication of field "%s"') % self.field.ellipsized_label)
        # redirect to the new page field
        return self.redirect_field_anchor(new_fields[0])


class FieldsPagesDirectory(Directory):
    def __init__(self, parent):
        self.parent = parent

    def _q_lookup(self, component):
        directory = FieldsDirectory(self.parent.objectdef)
        directory.field_var_prefix = self.parent.field_var_prefix
        try:
            directory.page_id = str(component)
        except ValueError:
            raise errors.TraversalError()
        return directory


class FieldsDirectory(Directory, DocumentableMixin):
    _q_exports = [
        '',
        'update_order',
        'move_page_fields',
        'new',
        'pages',
        ('update-documentation', 'update_documentation'),
    ]
    field_def_page_class = FieldDefPage
    blacklisted_types = []
    page_id = None
    field_var_prefix = '..._'
    readonly_message = _('The fields are readonly.')
    new_field_history_message = _('New field "%s"')
    field_count_message = _('This form contains %d fields.')
    field_over_count_message = _('This form contains more than %d fields.')
    support_import = True

    def __init__(self, objectdef):
        self.objectdef = objectdef
        self.documented_object = self.objectdef
        self.documented_element = self.objectdef
        self.pages = FieldsPagesDirectory(self)

    def _q_traverse(self, path):
        if self.page_id:
            try:
                page_field = [x for x in self.objectdef.fields if x.id == self.page_id][0]
            except IndexError:
                raise errors.TraversalError()
            label = misc.ellipsize(page_field.unhtmled_label, 40)
            get_response().breadcrumb.append(('pages/%s/' % self.page_id, _('Page "%s"') % label))
        else:
            get_response().breadcrumb.append(('fields/', _('Fields')))
        return Directory._q_traverse(self, path)

    def _q_lookup(self, component):
        d = self.field_def_page_class(self.objectdef, component)
        d.page_id = self.page_id
        return d

    def _q_index(self):
        get_response().set_title(self.objectdef.name)
        get_response().add_javascript(['jquery.js', 'jquery-ui.js', 'biglist.js'])

        r = TemplateIO(html=True)

        r += self.index_top()
        ignore_hard_limits = get_publisher().has_site_option('ignore-hard-limits')

        if self.page_id and self.page_id not in (x.id for x in self.objectdef.fields or []):
            raise errors.TraversalError()

        if self.objectdef.fields:
            if len(self.objectdef.fields) >= self.objectdef.fields_count_total_hard_limit:
                r += htmltext('<div class="errornotice">')
                r += htmltext(self.field_count_message % len(self.objectdef.fields))
                r += htmltext(' ')
                if ignore_hard_limits:
                    r += htmltext(_('It is over system limits and no new fields should be added.'))
                else:
                    r += htmltext(_('It is over system limits and no new fields can be added.'))
                r += htmltext('</div>')
            elif len(self.objectdef.fields) > self.objectdef.fields_count_total_soft_limit:
                r += htmltext('<div class="warningnotice">')
                r += htmltext(self.field_over_count_message % self.objectdef.fields_count_total_soft_limit)
                r += htmltext(' ')
                r += htmltext(_('It is close to the system limits and no new fields should be added.'))
                r += htmltext('</div>')
            elif (
                hasattr(self.objectdef, 'get_total_count_data_fields')
                and self.objectdef.get_total_count_data_fields() > 2000
            ):
                # warn before DATA_UPLOAD_MAX_NUMBER_FIELDS
                r += htmltext('<div class="warningnotice">')
                r += htmltext('<p>%s %s</p>') % (
                    _('There are at least %d data fields, including fields in blocks.')
                    % self.objectdef.get_total_count_data_fields(),
                    _('It is close to the system limits and no new fields should be added.'),
                )
                r += htmltext('</div>')

            if [x for x in self.objectdef.fields if x.key == 'page']:
                if self.objectdef.fields[0].key != 'page':
                    r += htmltext('<div class="errornotice">')
                    r += htmltext(_('In a multipage form, the first field should be of type "page".'))
                    r += htmltext('</div>')

            if self.page_id is not None:
                page_ids = [str(x.id) for x in self.objectdef.fields if x.key == 'page']
                r += htmltext('<p class="form-pages-navigation">')
                if self.page_id == page_ids[0]:
                    r += htmltext('<a class="pk-button disabled" href=".">%s</a>') % _('Previous page')
                else:
                    previous_page_id = page_ids[page_ids.index(self.page_id) - 1]
                    r += htmltext('<a class="pk-button" href="../%s/">%s</a>') % (
                        previous_page_id,
                        _('Previous page'),
                    )
                r += htmltext('<a class="pk-button" href="../../">%s</a>') % _('All pages')
                if self.page_id == page_ids[-1]:
                    r += htmltext('<a class="pk-button disabled" href=".">%s</a>') % _('Next page')
                else:
                    next_page_id = page_ids[page_ids.index(self.page_id) + 1]
                    r += htmltext('<a class="pk-button" href="../%s/">%s</a>') % (
                        next_page_id,
                        _('Next page'),
                    )
                r += htmltext('</p>')

            r += htmltext('<p class="hint">%s</p>') % _(
                'Use drag and drop with the handles to reorder fields.'
            )

            extra_classes = []
            if [x for x in self.objectdef.fields if x.key == 'page']:
                extra_classes.append('multipage')
            if self.objectdef.is_readonly():
                extra_classes.append('readonly')

            r += htmltext(
                '<ul id="fields-list" class="biglist sortable %s" data-page-no-label="%s">'
                % (' '.join(extra_classes), _('Page #%s:') % '***')
            )
            current_page_no = 0
            on_page = False
            for field in self.objectdef.fields:
                if field.key == 'page':
                    current_page_no += 1
                    on_page = bool(str(field.id) == self.page_id)

                li_attrs = {
                    'id': 'fieldId_%s' % field.id,
                    'data-id': str(field.id),
                    'class': 'biglistitem type-%s' % field.key,
                }
                if self.page_id and not on_page:
                    li_attrs['style'] = 'display: none;'
                if self.page_id and field.key == 'page':
                    li_attrs['class'] += ' page-in-multipage'

                r += htmltag('li', **li_attrs)
                type_label = field.get_type_label()
                if field.key in ('subtitle', 'title', 'comment'):
                    label = misc.ellipsize(field.unhtmled_label, 60)
                    r += htmltext(f'<a href="{field.id}/" class="biglistitem--content">')
                    if field.key in ('subtitle', 'title'):
                        r += htmltext('<span class="fields-list--%s" id="label%s">%s</span>') % (
                            field.key,
                            field.id,
                            label,
                        )
                    else:
                        r += htmltext('<span class="fields-list--comment" id="label%s">%s</span>') % (
                            field.id,
                            label,
                        )
                    r += htmltext('<span class="biglistitem--content-details">')
                    r += htmltext('<span class="type">%s</span>') % _(type_label)
                    if getattr(field, 'condition', None):
                        r += htmltext(' - <span class="condition">%s</span>') % _('depending on condition')
                    r += htmltext('</span></a>')
                    r += htmltext('<p class="commands">')
                else:
                    r += htmltext(f'<a href="{field.id}/" class="biglistitem--content">')
                    r += htmltext('<span class="fields-list--%s" id="label%s">' % (field.key, field.id))
                    if field.key == 'page':
                        r += htmltext('<span class="page-no">%s</span> ') % _('Page #%s:') % current_page_no
                    r += htmltext('%s</span>') % field.label
                    r += htmltext('<span class="biglistitem--content-details">')
                    if field.key != 'page':
                        r += htmltext('<span class="type">%s</span>') % _(type_label)
                    if hasattr(field, 'required'):
                        if field.required == 'optional':
                            required = ' - %s' % _('optional')
                        elif field.required == 'frontoffice':
                            required = ' - %s' % _('required only in frontoffice')
                        else:
                            required = ''
                        r += htmltext('<span class="optional">%s</span>') % required
                    if getattr(field, 'condition', None):
                        r += htmltext(' - <span class="condition">%s</span>') % _('depending on condition')
                    if field.key == 'page' and field.post_conditions:
                        r += htmltext(' - <span>%s</span>') % _('with post-conditions')
                    if (
                        field.key != 'page'
                        and getattr(field, 'varname', None)
                        and CompatibilityNamesDict.valid_key_regex.match(field.varname)
                    ):
                        varname = f'{self.field_var_prefix}{field.varname}'
                        breaking_varname = htmlescape(varname).replace('_', htmltext('_<wbr/>'))
                        r += htmltext(' - <span class="varname">{{ %s }}</span>' % breaking_varname)
                    r += htmltext('</span></a>')
                    r += htmltext('<p class="commands">')
                    if field.key == 'page' and self.page_id is None:
                        r += command_icon(
                            'pages/%s/' % field.id, 'view', label=_('Limit display to this page')
                        )
                if not self.objectdef.is_readonly():
                    if (
                        len(self.objectdef.fields) < self.objectdef.fields_count_total_hard_limit
                        or ignore_hard_limits
                    ):
                        r += command_icon('%s/duplicate' % field.id, 'duplicate', popup=(field.key == 'page'))
                    r += command_icon('%s/delete' % field.id, 'remove', popup=True)
                r += htmltext('</p></li>')
            r += htmltext('</ul>')

        if self.objectdef.is_readonly():
            get_response().filter['sidebar'] = (
                htmltext('<div class="infonotice"><p>%s</p></div>') % self.readonly_message
            )
            if hasattr(self.objectdef, 'snapshot_object'):
                url_prefix = '../../../'
                url_suffix = 'fields/'
                from wcs.blocks import BlockDef

                if isinstance(self.objectdef, BlockDef):
                    url_prefix = '../../'
                    url_suffix = ''
                if self.page_id is not None:
                    url_prefix += '../../'
                    url_suffix += 'pages/%s/' % self.page_id

                get_response().filter['sidebar'] += utils.snapshot_info_block(
                    snapshot=self.objectdef.snapshot_object, url_prefix=url_prefix, url_suffix=url_suffix
                )
        else:
            get_response().filter['sidebar'] = str(self.get_new_field_form_sidebar(self.page_id))
        r += self.index_bottom()
        return r.getvalue()

    def get_new_field_form_sidebar(self, page_id):
        r = TemplateIO(html=True)
        ignore_hard_limits = get_publisher().has_site_option('ignore-hard-limits')

        if len(self.objectdef.fields) >= self.objectdef.fields_count_total_hard_limit:
            if not ignore_hard_limits:
                r += htmltext('<div class="errornotice"><p>%s %s</p></div>') % (
                    self.field_count_message % len(self.objectdef.fields),
                    _('It is over system limits and no new fields can be added.'),
                )
                return r.getvalue()

        r += htmltext('<h3>%s</h3>') % _('New Field')
        r += htmltext('<div id="new-field">')
        get_request().form = None  # ignore the eventual ?page=x
        form = self.get_new_field_form(page_id)
        r += form.render()
        if self.support_import:
            form = self.get_import_fields_from(page_id)
            if form:
                r += form.render()
        r += htmltext('</div>')
        return r.getvalue()

    def get_new_field_form(self, page_id):
        form = Form(enctype='multipart/form-data', action='new', id='new-field')
        if page_id:
            form.add_hidden('page_id', page_id)
        form.add(StringWidget, 'label', title=_('Label'), required=True, size=50)
        form.add(
            SingleSelectWidget,
            'type',
            title=_('Type'),
            value='string',
            required=True,
            options=get_field_options(self.blacklisted_types),
        )
        form.add_submit('submit', _('Add'))
        return form

    def get_import_fields_from(self, page_id):
        formdefs = FormDef.select(order_by='name', lightweight=True, ignore_errors=True)
        carddefs = CardDef.select(order_by='name', lightweight=True, ignore_errors=True)
        if not (formdefs or carddefs):
            return
        options = [(None, '\u2500' * 5, '')]
        if formdefs and carddefs:
            options.append(OptGroup(_('Forms')))
        options.extend([(f'form:{x.id}', x.name, f'form:{x.id}') for x in formdefs])
        if formdefs and carddefs:
            options.append(OptGroup(_('Card Models')))
        options.extend([(f'card:{x.id}', x.name, f'card:{x.id}') for x in carddefs])
        form = Form(enctype='multipart/form-data', action='new', id='import-fields')
        if page_id:
            form.add_hidden('page_id', page_id)
        form.add(
            SingleSelectWidget,
            'form',
            title=_('Or import fields from:'),
            required=True,
            options=options,
        )
        form.add_submit('submit', _('Submit'))
        return form

    def index_top(self):
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s') % self.objectdef.name
        if self.page_id:
            current_page_no = 0
            for field in self.objectdef.fields:
                if field.key == 'page':
                    current_page_no += 1
                if str(field.id) == self.page_id:
                    r += ' - '
                    r += str(_('page %d') % current_page_no)
                    r += ' - '
                    r += field.label
        r += htmltext('</h2>')
        r += get_session().display_message()
        if not self.objectdef.fields:
            r += htmltext('<div class="infonotice">%s</div>') % _('There are not yet any fields defined.')
        return r.getvalue()

    def index_bottom(self):
        pass

    def update_order(self):
        get_response().set_content_type('application/json')
        request = get_request()

        if 'element' not in request.form:
            return json.dumps({'success': 'ko'})
        if 'order' not in request.form:
            return json.dumps({'success': 'ko'})

        dropped_element = request.form['element']
        dropped_page_index = None

        new_order = request.form['order'].strip(';').split(';')
        new_fields = []

        # build new ordered field list
        for y in new_order:
            for i, x in enumerate(self.objectdef.fields):
                if x.id != y:
                    continue
                new_fields.append(x)
                # if dropped field is a page, keep it's old index
                if x.id == dropped_element and x.key == 'page':
                    dropped_page_index = i
                break

        # get the list of dropped page fields from old field list
        page_field_ids = []
        if dropped_page_index is not None:
            for field in self.objectdef.fields[dropped_page_index + 1 :]:
                if field.key == 'page':
                    # next page found; break
                    break
                page_field_ids.append(field.id)

        # check new field list composition
        if set(self.objectdef.fields) != set(new_fields):
            return json.dumps({'success': 'ko'})

        self.objectdef.fields = new_fields
        self.objectdef.store(comment=_('Change in order of fields'))

        if not page_field_ids:
            return json.dumps({'success': 'ok'})

        # propose to move also page fields
        return json.dumps(
            {
                'success': 'ok',
                'additional-action': {
                    'message': str(_('Also move the fields of the page')),
                    'url': 'move_page_fields?fields=%s&page=%s' % (';'.join(page_field_ids), dropped_element),
                },
            }
        )

    def move_page_fields(self):
        request = get_request()

        if 'fields' not in request.form:
            return redirect('.')
        if 'page' not in request.form:
            return redirect('.')

        field_ids = request.form['fields'].strip(';').split(';')
        # keep all fields except page fields
        new_fields = [f for f in self.objectdef.fields if f.id not in field_ids]
        # find page fields
        page_fields = [f for f in self.objectdef.fields if f.id in field_ids]
        # find page in new fields, and insert page_fields
        for i, field in enumerate(new_fields):
            if field.id != request.form['page']:
                continue
            new_fields = new_fields[: i + 1] + page_fields + new_fields[i + 1 :]
            break

        # check new field list composition
        if set(self.objectdef.fields) != set(new_fields):
            return redirect('.')

        self.objectdef.fields = new_fields
        self.objectdef.store(comment=_('Change in order of fields'))

        return redirect('.')

    def new(self):
        form = Form(enctype='multipart/form-data')
        form.add_hidden('page_id')
        form.add_hidden('label')
        form.add_hidden('type')
        form.add_hidden('form')

        if not form.is_submitted():
            get_session().add_message(_('Submitted form was not filled properly.'))
            return redirect('.')

        page_id = form.get_widget('page_id').parse()

        redirect_url = '.'
        if page_id:
            redirect_url = './?page=%s' % page_id
            on_page = False
            for i, field in enumerate(self.objectdef.fields):
                if field.key == 'page':
                    if on_page:
                        break
                    if str(field.id) == str(page_id):
                        on_page = True
            else:
                i += 1
            insertion_point = i
        else:
            insertion_point = len(self.objectdef.fields)

        field_type = form.get_widget('type').parse()
        if form.get_widget('label').parse() and field_type:
            label = form.get_widget('label').parse()
            if field_type == 'comment' and not label.startswith('<'):
                label = '<p>%s</p>' % htmlescape(label)
            kwargs = {
                'label': label,
                'id': self.objectdef.get_new_field_id(),
            }
            if field_type.startswith('block:'):
                kwargs['block_slug'] = field_type.removeprefix('block:')
            field = fields.get_field_class_by_type(field_type)(**kwargs)
            if not field.is_no_data_field:
                field.varname = re.sub(r'^[0-9_]+', '', misc.simplify(field.label, space='_'))
            self.objectdef.fields.insert(insertion_point, field)
            self.objectdef.store(comment=self.new_field_history_message % field.ellipsized_label)
        elif form.get_widget('form') and form.get_widget('form').parse():
            form_class, form_id = form.get_widget('form').parse().split(':')
            if form_class == 'form':
                formdef = FormDef.get(form_id)
            else:
                formdef = CardDef.get(form_id)
            for j, field in enumerate(formdef.fields):
                field.id = self.objectdef.get_new_field_id()
                self.objectdef.fields.insert(insertion_point + j, field)
            self.objectdef.store(comment=_('Import of fields from "%s"') % formdef.name)
        else:
            get_session().add_message(_('Submitted form was not filled properly.'))

        return redirect(redirect_url)

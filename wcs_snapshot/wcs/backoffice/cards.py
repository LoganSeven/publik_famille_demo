# w.c.s. - web application for online forms
# Copyright (C) 2005-2019  Entr'ouvert
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

from quixote import get_publisher, get_request, get_response, redirect
from quixote.html import TemplateIO, htmltext

from wcs.admin import utils
from wcs.admin.categories import CardDefCategoriesDirectory
from wcs.admin.forms import (
    FormDefPage,
    FormDefUI,
    FormFieldDefPage,
    FormFieldsDirectory,
    FormsDirectory,
    OptionsDirectory,
)
from wcs.carddef import CardDef, get_cards_graph
from wcs.categories import CardDefCategory
from wcs.sql_criterias import Null, StrictNotEqual

from ..qommon import _, pgettext_lazy
from ..qommon.form import CheckboxesWidget, ComputedExpressionWidget, Form, RadiobuttonsWidget, StringWidget


class CardDefUI(FormDefUI):
    formdef_class = CardDef
    category_class = CardDefCategory
    section = 'cards'


class CardDefOptionsDirectory(OptionsDirectory):
    category_class = CardDefCategory
    category_empty_choice = _('Select a category for this card model')
    backoffice_submission_options_label = _('Submission')
    section = 'cards'

    def get_templates_form(self):
        form = super().get_templates_form()
        if not get_publisher().has_site_option('enable-card-identifier-template'):
            return form
        criterias = [
            StrictNotEqual('status', 'draft'),
        ]
        kwargs = {
            'hint': _(
                'The template should produce a unique identifier with only letters, '
                'lowercase or uppercase (without accents), digits, dashes and underscores. '
                'Other characters will be automatically replaced or removed.'
            )
        }
        if self.formdef.data_class().exists(criterias):
            kwargs['readonly'] = True
            kwargs['hint'] = _('Identifier cannot be modified if there are existing cards.')
        form.add(
            StringWidget,
            'id_template',
            title=_('Unique identifier template'),
            value=self.formdef.id_template,
            validation_function=ComputedExpressionWidget.validate_template,
            size=50,
            **kwargs,
        )
        return form

    def management(self):
        form = Form(enctype='multipart/form-data')
        form.add(
            CheckboxesWidget,
            'management_sidebar_items',
            title=_('Sidebar elements'),
            options=[(x[0], x[1], x[0]) for x in self.formdef.get_management_sidebar_available_items()],
            value=self.formdef.get_management_sidebar_items(),
            inline=False,
        )
        form.add(
            RadiobuttonsWidget,
            'history_pane_default_mode',
            title=_('History pane default mode'),
            options=[('collapsed', _('Collapsed'), 'collapsed'), ('expanded', _('Expanded'), 'expanded')],
            value=self.formdef.history_pane_default_mode,
            extra_css_class='widget-inline-radio',
        )
        return self.handle(form, pgettext_lazy('cards', 'Management'))


class CardFieldDefPage(FormFieldDefPage):
    section = 'cards'
    deletion_extra_warning_message = _(
        'Warning: this field data will be permanently deleted from existing cards.'
    )

    def get_deletion_extra_warning(self):
        warning = super().get_deletion_extra_warning()
        if warning and self.field.varname and self.objectdef.id_template:
            varnames = self.field.get_referenced_varnames(self.objectdef, self.objectdef.id_template)
            if self.field.varname in varnames:
                warning['level'] = 'error'
                warning['message'] = htmltext('%s<br>%s') % (
                    warning['message'],
                    _(
                        'This field may be used in the card custom identifiers, '
                        'its removal may render cards unreachable.'
                    ),
                )
        return warning


class CardFieldsDirectory(FormFieldsDirectory):
    field_def_page_class = CardFieldDefPage
    field_count_message = _('This card model contains %d fields.')
    field_over_count_message = _('This card model contains more than %d fields.')
    readonly_message = _('This card model is readonly.')

    def index_bottom(self):
        if self.objectdef.is_readonly():
            return
        if any(
            'display_locations' in x.get_admin_attributes() for x in self.objectdef.fields or []
        ) and not any(x.include_in_listing for x in self.objectdef.fields):
            r = TemplateIO(html=True)
            r += htmltext('<div class="pk-information">')
            r += htmltext('<p>%s</p>') % _('There are no fields configured to be shown in listings.')
            r += htmltext('<p>%s</p>') % _(
                'You should check the "Management Listings" box '
                'of the "Display" panel for at least one field.'
            )
            r += htmltext('</div>')
            return r.getvalue()


class CardDefPage(FormDefPage):
    formdef_class = CardDef
    formdef_export_prefix = 'card'
    formdef_ui_class = CardDefUI
    formdef_default_workflow = '_carddef_default'
    section = 'cards'

    options_directory_class = CardDefOptionsDirectory
    fields_directory_class = CardFieldsDirectory

    formdef_template_name = 'wcs/backoffice/carddef.html'
    delete_message = _('You are about to irrevocably delete this card model.')
    delete_title = _('Deleting Card Model:')
    duplicate_title = _('Duplicate Card Model')
    overwrite_message = _('You can replace this card model by uploading a file or by pointing to a form URL.')
    overwrite_success_message = _(
        'The card model has been successfully overwritten. '
        'Do note it kept its existing address and role and workflow parameters.'
    )
    backoffice_submission_role_label = _('Creation Roles')
    backoffice_submission_role_description = _(
        'Select the roles that will be allowed to create cards of this kind.'
    )

    def get_option_lines(self):
        options = super().get_option_lines()
        options['backoffice_submission_roles'] = self.add_option_line(
            'backoffice-submission-roles',
            self.backoffice_submission_role_label,
            self._get_roles_label('backoffice_submission_roles'),
        )
        options['management'] = self.add_option_line(
            'options/management',
            pgettext_lazy('cards', 'Management'),
            (
                _('Custom')
                if self.formdef.history_pane_default_mode != 'collapsed'
                or self.formdef.management_sidebar_items
                not in ({'__default__'}, self.formdef.get_default_management_sidebar_items())
                else _('Default')
            ),
        )
        return options

    def get_sorted_usage_in_formdefs(self):
        formdefs = list(self.formdef.usage_in_formdefs())
        formdefs.sort(key=lambda x: x.name.lower())
        return formdefs

    def duplicate_submit(self, form):
        response = super().duplicate_submit(form)
        self.formdefui.formdef.disabled = False
        self.formdefui.formdef.store()
        return response

    def get_check_deletion_message(self):
        if self.formdef.is_used():
            return _('Deletion is not possible as it is still used as datasource.')

        criterias = [
            StrictNotEqual('status', 'draft'),
            Null('anonymised'),
        ]
        if self.formdef.data_class().count(criterias):
            return _('Deletion is not possible as there are cards.')


class CardsDirectory(FormsDirectory):
    _q_exports = [
        '',
        'new',
        ('import', 'p_import'),
        'categories',
        'svg',
        ('application', 'applications_dir'),
        ('by-slug', 'by_slug'),
    ]

    by_slug = utils.BySlugDirectory(klass=CardDef)
    category_class = CardDefCategory
    categories = CardDefCategoriesDirectory()
    formdef_class = CardDef
    formdef_page_class = CardDefPage
    formdef_ui_class = CardDefUI

    section = 'cards'
    top_title = _('Card Models')
    index_template_name = 'wcs/backoffice/cards.html'
    import_title = _('Import Card Model')
    import_submit_label = _('Import Card Model')
    import_paragraph = _(
        'You can install a new card model by uploading a file or by pointing to the card model URL.'
    )
    import_loading_error_message = _('Error loading card model (%s).')
    import_success_message = _('This card model has been successfully imported. ')
    import_error_message = _(
        'Imported card model contained errors and has been automatically fixed, '
        'you should nevertheless check everything is ok. '
    )
    import_slug_change = _(
        'The card model identifier (%(slug)s) was already used by another card model. '
        'A new one has been generated (%(newslug)s).'
    )

    def get_extra_index_context_data(self):
        context = super().get_extra_index_context_data()
        context['is_global_accessible_cards'] = (
            get_publisher().get_backoffice_root().is_global_accessible('cards')
        )
        return context

    def new(self):
        get_response().breadcrumb.append(('new', _('New')))
        formdefui = self.formdef_ui_class(None)
        form = formdefui.new_form_ui()
        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            try:
                formdef = formdefui.submit_form(form)
                formdef.disabled = False
                formdef.store()
            except ValueError:
                pass
            else:
                return redirect(str(formdef.id) + '/')

        get_response().set_title(_('New Card Model'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('New Card Model')
        r += form.render()
        return r.getvalue()

    def import_submit(self, form):
        response = super().import_submit(form)
        if self.imported_formdef:
            self.imported_formdef.disabled = False
            self.imported_formdef.store()
        return response

    def svg(self):
        response = get_response()
        response.set_content_type('image/svg+xml')
        show_orphans = get_request().form.get('show-orphans') == 'on'
        return get_cards_graph(show_orphans=show_orphans)

    def _q_lookup(self, component):
        return self.formdef_page_class(component)

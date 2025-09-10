# w.c.s. - web application for online forms
# Copyright (C) 2005-2020  Entr'ouvert
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

from quixote import get_publisher, get_request, get_response, get_session, redirect
from quixote.directory import Directory
from quixote.html import TemplateIO, htmltext

from wcs.admin import utils
from wcs.admin.categories import BlockCategoriesDirectory, get_categories
from wcs.admin.fields import FieldDefPage, FieldsDirectory
from wcs.backoffice.applications import ApplicationsDirectory
from wcs.backoffice.deprecations import DeprecationsDirectory
from wcs.backoffice.snapshots import SnapshotsDirectory
from wcs.blocks import BlockDef, BlockdefImportError
from wcs.categories import BlockCategory
from wcs.fields import get_field_options
from wcs.fields.page import PostConditionsTableWidget
from wcs.qommon import _, misc, template
from wcs.qommon.errors import AccessForbiddenError, TraversalError
from wcs.qommon.form import (
    FileWidget,
    Form,
    HtmlWidget,
    OptGroup,
    SingleSelectWidget,
    SlugWidget,
    StringWidget,
)


class BlockFieldDefPage(FieldDefPage):
    def redirect_field_anchor(self, field):
        anchor = '#fieldId_%s' % field.id if field else ''
        return redirect('../%s' % anchor)

    def schedule_statistics_data_update(self):
        from wcs.formdef_jobs import UpdateStatisticsDataAfterJob

        get_publisher().add_after_job(
            UpdateStatisticsDataAfterJob(formdefs=self.objectdef.get_usage_formdefs())
        )


class BlockDirectory(FieldsDirectory):
    _q_exports = [
        '',
        'update_order',
        'new',
        'delete',
        'export',
        'settings',
        'inspect',
        'duplicate',
        ('history', 'snapshots_dir'),
        'overwrite',
        ('update-documentation', 'update_documentation'),
    ]
    field_def_page_class = BlockFieldDefPage
    blacklisted_types = ['page', 'table', 'table-select', 'tablerows', 'ranked-items', 'blocks', 'computed']
    support_import = False
    readonly_message = _('This block of fields is readonly.')

    field_count_message = _('This block of fields contains %d fields.')
    field_over_count_message = _('This block of fields contains more than %d fields.')

    def __init__(self, *args, **kwargs):
        kwargs.pop('component', None)  # snapshot
        if 'instance' in kwargs:
            kwargs['objectdef'] = kwargs.pop('instance')
        super().__init__(*args, **kwargs)
        self.snapshots_dir = SnapshotsDirectory(self.objectdef)

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('%s/' % self.objectdef.id, self.objectdef.name))
        return Directory._q_traverse(self, path)

    def _q_index(self):
        from wcs.applications import Application

        if not self.objectdef.is_readonly():
            Application.load_for_object(self.objectdef)
        html = super()._q_index()
        if self.objectdef.applications:
            r = TemplateIO(html=True)
            r += htmltext(get_response().filter['sidebar'])
            r += htmltext('<h3>%s</h3>') % _('Applications')
            for application in self.objectdef.applications:
                r += (
                    htmltext('<a class="button button-paragraph" href="../application/%s/">')
                    % application.slug
                )
                if application.icon:
                    r += (
                        htmltext(
                            '<img src="../application/%s/icon" alt="" class="application-icon" width="16" /> '
                        )
                        % application.slug
                    )
                r += htmltext('%s</a>') % application.name
            get_response().filter['sidebar'] = r.getvalue()
        return html

    def index_top(self):
        r = TemplateIO(html=True)
        r += htmltext('<div id="appbar">')
        r += htmltext('<div>')
        r += htmltext('<h2 class="appbar--title">%s</h2>') % self.objectdef.name
        if self.objectdef.category_id:
            r += htmltext('<p class="appbar--category-subtitle">%s</p>') % self.objectdef.category.name
        r += htmltext('</div>')
        r += htmltext('<span class="actions">')
        r += htmltext('<a class="extra-actions-menu-opener"></a>')
        r += htmltext('<ul class="extra-actions-menu">')
        r += htmltext('<li><a href="export">%s</a></li>') % _('Export')
        r += htmltext('<li><a href="delete" rel="popup">%s</a></li>') % _('Delete')
        r += htmltext('</ul>')
        r += self.get_documentable_button()
        r += htmltext('<a href="settings" role="button">%s</a>') % _('Settings')
        r += htmltext('</span>')
        r += htmltext('</div>')
        r += utils.last_modification_block(obj=self.objectdef)
        r += get_session().display_message()
        r += self.get_documentable_zone()

        if not self.objectdef.fields:
            r += htmltext('<div class="infonotice">%s</div>') % _('There are not yet any fields defined.')
        return r.getvalue()

    def index_bottom(self):
        formdefs = list(self.objectdef.get_usage_formdefs())
        formdefs.sort(key=lambda x: x.name.lower())
        if not formdefs:
            return
        r = TemplateIO(html=True)
        r += htmltext('<div class="section">')
        r += htmltext('<h3>%s</h3>') % _('Usage')
        r += htmltext('<ul class="objects-list single-links">')
        for formdef in formdefs:
            r += htmltext('<li><a href="%s">' % formdef.get_admin_url())
            r += htmltext('%s</a></li>') % formdef.name
        r += htmltext('</ul>')
        r += htmltext('</div>')
        return r.getvalue()

    def get_new_field_form_sidebar(self, page_id):
        r = TemplateIO(html=True)
        r += super().get_new_field_form_sidebar(page_id=page_id)
        r += htmltext('<h3>%s</h3>') % _('Actions')
        r += htmltext('<ul class="sidebar--buttons">')
        r += htmltext('<li><a class="button button-paragraph" href="duplicate" rel="popup">%s</a>') % _(
            'Duplicate'
        )
        if get_publisher().snapshot_class:
            r += htmltext('<li><a class="button button-paragraph" href="history/save">%s</a>') % _(
                'Save snapshot'
            )
        r += htmltext('<li><a class="button button-paragraph" rel="popup" href="overwrite">%s</a>') % _(
            'Overwrite with new import'
        )
        r += htmltext('</ul>')
        r += htmltext('<h3>%s</h3>') % _('Navigation')
        r += htmltext('<ul class="sidebar--buttons">')
        r += htmltext('<li><a class="button button-paragraph" href="history/">%s</a></li>') % _('History')
        r += htmltext('<li><a class="button button-paragraph" href="inspect">%s</a></li>') % _('Inspector')
        r += htmltext('</ul>')
        return r.getvalue()

    def delete(self):
        form = Form(enctype='multipart/form-data')
        if not self.objectdef.is_used():
            form.widgets.append(
                HtmlWidget('<p>%s</p>' % _('You are about to irrevocably delete this block.'))
            )
            form.add_submit('delete', _('Delete'))
        else:
            form.widgets.append(
                HtmlWidget('<p>%s</p>' % _('This block is still used, it cannot be deleted.'))
            )
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('..')
        if not form.is_submitted() or form.has_errors():
            get_response().breadcrumb.append(('delete', _('Delete')))
            get_response().set_title(_('Delete Block'))
            r = TemplateIO(html=True)
            r += htmltext('<h2>%s %s</h2>') % (_('Deleting Block:'), self.objectdef.name)
            r += form.render()
            return r.getvalue()

        get_publisher().snapshot_class.snap_deletion(self.objectdef)
        self.objectdef.remove_self()
        return redirect('..')

    def duplicate(self):
        form = Form(enctype='multipart/form-data')
        name_widget = form.add(StringWidget, 'name', title=_('Name'), required=True, size=30)
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if not form.is_submitted():
            original_name = self.objectdef.name
            new_name = '%s %s' % (original_name, _('(copy)'))
            names = [x.name for x in BlockDef.select()]
            no = 2
            while new_name in names:
                new_name = _('%(name)s (copy %(no)d)') % {'name': original_name, 'no': no}
                no += 1
            name_widget.set_value(new_name)

        if not form.is_submitted() or form.has_errors():
            get_response().set_title(_('Duplicate Block of Fields'))
            r = TemplateIO(html=True)
            get_response().breadcrumb.append(('duplicate', _('Duplicate')))
            r += htmltext('<h2>%s</h2>') % _('Duplicate Block of Fields')
            r += form.render()
            return r.getvalue()

        self.objectdef.id = None
        self.objectdef.slug = None
        self.objectdef.name = form.get_widget('name').parse()
        self.objectdef.store()
        return redirect('../%s/' % self.objectdef.id)

    def export(self):
        return misc.xml_response(
            self.objectdef,
            filename='block-%s.wcs' % self.objectdef.slug,
            content_type='application/x-wcs-form',
        )

    def overwrite(self):
        form = Form(enctype='multipart/form-data')
        form.widgets.append(
            HtmlWidget(
                '<div class="warningnotice"><p>%s</p></div>'
                % _('Field data will be lost if overwriting with an incompatible block.')
            )
        )
        form.add(FileWidget, 'file', title=_('File'), required=True)
        form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            try:
                return self.overwrite_submit(form)
            except ValueError:
                pass

        get_response().breadcrumb.append(('overwrite', _('Overwrite')))
        get_response().set_title(title=_('Overwrite'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Overwrite')
        r += form.render()
        return r.getvalue()

    def overwrite_submit(self, form):
        blockdef = BlocksDirectory.import_blockdef(form)
        self.objectdef.name = blockdef.name
        self.objectdef.digest_template = blockdef.digest_template
        self.objectdef.fields = blockdef.fields
        self.objectdef.post_conditions = blockdef.post_conditions
        self.objectdef.store(comment=_('Overwritten'))
        return redirect('.')

    def settings(self):
        get_response().breadcrumb.append(('settings', _('Settings')))
        form = Form()
        form.add(StringWidget, 'name', title=_('Name'), value=self.objectdef.name, size=50, required=True)
        disabled_slug = bool(self.objectdef.is_used())
        widget = form.add(
            SlugWidget,
            'slug',
            value=self.objectdef.slug,
            readonly=disabled_slug,
        )
        if disabled_slug:
            widget.hint = _('The identifier can not be modified as the block is in use.')

        category_options = get_categories(BlockCategory)
        if category_options:
            form.add(
                SingleSelectWidget,
                'category_id',
                title=_('Category'),
                options=category_options,
                value=self.objectdef.category_id,
            )

        form.add(
            StringWidget,
            'digest_template',
            title=_('Digest Template'),
            value=self.objectdef.digest_template,
            size=50,
            hint=_('Use block_var_... to refer to fields.'),
        )
        form.add(
            PostConditionsTableWidget,
            'post_conditions',
            title=_('Validation conditions'),
            value=self.objectdef.post_conditions,
        )

        if not self.objectdef.is_readonly():
            form.add_submit('submit', _('Submit'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors() and not form.get_submit() is True:
            self.objectdef.name = form.get_widget('name').parse()
            if form.get_widget('slug'):
                self.objectdef.slug = form.get_widget('slug').parse()
            # check there's no other block with the new slug
            block_with_slug = BlockDef.get_by_slug(self.objectdef.slug)
            if block_with_slug and block_with_slug.id != self.objectdef.id:
                form.get_widget('slug').set_error(_('This identifier is already used.'))
            if form.get_widget('category_id'):
                self.objectdef.category_id = form.get_widget('category_id').parse()
            self.objectdef.post_conditions = form.get_widget('post_conditions').parse()
            widget_template = form.get_widget('digest_template')
            if widget_template.parse() and 'form_var_' in widget_template.parse():
                widget_template.set_error(
                    _('Wrong variable "form_var_…" detected. Please replace it by "block_var_…".')
                )
            if not form.has_errors():
                self.objectdef.digest_template = widget_template.parse()
                self.objectdef.store()
                return redirect('.')

        get_response().set_title(_('Settings'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Settings')
        r += form.render()
        return r.getvalue()

    def inspect(self):
        get_response().set_title(self.objectdef.name)
        get_response().breadcrumb.append(('inspect', _('Inspector')))
        return self.render_inspect()

    def render_inspect(self):
        deprecations = DeprecationsDirectory()
        context = {
            'blockdef': self.objectdef,
            'view': self,
        }
        if not hasattr(self.objectdef, 'snapshot_object'):
            context.update(
                {
                    'deprecations': deprecations.get_deprecations(
                        f'{self.objectdef.xml_root_node}:{self.objectdef.id}'
                    ),
                    'deprecation_metadata': deprecations.metadata,
                }
            )
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/block-inspect.html'], context=context
        )


class BlocksDirectory(Directory):
    _q_exports = [
        '',
        'new',
        'categories',
        ('import', 'p_import'),
        ('application', 'applications_dir'),
        ('by-slug', 'by_slug'),
    ]
    do_not_call_in_templates = True
    by_slug = utils.BySlugDirectory(klass=BlockDef)
    categories = BlockCategoriesDirectory()

    def __init__(self):
        super().__init__()
        self.applications_dir = ApplicationsDirectory(BlockDef)

    def is_accessible(self, user=None):
        return BlockCategory.has_global_access() or any(BlockCategory.select_for_user())

    def _q_traverse(self, path):
        if not self.is_accessible():
            raise AccessForbiddenError()
        get_response().breadcrumb.append(('blocks/', _('Blocks of fields')))
        return super()._q_traverse(path)

    def _q_lookup(self, component):
        try:
            block = BlockDef.get(component)
        except KeyError:
            raise TraversalError()
        if not block.category and not BlockCategory.has_global_access():
            raise AccessForbiddenError()
        if block.category and not block.category.is_managed_by_user():
            raise AccessForbiddenError()
        return BlockDirectory(block)

    def _q_index(self):
        from wcs.applications import Application

        get_response().set_title(_('Blocks of fields'))
        get_response().add_javascript(['popup.js'])
        context = {
            'view': self,
            'applications': Application.select_for_object_type(BlockDef.xml_root_node),
            'elements_label': BlockDef.verbose_name_plural,
            'has_sidebar': True,
            'is_global_accessible_categories': get_publisher()
            .get_backoffice_root()
            .is_accessible('categories'),
        }
        blocks = BlockDef.select(order_by='name')
        Application.populate_objects(blocks)
        context.update(self.get_list_context(blocks))
        return template.QommonTemplateResponse(
            templates=['wcs/backoffice/blocks.html'],
            context=context,
            is_django_native=True,
        )

    def get_list_context(self, blocks):
        categories = BlockCategory.select_for_user()
        BlockCategory.sort_by_position(categories)
        if categories:
            if BlockCategory.has_global_access():
                categories.append(BlockCategory(_('Misc')))
            for category in categories:
                category.blocks = [x for x in blocks if x.category_id == category.id]
        return {
            'blocks': blocks,
            'categories': categories,
        }

    def new(self):
        form = Form(enctype='multipart/form-data')
        form.add(StringWidget, 'name', title=_('Name'), required=True, size=50)
        category_options = get_categories(BlockCategory)
        if category_options:
            form.add(
                SingleSelectWidget,
                'category_id',
                title=_('Category'),
                options=category_options,
            )
        form.add_submit('submit', _('Add'))
        form.add_submit('cancel', _('Cancel'))
        if form.get_widget('cancel').parse():
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            block = BlockDef(name=form.get_widget('name').parse())
            if form.get_widget('category_id'):
                block.category_id = form.get_widget('category_id').parse()
            block.store()
            return redirect('%s/' % block.id)

        get_response().breadcrumb.append(('new', _('New Block of Fields')))
        get_response().set_title(_('New Block of Fields'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('New Block of Fields')
        r += form.render()
        return r.getvalue()

    def p_import(self):
        form = Form(enctype='multipart/form-data')

        form.add(FileWidget, 'file', title=_('File'), required=True)
        form.add_submit('submit', _('Import Block of Fields'))
        form.add_submit('cancel', _('Cancel'))

        if form.get_submit() == 'cancel':
            return redirect('.')

        if form.is_submitted() and not form.has_errors():
            try:
                return self.import_submit(form)
            except ValueError:
                pass

        get_response().breadcrumb.append(('import', _('Import')))
        get_response().set_title(_('Import Block of Fields'))
        r = TemplateIO(html=True)
        r += htmltext('<h2>%s</h2>') % _('Import Block of Fields')
        r += htmltext('<p>%s</p>') % _('You can install a new block of fields by uploading a file.')
        r += form.render()
        return r.getvalue()

    @classmethod
    def import_blockdef(cls, form):
        fp = form.get_widget('file').parse().fp

        error, reason = False, None
        try:
            blockdef = BlockDef.import_from_xml(fp, check_deprecated=True)
            allowed_field_options = [
                x[0]
                for x in get_field_options(BlockDirectory.blacklisted_types)
                if not isinstance(x, OptGroup)
            ]
            for field in blockdef.fields or []:
                if field.key not in allowed_field_options:
                    raise BlockdefImportError(_('Invalid field in XML file (%s)') % field.key)
        except BlockdefImportError as e:
            error = True
            reason = _(e.msg) % e.msg_args
            if hasattr(e, 'render'):
                form.add_global_errors([e.render()])
            elif e.details:
                reason += ' [%s]' % e.details
        except ValueError:
            error = True

        if not error and not BlockCategory.has_global_access():
            management_roles = {x.id for x in getattr(blockdef.category, 'management_roles', None) or []}
            user_roles = set(get_request().user.get_roles())
            if not user_roles.intersection(management_roles):
                error = True
                reason = _('unauthorized category')

        if error:
            if reason:
                msg = _('Invalid File (%s)') % reason
            else:
                msg = _('Invalid File')
            form.set_error('file', msg)
            raise ValueError()

        return blockdef

    def import_submit(self, form):
        blockdef = self.import_blockdef(form)
        initial_blockdef_name = blockdef.name
        blockdef_names = [x.name for x in BlockDef.select()]
        copy_no = 1
        while blockdef.name in blockdef_names:
            if copy_no == 1:
                blockdef.name = _('Copy of %s') % initial_blockdef_name
            else:
                blockdef.name = _('Copy of %(name)s (%(no)d)') % {
                    'name': initial_blockdef_name,
                    'no': copy_no,
                }
            copy_no += 1
        blockdef.store()
        get_session().add_message(_('This block of fields has been successfully imported.'), level='info')
        return redirect('%s/' % blockdef.id)

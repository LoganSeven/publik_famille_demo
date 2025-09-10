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

from quixote import get_request, get_response
from quixote.directory import Directory

from wcs.qommon import _, errors, misc, template


class ApplicationMixin:
    formdef_objects_template = 'wcs/backoffice/application_formdefs.html'
    carddef_objects_template = 'wcs/backoffice/application_carddefs.html'
    workflow_objects_template = 'wcs/backoffice/application_workflows.html'
    block_objects_template = 'wcs/backoffice/application_blocks.html'
    mailtemplate_objects_template = 'wcs/backoffice/application_mailtemplates.html'
    commenttemplate_objects_template = 'wcs/backoffice/application_commenttemplates.html'
    datasource_objects_template = 'wcs/backoffice/application_datasources.html'
    wscall_objects_template = 'wcs/backoffice/application_wscalls.html'

    def get_template(self):
        if hasattr(self, '%s_objects_template' % self.object_type.replace('-', '')):
            return getattr(self, '%s_objects_template' % self.object_type.replace('-', ''))
        return 'wcs/backoffice/application_objects.html'

    def get_formdef_objects_context(self, objects):
        from wcs.admin.forms import FormsDirectory

        return FormsDirectory().get_list_context(objects)

    def get_carddef_objects_context(self, objects):
        from wcs.backoffice.cards import CardsDirectory

        return CardsDirectory().get_list_context(objects)

    def get_workflow_objects_context(self, objects):
        from wcs.admin.workflows import WorkflowsDirectory

        return WorkflowsDirectory().get_list_context(objects, application=True)

    def get_block_objects_context(self, objects):
        from wcs.admin.blocks import BlocksDirectory

        return BlocksDirectory().get_list_context(objects)

    def get_mailtemplate_objects_context(self, objects):
        from wcs.admin.mail_templates import MailTemplatesDirectory

        return MailTemplatesDirectory().get_list_context(objects)

    def get_commenttemplate_objects_context(self, objects):
        from wcs.admin.comment_templates import CommentTemplatesDirectory

        return CommentTemplatesDirectory().get_list_context(objects)

    def get_datasource_objects_context(self, objects):
        from wcs.admin.data_sources import NamedDataSourcesDirectory

        return NamedDataSourcesDirectory().get_list_context(objects, getattr(self, 'application', None))


class ApplicationsDirectory(ApplicationMixin, Directory):
    _q_exports = ['']

    def __init__(self, object_class):
        self.object_class = object_class
        self.object_type = object_class.xml_root_node
        self.object_label = object_class.verbose_name_plural

    def _q_traverse(self, path):
        get_response().breadcrumb.append(('application/', _('Applications')))
        return super()._q_traverse(path)

    def _q_index(self):
        get_response().set_title(_('Applications'))
        return template.QommonTemplateResponse(templates=[self.get_template()], context=self.get_context())

    def _q_lookup(self, component):
        from wcs.applications import Application

        application = Application.get_by_slug(component, ignore_errors=True)
        if not application or not application.visible:
            raise errors.TraversalError()
        return ApplicationDirectory(self.object_class, application)

    def get_context(self):
        from wcs.applications import Application

        context = {
            'elements_label': self.object_label,
        }
        objects = Application.get_orphan_objects_for_object_type(self.object_type)
        if hasattr(self, 'get_%s_objects_context' % self.object_type.replace('-', '')):
            context.update(
                getattr(self, 'get_%s_objects_context' % self.object_type.replace('-', ''))(objects)
            )
        else:
            context['objects'] = objects
        return context


class ApplicationDirectory(ApplicationMixin, Directory):
    _q_exports = ['', 'icon', 'logo']

    def __init__(self, object_class, application):
        self.object_type = object_class.xml_root_node
        self.object_label = object_class.verbose_name_plural
        self.application = application

    def _q_index(self):
        get_response().set_title(self.application.name)
        return template.QommonTemplateResponse(templates=[self.get_template()], context=self.get_context())

    def get_context(self):
        context = {
            'application': self.application,
        }
        objects = self.application.get_objects_for_object_type(self.object_type)
        if hasattr(self, 'get_%s_objects_context' % self.object_type.replace('-', '')):
            context.update(
                getattr(self, 'get_%s_objects_context' % self.object_type.replace('-', ''))(objects)
            )
        else:
            context['objects'] = objects
        return context

    def icon(self):
        return self._icon(size=(16, 16))

    def logo(self):
        return self._icon(size=(64, 64))

    def _icon(self, size):
        get_request().ignore_session = True
        response = get_response()

        if self.application.icon and self.application.icon.can_thumbnail():
            try:
                content = misc.get_thumbnail(
                    self.application.icon.get_fs_filename(),
                    content_type=self.application.icon.content_type,
                    size=size,
                )
                response.set_content_type('image/png')
                return content
            except misc.ThumbnailError:
                raise errors.TraversalError()
        else:
            raise errors.TraversalError()

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

import collections
import mimetypes

from quixote import get_publisher

from wcs import sql
from wcs.qommon.storage import Contains, Not
from wcs.qommon.upload_storage import PicklableUpload


class Application(sql.Application):
    id = None
    slug = None
    name = None
    description = None
    documentation_url = None
    icon = None
    version_number = None
    version_notes = None
    editable = False
    visible = True
    created_at = None
    updated_at = None

    @classmethod
    def get_by_slug(cls, slug, ignore_errors=True):
        objects = cls.select([sql.Equal('slug', slug)])
        if objects:
            return objects[0]
        if ignore_errors:
            return None
        raise KeyError(slug)

    @classmethod
    def update_or_create_from_manifest(cls, manifest, tar, editable=False, install=True):
        application = cls.get_by_slug(manifest.get('slug'), ignore_errors=True)
        if application is None:
            application = cls()
            application.slug = manifest.get('slug')
            application.editable = editable
        application.name = manifest.get('application')
        application.description = manifest.get('description')
        application.documentation_url = manifest.get('documentation_url')
        if manifest.get('icon'):
            application.icon = PicklableUpload(manifest['icon'], mimetypes.guess_type(manifest['icon'])[0])
            application.icon.receive([tar.extractfile(manifest['icon']).read()])
        else:
            application.icon = None
        application.version_number = manifest.get('version_number') or 'unknown'
        application.version_notes = manifest.get('version_notes')
        if not install:
            application.editable = editable
        application.visible = manifest.get('visible', True)
        application.store()
        return application

    @classmethod
    def select_for_object_type(cls, object_type):
        elements = ApplicationElement.select([sql.Equal('object_type', object_type)])
        application_ids = [e.application_id for e in elements]
        return [a for a in cls.get_ids(application_ids, ignore_errors=True, order_by='name') if a.visible]

    @classmethod
    def populate_objects(cls, objects):
        object_types = {o.xml_root_node for o in objects}
        elements = ApplicationElement.select([sql.Contains('object_type', object_types)])
        elements_by_objects = collections.defaultdict(list)
        for element in elements:
            elements_by_objects[(element.object_type, element.object_id)].append(element)
        application_ids = [e.application_id for e in elements]
        applications_by_ids = {a.id: a for a in cls.get_ids(application_ids, ignore_errors=True) if a.visible}
        for obj in objects:
            applications = []
            elements = elements_by_objects.get((obj.xml_root_node, str(obj.id))) or []
            for element in elements:
                application = applications_by_ids.get(element.application_id)
                if application:
                    applications.append(application)
            obj._applications = sorted(applications, key=lambda a: a.name)

    @classmethod
    def load_for_object(cls, obj):
        elements = ApplicationElement.select(
            [sql.Equal('object_type', obj.xml_root_node), sql.Equal('object_id', str(obj.id))]
        )
        application_ids = [e.application_id for e in elements]
        applications_by_ids = {a.id: a for a in cls.get_ids(application_ids, ignore_errors=True) if a.visible}
        applications = []
        for element in elements:
            application = applications_by_ids.get(element.application_id)
            if application:
                applications.append(application)
        obj._applications = sorted(applications, key=lambda a: a.name)

    def get_objects_for_object_type(self, object_type, lightweight=True):
        elements = ApplicationElement.select(
            [sql.Equal('application_id', self.id), sql.Equal('object_type', object_type)]
        )
        object_ids = [e.object_id for e in elements]
        select_kwargs = {}
        if object_type in ['formdef', 'carddef']:
            select_kwargs['lightweight'] = lightweight
        return (
            get_publisher()
            .get_object_class(object_type)
            .get_ids(object_ids, ignore_errors=True, order_by='name', **select_kwargs)
        )

    @classmethod
    def get_orphan_objects_for_object_type(cls, object_type, lightweight=True):
        elements = ApplicationElement.select([sql.Equal('object_type', object_type)])
        application_ids = [e.application_id for e in elements]
        applications_by_ids = {a.id: a for a in cls.get_ids(application_ids, ignore_errors=True) if a.visible}
        object_ids = [e.object_id for e in elements if applications_by_ids.get(e.application_id)]
        select_kwargs = {}
        if object_type in ['formdef', 'carddef']:
            select_kwargs['lightweight'] = lightweight
        return (
            get_publisher()
            .get_object_class(object_type)
            .select(
                clause=[Not(Contains('id', object_ids))], ignore_errors=True, order_by='name', **select_kwargs
            )
        )


class ApplicationElement(sql.ApplicationElement):
    id = None
    application_id = None
    object_type = None
    object_id = None
    created_at = None
    updated_at = None

    @classmethod
    def update_or_create_for_object(cls, application, obj):
        elements = cls.select(
            [
                sql.Equal('application_id', application.id),
                sql.Equal('object_type', obj.xml_root_node),
                sql.Equal('object_id', str(obj.id)),
            ]
        )
        if elements:
            element = elements[0]
            element.store()
            return element
        element = cls()
        element.application_id = application.id
        element.object_type = obj.xml_root_node
        element.object_id = obj.id
        element.store()
        return element

# lingo - payment and billing system
# Copyright (C) 2022-2024  Entr'ouvert
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import collections

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models


class WithApplicationMixin:
    @property
    def applications(self):
        if getattr(self, '_applications', None) is None:
            Application.load_for_object(self)
        return self._applications


class Application(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100, unique=True)
    icon = models.FileField(
        upload_to='applications/icons/',
        blank=True,
        null=True,
    )
    description = models.TextField(blank=True)
    documentation_url = models.URLField(blank=True)
    version_number = models.CharField(max_length=100)
    version_notes = models.TextField(blank=True)
    editable = models.BooleanField(default=True)
    visible = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return str(self.name)

    @classmethod
    def update_or_create_from_manifest(cls, manifest, tar, editable=False):
        application, dummy = cls.objects.get_or_create(
            slug=manifest.get('slug'), defaults={'editable': editable}
        )
        application.name = manifest.get('application')
        application.description = manifest.get('description') or ''
        application.documentation_url = manifest.get('documentation_url') or ''
        application.version_number = manifest.get('version_number') or 'unknown'
        application.version_notes = manifest.get('version_notes') or ''
        if not editable:
            application.editable = editable
        application.visible = manifest.get('visible', True)
        application.save()
        icon = manifest.get('icon')
        if icon:
            application.icon.save(icon, tar.extractfile(icon), save=True)
        else:
            application.icon.delete()
        return application

    @classmethod
    def select_for_object_class(cls, object_class):
        content_type = ContentType.objects.get_for_model(object_class)
        elements = ApplicationElement.objects.filter(content_type=content_type)
        return cls.objects.filter(pk__in=elements.values('application'), visible=True).order_by('name')

    @classmethod
    def populate_objects(cls, object_class, objects):
        content_type = ContentType.objects.get_for_model(object_class)
        elements = ApplicationElement.objects.filter(
            content_type=content_type, application__visible=True
        ).prefetch_related('application')
        elements_by_objects = collections.defaultdict(list)
        for element in elements:
            elements_by_objects[element.object_id].append(element)
        for obj in objects:
            applications = [element.application for element in elements_by_objects.get(obj.pk) or []]
            obj._applications = sorted(applications, key=lambda a: a.name)

    @classmethod
    def load_for_object(cls, obj):
        content_type = ContentType.objects.get_for_model(obj.__class__)
        elements = ApplicationElement.objects.filter(
            content_type=content_type, object_id=obj.pk, application__visible=True
        ).prefetch_related('application')
        applications = [element.application for element in elements]
        obj._applications = sorted(applications, key=lambda a: a.name)

    def get_objects_for_object_class(self, object_class):
        content_type = ContentType.objects.get_for_model(object_class)
        elements = ApplicationElement.objects.filter(content_type=content_type, application=self)
        return object_class.objects.filter(pk__in=elements.values('object_id'))

    @classmethod
    def get_orphan_objects_for_object_class(cls, object_class):
        content_type = ContentType.objects.get_for_model(object_class)
        elements = ApplicationElement.objects.filter(content_type=content_type, application__visible=True)
        return object_class.objects.exclude(pk__in=elements.values('object_id'))


class ApplicationElement(models.Model):
    application = models.ForeignKey(Application, on_delete=models.CASCADE)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['application', 'content_type', 'object_id']

    @classmethod
    def update_or_create_for_object(cls, application, obj):
        content_type = ContentType.objects.get_for_model(obj.__class__)
        element, created = cls.objects.get_or_create(
            application=application,
            content_type=content_type,
            object_id=obj.pk,
        )
        if not created:
            element.save()
        return element

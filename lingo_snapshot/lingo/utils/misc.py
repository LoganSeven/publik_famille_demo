# lingo - payment and billing system
# Copyright (C) 2022  Entr'ouvert
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

import copy
import hashlib
import json
import urllib.parse

from django.conf import settings
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.template.defaultfilters import yesno
from django.utils.html import linebreaks
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _


class LingoImportError(Exception):
    pass


def shorten_slug(instance):
    # shorten slug if needed
    max_length = instance._meta.get_field('slug').max_length - 1
    if not instance.slug and instance.label:
        instance.slug = slugify(instance.label)
    if len(instance.slug or '') > max_length:
        max_slug_length = max_length - 4
        suffix = hashlib.md5(instance.slug.encode()).hexdigest()[:4]
        instance.slug = instance.slug[:max_slug_length] + suffix


def generate_slug(instance, seen_slugs=None, **query_filters):
    base_slug = instance.base_slug
    slug = base_slug
    i = 1

    if seen_slugs is None:
        # no optimization: check slug in DB each time
        while instance._meta.model.objects.filter(slug=slug, **query_filters).exists():
            slug = '%s-%s' % (base_slug, i)
            i += 1
        return slug

    # seen_slugs is filled
    while True:
        if slug not in seen_slugs:
            # check in DB to be sure, but only if not seen
            queryset = instance._meta.model.objects.filter(slug=slug, **query_filters)
            if not queryset.exists():
                break
        slug = '%s-%s' % (base_slug, i)
        i += 1
    seen_slugs.add(slug)
    return slug


def clean_import_data(cls, data):
    cleaned_data = copy.deepcopy(data)
    for param in data:
        try:
            field = cls._meta.get_field(param)
        except FieldDoesNotExist:
            # remove unknown fields
            cleaned_data.pop(param)
            continue
        if field.many_to_many:
            # remove many to many fields, they have to be managed after update_or_create
            cleaned_data.pop(param)
            continue
        if param == 'slug':
            value = cleaned_data[param]
            try:
                field.run_validators(value)
            except ValidationError:
                raise LingoImportError(_('Bad slug format "%s"') % value)
    return cleaned_data


def get_known_service_for_url(url):
    netloc = urllib.parse.urlparse(url).netloc
    for services in settings.KNOWN_SERVICES.values():
        for service in services.values():
            remote_url = service.get('url')
            if urllib.parse.urlparse(remote_url).netloc == netloc:
                return service
    return None


def json_dump(*args, **kwargs):
    return json.dump(cls=DjangoJSONEncoder, *args, **kwargs)


class WithInspectMixin:
    def get_inspect_fields(self, keys=None):
        keys = keys or self.get_inspect_keys()
        for key in keys:
            field = self._meta.get_field(key)
            get_value_method = 'get_%s_inspect_value' % key
            get_display_method = 'get_%s_display' % key
            if hasattr(self, get_value_method):
                value = getattr(self, get_value_method)()
            elif hasattr(self, get_display_method):
                value = getattr(self, get_display_method)()
            else:
                value = getattr(self, key)
            if value in [None, '']:
                continue
            if isinstance(value, bool):
                value = yesno(value)
            if isinstance(field, models.TextField):
                value = mark_safe(linebreaks(value))
            yield (field.verbose_name, value)

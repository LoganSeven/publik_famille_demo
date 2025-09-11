# authentic2 - versatile identity manager
# Copyright (C) 2010-2019 Entr'ouvert
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

import uuid

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.utils.text import slugify

from . import models


def get_hex_uuid():
    return uuid.uuid4().hex


def get_operation(operation_tpl):
    operation, dummy = models.Operation.objects.get_or_create(slug=operation_tpl.slug)
    return operation


def get_default_ou():
    try:
        return models.OrganizationalUnit.objects.get(default=True)
    except models.OrganizationalUnit.DoesNotExist:
        return None


def get_default_ou_pk():
    return models.OrganizationalUnit.objects.filter(default=True).values_list('pk', flat=True).first()


def get_view_user_perm(ou=None):
    User = get_user_model()
    view_user_perm, dummy = models.Permission.objects.get_or_create(
        operation=get_operation(models.VIEW_OP),
        target_ct=ContentType.objects.get_for_model(ContentType),
        target_id=ContentType.objects.get_for_model(User).pk,
        ou__isnull=ou is None,
        ou=ou,
    )
    return view_user_perm


def get_search_user_perm(ou=None):
    User = get_user_model()
    search_user_perm, dummy = models.Permission.objects.get_or_create(
        operation=get_operation(models.SEARCH_OP),
        target_ct=ContentType.objects.get_for_model(ContentType),
        target_id=ContentType.objects.get_for_model(User).pk,
        ou__isnull=ou is None,
        ou=ou,
    )
    return search_user_perm


def get_search_ou_perm(ou=None):
    if ou:
        view_ou_perm, dummy = models.Permission.objects.get_or_create(
            operation=get_operation(models.SEARCH_OP),
            target_ct=ContentType.objects.get_for_model(ou),
            target_id=ou.pk,
            ou__isnull=True,
        )
    else:
        view_ou_perm, dummy = models.Permission.objects.get_or_create(
            operation=get_operation(models.SEARCH_OP),
            target_ct=ContentType.objects.get_for_model(ContentType),
            target_id=ContentType.objects.get_for_model(models.OrganizationalUnit).pk,
            ou__isnull=True,
        )
    return view_ou_perm


def get_manage_authorizations_user_perm(ou=None):
    User = get_user_model()
    manage_authorizations_user_perm, dummy = models.Permission.objects.get_or_create(
        operation=get_operation(models.MANAGE_AUTHORIZATIONS_OP),
        target_ct=ContentType.objects.get_for_model(ContentType),
        target_id=ContentType.objects.get_for_model(User).pk,
        ou__isnull=ou is None,
        ou=ou,
    )
    return manage_authorizations_user_perm


def generate_slug(name, seen_slugs=None):
    slug = base_slug = slugify(name).lstrip('_')
    if seen_slugs:
        i = 1
        while slug in seen_slugs:
            slug = '%s-%s' % (base_slug, i)
    return slug

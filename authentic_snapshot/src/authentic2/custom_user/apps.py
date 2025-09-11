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

from django.apps import AppConfig
from django.db import DEFAULT_DB_ALIAS, router


class CustomUserConfig(AppConfig):
    name = 'authentic2.custom_user'
    verbose_name = 'Authentic2 Custom User App'

    def ready(self):
        from django.db.models.signals import post_migrate

        post_migrate.connect(self.create_first_name_last_name_attributes, sender=self)

    def create_first_name_last_name_attributes(
        self, app_config, verbosity=2, interactive=True, using=DEFAULT_DB_ALIAS, **kwargs
    ):
        from django.conf import settings
        from django.contrib.auth import get_user_model
        from django.contrib.contenttypes.models import ContentType
        from django.utils import translation
        from django.utils.translation import gettext_lazy as _

        from authentic2.attribute_kinds import get_kind
        from authentic2.models import Attribute, AttributeValue

        if not router.allow_migrate(using, Attribute):
            return

        if Attribute.objects.filter(name__in=['first_name', 'last_name']).count() == 2:
            return

        translation.activate(settings.LANGUAGE_CODE)
        User = get_user_model()
        content_type = ContentType.objects.get_for_model(User)

        attrs = {}
        attrs['first_name'], dummy = Attribute.objects.get_or_create(
            name='first_name',
            defaults={
                'kind': 'string',
                'label': _('First name'),
                'required': True,
                'asked_on_registration': True,
                'user_editable': True,
                'user_visible': True,
            },
        )
        attrs['last_name'], dummy = Attribute.objects.get_or_create(
            name='last_name',
            defaults={
                'kind': 'string',
                'label': _('Last name'),
                'required': True,
                'asked_on_registration': True,
                'user_editable': True,
                'user_visible': True,
            },
        )

        serialize = get_kind('string').get('serialize')
        for user in User.objects.all():
            for attr_name, value in attrs.items():
                AttributeValue.objects.get_or_create(
                    content_type=content_type,
                    object_id=user.id,
                    attribute=value,
                    defaults={
                        'multiple': False,
                        'verified': False,
                        'content': serialize(getattr(user, attr_name, None)),
                    },
                )

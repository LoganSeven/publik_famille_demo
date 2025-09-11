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

import unicodedata
import uuid

import phonenumbers
from django.conf import settings
from django.contrib.auth.models import BaseUserManager
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.search import SearchQuery, TrigramDistance
from django.core.exceptions import ValidationError
from django.db import connection, models, transaction
from django.db.models import F, FloatField, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce, Lower
from django.utils import timezone

from authentic2 import app_settings
from authentic2.attribute_kinds import clean_number
from authentic2.models import AttributeValue
from authentic2.utils.date import parse_date
from authentic2.utils.lookups import ImmutableConcat, Unaccent
from authentic2.utils.postgres_utils import TrigramStrictWordDistance


class UserQuerySet(models.QuerySet):
    @transaction.atomic(savepoint=False)
    def free_text_search(self, search):
        search = search.strip()

        def wrap_qs(qs):
            return qs.annotate(dist=Value(0, output_field=FloatField()))

        if len(search) == 0:
            return wrap_qs(self.none())

        if '@' in search and len(search.split()) == 1:
            self.set_trigram_similarity_threshold()
            qs = self.annotate(lower_email=Lower('email'))
            # use lower_email so that LIKE '%{search}%' use the trigram index on LOWER("email")
            qs = qs.filter(lower_email__contains=search.lower()).order_by(
                Unaccent('last_name'), Unaccent('first_name')
            )
            if qs.exists():
                return wrap_qs(qs)

            # not match, search by trigrams
            qs = self.annotate(lower_email=Lower('email'))
            value = Lower(Value(search))
            qs = qs.filter(lower_email__trigram_similar=value)
            qs = qs.annotate(dist=TrigramDistance('lower_email', value))
            qs = qs.order_by('dist', 'last_name', 'first_name')
            return qs

        try:
            guid = uuid.UUID(search)
        except ValueError:
            pass
        else:
            return wrap_qs(self.filter(uuid=guid.hex))

        default_country = settings.PHONE_COUNTRY_CODES[settings.DEFAULT_COUNTRY_CODE]['region']
        phone_number = None
        formatted_phone_number = None
        try:
            phone_number = phonenumbers.parse(search)
        except phonenumbers.NumberParseException:
            try:
                phone_number = phonenumbers.parse(search, default_country)
            except phonenumbers.NumberParseException:
                pass

        if phone_number:
            formatted_phone_number = phonenumbers.format_number(
                phone_number, phonenumbers.PhoneNumberFormat.E164
            )
        else:
            try:
                formatted_phone_number = clean_number(search)
            except ValidationError:
                pass

        if formatted_phone_number:
            attribute_values = AttributeValue.objects.filter(
                search_vector=SearchQuery(formatted_phone_number), attribute__kind='phone_number'
            )
            qs = self.filter(attribute_values__in=attribute_values).order_by('last_name', 'first_name')
            if qs.exists():
                return wrap_qs(qs)

        try:
            date = parse_date(search)
        except ValueError:
            pass
        else:
            attribute_values = AttributeValue.objects.filter(
                search_vector=SearchQuery(date.isoformat()), attribute__kind='birthdate'
            )
            qs = self.filter(attribute_values__in=attribute_values).order_by('last_name', 'first_name')
            if qs.exists():
                return wrap_qs(qs)

        qs = self.find_duplicates(fullname=search, limit=None, threshold=app_settings.A2_FTS_THRESHOLD)
        extra_user_ids = set()
        attribute_values = AttributeValue.objects.filter(
            search_vector=SearchQuery(search), attribute__searchable=True
        )
        extra_user_ids.update(self.filter(attribute_values__in=attribute_values).values_list('id', flat=True))
        if len(search.split()) == 1:
            extra_user_ids.update(
                self.filter(Q(username__istartswith=search) | Q(email__istartswith=search)).values_list(
                    'id', flat=True
                )
            )
        if extra_user_ids:
            qs = qs | self.filter(id__in=extra_user_ids)
        qs = qs.order_by('dist', Unaccent('last_name'), Unaccent('first_name'))
        return qs

    @transaction.atomic(savepoint=False)
    def find_duplicates(
        self, first_name=None, last_name=None, fullname=None, birthdate=None, limit=5, threshold=None, ou=None
    ):
        self.set_trigram_strict_word_similarity_threshold(
            threshold=threshold or app_settings.A2_DUPLICATES_THRESHOLD
        )

        if fullname is not None:
            name = fullname
        else:
            assert first_name is not None and last_name is not None
            name = '%s %s' % (first_name, last_name)
        name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode('ascii').lower()

        qs = self.annotate(name=Lower(Unaccent(ImmutableConcat('first_name', Value(' '), 'last_name'))))
        qs = qs.filter(name__trigram_strict_word_similar=name)
        qs = qs.annotate(dist=TrigramStrictWordDistance('name', name))
        qs = qs.order_by('dist')

        if ou:
            qs = qs.filter(ou=ou)

        if limit is not None:
            qs = qs[:limit]

        # alter distance according to additionnal parameters
        if birthdate:
            bonus = app_settings.A2_DUPLICATES_BIRTHDATE_BONUS
            content_type = ContentType.objects.get_for_model(self.model)
            same_birthdate = AttributeValue.objects.filter(
                object_id=OuterRef('pk'),
                content_type=content_type,
                attribute__kind='birthdate',
                content=birthdate,
            ).annotate(bonus=Value(1 - bonus, output_field=FloatField()))
            qs = qs.annotate(
                dist=Coalesce(
                    Subquery(same_birthdate.values('bonus'), output_field=FloatField()) * F('dist'), F('dist')
                )
            )

        return qs

    def set_trigram_strict_word_similarity_threshold(self, threshold=None):
        assert (
            connection.in_atomic_block
        ), 'set_trigram_strict_word_similarity_threshold must be used in an atomic block'
        with connection.cursor() as cursor:
            cursor.execute(
                'SET pg_trgm.strict_word_similarity_threshold = %f'
                % (threshold or app_settings.A2_FTS_THRESHOLD)
            )

    def set_trigram_similarity_threshold(self, threshold=None):
        assert connection.in_atomic_block, 'set_trigram_similarity_threshold must be used in an atomic block'
        with connection.cursor() as cursor:
            cursor.execute(
                'SET pg_trgm.similarity_threshold = %f' % (threshold or app_settings.A2_FTS_THRESHOLD)
            )

    def filter_by_email(self, email):
        users = []
        for user in self.filter(email__iexact=email, is_active=True):
            if (
                unicodedata.normalize('NFKC', user.email).casefold()
                == unicodedata.normalize('NFKC', email).casefold()
            ):
                users.append(user)
        return users

    def get_by_email(self, email):
        """
        Prevents unicode normalization collision attacks (see
        https://nvd.nist.gov/vuln/detail/CVE-2019-19844)
        """
        users = self.filter_by_email(email=email)
        if not users:
            raise self.model.DoesNotExist
        if len(users) > 1:
            raise self.model.MultipleObjectsReturned
        return users[0]


class UserManager(BaseUserManager):
    def _create_user(self, username, email, password, is_staff, is_superuser, **extra_fields):
        """
        Creates and saves a User with the given username, email and password.
        """
        now = timezone.now()
        if not username:
            raise ValueError('The given username must be set')
        email = self.normalize_email(email)
        user = self.model(
            username=username,
            email=email,
            is_staff=is_staff,
            is_active=True,
            is_superuser=is_superuser,
            last_login=now,
            date_joined=now,
            **extra_fields,
        )
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, username, email=None, password=None, **extra_fields):
        return self._create_user(username, email, password, False, False, **extra_fields)

    def create_superuser(self, username, email, password, **extra_fields):
        return self._create_user(username, email, password, True, True, **extra_fields)

    def get_by_natural_key(self, username):
        return self.get(uuid=username)

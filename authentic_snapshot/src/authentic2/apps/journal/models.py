# authentic2 - versatile identity manager
# Copyright (C) 2010-2020 Entr'ouvert
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

import functools
import logging
import operator
import re
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from django.db.models import Count, F, JSONField, Q, QuerySet, Value
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Trunc
from django.utils.timezone import utc  # pylint: disable=no-name-in-module
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _

from authentic2.utils.cache import GlobalCache

from . import sql
from .utils import Statistics

logger = logging.getLogger(__name__)

User = get_user_model()

_registry = {}


@contextmanager
def clean_registry():
    global _registry  # pylint: disable=global-statement

    old_registry = _registry
    _registry = {}
    yield
    _registry = old_registry


class EventTypeDefinitionMeta(type):
    def __new__(mcs, name, bases, namespace, **kwargs):
        new_cls = type.__new__(mcs, name, bases, namespace, **kwargs)

        name = namespace.get('name')

        if name:
            assert (
                new_cls.retention_days is None or new_cls.retention_days >= 0
            ), 'retention_days must be None or >= 0'
            assert new_cls.name, 'name is missing'
            assert re.match(r'^[a-z_]+(?:\.[a-z_]+)*$', new_cls.name), (
                '%r is not proper event type name' % new_cls.name
            )
            assert new_cls.label, 'label is missing'

            assert new_cls.name not in _registry, 'name %r is already registered' % new_cls.name

            _registry[new_cls.name] = new_cls
        return new_cls


class EventTypeDefinition(metaclass=EventTypeDefinitionMeta):
    name = ''
    label = None
    # used to group type of events
    # how long to keep this type of events
    retention_days = None

    @classmethod
    def record(cls, *, user=None, session=None, references=None, data=None, api=False):
        event_type = EventType.objects.get_for_name(cls.name)

        if user and not isinstance(user, User):
            # API user from DRF or OIDC
            user = None

        return Event.objects.create(
            type=event_type,
            user=user,
            session_id=session and session.session_key,
            references=references or None,  # NULL values take less space
            data=data or None,  # NULL values take less space
            api=api,
        )

    @classmethod
    def get_for_name(cls, name):
        return _registry.get(name)

    @classmethod
    def search_by_name(cls, name):
        for evd_name, evd in _registry.items():
            if name in evd_name:
                yield evd

    @classmethod
    def get_message(cls, event, context=None):
        return cls.label

    @classmethod
    def get_statistics(
        cls,
        group_by_time,
        group_by_field=None,
        which_references=None,
        users_ou=None,
        start=None,
        end=None,
    ):
        if group_by_time not in ('timestamp', 'day', 'month', 'year'):
            raise ValueError('Usupported value for group_by_time: %s' % group_by_time)

        event_type = EventType.objects.get_for_name(cls.name)
        qs = Event.objects.filter(type=event_type)

        if start:
            qs = qs.filter(timestamp__gte=start)
        if end:
            qs = qs.filter(timestamp__lte=end)

        values = [group_by_time]
        if group_by_time != 'timestamp':
            qs = qs.annotate(
                **{group_by_time: Trunc('timestamp', kind=group_by_time, output_field=models.DateField())}
            )

        if group_by_field:
            # get field from JSONField
            qs = qs.annotate(**{group_by_field: KeyTextTransform(group_by_field, 'data')})
            values.append(group_by_field)

        if which_references is not None:
            qs = qs.which_references(which_references)

        if users_ou:
            qs = qs.filter(user__ou=users_ou)

        qs = qs.values(*values)
        qs = qs.annotate(count=Count('id'))
        return qs.order_by(group_by_time)

    @classmethod
    def get_global_statistics(cls, group_by_time, y_label, services_ou=None, start=None, end=None):
        which_ref = None
        if services_ou:
            from authentic2.models import Service

            services = Service.objects.filter(ou=services_ou)
            # look for the Service child and parent instances, see #68390 and #64853
            which_ref = [] if not services else [services, list(services.select_subclasses())]

        qs = cls.get_statistics(group_by_time=group_by_time, which_references=which_ref, start=start, end=end)
        stats = Statistics(qs, time_interval=group_by_time)

        for stat in qs:
            stats.add(x_label=stat[group_by_time], y_label=y_label, value=stat['count'])

        return stats.to_json()

    def __repr__(self):
        return '<EventTypeDefinition %r %s>' % (self.name, self.label)


@GlobalCache
def event_type_cache(name):
    event_type, dummy = EventType.objects.get_or_create(name=name)
    return event_type


class EventTypeManager(models.Manager):
    def get_for_name(self, name):
        return event_type_cache(name)


class EventType(models.Model):
    name = models.SlugField(verbose_name=_('name'), max_length=256, unique=True)

    @property
    def definition(self):
        return EventTypeDefinition.get_for_name(self.name)

    @property
    def retention_days_str(self):
        retention_days = getattr(settings, 'JOURNAL_DEFAULT_RETENTION_DAYS', 365)
        if self.definition:
            if self.definition.retention_days == 0:
                return _('retained forever')
            elif self.definition.retention_days is not None:
                retention_days = self.definition.retention_days
        return _('retained for %d days') % retention_days

    def __str__(self):
        definition = self.definition
        if definition:
            return str(definition.label)
        else:
            return '%s (definition not found)' % self.name

    objects = EventTypeManager()

    class Meta:
        verbose_name = _('event type')
        verbose_name_plural = _('event types')
        ordering = ('name',)


class EventQuerySet(QuerySet):
    @classmethod
    def _which_references_query(cls, instance_or_model_class_or_queryset):
        if isinstance(instance_or_model_class_or_queryset, list):
            return functools.reduce(
                operator.or_,
                (cls._which_references_query(ref) for ref in instance_or_model_class_or_queryset),
            )
        elif isinstance(instance_or_model_class_or_queryset, type) and issubclass(
            instance_or_model_class_or_queryset, models.Model
        ):
            ct = ContentType.objects.get_for_model(instance_or_model_class_or_queryset)
            q = Q(reference_ct_ids__contains=[ct.pk])
            # users can also be references by the user_id column
            if instance_or_model_class_or_queryset is User:
                q |= Q(user__isnull=False)
            return q
        elif isinstance(instance_or_model_class_or_queryset, QuerySet):
            qs = instance_or_model_class_or_queryset
            model = qs.model
            ct_model = ContentType.objects.get_for_model(model)
            qs_array = qs.values_list(Value(ct_model.id << 32) + F('pk'), flat=True)
            q = Q(reference_ids__overlap=sql.ArraySubquery(qs_array))
            if issubclass(model, User):
                q = q | Q(user__in=qs)
        else:
            instance = instance_or_model_class_or_queryset
            q = Q(reference_ids__contains=[reference_integer(instance)])
            if isinstance(instance, User):
                q = q | Q(user=instance)
        return q

    def which_references(self, instance_or_queryset):
        if instance_or_queryset == []:
            return self.none()
        return self.filter(self._which_references_query(instance_or_queryset))

    def from_cursor(self, cursor):
        return self.filter(
            Q(timestamp=cursor.timestamp, id__gte=cursor.event_id) | Q(timestamp__gt=cursor.timestamp)
        )

    def to_cursor(self, cursor):
        return self.filter(
            Q(timestamp=cursor.timestamp, id__lte=cursor.event_id) | Q(timestamp__lt=cursor.timestamp)
        )

    def __getitem__(self, i):
        # slice by cursor:
        # [cursor..20] or [-20..cursor]
        # it simplifies pagination
        if isinstance(i, slice) and i.step is None:
            _slice = i
            if isinstance(_slice.start, EventCursor) and isinstance(_slice.stop, int) and _slice.stop >= 0:
                return self.from_cursor(_slice.start)[: _slice.stop]
            if isinstance(_slice.start, int) and _slice.start <= 0 and isinstance(_slice.stop, EventCursor):
                qs = self.order_by('-timestamp', '-id').to_cursor(_slice.stop)[: -_slice.start]
                return list(reversed(qs))
        return super().__getitem__(i)

    def prefetch_references(self):
        prefetch_events_references(self)
        return self


class EventManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().select_related('type', 'user')


# contains/overlap operator on postresql ARRAY do not really support nested arrays,
# so we cannot use them to query an array generic foreign keys repsented as two
# elements integers arrays like '{{1,100},{2,300}}' as any integer will match.
# ex.:
# authentic_multitenant=# select '{{1,2}}'::int[] && '{{1,3}}'::int[];
#  ?column?
# ----------
#  t
# (1 line)
#
# To work around this limitation we map pair of integers (content_type.pk,
# instance.pk) to a corresponding unique 64-bit integer using the reversible
# mapping (content_type.pk << 32 + instance.pk).


def n_2_pairing(a, b):
    return a * 2**32 + b


def n_2_pairing_rev(n):
    return (n >> 32, n & (2**32 - 1))


def reference_integer(instance):
    return n_2_pairing(ContentType.objects.get_for_model(instance).pk, instance.pk)


class Event(models.Model):
    timestamp = models.DateTimeField(verbose_name=_('timestamp'), default=now, editable=False, blank=True)

    user = models.ForeignKey(
        verbose_name=_('user'),
        to=User,
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        blank=True,
        null=True,
    )

    session = models.ForeignKey(
        verbose_name=_('session'),
        to='sessions.Session',
        on_delete=models.DO_NOTHING,
        db_constraint=False,
        blank=True,
        null=True,
    )

    type = models.ForeignKey(verbose_name=_('type'), to=EventType, on_delete=models.PROTECT)

    reference_ids = ArrayField(
        verbose_name=_('reference ids'),
        base_field=models.BigIntegerField(),
        null=True,
    )

    reference_ct_ids = ArrayField(
        verbose_name=_('reference ct ids'),
        base_field=models.IntegerField(),
        null=True,
    )

    data = JSONField(verbose_name=_('data'), null=True)

    api = models.BooleanField(verbose_name=_('API'), default=False)

    objects = EventManager.from_queryset(EventQuerySet)()

    def __init__(self, *args, **kwargs):
        references = kwargs.pop('references', ())
        super().__init__(*args, **kwargs)
        for reference in references or ():
            self.add_reference_to_instance(reference)

    def add_reference_to_instance(self, instance):
        self.reference_ids = self.reference_ids or []
        self.reference_ct_ids = self.reference_ct_ids or []
        if instance is not None:
            self.reference_ids.append(reference_integer(instance))
            self.reference_ct_ids.append(ContentType.objects.get_for_model(instance).pk)
        else:
            self.reference_ids.append(0)
            self.reference_ct_ids.append(0)

    def get_reference_ids(self):
        return map(n_2_pairing_rev, self.reference_ids or ())

    @property
    def references(self):
        if not hasattr(self, '_references_cache'):
            self._references_cache = []
            for content_type_id, instance_pk in self.get_reference_ids():
                if content_type_id != 0:
                    content_type = ContentType.objects.get_for_id(content_type_id)
                    try:
                        self._references_cache.append(content_type.get_object_for_this_type(pk=instance_pk))
                        continue
                    except ObjectDoesNotExist:
                        pass
                self._references_cache.append(None)
        return self._references_cache

    @property
    def cursor(self):
        return EventCursor.from_event(self)

    def __repr__(self):
        return '<Event id:%s %s %s>' % (self.id, self.timestamp, self.type.name)

    @classmethod
    def cleanup(cls):
        """Expire old events by default retention days or customized at the
        EventTypeDefinition level."""
        event_types_by_retention_days = defaultdict(set)
        default_retention_days = getattr(settings, 'JOURNAL_DEFAULT_RETENTION_DAYS', 365)
        for event_type in EventType.objects.all():
            evd = event_type.definition
            retention_days = evd.retention_days if evd else None
            if retention_days == 0:
                # do not expire
                continue
            if retention_days is None:
                retention_days = default_retention_days
            event_types_by_retention_days[retention_days].add(event_type)

        for retention_days, event_types in event_types_by_retention_days.items():
            threshold = now() - timedelta(days=retention_days)
            Event.objects.filter(type__in=event_types).filter(timestamp__lt=threshold).delete()

    @property
    def session_id_shortened(self):
        return self.session_id[:6] if self.session_id else '-'

    @property
    def message(self):
        return self.message_in_context(None)

    def message_in_context(self, context):
        if self.type.definition:
            try:
                return self.type.definition.get_message(self, context)
            except Exception:
                logger.exception('could not render message of event type "%s"', self.type.name)
        return self.type.name

    def get_data(self, key, default=None):
        return (self.data or {}).get(key, default)

    def get_typed_references(self, *reference_types):
        count = 0
        for reference_type, reference in zip(reference_types, self.references):
            if reference_type is None:
                yield None
            else:
                if isinstance(reference, reference_type):
                    yield reference
                else:
                    yield None
            count += 1
        for dummy in range(len(reference_types) - count):
            yield None

    class Meta:
        verbose_name = _('event')
        verbose_name_plural = _('events')
        ordering = ('timestamp', 'id')


class EventCursor(str):
    '''Represents a point in the journal'''

    def __new__(cls, value):
        self = super().__new__(cls, value)
        try:
            timestamp, event_id = value.split(' ', 1)
            timestamp = float(timestamp)
            event_id = int(event_id)
            timestamp = datetime.fromtimestamp(timestamp, tz=utc)
        except ValueError as e:
            raise ValueError('invalid event cursor') from e
        self.timestamp = timestamp
        self.event_id = event_id
        return self

    @classmethod
    def parse(cls, value):
        try:
            return cls(value)
        except ValueError:
            return None

    @classmethod
    def from_event(cls, event):
        assert event.id is not None
        assert event.timestamp is not None
        cursor = super().__new__(cls, '%s %s' % (event.timestamp.timestamp(), event.id))
        cursor.timestamp = event.timestamp
        cursor.event_id = event.id
        return cursor

    def minus_one(self):
        return EventCursor('%s %s' % (self.timestamp.timestamp(), self.event_id - 1))


def prefetch_events_references(events, prefetcher=None):
    '''Prefetch references on an iterable of events, prevent N+1 queries problem.'''
    grouped_references = defaultdict(set)
    references = {}

    # group reference ids
    for event in events:
        for content_type_id, instance_pk in event.get_reference_ids():
            if content_type_id and instance_pk:
                grouped_references[content_type_id].add(instance_pk)

    # make batched queries for each CT
    for content_type_id, instance_pks in grouped_references.items():
        content_type = ContentType.objects.get_for_id(content_type_id)
        for instance in content_type.get_all_objects_for_this_type(pk__in=instance_pks):
            references[(content_type_id, instance.pk)] = instance
        if prefetcher:
            deleted_pks = [pk for pk in instance_pks if (content_type_id, pk) not in references]
            if deleted_pks:
                for found_pk, instance in prefetcher(content_type.model_class(), deleted_pks):
                    references[(content_type_id, found_pk)] = instance

    # prefetch the user column if absent
    if prefetcher:
        user_to_events = {}
        for event in events:
            if event.user is None and event.user_id:
                user_to_events.setdefault(event.user_id, []).append(event)
        for found_pk, instance in prefetcher(User, user_to_events.keys()):
            for event in user_to_events[found_pk]:
                # prevent TypeError in user's field descriptor __set__ method
                event._state.fields_cache['user'] = instance

    # assign references to events
    for event in events:
        event._references_cache = [
            references.get((content_type_id, instance_pk))
            for content_type_id, instance_pk in event.get_reference_ids()
        ]

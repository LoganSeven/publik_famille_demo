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


from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils.translation import gettext_lazy as _

User = get_user_model()


class WithSnapshotManager(models.Manager):
    snapshots = False

    def __init__(self, *args, **kwargs):
        self.snapshots = kwargs.pop('snapshots', False)
        super().__init__(*args, **kwargs)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.snapshots:
            return queryset.filter(snapshot__isnull=False)
        else:
            return queryset.filter(snapshot__isnull=True)


class WithSnapshotMixin:
    @classmethod
    def get_snapshot_model(cls):
        return cls._meta.get_field('snapshot').related_model

    def take_snapshot(self, *args, **kwargs):
        return self.get_snapshot_model().take(self, *args, **kwargs)


class AbstractSnapshot(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    comment = models.TextField(blank=True, null=True)
    serialization = models.JSONField(blank=True, default=dict, encoder=DjangoJSONEncoder)
    label = models.CharField(_('Label'), max_length=150, blank=True)
    application_slug = models.CharField(max_length=100, null=True)
    application_version = models.CharField(max_length=100, null=True)

    class Meta:
        abstract = True
        ordering = ('-timestamp',)

    @classmethod
    def get_instance_model(cls):
        return cls._meta.get_field('instance').related_model

    @classmethod
    def take(cls, instance, request=None, comment=None, deletion=False, label=None, application=None):
        snapshot = cls(instance=instance, comment=comment, label=label or '')
        if request and isinstance(request.user, User):
            snapshot.user = request.user
        if not deletion:
            snapshot.serialization = instance.export_json()
        else:
            snapshot.serialization = {}
            snapshot.comment = comment or _('deletion')
        if application:
            snapshot.application_slug = application.slug
            snapshot.application_version = application.version_number
        snapshot.save()
        return snapshot

    def get_instance(self):
        try:
            # try reusing existing instance
            instance = self.get_instance_model().snapshots.get(snapshot=self)
        except self.get_instance_model().DoesNotExist:
            instance = self.load_instance(self.serialization, snapshot=self)
        instance.slug = self.serialization['slug']  # restore slug
        return instance

    def load_instance(self, json_instance, snapshot=None):
        return self.get_instance_model().import_json(json_instance, snapshot=snapshot)[1]

    def load_history(self):
        if self.instance is None:
            self._history = []
            return
        history = type(self).objects.filter(instance=self.instance)
        self._history = [s.id for s in history]

    @property
    def previous(self):
        if not hasattr(self, '_history'):
            self.load_history()

        try:
            idx = self._history.index(self.id)
        except ValueError:
            return None
        if idx == 0:
            return None
        return self._history[idx - 1]

    @property
    def next(self):
        if not hasattr(self, '_history'):
            self.load_history()

        try:
            idx = self._history.index(self.id)
        except ValueError:
            return None
        try:
            return self._history[idx + 1]
        except IndexError:
            return None

    @property
    def first(self):
        if not hasattr(self, '_history'):
            self.load_history()

        return self._history[0]

    @property
    def last(self):
        if not hasattr(self, '_history'):
            self.load_history()

        return self._history[-1]


class AgendaSnapshot(AbstractSnapshot):
    instance = models.ForeignKey(
        'agendas.Agenda',
        on_delete=models.SET_NULL,
        null=True,
        related_name='instance_snapshots',
    )


class CheckTypeGroupSnapshot(AbstractSnapshot):
    instance = models.ForeignKey(
        'agendas.CheckTypeGroup',
        on_delete=models.SET_NULL,
        null=True,
        related_name='instance_snapshots',
    )


class PricingSnapshot(AbstractSnapshot):
    instance = models.ForeignKey(
        'pricing.Pricing',
        on_delete=models.SET_NULL,
        null=True,
        related_name='instance_snapshots',
    )


class CriteriaCategorySnapshot(AbstractSnapshot):
    instance = models.ForeignKey(
        'pricing.CriteriaCategory',
        on_delete=models.SET_NULL,
        null=True,
        related_name='instance_snapshots',
    )


class RegieSnapshot(AbstractSnapshot):
    instance = models.ForeignKey(
        'invoicing.Regie',
        on_delete=models.SET_NULL,
        null=True,
        related_name='instance_snapshots',
    )

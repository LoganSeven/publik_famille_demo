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
import uuid

from django.conf import settings
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from lingo.export_import.models import WithApplicationMixin
from lingo.snapshot.models import (
    AgendaSnapshot,
    CheckTypeGroupSnapshot,
    WithSnapshotManager,
    WithSnapshotMixin,
)
from lingo.utils.misc import LingoImportError, WithInspectMixin, clean_import_data, generate_slug


class Agenda(WithSnapshotMixin, WithApplicationMixin, WithInspectMixin, models.Model):
    # mark temporarily restored snapshots
    snapshot = models.ForeignKey(
        AgendaSnapshot, on_delete=models.CASCADE, null=True, related_name='temporary_instance'
    )

    label = models.CharField(_('Label'), max_length=150)
    slug = models.SlugField(_('Identifier'), max_length=160, unique=True)
    category_label = models.CharField(_('Category label'), max_length=150, null=True)
    category_slug = models.SlugField(_('Category identifier'), max_length=160, null=True)
    partial_bookings = models.BooleanField(default=False)
    check_type_group = models.ForeignKey(
        'CheckTypeGroup',
        verbose_name=_('Check type group'),
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )
    regie = models.ForeignKey(
        'invoicing.Regie',
        verbose_name=_('Regie'),
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )

    archived = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    application_component_type = 'lingo_agendas'
    application_label_singular = _('Agenda (payment)')
    application_label_plural = _('Agendas (payment)')

    objects = WithSnapshotManager()
    snapshots = WithSnapshotManager(snapshots=True)

    class Meta:
        ordering = ['label']

    def __str__(self):
        return self.label

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_slug(self)
        super().save(*args, **kwargs)

    @property
    def base_slug(self):
        return slugify(self.label)

    def get_dependencies(self):
        yield self.check_type_group
        yield self.regie
        if not settings.KNOWN_SERVICES.get('chrono'):
            return
        chrono = list(settings.KNOWN_SERVICES['chrono'].values())[0]
        chrono_url = chrono.get('url') or ''
        urls = {
            'export': f'{chrono_url}api/export-import/agendas/{self.slug}/',
            'dependencies': f'{chrono_url}api/export-import/agendas/{self.slug}/dependencies/',
            'redirect': f'{chrono_url}api/export-import/agendas/{self.slug}/redirect/',
        }
        yield {'type': 'agendas', 'id': self.slug, 'text': self.label, 'urls': urls}

    def export_json(self):
        return {
            'slug': self.slug,
            'check_type_group': self.check_type_group.slug if self.check_type_group else None,
            'regie': self.regie.slug if self.regie else None,
        }

    @classmethod
    def import_json(cls, data, snapshot=None):
        from lingo.invoicing.models import Regie

        data = copy.deepcopy(data)
        check_type_group = data.pop('check_type_group', None)
        regie = data.pop('regie', None)
        if snapshot:
            data['slug'] = str(uuid.uuid4())  # random slug
            agenda, dummy = cls.objects.update_or_create(snapshot=snapshot, defaults=data)
        else:
            try:
                agenda = Agenda.objects.get(slug=data['slug'])
            except Agenda.DoesNotExist:
                raise LingoImportError(_('Missing "%s" agenda') % data['slug'])
        if check_type_group:
            try:
                check_type_group = CheckTypeGroup.objects.get(slug=check_type_group)
            except CheckTypeGroup.DoesNotExist:
                raise LingoImportError(_('Missing "%s" check type group') % check_type_group)
        if regie:
            try:
                regie = Regie.objects.get(slug=regie)
            except Regie.DoesNotExist:
                raise LingoImportError(_('Missing "%s" regie') % regie)

        agenda.check_type_group = check_type_group
        agenda.regie = regie
        agenda.save()

        return False, agenda

    def get_inspect_keys(self):
        return ['label', 'slug', 'category_label', 'category_slug', 'partial_bookings']

    def get_settings_inspect_fields(self):
        yield from self.get_inspect_fields(keys=['check_type_group', 'regie'])

    def get_chrono_url(self):
        if not settings.KNOWN_SERVICES.get('chrono'):
            return
        chrono = list(settings.KNOWN_SERVICES['chrono'].values())[0]
        chrono_url = chrono.get('url') or ''
        return '%smanage/agendas/%s/settings/' % (chrono_url, self.slug)

    def get_real_kind_display(self):
        if self.partial_bookings:
            return _('Partial bookings')

        return _('Events')


class CheckTypeGroup(WithSnapshotMixin, WithApplicationMixin, WithInspectMixin, models.Model):
    # mark temporarily restored snapshots
    snapshot = models.ForeignKey(
        CheckTypeGroupSnapshot, on_delete=models.CASCADE, null=True, related_name='temporary_instance'
    )

    slug = models.SlugField(_('Identifier'), max_length=160, unique=True)
    label = models.CharField(_('Label'), max_length=150)
    unexpected_presence = models.ForeignKey(
        'agendas.CheckType',
        verbose_name=_('Check type to be used in case of unexpected presence'),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    unjustified_absence = models.ForeignKey(
        'agendas.CheckType',
        verbose_name=_('Check type to be used in case of unjustified absence'),
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    application_component_type = 'check_type_groups'
    application_label_singular = _('Check type group')
    application_label_plural = _('Check type groups')

    objects = WithSnapshotManager()
    snapshots = WithSnapshotManager(snapshots=True)

    class Meta:
        ordering = ['label']

    def __str__(self):
        return self.label

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_slug(self)
        super().save(*args, **kwargs)

    @property
    def base_slug(self):
        return slugify(self.label)

    def get_dependencies(self):
        return []

    @classmethod
    def import_json(cls, data, snapshot=None):
        check_types = data.pop('check_types', [])
        unexpected_presence = data.pop('unexpected_presence', None)
        unjustified_absence = data.pop('unjustified_absence', None)
        data = clean_import_data(cls, data)
        qs_kwargs = {}
        if snapshot:
            qs_kwargs = {'snapshot': snapshot}
            data['slug'] = str(uuid.uuid4())  # random slug
        else:
            qs_kwargs = {'slug': data['slug']}
        group, created = cls.objects.update_or_create(defaults=data, **qs_kwargs)

        for check_type in check_types:
            check_type['group'] = group
            CheckType.import_json(check_type)
        if unexpected_presence:
            try:
                group.unexpected_presence = group.check_types.get(slug=unexpected_presence)
            except CheckType.DoesNotExist:
                raise LingoImportError(_('Missing "%s" check type') % unexpected_presence)
            group.save()
        if unjustified_absence:
            try:
                group.unjustified_absence = group.check_types.get(slug=unjustified_absence)
            except CheckType.DoesNotExist:
                raise LingoImportError(_('Missing "%s" check type') % unjustified_absence)
            group.save()

        return created, group

    def export_json(self):
        return {
            'label': self.label,
            'slug': self.slug,
            'check_types': [a.export_json() for a in self.check_types.all()],
            'unexpected_presence': self.unexpected_presence.slug if self.unexpected_presence else None,
            'unjustified_absence': self.unjustified_absence.slug if self.unjustified_absence else None,
        }

    def get_inspect_keys(self):
        return ['label', 'slug']

    def get_settings_inspect_fields(self):
        keys = ['unexpected_presence', 'unjustified_absence']
        yield from self.get_inspect_fields(keys=keys)


class CheckTypeManager(models.Manager):
    def absences(self):
        return self.filter(kind='absence', disabled=False)

    def presences(self):
        return self.filter(kind='presence', disabled=False)


class CheckType(WithInspectMixin, models.Model):
    group = models.ForeignKey(CheckTypeGroup, on_delete=models.CASCADE, related_name='check_types')
    slug = models.SlugField(_('Identifier'), max_length=160)
    label = models.CharField(_('Label'), max_length=150)
    code = models.CharField(_('Code'), max_length=10, blank=True)
    colour = models.CharField(_('Colour'), max_length=7, default='#33CC33')
    kind = models.CharField(
        _('Kind'),
        max_length=8,
        choices=[('absence', _('Absence')), ('presence', _('Presence'))],
        default='absence',
    )
    pricing = models.DecimalField(
        _('Pricing'), max_digits=5, decimal_places=2, help_text=_('Fixed pricing'), blank=True, null=True
    )
    pricing_rate = models.IntegerField(
        _('Pricing rate'), help_text=_('Percentage rate'), blank=True, null=True
    )
    disabled = models.BooleanField(_('Disabled'), default=False)

    created_at = models.DateTimeField(auto_now_add=True, null=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    objects = CheckTypeManager()

    class Meta:
        ordering = ['label']
        unique_together = ['group', 'slug']

    def __str__(self):
        return self.label

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = generate_slug(self, group=self.group)
        super().save(*args, **kwargs)

    @property
    def base_slug(self):
        return slugify(self.label)

    @classmethod
    def import_json(cls, data):
        data = clean_import_data(cls, data)
        cls.objects.update_or_create(slug=data['slug'], group=data['group'], defaults=data)

    def export_json(self):
        return {
            'label': self.label,
            'slug': self.slug,
            'code': self.code,
            'colour': self.colour,
            'kind': self.kind,
            'pricing': self.pricing,
            'pricing_rate': self.pricing_rate,
            'disabled': self.disabled,
        }

    def get_inspect_keys(self):
        return ['label', 'slug', 'code', 'kind', 'pricing', 'pricing_rate', 'disabled']


class AgendaUnlockLog(models.Model):
    agenda = models.ForeignKey(Agenda, on_delete=models.CASCADE)
    campaign = models.ForeignKey('invoicing.Campaign', on_delete=models.CASCADE)

    date_unlock = models.DateTimeField(auto_now_add=True)

    active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

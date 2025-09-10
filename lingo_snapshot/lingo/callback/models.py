# lingo - payment and billing system
# Copyright (C) 2024  Entr'ouvert
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

import logging
import uuid

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.utils.translation import gettext_lazy as _

STATUS_CHOICES = [
    ('registered', _('Registered')),
    ('running', _('Running')),
    ('toretry', _('To retry')),
    ('failed', _('Failed')),
    ('completed', _('Completed')),
]


class CallbackFailure(Exception):
    pass


class Callback(models.Model):
    uuid = models.UUIDField(default=uuid.uuid4, primary_key=True)

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.PositiveIntegerField()
    content_object = GenericForeignKey('content_type', 'object_id')

    notification_type = models.CharField(max_length=50)
    payload = models.JSONField(blank=True, default=dict, encoder=DjangoJSONEncoder)
    status = models.CharField(
        max_length=15,
        default='registered',
        choices=STATUS_CHOICES,
    )
    retries_counter = models.IntegerField(default=0)
    retry_reason = models.CharField(max_length=250, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=['content_type', 'object_id']),
        ]

    def has_previous_callbacks(self):
        previous_qs = Callback.objects.filter(
            content_type=self.content_type,
            object_id=self.object_id,
            status__in=['registered', 'running', 'toretry'],
            created_at__lt=self.created_at,
        ).exclude(pk=self.pk)
        return previous_qs.exists()

    @classmethod
    def notify(cls, instance, notification_type, payload):
        callback = cls.objects.create(
            content_object=instance,
            notification_type=notification_type,
            payload=payload or {},
        )

        callback.do_notify()
        return callback

    def do_notify(self):
        if self.has_previous_callbacks():
            # this callback should be run after previous callbacks are completed or failed
            return

        if not self.content_object:
            return
        try:
            if not Callback.objects.filter(pk=self.pk, status__in=['registered', 'toretry']).update(
                status='running'
            ):
                return
            self.content_object.do_notify(
                self.notification_type, self.payload, timeout=(15, 60) if self.retries_counter else None
            )
        except CallbackFailure as e:
            logging_method = 'warning'
            self.retries_counter += 1
            if self.retries_counter > settings.CALLBACK_MAX_RETRIES / 2:
                logging_method = 'error'
            getattr(logging, logging_method)(str(e))
            self.retry_reason = str(e)[:250]
            if self.retries_counter > settings.CALLBACK_MAX_RETRIES:
                self.status = 'failed'
            else:
                self.status = 'toretry'
            self.save()
        else:
            self.status = 'completed'
            self.save()

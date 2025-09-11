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

import inspect
import logging

from django.db.transaction import TransactionManagementError, atomic

from authentic2.apps.journal.models import Event, EventType, EventTypeDefinition

logger = logging.getLogger(__name__)


class Journal:
    def __init__(self, request=None, user=None, session=None):
        self.request = request
        self._user = user
        self._session = session
        self._pending_records = []

    @property
    def user(self):
        return self._user or (
            self.request.user
            if hasattr(self.request, 'user') and self.request.user.is_authenticated
            else None
        )

    @property
    def session(self):
        return self._session or (self.request.session if hasattr(self.request, 'session') else None)

    def massage_kwargs(self, record_parameters, kwargs):
        for key in ['user', 'session']:
            if key in record_parameters and key not in kwargs:
                kwargs[key] = getattr(self, key)
        return kwargs

    def record(self, event_type_name, **kwargs):
        evd_class = EventTypeDefinition.get_for_name(event_type_name)
        if evd_class is None:
            logger.error('invalid event_type name "%s"', event_type_name)
            return
        try:
            record = evd_class.record
            record_signature = inspect.signature(record)
            parameters = record_signature.parameters

            kwargs = self.massage_kwargs(parameters, kwargs)
            try:
                with atomic(durable=True):
                    record(**kwargs)
            except (TransactionManagementError, RuntimeError):
                evt = record(**kwargs)
                self._pending_records.append((evt, event_type_name, kwargs))
        except Exception:
            logger.exception('failure to record event "%s"', event_type_name)

    def record_pending(self):
        pending = self._pending_records
        self._pending_records = []
        for evt, event_type_name, kwargs in pending:
            if evt is None:
                logger.warning(
                    '%s(%s) event may have been rollback, but the record method didn\'t return event instance.',
                    event_type_name,
                    ', '.join(['%s=%r' % it for it in kwargs.items()]),
                )
                continue
            try:
                evt.refresh_from_db()
            except Event.DoesNotExist:
                # EventType are cached by name and may also have been rolled-back
                event_type = EventType.objects.get_for_name(event_type_name)
                try:
                    event_type.refresh_from_db()
                except EventType.DoesNotExist:
                    event_type.save()
                self.record(event_type_name, **kwargs)


journal = Journal()

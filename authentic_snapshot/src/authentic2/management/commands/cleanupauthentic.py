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

import logging

from django.apps import apps
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Clean expired models of authentic2.'

    def handle(self, **options):
        for app in apps.get_app_configs():
            for model in app.get_models():
                # only models from authentic2
                if model.__module__.startswith('authentic2'):
                    try:
                        self.cleanup_model(model)
                    except Exception:
                        logger.exception('cleanup of model %s failed', model)

    def cleanup_model(self, model):
        manager = getattr(model, 'objects', None)
        if hasattr(manager, 'cleanup'):
            manager.cleanup()
        if hasattr(model, 'cleanup'):
            model.cleanup()

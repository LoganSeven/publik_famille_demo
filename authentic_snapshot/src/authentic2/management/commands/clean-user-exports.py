# authentic2 - versatile identity manager
# Copyright (C) 2010-2021 Entr'ouvert
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

import os
from datetime import datetime, timedelta
from shutil import rmtree

from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Clean old export files.'

    def handle(self, **options):
        path = default_storage.path('user_exports')
        if not os.path.exists(path):
            return

        for directory in os.listdir(path):
            dir_path = os.path.join(path, directory)
            modification_timestamp = os.path.getmtime(dir_path)
            if datetime.now() - datetime.fromtimestamp(modification_timestamp) > timedelta(days=7):
                rmtree(dir_path)

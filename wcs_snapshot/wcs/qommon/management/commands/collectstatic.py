# w.c.s. - web application for online forms
# Copyright (C) 2005-2017  Entr'ouvert
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

import os
import shutil

from django.core.management.base import BaseCommand

from wcs.qommon.publisher import get_publisher_class


class Command(BaseCommand):
    help = 'Collect static files in a single location.'

    def add_arguments(self, parser):
        parser.add_argument(
            '-c',
            '--clear',
            action='store_true',
            dest='clear',
            default=False,
            help='Clear the existing files using the storage '
            'before trying to copy or link the original file.',
        )
        parser.add_argument(
            '-l',
            '--link',
            action='store_true',
            dest='link',
            default=False,
            help='Create a symbolic link to each file instead of copying.',
        )

    def handle(self, **options):
        Publisher = get_publisher_class()
        Publisher.ERROR_LOG = None
        pub = Publisher.create_publisher()
        return self.collectstatic(pub, clear=options['clear'], link=options['link'])

    @classmethod
    def collectstatic(cls, pub, clear=False, link=False):
        root_directory_class = pub.root_directory_class.static
        static_dir = os.path.join(pub.app_dir, 'collectstatic')
        if clear and os.path.exists(static_dir):
            shutil.rmtree(static_dir)
        if not os.path.exists(static_dir):
            os.mkdir(static_dir)
        for prefix in root_directory_class.static_directories:
            for directory in root_directory_class.resolve_static_directories(prefix):
                if not os.path.exists(directory):
                    continue
                real_prefix = prefix.replace('_', '/')  # xstatic hack
                dst_base = os.path.join(static_dir, real_prefix)
                for basedir, dummy, filenames in os.walk(directory):
                    for filename in filenames:
                        dst_path = os.path.join(dst_base, basedir[len(directory) + 1 :])
                        dst_filename = os.path.join(dst_path, filename)
                        src_filename = os.path.join(basedir, filename)
                        src_mtime = int(os.stat(src_filename).st_mtime)
                        try:
                            dst_mtime = int(os.stat(dst_filename).st_mtime)
                        except OSError:
                            dst_mtime = 0
                        if src_mtime <= dst_mtime:
                            continue
                        if not os.path.exists(dst_path):
                            os.makedirs(dst_path)
                        if os.path.exists(dst_filename) or os.path.islink(dst_filename):
                            os.unlink(dst_filename)
                        if link:
                            os.symlink(src_filename, dst_filename)
                        else:
                            shutil.copy2(src_filename, dst_filename)
                            os.utime(dst_filename, (src_mtime, src_mtime))

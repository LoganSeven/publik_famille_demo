# w.c.s. - web application for online forms
# Copyright (C) 2005-2024  Entr'ouvert
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
import sys
from datetime import datetime
from shutil import rmtree

import psycopg2
import psycopg2.errorcodes

from . import TenantCommand


class Command(TenantCommand):
    support_all_tenants = False

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument('--force-drop', action='store_true')

    def handle(self, *args, **options):
        for domain in self.get_domains(**options):
            publisher = self.init_tenant_publisher(domain, register_tld_names=False)
            publisher.cleanup()
            self.delete_tenant(publisher, **options)

    def delete_tenant(self, pub, **options):
        if options.get('force_drop'):
            rmtree(pub.app_dir)
        else:
            deletion_date = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            os.rename(pub.app_dir, pub.app_dir + '_removed_%s.invalid' % deletion_date)

        postgresql_cfg = {}
        for k, v in pub.cfg['postgresql'].items():
            if v and isinstance(v, str):
                postgresql_cfg[k] = v

        # if there's a createdb-connection-params, we can do a DROP DATABASE with
        # the option --force-drop, rename it if not
        createdb_cfg = pub.cfg['postgresql'].get('createdb-connection-params', {})
        createdb = True
        if not createdb_cfg:
            createdb_cfg = postgresql_cfg
            createdb = False
        if 'database' in createdb_cfg:
            createdb_cfg['dbname'] = createdb_cfg.pop('database')
        try:
            pgconn = psycopg2.connect(**createdb_cfg)
        except psycopg2.Error as e:
            print(
                'failed to connect to postgresql (%s)' % psycopg2.errorcodes.lookup(e.pgcode),
                file=sys.stderr,
            )
            return

        pgconn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
        cur = pgconn.cursor()
        dbname = postgresql_cfg.get('dbname') or postgresql_cfg.get('database')
        try:
            if createdb:
                # terminate all postgresql backend processes (such as pgbouncer) using
                # the database.
                cur.execute(
                    'SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s', (dbname,)
                )
                if options.get('force_drop'):
                    cur.execute('DROP DATABASE %s' % dbname)
                else:
                    cur.execute('ALTER DATABASE %s RENAME TO removed_%s_%s' % (dbname, deletion_date, dbname))
            else:
                cur.execute(
                    """SELECT table_name
                               FROM information_schema.tables
                               WHERE table_schema = 'public'
                               AND table_type = 'BASE TABLE'"""
                )

                tables_names = [x[0] for x in cur.fetchall()]

                if options.get('force_drop'):
                    for table_name in tables_names:
                        cur.execute('DROP TABLE %s CASCADE' % table_name)

                else:
                    schema_name = 'removed_%s_%s' % (deletion_date, dbname)
                    cur.execute('CREATE SCHEMA %s' % schema_name[:63])
                    for table_name in tables_names:
                        cur.execute('ALTER TABLE %s SET SCHEMA %s' % (table_name, schema_name[:63]))

        except psycopg2.Error as e:
            print(
                'failed to alter database %s: (%s)' % (dbname, psycopg2.errorcodes.lookup(e.pgcode)),
                file=sys.stderr,
            )
            pgconn.close()
            return

        cur.close()
        pgconn.close()

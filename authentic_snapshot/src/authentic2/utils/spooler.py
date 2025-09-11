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

import contextlib
import logging
import sys
from functools import wraps

from django.db import close_old_connections, connection

USE_UWSGI = 'uwsgi' in sys.modules


logger = logging.getLogger(__name__)


def ensure_db(func):
    """Emulate Django"s setup/teardown of database connections before/after
    each request"""

    @wraps(func)
    def f(*args, **kwargs):
        close_old_connections()
        try:
            return func(*args, **kwargs)
        finally:
            close_old_connections()

    return f


@contextlib.contextmanager
def tenant_context(domain):
    from hobo.multitenant.middleware import TenantMiddleware  # pylint: disable=import-error
    from tenant_schemas.utils import tenant_context  # pylint: disable=import-error

    tenant = TenantMiddleware.get_tenant_by_hostname(domain)
    with tenant_context(tenant):
        yield


def tenantspool(func):
    """Wrap a function with uwsgidecorators.spool storing and restoring the
    current tenant."""
    if not USE_UWSGI:
        return func

    from uwsgidecorators import spool

    @ensure_db
    @wraps(func)
    def spooler_func(*args, **kwargs):
        with contextlib.ExitStack() as stack:
            if 'domain' in kwargs:
                stack.enter_context(tenant_context(kwargs.pop('domain')))
            try:
                func(*args, **kwargs)
            except Exception:
                logger.exception('spooler: exception during %s(%s, %s)', func.__name__, args, kwargs)
            else:
                logger.info('spooler: success of %s(%s, %s)', func.__name__, args, kwargs)

    # pass arguments as pickles
    base_spooler = spool(pass_arguments=True)(spooler_func)

    @wraps(func)
    def spooler(*args, **kwargs):
        domain = getattr(getattr(connection, 'tenant', None), 'domain_url', None)
        if domain is not None:
            kwargs['domain'] = domain
        return base_spooler(*args, **kwargs)

    return spooler


@tenantspool
def export_users(uuid, query):
    from authentic2.manager.user_export import export_users_to_file

    export_users_to_file(uuid, query)

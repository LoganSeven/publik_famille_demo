# w.c.s. - web application for online forms
# Copyright (C) 2005-2010  Entr'ouvert
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

import logging
import os
import sys

from quixote import get_publisher, get_request
from quixote.logger import DefaultLogger


class ApplicationLogger(DefaultLogger):
    def __init__(self, *args, **kwargs):
        stdout = sys.stdout
        super().__init__(*args, **kwargs)
        sys.stdout = stdout

    def log_internal_error(self, error_summary, error_msg, tech_id=None):
        self.log('exception caught')
        self.error_log.write(error_msg)
        if self.error_email:
            from .emails import email

            headers = {}
            if tech_id:
                headers['References'] = '<%s@%s>' % (tech_id, os.path.basename(get_publisher().app_dir))
            email_from = getattr(self, 'error_email_from', None) or self.error_email
            email(
                subject='[ERROR] %s' % error_summary,
                mail_body=error_msg,
                email_from=email_from,
                email_rcpt=[self.error_email],
                want_html=False,
                fire_and_forget=True,
                extra_headers=headers,
                ignore_mail_redirection=True,
            )


class Formatter(logging.Formatter):
    def format(self, record):
        request = get_request()
        if request:
            record.address = request.get_environ('REMOTE_ADDR', '-')
            record.path = request.get_path()
        else:
            record.address = '-'
            record.path = '-'

        if get_publisher() and hasattr(get_publisher(), 'tenant'):
            record.tenant = get_publisher().tenant.hostname
        else:
            record.tenant = '-'

        return logging.Formatter.format(self, record).replace('\n', '\n ')

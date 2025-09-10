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

from wcs.wscalls import call_webservice, get_app_error_code

from . import get_cfg
from .errors import SMSError


class PasserelleSMS:
    TIMEOUT = 10

    def __init__(self):
        sms_cfg = get_cfg('sms', {})
        self.sender = sms_cfg.get('sender', '')
        self.url = sms_cfg.get('passerelle_url', '')

    def send(self, sender, destinations, text, counter_name, quality=None):
        sender = sender or self.sender
        payload = {
            'from': sender,
            'message': text,
            'to': destinations,
            'counter': counter_name,
        }

        response, status, data = call_webservice(self.url, method='POST', post_data=payload)
        if status != 200 or (response and get_app_error_code(response, data, 'json')):
            raise SMSError('(to: %r): %s' % (destinations, status))


class SMS:
    @classmethod
    def get_sms_class(cls):
        sms_cfg = get_cfg('sms', {})
        if sms_cfg.get('sender') and sms_cfg.get('passerelle_url'):
            return PasserelleSMS()
        return None

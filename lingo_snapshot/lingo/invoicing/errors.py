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


from django.utils.translation import gettext_lazy as _


class PoolPromotionError(Exception):
    def __init__(self, msg):
        self.msg = msg


class InvoicingError(Exception):
    def __init__(self, details=None):
        self.details = details or {}
        super().__init__()

    def get_error_message(self):
        return str(self)

    def get_error_display(self):
        reason = None
        if self.details.get('reason'):
            reasons = {
                'empty-template': _('template is empty'),
                'empty-result': _('result is empty'),
                'syntax-error': _('syntax error'),
                'variable-error': _('variable error'),
                'missing-card-model': _('card model is not configured'),
                'not-a-boolean': _('result is not a boolean'),
                'not-defined': _('mapping not defined'),
            }
            reason = reasons.get(self.details['reason'])

        return self.get_error_message() % {
            'reason': reason,
            'key': self.details.get('key'),
        }


class PayerError(InvoicingError):
    label = _('Impossible to determine payer')

    def get_error_message(self):
        return _('Impossible to determine payer: %(reason)s')


class PayerDataError(InvoicingError):
    label = _('Impossible to get payer field')

    def get_error_message(self):
        return _('Impossible to get payer %(key)s: %(reason)s')


class AsyncJobException(Exception):
    pass


class WaitForOtherJobs(AsyncJobException):
    pass

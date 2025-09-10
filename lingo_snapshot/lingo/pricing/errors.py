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


class PricingError(Exception):
    def __init__(self, details=None):
        self.details = details or {}
        super().__init__()

    def get_error_message(self):
        return str(self)

    def get_error_display(self):
        formats = {
            'decimal': _('decimal'),
        }
        reason = None
        if self.details.get('reason'):
            reasons = {
                'not-found': _('not found'),
                'wrong-kind': _('wrong kind (group: %(check_type_group)s, check type: %(check_type)s)'),
                'not-configured': _(
                    'pricing not configured (group: %(check_type_group)s, check type: %(check_type)s)'
                ),
            }
            reason = reasons.get(self.details['reason']) % {
                'check_type': self.details.get('check_type'),
                'check_type_group': self.details.get('check_type_group'),
            }

        return self.get_error_message() % {
            'category': self.details.get('category'),
            'criterias': ', '.join(
                '%s (%s)' % (v, _('category: %s') % k) for k, v in self.details.get('criterias', {}).items()
            ),
            'pricing': self.details.get('pricing'),
            'wanted': formats.get(self.details.get('wanted')),
            'status': self.details.get('status'),
            'reason': reason,
            'reduction_rate': self.details.get('reduction_rate'),
            'effort_rate_target': self.details.get('effort_rate_target'),
        }


class PricingNotFound(PricingError):
    label = _('Agenda pricing not found')

    def get_error_message(self):
        return self.label


class CriteriaConditionNotFound(PricingError):
    label = _('No matching criteria for category')

    def get_error_message(self):
        return _('No matching criteria for category: %(category)s')


class MultipleDefaultCriteriaCondition(PricingError):
    label = _('Multiple default criteria found for category')

    def get_error_message(self):
        return _('Multiple default criteria found for category: %(category)s')


class PricingDataError(PricingError):
    label = _('Impossible to determine a pricing for criterias')

    def get_error_message(self):
        return _('Impossible to determine a pricing for criterias: %(criterias)s')


class MinPricingDataError(PricingError):
    label = _('Impossible to determine a minimal pricing for criterias')

    def get_error_message(self):
        return _('Impossible to determine a minimal pricing for criterias: %(criterias)s')


class PricingDataFormatError(PricingError):
    label = _('Pricing format error')

    def get_error_message(self):
        return _('Pricing is not a %(wanted)s: %(pricing)s')


class MinPricingDataFormatError(PricingError):
    label = _('Minimal pricing format error')

    def get_error_message(self):
        return _('Minimal pricing is not a %(wanted)s: %(pricing)s')


class PricingReductionRateError(PricingError):
    label = _('Impossible to determine a reduction rate')

    def get_error_message(self):
        return self.label


class PricingReductionRateFormatError(PricingError):
    label = _('Reduction rate format error')

    def get_error_message(self):
        return _('Reduction rate is not a %(wanted)s: %(reduction_rate)s')


class PricingReductionRateValueError(PricingError):
    label = _('Reduction rate bad value')

    def get_error_message(self):
        return _('Reduction rate bad value: %(reduction_rate)s')


class PricingEffortRateTargetError(PricingError):
    label = _('Impossible to determine an effort rate target')

    def get_error_message(self):
        return self.label


class PricingEffortRateTargetFormatError(PricingError):
    label = _('Effort rate target format error')

    def get_error_message(self):
        return _('Effort rate target is not a %(wanted)s: %(effort_rate_target)s')


class PricingEffortRateTargetValueError(PricingError):
    label = _('Effort rate target bad value')

    def get_error_message(self):
        return _('Effort rate target bad value: %(effort_rate_target)s')


class PricingAccountingCodeError(PricingError):
    label = _('Impossible to determine an accounting code')

    def get_error_message(self):
        return self.label


class PricingUnknownCheckStatusError(PricingError):
    label = _('Unknown check status')

    def get_error_message(self):
        return _('Unknown check status: %(status)s')


class PricingEventNotCheckedError(PricingError):
    label = _('Event is not checked')

    def get_error_message(self):
        return self.label


class PricingBookingNotCheckedError(PricingError):
    label = _('Booking is not checked')

    def get_error_message(self):
        return self.label


class PricingMultipleBookingError(PricingError):
    label = _('Multiple booking found')

    def get_error_message(self):
        return self.label


class PricingBookingCheckTypeError(PricingError):
    label = _('Check type error')

    def get_error_message(self):
        return _('Check type error: %(reason)s')

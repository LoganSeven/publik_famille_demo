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


import logging
import urllib.parse
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone, translation

from authentic2 import app_settings
from authentic2.a2_rbac.models import OrganizationalUnit
from authentic2.backends import get_user_queryset
from authentic2.backends.ldap_backend import LDAPBackend
from authentic2.journal_event_types import UserDeletionForInactivity, UserNotificationInactivity
from authentic2.utils import sms as utils_sms
from authentic2.utils.misc import get_password_authenticator, send_templated_mail

logger = logging.getLogger(__name__)

User = get_user_model()


class Command(BaseCommand):
    help = '''Clean unused accounts'''

    verbosity_to_log_level = {
        0: logging.CRITICAL,
        1: logging.WARNING,
        2: logging.INFO,
        3: logging.DEBUG,
    }

    def add_arguments(self, parser):
        parser.add_argument('--fake', action='store_true', help='do nothing', default=False)

    def handle(self, *args, **options):
        self.fake = options['fake']
        self.is_phone_authn_active = get_password_authenticator().is_phone_authn_active

        # add StreamHandler for console output
        handler = logging.StreamHandler()
        handler.setLevel(level=self.verbosity_to_log_level[options['verbosity']])
        logger.addHandler(handler)
        # prevent logging to external logs when fake
        if self.fake:
            logger.propagate = False

        self.now = timezone.now()

        realms = [block['realm'] for block in LDAPBackend.get_config() if block.get('realm')]
        self.user_qs = get_user_queryset().exclude(userexternalid__source__in=realms)
        if not self.is_phone_authn_active:
            self.user_qs = self.user_qs.exclude(email='')

        translation.activate(settings.LANGUAGE_CODE)
        try:
            self.clean_unused_accounts()
        except Exception:
            logger.exception('clean-unused-accounts failed')

    def clean_unused_accounts(self):
        count = app_settings.A2_CLEAN_UNUSED_ACCOUNTS_MAX_MAIL_PER_PERIOD
        for ou in OrganizationalUnit.objects.filter(clean_unused_accounts_alert__isnull=False):
            alert_delay = timedelta(days=ou.clean_unused_accounts_alert)
            deletion_delay = timedelta(days=ou.clean_unused_accounts_deletion)
            ou_users = self.user_qs.filter(ou=ou)

            # reset last_account_deletion_alert for users which connected since last alert
            active_users = ou_users.filter(
                Q(last_login__gte=F('last_account_deletion_alert'))
                | Q(keepalive__gte=F('last_account_deletion_alert'))
            )
            active_users.update(last_account_deletion_alert=None)

            inactive_users = ou_users.filter(
                (
                    Q(last_login__lte=self.now - alert_delay)
                    | (Q(last_login__isnull=True) & Q(date_joined__lte=self.now - alert_delay))
                )
                & (Q(keepalive__isnull=True) | Q(keepalive__lte=self.now - alert_delay))
            )

            # send first alert to users having never received an alert beforehand, skipping
            # federated users
            inactive_users_first_alert = inactive_users.filter(
                Q(last_account_deletion_alert__isnull=True)
                & Q(oidc_account__isnull=True)
                & Q(saml_identifiers__isnull=True)
            )
            days_to_deletion = ou.clean_unused_accounts_deletion - ou.clean_unused_accounts_alert
            for user in inactive_users_first_alert[:count]:
                logger.info('%s last login %d days ago, sending alert', user, ou.clean_unused_accounts_alert)
                self.send_alert(
                    user,
                    days_to_deletion=days_to_deletion,
                    days_of_inactivity=alert_delay.days,
                )

            inactive_users_to_delete = inactive_users.filter(
                (
                    Q(last_login__lte=self.now - deletion_delay)
                    | Q(last_login__isnull=True) & Q(date_joined__lte=self.now - deletion_delay)
                )
                & (Q(keepalive__isnull=True) | Q(keepalive__lte=self.now - deletion_delay))
                # ensure respect of alert delay before deletion
                # or if user is federated and never logged-in
                & (
                    Q(last_account_deletion_alert__lte=self.now - (deletion_delay - alert_delay))
                    | Q(last_login__isnull=True)
                    & (Q(oidc_account__isnull=False) | Q(saml_identifiers__isnull=False))
                )
            )
            for user in inactive_users_to_delete[:count]:
                logger.info(
                    '%s last login more than %d days ago, deleting user',
                    user,
                    ou.clean_unused_accounts_deletion,
                )

                has_saml_identifiers = getattr(user, 'saml_identifiers', None) and user.saml_identifiers.all()
                self.delete_user(
                    user,
                    days_of_inactivity=deletion_delay.days,
                    send_notification=user.last_login
                    or not (getattr(user, 'oidc_account', None) or has_saml_identifiers),
                )

    def send_alert(self, user, days_to_deletion, days_of_inactivity):
        ctx = {
            'user': user,
            'days_to_deletion': days_to_deletion,
            'login_url': urllib.parse.urljoin(settings.SITE_BASE_URL, settings.LOGIN_URL),
        }
        with transaction.atomic():
            if not self.fake:
                User.objects.filter(pk=user.pk).update(last_account_deletion_alert=self.now)
                UserNotificationInactivity.record(
                    user=user, days_of_inactivity=days_of_inactivity, days_to_deletion=days_to_deletion
                )
            if user.email:
                self.send_mail('authentic2/unused_account_alert', user, ctx)
            elif self.is_phone_authn_active and user.phone_identifier:
                self.send_sms('authentic2/unused_account_alert_sms.txt', user, ctx)
            else:
                logger.debug('%s has no email or identifiable phone number, alert was not sent', user)

    def send_mail(self, prefix, user, ctx):
        logger.debug('sending mail to %s', user.email)
        if not self.fake:

            def send_mail():
                send_templated_mail(user, prefix, ctx)

            transaction.on_commit(send_mail)

    def send_sms(self, template_name, user, ctx):
        logger.debug('sending sms to %s', user.email)
        if not self.fake:

            def send_sms():
                utils_sms.send_sms(
                    user.phone_identifier,
                    user.ou,
                    user=user,
                    template_names=(template_name,),
                    context=ctx,
                    kind=None,
                )

            transaction.on_commit(send_sms)

    def delete_user(self, user, days_of_inactivity, send_notification=True):
        ctx = {'user': user}
        with transaction.atomic():
            if send_notification:
                if user.email:
                    self.send_mail('authentic2/unused_account_delete', user, ctx)
                elif self.is_phone_authn_active and user.phone_identifier:
                    self.send_sms('authentic2/unused_account_delete_sms.txt', user, ctx)
            if not self.fake:
                UserDeletionForInactivity.record(user=user, days_of_inactivity=days_of_inactivity)
                user.delete()

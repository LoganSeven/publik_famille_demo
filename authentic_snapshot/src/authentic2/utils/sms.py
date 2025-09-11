# authentic2 - versatile identity manager
# Copyright (C) 2010-2022 Entr'ouvert
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
from random import choices

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from requests import RequestException

from authentic2 import app_settings
from authentic2.utils.misc import get_password_authenticator, render_plain_text_template_to_string

try:
    from hobo.requests_wrapper import Requests
except ImportError:  # fallback on python requests, no Publik signature
    from requests.sessions import Session as Requests  # # pylint: disable=ungrouped-imports


def sms_ratelimit_key(group, request):
    if 'phone' in request.session:
        phone = request.session['phone']
        return f'{group}:{phone}'
    else:
        prefix = request.POST['phone_0'][0]
        number = request.POST['phone_1'][0]
        return f'{group}:{prefix}:{number}'


def create_sms_code():
    return ''.join(
        choices(
            settings.SMS_CODE_ALLOWED_CHARACTERS,
            k=settings.SMS_CODE_LENGTH,
        )
    )


def generate_code(phone_number, user=None, kind=None, fake=False):
    from authentic2.models import SMSCode

    return SMSCode.create(
        phone_number,
        user=user,
        kind=kind or SMSCode.KIND_REGISTRATION,
        fake=fake or kind is SMSCode.KIND_PASSWORD_LOST and user is None,
    )


class SMSError(Exception):
    pass


def send_sms(phone_number, ou, user=None, template_names=None, context=None, kind=None, **kwargs):
    """Sends a registration code sms to a user, the latter inputs the received code
    in a dedicated form to validate their account creation.
    """

    from authentic2.models import AttributeValue, SMSCode

    logger = logging.getLogger(__name__)

    sender = settings.SMS_SENDER
    url = settings.SMS_URL
    requests = Requests()  # Publik signature requests wrapper

    if not sender:
        logger.error('settings.SMS_SENDER is not set')
        raise SMSError('SMS improperly configured')
    if not url:
        logger.error('settings.SMS_URL is not set')
        raise SMSError('SMS improperly configured')

    if not isinstance(context, dict):
        context = {}

    code = None
    existing_accounts = None
    if kind is not None:
        # SMS with a specific action requires generating a code
        code = generate_code(phone_number, user=user, kind=kind)
        if code.fake is True:
            return code
        context.update({'code': code})

        if kind == SMSCode.KIND_REGISTRATION:
            # existing accounts
            existing_accounts = AttributeValue.objects.filter(
                content_type=ContentType.objects.get_for_model(get_user_model()),
                object_id__isnull=False,
                multiple=False,
                attribute=get_password_authenticator().phone_identifier_field,
                content=phone_number,
            ).values_list('object_id', flat=True)
            if not app_settings.A2_PHONE_IS_UNIQUE:
                existing_accounts = (
                    get_user_model()
                    .objects.filter(
                        id__in=existing_accounts,
                        ou=ou,
                    )
                    .exists()
                )
            context.update({'existing_accounts': bool(existing_accounts)})

    message = render_plain_text_template_to_string(template_names, context)

    payload = {
        'message': message,
        'from': sender,
        'to': [phone_number],
    }

    try:
        with transaction.atomic():
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
    except RequestException as e:
        logger.warning('sms code to %s using %s failed: %s', phone_number, url, e)
        raise SMSError(f'Error while contacting SMS service: {e}')
    return code


def send_registration_sms(phone_number, ou, template_names=None, context=None, **kwargs):
    from authentic2.models import SMSCode

    return send_sms(
        phone_number,
        ou,
        template_names=template_names or ['registration/sms_code_registration.txt'],
        context=context,
        kind=SMSCode.KIND_REGISTRATION,
        **kwargs,
    )


def send_password_reset_confirmation_sms(phone_number, ou, context=None):
    return send_sms(
        phone_number,
        ou,
        template_names=['password_lost/sms_password_change_confirmation.txt'],
        context=context,
    )


def send_account_deletion_sms(phone_number, ou, user=None, template_names=None, context=None, **kwargs):
    from authentic2.models import SMSCode

    return send_sms(
        phone_number,
        ou,
        user=user,
        template_names=template_names or ['deletion/sms_code_account_deletion.txt'],
        context=context,
        kind=SMSCode.KIND_ACCOUNT_DELETION,
        **kwargs,
    )


def send_password_reset_sms(phone_number, ou, user=None, template_names=None, context=None, **kwargs):
    from authentic2.models import SMSCode

    return send_sms(
        phone_number,
        ou,
        user=user,
        template_names=template_names or ['password_lost/sms_code_password_lost.txt'],
        context=context,
        kind=SMSCode.KIND_PASSWORD_LOST,
        **kwargs,
    )


def send_phone_change_sms(phone_number, ou, user=None, template_names=None, context=None, **kwargs):
    from authentic2.models import SMSCode

    return send_sms(
        phone_number,
        ou,
        user=user,
        template_names=template_names or ['phone_change/sms_code_phone_change.txt'],
        context=context,
        kind=SMSCode.KIND_PHONE_CHANGE,
        **kwargs,
    )

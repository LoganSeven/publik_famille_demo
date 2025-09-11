# authentic2-auth-fc - authentic2 authentication for FranceConnect
# Copyright (C) 2019 Entr'ouvert
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


import datetime
import json
import logging
import os
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.shortcuts import resolve_url
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import gettext_lazy as _

from authentic2.validators import email_validator

from . import app_settings


def build_logout_url(request, logout_url, next_url=None):
    """
    For now fc_id_token in request.session is used as the flag of an
    active session on the OP. It is set in the login view and deleted in the
    logout return view.
    """
    if not next_url:
        next_url = resolve_url(settings.LOGIN_REDIRECT_URL)
    state = str(uuid.uuid4())
    states = request.session.setdefault('fc_states', {})
    request.session.modified = True
    states[state] = {
        'next': next_url,
    }
    if 'fc_id_token' in request.session:
        callback = request.build_absolute_uri(reverse('fc-logout'))
        qs = {
            'id_token_hint': request.session.get('fc_id_token_raw'),
            'post_logout_redirect_uri': callback,
            'state': state,
        }
        return logout_url + '?' + urlencode(qs)
    return None


def get_ref(ref, user_info):
    if not hasattr(user_info, 'items'):
        return None
    if '.' in ref:
        left, right = ref.split('.', 1)
        return get_ref(right, user_info.get(left, {}))
    return user_info[ref]


def mapping_to_value(mapping, user_info):
    if 'ref' in mapping:
        value = get_ref(mapping['ref'], user_info)
    elif 'value' in mapping:
        value = mapping['value']
    elif 'compute' in mapping:
        if mapping['compute'] == 'today':
            value = datetime.date.today()
        elif mapping['compute'] == 'random':
            value = str(uuid.uuid4())
    else:
        raise NotImplementedError

    if 'translation' in mapping:
        if mapping['translation'] == 'insee-communes':
            value = resolve_insee_commune(value)
        elif mapping['translation'] == 'insee-countries':
            value = resolve_insee_country(value)
        elif mapping['translation'] == 'insee-territory':
            value = resolve_insee_territory(value)
        elif mapping['translation'] == 'isodate':
            value = datetime.datetime.strptime(value, '%Y-%m-%d').date()
        elif mapping['translation'] == 'simple':
            value = mapping['translation_simple'].get(value, mapping.get('translation_simple_default', ''))
        elif mapping['translation'] == 'notempty':
            value = bool(value)
        else:
            raise NotImplementedError
    return value


_insee_communes = None


def resolve_insee_commune(insee_code):
    global _insee_communes  # pylint: disable=global-statement
    if not _insee_communes:
        with open(os.path.join(os.path.dirname(__file__), 'insee-communes.json')) as f:
            _insee_communes = json.load(f)
    return _insee_communes.get(insee_code, _('Unknown INSEE code'))


_insee_countries = None


def resolve_insee_country(insee_code):
    global _insee_countries  # pylint: disable=global-statement

    if not _insee_countries:
        with open(os.path.join(os.path.dirname(__file__), 'insee-countries.json')) as f:
            _insee_countries = json.load(f)
    return _insee_countries.get(insee_code, _('Unknown INSEE code'))


def resolve_insee_territory(insee_code):
    global _insee_communes  # pylint: disable=global-statement
    if not _insee_communes:
        with open(os.path.join(os.path.dirname(__file__), 'insee-communes.json')) as f:
            _insee_communes = json.load(f)
    if commune := _insee_communes.get(insee_code):
        return commune
    global _insee_countries  # pylint: disable=global-statement
    if not _insee_countries:
        with open(os.path.join(os.path.dirname(__file__), 'insee-countries.json')) as f:
            _insee_countries = json.load(f)
    if known := _insee_countries.get(insee_code):
        return _('Foreign country or territory ({})').format(known)
    else:
        return _('Unknown INSEE code')


def apply_user_info_mappings(user, user_info):
    assert user
    assert user_info

    logger = logging.getLogger(__name__)
    mappings = app_settings.user_info_mappings

    save_user = False
    tags = set()
    for attribute, mapping in mappings.items():
        # normalize mapping to dictionaries: if string, convert into a simple reference
        if hasattr(mapping, 'format'):
            mapping = {'ref': mapping}
        try:
            value = mapping_to_value(mapping, user_info)
        except (ValueError, KeyError, NotImplementedError) as e:
            logger.warning('auth_fc: cannot apply mapping %s <- %r: %s', attribute, mapping, e)
            continue
        if mapping.get('if-tag') and mapping['if-tag'] not in tags:
            continue

        if attribute == 'email':
            try:
                email_validator(value)
            except ValidationError:
                logger.warning('auth_fc: invalid email "%s" was ignored', value)
                continue

        if attribute == 'password':
            if mapping.get('if-empty') and user.has_usable_password():
                continue
            save_user = True
            user.set_password(value)
        elif hasattr(user.attributes, attribute):
            if mapping.get('if-empty') and getattr(user.attributes, attribute):
                continue
            verified = mapping.get('verified', False)
            accessor = user.verified_attributes if verified else user.attributes
            setattr(accessor, attribute, value)
        elif hasattr(user, attribute):
            save_user = True
            if mapping.get('if-empty') and getattr(user, attribute):
                continue
            setattr(user, attribute, value)
        else:
            logger.warning('auth_fc: unknown attribute in user_info mapping: %s', attribute)
            continue
        if mapping.get('tag'):
            tags.add(mapping['tag'])
    if save_user:
        user.save()


def clean_fc_session(session):
    session.pop('fc_id_token', None)
    session.pop('fc_id_token_raw', None)

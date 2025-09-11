# authentic2 - versatile identity manager
# Copyright (C) 2010-2019 Entr'ouvert
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


import string

import pytest
from django.core.exceptions import ValidationError

from authentic2 import app_settings
from authentic2.apps.authenticators.models import LoginPasswordAuthenticator
from authentic2.models import Attribute
from authentic2.passwords import generate_password, init_password_dictionaries, validate_password
from authentic2.utils.misc import get_password_authenticator


def test_generate_password(db):
    passwords = {generate_password() for i in range(10)}

    char_classes = [string.digits, string.ascii_lowercase, string.ascii_uppercase, string.punctuation]
    assert len(passwords) == 10
    for password in passwords:
        assert len(password) >= max(get_password_authenticator().password_min_length, 8)
        assert sum(any(char in char_class for char in password) for char_class in char_classes) == max(
            app_settings.A2_PASSWORD_POLICY_MIN_CLASSES, 3
        )


def test_validate_password_default_policy(db, settings):
    with pytest.raises(ValidationError):
        validate_password('aaaaaZZZZZZ')
    with pytest.raises(ValidationError):
        validate_password('00000aaaaaa')
    with pytest.raises(ValidationError):
        validate_password('00000ZZZZZZ')
    validate_password('000aaaaZZZZ')


def test_digits_password_policy(db, settings):
    LoginPasswordAuthenticator.objects.update(
        password_regex='^[0-9]{8}$', password_regex_error_msg='pasbon', password_min_length=0
    )
    settings.A2_PASSWORD_POLICY_MIN_CLASSES = 0

    with pytest.raises(ValidationError):
        validate_password('aaa')
    validate_password('12345678')


@pytest.mark.parametrize(
    'password,min_strength',
    [
        ('?', 0),
        ('?JR!', 1),
        ('?JR!p4A', 2),
        ('?JR!p4A2i', 3),
        ('?JR!p4A2i:#', 4),
    ],
)
def test_validate_password_strength(db, settings, password, min_strength):
    LoginPasswordAuthenticator.objects.update(
        password_min_length=len(password), min_password_strength=min_strength
    )
    validate_password(password)

    with pytest.raises(ValidationError):
        LoginPasswordAuthenticator.objects.update(password_min_length=len(password) + 1)
        validate_password(password)

    if min_strength < 4:
        LoginPasswordAuthenticator.objects.update(
            password_min_length=len(password), min_password_strength=min_strength + 1
        )
        with pytest.raises(ValidationError):
            validate_password(password)


def test_validate_password_strength_user_attributes(db, simple_user):
    LoginPasswordAuthenticator.objects.update(min_password_strength=3)
    simple_user.attributes.last_name = 'Kaczynski'

    validate_password('Kaczynski')

    with pytest.raises(ValidationError):
        validate_password('Kaczynski', inputs={'last_name': 'Kaczynski'})

    # each word of input should be matched
    with pytest.raises(ValidationError):
        validate_password('Kaczynski', inputs={'last_name': 'Kaczynski Faas-Hardegger'})

    with pytest.raises(ValidationError):
        validate_password('Kaczynski Faas-Hardegger', inputs={'last_name': 'Kaczynski Faas-Hardegger'})

    simple_user.attributes.last_name = 'Kaczynski Faas-Hardegger'
    with pytest.raises(ValidationError):
        validate_password('Kaczynski', user=simple_user)

    with pytest.raises(ValidationError):
        validate_password('Kaczynski Faas-Hardegger', user=simple_user)

    simple_user.attributes.last_name = 'Kaczynski'
    # inputs dict should override user attributes
    validate_password('Kaczynski', user=simple_user, inputs={'last_name': 'Faas-Hardegger'})


def test_validate_password_strength_custom_attribute(db, simple_user):
    LoginPasswordAuthenticator.objects.update(min_password_strength=3)
    Attribute.objects.create(
        kind='string',
        name='favourite_song',
    )

    validate_password('0opS 1 D1t iT @GAiN', user=simple_user)

    simple_user.attributes.favourite_song = '0opS 1 D1t iT @GAiN'
    with pytest.raises(ValidationError):
        validate_password('0opS 1 D1t iT @GAiN', user=simple_user)


def test_init_password_dictionaries(db, tmp_path):
    test_dictionary = tmp_path / 'dict'
    test_dictionary.write_text('0opS !? 1 D1t iT @GAiN\n1 pl@yed with your <3 He@rt\n')

    LoginPasswordAuthenticator.objects.update(min_password_strength=3)

    validate_password('0opS !? 1 D1t iT @GAiN')
    validate_password('1 pl@yed with your <3 He@rt')

    app_settings.A2_PASSWORD_POLICY_DICTIONARIES = {'3r1tney': test_dictionary}
    init_password_dictionaries()

    with pytest.raises(ValidationError):
        validate_password('0opS !? 1 D1t iT @GAiN')
    with pytest.raises(ValidationError):
        validate_password('1 pl@yed with your <3 He@rt')

    app_settings.A2_PASSWORD_POLICY_DICTIONARIES = {}
    init_password_dictionaries()

    validate_password('0opS !? 1 D1t iT @GAiN')
    validate_password('1 pl@yed with your <3 He@rt')


def test_zxcvbn_inputs_sideeffect_bug(db):
    LoginPasswordAuthenticator.objects.update(min_password_strength=3, password_min_length=0)
    validate_password('Kaczynski')
    with pytest.raises(ValidationError):
        validate_password('Kaczynski', inputs={'last_name': 'Kaczynski'})
    validate_password('Kaczynski')

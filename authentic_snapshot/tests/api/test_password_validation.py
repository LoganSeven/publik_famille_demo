# authentic2 - versatile identity manager
# Copyright (C) 2010-2023 Entr'ouvert
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

import pytest
from django.contrib.auth import get_user_model

from authentic2.apps.authenticators.models import LoginPasswordAuthenticator

pytestmark = pytest.mark.django_db

User = get_user_model()


def test_validate_password_default(app):
    for password, ok, length, lower, digit, upper in (
        ('.', False, False, False, False, False),
        ('x' * 8, False, True, True, False, False),
        ('x' * 8 + '1', False, True, True, True, False),
        ('x' * 8 + '1X', True, True, True, True, True),
    ):
        response = app.post_json('/api/validate-password/', params={'password': password})
        assert response.json['result'] == 1
        assert response.json['ok'] is ok
        assert len(response.json['checks']) == 4
        assert response.json['checks'][0]['label'] == '8 characters'
        assert response.json['checks'][0]['result'] is length
        assert response.json['checks'][1]['label'] == '1 lowercase letter'
        assert response.json['checks'][1]['result'] is lower
        assert response.json['checks'][2]['label'] == '1 digit'
        assert response.json['checks'][2]['result'] is digit
        assert response.json['checks'][3]['label'] == '1 uppercase letter'
        assert response.json['checks'][3]['result'] is upper


def test_validate_password_regex(app, settings):
    LoginPasswordAuthenticator.objects.update(
        password_regex='^.*ok.*$', password_regex_error_msg='must contain "ok"'
    )

    response = app.post_json('/api/validate-password/', params={'password': 'x' * 8 + '1X'})
    assert response.json['result'] == 1
    assert response.json['ok'] is False
    assert len(response.json['checks']) == 5
    assert response.json['checks'][0]['label'] == '8 characters'
    assert response.json['checks'][0]['result'] is True
    assert response.json['checks'][1]['label'] == '1 lowercase letter'
    assert response.json['checks'][1]['result'] is True
    assert response.json['checks'][2]['label'] == '1 digit'
    assert response.json['checks'][2]['result'] is True
    assert response.json['checks'][3]['label'] == '1 uppercase letter'
    assert response.json['checks'][3]['result'] is True
    assert response.json['checks'][4]['label'] == 'must contain "ok"'
    assert response.json['checks'][4]['result'] is False

    response = app.post_json('/api/validate-password/', params={'password': 'x' * 8 + 'ok1X'})
    assert response.json['result'] == 1
    assert response.json['ok'] is True
    assert len(response.json['checks']) == 5
    assert response.json['checks'][0]['label'] == '8 characters'
    assert response.json['checks'][0]['result'] is True
    assert response.json['checks'][1]['label'] == '1 lowercase letter'
    assert response.json['checks'][1]['result'] is True
    assert response.json['checks'][2]['label'] == '1 digit'
    assert response.json['checks'][2]['result'] is True
    assert response.json['checks'][3]['label'] == '1 uppercase letter'
    assert response.json['checks'][3]['result'] is True
    assert response.json['checks'][4]['label'] == 'must contain "ok"'
    assert response.json['checks'][4]['result'] is True


@pytest.mark.parametrize(
    'min_length, password,strength,label,inputs',
    [
        (0, '', 0, 'Very Weak', {}),
        (0, '?', 0, 'Very Weak', {}),
        (0, '?JR!', 1, 'Weak', {}),
        (0, '?JR!p4A', 2, 'Fair', {}),
        (0, '?JR!p4A2i', 3, 'Good', {}),
        (0, '?JR!p4A2i:#', 4, 'Strong', {}),
        (0, 'Kaczynski', 0, 'Very Weak', {'first_name': 'Kaczynski'}),
        (0, 'Faas-Hardegger', 4, 'Strong', {'first_name': 'Kaczynski'}),
        (12, '?JR!p4A2i:#', 0, 'Very Weak', {}),
    ],
)
def test_password_strength(app, settings, min_length, password, strength, label, inputs):
    LoginPasswordAuthenticator.objects.update(password_min_length=min_length)
    response = app.post_json('/api/password-strength/', params={'password': password, 'inputs': inputs})
    assert response.json['result'] == 1
    assert response.json['strength'] == strength
    assert response.json['strength_label'] == label


@pytest.mark.parametrize(
    'min_length, password, hint,inputs',
    [
        (0, '', 'add more words or characters.', {}),
        (0, 'sdfgh', 'avoid straight rows of keys like "sdfgh".', {}),
        (0, 'ertgfd', 'avoid short keyboard patterns like "ertgfd".', {}),
        (0, 'abab', 'avoid repeated words and characters like "abab".', {}),
        (0, 'abcd', 'avoid sequences like "abcd".', {}),
        (0, '2019', 'avoid recent years.', {}),
        (0, '02/08/14', 'avoid dates and years that are associated with you.', {}),
        (0, '02/08/14', 'avoid dates and years that are associated with you.', {}),
        (0, 'p@ssword', 'avoid "p@ssword" : it\'s similar to a commonly used password', {}),
        (0, 'password', 'avoid "password" : it\'s a commonly used password.', {}),
        (
            0,
            'Kaczynski',
            'avoid "Kaczynski" : it\'s similar to one of your personal informations.',
            {'first_name': 'Kaczynski'},
        ),
        (42, 'password', 'use at least 42 characters.', {}),
        (8, 'ha1Ienah', 'use a longer password.', {}),
    ],
)
def test_password_strength_hints(app, settings, min_length, password, hint, inputs):
    LoginPasswordAuthenticator.objects.update(password_min_length=min_length)
    html_hint_fmt = '%s <span class="a2-password-hint--hint">%s</span>'
    settings.A2_PASSWORD_POLICY_MIN_STRENGTH = 3
    response = app.post_json('/api/password-strength/', params={'password': password, 'inputs': inputs})
    assert response.json['result'] == 1
    assert response.json['hint'] == hint
    assert response.json['hint_html'] == html_hint_fmt % ('To create a more secure password, you can', hint)

    strength = response.json['strength']

    response = app.post_json(
        '/api/password-strength/',
        params={'password': password, 'inputs': inputs, 'min_strength': strength + 1},
    )
    assert response.json['hint_html'] == html_hint_fmt % (
        'Your password is too weak. To create a secure password, please',
        hint,
    )

    response = app.post_json(
        '/api/password-strength/',
        params={'password': password, 'inputs': inputs, 'min_strength': strength - 1},
    )
    assert response.json['hint_html'] == html_hint_fmt % (
        'Your password is strong enough. To create an even more secure password, you could',
        hint,
    )

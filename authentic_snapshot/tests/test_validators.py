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


import smtplib
from unittest import mock

import pytest
from django.core.exceptions import ValidationError

from authentic2.validators import EmailValidator, HexaColourValidator


def test_validate_colour():
    validator = HexaColourValidator()
    with pytest.raises(ValidationError):
        validator('abc')
    with pytest.raises(ValidationError):
        validator('blue')
    with pytest.raises(ValidationError):
        validator('#green')
    validator('#ff00ff')


class TestEmailValidator:
    @pytest.mark.parametrize(
        'bad_email',
        [
            'nok',
            '@nok.com',
            'foo@bar\x00',
            'foo&@bar',
            '|a@nok.com',
            'a/../b@nok.com',
            'a%b@nok.com',
            'a!b@nok.com',
            'a#b@nok.com',
            'a&b@nok.com',
            'a?b@nok.com',
            '@',
        ],
    )
    def test_bad_email(self, bad_email):
        with pytest.raises(ValidationError):
            EmailValidator()(bad_email)

    @pytest.mark.parametrize('good_email', ['ok@ok.com', 'a|b@ok.com'])
    def test_good_email(self, good_email):
        EmailValidator()(good_email)

    def test_validate_email_domain(self, settings):
        settings.A2_VALIDATE_EMAIL_DOMAIN = True
        with mock.patch('authentic2.validators.EmailValidator.query_mxs', return_value=[]) as query_mxs:
            with pytest.raises(ValidationError):
                EmailValidator()('ok@ok.com')
        assert query_mxs.call_count == 1
        with mock.patch('authentic2.validators.EmailValidator.query_mxs', return_value=['ok']) as query_mxs:
            EmailValidator()('ok@ok.com')
        assert query_mxs.call_count == 1

    @pytest.fixture
    def smtp(self):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        smtp = mock.Mock()
        smtp.helo.return_value = 250, None
        smtp.__enter__ = __enter__
        smtp.__exit__ = __exit__
        with mock.patch('smtplib.SMTP', return_value=smtp):
            yield smtp

    def test_rcpt_check(self, settings, smtp):
        settings.A2_VALIDATE_EMAIL_DOMAIN = True
        settings.A2_VALIDATE_EMAIL = True

        validator = EmailValidator(rcpt_check=True)

        with mock.patch('authentic2.validators.EmailValidator.query_mxs', return_value=['ok']):
            smtp.rcpt.return_value = 100, None
            validator('ok@ok.com')

            smtp.rcpt.return_value = 500, None
            with pytest.raises(ValidationError):
                validator('ok@ok.com')

            smtp.rcpt.return_value = 100, None
            smtp.rcpt.side_effect = smtplib.SMTPServerDisconnected
            validator('ok@ok.com')

            smtp.rcpt.return_value = 100, None
            smtp.connect.side_effect = smtplib.SMTPConnectError(1, 2)
            validator('ok@ok.com')

        assert smtp.connect.call_count == 4
        assert smtp.rcpt.call_count == 3

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


import pytest
from django.core.exceptions import ValidationError

from authentic2.forms.fields import PhoneField


def test_phonenumber_field(settings):
    settings.DEFAULT_COUNTRY_CODE = '33'
    field = PhoneField()

    assert field.help_text == (
        'Please select an international prefix and input your local number (the '
        'leading zero “0” for some local numbers may be removed or left as is).'
    )

    positive = [
        {'input': ['33', '01 01 01 01 01'], 'output': '+33101010101'},
        {'input': ['33', '0101010101'], 'output': '+33101010101'},
        {'input': ['33', '0666666666'], 'output': '+33666666666'},
        {'input': ['32', '081 00 0000'], 'output': '+3281000000'},
    ]
    # positive
    for value in positive:
        output = field.clean(value['input'])
        assert output == value['output']

    # negative
    for value in [
        ['33', '01a01'],
        ['33', '+01 01 01 01 01'],
        ['33', ' + 01/01.01-01.01'],
        ['33', '+01/01.01-01.01'],
        ['33', '01 01 01'],
        ['33', '01 01 01 010101'],
    ]:
        with pytest.raises(ValidationError):
            field.clean(value)

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

from authentic2.models import Token


def test_base(db):
    assert Token.objects.count() == 0
    token = Token.create('su', {'user_pk': 36})
    assert Token.objects.count() == 1
    assert Token.use('su', token.uuid, delete=False) == token
    assert Token.use('su', token.uuid.bytes, delete=False) == token
    assert Token.use('su', token.uuid.hex, delete=False) == token
    assert Token.use('su', token.uuid_b64url, delete=False) == token
    token2 = Token.use('su', str(token.uuid), delete=False)

    with pytest.raises(Token.DoesNotExist):
        Token.use('wtf', str(token.uuid))

    assert token2.content == {'user_pk': 36}
    Token.use('su', token.uuid)
    assert Token.objects.count() == 0
    with pytest.raises(Token.DoesNotExist):
        Token.use('su', token.uuid)


def test_default_expires(db, freezer):
    freezer.move_to('2020-01-01')
    assert Token.objects.count() == 0
    token = Token.create('su', {'user_pk': 36})
    Token.use('su', str(token.uuid), delete=False)
    freezer.tick(60)  # advance 60 seconds
    with pytest.raises(Token.DoesNotExist):
        Token.use('su', str(token.uuid), delete=False)


def test_default_integer_expires(db, freezer):
    freezer.move_to('2020-01-01')
    assert Token.objects.count() == 0
    token = Token.create('su', {'user_pk': 36}, duration=120)
    Token.use('su', str(token.uuid), delete=False)
    freezer.tick(60)  # advance 60 seconds
    Token.use('su', str(token.uuid), delete=False)
    freezer.tick(60)  # advance 60 seconds
    with pytest.raises(Token.DoesNotExist):
        Token.use('su', str(token.uuid), delete=False)


def test_cleanup(db, freezer):
    freezer.move_to('2020-01-01')
    Token.create('su', {'user_pk': 36})
    assert Token.objects.count() == 1
    Token.cleanup()
    assert Token.objects.count() == 1
    freezer.tick(60)  # advance 60 seconds
    Token.cleanup()
    assert Token.objects.count() == 0

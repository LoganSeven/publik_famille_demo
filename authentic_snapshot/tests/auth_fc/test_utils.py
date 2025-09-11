# authentic2 - authentic2 authentication for FranceConnect
# Copyright (C) 2024 Entr'ouvert
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

from authentic2_auth_fc.utils import resolve_insee_commune, resolve_insee_country, resolve_insee_territory


def test_resolve_insee_commune():
    assert resolve_insee_commune('02469') == 'MARLY-GOMONT'
    assert resolve_insee_commune('99412') == 'Unknown INSEE code'


def test_resolve_insee_country():
    assert resolve_insee_country('02469') == 'Unknown INSEE code'
    assert resolve_insee_country('99412') == 'NICARAGUA'


def test_resolve_insee_territory():
    assert resolve_insee_territory('02469') == 'MARLY-GOMONT'
    assert resolve_insee_territory('99412') == 'Foreign country or territory (NICARAGUA)'
    assert resolve_insee_territory('999999') == 'Unknown INSEE code'

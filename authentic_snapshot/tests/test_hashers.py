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

from django.contrib.auth.hashers import check_password

from authentic2 import hashers


def test_sha256_hasher():
    hasher = hashers.SHA256PasswordHasher()
    hashed = hasher.encode('admin', '')
    assert hasher.verify('admin', hashed)
    assert hashed == 'sha256$$8c6976e5b5410415bde908bd4dee15dfb167a9c873fc4bb8a81f6f2ab448a918'


def test_openldap_hashers():
    VECTORS = [
        x.split()
        for x in '''\
coin {SHA}NHj+acfc68FPYrMipEBZ3t8ABGY=
250523 {SHA}4zuJhPW1w0upqG7beAlxDcvtBj0=
coin {SSHA}zLPxfZ3RSNkIwVdHWEyB4Tpr6fT9LiVX
coin {SMD5}+x9QkU2T/wlPp6NK3bfYYxPYwaE=
coin {MD5}lqlRm4/d0X6MxLugQI///Q=='''.splitlines()
    ]
    for password, oldap_hash in VECTORS:
        dj_hash = hashers.olap_password_to_dj(oldap_hash)
        assert check_password(password, dj_hash)


def test_joomla_hasher():
    encoded = '8dd0adb5669160965fdd0291e1e03b92:uNkoculs9Y7zDaHtLBxVq71BuPP1fO5o'
    pwd = 'sournois'
    dj_encoded = hashers.JoomlaPasswordHasher.from_joomla(encoded)

    assert hashers.JoomlaPasswordHasher().verify(pwd, dj_encoded)
    assert hashers.JoomlaPasswordHasher.to_joomla(dj_encoded) == encoded


def test_plone_hasher():
    hasher = hashers.PloneSHA1PasswordHasher()
    assert hasher.verify('Azerty!123', 'plonesha1${SSHA}vS4g4MtzJyAjvhyW7vsrgjpJ6lDCU+Y42a6p')


def test_drupal_hasher():
    hasher = hashers.Drupal7PasswordHasher()
    encoded = '$S$Dynle.OzZaDw.KtHA3F81KvwnKFkFI3YPxe/q9ksun7HjrpEDy6N'
    pwd = 'Azerty!123'
    dj_encoded = hasher.from_drupal(encoded)

    assert hasher.verify(pwd, dj_encoded)
    assert hasher.to_drupal(dj_encoded) == encoded

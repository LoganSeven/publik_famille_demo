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

import os

import responses
from mellon.models import Issuer, UserSAMLIdentifier

from ..utils import login


def test_manager_user_sidebar(app, superuser, simple_user):
    login(app, superuser, '/manage/')
    response = app.get('/manage/users/%s/' % simple_user.id)
    assert 'SAML' not in response

    issuer1, _ = Issuer.objects.get_or_create(entity_id='https://idp1.com/')
    UserSAMLIdentifier.objects.create(user=simple_user, issuer=issuer1, name_id='1234')

    response = app.get('/manage/users/%s/' % simple_user.id)
    assert 'SAML' in response
    assert 'https://idp1.com/' in response
    assert '1234' in response


@responses.activate
def test_load_idp_metadata(app, superuser):
    login(app, superuser, '/manage/')
    response = app.get('/manage/authenticators/add/')
    response.form.set('authenticator', 'saml')
    response.form.set('name', 'SAML IDP')
    response = response.form.submit().follow()
    metadata_url = 'https://example.com/metadata.xml'
    response.form.set('metadata_url', metadata_url)
    response = response.form.submit()
    assert 'Metadata URL is unreachable' in response.text

    responses.get(metadata_url, body=b'', status=200)
    response = response.form.submit()
    assert 'Cannot parse metadata, no element found' in response.text

    with open(os.path.join(os.path.dirname(__file__), 'metadata.xml')) as metadata:
        metadata_content = metadata.read()
        responses.get(metadata_url, body=metadata_content, status=200)
        response = response.form.submit().follow()
        assert 'Metadata URL: https://example.com/metadata.xml' in response.text
        response = response.click('View metadata')
        assert response.text == metadata_content

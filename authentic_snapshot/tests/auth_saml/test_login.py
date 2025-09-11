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

from unittest import mock

import pytest
from django.core.files.base import ContentFile

from authentic2.apps.authenticators.models import LoginPasswordAuthenticator
from authentic2_auth_saml.adapters import AuthenticAdapter
from authentic2_auth_saml.models import SAMLAuthenticator


@pytest.fixture
def patched_adapter(monkeypatch):
    def load_idp(self, settings, order):
        settings['ENTITY_ID'] = 'idp1'
        return settings

    monkeypatch.setattr(AuthenticAdapter, 'load_idp', load_idp)


def test_saml_providers_on_login_page(db, app, settings):
    SAMLAuthenticator.objects.create(
        enabled=True,
        metadata='meta1.xml',
        slug='idp1',
        button_label='Test label',
        button_description='This is a test.',
    )

    response = app.get('/login/')
    assert response.pyquery('button[name="login-saml-idp1"]')
    assert not response.pyquery('button[name="login-saml-1"]')
    assert 'SAML' in response.text

    SAMLAuthenticator.objects.create(enabled=True, metadata='meta1.xml', slug='idp2')
    response = app.get('/login/')
    # two frontends should be present on login page
    assert response.pyquery('button[name="login-saml-idp1"]')
    assert response.pyquery('button[name="login-saml-idp2"]')
    assert 'Test label' in response.text
    assert 'This is a test.' in response.text


def test_saml_auth_button_image_and_label(app, db):
    authenticator = SAMLAuthenticator.objects.create(
        enabled=True, metadata='meta1.xml', slug='idp2', button_label='SAML label'
    )

    response = app.get('/login/')
    assert response.pyquery('.saml-login--form .buttons .submit-button').text() == 'SAML label'
    assert len(response.pyquery('.saml-login--form .buttons .submit-button-img')) == 0

    with open('tests/200x200.jpg', 'rb') as img:
        authenticator.button_image = ContentFile(img.read(), name='200x200.jpg')
    authenticator.button_label = 'saml alt'
    authenticator.save()

    response = app.get('/login/')
    assert response.pyquery('.saml-login--form .buttons .submit-button').text() == ''
    img_attr = response.pyquery('.saml-login--form .buttons .submit-button-img')[0].attrib
    assert img_attr['alt'] == 'saml alt'
    assert img_attr['src'].startswith('/media/authenticators/button_images/200x200')


def test_login_with_conditionnal_authenticators(db, app, settings, caplog):
    authenticator = SAMLAuthenticator.objects.create(enabled=True, metadata='xxx', slug='idp1')

    response = app.get('/login/')
    assert 'login-saml-idp1' in response

    authenticator.show_condition = 'remote_addr==\'0.0.0.0\''
    authenticator.save()
    response = app.get('/login/')
    assert 'login-saml-idp1' not in response

    authenticator2 = SAMLAuthenticator.objects.create(enabled=True, metadata='xxx', slug='idp2')
    response = app.get('/login/')
    assert 'login-saml-idp1' not in response
    assert 'login-saml-idp2' in response

    authenticator2.show_condition = 'remote_addr==\'0.0.0.0\''
    authenticator2.save()
    response = app.get('/login/')
    assert 'login-saml-idp1' not in response
    assert 'login-saml-idp2' not in response


def test_login_condition_dnsbl(db, app, settings, caplog):
    SAMLAuthenticator.objects.create(
        enabled=True,
        metadata='xxx',
        slug='idp1',
        show_condition='remote_addr in dnsbl(\'dnswl.example.com\')',
    )
    SAMLAuthenticator.objects.create(
        enabled=True,
        metadata='xxx',
        slug='idp2',
        show_condition='remote_addr not in dnsbl(\'dnswl.example.com\')',
    )
    with mock.patch('authentic2.utils.evaluate.check_dnsbl', return_value=True):
        response = app.get('/login/')
    assert 'login-saml-idp1' in response
    assert 'login-saml-idp2' not in response


def test_login_autorun(db, app, settings, patched_adapter):
    response = app.get('/login/')

    authenticator = SAMLAuthenticator.objects.create(enabled=True, metadata='xxx', slug='idp1')
    # hide password block
    LoginPasswordAuthenticator.objects.update_or_create(
        slug='password-authenticator', defaults={'enabled': False}
    )
    response = app.get('/login/', status=302)
    assert '/accounts/saml/login/?entityID=' in response['Location']

    authenticator.slug = 'slug_with_underscore'
    authenticator.save()
    response = app.get('/login/', status=302)
    assert '/accounts/saml/login/?entityID=' in response['Location']

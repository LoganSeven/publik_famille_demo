# authentic2 - versatile identity manager
# Copyright (C) 2010-2021 Entr'ouvert
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

import base64
from importlib import import_module

import pytest
from django.urls import reverse

from authentic2.a2_rbac.utils import get_default_ou
from authentic2.models import Attribute
from authentic2_idp_oidc import app_settings
from authentic2_idp_oidc.models import OIDCClaim, OIDCClient
from tests import utils

JWKSET = {
    'keys': [
        {
            'qi': 'h_zifVD-ChelxZUVxhICNcgGkQz26b-EdIlLY9rN7SX_aD3sLI_JHEHV4Bz3kV5eW8O4qJ8SHhfUdHGK-'
            'gRH7FVOGoXnXACf47QoXowHzsPLL64wCuZENTl7hIRGLY-BInULkfTQfuiVSMoxPjsVNTMBzMiz0bNjMQyMyvW5xH4',
            'kty': 'RSA',
            'd': 'pUcL4-LDBy3rqJWip269h5Hd6nLvqjXltfkVe_mL-LwZPHmCrUaj_SX54SnCY3Wyf7kxhoMYUac62lQ71923uJPFFdiavAujbNrtZPq32i4C-'
            '1apWXW8OGJr8VoVDqalxj9SAq1G54wbbsaAPrZdyuqy-esNxDqDigfbM-cWgngBBYo5CSsfnmnd05N2cUS26L7QzWbNHwilnBTE9e_J7rK3xUCDKrobv6_LiI-'
            'AhMmBHJSrCxjexh0wzfBi_Ntj9BGCcPThDjG8SQvaV-aLNdLfIy2XO3i076RLBB6Hm_yHuAparrwp-pPE48eQdiYjrSAFalz4ojWQ3_ByLA6uAQ',
            'q': '2FvfeWnIlWNUipan7DIBlJrmz5EinJNxrQ-BNwPHrAoIM8qvyC7jPy09YxZs5Y9CMMZSal6C4Nm2LHBFxHU9z1qd5'
            'XDzbk19G-y1lDqZizVXr876TpiAjuq03rcoMQm8dQru_pVjUdgxR64vKyJ9CaFMAqcpZeEMIqAvzhQG8uE',
            'dp': 'Kg4HPGpzenhK2ser6nfM1Yt-pkqBbWQotvqsxGptECXpbN7vweupvL5kJPeRrbsXKp9QE7DXTN1sG9puJxMSwtgiv'
            '4hr9Va9e9WOC6PMd2VY7tgw5uKMpPLMc5y82PusRhBoRh0SUUsjyQxK9PGtWYnGZXbAoaIYPdMyDlosfqU',
            'dq': 'QuUNEHYTjZTbo8n2-4FumarXKGBAalbwM8jyc7cYemnTpWfKt8M_gd4T99oMK2IC3h_DhZ3ZK3pE6DKCb76sMLtczH8C1RziTMsATWdc5_zDMtl07O4b-'
            'ZQ5_g51P8w515pc0JwRzFFi0z3Y2aZdMKgNX1id5SES5nXOshHhICE',
            'n': '0lN6CiJGFD8BSPV_azLoEl6Nq-WlHkU743D5rqvzw1sOaxstMGxAhVk2YIhWwfvapV6XjO_yvc4778VBTELOdjRw6BGUdBJepdwkL__TPyjEVhqMQj9MKhE'
            'U4GUy9w0Lsilb5D01kfrOKpmdcYw4jhcDvb0H4-LZgh1Vk84vF4WaQCUg_AX4drVDQOjoU8kuWIM8gz9w6zEsbIw-gtMRpFwS8ncA0zDX5VfyC77iMxzFftDIP2g'
            'M5GvdevMzvP9IRkRRBhP9vV4JchBFPHSA9OPJcnySjJJNW6aAJn6P6JasN1z68khjufM09J8UzmLAZYOq7gUG95Ox1KsV-g337Q',
            'e': 'AQAB',
            'p': '-Nyj_Sw3f2HUqSssCZv84y7b3blOtGGAhfYN_JtGfcTQv2bOtxrIUzeonCi-Z_1W4hO10tqxJcOB0ibtDqkDlLhnLaIYOBfriITRFK83EJG5sC-'
            '0KTmFzUXFTA2aMc1QgP-Fu6gUfQpPqLgWxhx8EFhkBlBZshKU5-C-385Sco0',
            'kid': '46c686ea-7d4e-41cd-a462-2125fc1dee0e',
        },
        {
            'kty': 'EC',
            'd': 'wwULaR9UYWZW6U2oEbkz3sO1lhPSj6DyA6e7PiUfhog',
            'use': 'sig',
            'crv': 'P-256',
            'x': 'HZMHZkX-63heqA5pvWn-UR7bgcXZNEcQa5wfvG_BzTw',
            'y': 'SUCuwjjiyKvGq5Odr0sjDqjha_CBqks0JQFrR7Ei5OQ',
            'alg': 'ES256',
            'kid': 'ac85baf4-835b-49b2-8272-ffecce7654c9',
        },
    ]
}


@pytest.fixture
def jwkset():
    return JWKSET


@pytest.fixture
def oidc_settings(settings, jwkset):
    settings.A2_IDP_OIDC_JWKSET = jwkset
    settings.A2_IDP_OIDC_PASSWORD_GRANT_RATELIMIT = '100/m'
    return settings


@pytest.fixture
def profile_settings(settings, jwkset):
    settings.A2_IDP_OIDC_JWKSET = jwkset
    settings.A2_IDP_OIDC_PASSWORD_GRANT_RATELIMIT = '100/m'
    return settings


def make_client(app, params=None):
    Attribute.objects.get_or_create(
        name='cityscape_image',
        defaults=dict(
            label='cityscape',
            kind='profile_image',
            asked_on_registration=True,
            required=False,
            user_visible=True,
            user_editable=True,
        ),
    )

    client = OIDCClient(
        name='oidcclient',
        slug='oidcclient',
        client_id='1234',
        ou=get_default_ou(),
        unauthorized_url='https://example.com/southpark/',
        redirect_uris='https://example.com/callbac%C3%A9',
    )

    for key, value in (params or {}).items():
        setattr(client, key, value)
    client.save()
    for mapping in app_settings.DEFAULT_MAPPINGS:
        OIDCClaim.objects.create(
            client=client, name=mapping['name'], value=mapping['value'], scopes=mapping['scopes']
        )
    return client


@pytest.fixture(name='make_client')
def make_client_fixture():
    return make_client


@pytest.fixture
def client(app, superuser):
    return make_client(app)


@pytest.fixture
def simple_oidc_client(db):
    return OIDCClient.objects.create(
        name='client', slug='client', ou=get_default_ou(), redirect_uris='https://example.com/'
    )


@pytest.fixture
def oidc_client(request, superuser, app, simple_user, oidc_settings):
    return make_client(app, getattr(request, 'param', None) or {})


@pytest.fixture
def normal_oidc_client(superuser, app, simple_user):
    url = reverse('a2-manager-add-oidc-service')
    assert OIDCClient.objects.count() == 0
    response = utils.login(app, superuser, path=url)
    response.form.set('name', 'oidcclient')
    response.form.set('ou', get_default_ou().pk)
    response.form.set('unauthorized_url', 'https://example.com/southpark/')
    response.form.set('redirect_uris', 'https://example.com/callbac%C3%A9')
    response = response.form.submit().follow()
    assert OIDCClient.objects.count() == 1
    client = OIDCClient.objects.get()
    utils.logout(app)
    return client


@pytest.fixture
def session(settings, db, simple_user):
    engine = import_module(settings.SESSION_ENGINE)
    session = engine.SessionStore()
    session['_auth_user_id'] = str(simple_user.id)
    session.create()
    return session


def client_authentication_headers(oidc_client):
    client_creds = '%s:%s' % (oidc_client.client_id, oidc_client.client_secret)
    token = base64.b64encode(client_creds.encode('ascii'))
    return {'Authorization': 'Basic %s' % str(token.decode('ascii'))}


def bearer_authentication_headers(access_token):
    return {'Authorization': 'Bearer %s' % str(access_token)}


@pytest.fixture
def rp_app(app_factory):
    '''Webtest app to use for calls from the RP, like HTTP Post to the token endpoint.'''
    return app_factory()

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


from urllib.parse import urlparse

import responses

from authentic2.custom_user.models import User
from authentic2.models import Attribute
from authentic2.saml.models import LibertyProvider

from . import utils


def test_user_admin(db, app, superuser):
    utils.login(app, superuser)
    Attribute.objects.create(
        label='SIRET',
        name='siret',
        kind='string',
        required=False,
        user_visible=True,
        user_editable=False,
        asked_on_registration=False,
        multiple=False,
    )
    Attribute.objects.create(
        label='Civilité',
        name='civilite',
        kind='title',
        required=False,
        user_visible=True,
        user_editable=True,
        asked_on_registration=True,
        multiple=False,
    )

    superuser.verified_attributes.first_name = 'John'
    superuser.verified_attributes.last_name = 'Doe'

    resp = app.get('/admin/custom_user/user/%s/' % superuser.pk).maybe_follow()
    form = resp.forms['user_form']
    assert set(form.fields.keys()) >= {
        'username',
        'first_name',
        'last_name',
        'civilite',
        'siret',
        'is_staff',
        'is_superuser',
        'ou',
        'groups',
        'date_joined_0',
        'date_joined_1',
        'last_login_0',
        'last_login_1',
    }
    form.set('first_name', 'John')
    form.set('last_name', 'Doe')
    form.set('civilite', 'Mr')
    form.set('siret', '1234')
    resp = form.submit('_continue').follow()
    modified_admin = User.objects.get(pk=superuser.pk)
    assert modified_admin.first_name == 'John'
    assert modified_admin.last_name == 'Doe'
    assert modified_admin.attributes.civilite == 'Mr'
    assert modified_admin.attributes.siret == '1234'


def test_attributes_admin(db, app, superuser):
    utils.login(app, superuser)
    resp = app.get('/admin/authentic2/attribute/')
    resp = resp.click('First name')


def test_app_setting_login_url(app, db, settings):
    settings.A2_MANAGER_LOGIN_URL = '/other-login/'
    response = app.get('/admin/')
    assert urlparse(response['Location']).path == '/admin/login/'
    response = response.follow()
    assert urlparse(response['Location']).path == settings.A2_MANAGER_LOGIN_URL
    assert urlparse(response['Location']).query == 'next=/admin/'


@responses.activate
def test_saml_libertyprovider_add_from_url(db, app, superuser):
    utils.login(app, superuser)
    resp = app.get(
        '/admin/saml/libertyprovider/add-from-url/?entity_id=http%3A%2F%2F127.0.0.1%3A8003%2Faccounts%2Fmellon%2Fmetadata%2F'
    )
    # in URL : entity_id = http://127.0.0.1:8003/accounts/mellon/metadata/
    resp.form.set('name', 'Some SAML client')
    resp.form.set('slug', 'some-saml-client')

    metadata = '''<EntityDescriptor entityID="http://127.0.0.1:8003/accounts/mellon/metadata/" xmlns="urn:oasis:names:tc:SAML:2.0:metadata">
 <SPSSODescriptor AuthnRequestsSigned="true" WantAssertionsSigned="true" protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
       <KeyDescriptor>
           <ds:KeyInfo xmlns:ds="http://www.w3.org/2000/09/xmldsig#">
               <ds:X509Data>
                   <ds:X509Certificate>MIICPTCCAaagAwIBAgIJALokqqFKWl7+MA0GCSqGSIb3DQEBCwUAMDYxNDAyBgNVBAMMK2Nvbm5leGlvbi1wYXJpc25hbnRlcnJlLnRlc3QuZW50cm91dmVydC5vcmcwHhcNMTkwNDE2MTE1NDQxWhcNMjkwNDE1MTE1NDQxWjA2MTQwMgYDVQQDDCtjb25uZXhpb24tcGFyaXNuYW50ZXJyZS50ZXN0LmVudHJvdXZlcnQub3JnMIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQD5ztXvBzQDQm2Ckfm4hk5J0OczQZmCoxLiI1zi7PuBEeaMxrSrH8pdv1kxsnToPILrA8kR1855wny98BQjmWsDZ9/UWst1TVHmoZmo811Zu2ucWl34nBlSjNDwNna9VCL4uFC9C0Oza2AQU7B45E//3PlihV2hAYhtzm5XACh9kQIDAQABo1MwUTAdBgNVHQ4EFgQU49GRX35TqEpcTZGdNIwOO3k5eNcwHwYDVR0jBBgwFoAU49GRX35TqEpcTZGdNIwOO3k5eNcwDwYDVR0TAQH/BAUwAwEB/zANBgkqhkiG9w0BAQsFAAOBgQC6bLxzOSKa76+6KS2pUb4I35VG9Sku2FlffZsM0jyJqfhroXWEYxduIZbjamGSOo5UoZuiBwaWof6QHcy34zuJolw1upKxjxPALSCgGfRcxbuk4yN3CroRKmeDvy1rHzVcfC1PXip3DVup/qUu81cnTA/ENRgnOwThgiZ4Ip2ZHg==</ds:X509Certificate>
               </ds:X509Data>
           </ds:KeyInfo>
       </KeyDescriptor>
   <SingleLogoutService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="http://127.0.0.1:8003/accounts/mellon/logout/"/>
   <AssertionConsumerService index="0" Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Artifact" Location="http://127.0.0.1:8003/accounts/mellon/login/"/>
   <AssertionConsumerService index="1" isDefault="true" Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST" Location="http://127.0.0.1:8003/accounts/mellon/login/"/>
 </SPSSODescriptor>
</EntityDescriptor>'''

    responses.get(
        'http://127.0.0.1:8003/accounts/mellon/metadata/',
        status=200,
        content_type='text/xml',
        body=metadata.encode('utf-8'),
    )
    resp = resp.form.submit('_continue').follow()
    form = resp.forms['libertyprovider_form']
    assert form.get('metadata_0').value == metadata
    resp = form.submit('_continue').follow()
    liberty_provider = LibertyProvider.objects.get(slug='some-saml-client')
    assert liberty_provider.metadata == metadata

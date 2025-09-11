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

import random
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.utils.timezone import now

from authentic2.a2_rbac.models import OrganizationalUnit as OU
from authentic2.a2_rbac.utils import get_default_ou
from authentic2.custom_user.models import User
from authentic2_idp_oidc.models import OIDCAuthorization, OIDCClient
from authentic2_idp_oidc.utils import make_sub


def test_api_synchronization(app, oidc_client):
    oidc_client.has_api_access = True
    oidc_client.save()
    users = [User.objects.create(username='user-%s' % i) for i in range(10)]
    for user in users[5:]:
        user.delete()
    deleted_subs = {make_sub(oidc_client, user) for user in users[5:]}

    app.authorization = ('Basic', (oidc_client.client_id, oidc_client.client_secret))
    status = 200
    if oidc_client.identifier_policy not in (OIDCClient.POLICY_PAIRWISE_REVERSIBLE, OIDCClient.POLICY_UUID):
        status = 401
    response = app.post_json(
        '/api/users/synchronization/',
        params={'known_uuids': [make_sub(oidc_client, user) for user in users]},
        status=status,
    )
    if status == 200:
        assert response.json['result'] == 1
        assert set(response.json['unknown_uuids']) == deleted_subs


def test_api_users_list_queryset_reduction(app, oidc_client):
    oidc_client.has_api_access = True
    oidc_client.identifier_policy = OIDCClient.POLICY_PAIRWISE_REVERSIBLE
    oidc_client.save()

    pre_modification = now().strftime('%Y-%m-%dT%H:%M:%S')

    users = [User.objects.create(username=f'user-{i}', last_name=f'Name-{i}') for i in range(20)]
    expired = now() + timedelta(hours=1)
    for user in random.sample(users, k=5):
        OIDCAuthorization.objects.create(
            client_id=oidc_client.id,
            client_ct=ContentType.objects.get_for_model(OIDCClient),
            user=user,
            expired=expired,
        )

    app.authorization = ('Basic', (oidc_client.client_id, oidc_client.client_secret))
    url = f'/api/users/?modified__gt={pre_modification}&claim_resolution'

    response = app.get(url, status=200)

    assert len(response.json['results']) == 5

    oidc_client.authorization_mode = OIDCClient.AUTHORIZATION_MODE_NONE
    oidc_client.save()
    response = app.get(url, status=200)

    assert len(response.json['results']) == User.objects.count()

    oidc_client.authorization_mode = OIDCClient.AUTHORIZATION_MODE_BY_OU
    oidc_client.ou = get_default_ou()
    oidc_client.save()
    response = app.get(url, status=200)

    assert not response.json['results']

    for user in random.sample(users, k=8):
        OIDCAuthorization.objects.create(
            client_id=get_default_ou().id,
            client_ct=ContentType.objects.get_for_model(OU),
            user=user,
            expired=expired,
        )
    response = app.get(url, status=200)

    assert len(response.json['results']) == 8

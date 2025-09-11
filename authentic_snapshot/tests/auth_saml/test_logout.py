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

import lasso
from mellon.models import SessionIndex, UserSAMLIdentifier
from mellon.models_utils import get_issuer

from ..utils import login

ISSUER = 'http://idp5/metadata'


def test_redirect_logout(app, idp, simple_user):
    response = login(app, simple_user, '/accounts/')

    session = app.session

    # simulate mellon state after login
    session['mellon_session'] = {'issuer': ISSUER}
    session.save()

    issuer = get_issuer(ISSUER)
    usi = UserSAMLIdentifier.objects.create(
        user=simple_user,
        issuer=issuer,
        name_id='1234',
        nid_format=lasso.SAML2_NAME_IDENTIFIER_FORMAT_PERSISTENT,
    )
    SessionIndex.objects.create(saml_identifier=usi, session_key=session.session_key, session_index='abcd')

    response = response.click('Logout')
    assert response.location.startswith('/accounts/saml/logout/?token')
    response = app.get('/accounts/saml/logout/?SAMLResponse=coin')
    assert response.location == '/logout/'

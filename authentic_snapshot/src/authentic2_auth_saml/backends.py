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

from mellon.backends import SAMLBackend as BaseSAMLBackend

from authentic2.middleware import StoreRequestMiddleware


class SAMLBackend(BaseSAMLBackend):
    def authenticate(self, request=None, **kwargs):
        from .models import SAMLAuthenticator

        if not SAMLAuthenticator.objects.filter(enabled=True).exists():
            return None
        return super().authenticate(request=request, **kwargs)

    def get_saml2_authn_context(self):
        # Pass AuthnContextClassRef from the previous IdP
        request = StoreRequestMiddleware.get_request()
        if request:
            authn_context_class_ref = request.session.get('mellon_session', {}).get('authn_context_class_ref')
            if authn_context_class_ref:
                return authn_context_class_ref

        import lasso

        return lasso.SAML2_AUTHN_CONTEXT_PREVIOUS_SESSION

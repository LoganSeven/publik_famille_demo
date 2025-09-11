# authentic2 - versatile identity manager
# Copyright (C) 2010-2023 Entr'ouvert
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


from django.apps import AppConfig


class Plugin:
    def check_origin(self, request, origin):
        from authentic2.cors import make_origin
        from authentic2.saml.models import LibertySession

        for session in LibertySession.objects.filter(django_session_key=request.session.session_key):
            provider_origin = make_origin(session.provider_id)
            if origin == provider_origin:
                return True


class SAML2IdPConfig(AppConfig):
    name = 'authentic2.idp.saml'
    label = 'authentic2_idp_saml'

    def get_a2_plugin(self):
        return Plugin()

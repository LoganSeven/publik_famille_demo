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

from django.urls import path, re_path

from authentic2.idp.saml.saml2_endpoints import (
    artifact,
    continue_sso,
    finish_slo,
    idp_slo,
    idp_sso,
    metadata,
    slo,
    slo_return,
    slo_soap,
    sso,
)

urlpatterns = [
    path('metadata', metadata, name='a2-idp-saml-metadata'),
    path('sso', sso, name='a2-idp-saml-sso'),
    path('continue', continue_sso, name='a2-idp-saml-continue'),
    path('slo', slo, name='a2-idp-saml-slo'),
    path('slo/soap', slo_soap, name='a2-idp-saml-slo-soap'),
    re_path(r'^idp_slo/(.*)$', idp_slo, name='a2-idp-saml-slo-idp'),
    path('slo_return', slo_return, name='a2-idp-saml-slo-return'),
    path('finish_slo', finish_slo, name='a2-idp-saml-finish-slo'),
    path('artifact', artifact, name='a2-idp-saml-artifact'),
    # legacy endpoint, now it's prefered to pass the entity_id in a parameter
    re_path(r'^idp_sso/(.+)$', idp_sso, name='a2-idp-saml-idp-sso-named'),
    path('idp_sso/', idp_sso, name='a2-idp-saml2-idp-sso'),
]

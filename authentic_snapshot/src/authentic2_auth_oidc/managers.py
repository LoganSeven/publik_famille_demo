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

from django.db.models.query import QuerySet


class OIDCProviderQuerySet(QuerySet):
    def get_by_natural_key(self, issuer):
        return self.get(issuer=issuer)


OIDCProviderManager = OIDCProviderQuerySet.as_manager


class OIDCClaimMappingQuerySet(QuerySet):
    def get_by_natural_key(self, claim, attribute, verified, required):
        return self.get(claim=claim, attribute=attribute, verified=verified, required=required)


OIDCClaimMappingManager = OIDCClaimMappingQuerySet.as_manager

# authentic2 - versatile identity manager
# Copyright (C) 2010-2020 Entr'ouvert
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

from authentic2.custom_user.models import User
from authentic2.models import Token
from authentic2.utils.misc import make_url


def build_url(user, duration=30):
    token = Token.create('su', {'user_pk': user.pk}, duration=duration)
    return make_url('su', kwargs={'uuid': token.uuid_b64url})


def resolve_token(uuid):
    try:
        token = Token.use('su', uuid)
    except (ValueError, TypeError, Token.DoesNotExist):
        return None

    try:
        return User.objects.get(pk=token.content['user_pk'])
    except User.DoesNotExist:
        return None

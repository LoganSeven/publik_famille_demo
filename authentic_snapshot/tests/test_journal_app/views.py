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


from django.contrib.auth import authenticate, get_user_model, login
from django.http import HttpResponse

from authentic2.apps.journal.journal import journal

User = get_user_model()


def login_view(request, name):
    user = User.objects.create(username=name)
    user.set_password('coin')
    user.save()
    user = authenticate(username=name, password='coin')
    login(request, user)
    journal.record('login', user=user, session=request.session)
    return HttpResponse('logged in', content_type='text/plain')

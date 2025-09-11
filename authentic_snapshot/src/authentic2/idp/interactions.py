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

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseRedirect
from django.shortcuts import render


@login_required
def consent_federation(request, nonce='', provider_id=None):
    """On a GET produce a form asking for consentment,
    On a POST handle the form and redirect to next"""
    if request.method == 'GET':
        return render(
            request,
            'interaction/consent_federation.html',
            {
                'provider_id': request.GET.get('provider_id', ''),
                'nonce': request.GET.get('nonce', ''),
                'next': request.GET.get('next', ''),
            },
        )
    else:
        next_url = '/'
        if 'next' in request.POST:
            next_url = request.POST['next']
        if 'accept' in request.POST:
            next_url = next_url + '&consent_answer=accepted'
            return HttpResponseRedirect(next_url)
        else:
            next_url = next_url + '&consent_answer=refused'
            return HttpResponseRedirect(next)

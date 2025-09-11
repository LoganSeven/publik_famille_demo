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

from unittest import mock

from . import utils


def test_home(app, settings, simple_user, service):
    from authentic2.a2_rbac.utils import get_default_ou
    from authentic2.models import Service

    utils.set_service(app, service)

    settings.A2_HOMEPAGE_URL = 'https://portal1/'

    resp = app.get('/login/')
    body = resp.pyquery('body')
    assert body.attr('data-home-url') == 'https://portal1/'
    assert body.attr('data-home-service-slug') == service.slug
    assert body.attr('data-home-service-name') == service.name
    assert body.attr('data-home-ou-slug') == service.ou.slug
    assert body.attr('data-home-ou-name') == service.ou.name

    service.ou.home_url = 'https://portal2/'
    service.ou.save()
    resp = app.get('/login/')
    body = resp.pyquery('body')
    assert body.attr('data-home-url') == 'https://portal2/'

    # if user comes back from a different service, the information is updated
    new_service = Service.objects.create(
        ou=get_default_ou(), slug='service2', name='Service2', home_url='https://portal3/'
    )
    utils.set_service(app, new_service)

    resp = app.get('/login/')
    body = resp.pyquery('body')
    assert body.attr('data-home-url') == 'https://portal3/'
    assert body.attr('data-home-service-slug') == new_service.slug
    assert body.attr('data-home-service-name') == new_service.name
    assert body.attr('data-home-ou-slug') == new_service.ou.slug
    assert body.attr('data-home-ou-name') == new_service.ou.name


@mock.patch('requests.Session.get')
def test_constant_aliases(request):
    from django.template import RequestContext, Template

    context = RequestContext(request)
    t_true = Template('{% if var == true %}OK{% endif %}')
    t_false = Template('{% if var == false %}OK{% endif %}')
    t_null = Template('{% if var == null %}OK{% endif %}')
    t_is_true = Template('{% if var is true %}OK{% endif %}')
    t_is_false = Template('{% if var is false %}OK{% endif %}')
    t_is_null = Template('{% if var is null %}OK{% endif %}')

    context.update({'var': True})
    assert t_true.render(context) == 'OK'
    assert t_false.render(context) == ''
    assert t_null.render(context) == ''
    assert t_is_true.render(context) == 'OK'
    assert t_is_false.render(context) == ''
    assert t_is_null.render(context) == ''

    context.update({'var': False})
    assert t_true.render(context) == ''
    assert t_false.render(context) == 'OK'
    assert t_null.render(context) == ''
    assert t_is_true.render(context) == ''
    assert t_is_false.render(context) == 'OK'
    assert t_is_null.render(context) == ''

    context.update({'var': None})
    assert t_true.render(context) == ''
    assert t_false.render(context) == ''
    assert t_null.render(context) == 'OK'
    assert t_is_true.render(context) == ''
    assert t_is_false.render(context) == ''
    assert t_is_null.render(context) == 'OK'

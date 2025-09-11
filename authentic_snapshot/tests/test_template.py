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

import pytest
import responses
from django.test.client import RequestFactory
from publik_django_templatetags.wcs.context_processors import Cards

from authentic2.utils import misc as utils_misc
from authentic2.utils.template import Template, TemplateError

pytestmark = pytest.mark.django_db


def test_render_template():
    # 1.1. test a simple conditional
    value = '{% if foo %}John{% else %}Jim{% endif %}'
    template = Template(value=value)

    context = {'foo': True}
    assert template.render(context=context) == 'John'

    context = {'foo': False}
    assert template.render(context=context) == 'Jim'

    context = {}
    assert template.render(context=context) == 'Jim'

    # 1.2. test comparison operators
    value = '{% if foo > bar %}Foo{% else %}Bar{% endif %}'
    template = Template(value=value)

    context = {'foo': 3, 'bar': 2}
    assert template.render(context=context) == 'Foo'

    context = {'foo': 3, 'bar': 9}
    assert template.render(context=context) == 'Bar'

    # 1.3. test set operator
    value = '{% if foo in bar %}Found{% else %}Missing{% endif %}'
    template = Template(value=value)
    context = {'foo': 'john', 'bar': ['miles', 'john', 'red', 'paul', 'philly joe']}
    assert template.render(context=context) == 'Found'

    context['bar'] = ['miles', 'wayne', 'herbie', 'ron', 'tony']
    assert template.render(context=context) == 'Missing'

    # 2. test 'add' builtin filter
    tmpl1 = Template(value='{{ base|add:suffix }}')
    tmpl2 = Template(value='{{ prefix|add:base }}')
    context = {
        'prefix': 'Mister ',
        'base': 'Tony',
        'suffix': ' Jr.',
    }

    assert tmpl1.render(context=context) == 'Tony Jr.'
    assert tmpl2.render(context=context) == 'Mister Tony'

    # 3. test 'with' tag
    value = '{% with name=user.name age=user.age %}{{ name }}: {{ age }}{% endwith %}'
    template = Template(value=value)

    context = {'user': {'name': 'Robert', 'age': 39}}
    assert template.render(context=context) == 'Robert: 39'

    # 4. test 'firstof' tag
    value = '{% firstof name surname nickname %}'
    template = Template(value=value)

    context = {'surname': 'Smith', 'nickname': 'Mitch'}
    assert template.render(context=context) == 'Smith'

    context.pop('surname')
    assert template.render(context=context) == 'Mitch'


def test_render_template_syntax_error():
    value = '{% if foo %}John{% else %}Jim{% endif %'  # oops
    template = Template(value=value)

    context = {'foo': True}
    assert template.render(context=context) == value

    with pytest.raises(TemplateError) as raised:
        template = Template(value=value, raises=True)
        assert 'template syntax error' in raised


def test_render_template_missing_variable():
    value = '{{ foo|add:bar }}'
    template = Template(value=value)

    context = {'foo': True}
    assert template.render(context=context) == value

    # missing variable errors happen at render time with a particular context
    template = Template(value=value, raises=True)
    with pytest.raises(TemplateError) as raised:
        template.render(context=context)
        assert 'missing template variable' in raised


def test_registration_with_custom_titles(app, fc):
    url = utils_misc.make_url('registration_register')
    response = app.get(url)
    assert '<h2>Registration</h2>' not in response.text
    assert '<h2>Register with Password</h2>' in response.text
    assert '<h2>Register with FC</h2>' in response.text


def test_login_with_custom_titles(app, fc):
    url = utils_misc.make_url('auth_login')
    response = app.get(url)
    assert '<h2>Log in with Password</h2>' in response.text
    assert '<h2>Log in with FC</h2>' in response.text


@pytest.fixture
def context():
    return {
        'cards': Cards(),
        'request': RequestFactory().get('/'),
    }


def test_render_publik_django_templatetags_no_context():
    value = '{{ cards|objects:"foo"|count }}'
    template = Template(value=value)

    assert template.render(context=None) == '0'


@responses.activate
def test_render_publik_django_templatetags(context, nocache):
    data = [{'id': 1, 'fields': {'foo': 'bar'}}, {'id': 2, 'fields': {'foo': 'baz'}}]
    responses.get('http://example.org/api/cards/foo/list', json={'count': 2, 'data': data})

    value = '{{ cards|objects:"foo"|count }}'
    template = Template(value=value)

    assert template.render(context=context) == '2'

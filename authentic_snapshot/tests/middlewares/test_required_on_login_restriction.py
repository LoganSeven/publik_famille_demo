# authentic2 - versatile identity manager
# Copyright (C) 2021 Entr'ouvert
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

from ..utils import login


def test_simple(app_factory, db, simple_user, cgu_attribute, settings):
    app = app_factory('example.com')
    settings.A2_OPENED_SESSION_COOKIE_DOMAIN = 'example.com'
    settings.ALLOWED_HOSTS = ['example.com']

    resp = login(app, simple_user, path='/accounts/')
    assert resp.location == '/accounts/edit/required/?next=/accounts/'
    assert 'A2_OPENED_SESSION' not in app.cookies
    resp = resp.follow()
    assert 'A2_OPENED_SESSION' not in app.cookies
    resp.form.set('cgu_2021', True)
    resp = resp.form.submit()
    assert 'A2_OPENED_SESSION' not in app.cookies
    assert resp.location == '/accounts/'
    resp = resp.follow()
    assert 'A2_OPENED_SESSION' in app.cookies
    assert 'les conditions générales d\'utilisation\xa0:\nTrue' in resp.pyquery.text()


def test_superuser(app_factory, db, cgu_attribute, settings, superuser):
    app = app_factory('example.com')
    settings.A2_OPENED_SESSION_COOKIE_DOMAIN = 'example.com'
    settings.ALLOWED_HOSTS = ['example.com']

    resp = login(app, superuser, path='/accounts/')
    assert 'Your account' in resp.text


def test_check_disabled_at_ou_level(app_factory, db, cgu_attribute, settings, simple_user):
    app = app_factory('example.com')
    settings.A2_OPENED_SESSION_COOKIE_DOMAIN = 'example.com'
    settings.ALLOWED_HOSTS = ['example.com']

    simple_user.ou.check_required_on_login_attributes = False
    simple_user.ou.save()

    resp = login(app, simple_user, path='/accounts/')
    assert 'Your account' in resp.text

# w.c.s. - web application for online forms
# Copyright (C) 2005-2010  Entr'ouvert
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

from quixote import get_publisher

from . import base


def login(method):
    m = get_publisher().ident_methods.get(method)
    if m and hasattr(m, 'login'):
        return m().login()
    return get_method_directory(method).login()


def register(method):
    return get_method_directory(method).register()


def get_method_directory(method):
    m = get_publisher().ident_methods.get(method)
    if not m:
        raise KeyError
    return m.method_directory()


def get_method_admin_directory(method):
    return get_publisher().ident_methods.get(method).method_admin_directory()


def get_method_user_directory(method, user):
    try:
        return get_publisher().ident_methods.get(method).method_user_directory(user)
    except (AttributeError, NotImplementedError, base.NoSuchMethodForUserError):
        return None


def get_method_classes():
    return get_publisher().ident_methods.values()

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

from django.utils.encoding import force_str
from django.utils.functional import lazy
from quixote import get_publisher

try:
    import lasso
except ImportError:
    lasso = None


def audit(action, **kwargs):
    from wcs.audit import Audit

    Audit.record(action, **kwargs)


def gettext(message):
    pub = get_publisher()
    if pub is None:
        return message
    return force_str(pub.gettext(str(message)))


def ngettext(*args):
    pub = get_publisher()
    if pub is None:
        return args[0]
    return force_str(pub.ngettext(*args))


def pgettext(*args):
    pub = get_publisher()
    if pub is None:
        return args[1]
    return force_str(pub.pgettext(*args))


def N_(x):
    return x


_ = lazy(gettext, str)
pgettext_lazy = lazy(pgettext, str)

from . import publisher  # noqa pylint: disable=wrong-import-position
from .publisher import get_cfg  # noqa pylint: disable=wrong-import-position
from .publisher import get_logger  # noqa pylint: disable=wrong-import-position
from .publisher import get_publisher_class  # noqa pylint: disable=wrong-import-position

publisher._ = _

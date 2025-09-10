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

import os

from quixote import get_publisher

from .. import _, audit, get_cfg


def hobo_kwargs(**kwargs):
    if os.path.exists(os.path.join(get_publisher().tenant.directory, 'hobo.json')):
        kwargs.update({'readonly': True, 'hint': _('This setting is locked-down by deployment.')})
    return kwargs


def cfg_submit(form, cfg_key, fields):
    get_publisher().reload_cfg()
    cfg_key = str(cfg_key)
    cfg_dict = get_cfg(cfg_key, {})
    for k in fields:
        widget = form.get_widget(k)
        if widget:
            cfg_dict[str(k)] = widget.parse()
    get_publisher().cfg[cfg_key] = cfg_dict
    audit('settings', cfg_key=cfg_key)
    get_publisher().write_cfg()

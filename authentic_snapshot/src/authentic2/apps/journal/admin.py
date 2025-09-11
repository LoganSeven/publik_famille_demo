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

import json

from django.contrib import admin
from django.utils.html import format_html

from .models import Event, EventType


@admin.register(EventType)
class EventTypeAdmin(admin.ModelAdmin):
    list_display = [
        '__str__',
        'name',
    ]


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    date_hierarchy = 'timestamp'
    list_filter = ['type']
    list_display = [
        'timestamp',
        'type',
        'user',
        'session_id_shortened',
        'message',
    ]
    fields = [
        'timestamp',
        'type',
        'user',
        'session_id_shortened',
        'formatted_references',
        'message',
        'raw_json',
    ]
    readonly_fields = [
        'timestamp',
        'user',
        'session_id_shortened',
        'formatted_references',
        'message',
        'raw_json',
    ]

    def formatted_references(self, event):
        return format_html('<pre>{}</pre>', event.reference_ids or [])

    def raw_json(self, event):
        return format_html('<pre>{}</pre>', json.dumps(event.data or {}, indent=4))

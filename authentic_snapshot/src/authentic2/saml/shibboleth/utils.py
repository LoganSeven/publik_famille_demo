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

from xml.etree.ElementTree import XMLParser


class FancyTreeBuilder(XMLParser):
    """Attach defined namespaces to elements during parsing"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._namespaces = {}
        self._parser.StartNamespaceDeclHandler = self._start_ns

    def _start(self, *args):
        elem = super()._start(*args)
        elem.namespaces = self._namespaces.copy()
        return elem

    def _start_list(self, *args):
        elem = super()._start_list(*args)
        elem.namespaces = self._namespaces.copy()
        return elem

    def _start_ns(self, prefix, uri):
        self._namespaces[prefix] = uri

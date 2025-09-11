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

import xml.etree.ElementTree as etree

LASSO_NS = 'http://www.entrouvert.org/namespaces/lasso/0.0'
SAML_ASSERTION_NS = 'urn:oasis:names:tc:SAML:2.0:assertion'


def lasso_elt(name):
    return f'{{{LASSO_NS}}}{name}'


def samla_elt(name):
    return f'{{{SAML_ASSERTION_NS}}}{name}'


SESSION_ELT = lasso_elt('Session')
NID_AND_SESSION_INDEX = lasso_elt('NidAndSessionIndex')
VERSION_AT = 'Version'
PROVIDER_ID_AT = 'ProviderID'
ASSERTION_ID_AT = 'AssertionID'
SESSION_INDEX_AT = 'SessionIndex'

NAMEID_ELT = samla_elt('NameID')
FORMAT_AT = 'Format'
NAME_QUALIFIER_AT = 'NameQualifier'
SP_NAME_QUALIFIER_AT = 'SPNameQualifier'


def build_name_id(name_id, treebuilder=None):
    if treebuilder is None:
        tb = etree.TreeBuilder()
    else:
        tb = treebuilder
    attrs = {FORMAT_AT: name_id['name_id_format']}
    if 'name_id_qualifier' in name_id:
        attrs[NAME_QUALIFIER_AT] = name_id['name_id_qualifier']
    if 'name_id_sp_name_qualifier' in name_id:
        attrs[SP_NAME_QUALIFIER_AT] = name_id['name_id_sp_name_qualifier']
    tb.start(NAMEID_ELT, attrs)
    tb.data(name_id['name_id_content'])
    tb.end(NAMEID_ELT)
    if treebuilder is None:
        return tb.close()


def buid_session_dump(sessions):
    tb = etree.TreeBuilder()
    tb.start(SESSION_ELT, {VERSION_AT: '2'})
    for session in sessions:
        tb.start(
            NID_AND_SESSION_INDEX,
            {
                PROVIDER_ID_AT: session['provider_id'],
                ASSERTION_ID_AT: '',
                SESSION_INDEX_AT: session['session_index'],
            },
        )
        build_name_id(session, tb)
        tb.end(NID_AND_SESSION_INDEX)
    tb.end(SESSION_ELT)
    return tb.close()

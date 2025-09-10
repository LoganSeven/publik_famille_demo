import xml.etree.ElementTree as ET

import pytest

from wcs import data_sources
from wcs.admin.settings import UserFieldsFormDef
from wcs.data_sources import NamedDataSource
from wcs.fields import StringField
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.template import Template

from .utilities import clean_temporary_pub, create_temporary_pub


@pytest.fixture
def pub():
    pub = create_temporary_pub()
    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_datasource_users(pub, sql_queries):
    pub.role_class.wipe()
    role1 = pub.role_class(name='role')
    role1.store()
    role2 = pub.role_class(name='role2')
    role2.store()
    role3 = pub.role_class(name='role3')
    role3.allows_backoffice_access = True
    role3.store()

    pub.user_class.wipe()

    NamedDataSource.wipe()
    datasource = NamedDataSource(name='foo')
    datasource.data_source = {'type': 'wcs:users'}
    datasource.store()

    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template(
        '{% for user in data_source.foo %}{{ user.text }}{% if not forloop.last %}, {% endif %}{% endfor %}'
    )

    assert data_sources.get_items({'type': datasource.slug}) == []
    assert data_sources.get_items(datasource.extended_data_source) == []
    assert data_sources.get_structured_items({'type': datasource.slug}) == []
    assert data_sources.get_structured_items(datasource.extended_data_source) == []
    assert tmpl.render(context) == ''

    users = []
    for i in range(2):
        user = pub.user_class(name='John Doe %s' % i)
        user.roles = [role1.id]
        user.name_identifiers = ['abc' + str(i)]
        user.store()
        users.append(user)

    # John Doe 1 can go in backoffice
    users[1].roles.append(role3.id)
    users[1].store()

    sql_queries.clear()
    assert data_sources.get_items({'type': datasource.slug}) == [
        (
            str(users[0].id),
            'John Doe 0',
            str(users[0].id),
            {
                'id': users[0].id,
                'text': 'John Doe 0',
                'user_name_identifier_0': 'abc0',
                'user_nameid': 'abc0',
                'user_admin_access': False,
                'user_backoffice_access': False,
                'user_display_name': 'John Doe 0',
                'user_email': None,
            },
        ),
        (
            str(users[1].id),
            'John Doe 1',
            str(users[1].id),
            {
                'id': users[1].id,
                'text': 'John Doe 1',
                'user_name_identifier_0': 'abc1',
                'user_nameid': 'abc1',
                'user_admin_access': False,
                'user_backoffice_access': True,
                'user_display_name': 'John Doe 1',
                'user_email': None,
            },
        ),
    ]
    assert len(sql_queries) == 3
    assert data_sources.get_items(datasource.extended_data_source) == [
        (
            str(users[0].id),
            'John Doe 0',
            str(users[0].id),
            {
                'id': users[0].id,
                'text': 'John Doe 0',
                'user_name_identifier_0': 'abc0',
                'user_nameid': 'abc0',
                'user_admin_access': False,
                'user_backoffice_access': False,
                'user_display_name': 'John Doe 0',
                'user_email': None,
            },
        ),
        (
            str(users[1].id),
            'John Doe 1',
            str(users[1].id),
            {
                'id': users[1].id,
                'text': 'John Doe 1',
                'user_name_identifier_0': 'abc1',
                'user_nameid': 'abc1',
                'user_admin_access': False,
                'user_backoffice_access': True,
                'user_display_name': 'John Doe 1',
                'user_email': None,
            },
        ),
    ]
    assert data_sources.get_structured_items({'type': datasource.slug}) == [
        {
            'id': users[0].id,
            'text': 'John Doe 0',
            'user_name_identifier_0': 'abc0',
            'user_nameid': 'abc0',
            'user_admin_access': False,
            'user_backoffice_access': False,
            'user_display_name': 'John Doe 0',
            'user_email': None,
        },
        {
            'id': users[1].id,
            'text': 'John Doe 1',
            'user_name_identifier_0': 'abc1',
            'user_nameid': 'abc1',
            'user_admin_access': False,
            'user_backoffice_access': True,
            'user_display_name': 'John Doe 1',
            'user_email': None,
        },
    ]
    assert data_sources.get_structured_items(datasource.extended_data_source) == [
        {
            'id': users[0].id,
            'text': 'John Doe 0',
            'user_name_identifier_0': 'abc0',
            'user_nameid': 'abc0',
            'user_admin_access': False,
            'user_backoffice_access': False,
            'user_display_name': 'John Doe 0',
            'user_email': None,
        },
        {
            'id': users[1].id,
            'text': 'John Doe 1',
            'user_name_identifier_0': 'abc1',
            'user_nameid': 'abc1',
            'user_admin_access': False,
            'user_backoffice_access': True,
            'user_display_name': 'John Doe 1',
            'user_email': None,
        },
    ]
    assert tmpl.render(context) == 'John Doe 0, John Doe 1'

    datasource.users_included_roles = [role1.id]
    datasource.store()
    assert data_sources.get_structured_items({'type': datasource.slug}) == [
        {
            'id': users[0].id,
            'text': 'John Doe 0',
            'user_name_identifier_0': 'abc0',
            'user_nameid': 'abc0',
            'user_admin_access': False,
            'user_backoffice_access': False,
            'user_display_name': 'John Doe 0',
            'user_email': None,
        },
        {
            'id': users[1].id,
            'text': 'John Doe 1',
            'user_name_identifier_0': 'abc1',
            'user_nameid': 'abc1',
            'user_admin_access': False,
            'user_backoffice_access': True,
            'user_display_name': 'John Doe 1',
            'user_email': None,
        },
    ]
    assert data_sources.get_structured_items(datasource.extended_data_source) == [
        {
            'id': users[0].id,
            'text': 'John Doe 0',
            'user_name_identifier_0': 'abc0',
            'user_nameid': 'abc0',
            'user_admin_access': False,
            'user_backoffice_access': False,
            'user_display_name': 'John Doe 0',
            'user_email': None,
        },
        {
            'id': users[1].id,
            'text': 'John Doe 1',
            'user_name_identifier_0': 'abc1',
            'user_nameid': 'abc1',
            'user_admin_access': False,
            'user_backoffice_access': True,
            'user_display_name': 'John Doe 1',
            'user_email': None,
        },
    ]
    assert tmpl.render(context) == 'John Doe 0, John Doe 1'

    datasource.users_included_roles = [role1.id, role2.id]
    datasource.store()
    assert data_sources.get_structured_items({'type': datasource.slug}) == []
    assert data_sources.get_structured_items(datasource.extended_data_source) == []
    assert tmpl.render(context) == ''

    users[0].roles = [role1.id, role2.id]
    users[0].store()
    assert data_sources.get_structured_items({'type': datasource.slug}) == [
        {
            'id': users[0].id,
            'text': 'John Doe 0',
            'user_name_identifier_0': 'abc0',
            'user_nameid': 'abc0',
            'user_admin_access': False,
            'user_backoffice_access': False,
            'user_display_name': 'John Doe 0',
            'user_email': None,
        }
    ]
    assert data_sources.get_structured_items(datasource.extended_data_source) == [
        {
            'id': users[0].id,
            'text': 'John Doe 0',
            'user_name_identifier_0': 'abc0',
            'user_nameid': 'abc0',
            'user_admin_access': False,
            'user_backoffice_access': False,
            'user_display_name': 'John Doe 0',
            'user_email': None,
        }
    ]
    assert tmpl.render(context) == 'John Doe 0'

    users[0].roles = [role2.id]
    users[0].store()
    datasource.users_included_roles = []
    datasource.users_excluded_roles = [role1.id]
    datasource.store()
    assert data_sources.get_structured_items({'type': datasource.slug}) == [
        {
            'id': users[0].id,
            'text': 'John Doe 0',
            'user_name_identifier_0': 'abc0',
            'user_nameid': 'abc0',
            'user_admin_access': False,
            'user_backoffice_access': False,
            'user_display_name': 'John Doe 0',
            'user_email': None,
        }
    ]
    assert data_sources.get_structured_items(datasource.extended_data_source) == [
        {
            'id': users[0].id,
            'text': 'John Doe 0',
            'user_name_identifier_0': 'abc0',
            'user_nameid': 'abc0',
            'user_admin_access': False,
            'user_backoffice_access': False,
            'user_display_name': 'John Doe 0',
            'user_email': None,
        }
    ]
    assert tmpl.render(context) == 'John Doe 0'

    datasource.users_excluded_roles = [role1.id, role2.id]
    datasource.store()
    assert data_sources.get_structured_items({'type': datasource.slug}) == []
    assert data_sources.get_structured_items(datasource.extended_data_source) == []
    assert tmpl.render(context) == ''

    datasource.users_excluded_roles = []
    datasource.store()
    users[1].is_active = False
    users[1].store()
    assert not datasource.include_disabled_users
    assert data_sources.get_structured_items({'type': datasource.slug}) == [
        {
            'id': users[0].id,
            'text': 'John Doe 0',
            'user_name_identifier_0': 'abc0',
            'user_nameid': 'abc0',
            'user_admin_access': False,
            'user_backoffice_access': False,
            'user_display_name': 'John Doe 0',
            'user_email': None,
        }
    ]
    assert data_sources.get_structured_items(datasource.extended_data_source) == [
        {
            'id': users[0].id,
            'text': 'John Doe 0',
            'user_name_identifier_0': 'abc0',
            'user_nameid': 'abc0',
            'user_admin_access': False,
            'user_backoffice_access': False,
            'user_display_name': 'John Doe 0',
            'user_email': None,
        }
    ]
    assert tmpl.render(context) == 'John Doe 0'

    datasource.include_disabled_users = True
    datasource.store()
    assert data_sources.get_structured_items({'type': datasource.slug}) == [
        {
            'id': users[0].id,
            'text': 'John Doe 0',
            'user_name_identifier_0': 'abc0',
            'user_nameid': 'abc0',
            'user_admin_access': False,
            'user_backoffice_access': False,
            'user_display_name': 'John Doe 0',
            'user_email': None,
        },
        {
            'id': users[1].id,
            'text': 'John Doe 1',
            'user_name_identifier_0': 'abc1',
            'user_nameid': 'abc1',
            'user_admin_access': False,
            'user_backoffice_access': True,
            'user_display_name': 'John Doe 1',
            'user_email': None,
        },
    ]
    assert data_sources.get_structured_items(datasource.extended_data_source) == [
        {
            'id': users[0].id,
            'text': 'John Doe 0',
            'user_name_identifier_0': 'abc0',
            'user_nameid': 'abc0',
            'user_admin_access': False,
            'user_backoffice_access': False,
            'user_display_name': 'John Doe 0',
            'user_email': None,
        },
        {
            'id': users[1].id,
            'text': 'John Doe 1',
            'user_name_identifier_0': 'abc1',
            'user_nameid': 'abc1',
            'user_admin_access': False,
            'user_backoffice_access': True,
            'user_display_name': 'John Doe 1',
            'user_email': None,
        },
    ]
    assert tmpl.render(context) == 'John Doe 0, John Doe 1'

    # by uuid
    assert datasource.get_structured_value('abc0') == {
        'id': users[0].id,
        'text': 'John Doe 0',
        'user_name_identifier_0': 'abc0',
        'user_nameid': 'abc0',
        'user_admin_access': False,
        'user_backoffice_access': False,
        'user_display_name': 'John Doe 0',
        'user_email': None,
    }
    assert datasource.get_display_value('abc0') == 'John Doe 0'
    assert datasource.get_structured_value('abc1') == {
        'id': users[1].id,
        'text': 'John Doe 1',
        'user_name_identifier_0': 'abc1',
        'user_nameid': 'abc1',
        'user_admin_access': False,
        'user_backoffice_access': True,
        'user_display_name': 'John Doe 1',
        'user_email': None,
    }
    assert datasource.get_display_value('abc1') == 'John Doe 1'

    # by id
    assert datasource.get_structured_value(str(users[0].id)) == {
        'id': users[0].id,
        'text': 'John Doe 0',
        'user_name_identifier_0': 'abc0',
        'user_nameid': 'abc0',
        'user_admin_access': False,
        'user_backoffice_access': False,
        'user_display_name': 'John Doe 0',
        'user_email': None,
    }
    assert datasource.get_display_value(str(users[0].id)) == 'John Doe 0'
    assert datasource.get_structured_value(str(users[1].id)) == {
        'id': users[1].id,
        'text': 'John Doe 1',
        'user_name_identifier_0': 'abc1',
        'user_nameid': 'abc1',
        'user_admin_access': False,
        'user_backoffice_access': True,
        'user_display_name': 'John Doe 1',
        'user_email': None,
    }
    assert datasource.get_display_value(str(users[1].id)) == 'John Doe 1'

    # by numeric id
    assert datasource.get_structured_value(users[0].id) == {
        'id': users[0].id,
        'text': 'John Doe 0',
        'user_name_identifier_0': 'abc0',
        'user_nameid': 'abc0',
        'user_admin_access': False,
        'user_backoffice_access': False,
        'user_display_name': 'John Doe 0',
        'user_email': None,
    }
    assert datasource.get_display_value(users[0].id) == 'John Doe 0'
    assert datasource.get_structured_value(users[1].id) == {
        'id': users[1].id,
        'text': 'John Doe 1',
        'user_name_identifier_0': 'abc1',
        'user_nameid': 'abc1',
        'user_admin_access': False,
        'user_backoffice_access': True,
        'user_display_name': 'John Doe 1',
        'user_email': None,
    }
    assert datasource.get_display_value(users[1].id) == 'John Doe 1'

    datasource.users_included_roles = [role1.id]
    datasource.users_excluded_roles = [role2.id]
    datasource.store()
    users[0].roles = [role1.id, role2.id]
    users[0].store()

    # by uuid
    assert datasource.get_structured_value('abc0') is None
    assert datasource.get_display_value('abc0') is None
    assert datasource.get_structured_value('abc1') == {
        'id': users[1].id,
        'text': 'John Doe 1',
        'user_name_identifier_0': 'abc1',
        'user_nameid': 'abc1',
        'user_admin_access': False,
        'user_backoffice_access': True,
        'user_display_name': 'John Doe 1',
        'user_email': None,
    }
    assert datasource.get_display_value('abc1') == 'John Doe 1'

    # by id
    assert datasource.get_structured_value(str(users[0].id)) is None
    assert datasource.get_display_value(str(users[0].id)) is None
    assert datasource.get_structured_value(str(users[1].id)) == {
        'id': users[1].id,
        'text': 'John Doe 1',
        'user_name_identifier_0': 'abc1',
        'user_nameid': 'abc1',
        'user_admin_access': False,
        'user_backoffice_access': True,
        'user_display_name': 'John Doe 1',
        'user_email': None,
    }
    assert datasource.get_display_value(str(users[1].id)) == 'John Doe 1'

    datasource.include_disabled_users = False
    datasource.store()
    users[1].is_active = False
    users[1].store()

    # by uuid
    assert datasource.get_structured_value('abc0') is None
    assert datasource.get_display_value('abc0') is None
    assert datasource.get_structured_value('abc1') is None
    assert datasource.get_display_value('abc1') is None

    # by id
    assert datasource.get_structured_value(str(users[0].id)) is None
    assert datasource.get_display_value(str(users[0].id)) is None
    assert datasource.get_structured_value(str(users[1].id)) is None
    assert datasource.get_display_value(str(users[1].id)) is None


def test_datasource_users_user_formdef(pub):
    pub.user_class.wipe()

    formdef = UserFieldsFormDef(pub)
    formdef.fields = [
        StringField(id='3', label='test', varname='plop'),
    ]
    formdef.store()

    user = pub.user_class(name='John Doe')
    user.form_data = {'3': 'Bar'}
    user.store()

    NamedDataSource.wipe()
    datasource = NamedDataSource(name='foo')
    datasource.data_source = {'type': 'wcs:users'}
    datasource.store()

    assert data_sources.get_items({'type': datasource.slug}) == [
        (
            str(user.id),
            'John Doe',
            str(user.id),
            {
                'user_display_name': 'John Doe',
                'user_email': None,
                'user_f3': 'Bar',
                'user_field_test': 'Bar',
                'user_var_plop': 'Bar',
                'user_admin_access': False,
                'user_backoffice_access': False,
                'id': user.id,
                'text': 'John Doe',
            },
        )
    ]


def test_legacy_format_import(pub):
    data_source_xml = """<datasource id="255">
  <name>Agents de la ville</name>
  <slug>agents_de_la_ville</slug>
  <data_source>
    <type>wcs:users</type>
    <value />
  </data_source><users_included_roles>
    <item>8201764fc2c24b92bd691fd231a4cf76</item>
  </users_included_roles>
</datasource>"""
    ds = NamedDataSource.import_from_xml_tree(ET.fromstring(data_source_xml))
    assert ds.users_included_roles == ['8201764fc2c24b92bd691fd231a4cf76']


def test_new_format_import(pub):
    data_source_xml = """<datasource id="255">
  <name>Agents de la ville</name>
  <slug>agents_de_la_ville</slug>
  <data_source>
    <type>wcs:users</type>
    <value />
  </data_source><users_included_roles>
    <role role-id="8201764fc2c24b92bd691fd231a4cf76" role-slug="agent">Agents</role>
  </users_included_roles>
</datasource>"""
    ds = NamedDataSource.import_from_xml_tree(ET.fromstring(data_source_xml))
    assert ds.users_included_roles == []  # role doesn't exist

    # import with id match
    pub.role_class.wipe()
    role1 = pub.role_class(name='role')
    role1.id = '8201764fc2c24b92bd691fd231a4cf76'
    role1.store()

    ds = NamedDataSource.import_from_xml_tree(ET.fromstring(data_source_xml), include_id=True)
    assert ds.users_included_roles == [role1.id]

    # import with slug match
    pub.role_class.wipe()
    role1 = pub.role_class(name='Agents')
    role1.slug = 'agent'
    role1.store()

    ds = NamedDataSource.import_from_xml_tree(ET.fromstring(data_source_xml), include_id=False)
    assert ds.users_included_roles == [role1.id]

    # import with name match
    pub.role_class.wipe()
    role1 = pub.role_class(name='Agents')
    role1.slug = 'agent'
    role1.store()

    ds = NamedDataSource.import_from_xml_tree(
        ET.fromstring(data_source_xml.replace('role-slug="agent"', '')), include_id=False
    )
    assert ds.users_included_roles == [role1.id]

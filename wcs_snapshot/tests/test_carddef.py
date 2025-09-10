import io
import json
import xml.etree.ElementTree as ET

import pytest

from wcs.blocks import BlockDef
from wcs.carddef import CardDef
from wcs.categories import CardDefCategory
from wcs.data_sources import NamedDataSource
from wcs.fields import BlockField, ComputedField, ItemField, ItemsField, StringField
from wcs.formdef import FormDef
from wcs.formdef_jobs import UpdateDigestAfterJob, UpdateRelationsAfterJob
from wcs.logged_errors import LoggedError
from wcs.qommon.http_request import HTTPRequest
from wcs.qommon.template import Template
from wcs.sql_criterias import Intersects, Not
from wcs.workflows import Workflow

from .utilities import MockSubstitutionVariables, clean_temporary_pub, create_temporary_pub


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()
    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()
    return pub


def teardown_module(module):
    clean_temporary_pub()


def export_to_indented_xml(carddef, include_id=False):
    carddef_xml = ET.fromstring(ET.tostring(carddef.export_to_xml(include_id=include_id)))
    ET.indent(carddef_xml)
    return carddef_xml


def assert_compare_carddef(carddef1, carddef2, include_id=False):
    assert ET.tostring(export_to_indented_xml(carddef1, include_id=include_id)) == ET.tostring(
        export_to_indented_xml(carddef2, include_id=include_id)
    )
    assert carddef1.export_to_json(include_id=include_id, indent=2) == carddef2.export_to_json(
        include_id=include_id, indent=2
    )


def assert_xml_import_export_works(carddef, include_id=False):
    carddef_xml = carddef.export_to_xml(include_id=include_id)
    carddef2 = CardDef.import_from_xml_tree(carddef_xml, include_id=include_id)
    assert_compare_carddef(carddef, carddef2, include_id=include_id)
    return carddef2


def test_basics(pub):
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.store()
    assert CardDef.get(carddef.id).name == 'foo'

    carddata_class = carddef.data_class()
    carddata = carddata_class()
    carddata.data = {'1': 'hello world'}
    carddata.just_created()
    carddata.store()
    assert carddata.status == 'wf-recorded'

    assert carddata_class.get(carddata.id).data['1'] == 'hello world'
    assert carddata_class.get(carddata.id).status == 'wf-recorded'

    assert carddata_class.get(carddata.id).uuid


def test_advertised_urls(pub):
    CardDef.wipe()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.store()
    assert CardDef.get(carddef.id).name == 'foo'
    assert carddef.get_url() == 'http://example.net/backoffice/data/foo/'
    assert carddef.get_backoffice_submission_url() == 'http://example.net/backoffice/data/foo/add/'
    assert carddef.get_admin_url() == 'http://example.net/backoffice/cards/%s/' % carddef.id
    assert carddef.get_api_url() == 'http://example.net/api/cards/foo/'


def test_xml_export_import(pub):
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
        ItemField(id='2', label='card field', data_source={'type': 'carddef:foo'}),
    ]
    carddef.store()

    # define also custom views
    pub.custom_view_class.wipe()

    custom_view = pub.custom_view_class()
    custom_view.title = 'datasource card view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': 'id'}, {'id': 'time'}, {'id': 'status'}, {'id': '1'}, {'id': '2'}]}
    custom_view.filters = {
        'filter': 'recorded',
        'filter-1': 'on',
        'filter-status': 'on',
        'filter-1-value': 'a',
    }
    custom_view.visibility = 'datasource'
    custom_view.order_by = '-receipt_time'
    custom_view.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'shared card view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': 'id'}, {'id': 'time'}, {'id': 'status'}]}
    custom_view.filters = {'filter': 'done', 'filter-1': 'on', 'filter-status': 'on', 'filter-1-value': 'b'}
    custom_view.visibility = 'any'
    custom_view.order_by = 'receipt_time'
    custom_view.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'private card view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.visibility = 'owner'
    custom_view.usier_id = 42
    custom_view.order_by = 'id'
    custom_view.store()

    carddef_xml = carddef.export_to_xml()
    assert carddef_xml.tag == 'carddef'
    carddef.data_class().wipe()
    pub.custom_view_class.wipe()

    carddef2 = CardDef.import_from_xml(io.BytesIO(ET.tostring(carddef_xml)))
    assert carddef2.name == 'foo'
    assert carddef2.fields[1].data_source == {'type': 'carddef:foo'}
    assert carddef2._custom_views

    custom_views = sorted(carddef2._custom_views, key=lambda a: a.visibility)
    assert len(custom_views) == 2
    assert custom_views[0].title == 'shared card view'
    assert custom_views[0].slug == 'shared-card-view'
    assert custom_views[0].columns == {'list': [{'id': 'id'}, {'id': 'time'}, {'id': 'status'}]}
    assert custom_views[0].filters == {
        'filter': 'done',
        'filter-1': 'on',
        'filter-status': 'on',
        'filter-1-value': 'b',
    }
    assert custom_views[0].visibility == 'any'
    assert custom_views[0].order_by == 'receipt_time'
    assert custom_views[0].formdef_id is None
    assert custom_views[0].formdef_type is None
    assert custom_views[1].title == 'datasource card view'
    assert custom_views[1].slug == 'datasource-card-view'
    assert custom_views[1].columns == {
        'list': [{'id': 'id'}, {'id': 'time'}, {'id': 'status'}, {'id': '1'}, {'id': '2'}]
    }
    assert custom_views[1].filters == {
        'filter': 'recorded',
        'filter-1': 'on',
        'filter-status': 'on',
        'filter-1-value': 'a',
    }
    assert custom_views[1].visibility == 'datasource'
    assert custom_views[1].order_by == '-receipt_time'
    assert custom_views[1].formdef_id is None
    assert custom_views[1].formdef_type is None

    carddef2.store()
    custom_views = sorted(pub.custom_view_class.select(), key=lambda a: a.visibility)
    assert len(custom_views) == 2
    assert custom_views[0].title == 'shared card view'
    assert custom_views[0].slug == 'shared-card-view'
    assert custom_views[0].columns == {'list': [{'id': 'id'}, {'id': 'time'}, {'id': 'status'}]}
    assert custom_views[0].filters == {
        'filter': 'done',
        'filter-1': 'on',
        'filter-status': 'on',
        'filter-1-value': 'b',
    }
    assert custom_views[0].visibility == 'any'
    assert custom_views[0].order_by == 'receipt_time'
    assert custom_views[0].formdef_id == str(carddef2.id)
    assert custom_views[0].formdef_type == 'carddef'
    assert custom_views[1].title == 'datasource card view'
    assert custom_views[1].slug == 'datasource-card-view'
    assert custom_views[1].columns == {
        'list': [{'id': 'id'}, {'id': 'time'}, {'id': 'status'}, {'id': '1'}, {'id': '2'}]
    }
    assert custom_views[1].filters == {
        'filter': 'recorded',
        'filter-1': 'on',
        'filter-status': 'on',
        'filter-1-value': 'a',
    }
    assert custom_views[1].visibility == 'datasource'
    assert custom_views[1].order_by == '-receipt_time'
    assert custom_views[1].formdef_id == str(carddef2.id)
    assert custom_views[1].formdef_type == 'carddef'


def test_xml_export_import_history_pane_default_mode(pub):
    CardDef.wipe()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.history_pane_default_mode = 'collapsed'
    f2 = assert_xml_import_export_works(carddef)
    assert f2.history_pane_default_mode == carddef.history_pane_default_mode


def test_xml_export_import_category_reference(pub):
    CardDefCategory.wipe()
    CardDef.wipe()

    cat = CardDefCategory()
    cat.name = 'test category'
    cat.store()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.category_id = str(cat.id)
    f2 = assert_xml_import_export_works(carddef)
    assert f2.category_id == carddef.category_id

    f2 = assert_xml_import_export_works(carddef, include_id=True)
    assert f2.category_id == carddef.category_id

    carddef_xml_with_id = carddef.export_to_xml(include_id=True)

    # check there's no reference to a non-existing category
    CardDefCategory.wipe()
    assert CardDef.import_from_xml_tree(carddef_xml_with_id, include_id=False).category_id is None
    assert CardDef.import_from_xml_tree(carddef_xml_with_id, include_id=True).category_id is None

    # check an import that is not using id fields will find the category by its
    # name
    cat = CardDefCategory()
    cat.id = '2'
    cat.name = 'test category'
    cat.store()
    assert CardDef.import_from_xml_tree(carddef_xml_with_id, include_id=False).category_id == '2'
    assert CardDef.import_from_xml_tree(carddef_xml_with_id, include_id=True).category_id is None


def test_template_access(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
        StringField(id='2', label='key', varname='key'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    for i in range(10):
        carddata = carddef.data_class()()
        if i % 3 == 0:
            carddata.data = {'1': 'blah'}
        if i % 3 == 1:
            carddata.data = {'1': 'foo'}
        if i % 3 == 2:
            carddata.data = {'1': 'bar'}
        carddata.data['2'] = str(i)
        carddata.just_created()
        carddata.store()

    context = pub.substitutions.get_context_variables(mode='lazy')

    tmpl = Template('{{cards.foo.objects|filter_by:"foo"|filter_value:"blah"|count}}')
    assert tmpl.render(context) == '4'
    tmpl = Template('{{cards|objects:"foo"|filter_by:"foo"|filter_value:"blah"|count}}')
    assert tmpl.render(context) == '4'

    pub.custom_view_class.wipe()

    custom_view1 = pub.custom_view_class()
    custom_view1.title = 'datasource card view'
    custom_view1.formdef = carddef
    custom_view1.columns = {'list': [{'id': 'id'}, {'id': 'time'}, {'id': 'status'}]}
    custom_view1.filters = {'filter-1': 'on', 'filter-1-value': 'blah'}
    custom_view1.visibility = 'datasource'
    custom_view1.order_by = '-f2'
    custom_view1.store()

    custom_view2 = pub.custom_view_class()
    custom_view2.title = 'shared card view'
    custom_view2.formdef = carddef
    custom_view2.columns = {'list': [{'id': 'id'}, {'id': 'time'}, {'id': 'status'}]}
    custom_view2.filters = {'filter-1': 'on', 'filter-1-value': 'foo'}
    custom_view2.visibility = 'any'
    custom_view2.order_by = 'f2'
    custom_view2.store()

    custom_view3 = pub.custom_view_class()
    custom_view3.title = 'private card view'
    custom_view3.formdef = carddef
    custom_view3.columns = {'list': [{'id': 'id'}]}
    custom_view3.filters = {}
    custom_view3.visibility = 'owner'
    custom_view3.user_id = '42'
    custom_view3.order_by = 'id'
    custom_view3.store()

    custom_view4 = pub.custom_view_class()
    custom_view4.title = 'role card view'
    custom_view4.formdef = carddef
    custom_view4.columns = {'list': [{'id': 'id'}]}
    custom_view4.filters = {}
    custom_view4.visibility = 'role'
    custom_view4.role_id = '42'
    custom_view4.order_by = 'id'
    custom_view4.store()

    tmpl = Template('{{cards.foo.objects|with_custom_view:"datasource-card-view"|count}}')
    assert tmpl.render(context) == '4'
    tmpl = Template('{{cards|objects:"foo"|with_custom_view:"datasource-card-view"|count}}')
    assert tmpl.render(context) == '4'
    tmpl = Template(
        '{% for data in cards|objects:"foo"|with_custom_view:"datasource-card-view" %}{{ data.internal_id }},{% endfor %}'
    )
    assert tmpl.render(context) == '10,7,4,1,'

    tmpl = Template('{{cards.foo.objects|with_custom_view:"shared-card-view"|count}}')
    assert tmpl.render(context) == '3'
    tmpl = Template('{{cards|objects:"foo"|with_custom_view:"shared-card-view"|count}}')
    assert tmpl.render(context) == '3'
    tmpl = Template(
        '{% for data in cards|objects:"foo"|with_custom_view:"shared-card-view" %}{{ data.internal_id }},{% endfor %}'
    )
    assert tmpl.render(context) == '2,5,8,'

    tmpl = Template('{{cards.foo.objects|with_custom_view:"private-card-view"|count}}')
    assert tmpl.render(context) == '0'
    tmpl = Template('{{cards|objects:"foo"|with_custom_view:"private-card-view"|count}}')
    assert tmpl.render(context) == '0'
    tmpl = Template('{{cards.foo.objects|with_custom_view:"role-card-view"|count}}')
    assert tmpl.render(context) == '0'
    tmpl = Template('{{cards|objects:"foo"|with_custom_view:"role-card-view"|count}}')
    assert tmpl.render(context) == '0'
    tmpl = Template('{{cards.foo.objects|with_custom_view:"unknown"|count}}')
    assert tmpl.render(context) == '0'
    tmpl = Template('{{cards|objects:"foo"|with_custom_view:"unknown"|count}}')
    assert tmpl.render(context) == '0'


def test_objects_filter(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'card'
    carddef.fields = []
    carddef.store()
    carddata_class = carddef.data_class()
    carddata_class.wipe()

    carddata = carddata_class()
    carddata.just_created()
    carddata.store()

    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{cards|objects:"card"|count}}')
    assert tmpl.render(context) == '1'


def test_with_custom_view(pub):
    Workflow.wipe()
    CardDef.wipe()

    workflow = Workflow(name='test')
    st1 = workflow.add_status('st1')
    st2 = workflow.add_status('st2')
    st3 = workflow.add_status('st3')
    workflow.store()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = []
    carddef.workflow = workflow
    carddef.store()
    carddef.data_class().wipe()

    for i in range(10):
        carddata = carddef.data_class()()
        carddata.just_created()
        if i % 3 == 0:
            carddata.jump_status(st1.id)
        elif i % 3 == 1:
            carddata.jump_status(st2.id)
        elif i % 3 == 2:
            carddata.jump_status(st3.id)
        carddata.store()

    context = pub.substitutions.get_context_variables(mode='lazy')

    pub.custom_view_class.wipe()

    custom_view = pub.custom_view_class()
    custom_view.title = 'card view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': 'id'}, {'id': 'time'}, {'id': 'status'}]}
    custom_view.filters = {'filter': f'{st1.id}', 'filter-status': 'on'}
    custom_view.visibility = 'datasource'
    custom_view.store()

    tmpl = Template('{{cards|objects:"foo"|with_custom_view:"card-view"|count}}')
    assert tmpl.render(context) == '4'

    custom_view.filters = {'filter': f'{st1.id}', 'filter-status': 'on', 'filter-operator': 'ne'}
    custom_view.store()
    assert tmpl.render(context) == '6'

    custom_view.filters = {'filter': f'{st1.id}|{st3.id}', 'filter-status': 'on', 'filter-operator': 'in'}
    custom_view.store()
    assert tmpl.render(context) == '7'

    custom_view.filters = {'filter': f'{st1.id}|{st3.id}', 'filter-status': 'on', 'filter-operator': 'not_in'}
    custom_view.store()
    assert tmpl.render(context) == '3'


def test_data_source_access_by_id(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.data = {'1': 'hello world'}
    carddata.just_created()
    carddata.store()

    carddata2 = carddef.data_class()()
    carddata2.data = {'1': 'bye'}
    carddata2.just_created()
    carddata2.store()

    cards = CardDef.get_data_source_items('carddef:foo', get_by_id=carddata.id)
    assert len(cards) == 1
    assert cards[0]['text'] == 'hello world'

    cards = CardDef.get_data_source_items('carddef:foo', get_by_id=carddata2.id)
    assert len(cards) == 1
    assert cards[0]['text'] == 'bye'

    cards = CardDef.get_data_source_items('carddef:foo', get_by_id=carddata.get_display_id())
    assert len(cards) == 1
    assert cards[0]['text'] == 'hello world'

    cards = CardDef.get_data_source_items('carddef:foo', get_by_id=carddata2.get_display_id())
    assert len(cards) == 1
    assert cards[0]['text'] == 'bye'


def test_data_source_access_invalid_id(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    assert CardDef.get_data_source_items('carddef:foo', get_by_id='424508729041982') == []


def test_data_source_structured_value_by_id(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.data = {'1': 'hello world'}
    carddata.just_created()
    carddata.store()

    carddata2 = carddef.data_class()()
    carddata2.data = {'1': 'None'}  # should not be matched when looking for None
    carddata2.just_created()
    carddata2.store()

    data_source = NamedDataSource()
    data_source.data_source = {'type': 'carddef:foo'}
    value = data_source.get_structured_value(carddata.id)
    assert value == {'id': 1, 'text': 'hello world', 'foo': 'hello world'}

    value = data_source.get_structured_value('hello world')
    assert value == {'id': 1, 'text': 'hello world', 'foo': 'hello world'}

    value = data_source.get_structured_value(12)
    assert value is None

    value = data_source.get_structured_value(None)
    assert value is None


def test_get_data_source_custom_view(pub):
    CardDef.wipe()
    carddef1 = CardDef()
    carddef1.name = 'foo 1'
    carddef1.fields = []
    carddef1.store()

    carddef2 = CardDef()
    carddef2.name = 'foo 2'
    carddef2.fields = []
    carddef2.store()

    pub.custom_view_class.wipe()

    custom_view_orphan = pub.custom_view_class()
    custom_view_orphan.title = 'view'
    custom_view_orphan.formdef = carddef1
    custom_view_orphan.columns = {'list': [{'id': 'id'}]}
    custom_view_orphan.filters = {}
    custom_view_orphan.visibility = 'datasource'
    custom_view_orphan.formdef_id = '99999'
    custom_view_orphan.store()

    custom_view1 = pub.custom_view_class()
    custom_view1.title = 'view'
    custom_view1.formdef = carddef1
    custom_view1.columns = {'list': [{'id': 'id'}]}
    custom_view1.filters = {}
    custom_view1.visibility = 'datasource'
    custom_view1.store()

    custom_view2 = pub.custom_view_class()
    custom_view2.title = 'view'
    custom_view2.formdef = carddef2
    custom_view2.columns = {'list': [{'id': 'id'}]}
    custom_view2.filters = {}
    custom_view2.visibility = 'datasource'
    custom_view2.store()

    assert CardDef.get_data_source_custom_view('carddef:foo-1:view').id == custom_view1.id
    assert CardDef.get_data_source_custom_view('carddef:foo-2:view').id == custom_view2.id
    assert CardDef.get_data_source_custom_view('carddef:foo-1:view', carddef=carddef1).id == custom_view1.id
    assert CardDef.get_data_source_custom_view('carddef:foo-1:view', carddef=carddef2) is None
    assert CardDef.get_data_source_custom_view('carddef:foo-2:view', carddef=carddef2).id == custom_view2.id
    assert CardDef.get_data_source_custom_view('carddef:foo-2:view', carddef=carddef1) is None


def test_data_source_custom_view_unknown_filter(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.data = {'1': 'Hello'}
    carddata.just_created()
    carddata.store()

    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {'filter-42': 'on', 'filter-42-value': 'Hello'}
    custom_view.visibility = 'datasource'
    custom_view.store()

    assert [i['text'] for i in CardDef.get_data_source_items('carddef:foo')] == ['Hello']
    assert [i['text'] for i in CardDef.get_data_source_items('carddef:foo:view')] == []
    assert LoggedError.count() == 1
    logged_error = LoggedError.select(order_by='id')[0]
    assert logged_error.summary == 'Invalid filter "42".'
    assert logged_error.formdef_id == str(carddef.id)


def test_data_source_anonymised_cards(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.data = {'1': 'Hello 1'}
    carddata.just_created()
    carddata.store()

    carddata = carddef.data_class()()
    carddata.data = {'1': 'Hello 2'}
    carddata.just_created()
    carddata.store()

    assert [i['text'] for i in CardDef.get_data_source_items('carddef:foo')] == ['Hello 1', 'Hello 2']
    carddata.anonymise()
    assert [i['text'] for i in CardDef.get_data_source_items('carddef:foo')] == ['Hello 1']


def test_data_source_custom_view_digest(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.data = {'1': 'Hello'}
    carddata.just_created()
    carddata.store()

    carddata2 = carddef.data_class()()
    carddata2.data = {'1': 'Bye'}
    carddata2.just_created()
    carddata2.store()

    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.visibility = 'datasource'
    custom_view.store()

    assert [i['text'] for i in CardDef.get_data_source_items('carddef:foo')] == ['Bye', 'Hello']
    assert [i['text'] for i in CardDef.get_data_source_items('carddef:foo:view')] == ['Bye', 'Hello']

    cards = CardDef.get_data_source_items('carddef:foo', query='hello')
    assert len(cards) == 1
    assert cards[0]['text'] == 'Hello'
    cards = CardDef.get_data_source_items('carddef:foo:view', query='hello')
    assert len(cards) == 1
    assert cards[0]['text'] == 'Hello'

    cards = CardDef.get_data_source_items('carddef:foo', query='foo')
    assert len(cards) == 0
    cards = CardDef.get_data_source_items('carddef:foo:view', query='foo')
    assert len(cards) == 0

    cards = CardDef.get_data_source_items('carddef:foo', get_by_text='Hello')
    assert len(cards) == 1
    assert cards[0]['text'] == 'Hello'
    cards = CardDef.get_data_source_items('carddef:foo:view', get_by_text='Hello')
    assert len(cards) == 1
    assert cards[0]['text'] == 'Hello'

    cards = CardDef.get_data_source_items('carddef:foo', get_by_text='Hello Foo Bar')
    assert len(cards) == 0
    cards = CardDef.get_data_source_items('carddef:foo:view', get_by_text='Hello Foo Bar')
    assert len(cards) == 0

    carddef.digest_templates = {
        'default': '{{ form_var_foo }}',
        'custom-view:view': '{{ form_var_foo }} Foo Bar',
    }
    carddef.store()
    pub.reset_caches()
    # rebuild digests
    carddata.store()
    carddata2.store()

    assert [i['text'] for i in CardDef.get_data_source_items('carddef:foo')] == ['Bye', 'Hello']
    assert [i['text'] for i in CardDef.get_data_source_items('carddef:foo:view')] == [
        'Bye Foo Bar',
        'Hello Foo Bar',
    ]

    cards = CardDef.get_data_source_items('carddef:foo', query='hello')
    assert len(cards) == 1
    assert cards[0]['text'] == 'Hello'
    cards = CardDef.get_data_source_items('carddef:foo:view', query='hello')
    assert len(cards) == 1
    assert cards[0]['text'] == 'Hello Foo Bar'

    cards = CardDef.get_data_source_items('carddef:foo', query='foo')
    assert len(cards) == 0
    cards = CardDef.get_data_source_items('carddef:foo:view', query='foo')
    assert len(cards) == 2
    assert cards[0]['text'] == 'Bye Foo Bar'
    assert cards[1]['text'] == 'Hello Foo Bar'

    cards = CardDef.get_data_source_items('carddef:foo', get_by_text='Hello')
    assert len(cards) == 1
    assert cards[0]['text'] == 'Hello'
    cards = CardDef.get_data_source_items('carddef:foo:view', get_by_text='Hello')
    assert len(cards) == 0

    cards = CardDef.get_data_source_items('carddef:foo', get_by_text='Hello Foo Bar')
    assert len(cards) == 0
    cards = CardDef.get_data_source_items('carddef:foo:view', get_by_text='Hello Foo Bar')
    assert len(cards) == 1
    assert cards[0]['text'] == 'Hello Foo Bar'

    # digests are not defined
    carddef.digest_templates = {}
    carddef.store()
    carddata.id_display = None
    carddata.digests = None
    carddata.store()
    carddata2.id_display = None
    carddata2.digests = None
    carddata2.store()
    carddef.digest_templates = {'custom-view:view': '{{ form_var_foo }} Foo Bar'}
    carddef.store()
    assert [i['text'] for i in CardDef.get_data_source_items('carddef:foo')] == ['', '']
    assert [i['text'] for i in CardDef.get_data_source_items('carddef:foo:view')] == ['', '']

    # check errors are recorded in slow code path
    assert [i['text'] for i in CardDef.get_data_source_items('carddef:foo', with_files_urls=True)] == ['', '']
    assert [i['text'] for i in CardDef.get_data_source_items('carddef:foo:view', with_files_urls=True)] == [
        '',
        '',
    ]
    assert LoggedError.count() == 2
    logged_error = LoggedError.select(order_by='id')[0]
    assert logged_error.summary == 'Digest (default) not defined'
    assert logged_error.formdata_id in (str(carddata.id), str(carddata2.id))
    logged_error = LoggedError.select(order_by='id')[1]
    assert logged_error.summary == 'Digest (custom view "view") not defined'
    assert logged_error.formdata_id in (str(carddata.id), str(carddata2.id))
    assert CardDef.get_data_source_items('carddef:foo', get_by_text='') == []
    assert CardDef.get_data_source_items('carddef:foo:view', get_by_text='') == []


def test_get_data_source_custom_view_order_by(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
        ItemField(
            id='2',
            label='Test2',
            varname='bar',
            data_source={
                'type': 'jsonvalue',
                'value': json.dumps(
                    [
                        {'id': '1', 'text': 'pomme'},
                        {'id': '2', 'text': 'poire'},
                        {'id': '3', 'text': 'pêche'},
                        {'id': '4', 'text': 'abricot'},
                    ]
                ),
            },
        ),
    ]
    carddef.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.data = {'1': 'Hello 1', '2': '1'}
    carddata.data['2_display'] = carddef.fields[1].store_display_value(carddata.data, carddef.fields[1].id)
    carddata.just_created()
    carddata.store()

    carddata = carddef.data_class()()
    carddata.data = {'1': 'Hello 2', '2': '4'}
    carddata.data['2_display'] = carddef.fields[1].store_display_value(carddata.data, carddef.fields[1].id)
    carddata.just_created()
    carddata.store()

    carddata = carddef.data_class()()
    carddata.data = {'1': 'Hello 3', '2': '2'}
    carddata.data['2_display'] = carddef.fields[1].store_display_value(carddata.data, carddef.fields[1].id)
    carddata.just_created()
    carddata.store()

    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.visibility = 'datasource'
    custom_view.store()

    assert [i['text'] for i in CardDef.get_data_source_items('carddef:foo:view')] == [
        'Hello 1',
        'Hello 2',
        'Hello 3',
    ]

    custom_view.order_by = '-f1'
    custom_view.store()
    assert [i['text'] for i in CardDef.get_data_source_items('carddef:foo:view')] == [
        'Hello 3',
        'Hello 2',
        'Hello 1',
    ]

    custom_view.order_by = 'f2'
    custom_view.store()
    assert [i['text'] for i in CardDef.get_data_source_items('carddef:foo:view')] == [
        'Hello 2',
        'Hello 3',
        'Hello 1',
    ]
    carddef.digest_templates['custom-view:view'] = '{{ form_var_bar }}'
    carddef.store()
    pub.reset_caches()
    for carddata in carddef.data_class().select():
        carddata.store()  # rebuild digests
    assert [i['text'] for i in CardDef.get_data_source_items('carddef:foo:view')] == [
        'abricot',
        'poire',
        'pomme',
    ]


def test_data_source_query_escape(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    carddata = carddef.data_class()()
    carddata.data = {'1': 'Astreinte\\Lundi %'}
    carddata.just_created()
    carddata.store()

    carddata2 = carddef.data_class()()
    carddata2.data = {'1': 'Astreinte\\Mardi _'}
    carddata2.just_created()
    carddata2.store()

    cards = CardDef.get_data_source_items('carddef:foo', query='astreinte')
    assert len(cards) == 2
    assert cards[0]['text'] == 'Astreinte\\Lundi %'

    cards = CardDef.get_data_source_items('carddef:foo', query='astreinte\\')
    assert len(cards) == 2
    assert cards[0]['text'] == 'Astreinte\\Lundi %'

    cards = CardDef.get_data_source_items('carddef:foo', query='astreinte\\l')
    assert len(cards) == 1
    assert cards[0]['text'] == 'Astreinte\\Lundi %'

    cards = CardDef.get_data_source_items('carddef:foo', query='%')
    assert len(cards) == 1
    assert cards[0]['text'] == 'Astreinte\\Lundi %'

    cards = CardDef.get_data_source_items('carddef:foo', query='_')
    assert len(cards) == 1
    assert cards[0]['text'] == 'Astreinte\\Mardi _'


def test_reverse_relations(pub):
    FormDef.wipe()
    CardDef.wipe()
    BlockDef.wipe()

    formdef1 = FormDef()
    formdef1.name = 'formdef 1'
    formdef1.store()

    formdef2 = FormDef()
    formdef2.name = 'formdef 2'
    formdef2.store()

    carddef1 = CardDef()
    carddef1.name = 'carddef 1'
    carddef1.store()

    carddef2 = CardDef()
    carddef2.name = 'carddef 2'
    carddef2.store()

    block1 = BlockDef()
    block1.name = 'block 1'
    block1.fields = [
        ItemField(id='0', label='unknown', data_source={'type': 'carddef:unknown'}),
        ItemField(
            id='1',
            label='item',
            varname='block_foo_1',
            data_source={'type': 'carddef:carddef-1'},
        ),
        ItemsField(id='2', label='items', data_source={'type': 'carddef:carddef-1'}),
        ComputedField(
            id='3',
            label='computed',
            varname='block_computed_foo_1',
            data_source={'type': 'carddef:carddef-1'},
        ),
    ]
    block1.store()

    assert formdef1.reverse_relations == []
    assert formdef2.reverse_relations == []
    assert carddef1.reverse_relations == []
    assert carddef2.reverse_relations == []

    formdef1.fields = [
        ItemField(id='0', label='unknown', data_source={'type': 'carddef:unknown'}),
        ItemField(id='1', label='item', data_source={'type': 'carddef:carddef-1'}),
    ]
    formdef1.store()

    formdef1.refresh_from_storage()
    formdef2.refresh_from_storage()
    carddef1.refresh_from_storage()
    carddef2.refresh_from_storage()
    assert formdef1.reverse_relations == []
    assert formdef2.reverse_relations == []
    assert carddef1.reverse_relations == [
        {'varname': '', 'type': 'item', 'obj': 'formdef:formdef-1'},
    ]
    assert carddef2.reverse_relations == []

    formdef2.fields = [
        ItemsField(id='1', label='items', varname='bar', data_source={'type': 'carddef:carddef-2'}),
    ]
    formdef2.store()

    formdef1.refresh_from_storage()
    formdef2.refresh_from_storage()
    carddef1.refresh_from_storage()
    carddef2.refresh_from_storage()
    assert formdef1.reverse_relations == []
    assert formdef2.reverse_relations == []
    assert carddef1.reverse_relations == [
        {'varname': '', 'type': 'item', 'obj': 'formdef:formdef-1'},
    ]
    assert carddef2.reverse_relations == [
        {'varname': 'bar', 'type': 'items', 'obj': 'formdef:formdef-2'},
    ]

    carddef1.fields = [
        ItemField(id='0', label='unknown', data_source={'type': 'carddef:unknown'}),
        ItemField(id='1', label='item', data_source={'type': 'carddef:carddef-2'}),
    ]
    carddef1.store()

    formdef1.refresh_from_storage()
    formdef2.refresh_from_storage()
    carddef1.refresh_from_storage()
    carddef2.refresh_from_storage()
    assert formdef1.reverse_relations == []
    assert formdef2.reverse_relations == []
    assert carddef1.reverse_relations == [
        {'varname': '', 'type': 'item', 'obj': 'formdef:formdef-1'},
    ]
    assert carddef2.reverse_relations == [
        {'varname': '', 'type': 'item', 'obj': 'carddef:carddef-1'},
        {'varname': 'bar', 'type': 'items', 'obj': 'formdef:formdef-2'},
    ]

    carddef1.fields = [
        ItemsField(id='1', label='items', data_source={'type': 'carddef:carddef-2'}),
    ]
    carddef1.store()

    formdef1.refresh_from_storage()
    formdef2.refresh_from_storage()
    carddef1.refresh_from_storage()
    carddef2.refresh_from_storage()
    assert formdef1.reverse_relations == []
    assert formdef2.reverse_relations == []
    assert carddef1.reverse_relations == [
        {'varname': '', 'type': 'item', 'obj': 'formdef:formdef-1'},
    ]
    assert carddef2.reverse_relations == [
        {'varname': '', 'type': 'items', 'obj': 'carddef:carddef-1'},
        {'varname': 'bar', 'type': 'items', 'obj': 'formdef:formdef-2'},
    ]

    # custom views ?
    carddef1.fields = [
        ComputedField(
            id='1',
            label='computed',
            varname='computed_foobar',
            data_source={'type': 'carddef:carddef-2:view'},
        ),
    ]
    carddef1.store()

    formdef1.refresh_from_storage()
    formdef2.refresh_from_storage()
    carddef1.refresh_from_storage()
    carddef2.refresh_from_storage()
    assert formdef1.reverse_relations == []
    assert formdef2.reverse_relations == []
    assert carddef1.reverse_relations == [
        {'varname': '', 'type': 'item', 'obj': 'formdef:formdef-1'},
    ]
    assert carddef2.reverse_relations == [
        {'varname': 'computed_foobar', 'type': 'computed', 'obj': 'carddef:carddef-1'},
        {'varname': 'bar', 'type': 'items', 'obj': 'formdef:formdef-2'},
    ]

    # circular relation ?
    carddef2.fields = [
        ItemsField(id='1', label='items', data_source={'type': 'carddef:carddef-2'}),
    ]
    carddef2.store()

    formdef1.refresh_from_storage()
    formdef2.refresh_from_storage()
    carddef1.refresh_from_storage()
    carddef2.refresh_from_storage()
    assert formdef1.reverse_relations == []
    assert formdef2.reverse_relations == []
    assert carddef1.reverse_relations == [
        {'varname': '', 'type': 'item', 'obj': 'formdef:formdef-1'},
    ]
    assert carddef2.reverse_relations == [
        {'varname': 'computed_foobar', 'type': 'computed', 'obj': 'carddef:carddef-1'},
        {'varname': '', 'type': 'items', 'obj': 'carddef:carddef-2'},
        {'varname': 'bar', 'type': 'items', 'obj': 'formdef:formdef-2'},
    ]

    # block field
    formdef1.fields.append(BlockField(id='2', label='block', block_slug=block1.slug))
    formdef1.store()

    formdef1.refresh_from_storage()
    formdef2.refresh_from_storage()
    carddef1.refresh_from_storage()
    carddef2.refresh_from_storage()
    assert formdef1.reverse_relations == []
    assert formdef2.reverse_relations == []
    assert carddef1.reverse_relations == [
        {'varname': '', 'type': 'computed', 'obj': 'formdef:formdef-1'},
        # no varname for block field, item/formdef-1 is already in reverse_relations
        {'varname': '', 'type': 'item', 'obj': 'formdef:formdef-1'},
        {'varname': '', 'type': 'items', 'obj': 'formdef:formdef-1'},
    ]
    assert carddef2.reverse_relations == [
        {'varname': 'computed_foobar', 'type': 'computed', 'obj': 'carddef:carddef-1'},
        {'varname': '', 'type': 'items', 'obj': 'carddef:carddef-2'},
        {'varname': 'bar', 'type': 'items', 'obj': 'formdef:formdef-2'},
    ]

    formdef1.fields[2] = BlockField(id='2', label='block', block_slug=block1.slug, varname='foo')
    formdef1.store()

    formdef1.refresh_from_storage()
    formdef2.refresh_from_storage()
    carddef1.refresh_from_storage()
    carddef2.refresh_from_storage()
    assert formdef1.reverse_relations == []
    assert formdef2.reverse_relations == []
    # varname defined for block field
    assert carddef1.reverse_relations == [
        {'varname': '', 'type': 'item', 'obj': 'formdef:formdef-1'},
        {'varname': '', 'type': 'items', 'obj': 'formdef:formdef-1'},
        {'varname': 'foo_block_computed_foo_1', 'type': 'computed', 'obj': 'formdef:formdef-1'},
        {'varname': 'foo_block_foo_1', 'type': 'item', 'obj': 'formdef:formdef-1'},
    ]
    assert carddef2.reverse_relations == [
        {'varname': 'computed_foobar', 'type': 'computed', 'obj': 'carddef:carddef-1'},
        {'varname': '', 'type': 'items', 'obj': 'carddef:carddef-2'},
        {'varname': 'bar', 'type': 'items', 'obj': 'formdef:formdef-2'},
    ]

    # update blockdef fields
    block1.fields = [
        ItemField(
            id='1',
            label='item',
            varname='block_foo_1',
            data_source={'type': 'carddef:carddef-2'},
        ),
        ItemsField(id='2', label='items', data_source={'type': 'carddef:carddef-1'}),
    ]
    block1.store()

    formdef1.refresh_from_storage()
    formdef2.refresh_from_storage()
    carddef1.refresh_from_storage()
    carddef2.refresh_from_storage()
    assert formdef1.reverse_relations == []
    assert formdef2.reverse_relations == []
    # varname defined for block field
    assert carddef1.reverse_relations == [
        {'varname': '', 'type': 'item', 'obj': 'formdef:formdef-1'},
        {'varname': '', 'type': 'items', 'obj': 'formdef:formdef-1'},
    ]
    assert carddef2.reverse_relations == [
        {'varname': 'computed_foobar', 'type': 'computed', 'obj': 'carddef:carddef-1'},
        {'varname': '', 'type': 'items', 'obj': 'carddef:carddef-2'},
        {'varname': 'foo_block_foo_1', 'type': 'item', 'obj': 'formdef:formdef-1'},
        {'varname': 'bar', 'type': 'items', 'obj': 'formdef:formdef-2'},
    ]


def test_data_source_custom_view_data_access(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
        StringField(id='2', label='Test2', varname='foo2'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    pub.custom_view_class.wipe()

    custom_view = pub.custom_view_class()
    custom_view.title = 'view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {'filter-1': 'on', 'filter-1-value': 'xxx'}

    custom_view.visibility = 'datasource'
    custom_view.store()

    carddata = carddef.data_class()()
    carddata.data = {'1': 'hello world'}
    carddata.just_created()
    carddata.store()

    # no filter, there's a card
    cards = CardDef.get_data_source_items('carddef:foo')
    assert len(cards) == 1
    cards = CardDef.get_data_source_items('carddef:foo', get_by_text='hello world')
    assert len(cards) == 1

    # nothing returned as the filter doesn't match anything
    cards = CardDef.get_data_source_items('carddef:foo:view')
    assert len(cards) == 0

    # filter is ignored for id lookup
    cards = CardDef.get_data_source_items('carddef:foo:view', get_by_id=carddata.id)
    assert len(cards) == 1
    assert cards[0]['text'] == 'hello world'

    cards = CardDef.get_data_source_items('carddef:foo:view', get_by_id=carddata.get_display_id())
    assert len(cards) == 1
    assert cards[0]['text'] == 'hello world'

    # filter is not ignored for text lookup
    cards = CardDef.get_data_source_items('carddef:foo:view', get_by_text='hello world')
    assert len(cards) == 0


def test_data_source_custom_view_filtering_on_items(pub):
    CardDef.wipe()
    carddef1 = CardDef()
    carddef1.name = 'Card'
    carddef1.digest_templates = {'default': '{{form_var_foo}}'}
    carddef1.fields = [
        StringField(id='1', label='string', varname='foo'),
    ]
    carddef1.store()
    carddef1.data_class().wipe()

    carddef2 = CardDef()
    carddef2.name = 'Subcard'
    carddef2.digest_templates = {'default': '{{form_var_bar}}'}
    carddef2.fields = [
        StringField(id='1', label='string', varname='bar'),
        ItemsField(
            id='2', label='Card', varname='card', data_source={'type': 'carddef:%s' % carddef1.url_name}
        ),
    ]
    carddef2.store()
    carddef2.data_class().wipe()

    custom_view = pub.custom_view_class()
    custom_view.title = 'view'
    custom_view.formdef = carddef2
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {'filter-2': 'on', 'filter-2-value': '{{ form_var_card }}'}  # a template !
    custom_view.visibility = 'datasource'
    custom_view.store()

    carddata11 = carddef1.data_class()()
    carddata11.data = {
        '1': 'Foo 1',
    }
    carddata11.just_created()
    carddata11.store()
    carddata12 = carddef1.data_class()()
    carddata12.data = {
        '1': 'Foo 2',
    }
    carddata12.just_created()
    carddata12.store()

    carddata21 = carddef2.data_class()()
    carddata21.data = {
        '1': 'Bar 1',
        '2': [str(carddata11.id)],
        '2_display': 'Foo 1',
    }
    carddata21.just_created()
    carddata21.store()
    carddata22 = carddef2.data_class()()
    carddata22.data = {
        '1': 'Bar 1',
        '2': [str(carddata12.id)],
        '2_display': 'Foo 2',
    }
    carddata22.just_created()
    carddata22.store()

    cards = CardDef.get_data_source_items('carddef:subcard:view')
    assert len(cards) == 0


def test_card_custom_view_referenced_names(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'Card'
    carddef.digest_templates = {'default': '{{form_var_foo}}'}
    carddef.fields = [
        StringField(id='1', label='string field', varname='foo'),
    ]
    carddef.store()
    carddef.data_class().wipe()

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'formdef'
    formdef.fields = [
        StringField(id='1', label='S1', varname='plop'),
        ItemField(id='3', label='Test', data_source={'type': 'carddef:card:custom-view'}),
    ]
    formdef.store()

    pub.custom_view_class.wipe()
    custom_view = pub.custom_view_class()
    custom_view.title = 'custom view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': '0'}]}
    custom_view.visibility = 'datasource'

    for operator in ('eq', 'ne', 'lt', 'lte', 'gt', 'gte', 'between', 'absent', 'existing', 'in', 'not_in'):
        custom_view.filters = {
            'filter-1': 'on',
            'filter-1-value': '{{ form_var_plop }}',
            'filter-1-operator': operator,
        }
        custom_view.store()

        assert CardDef.get_data_source_referenced_varnames('carddef:card:custom-view', formdef) == ['plop']

    custom_view.filters = {
        'filter-1': 'on',
        'filter-1-value': 'xxx|{{ form_var_plop }}',
        'filter-1-operator': 'in',
    }
    custom_view.store()

    # additional checks, artifically created in case they happen in some circumstances
    criteria = Intersects('1', ['xxx', '{{ form_var_plop }}'])
    assert list(criteria.get_referenced_varnames(formdef)) == ['plop']

    criteria = Not(Intersects('1', ['xxx', '{{ form_var_plop }}']))
    assert list(criteria.get_referenced_varnames(formdef)) == ['plop']


def test_card_digest_error_on_globals(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_foo }} {{cards|objects:"xxx"|count}}'}
    carddef.store()
    carddef.data_class().wipe()

    LoggedError.wipe()
    carddata = carddef.data_class()()
    carddata.data = {'1': 'hello world'}
    carddata.just_created()
    carddata.store()
    assert LoggedError.count() == 1
    assert (
        LoggedError.select()[0].summary
        == 'Could not render digest (default) ("cards" is not available in digests)'
    )
    assert carddata.digests == {'default': 'ERROR'}


@pytest.mark.parametrize('ids', [('foo', 'bar', 'baz'), (4, 5, 6)])
def test_card_custom_id(pub, ids):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.digest_templates = {'default': 'card {{ form_var_foo }}'}
    carddef.id_template = '{{ form_var_foo }}'
    carddef.store()

    card_ids = {}
    for id_value in ids:
        card = carddef.data_class()()
        card.data = {'1': str(id_value)}
        card.just_created()
        card.store()
        card_ids[id_value] = str(card.id)

    for id_value in ids:
        assert str(carddef.data_class().get_by_id(id_value).id) == card_ids[id_value]

    assert card.identifier == str(ids[-1])
    pub.substitutions.reset()
    pub.substitutions.feed(card)
    context = pub.substitutions.get_context_variables(mode='lazy')
    assert context['form_identifier'] == str(ids[-1])

    context = pub.substitutions.get_context_variables(mode='lazy')
    tmpl = Template('{{ cards|objects:"foo"|filter_by_identifier:"%s"|count }}' % ids[-1])
    assert tmpl.render(context) == '1'
    tmpl = Template(
        '{{ cards|objects:"foo"|filter_by_identifier:"%s"|first|get:"form_identifier" }}' % ids[-1]
    )
    assert tmpl.render(context) == str(ids[-1])


def test_card_custom_id_draft(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.digest_templates = {'default': 'card {{ form_var_foo }}'}
    carddef.id_template = '{{ form_var_foo }}'
    carddef.store()

    card = carddef.data_class()()
    card.data = {'1': 'id1'}
    card.status = 'draft'
    card.store()

    card = carddef.data_class()()
    card.data = {'1': 'id1'}
    card.just_created()
    card.store()

    assert carddef.data_class().get_by_id('id1').id == card.id

    card.anonymise()
    with pytest.raises(KeyError):
        carddef.data_class().get_by_id('id1')


def test_card_custom_id_format(pub):
    CardDef.wipe()
    carddef = CardDef()
    carddef.name = 'foo'
    carddef.store()

    data_class = carddef.data_class()
    assert data_class.force_valid_id_characters('foobar') == 'foobar'
    assert data_class.force_valid_id_characters('Foobar') == 'Foobar'
    assert data_class.force_valid_id_characters(' Foobar') == 'Foobar'
    assert data_class.force_valid_id_characters(' Foo bar') == 'Foo-bar'
    assert data_class.force_valid_id_characters(' Fôô bar') == 'Foo-bar'
    assert data_class.force_valid_id_characters(' Fôô bar...') == 'Foo-bar'
    assert data_class.force_valid_id_characters('_Fôô bar-') == '_Foo-bar-'
    assert data_class.force_valid_id_characters('_Fôô  bar-') == '_Foo-bar-'
    assert data_class.force_valid_id_characters('_Fôô  bar☭-') == '_Foo-bar-'
    assert data_class.force_valid_id_characters('_Fôô  bar❗') == '_Foo-bar'
    assert data_class.force_valid_id_characters(' Foo\'bar') == 'Foo-bar'


def test_card_custom_id_template_error(pub):
    LoggedError.wipe()
    CardDef.wipe()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.id_template = '{{ form_var_foo }} {{ cards|objects:"..." }}'
    carddef.store()

    card = carddef.data_class()()
    card.data = {'1': 'id1'}
    card.store()
    assert LoggedError.count() == 1
    assert (
        LoggedError.select()[0].summary == 'Could not render custom id ("cards" is not available in digests)'
    )
    assert card.id_display == 'error-1-1'

    # empty result
    LoggedError.wipe()
    carddef.id_template = '{{ form_var_blah|default:"" }}'
    carddef.store()

    card = carddef.data_class()()
    card.data = {'1': 'id1'}
    card.store()
    assert LoggedError.count() == 1
    assert LoggedError.select()[0].summary == 'Custom identifier template produced an empty string'
    assert card.id_display == 'error-1-2'


def test_card_update_related(pub):
    BlockDef.wipe()
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    carddata1 = carddef.data_class()()
    carddata1.data = {'1': 'card1'}
    carddata1.just_created()
    carddata1.store()

    carddata2 = carddef.data_class()()
    carddata2.data = {'1': 'card2'}
    carddata2.just_created()
    carddata2.store()

    # check update against item field
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [
        ItemField(id='1', label='Test', data_source={'type': 'carddef:foo'}),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': '1'}
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, formdef.fields[0].id)
    assert formdata.data['1_display'] == 'card1'
    formdata.just_created()
    formdata.store()

    pub.cleanup()
    carddef = carddef.get(carddef.id)
    carddata1 = carddef.data_class().get(carddata1.id)
    carddata1.data = {'1': 'card1-change1'}
    carddata1.store()

    pub.process_after_jobs()
    formdata.refresh_from_storage()
    assert formdata.data['1_display'] == 'card1-change1'

    # check update against items field
    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [
        ItemsField(id='1', label='Test', data_source={'type': 'carddef:foo'}),
    ]
    formdef.store()
    pub.reset_caches()

    formdata = formdef.data_class()()
    formdata.data = {'1': ['1', '2']}
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, formdef.fields[0].id)
    assert formdata.data['1_display'] == 'card1-change1, card2'
    formdata.just_created()
    formdata.store()

    pub.cleanup()
    carddef = carddef.get(carddef.id)
    carddata1 = carddef.data_class().get(carddata1.id)
    carddata1.data = {'1': 'card1-change2'}
    carddata1.store()
    pub.process_after_jobs()

    formdata.refresh_from_storage()
    assert formdata.data['1_display'] == 'card1-change2, card2'

    # check update against block field
    blockdef = BlockDef()
    blockdef.name = 'foo'
    blockdef.fields = [
        ItemField(id='1', label='Test', varname='bar', data_source={'type': 'carddef:foo'}),
    ]
    blockdef.digest_template = 'bloc:{{ block_var_bar }}'
    blockdef.store()

    formdef = FormDef()
    formdef.name = 'foo2'
    formdef.fields = [
        BlockField(id='1', label='Test', block_slug=blockdef.slug),
        BlockField(id='2', label='Test2', block_slug=blockdef.slug),  # left empty
    ]
    formdef.store()
    pub.reset_caches()

    formdata = formdef.data_class()()
    formdata.data = {
        '1': {
            'data': [
                {
                    '1': '1',
                    '1_display': 'card1-change2',
                },
                {
                    '1': '2',
                    '1_display': 'card2',
                },
            ],
            'schema': {},
        }
    }

    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, formdef.fields[0].id)
    assert formdata.data['1_display'] == 'bloc:card1-change2, bloc:card2'
    formdata.just_created()
    formdata.store()

    pub.cleanup()
    carddef = carddef.get(carddef.id)
    carddata1 = carddef.data_class().get(carddata1.id)
    carddata1.data = {'1': 'card1-change3'}
    carddata1.store()
    pub.process_after_jobs()

    formdata.refresh_from_storage()
    assert formdata.data['1']['data'][0]['1'] == '1'
    assert formdata.data['1']['data'][0]['1_display'] == 'card1-change3'
    assert formdata.data['1']['data'][1]['1'] == '2'
    assert formdata.data['1']['data'][1]['1_display'] == 'card2'
    assert formdata.data['1_display'] == 'bloc:card1-change3, bloc:card2'

    # check updating relations doesn't reset substitution variables
    pub.cleanup()
    carddef = carddef.get(carddef.id)
    carddata1 = carddef.data_class().get(carddata1.id)
    carddata1.data = {'1': 'card1-change4'}
    pub.substitutions.feed(MockSubstitutionVariables())
    carddata1.store()
    pub.process_after_jobs()
    assert any(isinstance(x, MockSubstitutionVariables) for x in pub.substitutions.sources)

    formdata.refresh_from_storage()
    assert formdata.data['1']['data'][0]['1'] == '1'
    assert formdata.data['1']['data'][0]['1_display'] == 'card1-change4'
    assert formdata.data['1']['data'][1]['1'] == '2'
    assert formdata.data['1']['data'][1]['1_display'] == 'card2'
    assert formdata.data['1_display'] == 'bloc:card1-change4, bloc:card2'


def test_card_update_related_with_custom_view(pub):
    CardDef.wipe()
    FormDef.wipe()
    pub.custom_view_class.wipe()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.digest_templates = {
        'default': '{{ form_var_foo }}',
        'custom-view:view': 'view-{{ form_var_foo }}',
    }
    carddef.store()
    carddef.data_class().wipe()

    carddata1 = carddef.data_class()()
    carddata1.data = {'1': 'card1'}
    carddata1.just_created()
    carddata1.store()

    carddata2 = carddef.data_class()()
    carddata2.data = {'1': 'card2'}
    carddata2.just_created()
    carddata2.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.visibility = 'datasource'
    custom_view.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [
        ItemField(id='1', label='Test', data_source={'type': 'carddef:foo:view'}),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': '1'}
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, formdef.fields[0].id)
    assert formdata.data['1_display'] == 'view-card1'
    formdata.just_created()
    formdata.store()

    pub.cleanup()
    carddef = carddef.get(carddef.id)
    carddata1 = carddef.data_class().get(carddata1.id)
    carddata1.data = {'1': 'card1-change1'}
    carddata1.store()
    pub.process_after_jobs()

    formdata.refresh_from_storage()
    assert formdata.data['1_display'] == 'view-card1-change1'

    # change digest template
    pub.cleanup()
    carddef.digest_templates['custom-view:view'] = 'second-view-{{ form_var_foo }}'
    carddef.store()
    UpdateDigestAfterJob(formdefs=[carddef]).execute()
    pub.process_after_jobs()

    formdata.refresh_from_storage()
    assert formdata.data['1_display'] == 'second-view-card1-change1'


def test_card_update_related_with_items_dynamic_custom_view(pub):
    CardDef.wipe()
    FormDef.wipe()
    pub.custom_view_class.wipe()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
        StringField(id='2', label='Test2'),
    ]
    carddef.digest_templates = {
        'default': '{{ form_var_foo }}',
        'custom-view:view': 'view-{{ form_var_foo }}',
    }
    carddef.store()
    carddef.data_class().wipe()

    carddata1 = carddef.data_class()()
    carddata1.data = {'1': 'card1', '2': 'ok'}
    carddata1.just_created()
    carddata1.store()

    carddata2 = carddef.data_class()()
    carddata2.data = {'1': 'card2', '2': 'ok'}
    carddata2.just_created()
    carddata2.store()

    custom_view = pub.custom_view_class()
    custom_view.title = 'view'
    custom_view.formdef = carddef
    custom_view.columns = {'list': [{'id': 'id'}]}
    custom_view.filters = {}
    custom_view.filters = {'filter-2': 'on', 'filter-2-value': '{{ form_var_data }}'}
    custom_view.visibility = 'datasource'
    custom_view.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [
        StringField(id='0', label='Foo', varname='data'),
        ItemsField(id='1', label='Test', data_source={'type': 'carddef:foo:view'}),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'0': 'ok', '1': ['1']}
    formdata.data['1_display'] = 'view-card1'
    assert formdata.data['1_display'] == 'view-card1'
    formdata.just_created()
    formdata.store()

    # check usual situation, carddata changed but is still present in the result set
    pub.cleanup()
    carddef = carddef.get(carddef.id)
    carddata1 = carddef.data_class().get(carddata1.id)
    carddata1.data = {'1': 'card1-change1', '2': 'ok'}
    carddata1.store()
    pub.process_after_jobs()

    formdata.refresh_from_storage()
    assert formdata.data['1'] == ['1']
    assert formdata.data['1_display'] == 'view-card1-change1'

    # check with a card that will no longer be part of the custom view result set
    pub.cleanup()
    carddef = carddef.get(carddef.id)
    carddata1 = carddef.data_class().get(carddata1.id)
    carddata1.data = {'1': 'card1-change2', '2': 'ko'}
    carddata1.store()
    pub.process_after_jobs()

    formdata.refresh_from_storage()
    assert formdata.data['1_display'] == 'view-card1-change2'  # data is also updated


def test_card_update_related_cascading(pub):
    BlockDef.wipe()
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    carddata1 = carddef.data_class()()
    carddata1.data = {'1': 'card1'}
    carddata1.just_created()
    carddata1.store()

    carddef2 = CardDef()
    carddef2.name = 'bar'
    carddef2.fields = [
        ItemField(id='1', label='Test', varname='foo', data_source={'type': 'carddef:foo'}),
    ]
    carddef2.digest_templates = {'default': 'bar-{{ form_var_foo }}'}
    carddef2.store()
    carddef2.data_class().wipe()

    carddata2 = carddef2.data_class()()
    carddata2.data = {'1': '1'}
    carddata2.data['1_display'] = carddef2.fields[0].store_display_value(
        carddata2.data, carddef2.fields[0].id
    )
    carddata2.just_created()
    carddata2.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [
        ItemField(id='1', label='Test', data_source={'type': 'carddef:bar'}),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': '1'}
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, formdef.fields[0].id)
    assert formdata.data['1_display'] == 'bar-card1'
    formdata.just_created()
    formdata.store()

    pub.cleanup()
    carddef = carddef.get(carddef.id)
    carddata1 = carddef.data_class().get(carddata1.id)
    carddata1.data = {'1': 'card1-change1'}
    carddata1.store()
    pub.process_after_jobs()

    formdata.refresh_from_storage()
    assert formdata.data['1_display'] == 'bar-card1-change1'


def test_card_update_related_cascading_loop(pub):
    BlockDef.wipe()
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
        ItemField(id='2', label='Test', varname='x', data_source={'type': 'carddef:bar'}),
    ]
    carddef.digest_templates = {'default': '{{ form_var_foo }} {{ form_var_x }}'}
    carddef.store()
    carddef.data_class().wipe()

    carddef2 = CardDef()
    carddef2.name = 'bar'
    carddef2.fields = [
        StringField(id='1', label='Test', varname='foo'),
        ItemField(id='2', label='Test', varname='x', data_source={'type': 'carddef:foo'}),
    ]
    carddef2.digest_templates = {'default': '{{ form_var_foo }} {{ form_var_x }}'}
    carddef2.store()
    carddef2.data_class().wipe()

    carddata1 = carddef.data_class()()
    carddata1.data = {'1': 'card1'}
    carddata1.just_created()
    carddata1.store()

    carddata2 = carddef2.data_class()()
    carddata2.data = {'1': 'card2', '2': '1'}
    carddata2.data['2_display'] = carddef2.fields[1].store_display_value(
        carddata2.data, carddef2.fields[1].id
    )
    assert carddata2.data['2_display'] == 'card1 None'
    carddata2.just_created()
    carddata2.store()

    pub.cleanup()
    carddef = carddef.get(carddef.id)
    carddata1 = carddef.data_class().get(carddata1.id)
    carddata1.data['2'] = str(carddata2.id)
    carddata1.data['2_display'] = carddef.fields[1].store_display_value(carddata1.data, carddef.fields[1].id)
    carddata1.store()
    pub.process_after_jobs()

    # check it will have stopped once getting back to carddata2
    carddata2.refresh_from_storage()
    assert carddata2.data['2_display'] == 'card1 card2 card1 None'


def test_card_update_related_items_relation(pub):
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.digest_templates = {'default': '{{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    carddata1 = carddef.data_class()()
    carddata1.data = {'1': 'card1'}
    carddata1.just_created()
    carddata1.store()

    carddata2 = carddef.data_class()()
    carddata2.data = {'1': 'card2'}
    carddata2.just_created()
    carddata2.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [
        ItemField(id='1', label='Test', data_source={'type': 'carddef:foo'}),
        ItemsField(id='2', label='Test2', data_source={'type': 'carddef:foo'}),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': '1', '2': ['1', '2']}
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, formdef.fields[0].id)
    formdata.data['2_display'] = formdef.fields[1].store_display_value(formdata.data, formdef.fields[1].id)
    assert formdata.data['1_display'] == 'card1'
    assert formdata.data['2_display'] == 'card1, card2'
    formdata.just_created()
    formdata.store()

    pub.cleanup()
    carddef = carddef.get(carddef.id)
    carddata1 = carddef.data_class().get(carddata1.id)
    carddata1.data = {'1': 'card1-change1'}
    carddata1.store()
    pub.process_after_jobs()

    formdata.refresh_from_storage()
    assert formdata.data['1_display'] == 'card1-change1'
    assert formdata.data['2_display'] == 'card1-change1, card2'


def test_card_update_related_deleted(pub):
    BlockDef.wipe()
    CardDef.wipe()
    FormDef.wipe()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.fields = [
        StringField(id='1', label='Test', varname='foo'),
    ]
    carddef.digest_templates = {'default': 'card-{{ form_var_foo }}'}
    carddef.store()
    carddef.data_class().wipe()

    carddata1 = carddef.data_class()()
    carddata1.data = {'1': 'card1'}
    carddata1.just_created()
    carddata1.store()

    formdef = FormDef()
    formdef.name = 'foo'
    formdef.fields = [
        ItemField(id='1', label='Test', data_source={'type': 'carddef:foo'}),
    ]
    formdef.store()

    formdata = formdef.data_class()()
    formdata.data = {'1': '1'}
    formdata.data['1_display'] = formdef.fields[0].store_display_value(formdata.data, formdef.fields[0].id)
    assert formdata.data['1_display'] == 'card-card1'
    formdata.just_created()
    formdata.store()
    formdef.remove_self()

    pub.cleanup()
    carddef = carddef.get(carddef.id)
    carddata1 = carddef.data_class().get(carddata1.id)
    carddata1.data = {'1': 'card1-change1'}
    carddata1.store()  # do not crash looking for related formdef that has been deleted

    # check the job doesn't fail if the carddef or carddata have been removed
    job = UpdateRelationsAfterJob(carddata=carddata1)
    carddata1.remove_self()
    job.execute()

    carddef.remove_self()
    job.execute()


def test_migrate_user_support(pub):
    CardDef.wipe()

    carddef = CardDef()
    carddef.name = 'foo'
    carddef.user_support = 'optional'
    carddef.migrate()
    assert carddef.submission_user_association == 'any'


def test_slug_with_digits(pub):
    carddef = CardDef()
    carddef.name = '2 plus 2 equals 4'
    carddef.store()
    assert CardDef.get(carddef.id).name == '2 plus 2 equals 4'
    assert CardDef.get(carddef.id).url_name == 'n2-plus-2-equals-4'
    assert CardDef.get(carddef.id).table_name == f'carddata_{carddef.id}_n2_plus_2_equals_4'

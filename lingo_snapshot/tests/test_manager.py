import json
from unittest import mock

import pytest

from .utils import login

pytestmark = pytest.mark.django_db


class MockedRequestResponse(mock.Mock):
    status_code = 200

    def json(self):
        return json.loads(self.content)


def mocked_requests_send(request, **kwargs):
    data = [
        {
            'id': 1,
            'fields': {'foo': 'bar', 'bar': False, 'rate': 42, 'target': 2000, 'accounting_code': '424242'},
        },
        {
            'id': 2,
            'fields': {'foo': 'baz', 'bar': True, 'rate': 35, 'target': 3000, 'accounting_code': '353535'},
        },
    ]  # fake result
    return MockedRequestResponse(content=json.dumps({'data': data}))


def test_unlogged_access(app):
    # connect while not being logged in
    assert app.get('/manage/', status=302).location.endswith('/login/?next=/manage/')


def test_simple_user_access(app, simple_user):
    # connect while being logged as a simple user
    app = login(app, username='user', password='user')
    assert app.get('/manage/', status=403)


def test_access(app, admin_user):
    app = login(app)
    assert app.get('/manage/', status=200)


@mock.patch('requests.Session.send', side_effect=mocked_requests_send)
def test_manager_inspect_test_template(mock_send, app, admin_user):
    app = login(app)

    inspect_page = app.get('/manage/inspect/')
    form_template = inspect_page.forms[0]

    values = [
        ('bar', 'bar'),
        ('{{ 40|add:2 }}', '42'),
        ('{{ cards|objects:"card_model_1"|first|get:"id" }}', '1'),
    ]
    for template, expected_result in values:
        form_template['django_template'] = template
        resp = form_template.submit()
        assert resp.status_int == 200

        result = json.loads(resp.text)
        assert 'error' not in result
        assert result['result'] == expected_result

    values = [
        ('{% for %}', "'for' statements should have at least four words: for"),
        (
            '{{ cards|objects:"card_model_1"|filter_by_internal_id:user_external_raw_id|first|get:"id" }}',
            "Failed lookup for key [user_external_raw_id] in [{'True': True, 'False': False, 'None': None}, {}, {}]",
        ),
    ]
    for template, expected_error in values:
        form_template['django_template'] = template

        resp = form_template.submit()
        assert resp.status_int == 200

        result = json.loads(resp.text)
        print(result)
        assert 'result' not in result
        assert result['error'] == expected_error


def test_menu_json(app, admin_user):
    app.get('/manage/menu.json', status=302)  # login

    app = login(app)
    resp = app.get('/manage/menu.json')
    assert resp.headers['content-type'] == 'application/json'
    assert resp.json[0]['label'] == 'Payments'

    resp = app.get('/manage/menu.json?callback=fooBar')
    assert resp.headers['content-type'] == 'application/javascript'
    assert resp.text.startswith('fooBar([{"')

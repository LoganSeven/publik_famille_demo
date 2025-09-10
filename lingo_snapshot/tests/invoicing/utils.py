import copy
import json
import re
import urllib.parse
from unittest import mock

WCS_CARDDEFS_DATA = [
    {'title': 'Card Model 1', 'slug': 'card_model_1', 'custom_views': [{'id': 'foo', 'text': 'bar'}]},
    {'title': 'Card Model 2', 'slug': 'card_model_2'},
    {'title': 'Card Model 3', 'slug': 'card_model_3'},
]

WCS_CARDDEF_SCHEMAS = {
    'card_model_1': {
        'name': 'Card Model 1',
        'fields': [
            {'label': 'Field A', 'varname': 'fielda', 'type': 'string'},
            {'label': 'Field B', 'varname': 'fieldb', 'type': 'bool'},
            {'label': 'Comment', 'type': 'comment'},
            {'label': 'Page', 'varname': 'page', 'type': 'page'},
        ],
    },
    'card_model_2': {
        'name': 'Card Model 2',
        'fields': [
            {'label': 'Field A', 'varname': 'fielda', 'type': 'string'},
            {'label': 'Field B', 'varname': 'fieldb', 'type': 'bool'},
        ],
    },
}

WCS_CARDS_DATA = {
    'card_model_1': [
        {
            'id': 42,
            'display_id': '10-42',
            'display_name': 'Card Model 1 - n°10-42',
            'digest': 'a a a',
            'text': 'aa',
            'fields': {
                'fielda': 'foo',
                'fieldb': True,
            },
        },
    ],
    'card_model_2': [
        {
            'id': 42,
            'display_id': '10-42',
            'display_name': 'Card Model 2 - n°10-42',
            'digest': 'a a a',
            'text': 'aa',
            'fields': {
                'fielda': 'foo',
                'fieldb': False,
            },
        },
    ],
    'card_model_3': [
        {
            'id': 42,
            'display_id': '10-42',
            'display_name': 'Card Model 2 - n°10-42',
            'digest': 'foo\'bar',
            'text': 'foo\'bar',
            'fields': {
                'fielda': 'foo\'bar',
                'fieldb': False,
            },
        },
    ],
}


class MockedRequestResponse(mock.Mock):
    status_code = 200

    def json(self):
        return json.loads(self.content)


def get_data_from_url(url):
    if '/api/cards/@list' in url:
        return WCS_CARDDEFS_DATA
    m_schema = re.match(r'/api/cards/([a-z0-9_]+)/@schema', url)
    if m_schema:
        return WCS_CARDDEF_SCHEMAS.get(m_schema.group(1)) or {}
    m_list = re.match(r'/api/cards/([a-z0-9_]+)/list', url)
    if m_list:
        return WCS_CARDS_DATA.get(m_list.group(1)) or []
    return []


def mocked_requests_send(request, **kwargs):
    request_url = urllib.parse.urlparse(request.url)
    data = copy.deepcopy(get_data_from_url(request_url.path))

    if not isinstance(data, list):
        return MockedRequestResponse(content=json.dumps(data))

    return MockedRequestResponse(content=json.dumps({'data': data}))

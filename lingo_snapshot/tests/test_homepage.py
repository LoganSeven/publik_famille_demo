import pytest

pytestmark = pytest.mark.django_db


def test_homepage(app, settings):
    assert app.get('/', status=302).location == '/manage/'

    settings.TEMPLATE_VARS['portal_user_url'] = 'https://example.net/'
    assert app.get('/', status=302).location == 'https://example.net/'

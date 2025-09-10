import django_webtest
import pytest
from django.contrib.auth.models import Group, User
from django.core.cache import cache
from mellon.models import Issuer


@pytest.fixture(autouse=True)
def media(settings, tmpdir):
    settings.MEDIA_ROOT = str(tmpdir.mkdir('media'))


@pytest.fixture
def app(request):
    wtm = django_webtest.WebTestMixin()
    wtm._patch_settings()
    request.addfinalizer(wtm._unpatch_settings)
    cache.clear()
    return django_webtest.DjangoTestApp()


@pytest.fixture
def simple_user():
    user = User.objects.create_user('user', password='user', email='user@example.com')
    issuer, dummy = Issuer.objects.get_or_create(entity_id='https://idp.example.com')
    user.saml_identifiers.create(name_id='ab' * 16, issuer=issuer)
    return user


@pytest.fixture
def managers_group():
    return Group.objects.create(name='Managers')


@pytest.fixture
def manager_user(managers_group):
    user = User.objects.create_user('manager', password='manager')
    user.groups.set([managers_group])
    return user


@pytest.fixture
def admin_user():
    return User.objects.create_superuser('admin', email=None, password='admin')


@pytest.fixture
def nocache(settings):
    settings.CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.dummy.DummyCache',
        }
    }

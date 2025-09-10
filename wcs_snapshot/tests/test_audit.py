import pytest
from django.utils.timezone import now

from wcs.audit import Audit
from wcs.formdef import FormDef
from wcs.qommon import audit
from wcs.qommon.http_request import HTTPRequest

from .utilities import clean_temporary_pub, create_temporary_pub


@pytest.fixture
def pub(request):
    pub = create_temporary_pub()
    req = HTTPRequest(None, {'SCRIPT_NAME': '/', 'SERVER_NAME': 'example.net'})
    pub.set_app_dir(req)
    pub.load_site_options()
    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_audit_clean_job(pub, freezer):
    Audit.wipe()
    FormDef.wipe()
    pub.user_class.wipe()

    user = pub.user_class()
    user.name = 'Test'
    user.store()

    formdef = FormDef()
    formdef.name = 'form title'
    formdef.store()

    current_timestamp = now()
    freezer.move_to('2018-12-01T00:00:00')
    audit('listing', obj=formdef, user=user.id)
    audit('listing', obj=formdef, user=user.id)

    freezer.move_to(current_timestamp)
    audit('listing', obj=formdef, user=user.id)

    assert Audit.count() == 3
    Audit.clean()
    assert Audit.count() == 1

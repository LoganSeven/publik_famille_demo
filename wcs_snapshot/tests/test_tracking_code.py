import pytest

from wcs.formdef import FormDef
from wcs.tracking_code import TrackingCode

from .utilities import clean_temporary_pub, create_temporary_pub


@pytest.fixture
def pub(request):
    return create_temporary_pub()


def teardown_module(module):
    clean_temporary_pub()


def test_tracking_code(pub):
    TrackingCode.wipe()

    code = TrackingCode()
    code.store()
    code = TrackingCode()
    code.store()
    assert TrackingCode.count() == 2

    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'foobar'
    formdef.fields = []
    formdef.store()
    formdata = formdef.data_class()()
    formdata.store()

    code = TrackingCode.get(code.id)
    code.formdef = formdef
    code.formdata = formdata
    assert code.formdef_id == str(formdef.id)
    assert code.formdata_id == str(formdata.id)
    code.store()
    assert TrackingCode.count() == 2

    assert TrackingCode.get(code.id).formdef_id == code.formdef_id
    assert TrackingCode.get(code.id).formdata_id == code.formdata_id

    assert TrackingCode.get(code.id).formdata.tracking_code == code.id


def test_tracking_code_duplicate(pub):
    TrackingCode.wipe()

    code = TrackingCode()
    code.store()
    code_id = code.id

    code = TrackingCode()
    real_get_new_id = TrackingCode.get_new_id

    marker = {}

    def fake_get_new_id(cls):
        if not hasattr(cls, 'cnt'):
            cls.cnt = 0
        cls.cnt += 1
        if cls.cnt < 5:
            return code_id

        marker['done'] = True
        return real_get_new_id(cls)

    TrackingCode.get_new_id = fake_get_new_id
    code.store()
    TrackingCode.get_new_id = real_get_new_id

    assert marker.get('done')  # makes sure we got to the real new id code
    assert TrackingCode.count() == 2

import pytest

from wcs import fields

from ..utilities import clean_temporary_pub, create_temporary_pub, get_app
from .test_all import create_formdef


def pytest_generate_tests(metafunc):
    if 'pub' in metafunc.fixturenames:
        metafunc.parametrize('pub', ['sql', 'sql-lazy'], indirect=True)


@pytest.fixture
def pub(request):
    pub = create_temporary_pub(lazy_mode=bool('lazy' in request.param))
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()
    return pub


def teardown_module(module):
    clean_temporary_pub()


def test_form_numeric_field(pub):
    formdef = create_formdef()
    formdef.fields = [
        fields.NumericField(id='1', label='number'),
    ]
    formdef.store()
    formdef.data_class().wipe()

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f1'].value = '10'

    resp = resp.forms[0].submit('submit')  # -> validation
    assert 'Check values then click submit.' in resp.text
    assert resp.pyquery('#form_f1').attr.value == '10'

    resp = resp.forms[0].submit('submit').follow()  # -> submit
    assert 'The form has been recorded' in resp.text
    assert formdef.data_class().count() == 1
    data_id = formdef.data_class().select()[0].id
    data = formdef.data_class().get(data_id)
    assert data.data == {'1': 10}

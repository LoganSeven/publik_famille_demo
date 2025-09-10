import hashlib
import json
import os
from unittest import mock

import pytest
from django.utils.encoding import force_bytes
from webtest import Upload

from wcs import fields
from wcs.audit import Audit
from wcs.formdef import FormDef
from wcs.qommon.ident.password_accounts import PasswordAccount
from wcs.sql import Equal
from wcs.wf.register_comment import RegisterCommenterWorkflowStatusItem

from .utilities import clean_temporary_pub, create_temporary_pub, get_app, login


def pytest_generate_tests(metafunc):
    if 'pub' in metafunc.fixturenames:
        metafunc.parametrize('pub', ['sql', 'sql-lazy'], indirect=True)


@pytest.fixture
def pub(request, emails):
    pub = create_temporary_pub(
        lazy_mode=bool('lazy' in request.param),
    )
    pub.cfg['identification'] = {'methods': ['password']}
    pub.cfg['language'] = {'language': 'en'}
    pub.write_cfg()
    with open(os.path.join(pub.app_dir, 'site-options.cfg'), 'w') as fd:
        fd.write(
            '''
[storage-remote]
label = remote storage
class = wcs.qommon.upload_storage.RemoteOpaqueUploadStorage
ws = https://crypto.example.net/ws1/

[storage-remote-bo]
label = remote storage backoffice only
class = wcs.qommon.upload_storage.RemoteOpaqueUploadStorage
ws = https://crypto.example.net/ws2/
frontoffice_redirect = false

[api-secrets]
crypto.example.net = 1234

[wscall-secrets]
crypto.example.net = 1234
'''
        )
    return pub


def teardown_module(module):
    clean_temporary_pub()


def create_formdef():
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.FileField(id='0', label='file', varname='file'),
        fields.FileField(id='1', label='remote file', varname='remote_file'),
    ]
    formdef.store()
    return formdef


def create_user_and_admin(pub):
    pub.user_class.wipe()
    PasswordAccount.wipe()

    user = pub.user_class()
    user.email = 'foo@localhost'
    user.store()
    account = PasswordAccount(id='foo')
    account.set_password('foo')
    account.user_id = user.id
    account.store()

    admin = pub.user_class()
    admin.email = 'admin@localhost'
    admin.is_admin = True
    admin.store()
    account = PasswordAccount(id='admin')
    account.set_password('admin')
    account.user_id = admin.id
    account.store()
    return user, admin


@mock.patch('wcs.wscalls.call_webservice')
def test_form_file_field_upload_storage(wscall, pub):
    create_user_and_admin(pub)
    formdef = create_formdef()
    formdef.data_class().wipe()

    assert formdef.fields[0].storage == formdef.fields[1].storage == 'default'

    assert 'remote' in pub.get_site_storages()

    formdef.fields[1].storage = 'remote'
    formdef.store()
    assert formdef.fields[0].storage == 'default'
    assert formdef.fields[1].storage == 'remote'

    wscall.return_value = (
        None,
        200,
        json.dumps({'err': 0, 'data': {'redirect_url': 'https://crypto.example.net/'}}),
    )

    with open(os.path.join(os.path.dirname(__file__), 'image-with-gps-data.jpeg'), 'rb') as fd:
        image_content = fd.read()

    upload_0 = Upload('file.jpg', image_content, 'image/jpeg')
    upload_1 = Upload('remote.jpg', image_content, 'image/jpeg')

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0$file'] = upload_0
    resp.forms[0]['f1$file'] = upload_1
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    assert 'download?f=0&thumbnail=1' in resp.text
    assert 'download?f=1&thumbnail=1' not in resp.text  # no thumbnail for remote storage
    assert 'href="download?f=0"' in resp.text
    assert 'href="download?f=1"' in resp.text

    resp = resp.click('remote.jpg')
    assert resp.location.startswith('https://crypto.example.net/')
    assert '&signature=' in resp.location

    # no links, via webservice
    wscall.return_value = (
        None,
        200,
        json.dumps(
            {
                'err': 0,
                'data': {
                    'redirect_url': 'https://crypto.example.net/',
                    'backoffice_redirect_url': None,
                    'frontoffice_redirect_url': None,
                },
            }
        ),
    )
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0$file'] = upload_0
    resp.forms[0]['f1$file'] = upload_1
    resp = resp.forms[0].submit('submit')
    assert resp.click('file.jpg').status_code == 200
    with pytest.raises(IndexError):
        resp.click('remote.jpg')
    resp = resp.forms[0].submit('submit')
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert 'download?f=0&thumbnail=1' in resp.text
    assert 'download?f=1&thumbnail=1' not in resp.text
    assert 'href="download?f=0"' in resp.text
    assert 'href="download?f=1"' not in resp.text  # no link on frontoffice
    admin_app = login(get_app(pub), username='admin', password='admin')
    resp = admin_app.get('/backoffice/management/test/2/')
    assert 'download?f=0&thumbnail=1' in resp.text
    assert 'download?f=1&thumbnail=1' not in resp.text
    assert 'href="download?f=0"' in resp.text
    assert 'href="download?f=1"' not in resp.text  # no link on backoffice
    admin_app.get('/backoffice/management/test/2/download?f=1', status=404)  # cannot access

    # link only on backoffice, via site-options
    formdef.fields[1].storage = 'remote-bo'
    formdef.store()
    wscall.return_value = (
        None,
        200,
        json.dumps({'err': 0, 'data': {'redirect_url': 'https://crypto.example.net/'}}),
    )
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0$file'] = upload_0
    resp.forms[0]['f1$file'] = upload_1
    resp = resp.forms[0].submit('submit')
    resp = resp.forms[0].submit('submit')
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text
    assert 'download?f=0&thumbnail=1' in resp.text
    assert 'download?f=1&thumbnail=1' not in resp.text  # no thumbnail for remote storage
    assert 'href="download?f=0"' in resp.text
    assert 'href="download?f=1"' not in resp.text  # no link on frontoffice
    # go to backoffice
    resp = admin_app.get('/backoffice/management/test/3/')
    assert 'download?f=0&thumbnail=1' in resp.text
    assert 'download?f=1&thumbnail=1' not in resp.text  # no thumbnail for remote storage
    assert 'href="download?f=0"' in resp.text
    assert 'href="download?f=1"' in resp.text  # link is present on backoffice

    # check access is recorded
    Audit.wipe()
    resp = resp.click('remote.jpg')
    assert resp.status_code == 302
    assert Audit.count([Equal('action', 'redirect remote stored file')]) == 1

    # file size limit verification
    formdef.fields[1].max_file_size = '1ko'
    formdef.store()
    wscall.return_value = (
        None,
        200,
        json.dumps({'err': 0, 'data': {'redirect_url': 'https://crypto.example.net/'}}),
    )
    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0$file'] = upload_0
    resp.forms[0]['f1$file'] = upload_1
    resp = resp.forms[0].submit('submit')
    assert 'over file size limit (1ko)' in resp.text

    # api access (json export)
    resp = admin_app.get('/api/forms/test/1/', status=200)
    assert resp.json['fields']['file']['content'].startswith('/9j/4AAQSkZJRg')
    assert 'storage' not in resp.json['fields']['file']
    assert resp.json['fields']['remote_file']['content'] == ''
    assert resp.json['fields']['remote_file']['storage'] == 'remote'
    assert resp.json['fields']['remote_file']['storage_attrs'] == {
        'redirect_url': 'https://crypto.example.net/',
        'file_size': 1834,
    }

    resp = admin_app.get('/api/forms/test/2/', status=200)
    assert resp.json['fields']['remote_file']['content'] == ''
    assert resp.json['fields']['remote_file']['storage'] == 'remote'
    assert resp.json['fields']['remote_file']['storage_attrs'] == {
        'redirect_url': 'https://crypto.example.net/',
        'frontoffice_redirect_url': None,
        'backoffice_redirect_url': None,
        'file_size': 1834,
    }

    resp = admin_app.get('/api/forms/test/3/', status=200)
    assert resp.json['fields']['remote_file']['content'] == ''
    assert resp.json['fields']['remote_file']['storage'] == 'remote-bo'
    assert resp.json['fields']['remote_file']['storage_attrs'] == {
        'redirect_url': 'https://crypto.example.net/',
        'file_size': 1834,
    }


def test_thumbnail_caching(pub):
    create_user_and_admin(pub)
    FormDef.wipe()
    formdef = FormDef()
    formdef.name = 'test'
    formdef.fields = [
        fields.FileField(id='0', label='file', varname='file'),
    ]
    formdef.store()

    assert formdef.fields[0].storage == 'default'

    with open(os.path.join(os.path.dirname(__file__), 'image-with-gps-data.jpeg'), 'rb') as fd:
        image_content = fd.read()
    upload = Upload('file.jpg', image_content, 'image/jpeg')

    resp = get_app(pub).get('/test/')
    resp.forms[0]['f0$file'] = upload
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    data_file = formdef.data_class().get(1).data['0']
    thumbs_dir = os.path.join(pub.app_dir, 'thumbs')
    thumb_filepath = os.path.join(
        thumbs_dir, hashlib.sha256(force_bytes(data_file.get_fs_filename())).hexdigest()
    )
    assert os.path.exists(thumbs_dir) is False
    assert os.path.exists(thumb_filepath) is False
    admin_app = login(get_app(pub), username='admin', password='admin')
    admin_app.get('/backoffice/management/test/1/download?f=0&thumbnail=1').follow()
    assert os.path.exists(thumbs_dir) is True
    assert os.path.exists(thumb_filepath) is True
    # again, thumbs_dir already exists
    admin_app.get('/backoffice/management/test/1/download?f=0&thumbnail=1').follow()


@mock.patch('wcs.wscalls.call_webservice')
def test_remoteopaque_in_attachmentevolutionpart(wscall, pub):
    create_user_and_admin(pub)
    formdef = create_formdef()
    formdef.fields[1].storage = 'remote-bo'
    formdef.store()
    formdef.data_class().wipe()

    wscall.return_value = (
        None,
        200,
        json.dumps({'err': 0, 'data': {'redirect_url': 'https://crypto.example.net/'}}),
    )

    with open(os.path.join(os.path.dirname(__file__), 'image-with-gps-data.jpeg'), 'rb') as fd:
        image_content = fd.read()
    upload_0 = Upload('local-file.jpg', image_content, 'image/jpeg')
    upload_1 = Upload('remote-file.jpg', image_content, 'image/jpeg')

    user_app = login(get_app(pub), username='foo', password='foo')
    admin_app = login(get_app(pub), username='admin', password='admin')

    resp = user_app.get('/test/')
    resp.forms[0]['f0$file'] = upload_0
    resp.forms[0]['f1$file'] = upload_1
    resp = resp.forms[0].submit('submit')
    assert 'Check values then click submit.' in resp.text
    resp = resp.forms[0].submit('submit')
    assert resp.status_int == 302
    resp = resp.follow()
    assert 'The form has been recorded' in resp.text

    # register a comment = create a AttachmentEvolutionPart
    formdata = formdef.data_class().select()[0]
    item = RegisterCommenterWorkflowStatusItem()
    item.attachments = ['{{form_var_file_raw}}', '{{form_var_remote_file_raw}}']
    item.comment = 'text in form history'
    item.perform(formdata)

    # links on frontoffice: no link to remote file
    resp = user_app.get('/test/%s/' % formdata.id)
    assert resp.text.count('<p class="wf-attachment"><a href="attachment?f=') == 1
    assert resp.text.count('<p class="wf-attachment"><a href="attachment?f=uuid-') == 0
    # links on backoffice: links to local and remote file
    resp = admin_app.get('/backoffice/management/test/%s/' % formdata.id)
    assert resp.text.count('<p class="wf-attachment"><a href="attachment?f=') == 2
    assert resp.text.count('<p class="wf-attachment"><a href="attachment?f=uuid-') == 1

    local_file = formdata.evolution[-1].parts[0]
    local_file_id = os.path.basename(local_file.filename)
    remote_file = formdata.evolution[-1].parts[1]
    remote_file_id = remote_file.filename
    assert not local_file_id.startswith('uuid-')
    assert remote_file_id.startswith('uuid-')

    # clic on remote file in frontoffice: redirect... but forbidden
    resp = user_app.get('/test/%s/attachment?f=%s' % (formdata.id, remote_file_id))
    assert resp.status_int == 302
    resp = resp.follow(status=404)
    # click in backoffice, redirect to decryption system
    Audit.wipe()
    resp = admin_app.get('/backoffice/management/test/%s/attachment?f=%s' % (formdata.id, remote_file_id))
    assert resp.status_int == 302
    resp = resp.follow()
    assert resp.location.startswith('https://crypto.example.net/')
    assert '&signature=' in resp.location
    # check access is recorded
    assert Audit.count([Equal('action', 'redirect remote stored file')]) == 1

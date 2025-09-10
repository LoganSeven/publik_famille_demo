# w.c.s. - web application for online forms
# Copyright (C) 2005-2020  Entr'ouvert
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

import base64
import io
import json
import os

from django.utils.encoding import force_str
from django.utils.module_loading import import_string
from quixote import get_publisher
from quixote.http_request import Upload

from wcs.clamd_file import PickableClamD

from .errors import ConnectionError
from .misc import Image, can_thumbnail, file_digest
from .storage import atomic_write


class PicklableUpload(Upload, PickableClamD):
    def __init__(self, orig_filename, content_type=None, charset=None):
        if orig_filename:
            orig_filename = orig_filename.strip()
        super().__init__(orig_filename, content_type=content_type, charset=charset)

    def __getstate__(self):
        odict = self.__dict__.copy()
        if 'fp' in odict:
            del odict['fp']
        get_storage_object(getattr(self, 'storage', None)).save(self)
        odict['qfilename'] = getattr(self, 'qfilename', None)
        return odict

    def get_file_pointer(self):
        if 'fp' in self.__dict__ and self.__dict__.get('fp') is not None:
            return self.__dict__.get('fp')
        if getattr(self, 'qfilename', None):
            basedir = os.path.join(get_publisher().app_dir, 'uploads')
            self.fp = open(os.path.join(basedir, self.qfilename), 'rb')  # pylint: disable=consider-using-with
            return self.fp
        return None

    def close(self):
        fp = getattr(self, 'fp', None)
        if fp and not isinstance(fp.name, int):
            # file can be recreated we can close it
            fp.close()

    def __setstate__(self, dict):
        self.__dict__.update(dict)
        if hasattr(self, 'data'):
            # backward compatibility with older w.c.s. version
            self.fp = io.BytesIO(self.data)
            del self.data

    def file_digest(self):
        if getattr(self, 'qfilename', None):
            # last file part is created using misc.file_digest()
            return self.qfilename.split('/')[-1]
        return None

    def get_file(self):
        # quack like UploadedFile
        return self.get_file_pointer()

    def get_fs_filename(self):
        if not hasattr(self, 'qfilename'):
            return None
        if hasattr(self, 'storage_attrs'):  # alternative storage
            return None
        basedir = os.path.join(get_publisher().app_dir, 'uploads')
        return os.path.join(basedir, self.qfilename)

    def get_file_path(self):
        return self.get_fs_filename()

    def get_file_size(self):
        filename = self.get_fs_filename()
        return os.stat(filename).st_size if filename else None

    def get_content(self):
        if hasattr(self, 'storage_attrs'):  # alternative storage
            return b''
        if hasattr(self, 'qfilename'):
            filename = os.path.join(get_publisher().app_dir, 'uploads', self.qfilename)
            with open(filename, 'rb') as fd:
                return fd.read()
        if self.fp:
            get_storage_object(getattr(self, 'storage', None)).save(self)
            return self.get_content()
        return None

    def get_base64_content(self):
        content = self.get_content()
        if content:
            return base64.encodebytes(content)
        return b''

    def get_json_value(self, include_file_content=True):
        return get_storage_object(getattr(self, 'storage', None)).get_json_value(
            self, include_file_content=include_file_content
        )

    def can_thumbnail(self):
        return get_storage_object(getattr(self, 'storage', None)).can_thumbnail(self)

    def has_redirect_url(self):
        return get_storage_object(getattr(self, 'storage', None)).has_redirect_url(self)

    def get_redirect_url(self, backoffice=False):
        return get_storage_object(getattr(self, 'storage', None)).get_redirect_url(
            self, backoffice=backoffice
        )

    def strip_metadata(self):
        if Image is None:
            return self
        try:
            image = Image.open(io.BytesIO(self.get_content()))
        except OSError:
            return self

        image_without_exif = Image.new(image.mode, image.size)
        image_without_exif.putdata(image.getdata())
        if image.mode == 'P':
            image_without_exif.putpalette(image.getpalette())
        content = io.BytesIO()
        image_without_exif.save(content, image.format)
        new_file = PicklableUpload(self.base_filename, self.content_type)
        new_file.receive([content.getvalue()])
        return new_file

    def __eq__(self, other):
        for attr in ('orig_filename', 'base_filename', 'content_type', 'charset'):
            if getattr(self, attr, None) != getattr(other, attr, None):
                return False
        if self.file_digest() and self.file_digest() == other.file_digest():
            return True
        return bool(self.get_content() == other.get_content())

    def get(self, key):  # for |getlist
        assert key in ('file_digest',)
        return getattr(self, key, None)()


class UploadStorageError(Exception):
    pass


class UploadStorage:
    def save_tempfile(self, upload):
        upload.__class__ = PicklableUpload
        dirname = os.path.join(get_publisher().app_dir, 'tempfiles')
        filename = os.path.join(dirname, upload.token)
        with open(filename, 'wb') as fd:
            upload.get_file_pointer().seek(0)
            fd.write(upload.get_file_pointer().read())
            upload.size = fd.tell()

    def get_tempfile(self, temp_data):
        value = PicklableUpload(temp_data['orig_filename'], temp_data['content_type'], temp_data['charset'])
        value.storage = temp_data.get('storage')
        dirname = os.path.join(get_publisher().app_dir, 'tempfiles')
        filename = os.path.join(dirname, temp_data['unsigned_token'])
        value.token = temp_data['token']
        value.file_size = os.path.getsize(filename)
        value.fp = open(filename, 'rb')  # pylint: disable=consider-using-with
        return value

    def save(self, upload):
        basedir = os.path.join(get_publisher().app_dir, 'uploads')
        if not os.path.exists(basedir):
            os.mkdir(basedir)
        if getattr(upload, 'qfilename', None):
            filepath = os.path.join(basedir, upload.qfilename)
        else:
            if upload.fp.closed:
                assert not isinstance(upload.fp.name, int)
                upload.fp = open(upload.fp.name, 'rb')  # pylint: disable=consider-using-with
            upload.qfilename = file_digest(upload.fp)
            filepath = os.path.join(basedir, upload.qfilename)

        if getattr(upload, 'fp', None) and not upload.fp.closed:
            upload.fp.seek(0)
            atomic_write(filepath, upload.fp)
            upload.fp.close()
            upload.fp = None

    def get_json_value(self, upload, include_file_content=True):
        value = {
            'filename': upload.base_filename,
            'content_type': upload.content_type or 'application/octet-stream',
        }
        if include_file_content:
            value['content'] = force_str(base64.b64encode(upload.get_content()))
            value['content_is_base64'] = True
        return value

    def can_thumbnail(self, upload):
        return can_thumbnail(upload.content_type)

    def has_redirect_url(self, upload):
        return False

    def get_redirect_url(self, upload, backoffice=False):
        # should never be called, has_redirect_url is False
        raise AssertionError('no get_redirect_url on UploadStorage object')


class RemoteOpaqueUploadStorage:
    def __init__(self, ws, frontoffice_redirect='true', backoffice_redirect='true', **kwargs):
        self.ws = ws
        self.frontoffice_redirect = bool(frontoffice_redirect == 'true')
        self.backoffice_redirect = bool(backoffice_redirect == 'true')

    def file_digest(self):
        return None

    def save_tempfile(self, upload):
        if getattr(upload, 'storage_attrs', None):
            # upload is already a remote PicklableUpload, it does not
            # have content. We are certainly restoring a draft.
            return

        upload.__class__ = PicklableUpload
        upload.get_file_pointer().seek(0)
        content = upload.get_file_pointer().read()
        base64content = base64.b64encode(content)
        file_size = len(content)

        post_data = {
            'file': {
                'filename': upload.base_filename,
                'content_type': upload.content_type or 'application/octet-stream',
                'content': base64content,
            }
        }
        try:
            from wcs.wscalls import call_webservice

            dummy, status, data = call_webservice(self.ws, method='POST', post_data=post_data)
        except ConnectionError as e:
            raise UploadStorageError('remote storage connection error (%r)' % e)
        if status not in (200, 201):
            raise UploadStorageError('remote storage returned status %s' % status)
        try:
            ws_result = json.loads(data)
        except (ValueError, TypeError):
            raise UploadStorageError('remote storage returned invalid JSON')
        if not isinstance(ws_result, dict):
            raise UploadStorageError('remote storage returned non-dict JSON')
        if ws_result.get('err') != 0:
            raise UploadStorageError('remote storage returned err = %s' % ws_result.get('err'))
        ws_result_data = ws_result.get('data', {})
        if not ws_result_data.get('redirect_url'):
            raise UploadStorageError(
                'remote storage returned data.redirect_url= %s' % ws_result_data.get('redirect_url')
            )

        upload.storage_attrs = ws_result_data
        upload.storage_attrs['file_size'] = file_size

    def get_tempfile(self, temp_data):
        value = PicklableUpload(temp_data['orig_filename'], temp_data['content_type'], temp_data['charset'])
        value.storage = temp_data.get('storage')
        value.storage_attrs = temp_data['storage-attrs']
        value.token = temp_data['token']
        value.fp = None
        value.file_size = value.storage_attrs['file_size']
        return value

    def save(self, upload):
        pass

    def get_json_value(self, upload, include_file_content=True):
        value = {
            'filename': upload.base_filename,
            'content_type': upload.content_type or 'application/octet-stream',
            'storage': upload.storage,
            'storage_attrs': upload.storage_attrs,
        }
        if include_file_content:  # actually not possible
            value['content'] = ''
        return value

    def can_thumbnail(self, upload):
        return False

    def has_redirect_url(self, upload):
        return True

    def get_redirect_url(self, upload, backoffice=False):
        if backoffice:
            if not self.backoffice_redirect:
                return None
            if 'backoffice_redirect_url' in upload.storage_attrs:
                return upload.storage_attrs['backoffice_redirect_url']
        else:
            if not self.frontoffice_redirect:
                return None
            if 'frontoffice_redirect_url' in upload.storage_attrs:
                return upload.storage_attrs['frontoffice_redirect_url']
        return upload.storage_attrs.get('redirect_url')


def get_storage_object(storage):
    if not storage or storage == 'default':
        return UploadStorage()
    storage_cfg = get_publisher().get_site_storages().get(storage)
    if not storage_cfg:
        raise UploadStorageError('unknown storage %s' % storage)
    try:
        storage_class = import_string(storage_cfg['class'])
    except ImportError:
        raise UploadStorageError('failed to import storage class %s' % storage_cfg['class'])
    return storage_class(**storage_cfg)

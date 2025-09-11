# authentic2 - versatile identity manager
# Copyright (C) 2010-2019 Entr'ouvert
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License as published
# by the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import datetime
import threading

import pytest
from django.db import connection, transaction

from authentic2.a2_rbac.models import Role
from authentic2.custom_user.models import User
from authentic2.models import Attribute, Lock, Service
from authentic2.utils.misc import ServiceAccessDenied


def test_attribute_disabled(db):
    attribute = Attribute.all_objects.create(name='test', label='test', kind='string')
    user = User.objects.create()
    user.attributes.test = 'abcd'

    assert user.to_json()['test'] == 'abcd'
    attribute.disabled = True
    attribute.save()
    assert 'test' not in user.to_json()

    with pytest.raises(AttributeError):
        assert user.attributes.test == 'abcd'

    with pytest.raises(AttributeError):
        user.attributes.test = '1234'


def test_service_authorize(db):
    service = Service.objects.create(name='foo', slug='foo')
    role = Role.objects.create(name='foo')
    service.authorized_roles.add(role)

    user = User.objects.create()
    with pytest.raises(ServiceAccessDenied):
        service.authorize(user)

    user.is_superuser = True
    user.save()
    assert service.authorize(user)


class TestLock:
    def test_wait(self, transactional_db):
        with pytest.raises(transaction.TransactionManagementError):
            Lock.lock('a')

        with transaction.atomic():
            Lock.lock('a')

        count = 50
        l1 = ['a'] * count
        l2 = []

        def f():
            import random

            for _ in range(count):
                with transaction.atomic():
                    locks = ['a', 'b']
                    random.shuffle(locks)
                    Lock.lock(*locks)
                    if l1:
                        l2.append(l1.pop())
            l2.append('x')
            connection.close()

        thread1 = threading.Thread(target=f)
        thread2 = threading.Thread(target=f)
        thread1.start()
        thread2.start()
        thread1.join()
        thread2.join()

        assert len(l1) == 0
        assert len(l2) == count + 2

    def test_nowait(self, transactional_db):
        barrier1 = threading.Barrier(2)
        barrier2 = threading.Barrier(2)

        # prevent contention on the unique index lock
        Lock.objects.create(name='lock-name')

        def locker():
            try:
                with transaction.atomic():
                    Lock.lock('lock-name')
                    barrier1.wait()
                    barrier2.wait()
            finally:
                connection.close()

        exception = None

        def locker_nowait():
            nonlocal exception

            try:
                with transaction.atomic():
                    barrier1.wait()
                    try:
                        Lock.lock('lock-name', nowait=True)
                    except Lock.Error as e:
                        exception = e
            finally:
                barrier2.wait()
                connection.close()

        locker_thread = threading.Thread(target=locker)
        locker_nowait_thread = threading.Thread(target=locker_nowait)
        locker_thread.start()
        locker_nowait_thread.start()
        locker_thread.join()
        locker_nowait_thread.join()
        assert exception is not None

    def test_clean(self, transactional_db):
        import time
        import uuid

        count = 0

        def take_locks():
            nonlocal count
            for _ in range(100):
                with transaction.atomic():
                    name = str(uuid.uuid4())
                    Lock.lock(name)
                    time.sleep(0.01)
                    assert Lock.objects.get(name=name)
                    count += 1
            connection.close()

        thread1 = threading.Thread(target=take_locks)
        thread1.start()

        def clean():
            while thread1.is_alive():
                time.sleep(0.001)
                Lock.cleanup(age=datetime.timedelta(seconds=0))
            connection.close()

        thread2 = threading.Thread(target=clean)
        thread2.start()
        thread1.join()
        thread2.join()
        Lock.cleanup(age=datetime.timedelta(seconds=0))
        assert Lock.objects.count() == 0
        assert count == 100

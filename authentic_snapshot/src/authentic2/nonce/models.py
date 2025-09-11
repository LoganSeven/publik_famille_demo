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

from django.db import models
from django.utils import timezone

__all__ = ('Nonce',)

_NONCE_LENGTH_CONSTANT = 256


class NonceManager(models.Manager):
    def cleanup(self, now=None):
        now = now or timezone.now()
        self.filter(not_on_or_after__lt=now).delete()


class Nonce(models.Model):
    value = models.CharField(max_length=_NONCE_LENGTH_CONSTANT)
    context = models.CharField(max_length=_NONCE_LENGTH_CONSTANT, blank=True, null=True)
    not_on_or_after = models.DateTimeField(blank=True, null=True)

    objects = NonceManager()

    def __str__(self):
        return str(self.value)

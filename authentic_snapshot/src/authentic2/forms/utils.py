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

from django import forms

from authentic2.utils.misc import good_next_url


class FormNeedsRequest:
    def __init__(self, *args, **kwargs):
        self.request = kwargs.pop('request')
        super().__init__(*args, **kwargs)


class NextUrlFormMixin(FormNeedsRequest, forms.Form):
    '''Use with authentic.cbv.NextUrlViewMixin.'''

    next_url = forms.CharField(widget=forms.HiddenInput(), required=False)

    def clean_next_url(self):
        next_url = self.cleaned_data.get('next_url')
        if not good_next_url(self.request, next_url):
            return ''
        return next_url

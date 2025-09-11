# authentic2 - versatile identity manager
# Copyright (C) 2010-2020 Entr'ouvert
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

from django.conf import settings
from django.contrib import messages
from django.forms.forms import NON_FIELD_ERRORS
from django.forms.utils import ErrorList
from django.utils.translation import gettext as _


def check_cookie_works(request):
    """Verify the test cookie is set, if not log a message for the user explaining the problem.

    It should only be used in views in which we are sure of coming from the login page.

    For authentication sources able to do IdP initiated SSOs, please do not use.
    """
    if not request.COOKIES:
        messages.warning(
            request,
            _(
                'Cookies are disabled in your browser, please activate them or you will not be able to'
                ' log in.'
            ),
        )
        return False
    else:
        return True


def csrf_token_check(request, form):
    """Check a request for CSRF cookie validation, and add an error to the form
    if check fails.
    """
    # allow tests to disable csrf check
    if (
        form.is_valid()
        and not getattr(request, 'csrf_processing_done', False)
        and 'django.middleware.csrf.CsrfViewMiddleware' in settings.MIDDLEWARE
    ):
        msg = _('The form was out of date, please try again.')
        form._errors[NON_FIELD_ERRORS] = ErrorList([msg])
        check_cookie_works(request)

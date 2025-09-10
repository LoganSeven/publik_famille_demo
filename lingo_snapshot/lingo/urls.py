# lingo - payment and billing system
# Copyright (C) 2022  Entr'ouvert
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
from django.conf.urls.static import static
from django.contrib.auth.decorators import login_required
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.urls import include, path, re_path

from .api.urls import urlpatterns as lingo_api_urls
from .basket.urls import public_urlpatterns as lingo_basket_public_urls
from .epayment.urls import manager_urlpatterns as lingo_epayment_manager_urls
from .epayment.urls import public_urlpatterns as lingo_epayment_public_urls
from .export_import import urls as export_import_urls
from .invoicing.urls import urlpatterns as lingo_invoicing_urls
from .manager.urls import urlpatterns as lingo_manager_urls
from .pricing.urls import urlpatterns as lingo_pricing_urls
from .urls_utils import decorated_includes
from .views import homepage, login, logout

urlpatterns = [
    path('', homepage, name='homepage'),
    re_path(r'^manage/', decorated_includes(login_required, include(lingo_manager_urls))),
    re_path(r'^manage/invoicing/', decorated_includes(login_required, include(lingo_invoicing_urls))),
    re_path(r'^manage/pricing/', decorated_includes(login_required, include(lingo_pricing_urls))),
    re_path(r'^manage/epayment/', decorated_includes(login_required, include(lingo_epayment_manager_urls))),
    path('basket/', include(lingo_basket_public_urls)),
    path('api/', include(lingo_api_urls)),
    path('api/', include(export_import_urls)),
    path('login/', login, name='auth_login'),
    path('logout/', logout, name='auth_logout'),
    path('', include(lingo_epayment_public_urls)),
]

if 'mellon' in settings.INSTALLED_APPS:
    urlpatterns.append(
        path(
            'accounts/mellon/',
            include('mellon.urls'),
            kwargs={
                'template_base': 'lingo/mellon_base_template.html',
            },
        )
    )

# static and media files
urlpatterns += staticfiles_urlpatterns()
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

if settings.DEBUG and 'debug_toolbar' in settings.INSTALLED_APPS:
    import debug_toolbar  # pylint: disable=import-error

    urlpatterns = [
        path('__debug__/', include(debug_toolbar.urls)),
    ] + urlpatterns

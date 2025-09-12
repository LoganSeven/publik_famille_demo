# publik_famille_demo/urls.py
from django.contrib import admin
from django.urls import path, include
from django.views.generic import TemplateView
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path('families/', include('families.urls')),
    path('activities/', include('activities.urls')),
    path('billing/', include('billing.urls')),
    path('documents/', include('documents.urls')),
    path('monitoring/', include('monitoring.urls')),
    path('', TemplateView.as_view(template_name='home.html'), name='home'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

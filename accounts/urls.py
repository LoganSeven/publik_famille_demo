# accounts/urls.py
from django.urls import path, include
from .views import signup, logout_view
from . import views_identity

urlpatterns = [
    path('signup/', signup, name='signup'),
    path('logout/', logout_view, name='logout'),
    # identité
    path('verify/', views_identity.verify_identity, name='accounts_verify_identity'),
    path('verify/start/', views_identity.verify_start, name='accounts_verify_start'),
    path('verify/callback/', views_identity.verify_callback, name='accounts_verify_callback'),
    # inclut les vues d’authentification standard de Django (login, reset, etc.)
    path('', include('django.contrib.auth.urls')),
]

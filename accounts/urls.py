# accounts/urls.py
from django.urls import path, include
from .views import signup, logout_view

urlpatterns = [
    path('signup/', signup, name='signup'),
    # route de déconnexion pour autoriser GET/POST
    path('logout/', logout_view, name='logout'),
    # inclut les vues d’authentification standard de Django (login, reset, etc.)
    path('', include('django.contrib.auth.urls')),
]

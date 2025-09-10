# monitoring/urls.py
from django.urls import path
from .views import logs_view
app_name = 'monitoring'
urlpatterns = [
    path('logs/', logs_view, name='logs'),
]

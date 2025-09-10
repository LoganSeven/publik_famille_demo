# activities/urls.py
from django.urls import path
from .views import ActivityListView, ActivityDetailView, EnrollmentListView, EnrollView
app_name = 'activities'
urlpatterns = [
    path('', ActivityListView.as_view(), name='list'),
    path('<int:pk>/', ActivityDetailView.as_view(), name='detail'),
    path('<int:pk>/inscrire/', EnrollView.as_view(), name='enroll'),
    path('inscriptions/', EnrollmentListView.as_view(), name='enrollments'),
]

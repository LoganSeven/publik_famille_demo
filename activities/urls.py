# activities/urls.py
from django.urls import path
from .views import ActivityListView, ActivityDetailView, EnrollmentListView, EnrollView

app_name = "activities"

urlpatterns = [
    # Liste des activités (filtrées côté vue pour l'avenir)
    path("", ActivityListView.as_view(), name="list"),
    # Détail d'une activité (nécessite l'ID)
    path("<int:pk>/", ActivityDetailView.as_view(), name="detail"),
    # Inscription (POST uniquement) sur une activité donnée
    path("<int:pk>/inscrire/", EnrollView.as_view(), name="enroll"),
    # Liste des inscriptions du parent connecté
    path("inscriptions/", EnrollmentListView.as_view(), name="enrollments"),
]

# families/urls.py
from django.urls import path
from .views import ChildListView, ChildCreateView, ChildUpdateView, ChildDeleteView
app_name = 'families'
urlpatterns = [
    path('', ChildListView.as_view(), name='child_list'),
    path('ajouter/', ChildCreateView.as_view(), name='child_add'),
    path('<int:pk>/editer/', ChildUpdateView.as_view(), name='child_edit'),
    path('<int:pk>/supprimer/', ChildDeleteView.as_view(), name='child_delete'),
]

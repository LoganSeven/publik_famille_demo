# families/views.py
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.shortcuts import get_object_or_404
from .models import Child
from .forms import ChildForm

class ChildListView(LoginRequiredMixin, ListView):
    template_name = 'families/child_list.html'
    context_object_name = 'children'
    def get_queryset(self):
        return Child.objects.filter(parent=self.request.user)

class ChildCreateView(LoginRequiredMixin, CreateView):
    model = Child
    form_class = ChildForm
    template_name = 'families/child_form.html'
    success_url = reverse_lazy('families:child_list')
    def form_valid(self, form):
        form.instance.parent = self.request.user
        return super().form_valid(form)

class ChildUpdateView(LoginRequiredMixin, UpdateView):
    model = Child
    form_class = ChildForm
    template_name = 'families/child_form.html'
    success_url = reverse_lazy('families:child_list')
    def get_object(self, queryset=None):
        return get_object_or_404(Child, pk=self.kwargs['pk'], parent=self.request.user)

class ChildDeleteView(LoginRequiredMixin, DeleteView):
    model = Child
    template_name = 'families/child_confirm_delete.html'
    success_url = reverse_lazy('families:child_list')
    def get_object(self, queryset=None):
        return get_object_or_404(Child, pk=self.kwargs['pk'], parent=self.request.user)

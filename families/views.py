# families/views.py
"""
Views for the families application.

This module defines class-based views for managing children,
including listing, creating, updating, and deleting.
"""

from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import ListView, CreateView, UpdateView, DeleteView
from django.shortcuts import get_object_or_404
from .models import Child
from .forms import ChildForm


class ChildListView(LoginRequiredMixin, ListView):
    """
    View for listing children of the authenticated parent.

    Requires login and restricts the queryset to children
    belonging to the current user.
    """

    template_name = "families/child_list.html"
    context_object_name = "children"

    def get_queryset(self):
        """
        Return the queryset of children for the current user.

        Returns
        -------
        QuerySet
            All Child instances related to the authenticated parent.
        """
        return Child.objects.filter(parent=self.request.user)


class ChildCreateView(LoginRequiredMixin, CreateView):
    """
    View for creating a new child record.

    Requires login and automatically sets the parent field
    to the authenticated user.
    """

    model = Child
    form_class = ChildForm
    template_name = "families/child_form.html"
    success_url = reverse_lazy("families:child_list")

    def form_valid(self, form):
        """
        Set the parent to the authenticated user before saving.

        Parameters
        ----------
        form : ChildForm
            The validated form instance.

        Returns
        -------
        HttpResponse
            Redirect to the success URL if valid.
        """
        form.instance.parent = self.request.user
        return super().form_valid(form)


class ChildUpdateView(LoginRequiredMixin, UpdateView):
    """
    View for updating an existing child record.

    Requires login and restricts updates to children owned
    by the authenticated user.
    """

    model = Child
    form_class = ChildForm
    template_name = "families/child_form.html"
    success_url = reverse_lazy("families:child_list")

    def get_object(self, queryset=None):
        """
        Retrieve the child instance ensuring ownership by the user.

        Parameters
        ----------
        queryset : QuerySet, optional
            An optional queryset to filter results.

        Returns
        -------
        Child
            The child instance belonging to the authenticated user.
        """
        return get_object_or_404(Child, pk=self.kwargs["pk"], parent=self.request.user)


class ChildDeleteView(LoginRequiredMixin, DeleteView):
    """
    View for deleting an existing child record.

    Requires login and restricts deletion to children owned
    by the authenticated user.
    """

    model = Child
    template_name = "families/child_confirm_delete.html"
    success_url = reverse_lazy("families:child_list")

    def get_object(self, queryset=None):
        """
        Retrieve the child instance ensuring ownership by the user.

        Parameters
        ----------
        queryset : QuerySet, optional
            An optional queryset to filter results.

        Returns
        -------
        Child
            The child instance belonging to the authenticated user.
        """
        return get_object_or_404(Child, pk=self.kwargs["pk"], parent=self.request.user)

# activities/views.py
"""
Views for the activities application.

This module defines class-based views for listing activities,
displaying details, managing enrollments, and handling enrollment
creation with integration to enrollment and billing gateways.
"""

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import ListView, DetailView, View
from django.utils import timezone

from .models import Activity, Enrollment
from .forms import EnrollmentForm
from billing.gateways import get_billing_gateway
from .gateways import get_enrollment_gateway

# Attempt to use HTML-based logging if available; fallback to standard logging otherwise
try:
    from monitoring.html_logger import info, warn, error  # type: ignore
except Exception:
    import logging

    _logger = logging.getLogger(__name__)

    def info(msg: str) -> None:
        """Log informational messages when HTML logger is unavailable."""
        _logger.info(msg)

    def warn(msg: str) -> None:
        """Log warning messages when HTML logger is unavailable."""
        _logger.warning(msg)

    def error(msg: str) -> None:
        """Log error messages when HTML logger is unavailable."""
        _logger.error(msg)


class ActivityListView(ListView):
    """
    View for listing all active activities.

    Displays only activities that are active and whose start date
    is greater than or equal to the current date.
    """

    template_name = "activities/activity_list.html"
    context_object_name = "activities"

    def get_queryset(self):
        """
        Return the queryset of activities to be displayed.

        Returns
        -------
        QuerySet
            Active activities starting today or later.
        """
        today = timezone.now().date()
        return Activity.objects.filter(is_active=True, start_date__gte=today)


class ActivityDetailView(DetailView):
    """
    View for displaying the details of a single activity.

    Adds context information about whether enrollment is allowed
    and provides an enrollment form if the user is authenticated.
    """

    model = Activity
    template_name = "activities/activity_detail.html"
    context_object_name = "activity"

    def get_context_data(self, **kwargs):
        """
        Extend the context with enrollment form and availability flag.

        Parameters
        ----------
        **kwargs : dict
            Additional context data passed from the superclass.

        Returns
        -------
        dict
            Context dictionary with keys:
            - ``can_enroll``: bool indicating if enrollment is possible.
            - ``form``: EnrollmentForm instance for authenticated users.
        """
        ctx = super().get_context_data(**kwargs)
        activity = self.object
        today = timezone.now().date()
        ctx["can_enroll"] = activity.start_date is None or activity.start_date >= today
        if self.request.user.is_authenticated:
            ctx["form"] = EnrollmentForm(user=self.request.user)
        return ctx


class EnrollmentListView(LoginRequiredMixin, ListView):
    """
    View for listing all enrollments of the authenticated user.

    Requires login and displays enrollments associated with
    the user's children.
    """

    template_name = "activities/enrollment_list.html"
    context_object_name = "enrollments"

    def get_queryset(self):
        """
        Return the queryset of enrollments for the current user.

        Returns
        -------
        QuerySet
            Enrollments filtered by the authenticated user's children.
        """
        return Enrollment.objects.filter(child__parent=self.request.user)


class EnrollView(LoginRequiredMixin, View):
    """
    Handle creation of an enrollment and associated invoice.

    Enrollment is created through the configured enrollment gateway,
    and a billing record is generated using the billing gateway.
    """

    def post(self, request, pk):
        """
        Handle POST request to enroll a child in an activity.

        Parameters
        ----------
        request : HttpRequest
            The HTTP request containing enrollment data.
        pk : int
            The primary key of the activity to enroll in.

        Returns
        -------
        HttpResponse
            A redirection response to either the detail page,
            enrollment list, or verification page.
        """
        activity = get_object_or_404(Activity, pk=pk, is_active=True)
        form = EnrollmentForm(request.POST, user=request.user)

        if not form.is_valid():
            messages.error(request, "Form is invalid.")
            warn(f"Form invalid for enrollment (user={request.user.id}, activity={activity.id}).")
            return redirect("activities:detail", pk=pk)

        child = form.cleaned_data["child"]
        if child.parent_id != request.user.id:
            messages.error(request, "Invalid child selection.")
            error(
                f"Unauthorized enrollment attempt "
                f"(user={request.user.id}, child={child.id})."
            )
            return redirect("activities:detail", pk=activity.pk)

        # Identity verification: enforce unless user is staff or superuser
        if not request.user.is_staff and not request.user.is_superuser:
            profile = getattr(request.user, "profile", None)
            if not profile or not profile.id_verified:
                messages.warning(
                    request,
                    "Please verify your identity before enrolling a child in an activity.",
                )
                return redirect(
                    f"{reverse('accounts_verify_identity')}?next={request.get_full_path()}"
                )

        # Capacity check
        if activity.capacity is not None:
            current = Enrollment.objects.filter(activity=activity).count()
            if current >= activity.capacity:
                messages.error(request, "Activity is full.")
                warn(f"Capacity reached for activity {activity.id}.")
                return redirect("activities:detail", pk=activity.pk)

        enrollment_gateway = get_enrollment_gateway()
        billing_gateway = get_billing_gateway()

        try:
            enrollment, created = enrollment_gateway.create_enrollment(
                activity=activity, child=child
            )
            if not created:
                messages.info(request, "This enrollment already exists.")
                info(
                    f"Enrollment already exists child={child.id} activity={activity.id}."
                )
                return redirect("activities:enrollments")

            billing_gateway.create_invoice(enrollment=enrollment, amount=activity.fee)
            messages.success(
                request, "Enrollment created. Please proceed with payment."
            )
            info(
                f"Enrollment created enrollment_id={enrollment.id} "
                f"(user={request.user.id})."
            )
            return redirect("activities:enrollments")
        except Exception as exc:
            error(
                f"Error during enrollment creation: {exc!r} "
                f"(user={request.user.id}, activity={activity.id})."
            )
            messages.error(request, "Internal error during enrollment creation.")
            return redirect("activities:detail", pk=activity.pk)

# activities/views.py
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

try:
    from monitoring.html_logger import info, warn, error  # type: ignore
except Exception:
    import logging
    _logger = logging.getLogger(__name__)
    def info(msg: str) -> None: _logger.info(msg)
    def warn(msg: str) -> None: _logger.warning(msg)
    def error(msg: str) -> None: _logger.error(msg)

class ActivityListView(ListView):
    template_name = 'activities/activity_list.html'
    context_object_name = 'activities'

    def get_queryset(self):
        today = timezone.now().date()
        return Activity.objects.filter(is_active=True, start_date__gte=today)

class ActivityDetailView(DetailView):
    model = Activity
    template_name = 'activities/activity_detail.html'
    context_object_name = 'activity'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        activity = self.object
        today = timezone.now().date()
        ctx['can_enroll'] = activity.start_date is None or activity.start_date >= today
        if self.request.user.is_authenticated:
            ctx['form'] = EnrollmentForm(user=self.request.user)
        return ctx

class EnrollmentListView(LoginRequiredMixin, ListView):
    template_name = 'activities/enrollment_list.html'
    context_object_name = 'enrollments'

    def get_queryset(self):
        return Enrollment.objects.filter(child__parent=self.request.user)

class EnrollView(LoginRequiredMixin, View):
    """Crée une inscription via la passerelle choisie, puis une facture via la passerelle de facturation."""
    def post(self, request, pk):
        activity = get_object_or_404(Activity, pk=pk, is_active=True)
        form = EnrollmentForm(request.POST, user=request.user)

        if not form.is_valid():
            messages.error(request, "Formulaire invalide.")
            warn(f"Form invalid for enrollment (user={request.user.id}, activity={activity.id}).")
            return redirect('activities:detail', pk=pk)

        child = form.cleaned_data['child']
        if child.parent_id != request.user.id:
            messages.error(request, "Enfant invalide.")
            error(f"Tentative d'inscription avec enfant non autorisé (user={request.user.id}, child={child.id}).")
            return redirect('activities:detail', pk=activity.pk)

        # Vérification d'identité : redirige si le profil n'est pas vérifié (sauf admin)
        if not request.user.is_staff and not request.user.is_superuser:
            profile = getattr(request.user, 'profile', None)
            if not profile or not profile.id_verified:
                messages.warning(request, "Veuillez vérifier votre identité avant d'inscrire un enfant à une activité.")
                return redirect(f"{reverse('accounts_verify_identity')}?next={request.get_full_path()}")

        if activity.capacity is not None:
            current = Enrollment.objects.filter(activity=activity).count()
            if current >= activity.capacity:
                messages.error(request, "Activité complète.")
                warn(f"Capacité atteinte pour l'activité {activity.id}.")
                return redirect('activities:detail', pk=activity.pk)

        enrollment_gateway = get_enrollment_gateway()
        billing_gateway = get_billing_gateway()

        try:
            enrollment, created = enrollment_gateway.create_enrollment(activity=activity, child=child)
            if not created:
                messages.info(request, "Cette inscription existe déjà.")
                info(f"Inscription déjà existante child={child.id} activity={activity.id}.")
                return redirect('activities:enrollments')

            billing_gateway.create_invoice(enrollment=enrollment, amount=activity.fee)
            messages.success(request, "Inscription créée. Merci de régler la facture.")
            info(f"Inscription créée enrollment_id={enrollment.id} (user={request.user.id}).")
            return redirect('activities:enrollments')
        except Exception as exc:
            error(f"Erreur lors de la création d'inscription: {exc!r} (user={request.user.id}, activity={activity.id}).")
            messages.error(request, "Erreur interne lors de la création de l'inscription.")
            return redirect('activities:detail', pk=activity.pk)

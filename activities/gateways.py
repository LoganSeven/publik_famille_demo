# activities/gateways.py
from dataclasses import dataclass
from typing import Protocol
from django.db import transaction
from .models import Activity, Enrollment
from families.models import Child

class EnrollmentGateway(Protocol):
    def create_enrollment(self, *, activity: Activity, child: Child) -> tuple[Enrollment, bool]: ...

@dataclass
class LocalEnrollmentGateway:
    def create_enrollment(self, *, activity: Activity, child: Child) -> tuple[Enrollment, bool]:
        with transaction.atomic():
            obj, created = Enrollment.objects.get_or_create(
                activity=activity, child=child,
                defaults={'status': Enrollment.Status.PENDING_PAYMENT}
            )
        return obj, created

@dataclass
class WcsEnrollmentGateway:
    base_url: str | None = None
    def create_enrollment(self, *, activity: Activity, child: Child) -> tuple[Enrollment, bool]:
        # Stub: en réel on POST sur WCS, reçoit un id de dossier, et on maintient un miroir local.
        with transaction.atomic():
            obj, created = Enrollment.objects.get_or_create(
                activity=activity, child=child,
                defaults={'status': Enrollment.Status.PENDING_PAYMENT}
            )
        return obj, created

def get_enrollment_gateway():
    from django.conf import settings
    backend = getattr(settings, 'ENROLLMENT_BACKEND', 'local')
    if backend == 'wcs':
        return WcsEnrollmentGateway(base_url=getattr(settings, 'WCS_BASE_URL', None))
    return LocalEnrollmentGateway()

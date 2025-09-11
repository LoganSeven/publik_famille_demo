# activities/gateways.py
from dataclasses import dataclass
from typing import Protocol, Tuple, Optional, Dict, Any
import logging
import os

from django.db import transaction
from django.utils import timezone

import requests
from requests.exceptions import RequestException

from .models import Activity, Enrollment
from families.models import Child
from .exceptions import EnrollmentError, EnrollmentCreationError, EnrollmentSyncError  # type: ignore

logger = logging.getLogger(__name__)


class EnrollmentGateway(Protocol):
    def create_enrollment(self, *, activity: Activity, child: Child) -> Tuple[Enrollment, bool]:
        ...


@dataclass
class LocalEnrollmentGateway:
    def create_enrollment(self, *, activity: Activity, child: Child) -> Tuple[Enrollment, bool]:
        """
        Pure local enrollment creation.
        """
        with transaction.atomic():
            obj, created = Enrollment.objects.get_or_create(
                activity=activity,
                child=child,
                defaults={"status": Enrollment.Status.PENDING_PAYMENT},
            )
        return obj, created


@dataclass
class WcsEnrollmentGateway:
    """
    Remote WCS gateway mirroring the rigor of the Lingo gateway:
    - robust network error handling with timeouts
    - logs
    - tolerant write-back of `wcs_id` if the field exists
    """
    base_url: Optional[str] = None
    api_token: Optional[str] = None
    timeout_sec: int = 5

    # ---------- internal helpers ----------

    def _require_base(self) -> str:
        base = self.base_url or os.getenv("WCS_BASE_URL")
        if not base:
            raise EnrollmentCreationError("WCS_BASE_URL is not configured")
        return base.rstrip("/")

    def _headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        token = self.api_token or os.getenv("WCS_API_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    # ---------- public API ----------

    def create_enrollment(self, *, activity: Activity, child: Child) -> Tuple[Enrollment, bool]:
        """
        Create (or get) an enrollment *and* mirror it to the WCS backend.

        Payload kept minimal & generic; extend here if your WCS expects more fields.
        """
        base = self._require_base()
        url = f"{base}/enrollments"

        payload: Dict[str, Any] = {
            "activity_id": activity.pk,
            "child_id": child.pk,
            # Optionnel : enrichir selon vos besoins WCS
            "child": {
                "first_name": getattr(child, "first_name", None),
                "last_name": getattr(child, "last_name", None),
                "birth_date": getattr(child, "birth_date", None).isoformat() if getattr(child, "birth_date", None) else None,
            },
            "requested_on": timezone.now().isoformat(),
        }

        try:
            resp = requests.post(url, json=payload, headers=self._headers(), timeout=self.timeout_sec)
            resp.raise_for_status()
            data = resp.json()
        except RequestException as exc:
            logger.exception("WCS create_enrollment failed")
            raise EnrollmentCreationError("Failed to create enrollment at WCS") from exc

        wcs_id = data.get("id")

        with transaction.atomic():
            obj, created = Enrollment.objects.get_or_create(
                activity=activity,
                child=child,
                defaults={"status": Enrollment.Status.PENDING_PAYMENT},
            )

            # Ecrit obj.wcs_id si le champ existe (migration appliquée) ; sinon log warn et continue.
            if hasattr(obj, "wcs_id"):
                # type: ignore[attr-defined]
                setattr(obj, "wcs_id", wcs_id)
                obj.save(update_fields=["wcs_id"])
            else:
                logger.warning("Enrollment has no `wcs_id` field yet. Did you apply the migration?")

        return obj, created

    def sync_enrollment(self, *, enrollment: Enrollment) -> Enrollment:
        """
        Optionnel: readback status from WCS.
        """
        base = self._require_base()
        wcs_id = getattr(enrollment, "wcs_id", None)
        if not wcs_id:
            raise EnrollmentSyncError("Enrollment has no wcs_id")

        url = f"{base}/enrollments/{wcs_id}"
        try:
            resp = requests.get(url, headers=self._headers(), timeout=self.timeout_sec)
            resp.raise_for_status()
            data = resp.json()
        except RequestException as exc:
            logger.exception("WCS sync_enrollment failed")
            raise EnrollmentSyncError("Failed to sync enrollment from WCS") from exc

        # Exemple de mapping de statut si WCS renvoie un champ status
        remote_status = (data or {}).get("status", "").upper()
        if remote_status and remote_status in dict(Enrollment.Status.choices):
            if enrollment.status != remote_status:
                enrollment.status = remote_status  # type: ignore[assignment]
                enrollment.save(update_fields=["status"])
        else:
            logger.warning("Unknown or missing status from WCS: %r", remote_status)

        return enrollment


def get_enrollment_gateway():
    from django.conf import settings
    backend = getattr(settings, "ENROLLMENT_BACKEND", "local")
    if backend == "wcs":
        return WcsEnrollmentGateway(
            base_url=getattr(settings, "WCS_BASE_URL", None),
            api_token=getattr(settings, "WCS_API_TOKEN", None),
        )
    return LocalEnrollmentGateway()

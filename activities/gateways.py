# activities/gateways.py
"""
Gateways for enrollment management.

This module defines abstractions and implementations of gateways
responsible for creating and synchronizing enrollments. A gateway
can operate locally or interact with an external WCS (Web
Citizen Service) backend. All implementations follow a common
interface to allow interchangeable use.
"""

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
    """
    Protocol for enrollment gateways.

    Any enrollment gateway must implement the ``create_enrollment``
    method to create or retrieve an enrollment associated with
    an activity and a child.
    """

    def create_enrollment(
        self, *, activity: Activity, child: Child
    ) -> Tuple[Enrollment, bool]:
        """
        Create or retrieve an enrollment for a given activity and child.

        Parameters
        ----------
        activity : Activity
            The activity in which the child should be enrolled.
        child : Child
            The child being enrolled.

        Returns
        -------
        tuple
            A tuple of (enrollment instance, created flag).
        """
        ...


@dataclass
class LocalEnrollmentGateway:
    """
    Local enrollment gateway implementation.

    Handles enrollment creation directly in the local database
    without contacting external services.
    """

    def create_enrollment(
        self, *, activity: Activity, child: Child
    ) -> Tuple[Enrollment, bool]:
        """
        Create or retrieve a local enrollment.

        Parameters
        ----------
        activity : Activity
            The activity in which the child should be enrolled.
        child : Child
            The child being enrolled.

        Returns
        -------
        tuple
            A tuple containing the enrollment and a boolean
            indicating whether it was created.
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
    Remote WCS enrollment gateway.

    Provides integration with an external WCS service. It mirrors
    local enrollment creation to the WCS backend and can synchronize
    enrollment status. Features include robust error handling,
    configurable timeouts, and token-based authentication.

    Attributes
    ----------
    base_url : str, optional
        The base URL of the WCS backend.
    api_token : str, optional
        The API token for authentication with WCS.
    timeout_sec : int
        The timeout for HTTP requests, in seconds.
    """

    base_url: Optional[str] = None
    api_token: Optional[str] = None
    timeout_sec: int = 5

    # ---------- internal helpers ----------

    def _require_base(self) -> str:
        """
        Ensure that the base URL is defined.

        Returns
        -------
        str
            The configured base URL.

        Raises
        ------
        EnrollmentCreationError
            If the base URL is not provided in settings or environment.
        """
        base = self.base_url or os.getenv("WCS_BASE_URL")
        if not base:
            raise EnrollmentCreationError("WCS_BASE_URL is not configured")
        return base.rstrip("/")

    def _headers(self) -> Dict[str, str]:
        """
        Build HTTP headers for WCS requests.

        Returns
        -------
        dict
            A dictionary of headers including Authorization
            if an API token is available.
        """
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        token = self.api_token or os.getenv("WCS_API_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    # ---------- public API ----------

    def create_enrollment(
        self, *, activity: Activity, child: Child
    ) -> Tuple[Enrollment, bool]:
        """
        Create or retrieve an enrollment and mirror it to WCS.

        Parameters
        ----------
        activity : Activity
            The activity in which the child should be enrolled.
        child : Child
            The child being enrolled.

        Returns
        -------
        tuple
            A tuple containing the enrollment and a boolean
            indicating whether it was created.

        Raises
        ------
        EnrollmentCreationError
            If the WCS backend request fails.
        """
        base = self._require_base()
        url = f"{base}/enrollments"

        payload: Dict[str, Any] = {
            "activity_id": activity.pk,
            "child_id": child.pk,
            # Optional: extend payload according to WCS requirements
            "child": {
                "first_name": getattr(child, "first_name", None),
                "last_name": getattr(child, "last_name", None),
                "birth_date": getattr(child, "birth_date", None).isoformat()
                if getattr(child, "birth_date", None)
                else None,
            },
            "requested_on": timezone.now().isoformat(),
        }

        try:
            resp = requests.post(
                url, json=payload, headers=self._headers(), timeout=self.timeout_sec
            )
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

            # Write WCS identifier if the field exists in the model
            if hasattr(obj, "wcs_id"):
                # type: ignore[attr-defined]
                setattr(obj, "wcs_id", wcs_id)
                obj.save(update_fields=["wcs_id"])
            else:
                logger.warning(
                    "Enrollment has no `wcs_id` field. "
                    "Ensure migrations have been applied."
                )

        return obj, created

    def sync_enrollment(self, *, enrollment: Enrollment) -> Enrollment:
        """
        Synchronize enrollment status from WCS.

        Parameters
        ----------
        enrollment : Enrollment
            The local enrollment instance to synchronize.

        Returns
        -------
        Enrollment
            The updated enrollment instance.

        Raises
        ------
        EnrollmentSyncError
            If synchronization with WCS fails.
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

        # Example: mapping remote status to local Enrollment.Status
        remote_status = (data or {}).get("status", "").upper()
        if remote_status and remote_status in dict(Enrollment.Status.choices):
            if enrollment.status != remote_status:
                enrollment.status = remote_status  # type: ignore[assignment]
                enrollment.save(update_fields=["status"])
        else:
            logger.warning("Unknown or missing status from WCS: %r", remote_status)

        return enrollment


def get_enrollment_gateway():
    """
    Factory function to select the enrollment gateway.

    Returns
    -------
    EnrollmentGateway
        The enrollment gateway instance based on configuration.
        Defaults to local gateway if no backend is specified.
    """
    from django.conf import settings

    backend = getattr(settings, "ENROLLMENT_BACKEND", "local")
    if backend == "wcs":
        return WcsEnrollmentGateway(
            base_url=getattr(settings, "WCS_BASE_URL", None),
            api_token=getattr(settings, "WCS_API_TOKEN", None),
        )
    return LocalEnrollmentGateway()

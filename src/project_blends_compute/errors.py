from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ServiceError(Exception):
    message: str
    code: str = "service_error"
    status_code: int = 400
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message


class ConfigurationError(ServiceError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message=message, code="configuration_error", status_code=503, details=details)


class ArtifactValidationError(ServiceError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message=message, code="artifact_validation_error", status_code=422, details=details)


class IdentityResolutionError(ServiceError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message=message, code="identity_resolution_error", status_code=422, details=details)


class ExternalDependencyUnavailable(ServiceError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message=message, code="external_dependency_unavailable", status_code=503, details=details)


class NotFoundError(ServiceError):
    def __init__(self, message: str, **details: Any) -> None:
        super().__init__(message=message, code="not_found", status_code=404, details=details)

# Public domain-error name used by the API layer.
ProjectBlendsError = ServiceError


def _service_error_to_dict(self: ServiceError) -> dict[str, Any]:
    return {"code": self.code, "message": self.message, "details": self.details}

ServiceError.to_dict = _service_error_to_dict  # type: ignore[attr-defined]

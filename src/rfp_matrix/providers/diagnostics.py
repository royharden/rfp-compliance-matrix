"""Pure offline configuration report; no credential, SDK, filesystem or DB I/O."""
from __future__ import annotations

from typing import Any, Mapping

from rfp_matrix.contracts import M3Error
from .config import ProviderConfig


def provider_diagnostics(config: ProviderConfig | Mapping[str, Any] | None = None) -> dict[str, Any]:
    try:
        policy = config if isinstance(config, ProviderConfig) else ProviderConfig.from_mapping(config or {})
        reason = policy.admission_reason()
        return {"schema_version": 1, "status": "configured" if reason is None else "disabled",
            "reason": reason, "method": policy.method, "requested_model": policy.model,
            "configured_sdk": {"name": policy.sdk_name, "version": policy.sdk_version,
                               "installed_version_verified": False},
            "config_sha256": policy.config_sha256, "configured_admission_matches": reason is None,
            "budget_usd": policy.budget_usd, "price_policy_present": bool(policy.price_policy),
            "key_checked": False, "sdk_imported": False, "dispatch_performed": False,
            "runtime_verified": False, "live_authorization_verified": False}
    except (M3Error, TypeError, ValueError):
        return {"schema_version": 1, "status": "invalid_configuration", "reason": "provider_config",
            "key_checked": False, "sdk_imported": False, "dispatch_performed": False,
            "runtime_verified": False, "live_authorization_verified": False}

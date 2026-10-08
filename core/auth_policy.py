"""Deployment-controlled sign-in and OIDC authorization policy.

Only verified ID-token / subject-bound UserInfo claims reach this module.
Provider values select grants; they never define local permission names.
"""

import json
import os
from dataclasses import dataclass


OIDC_FEATURE_PRIVILEGES = frozenset({
    "can_use_agent", "can_use_browser", "can_use_bash", "can_use_documents",
    "can_use_research", "can_generate_images", "can_manage_memory",
})


class OidcPolicyError(ValueError):
    """Invalid mapping configuration or missing authoritative group evidence."""


def password_auth_enabled() -> bool:
    return os.getenv("PASSWORD_AUTH_ENABLED", "true").strip().lower() in {"true", "1", "yes", "on"}


def validate_auth_policy() -> None:
    """Reject an impossible sign-in configuration without contacting the IdP."""
    value = os.getenv("PASSWORD_AUTH_ENABLED", "true").strip().lower()
    if value not in {"true", "1", "yes", "on", "false", "0", "no", "off"}:
        raise ValueError("PASSWORD_AUTH_ENABLED must be a boolean")
    if not password_auth_enabled():
        if os.getenv("AUTH_ENABLED", "true").strip().lower() == "false":
            raise ValueError("PASSWORD_AUTH_ENABLED=false requires authentication to remain enabled")
        if os.getenv("OIDC_ENABLED", "false").lower() != "true" or not all(
            os.getenv(key, "").strip() for key in ("OIDC_ISSUER", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET")
        ):
            raise ValueError("PASSWORD_AUTH_ENABLED=false requires OIDC configuration")
    oidc_group_policy()


def _group_names(value):
    if not isinstance(value, list) or len(value) > 4096 or any(
        not isinstance(group, str) or not group or len(group) > 256 for group in value
    ):
        raise OidcPolicyError("Groups must be an array of nonempty strings")
    return frozenset(value)


@dataclass(frozen=True)
class OidcGroupPolicy:
    claim: str
    admin_groups: frozenset
    privilege_groups: dict

    @property
    def managed(self):
        return bool(self.admin_groups or self.privilege_groups)

    def grants(self, claims):
        if not self.managed:
            return None, {}
        # Prefer an exact key (including namespaced claims), then a dotted path.
        value = claims.get(self.claim)
        if self.claim not in claims:
            value = claims
            for part in self.claim.split("."):
                value = value.get(part) if isinstance(value, dict) else None
        groups = _group_names(value)
        admin = bool(groups & self.admin_groups) if self.admin_groups else None
        return admin, {name: bool(groups & allowed) for name, allowed in self.privilege_groups.items()}


def oidc_group_policy() -> OidcGroupPolicy:
    claim = os.getenv("OIDC_GROUPS_CLAIM", "groups").strip()
    if not claim or len(claim) > 256 or any(not part for part in claim.split(".")):
        raise OidcPolicyError("OIDC_GROUPS_CLAIM must name a claim or dotted claim path")
    admin = _group_names([g.strip() for g in os.getenv("OIDC_ADMIN_GROUPS", "").split(",") if g.strip()])
    raw = os.getenv("OIDC_PRIVILEGE_GROUPS", "").strip() or "{}"
    if len(raw) > 65536:
        raise OidcPolicyError("OIDC_PRIVILEGE_GROUPS is too large")
    try:
        mappings = json.loads(raw)
    except ValueError as exc:
        raise OidcPolicyError("OIDC_PRIVILEGE_GROUPS must be a JSON object") from exc
    if not isinstance(mappings, dict) or set(mappings) - OIDC_FEATURE_PRIVILEGES:
        raise OidcPolicyError("OIDC_PRIVILEGE_GROUPS contains an unsupported permission")
    return OidcGroupPolicy(claim, admin, {key: _group_names(value) for key, value in mappings.items()})


def oidc_provider_display(default_name: str) -> dict:
    name = os.getenv("OIDC_PROVIDER_NAME", "").strip()[:80] or default_name
    icon = os.getenv("OIDC_PROVIDER_ICON", "shield").strip().lower()
    return {"provider_name": name, "provider_icon": icon if icon in {"shield", "key", "building"} else "shield"}

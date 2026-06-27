from dataclasses import dataclass
from typing import Iterable

DEFAULT_TENANT_ID = "default"
DEFAULT_PROFILE_ID = "default:base"
DEFAULT_PROFILE_SOURCE = "default"
API_PROFILE_SOURCE = "api"


@dataclass(frozen=True)
class TenantCompressionProfile:
    tenant_id: str = DEFAULT_TENANT_ID
    profile_id: str = DEFAULT_PROFILE_ID
    source: str = DEFAULT_PROFILE_SOURCE
    default_aggressiveness: float | None = None
    min_rate: float | None = None
    force_keep_tokens: tuple[str, ...] = ()
    force_drop_phrases: tuple[str, ...] = ()


def build_tenant_profile(
    *,
    tenant_id: str | None = None,
    profile_id: str | None = None,
    default_aggressiveness: float | None = None,
    min_rate: float | None = None,
    force_keep_tokens: Iterable[str] | None = None,
    force_drop_phrases: Iterable[str] | None = None,
) -> TenantCompressionProfile:
    normalized_tenant_id = _clean_value(tenant_id) or DEFAULT_TENANT_ID
    normalized_profile_id = _clean_value(profile_id)
    keep_tokens = _clean_unique(force_keep_tokens or ())
    drop_phrases = _clean_unique(force_drop_phrases or ())
    source = (
        API_PROFILE_SOURCE
        if (
            normalized_tenant_id != DEFAULT_TENANT_ID
            or normalized_profile_id is not None
            or default_aggressiveness is not None
            or min_rate is not None
            or keep_tokens
            or drop_phrases
        )
        else DEFAULT_PROFILE_SOURCE
    )

    if normalized_profile_id is None:
        normalized_profile_id = (
            f"{normalized_tenant_id}:api"
            if source == API_PROFILE_SOURCE
            else DEFAULT_PROFILE_ID
        )

    return TenantCompressionProfile(
        tenant_id=normalized_tenant_id,
        profile_id=normalized_profile_id,
        source=source,
        default_aggressiveness=default_aggressiveness,
        min_rate=min_rate,
        force_keep_tokens=keep_tokens,
        force_drop_phrases=drop_phrases,
    )


def _clean_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _clean_unique(values: Iterable[str]) -> tuple[str, ...]:
    cleaned_values: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        cleaned_values.append(cleaned)
    return tuple(cleaned_values)

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ExperimentProfile:
    """Allowlisted benchmark-only compression configuration."""

    profile_id: str
    strict_prose_whitespace: bool | None = None
    enable_json_minify: bool | None = None
    enable_literal_aliases: bool | None = None
    enable_duplicate_wrapper_aliases: bool = False
    enable_tenant_boilerplate: bool = False
    enable_critical_clause_shielding: bool = False
    require_tokenizer_backed_gates: bool = False
    min_whitespace_savings_tokens: int = 0
    min_whitespace_reduction: float = 0.0
    min_json_minify_savings_tokens: int = 0
    min_json_minify_reduction: float | None = None
    min_literal_alias_savings_tokens: int | None = None
    min_literal_alias_reduction: float | None = None
    min_toon_characters: int | None = None
    min_toon_lines: int | None = None
    min_toon_savings_tokens: int = 0
    min_toon_reduction: float | None = None
    min_html_characters: int | None = None
    min_html_savings_tokens: int = 0
    min_html_reduction: float | None = None

    def export(self) -> dict[str, Any]:
        return asdict(self)


_PROFILES = {
    "baseline": ExperimentProfile(profile_id="baseline"),
    "strict_whitespace_token_positive": ExperimentProfile(
        profile_id="strict_whitespace_token_positive",
        strict_prose_whitespace=True,
        require_tokenizer_backed_gates=True,
        min_whitespace_savings_tokens=2,
        min_whitespace_reduction=0.005,
        enable_critical_clause_shielding=True,
    ),
    "json_minify_safe": ExperimentProfile(
        profile_id="json_minify_safe",
        enable_json_minify=True,
        require_tokenizer_backed_gates=True,
        min_json_minify_savings_tokens=8,
        min_json_minify_reduction=0.05,
        enable_critical_clause_shielding=True,
    ),
    "literal_aliases_safe": ExperimentProfile(
        profile_id="literal_aliases_safe",
        enable_literal_aliases=True,
        require_tokenizer_backed_gates=True,
        min_literal_alias_savings_tokens=16,
        min_literal_alias_reduction=0.05,
        enable_critical_clause_shielding=True,
    ),
    "toon_expanded_safe": ExperimentProfile(
        profile_id="toon_expanded_safe",
        require_tokenizer_backed_gates=True,
        min_toon_characters=120,
        min_toon_lines=2,
        min_toon_savings_tokens=16,
        min_toon_reduction=0.05,
        enable_critical_clause_shielding=True,
    ),
    "html_markdown_expanded_safe": ExperimentProfile(
        profile_id="html_markdown_expanded_safe",
        require_tokenizer_backed_gates=True,
        min_html_characters=300,
        min_html_savings_tokens=16,
        min_html_reduction=0.20,
        enable_critical_clause_shielding=True,
    ),
    "tenant_boilerplate_exact": ExperimentProfile(
        profile_id="tenant_boilerplate_exact",
        enable_tenant_boilerplate=True,
        require_tokenizer_backed_gates=True,
        enable_critical_clause_shielding=True,
    ),
    "duplicate_wrapper_aliases": ExperimentProfile(
        profile_id="duplicate_wrapper_aliases",
        enable_duplicate_wrapper_aliases=True,
        require_tokenizer_backed_gates=True,
        enable_critical_clause_shielding=True,
    ),
    # Membership is intentionally conservative. Profiles are added here only
    # after their independent benchmark satisfies the preregistered gates.
    "safe_stack_v1": ExperimentProfile(
        profile_id="safe_stack_v1",
        require_tokenizer_backed_gates=True,
        enable_critical_clause_shielding=True,
    ),
}

EXPERIMENT_PROFILE_IDS = tuple(_PROFILES)


def resolve_experiment_profile(
    profile_id: str | ExperimentProfile | None,
) -> ExperimentProfile:
    if isinstance(profile_id, ExperimentProfile):
        return profile_id
    resolved_id = profile_id or "baseline"
    try:
        return _PROFILES[resolved_id]
    except KeyError as exc:
        allowed = ", ".join(EXPERIMENT_PROFILE_IDS)
        raise ValueError(
            f"unknown experiment_profile {resolved_id!r}; allowed values: {allowed}"
        ) from exc

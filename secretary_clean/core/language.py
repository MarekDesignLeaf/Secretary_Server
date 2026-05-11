"""Core language resolution for the clean Secretary backend.

This module is backend-owned business logic. Frontends may display these options
and submit choices, but they must not decide translation direction or voice
language behavior themselves.
"""

from __future__ import annotations

from .models import LanguageContext, LanguageDefinition, TenantOperatingProfile, UserAccount

AVAILABLE_LANGUAGES: tuple[LanguageDefinition, ...] = (
    LanguageDefinition(code="en-GB", name="English", native_name="English"),
    LanguageDefinition(code="cs-CZ", name="Czech", native_name="Čeština"),
    LanguageDefinition(code="pl-PL", name="Polish", native_name="Polski"),
    LanguageDefinition(code="de-DE", name="German", native_name="Deutsch"),
    LanguageDefinition(code="fr-FR", name="French", native_name="Français"),
    LanguageDefinition(code="es-ES", name="Spanish", native_name="Español"),
    LanguageDefinition(code="sk-SK", name="Slovak", native_name="Slovenčina"),
    LanguageDefinition(code="ro-RO", name="Romanian", native_name="Română"),
)

_LANGUAGE_ALIASES = {
    "en": "en-GB",
    "en-gb": "en-GB",
    "en-us": "en-GB",
    "english": "en-GB",
    "cs": "cs-CZ",
    "cs-cz": "cs-CZ",
    "czech": "cs-CZ",
    "cesky": "cs-CZ",
    "cestina": "cs-CZ",
    "čeština": "cs-CZ",
    "pl": "pl-PL",
    "pl-pl": "pl-PL",
    "polish": "pl-PL",
    "polski": "pl-PL",
    "de": "de-DE",
    "de-de": "de-DE",
    "german": "de-DE",
    "deutsch": "de-DE",
    "fr": "fr-FR",
    "fr-fr": "fr-FR",
    "french": "fr-FR",
    "français": "fr-FR",
    "es": "es-ES",
    "es-es": "es-ES",
    "spanish": "es-ES",
    "español": "es-ES",
    "sk": "sk-SK",
    "sk-sk": "sk-SK",
    "slovak": "sk-SK",
    "slovenčina": "sk-SK",
    "ro": "ro-RO",
    "ro-ro": "ro-RO",
    "romanian": "ro-RO",
    "română": "ro-RO",
}


def normalize_language_code(raw: str | None, default: str = "en-GB") -> str:
    if not raw or not raw.strip():
        return default
    key = raw.strip().lower().replace("_", "-")
    return _LANGUAGE_ALIASES.get(key, default)


def enabled_language_codes_for_scope(enabled_codes: list[str], fallback: str) -> list[str]:
    normalized = []
    for code in enabled_codes:
        value = normalize_language_code(code, fallback)
        if value not in normalized:
            normalized.append(value)
    return normalized or [fallback]


def resolve_language_context(
    *,
    profile: TenantOperatingProfile,
    user: UserAccount,
    client_language_code: str | None,
) -> LanguageContext:
    internal = normalize_language_code(
        user.preferred_language_code or profile.default_internal_language_code,
        profile.default_internal_language_code,
    )
    customer = normalize_language_code(
        client_language_code or profile.default_customer_language_code,
        profile.default_customer_language_code,
    )
    voice_input = customer if profile.voice_input_strategy.value in {"client_preferred", "detect_from_context"} else internal
    if profile.voice_input_strategy.value == "user_preferred":
        voice_input = internal
    voice_output = customer if profile.voice_output_strategy.value == "client_preferred" else internal
    if profile.voice_output_strategy.value == "tenant_default":
        voice_output = normalize_language_code(profile.default_customer_language_code)

    return LanguageContext(
        internal_language_code=internal,
        customer_language_code=customer,
        voice_input_language_code=voice_input,
        voice_output_language_code=voice_output,
        translate_customer_to_internal=profile.auto_translate_customer_to_internal and customer != internal,
        translate_internal_to_customer=profile.auto_translate_internal_to_customer and internal != customer,
        resolution_source="client_preferred" if client_language_code else "tenant_defaults",
    )

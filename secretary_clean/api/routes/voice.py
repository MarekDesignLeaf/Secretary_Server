from __future__ import annotations

from fastapi import APIRouter, Depends

from secretary_clean.api.deps import get_repository, require_permission
from secretary_clean.core.language import resolve_language_context
from secretary_clean.core.models import Permission, UserAccount, VoiceExecuteRequest, VoiceExecuteResult, VoiceResolveRequest, VoiceResolveResult
from secretary_clean.core.repository import InMemorySecretaryRepository

router = APIRouter(prefix="/voice", tags=["voice foundation"])

# ── Intent phrase table ────────────────────────────────────────────────────
# Each tuple: (phrase_fragment, intent_code, requires_confirmation)
# Checked in order — first match wins.
# Navigation intents are low-risk (requires_confirmation=False).
# Mutating CRM intents are medium-risk (requires_confirmation=True).

_INTENT_RULES: list[tuple[str, str, bool]] = [
    # ── Navigation (EN / CS / PL / SK / DE) ─────────────────────────────
    ("go home",          "navigate.home",     False),
    ("home screen",      "navigate.home",     False),
    ("na domů",          "navigate.home",     False),
    ("na hlavní",        "navigate.home",     False),
    ("domovská",         "navigate.home",     False),
    ("domov",            "navigate.home",     False),
    ("do home",          "navigate.home",     False),
    ("strona główna",    "navigate.home",     False),
    ("open crm",         "navigate.crm",      False),
    ("crm screen",       "navigate.crm",      False),
    ("open clients",     "navigate.crm",      False),
    ("klienti",          "navigate.crm",      False),
    ("přejdi na crm",    "navigate.crm",      False),
    ("otwórz crm",       "navigate.crm",      False),
    ("calendar",         "navigate.calendar", False),
    ("kalendář",         "navigate.calendar", False),
    ("kalendarz",        "navigate.calendar", False),
    ("kalender",         "navigate.calendar", False),
    ("settings",         "navigate.settings", False),
    ("nastavení",        "navigate.settings", False),
    ("ustawienia",       "navigate.settings", False),
    ("einstellungen",    "navigate.settings", False),
    ("open tools",       "navigate.tools",    False),
    ("tools screen",     "navigate.tools",    False),
    ("nástroje",         "navigate.tools",    False),
    ("narzędzia",        "navigate.tools",    False),
    ("pricing",          "navigate.pricing",  False),
    ("ceník",            "navigate.pricing",  False),
    ("cennik",           "navigate.pricing",  False),

    # ── Language change ───────────────────────────────────────────────────
    ("speak czech",      "lang.cs", False),
    ("mluv česky",       "lang.cs", False),
    ("jazyk český",      "lang.cs", False),
    ("czech language",   "lang.cs", False),
    ("speak polish",     "lang.pl", False),
    ("mów po polsku",    "lang.pl", False),
    ("język polski",     "lang.pl", False),
    ("polish language",  "lang.pl", False),
    ("speak english",    "lang.en", False),
    ("mluv anglicky",    "lang.en", False),
    ("english language", "lang.en", False),
    ("język angielski",  "lang.en", False),

    # ── CRM — create client (EN / CS / PL / SK / DE) ────────────────────
    ("create client",    "crm.clients.create", True),
    ("new client",       "crm.clients.create", True),
    ("add client",       "crm.clients.create", True),
    ("nový klient",      "crm.clients.create", True),
    ("nového klienta",   "crm.clients.create", True),
    ("přidat klienta",   "crm.clients.create", True),
    ("nowy klient",      "crm.clients.create", True),
    ("dodaj klienta",    "crm.clients.create", True),
    ("nový zákazník",    "crm.clients.create", True),
    ("neuer kunde",      "crm.clients.create", True),
    ("kunde hinzufügen", "crm.clients.create", True),

    # ── CRM — create job ──────────────────────────────────────────────────
    ("create job",       "crm.jobs.create", True),
    ("new job",          "crm.jobs.create", True),
    ("add job",          "crm.jobs.create", True),
    ("nová zakázka",     "crm.jobs.create", True),
    ("novou zakázku",    "crm.jobs.create", True),
    ("přidat zakázku",   "crm.jobs.create", True),
    ("nowe zlecenie",    "crm.jobs.create", True),
    ("dodaj zlecenie",   "crm.jobs.create", True),
    ("nový projekt",     "crm.jobs.create", True),
    ("neuer auftrag",    "crm.jobs.create", True),

    # ── CRM — create task ─────────────────────────────────────────────────
    ("create task",      "crm.tasks.create", True),
    ("new task",         "crm.tasks.create", True),
    ("add task",         "crm.tasks.create", True),
    ("nový úkol",        "crm.tasks.create", True),
    ("nový ukol",        "crm.tasks.create", True),
    ("přidat úkol",      "crm.tasks.create", True),
    ("nowe zadanie",     "crm.tasks.create", True),
    ("dodaj zadanie",    "crm.tasks.create", True),
    ("neue aufgabe",     "crm.tasks.create", True),

    # ── CRM — create quote ────────────────────────────────────────────────
    ("create quote",     "crm.quotes.create", True),
    ("new quote",        "crm.quotes.create", True),
    ("add quote",        "crm.quotes.create", True),
    ("nová nabídka",     "crm.quotes.create", True),
    ("novou nabídku",    "crm.quotes.create", True),
    ("přidat nabídku",   "crm.quotes.create", True),
    ("nowa oferta",      "crm.quotes.create", True),
    ("dodaj ofertę",     "crm.quotes.create", True),
    ("neues angebot",    "crm.quotes.create", True),

    # ── CRM — create invoice ──────────────────────────────────────────────
    ("create invoice",   "crm.invoices.create", True),
    ("new invoice",      "crm.invoices.create", True),
    ("add invoice",      "crm.invoices.create", True),
    ("nová faktura",     "crm.invoices.create", True),
    ("novou fakturu",    "crm.invoices.create", True),
    ("vystavit fakturu", "crm.invoices.create", True),
    ("nowa faktura",     "crm.invoices.create", True),
    ("dodaj fakturę",    "crm.invoices.create", True),
    ("neue rechnung",    "crm.invoices.create", True),

    # ── CRM — work report ─────────────────────────────────────────────────
    ("work report",      "crm.work_reports.create", True),
    ("new report",       "crm.work_reports.create", True),
    ("add report",       "crm.work_reports.create", True),
    ("nový výkaz",       "crm.work_reports.create", True),
    ("nový vykaz",       "crm.work_reports.create", True),
    ("přidat výkaz",     "crm.work_reports.create", True),
    ("nowy raport",      "crm.work_reports.create", True),
    ("dodaj raport",     "crm.work_reports.create", True),
    ("arbeitsbericht",   "crm.work_reports.create", True),

    # ── CRM — create lead ─────────────────────────────────────────────────
    ("create lead",      "crm.leads.create", True),
    ("new lead",         "crm.leads.create", True),
    ("add lead",         "crm.leads.create", True),
    ("nový lead",        "crm.leads.create", True),
    ("nová poptávka",    "crm.leads.create", True),
    ("novou poptávku",   "crm.leads.create", True),
    ("nowy lead",        "crm.leads.create", True),
    ("nowe zapytanie",   "crm.leads.create", True),
    ("neue anfrage",     "crm.leads.create", True),

    # ── CRM — create contact ──────────────────────────────────────────────
    ("create contact",   "crm.contacts.create", True),
    ("new contact",      "crm.contacts.create", True),
    ("add contact",      "crm.contacts.create", True),
    ("nový kontakt",     "crm.contacts.create", True),
    ("přidat kontakt",   "crm.contacts.create", True),
    ("nowy kontakt",     "crm.contacts.create", True),
    ("dodaj kontakt",    "crm.contacts.create", True),
    ("neuer kontakt",    "crm.contacts.create", True),
]


def _resolve(
    utterance: str,
    *,
    user: UserAccount,
    repository: InMemorySecretaryRepository,
    client_id: str | None,
) -> VoiceResolveResult:
    normalized = " ".join(utterance.lower().split())
    profile = repository.get_tenant_operating_profile(user.company_id)
    client_language = repository.get_client_preferred_language_code(user.company_id, client_id)
    language_context = resolve_language_context(
        profile=profile,
        user=user,
        client_language_code=client_language,
    )

    for phrase, intent, requires_confirmation in _INTENT_RULES:
        if phrase in normalized:
            return VoiceResolveResult(
                utterance=utterance,
                resolved_intent=intent,
                confidence=0.82,
                requires_confirmation=requires_confirmation,
                reason=f"Matched phrase '{phrase}' → {intent}",
                language_context=language_context,
            )

    return VoiceResolveResult(
        utterance=utterance,
        resolved_intent=None,
        confidence=0.0,
        requires_confirmation=True,
        reason="No intent matched — no action will be executed.",
        language_context=language_context,
    )


@router.post("/resolve", response_model=VoiceResolveResult)
def resolve_voice_command(
    payload: VoiceResolveRequest,
    user: UserAccount = Depends(require_permission(Permission.voice_execute)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    return _resolve(payload.utterance, user=user, repository=repository, client_id=payload.client_id)


@router.post("/execute", response_model=VoiceExecuteResult)
def execute_voice_command(
    payload: VoiceExecuteRequest,
    user: UserAccount = Depends(require_permission(Permission.voice_execute)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    resolution = _resolve(payload.utterance, user=user, repository=repository, client_id=payload.client_id)
    if not resolution.resolved_intent:
        return VoiceExecuteResult(
            executed=False,
            resolved_intent=None,
            requires_confirmation=True,
            message="Command was not executed — no backend intent matched.",
            language_context=resolution.language_context,
        )
    if not payload.confirmed:
        return VoiceExecuteResult(
            executed=False,
            resolved_intent=resolution.resolved_intent,
            requires_confirmation=True,
            message="Confirmation required before executing a mutating voice command.",
            language_context=resolution.language_context,
        )
    return VoiceExecuteResult(
        executed=True,
        resolved_intent=resolution.resolved_intent,
        requires_confirmation=False,
        message=f"Intent '{resolution.resolved_intent}' accepted — application service layer will handle.",
        language_context=resolution.language_context,
    )

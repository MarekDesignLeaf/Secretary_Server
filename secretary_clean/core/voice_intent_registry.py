"""Central voice intent registry (Voice Command Learning — Phase 1, §3).

Code-first, authoritative list of every intent the assistant understands. The
registry is the single source of truth for:

  * which intents exist (`REGISTRY`)
  * whether an intent can actually be executed today (`is_implemented`)
  * which permission is required at execution (`required_permission`)
  * whether an intent is dangerous and must always confirm (`requires_confirmation`)
  * the pre-built phrase coverage used by the resolver (canonical_phrases + synonyms)

Android owns NONE of this — it may only send text to the backend. The registry
is exported read-only for audit via GET /api/v1/voice/intents (added Phase 2).

`is_implemented=True` means voice.py has an execution branch for the intent
(verified against secretary_clean/api/routes/voice.py). Planned intents keep
their phrase coverage so a taught alias parks as PENDING and auto-activates when
the module ships (design §10).
"""
from __future__ import annotations

from dataclasses import dataclass, field

# ── Confidence bands (design §2) ──────────────────────────────────────────────
HIGH_THRESHOLD = 0.85
MEDIUM_THRESHOLD = 0.55
# Two distinct intents whose scores fall within this margin are ambiguous.
AMBIGUITY_MARGIN = 0.12
# Score awarded to an explicit whole-phrase match against the registry.
PHRASE_MATCH_CONFIDENCE = 0.95


def band(confidence: float) -> str:
    if confidence >= HIGH_THRESHOLD:
        return "HIGH"
    if confidence >= MEDIUM_THRESHOLD:
        return "MEDIUM"
    return "LOW"


# ── Dangerous intents — ALWAYS require confirmation (design §8) ───────────────
# Listed by the spec; includes intents not yet implemented (reserved) so the
# guarantee holds the moment they ship.
DANGEROUS_INTENTS: frozenset[str] = frozenset({
    "calendar.delete", "calendar.update",
    "client.delete", "job.delete",
    "whatsapp.send", "email.send",
    "invoice.from_work_report", "invoice.create", "invoice.send",
    "payment.record", "quote.approve",
})


@dataclass(frozen=True)
class VoiceIntent:
    intent_code: str
    module: str
    description: str
    required_permission: str          # Permission enum value, checked at execution
    is_active: bool = True
    is_implemented: bool = True       # True ⇢ voice.py has an execution branch
    requires_confirmation: bool = False
    supported_languages: tuple[str, ...] = ("cs", "en", "pl")
    canonical_phrases: tuple[str, ...] = ()
    synonyms: tuple[str, ...] = ()
    required_entities: tuple[str, ...] = ()
    optional_entities: tuple[str, ...] = ()
    example_commands: tuple[str, ...] = ()
    fallback_message: str = "Tomuto příkazu zatím nerozumím."

    @property
    def all_phrases(self) -> tuple[str, ...]:
        return tuple(self.canonical_phrases) + tuple(self.synonyms)


_R = "voice.execute"   # read-only / query intents
_W = "crm.manage"      # mutating intents


def _vi(code, module, desc, perm, *, implemented=True, phrases=(), synonyms=(),
        required=(), optional=(), examples=(), fallback=None) -> VoiceIntent:
    return VoiceIntent(
        intent_code=code, module=module, description=desc, required_permission=perm,
        is_implemented=implemented,
        requires_confirmation=code in DANGEROUS_INTENTS,
        canonical_phrases=tuple(phrases), synonyms=tuple(synonyms),
        required_entities=tuple(required), optional_entities=tuple(optional),
        example_commands=tuple(examples),
        fallback_message=fallback or "Tomuto příkazu zatím nerozumím.",
    )


# ── The registry ──────────────────────────────────────────────────────────────
_INTENTS: list[VoiceIntent] = [
    # ----- CALENDAR -------------------------------------------------------
    _vi("calendar.list", "calendar", "Read the calendar / upcoming events", _R,
        phrases=("co mám v kalendáři", "co mám zítra", "co mám dnes", "co mě čeká",
                 "ukaž kalendář", "zobraz kalendář", "můj rozvrh", "jaký mám program",
                 "co mám naplánováno", "co je dál", "moje schůzky",
                 "what do i have", "what's on my calendar", "show my calendar",
                 "my schedule", "what's next", "what do i have tomorrow",
                 "show calendar", "my agenda", "what's coming up", "what's today"),
        optional=("date", "window"),
        examples=("co mám zítra", "what do i have tomorrow")),

    _vi("calendar.create", "calendar", "Create a calendar meeting / event", _W,
        phrases=("vytvoř schůzku", "nová schůzka", "naplánuj schůzku", "domluv schůzku",
                 "přidej událost", "přidej schůzku", "zapiš schůzku", "udělej schůzku",
                 "naplánuj termín", "přidej termín", "vytvoř událost", "založ schůzku",
                 "create meeting", "new meeting", "schedule meeting", "add meeting",
                 "create appointment", "new appointment", "set up a meeting",
                 "book a meeting", "create event", "add event"),
        required=("start_at",), optional=("person", "title", "time"),
        examples=("vytvoř schůzku zítra v 10 s Petrem", "schedule meeting tomorrow at 10")),

    _vi("calendar.update", "calendar", "Move / reschedule an existing event", _W,
        phrases=("přesuň schůzku", "přesuň termín", "změň termín", "přelož schůzku",
                 "posuň schůzku", "uprav schůzku", "přesuň meeting",
                 "move meeting", "reschedule meeting", "change meeting time",
                 "move appointment", "reschedule appointment", "push the meeting"),
        optional=("person", "date", "time", "new_start"),
        examples=("přesuň schůzku s Petrem na pátek", "move meeting with Pete to Friday")),

    _vi("calendar.delete", "calendar", "Cancel / delete an event", _W,
        phrases=("zruš schůzku", "smaž schůzku", "odvolej schůzku", "zruš termín",
                 "vymaž schůzku", "odstraň událost", "zruš meeting", "smaž termín",
                 "cancel meeting", "delete meeting", "remove meeting",
                 "cancel appointment", "delete appointment", "cancel the event"),
        optional=("person", "date"),
        examples=("zruš zítřejší schůzku", "cancel tomorrow's meeting")),

    _vi("calendar.sync", "calendar", "Sync the Google calendar", _W,
        phrases=("synchronizuj kalendář", "sesynchronizuj kalendář", "synchronizuj google",
                 "aktualizuj kalendář", "synchronizuj s googlem",
                 "sync calendar", "synchronize calendar", "sync google calendar"),
        examples=("synchronizuj kalendář", "sync calendar")),

    # ----- CLIENTS --------------------------------------------------------
    _vi("client.create", "crm", "Create a new client / customer", _W,
        phrases=("vytvoř klienta", "nový klient", "přidej klienta", "založ klienta",
                 "zapiš klienta", "zaeviduj klienta", "udělej klienta", "nový zákazník",
                 "přidej zákazníka", "založ zákazníka", "nový kontakt klienta",
                 "create client", "new client", "add client", "register client",
                 "new customer", "add customer", "create a customer", "add a contact"),
        required=("name",), optional=("phone", "address"),
        examples=("vytvoř klienta Jan Novák", "create client John Smith")),

    _vi("client.find", "crm", "Look up a client and read back their details", _R,
        phrases=("najdi klienta", "najdi kontakt", "vyhledej klienta", "vyhledej kontakt",
                 "kdo je", "informace o klientovi", "detail klienta", "ukaž klienta",
                 "najdi mi", "vyhledej mi klienta",
                 "find client", "find contact", "look up client", "search client",
                 "who is", "show client", "look up contact", "search contact"),
        required=("query",),
        examples=("najdi klienta Novák", "find client Smith")),

    _vi("client.set_address", "crm", "Fill a client's address from their last message", _W,
        phrases=("doplň adresu", "ulož adresu", "nastav adresu", "zapiš adresu",
                 "doplň adresu klientovi", "adresu ze zprávy",
                 "set address", "save address", "fill address", "update address"),
        optional=("person",),
        examples=("doplň adresu Novákovi", "set address for Smith")),

    # ----- TASKS ----------------------------------------------------------
    _vi("task.create", "crm", "Create a task", _W,
        phrases=("vytvoř úkol", "nový úkol", "přidej úkol", "založ úkol", "zapiš úkol",
                 "udělej úkol", "zadej úkol", "dej úkol", "nový task", "přidej task",
                 "create task", "new task", "add task", "make a task", "add a todo",
                 "create a to do", "set a task", "remind me to", "new todo"),
        required=("title",), optional=("person", "date", "time"),
        examples=("vytvoř úkol zavolat dodavateli", "create task call the supplier")),

    _vi("task.list", "crm", "List my tasks", _R,
        phrases=("moje úkoly", "co mám za úkoly", "jaké mám úkoly", "zobraz úkoly",
                 "ukaž úkoly", "seznam úkolů", "co mám udělat", "nedokončené úkoly",
                 "my tasks", "show tasks", "list tasks", "what are my tasks",
                 "what do i have to do", "show my todos", "open tasks", "pending tasks"),
        examples=("ukaž úkoly", "show my tasks")),

    _vi("task.complete", "crm", "Mark a task as done", _W,
        phrases=("dokonči úkol", "splněno", "úkol hotovo", "označ úkol", "uzavři úkol",
                 "hotový úkol", "splnil jsem",
                 "complete task", "mark task done", "finish task", "task done",
                 "close task", "mark as completed"),
        optional=("person",),
        examples=("dokonči úkol posekat trávník", "mark task done")),

    # ----- JOBS -----------------------------------------------------------
    _vi("job.create", "crm", "Create a job / order", _W,
        phrases=("vytvoř zakázku", "nová zakázka", "založ zakázku", "přidej zakázku",
                 "zaeviduj zakázku", "udělej zakázku", "nová zakázka pro",
                 "create job", "new job", "add job", "create order", "new order",
                 "register a job", "open a job", "start a job", "log a job"),
        required=("title",), optional=("client",),
        examples=("vytvoř zakázku pro Nováka", "create job for Smith")),

    _vi("job.list", "crm", "List jobs", _R,
        phrases=("zobraz zakázky", "moje zakázky", "seznam zakázek", "ukaž zakázky",
                 "jaké mám zakázky", "co mám za zakázky", "aktivní zakázky",
                 "list jobs", "show jobs", "my jobs", "what jobs do i have",
                 "active jobs", "open jobs"),
        examples=("ukaž zakázky", "show jobs")),

    _vi("job.change_status", "crm", "Change a job's status", _W,
        phrases=("změň stav zakázky", "změň zakázku na", "stav zakázky", "nastav zakázku",
                 "zakázka je", "dokonči zakázku", "zakázku dokončeno",
                 "change job status", "set job status", "mark job", "job is done",
                 "update job status", "complete job"),
        optional=("new_status", "person"),
        examples=("dokonči zakázku Novák", "mark job done")),

    # ----- WORK REPORT / INVOICE / QUOTE ---------------------------------
    _vi("work_report.start", "work_report", "Start the work-report dialog", _W,
        phrases=("vytvoř pracovní výkaz", "pracovní výkaz", "vytvoř výkaz", "nový výkaz",
                 "založ výkaz", "zapiš výkaz", "udělej výkaz", "začni výkaz",
                 "create work report", "new work report", "start work report",
                 "log work", "record hours", "fill timesheet", "add a timesheet"),
        examples=("vytvoř pracovní výkaz", "start work report")),

    _vi("invoice.from_work_report", "billing",
        "Create an invoice from a work report", _W, implemented=False,
        phrases=("vytvoř fakturu", "udělej fakturu", "vystav fakturu", "fakturuj",
                 "fakturu z výkazu", "vytvoř fakturu z výkazu", "nová faktura",
                 "create invoice", "make invoice", "issue invoice", "invoice the work",
                 "invoice from work report", "bill the client"),
        optional=("work_report_id", "client"),
        fallback="Fakturace z hlasu zatím není dostupná, brzy ji doplníme.",
        examples=("vytvoř fakturu z výkazu", "create invoice from work report")),

    _vi("quote.create", "billing", "Create a price quote", _W, implemented=False,
        phrases=("vytvoř nabídku", "cenová nabídka", "udělej nabídku", "nová nabídka",
                 "připrav nabídku", "vystav nabídku",
                 "create quote", "new quote", "make a quote", "prepare an estimate",
                 "create estimate", "draft a quote"),
        optional=("client",),
        fallback="Tvorba nabídky z hlasu zatím není dostupná.",
        examples=("vytvoř cenovou nabídku", "create a quote")),

    # ----- COMMUNICATION --------------------------------------------------
    _vi("whatsapp.send", "comm", "Send a WhatsApp message", _W,
        phrases=("pošli whatsapp", "napiš whatsapp", "pošli zprávu na whatsapp",
                 "pošli whatsapp zprávu", "odpověz na whatsapp", "odepiš", "odpověz mu",
                 "napiš na whatsapp", "zpráva přes whatsapp",
                 "send whatsapp", "send a whatsapp message", "message on whatsapp",
                 "reply on whatsapp", "text on whatsapp", "whatsapp them"),
        required=("person", "message"),
        examples=("pošli whatsapp Petrovi že přijedu", "send whatsapp to Pete")),

    _vi("whatsapp.read", "comm", "Read incoming WhatsApp messages", _R,
        phrases=("přečti zprávy", "přečti mi zprávy", "nové zprávy", "mám nové zprávy",
                 "zkontroluj zprávy", "přečti whatsapp", "co mi přišlo", "nějaké zprávy",
                 "read messages", "check messages", "new messages", "read whatsapp",
                 "any new messages", "read my messages"),
        optional=("person",),
        examples=("přečti zprávy", "read my messages")),

    _vi("comm.log", "comm", "Log a communication (call / email / sms)", _W,
        phrases=("zaloguj hovor", "zaznamenej hovor", "zapiš hovor", "volal jsem",
                 "telefonoval jsem", "zaloguj email", "zaznamenej komunikaci",
                 "zaloguj sms", "mluvil jsem s", "kontaktoval jsem",
                 "log call", "log a call", "record call", "log communication",
                 "note a call", "log email", "i called"),
        optional=("comm_type", "person"),
        examples=("zaloguj hovor s Novákem", "log call with Smith")),

    _vi("comm.list", "comm", "Show communication history", _R,
        phrases=("historie komunikace", "historie hovorů", "co jsme řešili",
                 "komunikace s", "zobraz komunikaci", "poslední komunikace",
                 "communication history", "call history", "what did we discuss",
                 "show communication", "recent communication"),
        optional=("person",),
        examples=("historie komunikace s Novákem", "communication history with Smith")),

    # ----- UTILITY / READ-ONLY -------------------------------------------
    _vi("weather.get", "utility", "Weather / forecast lookup", _R,
        phrases=("počasí", "předpověď", "bude pršet", "jaké bude venku", "kolik je venku",
                 "počasí zítra", "předpověď na týden",
                 "weather", "forecast", "is it going to rain", "what's the weather",
                 "weather tomorrow", "weekly forecast"),
        optional=("date", "place", "week"),
        examples=("jaké bude zítra počasí", "what's the weather tomorrow")),

    _vi("contacts.import", "crm", "Import device contacts into the CRM", _W,
        phrases=("importuj kontakty", "naimportuj kontakty", "načti kontakty",
                 "synchronizuj kontakty", "stáhni kontakty", "import kontaktů",
                 "import contacts", "sync contacts", "import phone contacts",
                 "load contacts", "pull in contacts"),
        examples=("importuj kontakty", "import my contacts")),
]

REGISTRY: dict[str, VoiceIntent] = {vi.intent_code: vi for vi in _INTENTS}


# ── Helpers ───────────────────────────────────────────────────────────────────
def get(intent_code: str) -> VoiceIntent | None:
    return REGISTRY.get(intent_code)


def is_known(intent_code: str) -> bool:
    return intent_code in REGISTRY


def is_implemented(intent_code: str) -> bool:
    vi = REGISTRY.get(intent_code)
    return bool(vi and vi.is_implemented)


def implemented_intents() -> set[str]:
    return {c for c, vi in REGISTRY.items() if vi.is_implemented}


def requires_confirmation(intent_code: str) -> bool:
    """Dangerous (§8) OR flagged in the registry. Unknown intents → False."""
    if intent_code in DANGEROUS_INTENTS:
        return True
    vi = REGISTRY.get(intent_code)
    return bool(vi and vi.requires_confirmation)


def required_permission(intent_code: str) -> str:
    vi = REGISTRY.get(intent_code)
    return vi.required_permission if vi else _R


def export() -> list[dict]:
    """Audit export shape for GET /api/v1/voice/intents (Phase 2)."""
    return [
        {
            "intent_code": vi.intent_code,
            "module": vi.module,
            "description": vi.description,
            "required_permission": vi.required_permission,
            "is_active": vi.is_active,
            "is_implemented": vi.is_implemented,
            "requires_confirmation": vi.requires_confirmation,
            "supported_languages": list(vi.supported_languages),
            "canonical_phrases": list(vi.canonical_phrases),
            "synonyms": list(vi.synonyms),
            "required_entities": list(vi.required_entities),
            "optional_entities": list(vi.optional_entities),
            "example_commands": list(vi.example_commands),
            "fallback_message": vi.fallback_message,
        }
        for vi in _INTENTS
    ]

"""Backend help content — single source of truth for the in-app command reference.

Help is bound to the USER and their settings:
- sections/commands are filtered by the user's permissions (RBAC, blueprint S17),
- text is returned in the user's preferred language (falls back to cs),
- "live" marks commands the backend can already execute.

Both the /voice/help endpoint and the spoken "help" intent read from here, so the
reference can never drift between screen and voice.
"""
from __future__ import annotations

from secretary_clean.core.models import Permission, UserAccount

# Each command: (phrase_by_lang, live, required_permission_or_None)
# Each section: key, title_by_lang, spoken_by_lang, required_permission, commands

_T = dict[str, str]


def _t(en: str, cs: str, pl: str) -> _T:
    return {"en": en, "cs": cs, "pl": pl}


class HelpCommand:
    def __init__(self, phrase: _T, live: bool, permission: Permission | None = None):
        self.phrase = phrase
        self.live = live
        self.permission = permission


class HelpSection:
    def __init__(self, key: str, title: _T, spoken: _T,
                 permission: Permission | None, commands: list[HelpCommand]):
        self.key = key
        self.title = title
        self.spoken = spoken
        self.permission = permission
        self.commands = commands


SECTIONS: list[HelpSection] = [
    HelpSection("kalendar",
        _t("Calendar", "Kalendář", "Kalendarz"),
        _t("calendar", "kalendář", "kalendarz"),
        Permission.voice_execute, [
        HelpCommand(_t("add meeting / create appointment", "přidej schůzku / vytvoř schůzku / domluv schůzku", "dodaj spotkanie"), True),
        HelpCommand(_t("what do I have today / tomorrow", "co mám dnes / co mám zítra / co je v kalendáři", "co mam dzisiaj"), True),
        HelpCommand(_t("cancel meeting / delete appointment", "zruš schůzku / smaž termín / odvolej schůzku", "odwołaj spotkanie"), True),
        HelpCommand(_t("move meeting to 14:00 (planned)", "přesuň schůzku na 14:00 (plánováno)", "przenieś spotkanie (planowane)"), False),
    ]),
    HelpSection("klienti",
        _t("Clients", "Klienti", "Klienci"),
        _t("clients", "klienti", "klienci"),
        Permission.crm_manage, [
        HelpCommand(_t("create client / new client", "vytvoř klienta / nový klient / zaeviduj klienta", "utwórz klienta"), True),
        HelpCommand(_t("find client Novák (planned)", "najdi klienta Novák (plánováno)", "znajdź klienta (planowane)"), False),
        HelpCommand(_t("add note to client (planned)", "přidej poznámku ke klientovi (plánováno)", "dodaj notatkę (planowane)"), False),
        HelpCommand(_t("archive client (planned)", "archivuj klienta (plánováno)", "archiwizuj klienta (planowane)"), False),
    ]),
    HelpSection("ukoly",
        _t("Tasks", "Úkoly", "Zadania"),
        _t("tasks", "úkoly", "zadania"),
        Permission.crm_manage, [
        HelpCommand(_t("create task / add task", "vytvoř úkol / přidej úkol / zadej úkol", "utwórz zadanie"), True),
        HelpCommand(_t("my tasks (planned)", "moje úkoly / co mám za úkoly (plánováno)", "moje zadania (planowane)"), False),
        HelpCommand(_t("complete task (planned)", "dokonči úkol / označ úkol jako hotový (plánováno)", "zakończ zadanie (planowane)"), False),
        HelpCommand(_t("assign task to Daniel (planned)", "přiřaď úkol Danielovi (plánováno)", "przypisz zadanie (planowane)"), False),
    ]),
    HelpSection("zakazky",
        _t("Jobs", "Zakázky", "Zlecenia"),
        _t("jobs", "zakázky", "zlecenia"),
        Permission.crm_manage, [
        HelpCommand(_t("create job (planned)", "vytvoř zakázku (plánováno)", "utwórz zlecenie (planowane)"), False),
        HelpCommand(_t("change job status (planned)", "změň stav zakázky na dokončeno (plánováno)", "zmień status (planowane)"), False),
        HelpCommand(_t("show jobs (planned)", "zobraz zakázky / zakázky na zítřek (plánováno)", "pokaż zlecenia (planowane)"), False),
    ]),
    HelpSection("nabidky",
        _t("Quotes", "Nabídky", "Oferty"),
        _t("quotes", "nabídky", "oferty"),
        Permission.pricing_manage, [
        HelpCommand(_t("create quote (planned)", "vytvoř nabídku (plánováno)", "utwórz ofertę (planowane)"), False),
        HelpCommand(_t("send quote (planned)", "odešli nabídku klientovi (plánováno)", "wyślij ofertę (planowane)"), False),
        HelpCommand(_t("approve quote (planned)", "schval nabídku (plánováno)", "zatwierdź ofertę (planowane)"), False),
    ]),
    HelpSection("faktury",
        _t("Invoices", "Faktury", "Faktury"),
        _t("invoices", "faktury", "faktury"),
        Permission.crm_manage, [
        HelpCommand(_t("create invoice (planned)", "vytvoř fakturu ze zakázky (plánováno)", "utwórz fakturę (planowane)"), False),
        HelpCommand(_t("mark invoice paid (planned)", "označ fakturu jako uhrazenou (plánováno)", "oznacz jako opłaconą (planowane)"), False),
        HelpCommand(_t("unpaid invoices (planned)", "neuhrazené faktury (plánováno)", "nieopłacone faktury (planowane)"), False),
    ]),
    HelpSection("komunikace",
        _t("Communication", "Komunikace", "Komunikacja"),
        _t("communication", "komunikace", "komunikacja"),
        Permission.crm_manage, [
        HelpCommand(_t("log a call (planned)", "zaloguj hovor s klientem (plánováno)", "zarejestruj rozmowę (planowane)"), False),
        HelpCommand(_t("client communication history (planned)", "historie komunikace klienta (plánováno)", "historia komunikacji (planowane)"), False),
    ]),
    HelpSection("material",
        _t("Materials & stock", "Materiál a sklad", "Materiały"),
        _t("materials", "materiál", "materiały"),
        Permission.crm_manage, [
        HelpCommand(_t("order material (planned)", "objednej materiál (plánováno)", "zamów materiał (planowane)"), False),
        HelpCommand(_t("check stock (planned)", "kolik máme na skladě (plánováno)", "stan magazynu (planowane)"), False),
    ]),
    HelpSection("reporty",
        _t("Reports", "Reporty", "Raporty"),
        _t("reports", "reporty", "raporty"),
        Permission.crm_manage, [
        HelpCommand(_t("jobs report (planned)", "report zakázek (plánováno)", "raport zleceń (planowane)"), False),
    ]),
    HelpSection("aliasy",
        _t("Command aliases", "Aliasy příkazů", "Aliasy poleceń"),
        _t("aliases", "aliasy", "aliasy"),
        Permission.voice_execute, [
        HelpCommand(_t("create alias X for command Y", "vytvoř alias domluv návštěvu na příkaz přidej schůzku", "utwórz alias"), True),
        HelpCommand(_t("forget alias X", "zapomeň alias domluv návštěvu", "zapomnij alias"), True),
    ]),
    HelpSection("nastaveni",
        _t("Settings", "Nastavení", "Ustawienia"),
        _t("settings", "nastavení", "ustawienia"),
        Permission.company_manage, [
        HelpCommand(_t("open settings (planned)", "otevři nastavení (plánováno)", "otwórz ustawienia (planowane)"), False),
    ]),
]


def _lang_of(user: UserAccount) -> str:
    code = (user.preferred_language_code or "cs").lower()
    return code.split("-")[0] if code.split("-")[0] in ("en", "cs", "pl") else "cs"


def _allowed(user: UserAccount, perm: Permission | None) -> bool:
    return perm is None or perm in set(user.permissions)


def help_for_user(user: UserAccount) -> dict:
    """Structured help filtered by the user's permissions, in their language."""
    lang = _lang_of(user)
    out_sections = []
    for s in SECTIONS:
        if not _allowed(user, s.permission):
            continue
        cmds = [
            {"phrase": c.phrase.get(lang, c.phrase["cs"]), "live": c.live}
            for c in s.commands if _allowed(user, c.permission)
        ]
        if not cmds:
            continue
        out_sections.append({
            "key": s.key,
            "title": s.title.get(lang, s.title["cs"]),
            "spoken": s.spoken.get(lang, s.spoken["cs"]),
            "commands": cmds,
        })
    return {"language": lang, "sections": out_sections}


def _strip_diacritics(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def spoken_overview(user: UserAccount) -> str:
    data = help_for_user(user)
    lang = data["language"]
    names = ", ".join(s["spoken"] for s in data["sections"])
    if lang == "en":
        return (f"Help. You can control these areas: {names}. "
                f"Say for example 'help calendar' and I'll read the commands. "
                f"Commands marked as planned will be added gradually.")
    if lang == "pl":
        return (f"Pomoc. Możesz sterować obszarami: {names}. "
                f"Powiedz na przykład 'pomoc kalendarz'.")
    return (f"Nápověda. Můžeš ovládat tyto oblasti: {names}. "
            f"Řekni třeba „nápověda kalendář“ a přečtu ti příkazy té oblasti. "
            f"Příkazy označené jako plánované budou přidány postupně.")


def find_section(user: UserAccount, query: str):
    q = _strip_diacritics(query.lower()).strip()
    data = help_for_user(user)
    for s in data["sections"]:
        k = _strip_diacritics(s["key"])
        if q == k or k in q or q in k:
            return s
    return None


def spoken_section(user: UserAccount, section: dict) -> str:
    lang = _lang_of(user)
    live = [c for c in section["commands"] if c["live"]]
    planned = [c for c in section["commands"] if not c["live"]]
    def clean(p): return p.split(" (")[0]
    if lang == "en":
        sb = f"Help for {section['spoken']}. "
        if live: sb += "You can say: " + "; ".join(clean(c["phrase"]) for c in live) + ". "
        if planned: sb += "Coming soon: " + "; ".join(clean(c["phrase"]) for c in planned) + "."
        return sb
    if lang == "pl":
        sb = f"Pomoc {section['spoken']}. "
        if live: sb += "Możesz powiedzieć: " + "; ".join(clean(c["phrase"]) for c in live) + ". "
        if planned: sb += "Wkrótce: " + "; ".join(clean(c["phrase"]) for c in planned) + "."
        return sb
    sb = f"Nápověda {section['spoken']}. "
    if live: sb += "Můžeš říct: " + "; ".join(clean(c["phrase"]) for c in live) + ". "
    if planned: sb += "Připravuje se: " + "; ".join(clean(c["phrase"]) for c in planned) + "."
    return sb


def is_help(text: str) -> tuple[bool, str]:
    """Return (is_help, rest). rest is the section query after the help keyword."""
    n = _strip_diacritics(" ".join(text.lower().split()))
    for kw in ("help", "napoveda", "pomoc"):
        if n == kw:
            return True, ""
        if n.startswith(kw + " "):
            return True, n[len(kw):].strip()
    return False, ""

"""Hierarchical command tree — single source of truth for the command catalogue.

Three levels:  MODULE  ->  BRANCH (subsection)  ->  COMMAND (intent + phrase)

Used by:
- /voice/help        : renders the tree (filtered by permission/language)
- /voice/learn-alias : finds where a target command lives, so the user can be
                       told the correct location ("Calendar > Scheduling").

Branches may be EMPTY on purpose: they reserve a place for future commands so the
structure grows predictably. Empty/planned branches are still shown (marked).

Status of each command:
- live=True  : backend executes it now
- live=False : planned (activates when the module ships)
"""
from __future__ import annotations

from secretary_clean.core.models import Permission


def _t(en, cs, pl):
    return {"en": en, "cs": cs, "pl": pl}


# command = (intent, phrase_by_lang, live)
# branch  = (branch_key, branch_title_by_lang, [commands])
# module  = (module_key, module_title_by_lang, permission, [branches])

COMMAND_TREE = [
    ("kalendar", _t("Calendar", "Kalendář", "Kalendarz"), Permission.voice_execute, [
        ("planovani", _t("Scheduling", "Plánování", "Planowanie"), [
            ("calendar.create", _t("add meeting", "přidej schůzku / domluv schůzku", "dodaj spotkanie"), True),
            ("calendar.update", _t("move meeting", "přesuň schůzku na 14:00", "przenieś spotkanie"), True),
        ]),
        ("prehled", _t("Overview", "Přehled", "Przegląd"), [
            ("calendar.list", _t("what do I have today / tomorrow",
                                 "co mám dnes / co mám zítra / zobraz kalendář / co mě čeká",
                                 "co mam dzisiaj / jutro"), True),
            ("calendar.list", _t("this week / next week",
                                 "co mám tento týden / příští týden / ukaž týden",
                                 "ten tydzień / następny tydzień"), True),
        ]),
        ("ruseni", _t("Cancellation", "Rušení", "Anulowanie"), [
            ("calendar.delete", _t("cancel meeting", "zruš schůzku / smaž termín", "odwołaj spotkanie"), True),
        ]),
        ("synchronizace", _t("Sync", "Synchronizace", "Synchronizacja"), [
            ("calendar.sync", _t("sync calendar", "synchronizuj kalendář", "synchronizuj kalendarz"), True),
        ]),
    ]),
    ("klienti", _t("Clients", "Klienti", "Klienci"), Permission.crm_manage, [
        ("zakladani", _t("Create", "Zakládání", "Tworzenie"), [
            ("client.create", _t("new client", "vytvoř klienta / nový klient", "nowy klient"), True),
        ]),
        ("vyhledavani", _t("Find", "Vyhledávání", "Szukanie"), [
            ("client.find", _t("find client", "najdi klienta", "znajdź klienta"), False),
        ]),
        ("sprava", _t("Manage", "Správa", "Zarządzanie"), [
            ("client.note", _t("add note", "přidej poznámku ke klientovi", "dodaj notatkę"), False),
            ("client.archive", _t("archive client", "archivuj klienta", "archiwizuj klienta"), False),
        ]),
    ]),
    ("ukoly", _t("Tasks", "Úkoly", "Zadania"), Permission.crm_manage, [
        ("zakladani", _t("Create", "Zakládání", "Tworzenie"), [
            ("task.create", _t("new task", "vytvoř úkol / přidej úkol", "nowe zadanie"), True),
        ]),
        ("sprava", _t("Manage", "Správa", "Zarządzanie"), [
            ("task.list", _t("my tasks", "moje úkoly / zobraz úkoly", "moje zadania"), True),
            ("task.complete", _t("complete task", "dokonči úkol / úkol hotovo", "zakończ zadanie"), True),
            ("task.assign", _t("assign task", "přiřaď úkol", "przypisz zadanie"), False),
        ]),
    ]),
    ("zakazky", _t("Jobs", "Zakázky", "Zlecenia"), Permission.crm_manage, [
        ("zivotni_cyklus", _t("Lifecycle", "Životní cyklus", "Cykl życia"), [
            ("job.create", _t("create job", "vytvoř zakázku / nová zakázka pro klienta", "utwórz zlecenie"), True),
            ("job.change_status", _t("change status", "změň stav zakázky / dokonči zakázku", "zmień status"), True),
        ]),
        ("prehled", _t("Overview", "Přehled", "Przegląd"), [
            ("job.list", _t("show jobs", "zobraz zakázky / moje zakázky", "pokaż zlecenia"), True),
        ]),
    ]),
    ("leady", _t("Leads", "Leady", "Leady"), Permission.crm_manage, [
        ("prichoz", _t("Intake", "Příchozí", "Przychodzące"), [
            ("lead.create", _t("new lead", "nový lead", "nowy lead"), False),
        ]),
        ("konverze", _t("Conversion", "Konverze", "Konwersja"), [
            ("lead.convert", _t("convert lead", "převeď lead na klienta", "konwertuj lead"), False),
        ]),
    ]),
    ("nabidky", _t("Quotes", "Nabídky", "Oferty"), Permission.pricing_manage, [
        ("tvorba", _t("Create", "Tvorba", "Tworzenie"), [
            ("quote.create", _t("create quote", "vytvoř nabídku", "utwórz ofertę"), False),
        ]),
        ("workflow", _t("Workflow", "Workflow", "Workflow"), [
            ("quote.send", _t("send quote", "odešli nabídku", "wyślij ofertę"), False),
            ("quote.approve", _t("approve quote", "schval nabídku", "zatwierdź ofertę"), False),
        ]),
    ]),
    ("faktury", _t("Invoices", "Faktury", "Faktury"), Permission.crm_manage, [
        ("tvorba", _t("Create", "Tvorba", "Tworzenie"), [
            ("invoice.create", _t("create invoice", "vytvoř fakturu", "utwórz fakturę"), False),
        ]),
        ("platby", _t("Payments", "Platby", "Płatności"), [
            ("invoice.status", _t("mark paid", "označ fakturu jako uhrazenou", "oznacz jako opłaconą"), False),
            ("invoice.list", _t("unpaid invoices", "neuhrazené faktury", "nieopłacone faktury"), False),
        ]),
    ]),
    ("komunikace", _t("Communication", "Komunikace", "Komunikacja"), Permission.crm_manage, [
        ("zaznamy", _t("Logging", "Záznamy", "Rejestrowanie"), [
            ("comm.log", _t("log a call", "zaloguj hovor / zaznamenej email", "zarejestruj rozmowę"), True),
            ("comm.list", _t("history", "historie komunikace", "historia"), True),
        ]),
    ]),
    ("material", _t("Materials & stock", "Materiál a sklad", "Materiały"), Permission.crm_manage, [
        ("objednavky", _t("Ordering", "Objednávky", "Zamówienia"), [
            ("material.order", _t("order material", "objednej materiál", "zamów materiał"), False),
        ]),
        ("sklad", _t("Stock", "Sklad", "Magazyn"), [
            ("material.check", _t("check stock", "kolik máme na skladě", "stan magazynu"), False),
        ]),
    ]),
    ("reporty", _t("Reports", "Reporty", "Raporty"), Permission.crm_manage, [
        ("vykazy", _t("Reports", "Výkazy", "Raporty"), [
            ("report.jobs", _t("jobs report", "report zakázek", "raport zleceń"), False),
        ]),
    ]),
    # Reserved empty modules for future growth (no commands yet, shown as planned).
    ("nastaveni", _t("Settings", "Nastavení", "Ustawienia"), Permission.company_manage, [
        ("obecne", _t("General", "Obecné", "Ogólne"), []),
    ]),
]


def _lang(code: str | None) -> str:
    c = (code or "cs").lower().split("-")[0]
    return c if c in ("en", "cs", "pl") else "cs"


def tree_for_user(user) -> dict:
    """Permission-filtered tree in the user's language. Empty branches kept."""
    lang = _lang(getattr(user, "preferred_language_code", None))
    perms = set(user.permissions)
    out = []
    for mkey, mtitle, perm, branches in COMMAND_TREE:
        if perm is not None and perm not in perms:
            continue
        bout = []
        for bkey, btitle, cmds in branches:
            cout = [{"intent": i, "phrase": ph.get(lang, ph["cs"]), "live": live}
                    for (i, ph, live) in cmds]
            bout.append({"key": bkey, "title": btitle.get(lang, btitle["cs"]),
                         "commands": cout})
        out.append({"key": mkey, "title": mtitle.get(lang, mtitle["cs"]),
                    "branches": bout})
    return {"language": lang, "modules": out}


def locate_intent(intent: str) -> dict | None:
    """Return where a command lives: module/branch keys + titles (cs).
    Used by alias learning to tell the user the correct location."""
    for mkey, mtitle, _perm, branches in COMMAND_TREE:
        for bkey, btitle, cmds in branches:
            for (i, _ph, live) in cmds:
                if i == intent:
                    return {"module_key": mkey, "module_title": mtitle["cs"],
                            "branch_key": bkey, "branch_title": btitle["cs"],
                            "live": live}
    return None


def all_intents() -> set:
    return {i for (_m, _mt, _p, branches) in COMMAND_TREE
            for (_b, _bt, cmds) in branches for (i, _ph, _l) in cmds}

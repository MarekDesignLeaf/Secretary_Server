"""Phase 1 — deterministic resolver: synonym dictionary, composition, bands,
ambiguity. Pure unit tests, no HTTP / DB / AI."""
from secretary_clean.core import voice_resolver as vr
from secretary_clean.core import voice_intent_registry as reg
from secretary_clean.core import voice_synonyms as syn
from secretary_clean.core.voice_intents import ParsedIntent


# ── normalization ─────────────────────────────────────────────────────────────
def test_normalize_strips_diacritics_punctuation_and_case():
    assert syn.normalize("Vytvoř KLIENTA, prosím!") == "vytvor klienta prosim"


def test_normalize_fixes_common_stt_error():
    assert "klienta" in syn.normalize("vytvoř klijenta")


# ── builtin synonym dictionary (stage C, EXPLICIT) ────────────────────────────
def test_builtin_synonym_resolves_client_create_czech():
    r = vr.resolve("založ klienta Jan Novák")
    assert r.intent == "client.create"
    assert r.source == "BUILTIN_SYNONYM"
    assert r.band == "HIGH"


def test_builtin_synonym_resolves_english_variant():
    r = vr.resolve("create a customer please")
    assert r.intent == "client.create"
    assert r.band == "HIGH"


def test_builtin_synonym_resolves_calendar_list_without_false_ambiguity():
    r = vr.resolve("co mám zítra")
    assert r.intent == "calendar.list"
    assert not r.is_ambiguous


# ── composition layer (action + object → intent, COMPOSED) ────────────────────
def test_composition_resolves_novel_verb_object_combo():
    # "udělej klienta" is not an explicit registry phrase but composes.
    r = vr.resolve("udělej mi klienta")
    assert r.intent == "client.create"


def test_composition_covers_planned_invoice_intent():
    r = vr.resolve("vytvoř fakturu")
    assert r.intent == "invoice.from_work_report"
    # Known intent, but not executable yet → resolver flags it.
    assert r.is_implemented is False


# ── explicit beats composed: no spurious ambiguity ────────────────────────────
def test_explicit_phrase_beats_composed_other_object():
    # task (explicit "vytvoř úkol") must win over client (composed via "klienta").
    r = vr.resolve("vytvoř úkol pro klienta Nováka")
    assert r.intent == "task.create"
    assert not r.is_ambiguous


# ── ambiguity: two composed intents tie → ask, never guess ────────────────────
def test_two_composed_intents_are_ambiguous():
    # Infinitive "vytvořit" is not an explicit registry phrase (those are
    # imperative "vytvoř"), so both objects only compose → genuine tie.
    # Mirrors the spec example "vytvořit klienta, nebo vytvořit úkol?".
    r = vr.resolve("vytvořit klienta nebo vytvořit úkol")
    assert r.is_ambiguous
    assert r.intent is None
    intents = {c[0] for c in r.candidates}
    assert intents == {"client.create", "task.create"}


# ── dangerous intents always require confirmation (§8) ────────────────────────
def test_dangerous_intent_requires_confirmation():
    r = vr.resolve("zruš schůzku s Petrem")
    assert r.intent == "calendar.delete"
    assert r.requires_confirmation is True


def test_whatsapp_send_requires_confirmation():
    r = vr.resolve("pošli whatsapp Petrovi")
    assert r.intent == "whatsapp.send"
    assert r.requires_confirmation is True


# ── pure cancel detection ─────────────────────────────────────────────────────
def test_pure_cancel_resolves_to_unknown_cancel():
    for word in ("omyl", "zruš to", "neplatný příkaz", "to nic", "zapomeň"):
        r = vr.resolve(word)
        assert r.intent == "unknown.cancel", word


def test_long_note_containing_cancel_word_is_not_cancel():
    r = vr.resolve("klient chtěl ještě zrušit starý záhon a vysadit nový trávník")
    assert r.intent != "unknown.cancel"


# ── alias lookup hook (stage D) ───────────────────────────────────────────────
def test_active_alias_hook_resolves():
    def lookup(norm):
        return {"intent": "client.create", "confidence": 1.0, "status": "ACTIVE"}
    r = vr.resolve("kobliha expres", alias_lookup=lookup)
    assert r.intent == "client.create"
    assert r.source == "USER_ALIAS"


def test_pending_alias_hook_marks_not_implemented():
    def lookup(norm):
        return {"intent": "invoice.from_work_report", "confidence": 1.0, "status": "PENDING"}
    r = vr.resolve("zafakturuj to", alias_lookup=lookup)
    assert r.source == "PENDING_ALIAS"
    assert r.is_implemented is False


def test_builtin_wins_over_alias_hook():
    # Builtin (stage C) runs before the alias hook (stage D).
    called = {"n": 0}
    def lookup(norm):
        called["n"] += 1
        return {"intent": "job.create", "status": "ACTIVE"}
    r = vr.resolve("založ klienta", alias_lookup=lookup)
    assert r.intent == "client.create"
    assert called["n"] == 0  # short-circuited before the hook


# ── parser fallback (stage E) ─────────────────────────────────────────────────
def test_parser_fallback_used_when_builtin_misses():
    def stub_parser(utterance, base=None):
        return ParsedIntent(intent="task.create", confidence=0.75,
                             entities={}, requires_confirmation=True)
    r = vr.resolve("qqzz nonsense phrase", alias_lookup=None, parser=stub_parser)
    assert r.intent == "task.create"
    assert r.source == "PARSER"


def test_unknown_when_everything_misses():
    def stub_parser(utterance, base=None):
        return ParsedIntent(intent=None, confidence=0.0)
    r = vr.resolve("qqzz nonsense phrase", parser=stub_parser)
    assert r.intent is None
    assert r.source == "UNKNOWN"
    assert r.band == "LOW"


# ── confidence bands ──────────────────────────────────────────────────────────
def test_band_thresholds():
    assert reg.band(0.95) == "HIGH"
    assert reg.band(0.85) == "HIGH"
    assert reg.band(0.70) == "MEDIUM"
    assert reg.band(0.55) == "MEDIUM"
    assert reg.band(0.50) == "LOW"


# ── registry invariants ───────────────────────────────────────────────────────
def test_every_composition_target_is_a_known_intent():
    for (action, obj), intent in syn.COMPOSITION.items():
        assert reg.is_known(intent), f"{action}+{obj} -> unknown {intent}"


def test_planned_intents_are_marked_not_implemented():
    assert reg.is_implemented("invoice.from_work_report") is False
    assert reg.is_implemented("quote.create") is False
    assert reg.is_implemented("client.create") is True


# Spec §9 names these as the intents needing broad (≈10 cs + 10 en) coverage.
# The composition layer (voice_synonyms) adds further generative coverage on top.
_SPEC_CORE = {
    "calendar.list", "calendar.create", "calendar.update", "calendar.delete",
    "client.create", "client.find", "task.create", "task.list",
    "job.create", "job.list", "work_report.start", "whatsapp.send",
}


def test_core_intents_have_broad_phrase_coverage():
    for code in _SPEC_CORE:
        assert len(reg.REGISTRY[code].all_phrases) >= 12, f"{code} has too few phrases"


def test_every_implemented_intent_has_some_coverage():
    for code, intent in reg.REGISTRY.items():
        if intent.is_implemented:
            assert len(intent.all_phrases) >= 6, f"{code} has too few phrases"

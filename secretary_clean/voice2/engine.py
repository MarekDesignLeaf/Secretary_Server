"""voice2 engine — the pipeline behind POST /api/v1/voice/execute.

One utterance in → one VoiceExecuteResult out, with:

  * multi-command segmentation (nlu.segment) + shared context between segments
  * resolution per segment: user/tenant ALIAS → deterministic parser →
    builtin synonyms (with ambiguity question) → AI fallback → learning dialog
  * slot filling through backend-owned pending actions (restart-safe)
  * enforced confirmation for DANGEROUS intents (registry §8 — finally real)
  * permission re-check at execution (alias = translation, never a grant)
  * read-back VERIFICATION of every write (voice2.verify)
  * durable per-user learning: AI hits become DB aliases; every resolution
    writes a learning event; alias usage is touched

The v1 response contract (VoiceExecuteResult fields, statuses, messages) is
preserved for single-command utterances; multi-command adds `data.commands`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from secretary_clean.core import ai_intent
from secretary_clean.core import help_content
from secretary_clean.core import contact_validation as cval
from secretary_clean.core import voice_intents as vi
from secretary_clean.core import voice_intent_registry as vreg
from secretary_clean.core import voice_learning_service as vls
from secretary_clean.core import voice_resolver as vres
from secretary_clean.core import voice_slots as vsl
from secretary_clean.core import voice_synonyms as vsyn
from secretary_clean.core.language import resolve_language_context
from secretary_clean.core.models import (
    PendingVoiceAction, VoiceExecuteResult, VoicePendingLearning,
)
from secretary_clean.voice2 import nlu, verify as vfy
from secretary_clean.voice2.handlers import HANDLERS, Ctx, H

PENDING_TTL_MIN = 30

_CANCEL_WORDS = ("zrus", "zrusit", "cancel", "nech to byt", "to staci", "stop",
                 "nechci", "zapomen na to", "uz ne", "konec", "omyl",
                 "neplatny prikaz", "anuluj")

_LEARN_PROMPT = ("Tomuto příkazu zatím nerozumím. K čemu ho mám přiřadit? "
                 "Řekni známý příkaz, třeba „vytvoř klienta“, nebo řekni zruš.")
_LEARN_RETRY = ("Nerozumím, ke kterému příkazu to patří. Řekni známý příkaz, "
                "třeba „vytvoř úkol“, nebo řekni zruš.")
_LEARN_MAX_ATTEMPTS = 2
_CONFIRM_MAX_ATTEMPTS = 2

# Free-text slot fill order for follow-up answers, per intent (the answer fills
# the FIRST still-empty slot in this order — v1 semantics, extended).
_ANSWER_ORDER = {
    "client.create": ("name", "phone", "address"),
    "task.create": ("title",),
    "job.create": ("title", "client"),
    "whatsapp.send": ("person", "message"),
    "job.change_status": ("new_status",),
    "client.note": ("person", "note"),
    "lead.create": ("name",),
    "task.assign": ("person",),
    "quote.create": ("client",),
    "calendar.update": (),
    "invoice.from_work_report": ("client",),
}

# Additional required slots for v2 intents (merged over voice_slots tables).
_EXTRA_SLOTS = {
    "client.note": [("person", "Ke kterému klientovi mám poznámku přidat?"),
                    ("note", "Co mám do poznámky napsat?")],
    "lead.create": [("name", "Jak se zájemce jmenuje?")],
    "task.assign": [("person", "Komu mám úkol přiřadit?")],
}


def _strip_diacritics(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def _is_cancel(text: str) -> bool:
    low = _strip_diacritics(" ".join(text.lower().split()))
    return any(w in low for w in _CANCEL_WORDS)


def _should_learn_unknown(utterance: str) -> bool:
    if vres.is_pure_cancel(utterance):
        return False
    words = vsyn.normalize(utterance).split()
    return 1 <= len(words) <= 6


def _missing_slots(intent: str, data: dict) -> list[str]:
    missing = vsl.missing_slots(intent, data)
    for key, _q in _EXTRA_SLOTS.get(intent, []):
        if not data.get(key) and key not in missing:
            missing.append(key)
    return missing


def _slot_question(intent: str, missing: list[str]) -> str | None:
    q = vsl.next_question(intent, missing)
    if q:
        return q
    if missing:
        for key, question in _EXTRA_SLOTS.get(intent, []):
            if key == missing[0]:
                return question
    return "Doplň prosím chybějící údaj."


def _merge_answer(intent: str, text: str, data: dict) -> dict:
    """Extract entities from a follow-up answer and merge into collected data."""
    d = dict(data)
    date_iso = vi.parse_date(text)
    hhmm = vi.parse_time(text)
    if date_iso:
        d["date"] = date_iso
    if hhmm:
        d["time"] = hhmm
    if d.get("date"):
        t = d.get("time")
        d["start_at"] = f"{d['date']}T{t}:00Z" if t else f"{d['date']}T00:00:00Z"
    person = vi.extract_person(text)
    if person:
        d["person"] = person
    if intent == "calendar.create":
        answered_when = bool(date_iso or hhmm)
        if not answered_when and not d.get("title") and not d.get("person") and text.strip():
            d["title"] = text.strip()
    ans = text.strip()
    for slot in _ANSWER_ORDER.get(intent, ()):
        if not d.get(slot):
            d[slot] = ans
            break
    return d


def _normalize_contact_fields(intent: str, data: dict):
    if intent != "client.create":
        return None, None
    if data.get("phone"):
        norm, err = cval.normalize_phone(data["phone"])
        if err:
            bad = data["phone"]
            data["phone"] = None
            return "phone", (f"Telefon „{bad}“ {err}. "
                             f"Řekni platné telefonní číslo klienta.")
        data["phone"] = norm
    if data.get("email"):
        norm, err = cval.normalize_email(data["email"])
        if err:
            bad = data["email"]
            data["email"] = None
            return "email", f"E-mail „{bad}“ {err}. Řekni platný e-mail klienta."
        data["email"] = norm
    return None, None


# ── engine ────────────────────────────────────────────────────────────────────

class _Reply(Exception):
    """Internal: carry a finished VoiceExecuteResult up the stack."""

    def __init__(self, result: VoiceExecuteResult):
        self.result = result


class Engine:
    def __init__(self, payload, user, repository):
        self.payload = payload
        self.user = user
        self.repository = repository
        self.lang_ctx = self._lang_ctx()
        self.commands: list[dict] = []          # per-command report entries

    # -- language / response plumbing (v1-compatible) --------------------------
    def _lang_ctx(self):
        profile = self.repository.get_tenant_operating_profile(self.user.company_id)
        client_language = self.repository.get_client_preferred_language_code(
            self.user.company_id, self.payload.client_id)
        return resolve_language_context(profile=profile, user=self.user,
                                        client_language_code=client_language)

    def _app_lang(self) -> str:
        lang = (getattr(self.lang_ctx, "voice_output_language_code", "") or "").split("-")[0].lower()
        if lang not in ("cs", "en", "pl"):
            prof = self.repository.get_tenant_operating_profile(self.user.company_id)
            lang = (getattr(prof, "default_internal_language_code", "") or "cs").split("-")[0].lower()
        return lang

    def _localize(self, msg):
        lang = self._app_lang()
        if not msg or lang == "cs":
            return msg
        target = {"en": "English", "pl": "Polish"}.get(lang)
        if not target:
            return msg
        from secretary_clean.core import translation as _tr
        if not _tr.is_configured():
            return msg
        ok, out, _err = _tr.translate_text(msg, target, "Czech")
        return out if ok and out else msg

    def res(self, executed, message, status="executed", action=None, entity_id=None,
            data=None, needs_confirm=False, missing=None, question=None,
            pending_id=None) -> VoiceExecuteResult:
        d = dict(data or {})
        if self.commands:
            d.setdefault("commands", self.commands)
        return VoiceExecuteResult(
            executed=executed, resolved_intent=action,
            requires_confirmation=needs_confirm,
            message=self._localize(message), action=action, entity_id=entity_id,
            data=d, status=status, missing_fields=missing or [],
            question=self._localize(question), pending_action_id=pending_id,
            language_context=self.lang_ctx,
        )

    # -- entry point ------------------------------------------------------------
    def run(self) -> VoiceExecuteResult:
        try:
            return self._run()
        except _Reply as r:
            return r.result

    def _run(self) -> VoiceExecuteResult:
        payload, user, repository = self.payload, self.user, self.repository

        # HELP (never interrupts a dialog)
        if not payload.pending_action_id:
            is_h, rest = help_content.is_help(payload.utterance)
            if is_h:
                if not rest:
                    return self.res(True, help_content.spoken_overview(user), action="help")
                sec = help_content.find_section(user, rest)
                if sec is not None:
                    return self.res(True, help_content.spoken_section(user, sec), action="help")
                return self.res(True, help_content.spoken_overview(user), action="help")

        pending = None
        if payload.pending_action_id:
            pending = repository.get_pending_action(payload.pending_action_id, user.company_id)
            if pending and pending.status != "needs_more_info":
                pending = None
            # v2 fix: enforce the TTL that v1 set but never checked.
            if pending and pending.expires_at:
                exp = pending.expires_at
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp < datetime.now(timezone.utc):
                    pending.status = "cancelled"
                    repository.update_pending_action(pending)
                    pending = None

        if pending and pending.intent == "alias.learn":
            return self._continue_learning(pending)

        if pending and _is_cancel(payload.utterance):
            pending.status = "cancelled"
            repository.update_pending_action(pending)
            return self.res(False, "Dobře, akci jsem zrušila.", status="cancelled",
                            action=pending.intent, pending_id=pending.id)

        ctx = nlu.SegmentContext()

        if pending:
            cd = dict(pending.collected_data or {})
            queue = list(cd.pop("_queue", []) or [])
            await_confirm = bool(cd.pop("_await_confirm", False))
            attempts = int(cd.pop("_confirm_attempts", 0))
            intent = pending.intent
            if await_confirm:
                if nlu.is_confirm(payload.utterance):
                    cd["_confirmed"] = True   # sticks across later slot dialogs
                    self._finish_pending(pending, cd)
                    self._execute_step(intent, cd, "CONTINUATION", ctx,
                                       queue=queue, confirmed=True,
                                       raw=cd.get("raw") or payload.utterance)
                else:
                    attempts += 1
                    if attempts >= _CONFIRM_MAX_ATTEMPTS:
                        pending.status = "cancelled"
                        repository.update_pending_action(pending)
                        return self.res(False, "Dobře, akci jsem zrušila.",
                                        status="cancelled", action=intent,
                                        pending_id=pending.id)
                    q = pending.last_question or "Mám to opravdu provést? Řekni ano, nebo zruš."
                    cd["_await_confirm"] = True
                    cd["_confirm_attempts"] = attempts
                    if queue:
                        cd["_queue"] = queue
                    pending.collected_data = cd
                    repository.update_pending_action(pending)
                    raise _Reply(self.res(False, q, status="needs_more_info",
                                          action=intent, needs_confirm=True,
                                          question=q, missing=["confirmation"],
                                          pending_id=pending.id))
            else:
                data = _merge_answer(intent, payload.utterance, cd)
                self._process_step(intent, data, "CONTINUATION", ctx, queue=queue,
                                   pending=pending, raw=data.get("raw") or payload.utterance)
        else:
            segments = nlu.segment(payload.utterance)
            queue = segments[1:]
            self._process_segment(segments[0], ctx, queue=queue,
                                  single=(len(segments) == 1))

        # queue processed inside the step methods; assemble the final report
        return self._final_result()

    # -- pending helpers --------------------------------------------------------
    def _finish_pending(self, pending, data):
        pending.collected_data = data
        pending.missing_fields = []
        pending.status = "executed"
        self.repository.update_pending_action(pending)

    def _park(self, intent, data, question, missing, queue, *, needs_confirm=False,
              pending=None, status="needs_more_info", executed_so_far=True):
        """Persist a pending action (with the remaining queue) and reply."""
        now = datetime.now(timezone.utc)
        cd = dict(data)
        if queue:
            cd["_queue"] = list(queue)
        if needs_confirm:
            cd["_await_confirm"] = True
        if pending:
            pending.collected_data = cd
            pending.missing_fields = missing
            pending.last_question = question
            pending.status = "needs_more_info"
            self.repository.update_pending_action(pending)
            pid = pending.id
        else:
            pa = PendingVoiceAction(
                id=str(uuid.uuid4()), company_id=self.user.company_id,
                user_id=self.user.id, intent=intent, status="needs_more_info",
                collected_data=cd, missing_fields=missing, last_question=question,
                created_at=now, updated_at=now,
                expires_at=now + timedelta(minutes=PENDING_TTL_MIN))
            self.repository.create_pending_action(pa)
            pid = pa.id
        prefix = ""
        if self.commands:
            done = [c["message"] for c in self.commands if c.get("executed")]
            if done:
                prefix = " ".join(done) + " "
        raise _Reply(self.res(False, prefix + question, status=status, action=intent,
                              data=data, needs_confirm=needs_confirm,
                              missing=missing, question=question, pending_id=pid))

    # -- learning dialog (ported from v1, unchanged semantics) ------------------
    def _continue_learning(self, pending) -> VoiceExecuteResult:
        payload, user, repository = self.payload, self.user, self.repository
        cd = dict(pending.collected_data or {})
        pl = (repository.get_voice_pending_learning(cd.get("learning_id"), user.company_id)
              if cd.get("learning_id") else None)
        unknown_phrase = cd.get("unknown_phrase") or (pl.unknown_phrase if pl else "")

        def _finish(state, pa_status):
            if pl:
                pl.state = state
                repository.update_voice_pending_learning(pl)
            pending.status = pa_status
            repository.update_pending_action(pending)

        if vres.is_pure_cancel(payload.utterance):
            _finish("CANCELLED", "cancelled")
            vls.record_event(repository, user.company_id, user.id, unknown_phrase,
                             "CANCELLED")
            return self.res(False, "Dobře, nic neukládám.", status="cancelled",
                            pending_id=pending.id)

        target = vls.resolve_target_intent(payload.utterance)
        if not target:
            attempt = int(cd.get("attempt", 0)) + 1
            if attempt >= _LEARN_MAX_ATTEMPTS:
                _finish("CANCELLED", "cancelled")
                vls.record_event(repository, user.company_id, user.id, unknown_phrase,
                                 "UNKNOWN", metadata={"gave_up": True})
                return self.res(False, "Nevadí, zkusíme to jindy.", status="cancelled",
                                pending_id=pending.id)
            cd["attempt"] = attempt
            pending.collected_data = cd
            pending.last_question = _LEARN_RETRY
            repository.update_pending_action(pending)
            return self.res(False, _LEARN_RETRY, status="needs_more_info",
                            question=_LEARN_RETRY, missing=["target"],
                            pending_id=pending.id)

        norm = vsyn.normalize(unknown_phrase)
        existing = vls.find_exact_alias(repository, user.company_id, user.id, norm)
        if existing is not None:
            existing.raw_phrase = unknown_phrase
            existing.target_intent = target
            existing.status = vls.status_for(target)
            repository.update_voice_alias(existing)
            alias = existing
        else:
            alias = vls.new_alias(user.company_id, user.id, unknown_phrase, target,
                                  created_by=user.id)
            repository.create_voice_alias(alias)
        _finish("COMPLETED", "executed")
        rtype = "USER_ALIAS" if alias.status == "ACTIVE" else "PENDING_ALIAS"
        vls.record_event(repository, user.company_id, user.id, unknown_phrase, rtype,
                         resolved_intent=target, created_alias_id=alias.id)
        if alias.status == "ACTIVE":
            msg = f"Rozumím. Příště „{unknown_phrase}“ provedu jako {target}."
        else:
            msg = (f"Zapamatováno. „{unknown_phrase}“ spustí {target}, "
                   f"jakmile tu funkci přidáme.")
        return self.res(True, msg, action="alias.create", entity_id=alias.id,
                        data={"alias": alias.model_dump(mode="json")})

    # -- resolution -------------------------------------------------------------
    def _resolve(self, text: str, *, single: bool, queue: list):
        """Return (intent, data, learn_source) or raise _Reply for questions/
        learning dialogs/unknowns."""
        user, repository = self.user, self.repository
        norm = vsyn.normalize(text)

        alias = repository.find_voice_alias(user.company_id, norm, user.id)
        if alias and alias.status == "ACTIVE":
            repository.touch_voice_alias(alias.id, user.company_id)
            rp = vi.parse_intent(text)
            data = dict(rp.entities) if rp.intent == alias.target_intent \
                else nlu.entities_from_text(alias.target_intent, text)
            return alias.target_intent, data, "USER_ALIAS"
        if alias and alias.status == "PENDING":
            vls.record_event(repository, user.company_id, user.id, text,
                             "PENDING_ALIAS", resolved_intent=alias.target_intent)
            ri = vreg.get(alias.target_intent)
            raise _Reply(self.res(False, ri.fallback_message if ri else
                                  "Tento příkaz zatím neumím vykonat.",
                                  status="error", action=alias.target_intent))

        parsed = vi.parse_intent(text)
        if parsed.intent:
            return parsed.intent, dict(parsed.entities), "PARSER"

        alias_lookup = vls.build_alias_lookup(repository, user.company_id, user.id)
        resolution = vres.resolve(text, alias_lookup=alias_lookup)
        if resolution.is_ambiguous and len(resolution.candidates) >= 2:
            a, b = resolution.candidates[0][0], resolution.candidates[1][0]
            da = (vreg.get(a).description if vreg.get(a) else a)
            db = (vreg.get(b).description if vreg.get(b) else b)
            q = f"Nerozumím přesně. Myslíš {da}, nebo {db}?"
            vls.record_event(repository, user.company_id, user.id, text, "AMBIGUOUS",
                             metadata={"candidates": [a, b]})
            raise _Reply(self.res(False, q, status="needs_more_info", action=None,
                                  question=q, data={"candidates": [a, b]}))
        if resolution.intent and resolution.source in ("BUILTIN_SYNONYM", "USER_ALIAS"):
            if not resolution.is_implemented:
                vls.record_event(repository, user.company_id, user.id, text,
                                 "PENDING_ALIAS", resolved_intent=resolution.intent)
                ri = vreg.get(resolution.intent)
                raise _Reply(self.res(False, ri.fallback_message if ri else
                                      "Tento příkaz zatím neumím vykonat.",
                                      status="error", action=resolution.intent))
            rp = vi.parse_intent(text)
            data = dict(rp.entities) if rp.intent == resolution.intent \
                else nlu.entities_from_text(resolution.intent, text)
            return resolution.intent, data, resolution.source

        ai = ai_intent.classify(text, getattr(self.lang_ctx, "internal", None))
        if ai and ai.get("intent"):
            intent = ai["intent"]
            data = dict(ai.get("entities") or {})
            data.setdefault("raw", text)
            self._persist_ai_alias(text, intent, ai.get("confidence"))
            return intent, data, "AI_FALLBACK"

        # UNKNOWN
        vls.record_event(repository, user.company_id, user.id, text, "UNKNOWN")
        if single and _should_learn_unknown(text):
            now = datetime.now(timezone.utc)
            pl = VoicePendingLearning(
                id=str(uuid.uuid4()), company_id=user.company_id, user_id=user.id,
                unknown_phrase=text, normalized_unknown_phrase=vsyn.normalize(text),
                state="WAITING_FOR_TARGET", attempt_count=0, created_at=now,
                expires_at=now + timedelta(minutes=PENDING_TTL_MIN))
            repository.create_voice_pending_learning(pl)
            pa = PendingVoiceAction(
                id=str(uuid.uuid4()), company_id=user.company_id, user_id=user.id,
                intent="alias.learn", status="needs_more_info",
                collected_data={"learning_id": pl.id, "unknown_phrase": text,
                                "attempt": 0},
                missing_fields=["target"], last_question=_LEARN_PROMPT,
                created_at=now, updated_at=now,
                expires_at=now + timedelta(minutes=PENDING_TTL_MIN))
            repository.create_pending_action(pa)
            raise _Reply(self.res(False, _LEARN_PROMPT, status="needs_more_info",
                                  question=_LEARN_PROMPT, missing=["target"],
                                  pending_id=pa.id))
        if single:
            raise _Reply(self.res(False, "Nerozuměl jsem příkazu. Můžeš to říct jinak?",
                                  status="error"))
        # multi-command: report the failed clause and carry on
        self.commands.append({"utterance": text, "intent": None, "executed": False,
                              "status": "error",
                              "message": f"Části „{text}“ jsem nerozuměla."})
        return None, {}, "UNKNOWN"

    def _persist_ai_alias(self, text: str, intent: str, confidence) -> None:
        """Durable learning: an AI-resolved phrasing becomes a per-user alias so
        next time it resolves deterministically (and survives restarts)."""
        try:
            repository, user = self.repository, self.user
            norm = vsyn.normalize(text)
            if repository.find_voice_alias(user.company_id, norm, user.id):
                return
            alias = vls.new_alias(user.company_id, user.id, text, intent,
                                  source="ai_learning", created_by=user.id,
                                  confidence=float(confidence or 0.8))
            repository.create_voice_alias(alias)
        except Exception:  # noqa: BLE001 — learning must never break the command
            pass

    # -- step processing ----------------------------------------------------------
    def _process_segment(self, text: str, ctx: nlu.SegmentContext, *, queue: list,
                         single: bool):
        resolved = self._resolve(text, single=single, queue=queue)
        intent, data, source = resolved
        if intent is None:                      # unresolvable clause in a batch
            self._continue_queue(ctx, queue)
            return
        data.setdefault("raw", text)
        data = ctx.enrich(text, data)
        self._process_step(intent, data, source, ctx, queue=queue, raw=text)

    def _process_step(self, intent: str, data: dict, source: str,
                      ctx: nlu.SegmentContext, *, queue: list, pending=None,
                      raw: str = ""):
        missing = _missing_slots(intent, data)
        if missing:
            q = _slot_question(intent, missing)
            self._park(intent, data, q, missing, queue, pending=pending)

        bad_slot, bad_msg = _normalize_contact_fields(intent, data)
        if bad_slot:
            self._park(intent, data, bad_msg, [bad_slot], queue, pending=pending)

        if pending:
            self._finish_pending(pending, data)

        self._execute_step(intent, data, source, ctx, queue=queue,
                           confirmed=bool(self.payload.confirmed)
                           or bool(data.get("_confirmed")), raw=raw)

    def _execute_step(self, intent: str, data: dict, source: str,
                      ctx: nlu.SegmentContext, *, queue: list, confirmed: bool,
                      raw: str = ""):
        user, repository = self.user, self.repository

        # permission at execution — alias is a translation, not a grant
        req_perm = vreg.required_permission(intent)
        if req_perm and req_perm not in {p.value for p in user.permissions}:
            vls.record_event(repository, user.company_id, user.id,
                             raw or self.payload.utterance, "FAILED_PERMISSION",
                             resolved_intent=intent)
            self.commands.append({"utterance": raw, "intent": intent,
                                  "executed": False, "status": "error",
                                  "message": "Na tento příkaz nemáš oprávnění."})
            raise _Reply(self.res(False, "Na tento příkaz nemáš oprávnění.",
                                  status="error", action=intent))

        handler = HANDLERS.get(intent)
        if handler is None:
            self.commands.append({"utterance": raw, "intent": intent,
                                  "executed": False, "status": "error",
                                  "message": f"Intent '{intent}' zatim neumim vykonat."})
            raise _Reply(self.res(False, f"Intent '{intent}' zatim neumim vykonat.",
                                  status="error", action=intent))

        base_utter = raw or self.payload.utterance

        # dangerous → confirm, but only AFTER checking the action is feasible.
        # A dry-run lets the handler surface a missing slot or a "not found"
        # error before we bother the user with a yes/no, and confirms only the
        # real write. Handlers that don't implement dry_run fall through and the
        # generic confirmation applies.
        if vreg.requires_confirmation(intent) and not confirmed:
            dctx = Ctx(user=user, repository=repository, utterance=base_utter,
                       client_id=self.payload.client_id, dry_run=True)
            try:
                pre: H = handler(dctx, data)
            except Exception:  # noqa: BLE001 — treat as "no preview available"
                pre = None
            if pre is not None and pre.status in ("error", "needs_more_info"):
                if pre.status == "needs_more_info":
                    self._park(intent, data, pre.question or pre.message,
                               pre.missing or ["value"], queue)
                self.commands.append({"utterance": raw, "intent": intent,
                                      "executed": False, "status": "error",
                                      "message": pre.message})
                raise _Reply(self.res(False, pre.message, status="error", action=intent))
            desc = vreg.get(intent).description if vreg.get(intent) else intent
            q = (pre.message if pre is not None and pre.status == "ready"
                 else f"Mám opravdu provést: {desc}? Řekni ano, nebo zruš.")
            self._park(intent, data, q, ["confirmation"], queue, needs_confirm=True)

        if source != "CONTINUATION":
            # fresh resolution → audit it (continuations were logged when fresh)
            vls.record_event(repository, user.company_id, user.id,
                             base_utter, source, resolved_intent=intent,
                             was_executed=True, was_confirmed=confirmed)

        hctx = Ctx(user=user, repository=repository,
                   utterance=base_utter, client_id=self.payload.client_id)
        try:
            h: H = handler(hctx, data)
        except Exception as exc:  # noqa: BLE001 — a failed step must be reported, not a 500
            self.commands.append({"utterance": raw, "intent": intent,
                                  "executed": False, "status": "error",
                                  "message": f"Akce selhala: {exc}."})
            raise _Reply(self.res(False, f"Akce selhala: {exc}.", status="error",
                                  action=intent))

        if h.status == "needs_more_info":
            self._park(intent, data, h.question or h.message, h.missing or ["value"],
                       queue)

        # read-back verification of writes
        verification = None
        if h.executed and h.verify_kind and h.entity_id:
            vr = vfy.run(repository, user.company_id,
                         vfy.VerifySpec(kind=h.verify_kind, entity_id=h.entity_id,
                                        expected=h.verify_expected))
            verification = vr.as_dict()
            h.data["verified"] = vr.verified
            h.data["verification"] = verification
            if not vr.verified:
                h.message += " Upozornění: zápis se nepodařilo ověřit zpětným čtením."

        self.commands.append({
            "utterance": raw, "intent": intent, "executed": h.executed,
            "status": h.status, "entity_id": h.entity_id, "message": h.message,
            **({"verified": h.data.get("verified")} if "verified" in h.data else {}),
        })
        self._last = (intent, h)
        ctx.absorb(intent, data,
                   entity_kind=(h.verify_kind or "").split(":")[-1].rstrip("s") or None
                   if h.verify_kind else None,
                   entity_id=h.entity_id)
        if h.entity_id and intent.startswith("job."):
            ctx.last_entity = ("job", h.entity_id)

        self._continue_queue(ctx, queue)

    def _continue_queue(self, ctx: nlu.SegmentContext, queue: list):
        if not queue:
            return
        rest = list(queue)
        nxt = rest.pop(0)
        self._process_segment(nxt, ctx, queue=rest, single=False)

    # -- final assembly -----------------------------------------------------------
    def _final_result(self) -> VoiceExecuteResult:
        if not self.commands:
            return self.res(False, "Nerozuměl jsem příkazu. Můžeš to říct jinak?",
                            status="error")
        if len(self.commands) == 1 and hasattr(self, "_last"):
            intent, h = self._last
            return self.res(h.executed, h.message, status=h.status, action=intent,
                            entity_id=h.entity_id, data=h.data)
        executed_all = all(c.get("executed") for c in self.commands)
        executed_any = any(c.get("executed") for c in self.commands)
        message = " ".join(c["message"] for c in self.commands)
        last_intent = next((c["intent"] for c in reversed(self.commands)
                            if c.get("intent")), None)
        last_id = next((c.get("entity_id") for c in reversed(self.commands)
                        if c.get("entity_id")), None)
        data = dict(getattr(self, "_last", (None, H(False, "")))[1].data or {}) \
            if hasattr(self, "_last") else {}
        data["commands"] = self.commands
        status = "executed" if executed_all else ("partial" if executed_any else "error")
        return self.res(executed_any, message, status=status, action=last_intent,
                        entity_id=last_id, data=data)


def execute(payload, user, repository) -> VoiceExecuteResult:
    return Engine(payload, user, repository).run()

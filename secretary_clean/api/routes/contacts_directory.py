"""Shared Contacts Directory — an address book grouped by sections (the Android
"Kontakty" tab). Separate from CRM clients. The contacts are stored as generic
CRM records under the "contacts" module; sections (groups) are seeded per company
so the import dialog always has groups to assign to.

Endpoints (all tenant-scoped):
  GET    /crm/contact-sections          list groups (seeds defaults on first use)
  POST   /crm/contact-sections          create a custom group
  GET    /crm/contacts                  list shared contacts
  POST   /crm/contacts                  create a contact
  PUT    /crm/contacts/{id}             edit a contact
  DELETE /crm/contacts/{id}             soft-delete a contact
  GET    /crm/contacts/duplicates       duplicate pairs (same phone / same name)
  GET    /crm/contacts/sort-session     contacts for the voice-sorting flow
  POST   /crm/contacts/assign-section   set a contact's group (by id or name+phone)
  POST   /crm/contacts/merge            merge two contacts
  POST   /crm/contacts/import           bulk import selected device contacts
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from secretary_clean.api.deps import current_user, get_repository, require_permission
from secretary_clean.core import contact_validation as cval
from secretary_clean.core.contact_sections import slugify_section
from secretary_clean.core.crm_shapes import iso
from secretary_clean.core.models import CRMUpdateRequest, Permission, UserAccount
from secretary_clean.core.repository import InMemorySecretaryRepository

router = APIRouter(prefix="/crm", tags=["contacts directory"])

# Contact fields persisted in the CRM record's data blob.
_CONTACT_KEYS = ("section_code", "company_name", "phone_primary", "email_primary",
                 "address", "address_line1", "city", "postcode", "country",
                 "notes", "source", "contact_key")


def _records(repository, company_id):
    return [r for r in repository.list_crm_records("contacts", company_id)
            if r.status != "deleted"]


def _sec_names(repository, company_id) -> dict[str, str]:
    return {s["section_code"]: s["display_name"]
            for s in repository.list_contact_sections(company_id)}


def _contact_out(rec, sec_names: dict[str, str]) -> dict:
    d = rec.data or {}
    code = d.get("section_code") or ""
    return {
        "id": rec.id,
        "section_code": code,
        "section_name": sec_names.get(code),
        "display_name": rec.name,
        "company_name": d.get("company_name"),
        "phone_primary": d.get("phone_primary"),
        "email_primary": d.get("email_primary"),
        "address": d.get("address"),
        "address_line1": d.get("address_line1"),
        "city": d.get("city"),
        "postcode": d.get("postcode"),
        "country": d.get("country"),
        "notes": d.get("notes"),
        "source": d.get("source"),
        "created_at": iso(rec.created_at),
        "updated_at": iso(rec.updated_at),
    }


def _contact_data(payload: dict) -> tuple[str, dict, list[str]]:
    """Validate/normalize an inbound contact payload → (display_name, data, errors)."""
    clean, errors = cval.validate_and_normalize(payload)
    name = str(clean.get("display_name") or "").strip()
    data = {k: clean.get(k) for k in _CONTACT_KEYS if k in clean}
    return name, data, errors


def _digits(s) -> str:
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def _match_key(phone) -> str:
    """Country-neutral dedup key: the last 9 digits (national significant
    number). Treats "+44 7911 000111" and "07911 000111" as the same number."""
    d = _digits(phone)
    return d[-9:] if len(d) >= 9 else d


# ── Sections (groups) ─────────────────────────────────────────────────────────
@router.get("/contact-sections")
def list_contact_sections(
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    return repository.list_contact_sections(user.company_id)


@router.post("/contact-sections")
def create_contact_section(
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    name = str(payload.get("display_name") or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Název skupiny je povinný.")
    code = (str(payload.get("section_code") or "").strip() or slugify_section(name))
    existing = {s["section_code"] for s in repository.list_contact_sections(user.company_id)}
    base, n = code, 2
    while code in existing:
        code = f"{base}_{n}"
        n += 1
    return repository.create_contact_section(user.company_id, code, name,
                                             sort_order=200, is_default=False)


# ── Contacts ──────────────────────────────────────────────────────────────────
@router.get("/contacts")
def list_contacts(
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    names = _sec_names(repository, user.company_id)
    return [_contact_out(r, names) for r in _records(repository, user.company_id)]


@router.post("/contacts")
def create_contact(
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    name, data, errors = _contact_data(payload)
    if errors:
        raise HTTPException(status_code=422, detail=" ".join(errors))
    if not name:
        raise HTTPException(status_code=422, detail="Jméno kontaktu je povinné.")
    data.setdefault("source", "manual")
    rec = repository.create_crm_record("contacts", user.company_id, name, data)
    repository.log_activity(user.company_id, user.id, "contact", rec.id,
                            "create", f"Contact created: {name}")
    return _contact_out(rec, _sec_names(repository, user.company_id))


@router.put("/contacts/{contact_id}")
def update_contact(
    contact_id: str,
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    name, data, errors = _contact_data(payload)
    if errors:
        raise HTTPException(status_code=422, detail=" ".join(errors))
    try:
        rec = repository.update_crm_record(
            "contacts", contact_id, user.company_id,
            CRMUpdateRequest(name=name or None, data=data or None))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Kontakt nenalezen.") from exc
    return _contact_out(rec, _sec_names(repository, user.company_id))


@router.delete("/contacts/{contact_id}")
def delete_contact(
    contact_id: str,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    try:
        repository.delete_crm_record("contacts", contact_id, user.company_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Kontakt nenalezen.") from exc
    return {"ok": True, "id": contact_id, "status": "deleted"}


@router.get("/contacts/duplicates")
def contact_duplicates(
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    recs = _records(repository, user.company_id)
    pairs, seen_phone, seen_name = [], {}, {}
    for r in recs:
        d = r.data or {}
        ph = _match_key(d.get("phone_primary"))
        nm = (r.name or "").strip().lower()
        if ph and len(ph) >= 7:
            if ph in seen_phone:
                pairs.append((seen_phone[ph], r, "same_phone"))
            else:
                seen_phone[ph] = r
        if nm:
            if nm in seen_name:
                pairs.append((seen_name[nm], r, "same_name"))
            else:
                seen_name[nm] = r
    out = []
    for a, b, reason in pairs:
        da, db = a.data or {}, b.data or {}
        out.append({
            "id1": a.id, "name1": a.name, "phone1": da.get("phone_primary"),
            "section1": da.get("section_code"),
            "id2": b.id, "name2": b.name, "phone2": db.get("phone_primary"),
            "section2": db.get("section_code"), "reason": reason,
        })
    return {"duplicates": out}


@router.get("/contacts/sort-session")
def contacts_for_sorting(
    sort_by: str = Query("name"),
    phone_prefix: str = Query("+44"),
    filter_role: str = Query("unclassified"),
    limit: int = Query(500, ge=1, le=2000),
    user: UserAccount = Depends(current_user),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    out = []
    for r in _records(repository, user.company_id):
        d = r.data or {}
        role = d.get("section_code") or ""
        if filter_role == "unclassified" and role:
            continue
        out.append({"id": r.id, "display_name": r.name,
                    "phone_primary": d.get("phone_primary") or "",
                    "contact_role": role or None})
    return {"contacts": out[:limit], "total": len(out)}


@router.post("/contacts/assign-section")
def assign_section(
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    code = str(payload.get("section_code") or "").strip()
    if not code:
        raise HTTPException(status_code=422, detail="Chybí skupina.")
    contact_id = payload.get("contact_id")
    target = None
    if contact_id:
        try:
            target = repository.get_crm_record("contacts", str(contact_id), user.company_id)
        except KeyError:
            target = None
    if target is None:
        # Match by name (+ phone) among existing contacts.
        nm = str(payload.get("display_name") or "").strip().lower()
        ph = _digits(payload.get("phone"))
        for r in _records(repository, user.company_id):
            d = r.data or {}
            if nm and (r.name or "").strip().lower() == nm and \
               (not ph or _digits(d.get("phone_primary")) == ph):
                target = r
                break
    if target is None:
        # Create it on the fly so voice-sorting a device contact persists.
        data = {"section_code": code, "phone_primary": payload.get("phone"),
                "source": "voice_sort"}
        rec = repository.create_crm_record(
            "contacts", user.company_id,
            str(payload.get("display_name") or "Kontakt").strip(), data)
        return {"ok": True, "id": rec.id, "created": True}
    repository.update_crm_record("contacts", target.id, user.company_id,
                                 CRMUpdateRequest(data={"section_code": code}))
    return {"ok": True, "id": target.id, "created": False}


@router.post("/contacts/merge")
def merge_contacts(
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    pid = str(payload.get("primary_id") or "")
    sid = str(payload.get("secondary_id") or "")
    if not pid or not sid or pid == sid:
        raise HTTPException(status_code=422, detail="Neplatné kontakty ke sloučení.")
    try:
        primary = repository.get_crm_record("contacts", pid, user.company_id)
        secondary = repository.get_crm_record("contacts", sid, user.company_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Kontakt nenalezen.") from exc
    if not primary or not secondary:
        raise HTTPException(status_code=404, detail="Kontakt nenalezen.")
    # Fill primary's empty fields from the secondary, then soft-delete secondary.
    merged = dict(primary.data or {})
    for k, v in (secondary.data or {}).items():
        if not merged.get(k) and v:
            merged[k] = v
    repository.update_crm_record("contacts", pid, user.company_id,
                                 CRMUpdateRequest(data=merged))
    repository.delete_crm_record("contacts", sid, user.company_id)
    return {"ok": True, "primary_id": pid, "merged_from": sid}


@router.post("/contacts/import")
def import_contacts(
    payload: dict,
    user: UserAccount = Depends(require_permission(Permission.crm_manage)),
    repository: InMemorySecretaryRepository = Depends(get_repository),
):
    contacts = payload.get("contacts") or []
    imported, merged, errors = 0, 0, []
    existing = _records(repository, user.company_id)
    by_phone = {_match_key((r.data or {}).get("phone_primary")): r
                for r in existing if _match_key((r.data or {}).get("phone_primary"))}
    by_name = {(r.name or "").strip().lower(): r for r in existing if r.name}
    for c in contacts:
        if c.get("selected") is False:
            continue
        name = str(c.get("display_name") or "").strip()
        if not name:
            continue
        phone, _pe = cval.normalize_phone(c.get("phone_primary") or c.get("phone"))
        email, _ee = cval.normalize_email(c.get("email_primary") or c.get("email"))
        data = {
            "section_code": str(c.get("section_code") or "").strip(),
            "phone_primary": phone, "email_primary": email,
            "address": c.get("address"), "address_line1": c.get("address_line1"),
            "city": c.get("city"), "postcode": c.get("postcode"),
            "country": c.get("country"), "source": "import",
            "contact_key": c.get("contact_key"),
        }
        dup = (by_phone.get(_match_key(phone)) if phone else None) or by_name.get(name.lower())
        if dup is not None:
            repository.update_crm_record("contacts", dup.id, user.company_id,
                                         CRMUpdateRequest(name=name, data=data))
            merged += 1
        else:
            rec = repository.create_crm_record("contacts", user.company_id, name, data)
            by_name[name.lower()] = rec
            if phone:
                by_phone[_match_key(phone)] = rec
            imported += 1
    if imported or merged:
        repository.log_activity(user.company_id, user.id, "contact", "", "import",
                                f"Imported {imported} contacts ({merged} merged)",
                                source_channel="app")
    return {"imported": imported, "merged": merged, "errors": errors}

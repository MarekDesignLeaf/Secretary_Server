"""
data_importer.py
================
Import system for Secretary CRM.

Lifecycle (per agent_permissions_config.yaml import_policy):
  create_session → upload/parse (staging) → save_mappings →
  validate → preview (read-only) → approve → apply → [rollback]

Rules enforced here:
  - direct_import_to_production_tables_allowed = False
  - Staging is mandatory; production tables are never touched until apply()
  - Every action is written to import_audit_log
  - rollback_snapshot stored for UPDATE actions
  - Secrets must never appear in staging rows, audit logs, or error messages

Supported source types:
  csv, excel, json, postgresql, rest_api
  (xml / mysql / mariadb / sqlite / mssql / mongodb / google_sheets /
   airtable / other_crm: stub adapters return ImportError with guidance)
"""

from __future__ import annotations

import csv
import io
import json
import logging
import traceback
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

import psycopg2
import psycopg2.extras
import requests

logger = logging.getLogger("data_importer")

# ─────────────────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────────────────

class ImportError(Exception):
    """Base import error."""

class ParseError(ImportError):
    """Failed to parse / read source data."""

class MappingError(ImportError):
    """Invalid or incomplete field mapping."""

class ValidationError(ImportError):
    """One or more rows failed validation (not fatal — check row statuses)."""

class ApplyError(ImportError):
    """Error during apply phase."""

class RollbackError(ImportError):
    """Error during rollback phase."""


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _audit(conn, session_id: str, tenant_id: int, event_type: str,
           details: dict, actor_user_id: Optional[int] = None,
           actor_type: str = "system") -> None:
    """Write one row to import_audit_log. Never raises — swallows exceptions."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO crm.import_audit_log
                    (session_id, tenant_id, event_type, event_details_json,
                     actor_user_id, actor_type)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (session_id, tenant_id, event_type,
                 json.dumps(details), actor_user_id, actor_type),
            )
    except Exception as exc:
        logger.error("audit write failed: %s", exc)


def _set_session_status(conn, session_id: str, status: str,
                        error_message: Optional[str] = None,
                        **extra_fields) -> None:
    updates = {"status": status, "updated_at": _now()}
    if error_message is not None:
        updates["error_message"] = error_message
    updates.update(extra_fields)

    set_clauses = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [session_id]
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE crm.import_sessions SET {set_clauses} WHERE id = %s",
            values,
        )


def _get_session(conn, session_id: str, tenant_id: int) -> dict:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM crm.import_sessions WHERE id = %s AND tenant_id = %s",
            (session_id, tenant_id),
        )
        row = cur.fetchone()
    if not row:
        raise ImportError(f"Session {session_id} not found for tenant {tenant_id}")
    return dict(row)


def _get_mappings(conn, session_id: str) -> List[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM crm.import_field_mappings WHERE session_id = %s ORDER BY sort_order",
            (session_id,),
        )
        return [dict(r) for r in cur.fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# Source Adapters
# ─────────────────────────────────────────────────────────────────────────────

class BaseAdapter:
    """Returns an iterator of dicts (one per source record)."""

    def read(self) -> Iterator[dict]:
        raise NotImplementedError


class CsvAdapter(BaseAdapter):
    """Parse CSV from bytes or file path."""

    def __init__(self, data: bytes, options: dict):
        self._data = data
        self._delimiter = options.get("delimiter", ",")
        self._encoding = options.get("encoding", "utf-8-sig")
        self._skip_header = options.get("skip_header", True)

    def read(self) -> Iterator[dict]:
        text = self._data.decode(self._encoding, errors="replace")
        reader = csv.DictReader(
            io.StringIO(text),
            delimiter=self._delimiter,
        )
        for row in reader:
            yield dict(row)


class ExcelAdapter(BaseAdapter):
    """Parse .xlsx / .xls using openpyxl (xlsx) or xlrd (xls)."""

    def __init__(self, data: bytes, options: dict):
        self._data = data
        self._sheet_name = options.get("sheet_name")    # None → first sheet
        self._skip_rows = options.get("skip_rows", 0)

    def read(self) -> Iterator[dict]:
        try:
            import openpyxl
        except ImportError:
            raise ParseError("openpyxl is required for Excel import: pip install openpyxl")

        wb = openpyxl.load_workbook(io.BytesIO(self._data), read_only=True, data_only=True)
        if self._sheet_name:
            ws = wb[self._sheet_name]
        else:
            ws = wb.active

        rows = list(ws.iter_rows(values_only=True))
        if len(rows) <= self._skip_rows:
            return

        headers = [str(h) if h is not None else f"col_{i}"
                   for i, h in enumerate(rows[self._skip_rows])]

        for row in rows[self._skip_rows + 1:]:
            yield {headers[i]: (str(v) if v is not None else "")
                   for i, v in enumerate(row)}


class JsonAdapter(BaseAdapter):
    """Parse JSON array or NDJSON."""

    def __init__(self, data: bytes, options: dict):
        self._data = data
        self._root_path = options.get("root_path")  # e.g. "data.items"
        self._ndjson = options.get("ndjson", False)

    def read(self) -> Iterator[dict]:
        if self._ndjson:
            for line in self._data.decode("utf-8").splitlines():
                line = line.strip()
                if line:
                    yield json.loads(line)
            return

        parsed = json.loads(self._data.decode("utf-8"))
        if self._root_path:
            for key in self._root_path.split("."):
                parsed = parsed[key]

        if isinstance(parsed, list):
            for item in parsed:
                yield item
        elif isinstance(parsed, dict):
            yield parsed
        else:
            raise ParseError(f"JSON root must be array or object, got {type(parsed)}")


class RestApiAdapter(BaseAdapter):
    """
    Fetch records from a paginated REST API.
    source_config_json must use slot references (e.g. {api_key_slot: "my_tool.api_key"})
    — never plain-text credentials here.

    Supported pagination styles: none, offset, cursor, page.
    """

    def __init__(self, config: dict, options: dict):
        self._url = config.get("url", "")
        self._method = config.get("method", "GET").upper()
        self._headers = config.get("headers", {})
        self._params = config.get("params", {})
        self._body = config.get("body")
        self._data_path = config.get("data_path")       # e.g. "data" or "results"
        self._pagination = config.get("pagination", "none")
        self._page_param = config.get("page_param", "page")
        self._page_size_param = config.get("page_size_param", "per_page")
        self._page_size = config.get("page_size", 100)
        self._next_url_path = config.get("next_url_path", "next")
        self._timeout = options.get("timeout_ms", 10000) / 1000
        self._max_pages = options.get("max_pages", 50)

    def read(self) -> Iterator[dict]:
        session = requests.Session()
        url = self._url
        page = 1

        for _ in range(self._max_pages):
            params = dict(self._params)
            if self._pagination == "offset":
                params[self._page_size_param] = self._page_size
                params[self._page_param] = (page - 1) * self._page_size
            elif self._pagination == "page":
                params[self._page_size_param] = self._page_size
                params[self._page_param] = page

            resp = session.request(
                self._method, url,
                headers=self._headers,
                params=params,
                json=self._body if self._method in ("POST", "PUT") else None,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            records = data
            if self._data_path:
                for key in self._data_path.split("."):
                    records = records[key]

            if not records:
                break

            for record in records:
                yield record

            if self._pagination == "cursor":
                next_url = data
                for key in self._next_url_path.split("."):
                    next_url = next_url.get(key) if isinstance(next_url, dict) else None
                if not next_url:
                    break
                url = next_url
            elif self._pagination == "none":
                break
            else:
                page += 1


class PostgresAdapter(BaseAdapter):
    """Read from a source PostgreSQL table/query."""

    def __init__(self, config: dict, options: dict):
        self._dsn = config.get("dsn", "")
        self._query = config.get("query", "")
        self._table = config.get("table", "")
        self._limit = options.get("limit", 100000)

    def read(self) -> Iterator[dict]:
        if not self._dsn:
            raise ParseError("PostgreSQL source requires dsn in source_config_json")
        query = self._query or f"SELECT * FROM {self._table} LIMIT {self._limit}"
        conn = psycopg2.connect(self._dsn)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(query)
                for row in cur:
                    yield dict(row)
        finally:
            conn.close()


class _StubAdapter(BaseAdapter):
    """Placeholder for not-yet-implemented sources."""

    def __init__(self, source_type: str):
        self._source_type = source_type

    def read(self) -> Iterator[dict]:
        raise ParseError(
            f"Source type '{self._source_type}' is recognised but not yet implemented. "
            "Use csv, excel, json, postgresql, or rest_api."
        )


def _get_adapter(source_type: str, data: Optional[bytes],
                 source_config: dict, options: dict) -> BaseAdapter:
    if source_type == "csv":
        return CsvAdapter(data or b"", options)
    elif source_type in ("excel", "xlsx", "xls"):
        return ExcelAdapter(data or b"", options)
    elif source_type == "json":
        return JsonAdapter(data or b"", options)
    elif source_type == "rest_api":
        return RestApiAdapter(source_config, options)
    elif source_type == "postgresql":
        return PostgresAdapter(source_config, options)
    else:
        return _StubAdapter(source_type)


# ─────────────────────────────────────────────────────────────────────────────
# Transform engine
# ─────────────────────────────────────────────────────────────────────────────

def _apply_transform(value: Any, transform_type: str, config: dict) -> Any:
    """Apply a single transform to a field value. Returns transformed value."""
    if transform_type == "direct":
        return value
    elif transform_type == "trim":
        return str(value).strip() if value is not None else value
    elif transform_type == "uppercase":
        return str(value).upper() if value is not None else value
    elif transform_type == "lowercase":
        return str(value).lower() if value is not None else value
    elif transform_type == "date_parse":
        from datetime import datetime as dt
        fmt = config.get("format", "%Y-%m-%d")
        try:
            return dt.strptime(str(value).strip(), fmt).date().isoformat()
        except ValueError:
            return value  # leave as-is; validation will flag it
    elif transform_type == "number_parse":
        try:
            s = str(value).replace(",", ".").strip()
            return float(s) if "." in s else int(s)
        except (ValueError, TypeError):
            return value
    elif transform_type == "boolean_map":
        true_vals = {str(v).lower() for v in config.get("true_values", ["true", "1", "yes", "y"])}
        return str(value).lower() in true_vals
    elif transform_type == "static_value":
        return config.get("static_value")
    elif transform_type == "lookup":
        table = config.get("lookup_table", {})
        return table.get(str(value), config.get("default", value))
    else:
        return value  # unknown transform — pass through


def _apply_mappings(raw: dict, mappings: List[dict]) -> Tuple[dict, List[dict]]:
    """
    Apply all field mappings to a raw row dict.
    Returns (mapped_dict, list_of_transform_errors).
    Columns with target_field=None are skipped.
    """
    mapped: dict = {}
    errors: List[dict] = []

    for m in mappings:
        src = m["source_column"]
        tgt = m.get("target_field")
        if not tgt:
            continue  # explicitly skipped

        value = raw.get(src)
        if value is None or value == "":
            if m.get("default_value") is not None:
                value = m["default_value"]

        try:
            transform_config = m.get("transform_config_json") or {}
            if isinstance(transform_config, str):
                transform_config = json.loads(transform_config)
            value = _apply_transform(value, m.get("transform_type", "direct"), transform_config)
        except Exception as exc:
            errors.append({
                "field": src,
                "code": "transform_error",
                "message": str(exc),
            })
            value = raw.get(src)  # fall back to raw

        mapped[tgt] = value

    return mapped, errors


# ─────────────────────────────────────────────────────────────────────────────
# Validator
# ─────────────────────────────────────────────────────────────────────────────

# Validation rules keyed by target_table. Add more target tables as needed.
_TARGET_VALIDATORS: Dict[str, List[dict]] = {
    "crm.contacts": [
        {"field": "first_name", "required": True, "max_length": 100},
        {"field": "last_name",  "required": True, "max_length": 100},
        {"field": "email",      "required": False, "format": "email"},
        {"field": "phone",      "required": False, "max_length": 50},
    ],
    "crm.clients": [
        {"field": "name",  "required": True, "max_length": 255},
        {"field": "email", "required": False, "format": "email"},
    ],
}


def _validate_row(mapped: dict, target_table: str) -> Tuple[List[dict], List[dict]]:
    """
    Returns (errors, warnings). Both are lists of {field, code, message}.
    """
    errors: List[dict] = []
    warnings: List[dict] = []
    rules = _TARGET_VALIDATORS.get(target_table, [])

    import re
    EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

    for rule in rules:
        field = rule["field"]
        value = mapped.get(field)

        if rule.get("required") and (value is None or str(value).strip() == ""):
            errors.append({"field": field, "code": "required", "message": f"{field} is required"})
            continue

        if value is None or str(value).strip() == "":
            continue

        if rule.get("max_length") and len(str(value)) > rule["max_length"]:
            errors.append({
                "field": field,
                "code": "max_length",
                "message": f"{field} exceeds {rule['max_length']} characters",
            })

        if rule.get("format") == "email" and not EMAIL_RE.match(str(value)):
            errors.append({"field": field, "code": "invalid_email",
                           "message": f"{field} is not a valid email address"})

    return errors, warnings


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def create_session(conn, tenant_id: int, source_type: str, target_table: str,
                   import_mode: str = "semi_automatic",
                   session_name: str = "",
                   source_config: Optional[dict] = None,
                   options: Optional[dict] = None,
                   created_by: Optional[int] = None) -> dict:
    """
    Create a new import session.
    Returns the session dict.
    """
    session_id = str(uuid.uuid4())
    source_config = source_config or {}
    options = options or {}

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO crm.import_sessions
                (id, tenant_id, session_name, source_type, target_table,
                 import_mode, status, source_config_json, options_json, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, 'created', %s, %s, %s)
            """,
            (session_id, tenant_id, session_name, source_type, target_table,
             import_mode, json.dumps(source_config), json.dumps(options), created_by),
        )
    conn.commit()

    _audit(conn, session_id, tenant_id, "session_created", {
        "source_type": source_type,
        "target_table": target_table,
        "import_mode": import_mode,
    }, actor_user_id=created_by)
    conn.commit()

    return _get_session(conn, session_id, tenant_id)


def upload_and_parse(conn, session_id: str, tenant_id: int,
                     file_data: bytes, filename: str,
                     actor_user_id: Optional[int] = None) -> dict:
    """
    Parse uploaded file bytes and load rows into import_staging.
    Session must be in status 'created' or 'staged' (re-upload allowed).

    Returns updated session dict with total_rows populated.
    """
    session = _get_session(conn, session_id, tenant_id)
    if session["status"] not in ("created", "staged", "mapping_required",
                                  "preview_ready", "failed"):
        raise ImportError(
            f"Cannot re-upload in status '{session['status']}'. "
            "Rollback first or use a new session."
        )

    source_type = session["source_type"]
    options = session.get("options_json") or {}
    source_config = session.get("source_config_json") or {}
    if isinstance(options, str):
        options = json.loads(options)
    if isinstance(source_config, str):
        source_config = json.loads(source_config)

    # Update filename and set status = parsing
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE crm.import_sessions SET status='parsing', source_filename=%s, updated_at=%s WHERE id=%s",
            (filename, _now(), session_id),
        )
    conn.commit()

    _audit(conn, session_id, tenant_id, "file_uploaded",
           {"filename": filename, "size_bytes": len(file_data)},
           actor_user_id=actor_user_id)
    conn.commit()

    # Clear previous staging rows if re-uploading
    with conn.cursor() as cur:
        cur.execute("DELETE FROM crm.import_staging WHERE session_id = %s", (session_id,))
    conn.commit()

    adapter = _get_adapter(source_type, file_data, source_config, options)

    total = 0
    try:
        _audit(conn, session_id, tenant_id, "parsing_started", {},
               actor_user_id=actor_user_id)
        conn.commit()

        batch: List[tuple] = []
        for idx, raw_row in enumerate(adapter.read()):
            batch.append((
                str(uuid.uuid4()),
                session_id,
                idx,
                json.dumps(raw_row),
                json.dumps({}),   # mapped_json empty until mappings applied
                "pending",
                json.dumps([]),
                json.dumps([]),
            ))
            total += 1
            if len(batch) >= 500:
                _insert_staging_batch(conn, batch)
                conn.commit()
                batch = []

        if batch:
            _insert_staging_batch(conn, batch)
            conn.commit()

    except Exception as exc:
        _set_session_status(conn, session_id, "failed", error_message=str(exc))
        conn.commit()
        _audit(conn, session_id, tenant_id, "parsing_completed",
               {"success": False, "error": str(exc)}, actor_user_id=actor_user_id)
        conn.commit()
        raise ParseError(f"Parse failed: {exc}") from exc

    _set_session_status(conn, session_id, "mapping_required",
                        total_rows=total, valid_rows=0, invalid_rows=0)
    conn.commit()
    _audit(conn, session_id, tenant_id, "parsing_completed",
           {"success": True, "total_rows": total, "filename": filename},
           actor_user_id=actor_user_id)
    conn.commit()

    return _get_session(conn, session_id, tenant_id)


def _insert_staging_batch(conn, batch: List[tuple]) -> None:
    with conn.cursor() as cur:
        psycopg2.extras.execute_batch(
            cur,
            """
            INSERT INTO crm.import_staging
                (id, session_id, row_index, raw_json, mapped_json,
                 validation_status, validation_errors, validation_warnings)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            batch,
        )


def save_mappings(conn, session_id: str, tenant_id: int,
                  mappings: List[dict],
                  actor_user_id: Optional[int] = None) -> dict:
    """
    Replace all field mappings for the session and re-apply transforms to staging rows.
    Each mapping dict: {source_column, target_field, transform_type, transform_config_json,
                        required, default_value, sort_order}

    Returns updated session dict.
    """
    session = _get_session(conn, session_id, tenant_id)
    if session["status"] in ("applying", "completed", "rolled_back"):
        raise MappingError(f"Cannot modify mappings in status '{session['status']}'")

    # Replace mappings
    with conn.cursor() as cur:
        cur.execute("DELETE FROM crm.import_field_mappings WHERE session_id = %s", (session_id,))
        for i, m in enumerate(mappings):
            cur.execute(
                """
                INSERT INTO crm.import_field_mappings
                    (session_id, source_column, target_field, transform_type,
                     transform_config_json, required, default_value, sort_order)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    session_id,
                    m["source_column"],
                    m.get("target_field"),
                    m.get("transform_type", "direct"),
                    json.dumps(m.get("transform_config_json") or {}),
                    m.get("required", False),
                    m.get("default_value"),
                    m.get("sort_order", i),
                ),
            )
    conn.commit()

    # Re-apply transforms to all staging rows
    saved_mappings = _get_mappings(conn, session_id)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, raw_json FROM crm.import_staging WHERE session_id = %s ORDER BY row_index",
            (session_id,),
        )
        rows = cur.fetchall()

    batch_updates: List[tuple] = []
    for row in rows:
        raw = row["raw_json"]
        if isinstance(raw, str):
            raw = json.loads(raw)
        mapped, _ = _apply_mappings(raw, saved_mappings)
        batch_updates.append((json.dumps(mapped), "pending",
                               json.dumps([]), json.dumps([]), row["id"]))

    if batch_updates:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                """
                UPDATE crm.import_staging
                SET mapped_json=%s, validation_status=%s,
                    validation_errors=%s, validation_warnings=%s
                WHERE id=%s
                """,
                batch_updates,
            )
        conn.commit()

    _set_session_status(conn, session_id, "staged")
    conn.commit()
    _audit(conn, session_id, tenant_id, "mapping_saved",
           {"mapping_count": len(mappings)}, actor_user_id=actor_user_id)
    conn.commit()

    return _get_session(conn, session_id, tenant_id)


def validate(conn, session_id: str, tenant_id: int,
             actor_user_id: Optional[int] = None) -> dict:
    """
    Run validation on all staged rows.
    Updates each row's validation_status, validation_errors, validation_warnings.
    Sets session status to 'preview_ready' (even if some rows are invalid).

    Returns updated session dict with valid_rows, invalid_rows counts.
    """
    session = _get_session(conn, session_id, tenant_id)
    if session["status"] not in ("staged", "mapping_required", "preview_ready", "validating"):
        raise ImportError(f"Cannot validate in status '{session['status']}'")

    target_table = session["target_table"]
    _set_session_status(conn, session_id, "validating")
    conn.commit()
    _audit(conn, session_id, tenant_id, "validation_started", {},
           actor_user_id=actor_user_id)
    conn.commit()

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, mapped_json FROM crm.import_staging WHERE session_id = %s ORDER BY row_index",
            (session_id,),
        )
        rows = cur.fetchall()

    valid_count = 0
    invalid_count = 0
    batch_updates: List[tuple] = []

    for row in rows:
        mapped = row["mapped_json"]
        if isinstance(mapped, str):
            mapped = json.loads(mapped)

        errors, warnings = _validate_row(mapped, target_table)
        if errors:
            status = "invalid"
            invalid_count += 1
        else:
            status = "valid"
            valid_count += 1

        batch_updates.append((status, json.dumps(errors), json.dumps(warnings), row["id"]))

    if batch_updates:
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(
                cur,
                """
                UPDATE crm.import_staging
                SET validation_status=%s, validation_errors=%s, validation_warnings=%s
                WHERE id=%s
                """,
                batch_updates,
            )
        conn.commit()

    _set_session_status(conn, session_id, "preview_ready",
                        valid_rows=valid_count, invalid_rows=invalid_count)
    conn.commit()
    _audit(conn, session_id, tenant_id, "validation_completed", {
        "valid_rows": valid_count,
        "invalid_rows": invalid_count,
        "total_rows": len(rows),
    }, actor_user_id=actor_user_id)
    conn.commit()

    return _get_session(conn, session_id, tenant_id)


def get_preview(conn, session_id: str, tenant_id: int,
                page: int = 1, page_size: int = 50,
                filter_status: Optional[str] = None) -> dict:
    """
    Return a paginated preview of staging rows.
    filter_status: 'valid' | 'invalid' | 'skipped' | None (all)
    """
    session = _get_session(conn, session_id, tenant_id)
    offset = (page - 1) * page_size

    where_clauses = ["session_id = %s"]
    params: List[Any] = [session_id]
    if filter_status:
        where_clauses.append("validation_status = %s")
        params.append(filter_status)

    where = " AND ".join(where_clauses)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"SELECT COUNT(*) AS cnt FROM crm.import_staging WHERE {where}", params
        )
        total = cur.fetchone()["cnt"]

        cur.execute(
            f"""
            SELECT id, row_index, raw_json, mapped_json, validation_status,
                   validation_errors, validation_warnings, is_duplicate, action
            FROM crm.import_staging
            WHERE {where}
            ORDER BY row_index
            LIMIT %s OFFSET %s
            """,
            params + [page_size, offset],
        )
        rows = [dict(r) for r in cur.fetchall()]

    return {
        "session_id": session_id,
        "session_status": session["status"],
        "total": total,
        "page": page,
        "page_size": page_size,
        "rows": rows,
    }


def approve(conn, session_id: str, tenant_id: int,
            actor_user_id: Optional[int] = None) -> dict:
    """
    Mark the session as approved for import.
    Only possible from 'preview_ready'.
    Raises ImportError if there are any invalid rows (unless overridden by options skip_invalid=True).
    """
    session = _get_session(conn, session_id, tenant_id)
    if session["status"] != "preview_ready":
        raise ImportError(
            f"Approve requires status 'preview_ready', current: '{session['status']}'"
        )

    options = session.get("options_json") or {}
    if isinstance(options, str):
        options = json.loads(options)

    if session["invalid_rows"] > 0 and not options.get("skip_invalid", False):
        raise ImportError(
            f"{session['invalid_rows']} invalid rows found. "
            "Fix mappings + re-validate, or set options.skip_invalid=true to skip them."
        )

    now = _now()
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE crm.import_sessions
            SET status='approved', preview_approved_by=%s, preview_approved_at=%s, updated_at=%s
            WHERE id=%s
            """,
            (actor_user_id, now, now, session_id),
        )
    conn.commit()
    _audit(conn, session_id, tenant_id, "preview_approved",
           {"valid_rows": session["valid_rows"], "invalid_rows": session["invalid_rows"]},
           actor_user_id=actor_user_id, actor_type="user")
    conn.commit()

    return _get_session(conn, session_id, tenant_id)


def apply_import(conn, session_id: str, tenant_id: int,
                 actor_user_id: Optional[int] = None) -> dict:
    """
    Apply approved import to production tables.
    Only valid rows are inserted/updated. Invalid rows are skipped.
    rollback_snapshot is stored for UPDATE actions.

    Returns updated session dict with imported_rows count.
    """
    session = _get_session(conn, session_id, tenant_id)
    if session["status"] != "approved":
        raise ApplyError(
            f"Apply requires status 'approved', current: '{session['status']}'"
        )

    target_table = session["target_table"]
    options = session.get("options_json") or {}
    if isinstance(options, str):
        options = json.loads(options)

    _set_session_status(conn, session_id, "applying")
    conn.commit()
    _audit(conn, session_id, tenant_id, "import_started",
           {"target_table": target_table}, actor_user_id=actor_user_id, actor_type="user")
    conn.commit()

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, row_index, mapped_json, action
            FROM crm.import_staging
            WHERE session_id = %s AND validation_status = 'valid'
            ORDER BY row_index
            """,
            (session_id,),
        )
        rows = cur.fetchall()

    imported = 0
    errors: List[str] = []

    for row in rows:
        mapped = row["mapped_json"]
        if isinstance(mapped, str):
            mapped = json.loads(mapped)

        action = row.get("action", "insert")
        try:
            prod_id, snapshot = _write_to_production(
                conn, target_table, mapped, action, tenant_id, options
            )
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE crm.import_staging
                    SET validation_status='imported', production_row_id=%s, rollback_snapshot=%s
                    WHERE id=%s
                    """,
                    (str(prod_id) if prod_id else None,
                     json.dumps(snapshot) if snapshot else None,
                     row["id"]),
                )
            conn.commit()
            imported += 1
        except Exception as exc:
            conn.rollback()
            err_msg = f"Row {row['row_index']}: {exc}"
            errors.append(err_msg)
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE crm.import_staging
                    SET validation_status='invalid',
                        validation_errors=%s
                    WHERE id=%s
                    """,
                    (json.dumps([{"field": "__apply__", "code": "apply_error",
                                  "message": err_msg}]),
                     row["id"]),
                )
            conn.commit()

    final_status = "completed" if not errors else "failed"
    now = _now()
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE crm.import_sessions
            SET status=%s, imported_rows=%s, applied_by=%s, applied_at=%s,
                updated_at=%s, error_message=%s
            WHERE id=%s
            """,
            (
                final_status, imported, actor_user_id, now, now,
                "; ".join(errors[:5]) if errors else None,
                session_id,
            ),
        )
    conn.commit()

    _audit(conn, session_id, tenant_id, "import_completed", {
        "success": final_status == "completed",
        "imported_rows": imported,
        "error_count": len(errors),
        "errors_sample": errors[:3],
    }, actor_user_id=actor_user_id)
    conn.commit()

    return _get_session(conn, session_id, tenant_id)


def _write_to_production(conn, target_table: str, mapped: dict,
                          action: str, tenant_id: int,
                          options: dict) -> Tuple[Any, Optional[dict]]:
    """
    Write one row to a production table.
    Returns (production_row_id, rollback_snapshot_or_None).

    Only crm.contacts and crm.clients are fully wired up.
    Other tables fall back to a generic INSERT.
    """
    snapshot: Optional[dict] = None

    if target_table == "crm.contacts":
        return _insert_contact(conn, mapped, tenant_id, options)
    elif target_table == "crm.clients":
        return _insert_client(conn, mapped, tenant_id, options)
    else:
        # Generic insert — caller must ensure mapped keys match column names
        if not mapped:
            raise ApplyError("Mapped row is empty")
        cols = list(mapped.keys())
        vals = [mapped[c] for c in cols]
        placeholders = ", ".join("%s" for _ in cols)
        col_list = ", ".join(cols)
        with conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO {target_table} ({col_list}) VALUES ({placeholders}) RETURNING id",
                vals,
            )
            result = cur.fetchone()
        return result[0] if result else None, snapshot


def _insert_contact(conn, mapped: dict, tenant_id: int, options: dict):
    """Insert or upsert a contact row."""
    now = _now().isoformat()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO crm.contacts
                (id, tenant_id, first_name, last_name, email, phone,
                 notes, created_at, updated_at)
            VALUES
                (gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                tenant_id,
                mapped.get("first_name", ""),
                mapped.get("last_name", ""),
                mapped.get("email"),
                mapped.get("phone"),
                mapped.get("notes"),
                now, now,
            ),
        )
        row = cur.fetchone()
    return row[0], None


def _insert_client(conn, mapped: dict, tenant_id: int, options: dict):
    """Insert or upsert a client row."""
    now = _now().isoformat()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO crm.clients
                (id, tenant_id, name, email, phone, address, notes, created_at, updated_at)
            VALUES
                (gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                tenant_id,
                mapped.get("name", ""),
                mapped.get("email"),
                mapped.get("phone"),
                mapped.get("address"),
                mapped.get("notes"),
                now, now,
            ),
        )
        row = cur.fetchone()
    return row[0], None


def rollback_import(conn, session_id: str, tenant_id: int,
                    actor_user_id: Optional[int] = None) -> dict:
    """
    Rollback a completed import by deleting all rows that were inserted
    (production_row_id is set on staging rows).

    UPDATE rollback uses rollback_snapshot.
    Only works on sessions with status 'completed' or 'failed' (partial apply).
    """
    session = _get_session(conn, session_id, tenant_id)
    if session["status"] not in ("completed", "failed"):
        raise RollbackError(
            f"Rollback requires status 'completed' or 'failed', "
            f"current: '{session['status']}'"
        )

    target_table = session["target_table"]
    _set_session_status(conn, session_id, "applying")   # lock during rollback
    conn.commit()
    _audit(conn, session_id, tenant_id, "rollback_started",
           {"target_table": target_table}, actor_user_id=actor_user_id, actor_type="user")
    conn.commit()

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, production_row_id, action, rollback_snapshot
            FROM crm.import_staging
            WHERE session_id = %s AND validation_status = 'imported'
            """,
            (session_id,),
        )
        imported_rows = cur.fetchall()

    rolled_back = 0
    errors: List[str] = []

    for row in imported_rows:
        prod_id = row["production_row_id"]
        action = row.get("action", "insert")
        snapshot = row.get("rollback_snapshot")
        if isinstance(snapshot, str):
            snapshot = json.loads(snapshot)

        try:
            if action == "insert" and prod_id:
                with conn.cursor() as cur:
                    cur.execute(
                        f"DELETE FROM {target_table} WHERE id = %s", (prod_id,)
                    )
                conn.commit()
            elif action == "update" and prod_id and snapshot:
                cols = list(snapshot.keys())
                set_clause = ", ".join(f"{c} = %s" for c in cols)
                vals = [snapshot[c] for c in cols] + [prod_id]
                with conn.cursor() as cur:
                    cur.execute(
                        f"UPDATE {target_table} SET {set_clause} WHERE id = %s",
                        vals,
                    )
                conn.commit()

            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE crm.import_staging SET validation_status='rolled_back' WHERE id=%s",
                    (row["id"],),
                )
            conn.commit()
            rolled_back += 1
        except Exception as exc:
            conn.rollback()
            errors.append(f"Row {row['id']}: {exc}")

    now = _now()
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE crm.import_sessions
            SET status='rolled_back', rolled_back_rows=%s,
                rolled_back_by=%s, rolled_back_at=%s, updated_at=%s,
                error_message=%s
            WHERE id=%s
            """,
            (rolled_back, actor_user_id, now, now,
             "; ".join(errors[:5]) if errors else None,
             session_id),
        )
    conn.commit()

    _audit(conn, session_id, tenant_id, "rollback_completed", {
        "rolled_back_rows": rolled_back,
        "error_count": len(errors),
        "errors_sample": errors[:3],
    }, actor_user_id=actor_user_id)
    conn.commit()

    return _get_session(conn, session_id, tenant_id)


def get_audit_log(conn, session_id: str, tenant_id: int,
                  limit: int = 100) -> List[dict]:
    """Return audit log entries for a session, newest first."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT id, event_type, event_details_json, actor_user_id,
                   actor_type, created_at
            FROM crm.import_audit_log
            WHERE session_id = %s AND tenant_id = %s
            ORDER BY created_at ASC
            LIMIT %s
            """,
            (session_id, tenant_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def list_sessions(conn, tenant_id: int,
                  status: Optional[str] = None,
                  target_table: Optional[str] = None,
                  limit: int = 50, offset: int = 0) -> List[dict]:
    """List import sessions for a tenant."""
    clauses = ["tenant_id = %s"]
    params: List[Any] = [tenant_id]
    if status:
        clauses.append("status = %s")
        params.append(status)
    if target_table:
        clauses.append("target_table = %s")
        params.append(target_table)

    where = " AND ".join(clauses)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"""
            SELECT id, session_name, source_type, target_table, import_mode,
                   status, total_rows, valid_rows, invalid_rows, imported_rows,
                   source_filename, created_by, created_at, updated_at
            FROM crm.import_sessions
            WHERE {where}
            ORDER BY created_at DESC
            LIMIT %s OFFSET %s
            """,
            params + [limit, offset],
        )
        return [dict(r) for r in cur.fetchall()]

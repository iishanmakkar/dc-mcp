"""Transport-independent operations. REST (app.py) and MCP (mcp_tools.py) both call these."""
from __future__ import annotations

import base64, binascii, json, re
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd

from datacleaner import engine as E
from datacleaner.safeio import DataError, Limits, EXPORT_FORMATS, export_bytes, fetch_url, load_bytes

from .config import Settings, get_settings
from .quota import Meter, QuotaError, Tier, tier_for
from .store import Dataset, NotFound, Store

MAX_PARAM_BYTES = 200_000
AUTO_LEVELS = {"none", "low", "medium"}          # "high" can only be approved finding by finding


class ServiceError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _safe_stem(name: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", name or "dataset")
    return re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._")[:60] or "dataset"


def _shrink(value: Any, limit: int = 12) -> Any:
    """Keep plan/param views small: long lists and mappings become a sample plus a count."""
    if isinstance(value, dict):
        if len(value) > limit:
            return {"_items": len(value), "_sample": {k: value[k] for k in list(value)[:5]}}
        return {k: _shrink(v, limit) for k, v in value.items()}
    if isinstance(value, list) and len(value) > limit:
        return {"_items": len(value), "_sample": value[:5]}
    return value


class Service:
    def __init__(self, settings: Optional[Settings] = None):
        self.s = settings or get_settings()
        self.store = Store(self.s.ttl_seconds, self.s.max_datasets_per_client)
        self.meter = Meter()
        # client_id -> verified plan hint from OAuth claims ("paid"/None).
        # Filled in per request by server/app.py; tier() falls back to None.
        self._tier_hints: dict[str, str | None] = {}
        E.set_verbose(False)

    # ------------------------------------------------------------------ helpers
    def note_tier_hint(self, client: str, hint: str | None) -> None:
        if hint:
            if len(self._tier_hints) >= 5000 and client not in self._tier_hints:
                self._tier_hints.pop(next(iter(self._tier_hints)))   # bound memory: evict oldest
            self._tier_hints[client] = hint
        else:
            self._tier_hints.pop(client, None)

    def tier(self, client: str, tier_hint: str | None = None) -> Tier:
        hint = tier_hint if tier_hint is not None else self._tier_hints.get(client)
        return tier_for(client, self.s, hint)

    def _limits(self, t: Tier) -> Limits:
        return Limits(max_bytes=t.max_bytes, max_rows=t.max_rows, max_cols=self.s.max_cols)

    def _get(self, client: str, dataset_id: str) -> Dataset:
        self.meter.rate_limit(client, self.s.rate_limit_per_minute)
        try:
            return self.store.get_dataset(client, dataset_id)
        except NotFound as exc:
            raise ServiceError(str(exc), 404)

    def _resolve_bytes(self, client: str, t: Tier, file_base64: Optional[str], csv_text: Optional[str],
                       file_url: Optional[str], upload_id: Optional[str], filename: Optional[str]):
        given = [x for x in (file_base64, csv_text, file_url, upload_id) if x]
        if len(given) != 1:
            if csv_text == "" and not any([file_base64, file_url, upload_id]):
                raise ServiceError("The file is empty.")
            raise ServiceError("Provide exactly one of: file_base64, csv_text, file_url, upload_id.")
        if file_base64:
            if len(file_base64) > t.max_bytes * 4 // 3 + 16:
                raise ServiceError(f"The file is larger than {t.max_bytes // (1024 * 1024)} MB, the limit for this plan.", 413)
            try:
                return base64.b64decode(file_base64, validate=True), filename
            except (binascii.Error, ValueError):
                raise ServiceError("file_base64 is not valid base64.")
        if csv_text:
            data = csv_text.encode("utf-8")
            if len(data) > t.max_bytes:
                raise ServiceError(f"The text is larger than {t.max_bytes // (1024 * 1024)} MB, the limit for this plan.", 413)
            return data, filename or "pasted.csv"
        if file_url:
            if not self.s.allow_url_fetch:
                raise ServiceError("Fetching files from links is disabled on this server.", 403)
            try:
                data, name = fetch_url(file_url, max_bytes=t.max_bytes)
            except DataError as exc:
                raise ServiceError(str(exc))
            return data, filename or name
        try:
            slot = self.store.pop_slot(client, upload_id)
        except NotFound as exc:
            raise ServiceError(str(exc), 404)
        return slot.payload, filename or slot.filename

    # ------------------------------------------------------------------- views
    @staticmethod
    def findings_view(agent: E.AgenticDataCleaner, limit: int = 80) -> List[Dict[str, Any]]:
        out = []
        for f in agent.findings[:limit]:
            out.append({"id": f.id, "severity": f.severity, "category": f.category, "title": f.title,
                        "detail": f.detail[:400], "columns": f.columns[:10],
                        "suggested_action": f.suggested_action if f.suggested_action != "none" else None})
        return out

    @staticmethod
    def plan_view(agent: E.AgenticDataCleaner) -> List[Dict[str, Any]]:
        by_id = {f.id: f for f in agent.findings}
        out = []
        for p in agent.plan:
            f = by_id.get(p.finding_id)
            out.append({"finding_id": p.finding_id, "issue": f.title if f else p.finding_id,
                        "action": p.action, "params": _shrink(p.params), "risk": p.risk,
                        "why": p.rationale,
                        "applies_automatically": p.action != "none" and E.RISK_RANK.get(p.risk, 2) <= 1,
                        "needs_explicit_approval": p.action != "none" and E.RISK_RANK.get(p.risk, 2) > 1})
        return out

    def _dataset_view(self, ds: Dataset, t: Tier) -> Dict[str, Any]:
        a = ds.agent
        counts: Dict[str, int] = {}
        for f in a.findings:
            counts[f.severity] = counts.get(f.severity, 0) + 1
        return {"dataset_id": ds.id, "name": ds.name, "rows": len(a.df), "columns": len(a.df.columns),
                "column_names": [str(c) for c in a.df.columns][:100], "issues_by_severity": counts,
                "issues": self.findings_view(a), "summary": a.narrate(),
                "plan_tier": t.name, "expires_in_seconds": int(ds.expires - self.store._now()),
                "note": "Your original file is never modified; all changes are made to a temporary working copy."}

    @staticmethod
    def _pending(agent: E.AgenticDataCleaner) -> List[Dict[str, Any]]:
        """What still needs a human decision after the automatic pass."""
        saved_plan, saved_findings = agent.plan, agent.findings
        try:
            agent.scan()
            agent.make_plan()
            by_id = {f.id: f for f in agent.findings}
            pending = []
            for p in agent.plan:
                f = by_id.get(p.finding_id)
                if f is None or f.severity == "info":
                    continue
                if p.action != "none" and E.RISK_RANK.get(p.risk, 2) > 1:
                    pending.append({"finding_id": p.finding_id, "issue": f.title, "kind": "approval_required",
                                    "proposed_action": p.action, "params": _shrink(p.params), "risk": p.risk})
                elif p.action == "none" and not p.rationale.startswith("Deterministic rule"):
                    pending.append({"finding_id": p.finding_id, "issue": f.title, "kind": "needs_decision", "reason": p.rationale})
            return pending
        finally:
            # make_plan() replaces agent.plan with unreviewed items (approved=None).
            # Restore the reviewed plan so the audit report keeps the approval record.
            agent.plan = saved_plan
            agent.findings = saved_findings

    # ------------------------------------------------------------------ actions
    def create_upload_slot(self, client: str) -> Dict[str, Any]:
        self.meter.rate_limit(client, self.s.rate_limit_per_minute)
        t = self.tier(client)
        slot = self.store.add_slot(client)
        return {"upload_id": slot.token, "upload_url": f"{self.s.public_base_url}/v1/uploads/{slot.token}",
                "method": "PUT", "max_bytes": t.max_bytes,
                "expires_in_seconds": int(min(self.s.ttl_seconds, 900)),
                "then": "PUT the raw file bytes to upload_url (add ?filename=name.csv), then call scan_dataset with upload_id."}

    def receive_upload(self, token: str, body: bytes, filename: Optional[str]) -> Dict[str, Any]:
        try:
            slot = self.store.take_slot(token)
        except NotFound as exc:
            raise ServiceError(str(exc), 404)
        t = self.tier(slot.owner)
        if len(body) > t.max_bytes:
            raise ServiceError(f"The file is larger than {t.max_bytes // (1024 * 1024)} MB, the limit for this plan.", 413)
        slot.payload, slot.filename = body, (filename or "upload")[:120]
        return {"upload_id": token, "bytes": len(body)}

    def scan(self, client: str, *, file_base64: Optional[str] = None, csv_text: Optional[str] = None,
             file_url: Optional[str] = None, upload_id: Optional[str] = None, filename: Optional[str] = None,
             sheet: Any = None, date_order: Optional[str] = None) -> Dict[str, Any]:
        self.meter.rate_limit(client, self.s.rate_limit_per_minute)
        t = self.tier(client)
        if date_order not in (None, "dmy", "mdy"):
            raise ServiceError("date_order must be 'dmy' (day first), 'mdy' (month first) or omitted.")
        data, name = self._resolve_bytes(client, t, file_base64, csv_text, file_url, upload_id, filename)
        try:
            loaded = load_bytes(data, name, sheet=sheet, limits=self._limits(t))
        except DataError as exc:
            hint = " Upgrade to process larger files." if t.name == "free" and "limit" in str(exc) else ""
            raise ServiceError(str(exc) + hint, 413 if "limit" in str(exc) else 400)
        try:
            self.meter.start_file(client, t)
        except QuotaError as exc:
            raise ServiceError(str(exc), exc.status)
        agent = E.AgenticDataCleaner(loaded.df, use_llm=self.s.llm_enabled, redact_llm_context=True,
                                     name=(name or "dataset")[:120], date_order=date_order)
        agent.ingest()
        agent.scan()
        try:
            ds = self.store.add_dataset(client, agent, agent.source_name, t.name)
        except NotFound as exc:
            raise ServiceError(str(exc), 429)
        self.meter.record(client, "scan", len(agent.df), len(agent.df.columns), t.name)
        view = self._dataset_view(ds, t)
        if loaded.sheet_names:
            view["sheets"], view["sheet_used"] = loaded.sheet_names, loaded.sheet
        return view

    def plan(self, client: str, dataset_id: str, date_order: Optional[str] = None) -> Dict[str, Any]:
        ds = self._get(client, dataset_id)
        if date_order not in (None, "dmy", "mdy"):
            raise ServiceError("date_order must be 'dmy', 'mdy' or omitted.")
        with ds.lock:
            if date_order:
                ds.agent.date_order = date_order
            ds.agent.scan()
            ds.agent.make_plan()
            return {"dataset_id": ds.id, "plan": self.plan_view(ds.agent),
                    "how_to_apply": "Call apply_cleaning_plan. Low/medium-risk items apply automatically; for a "
                                    "high-risk item pass its finding_id in approve_ids after the user agrees."}

    def apply_plan(self, client: str, dataset_id: str, auto_approve_max_risk: str = "medium",
                   approve_ids: Optional[Sequence[str]] = None, date_order: Optional[str] = None) -> Dict[str, Any]:
        ds = self._get(client, dataset_id)
        if auto_approve_max_risk not in AUTO_LEVELS:
            raise ServiceError("auto_approve_max_risk must be 'none', 'low' or 'medium'. High-risk changes "
                               "(deleting rows/columns, imputing values, masking PII) need approve_ids.")
        if date_order not in (None, "dmy", "mdy"):
            raise ServiceError("date_order must be 'dmy', 'mdy' or omitted.")
        with ds.lock:
            a = ds.agent
            if date_order:
                a.date_order = date_order
            start = len(a.log)
            a.scan()
            a.make_plan()
            a.review(interactive=False, auto_approve_max_risk=auto_approve_max_risk, approve_ids=list(approve_ids or []))
            a.apply()
            verify = a.verify() if any(p.approved for p in a.plan) else {}
            steps = [s.to_dict() for s in a.log[start:]]
            for s in steps:
                s["params"] = _shrink(s["params"])
            return {"dataset_id": ds.id, "steps": steps, "verify": verify, "rows": len(a.df),
                    "columns": len(a.df.columns), "remaining": self._pending(a)}

    def apply_action(self, client: str, dataset_id: str, action: str, params: Optional[Dict[str, Any]] = None,
                     confirm: bool = False) -> Dict[str, Any]:
        ds = self._get(client, dataset_id)
        params = params or {}
        if action not in E.ACTIONS:
            raise ServiceError(f"Unknown action '{action}'. Use list_cleaning_actions.")
        try:
            size = len(json.dumps(params, default=str))
        except (TypeError, ValueError):
            raise ServiceError("params must be JSON-serialisable.")
        if size > MAX_PARAM_BYTES:
            raise ServiceError("params are too large.", 413)
        risky = E.ACTIONS[action].destructive or E.risk_for(action, params) == "high"
        if risky and not confirm:
            raise ServiceError(f"'{action}' changes or removes data. Ask the user to confirm, then call again "
                               f"with confirm=true.", 409)
        with ds.lock:
            step = ds.agent.act(action, **params).to_dict()
            step["params"] = _shrink(step["params"])
            return {"dataset_id": ds.id, "step": step, "rows": len(ds.agent.df), "columns": len(ds.agent.df.columns)}

    def preview(self, client: str, dataset_id: str, rows: int = 20) -> Dict[str, Any]:
        ds = self._get(client, dataset_id)
        rows = max(1, min(int(rows), 50))
        with ds.lock:
            head = ds.agent.df.head(rows)
            records = json.loads(head.to_json(orient="records", date_format="iso"))
            dtypes = {str(c): str(t) for c, t in ds.agent.df.dtypes.items()}
            return {"dataset_id": ds.id, "rows_total": len(ds.agent.df), "rows_shown": len(records),
                    "dtypes": dtypes, "records": records}

    def export(self, client: str, dataset_id: str, fmt: str = "csv", include_report: bool = True,
               include_script: bool = True) -> Dict[str, Any]:
        ds = self._get(client, dataset_id)
        t = self.tier(client)
        if fmt not in EXPORT_FORMATS:
            raise ServiceError(f"format must be one of {sorted(EXPORT_FORMATS)}.")
        if fmt not in t.formats:
            raise ServiceError(f"The free plan exports CSV only. Upgrade to export {fmt}.", 402)
        with ds.lock:
            a = ds.agent
            stem = _safe_stem(ds.name)
            files = []
            payload, mime, ext = export_bytes(a.df, fmt)
            art = self.store.add_artifact(client, payload, mime, f"{stem}_cleaned{ext}")
            ds.artifact_tokens.append(art.token)
            files.append(("cleaned_data", art, len(a.df)))
            notes = []
            if include_report:
                if t.audit_report:
                    rep = json.dumps(a.to_report_dict(), indent=2, default=str).encode("utf-8")
                    r = self.store.add_artifact(client, rep, "application/json", f"{stem}_cleaning_report.json")
                    ds.artifact_tokens.append(r.token)
                    files.append(("audit_report", r, None))
                else:
                    notes.append("The audit report is part of the paid plan.")
            if include_script:
                if t.replay_script:
                    sc = a.pipeline_script().encode("utf-8")
                    r = self.store.add_artifact(client, sc, "text/x-python", f"{stem}_pipeline.py")
                    ds.artifact_tokens.append(r.token)
                    files.append(("replay_script", r, None))
                else:
                    notes.append("The replayable pipeline script is part of the paid plan.")
            self.meter.record(client, "export", len(a.df), len(a.df.columns), t.name)
            return {"dataset_id": ds.id, "rows": len(a.df), "columns": len(a.df.columns),
                    "files": [{"kind": k, "filename": art.filename, "bytes": len(art.payload),
                               "download_url": f"{self.s.public_base_url}/v1/download/{art.token}/{art.filename}"}
                              for k, art, _ in files],
                    "links_expire_in_seconds": int(art.expires - self.store._now()), "notes": notes}

    def clean(self, client: str, *, fmt: str = "csv", auto_approve_max_risk: str = "medium",
              approve_ids: Optional[Sequence[str]] = None, date_order: Optional[str] = None,
              **source: Any) -> Dict[str, Any]:
        """One call: scan -> plan -> apply (up to 3 rounds) -> export. High-risk changes are never automatic."""
        # Validate BEFORE scan: a rejected call must not burn the daily file quota
        # nor leave an orphaned dataset behind.
        if auto_approve_max_risk not in AUTO_LEVELS:
            raise ServiceError("auto_approve_max_risk must be 'none', 'low' or 'medium'.")
        if fmt not in EXPORT_FORMATS:
            raise ServiceError(f"format must be one of {sorted(EXPORT_FORMATS)}.")
        if date_order not in (None, "dmy", "mdy"):
            raise ServiceError("date_order must be 'dmy' (day first), 'mdy' (month first) or omitted.")
        t0 = self.tier(client)
        if fmt not in t0.formats:
            raise ServiceError(f"The free plan exports CSV only. Upgrade to export {fmt}.", 402)
        view = self.scan(client, date_order=date_order, **source)
        ds = self.store.get_dataset(client, view["dataset_id"])
        with ds.lock:
            a = ds.agent
            steps: List[Dict[str, Any]] = []
            for rnd in range(3):
                a.scan()
                if not a.findings:
                    break
                a.make_plan()
                a.review(interactive=False, auto_approve_max_risk=auto_approve_max_risk,
                         approve_ids=list(approve_ids or []) if rnd == 0 else None)
                if not any(p.approved for p in a.plan):
                    break
                start = len(a.log)
                a.apply()
                steps += [s.to_dict() for s in a.log[start:]]
                if a.verify()["resolved"] == 0:
                    break
            pending = self._pending(a)
            for s in steps:
                s["params"] = _shrink(s["params"])
        exported = self.export(client, ds.id, fmt)
        sm = a.summary()
        return {"dataset_id": ds.id, "before": view["issues_by_severity"], "rows_before": sm["before"]["rows"],
                "rows_after": sm["after"]["rows"], "steps": steps, "needs_your_decision": pending,
                "files": exported["files"], "notes": exported["notes"],
                "links_expire_in_seconds": exported["links_expire_in_seconds"]}

    def delete(self, client: str, dataset_id: str) -> Dict[str, Any]:
        self.meter.rate_limit(client, self.s.rate_limit_per_minute)
        return {"deleted": self.store.delete_dataset(client, dataset_id)}

    def actions(self) -> List[Dict[str, Any]]:
        return [{"action": a.name, "description": a.description, "params": a.params,
                 "removes_data": a.destructive, "default_risk": E.risk_for(a.name)} for a in E.ACTIONS.values()]

    def download(self, token: str):
        try:
            return self.store.get_artifact(token)
        except NotFound as exc:
            raise ServiceError(str(exc), 404)

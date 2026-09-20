"""MCP (Model Context Protocol) tools -- the surface any agent talks to.

Works with Muse, Claude, ChatGPT, Cursor, VS Code, or any MCP client over
Streamable HTTP: the tools are deterministic and never call a model. Descriptions
are written for the agent: they say when to use each tool and what to tell the user.
"""

from __future__ import annotations

import contextvars
from typing import Any, Dict, List, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .quota import QuotaError
from .service import Service, ServiceError

current_client: contextvars.ContextVar[str] = contextvars.ContextVar("current_client", default="anonymous")
# Full auth principal when available (scheme, subject, tier_hint). Kept separate so
# existing code using current_client (a plain string) keeps working.
current_principal: contextvars.ContextVar[Any] = contextvars.ContextVar("current_principal", default=None)

INSTRUCTIONS = """Agentic Data Cleaner finds and fixes problems in tabular data (CSV, TSV, Excel .xlsx, JSON, JSONL, Parquet).
Typical flow: clean_dataset does everything safe in one call and returns download links plus the decisions that need
the user. For control, use scan_dataset -> get_cleaning_plan -> apply_cleaning_plan -> export_dataset.
Rules: never approve a high-risk change (deleting rows/columns, filling in missing values, masking personal data)
unless the user has said yes to that specific change. The user's original file is never modified. Working copies are
deleted automatically after about an hour; delete_dataset removes one immediately. If dates like 03/04/2024 are
ambiguous, ask the user whether they are day-first (dmy) or month-first (mdy) instead of guessing."""

_RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
_WEB = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
_DEL = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)


def _wrap(fn):
    def call(*a, **kw):
        try:
            return fn(*a, **kw)
        except (ServiceError, QuotaError) as exc:
            raise ToolError(str(exc))     # anticipated failure: the message reaches the model
    call.__name__, call.__doc__ = fn.__name__, fn.__doc__
    return call


def build_mcp(svc: Service) -> MCPServer:
    mcp = MCPServer("Agentic Data Cleaner", instructions=INSTRUCTIONS, version="2.0.0",
                    website_url=svc.s.public_base_url)

    def me() -> str:
        return current_client.get()

    @mcp.tool(annotations=_WEB, title="Clean a dataset in one step")
    def clean_dataset(file_base64: Optional[str] = None, csv_text: Optional[str] = None,
                      file_url: Optional[str] = None, upload_id: Optional[str] = None,
                      filename: Optional[str] = None, output_format: str = "csv",
                      date_order: Optional[str] = None, sheet: Optional[str] = None) -> Dict[str, Any]:
        """Scan a data file, apply every SAFE fix (whitespace, placeholder nulls like 'N/A', encoding damage,
        text/number/date types, category spelling variants), and return download links for the cleaned file
        (plus, on the paid plan, an audit report and a replayable Python script).
        Pass exactly one of: file_base64 (small files), csv_text (pasted CSV), file_url (public https link),
        upload_id (from create_upload_url, for big files). Nothing risky is done automatically: deleting duplicate
        rows, imputing values and masking personal data come back under needs_your_decision for the user to approve
        via apply_cleaning_plan(approve_ids=[...]). output_format: csv, tsv, xlsx, json, jsonl or parquet.
        date_order: 'dmy' or 'mdy' only if the user told you the date convention."""
        return _wrap(svc.clean)(me(), fmt=output_format, date_order=date_order, file_base64=file_base64,
                                csv_text=csv_text, file_url=file_url, upload_id=upload_id, filename=filename, sheet=sheet)

    @mcp.tool(annotations=_WEB, title="Scan a dataset for problems")
    def scan_dataset(file_base64: Optional[str] = None, csv_text: Optional[str] = None,
                     file_url: Optional[str] = None, upload_id: Optional[str] = None,
                     filename: Optional[str] = None, sheet: Optional[str] = None,
                     date_order: Optional[str] = None) -> Dict[str, Any]:
        """Load a file into a temporary working copy and report every data-quality problem found (missing values,
        duplicates, wrong types, inconsistent categories, outliers, encoding damage, personal data), without changing
        anything. Returns a dataset_id for the other tools. Pass exactly one of file_base64, csv_text, file_url,
        upload_id."""
        return _wrap(svc.scan)(me(), file_base64=file_base64, csv_text=csv_text, file_url=file_url,
                               upload_id=upload_id, filename=filename, sheet=sheet, date_order=date_order)

    @mcp.tool(annotations=_RO, title="Show the proposed cleaning plan")
    def get_cleaning_plan(dataset_id: str, date_order: Optional[str] = None) -> Dict[str, Any]:
        """Return the proposed fix for each problem, with its risk level and whether it applies automatically.
        Show high-risk items (needs_explicit_approval=true) to the user in plain language before approving them."""
        return _wrap(svc.plan)(me(), dataset_id, date_order)

    @mcp.tool(annotations=_WRITE, title="Apply the cleaning plan")
    def apply_cleaning_plan(dataset_id: str, auto_approve_max_risk: str = "medium",
                            approve_ids: Optional[List[str]] = None,
                            date_order: Optional[str] = None) -> Dict[str, Any]:
        """Apply the plan to the working copy. Items at or below auto_approve_max_risk ('none', 'low' or 'medium')
        are applied; put a finding_id in approve_ids ONLY after the user explicitly agreed to that specific
        high-risk change. Returns what was done, what is still open and why."""
        return _wrap(svc.apply_plan)(me(), dataset_id, auto_approve_max_risk, approve_ids, date_order)

    @mcp.tool(annotations=_WRITE, title="Run one specific cleaning action")
    def apply_cleaning_action(dataset_id: str, action: str, params: Optional[Dict[str, Any]] = None,
                              confirm: bool = False) -> Dict[str, Any]:
        """Run a single action from list_cleaning_actions with exact parameters, e.g. action='fill_missing',
        params={'columns':['age'],'strategy':'median'}. Actions that remove or invent data require confirm=true,
        which you may set only after the user agreed."""
        return _wrap(svc.apply_action)(me(), dataset_id, action, params, confirm)

    @mcp.tool(annotations=_RO, title="Preview rows")
    def preview_dataset(dataset_id: str, rows: int = 20) -> Dict[str, Any]:
        """Return the first rows (max 50) and column types of the current working copy."""
        return _wrap(svc.preview)(me(), dataset_id, rows)

    @mcp.tool(annotations=_WRITE, title="Export the cleaned data")
    def export_dataset(dataset_id: str, output_format: str = "csv", include_report: bool = True,
                       include_script: bool = True) -> Dict[str, Any]:
        """Create time-limited download links for the cleaned file in csv, tsv, xlsx, json, jsonl or parquet.
        The paid plan also includes a JSON audit report (every change, in order) and a standalone Python script that
        replays the exact cleaning on future files. Give the links to the user; they expire in about an hour."""
        return _wrap(svc.export)(me(), dataset_id, output_format, include_report, include_script)

    @mcp.tool(annotations=_WRITE, title="Get an upload URL for a big file")
    def create_upload_url() -> Dict[str, Any]:
        """For files too large to pass inline: returns a one-time URL. PUT the raw file bytes to it
        (curl -T file.csv 'URL?filename=file.csv'), then call scan_dataset or clean_dataset with the upload_id."""
        return _wrap(svc.create_upload_slot)(me())

    @mcp.tool(annotations=_DEL, title="Delete a dataset now")
    def delete_dataset(dataset_id: str) -> Dict[str, Any]:
        """Permanently delete a working copy and its download links immediately."""
        return _wrap(svc.delete)(me(), dataset_id)

    @mcp.tool(annotations=_RO, title="List available cleaning actions")
    def list_cleaning_actions() -> List[Dict[str, Any]]:
        """List every cleaning action with its parameters and default risk."""
        return svc.actions()

    return mcp

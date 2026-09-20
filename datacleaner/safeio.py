"""Hardened loading / exporting / fetching for the hosted service.

Nothing here touches the filesystem with a user-supplied path. Inputs are bytes; outputs are bytes.
Blocked on purpose: pickle, HDF5, SQLite, HTML/XML readers, legacy .xls, globs and directories.
"""
from __future__ import annotations

import csv, http.client, io, ipaddress, json, socket, ssl, time, zipfile
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import pandas as pd

from .engine import _excel_safe, _json_safe, is_text_dtype, to_object

ALLOWED_INPUT = {".csv", ".tsv", ".txt", ".psv", ".json", ".jsonl", ".ndjson", ".xlsx", ".xlsm", ".parquet"}
EXPORT_FORMATS = {
    "csv": ("text/csv; charset=utf-8", ".csv"),
    "tsv": ("text/tab-separated-values; charset=utf-8", ".tsv"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
    "json": ("application/json", ".json"),
    "jsonl": ("application/x-ndjson", ".jsonl"),
    "parquet": ("application/vnd.apache.parquet", ".parquet"),
}


class DataError(ValueError):
    """Bad input the caller can fix (unsupported format, malformed file, too big)."""


@dataclass(frozen=True)
class Limits:
    max_bytes: int = 25 * 1024 * 1024
    max_rows: int = 200_000
    max_cols: int = 500


@dataclass
class Loaded:
    df: pd.DataFrame
    fmt: str
    sheet_names: List[str]
    sheet: Optional[str] = None


# ------------------------------------------------------------------ detection
def _ext(filename: Optional[str]) -> str:
    name = (filename or "").lower().split("?")[0]
    return "." + name.rsplit(".", 1)[-1] if "." in name else ""


def detect_format(data: bytes, filename: Optional[str] = None) -> str:
    ext = _ext(filename)
    if ext == ".xls" or ext == ".xlsb" or ext == ".ods":
        raise DataError("Legacy Excel/ODS files are not accepted. Save the file as .xlsx or CSV and try again.")
    if ext in (".pkl", ".pickle", ".h5", ".hdf5", ".db", ".sqlite", ".sqlite3", ".html", ".htm", ".xml"):
        raise DataError(f"'{ext}' files are not accepted by the hosted service.")
    if data[:4] == b"PAR1":
        return "parquet"
    if data[:2] == b"PK":
        return "xlsx"
    if ext in (".xlsx", ".xlsm"):
        return "xlsx"
    if ext == ".parquet":
        return "parquet"
    if ext in (".jsonl", ".ndjson"):
        return "jsonl"
    head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    if ext == ".json" or head[:1] in (b"[", b"{"):
        return "json"
    if ext == ".tsv":
        return "tsv"
    return "csv"


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1", errors="replace")


def _delimiter(text: str, hint: Optional[str]) -> str:
    if hint == "tsv":
        return "\t"
    sample = text[:65536]
    first = sample.split("\n", 1)[0]
    if not any(d in first for d in ",;\t|"):
        return ","
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except Exception:
        counts = {d: first.count(d) for d in [",", ";", "\t", "|"]}
        return max(counts, key=counts.get)


def _strict_field_counts(text: str, delim: str, limits: Limits) -> None:
    """Reject ragged rows BEFORE pandas: newer pandas silently drops misaligned
    fields (data loss with status 200) instead of raising ParserError."""
    try:
        rows = list(csv.reader(io.StringIO(text[:4 * 1024 * 1024]), delimiter=delim))
    except Exception:
        return                                    # let pandas produce the real error
    rows = [r for r in rows if r and not (len(r) == 1 and not r[0].strip())]
    if not rows:
        return
    width = len(rows[0])
    if width > limits.max_cols:
        raise DataError(f"The file has {width:,} columns; the limit is {limits.max_cols:,}.")
    bad = [i + 1 for i, r in enumerate(rows[1:]) if len(r) != width]
    if bad:
        shown = ", ".join(str(i) for i in bad[:5]) + ("..." if len(bad) > 5 else "")
        raise DataError(f"Some rows have a different number of columns than the header "
                        f"(rows {shown} have {len(rows[bad[0]])} fields instead of {width}). "
                        f"Re-export the file or fix the malformed rows.")


# --------------------------------------------------------------------- loaders
def _check_zip(data: bytes) -> None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise DataError("That file is not a valid .xlsx workbook.")
    infos = zf.infolist()
    total = sum(i.file_size for i in infos)
    if len(infos) > 5000 or total > 300 * 1024 * 1024 or (len(data) and total / len(data) > 200):
        raise DataError("The workbook expands to an unreasonable size and was rejected.")
    if not any(i.filename == "xl/workbook.xml" for i in infos):
        raise DataError("That archive is not an Excel workbook.")


def _finish(df: pd.DataFrame, limits: Limits) -> pd.DataFrame:
    if len(df) > limits.max_rows:
        raise DataError(f"The file has more than {limits.max_rows:,} rows, which is the limit for this plan.")
    if len(df.columns) == 0:
        raise DataError("The file has no columns.")
    if len(df.columns) > limits.max_cols:
        raise DataError(f"The file has {len(df.columns):,} columns; the limit is {limits.max_cols:,}.")
    df = df.copy()
    df.columns = [str(c) for c in df.columns]
    return df.reset_index(drop=True)


def _records_to_frame(records: Any, limits: Limits) -> pd.DataFrame:
    if isinstance(records, dict):
        lists = [v for v in records.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
        if len(lists) == 1:
            records = lists[0]
        elif all(isinstance(v, list) for v in records.values()):
            return pd.DataFrame(records)
        else:
            records = [records]
    if not isinstance(records, list):
        raise DataError("JSON must be an array of records or an object containing one.")
    if len(records) > limits.max_rows:
        raise DataError(f"The file has more than {limits.max_rows:,} rows, which is the limit for this plan.")
    if records and all(isinstance(r, dict) for r in records):
        return pd.json_normalize(records, max_level=3)
    return pd.DataFrame({"value": records})


def load_bytes(data: bytes, filename: Optional[str] = None, *, sheet: Any = None,
               limits: Limits = Limits()) -> Loaded:
    if not data:
        raise DataError("The file is empty.")
    if len(data) > limits.max_bytes:
        raise DataError(f"The file is larger than {limits.max_bytes // (1024 * 1024)} MB, the limit for this plan.")
    fmt = detect_format(data, filename)
    names: List[str] = []
    chosen: Optional[str] = None
    try:
        if fmt in ("csv", "tsv"):
            text = _decode(data)
            delim = _delimiter(text, fmt)
            _strict_field_counts(text, delim, limits)
            df = pd.read_csv(io.StringIO(text), sep=delim, nrows=limits.max_rows + 1,
                             low_memory=False, skipinitialspace=True,
                             keep_default_na=False, na_values=[""])
        elif fmt == "xlsx":
            _check_zip(data)
            with pd.ExcelFile(io.BytesIO(data), engine="openpyxl") as xl:
                names = list(xl.sheet_names)
                if sheet is None:
                    chosen = names[0]
                elif isinstance(sheet, int) and 0 <= sheet < len(names):
                    chosen = names[sheet]
                elif sheet in names:
                    chosen = sheet
                else:
                    short = repr(sheet)
                    if len(short) > 200:
                        short = short[:200] + "..."
                    raise DataError(f"Sheet {short} not found. Available sheets: {names}")
                df = xl.parse(chosen, nrows=limits.max_rows + 1, keep_default_na=False, na_values=[""])
        elif fmt == "json":
            df = _records_to_frame(json.loads(_decode(data)), limits)
        elif fmt == "jsonl":
            lines = [ln for ln in _decode(data).splitlines() if ln.strip()]
            if len(lines) > limits.max_rows:
                raise DataError(f"The file has more than {limits.max_rows:,} rows, which is the limit for this plan.")
            df = _records_to_frame([json.loads(ln) for ln in lines], limits)
        elif fmt == "parquet":
            import pyarrow.parquet as pq
            pf = pq.ParquetFile(io.BytesIO(data))
            if pf.metadata.num_rows > limits.max_rows:
                raise DataError(f"The file has {pf.metadata.num_rows:,} rows; the limit for this plan is {limits.max_rows:,}.")
            if pf.metadata.num_columns > limits.max_cols:
                raise DataError(f"The file has {pf.metadata.num_columns:,} columns; the limit is {limits.max_cols:,}.")
            try:
                raw_size = sum(pf.metadata.row_group(i).total_byte_size
                               for i in range(pf.metadata.num_row_groups))
            except Exception:
                raw_size = 0
            if raw_size and raw_size > limits.max_bytes * 8:
                raise DataError(f"The file expands to ~{raw_size // (1024 * 1024)} MB in memory, "
                                f"over the limit for this plan. Export it as CSV and try again.")
            df = pf.read().to_pandas()
        else:  # pragma: no cover - detect_format only returns the formats above
            raise DataError(f"Unsupported format: {fmt}")
    except DataError:
        raise
    except pd.errors.ParserError as exc:
        raise DataError("Some rows have a different number of columns than the header. Re-export the file "
                        f"or fix the malformed rows. ({str(exc)[:160]})")
    except pd.errors.EmptyDataError:
        raise DataError("The file has no data.")
    except json.JSONDecodeError as exc:
        raise DataError(f"The JSON is malformed: {exc.msg} (line {exc.lineno}).")
    except Exception as exc:
        raise DataError(f"Could not read that file as {fmt}: {type(exc).__name__}: {str(exc)[:160]}")
    return Loaded(_finish(df, limits), fmt, names, chosen)


# ---------------------------------------------------------------------- export
def export_bytes(df: pd.DataFrame, fmt: str, na_rep: str = "") -> Tuple[bytes, str, str]:
    """Serialise to bytes. Returns (payload, mime type, extension)."""
    if fmt not in EXPORT_FORMATS:
        raise DataError(f"Unsupported export format '{fmt}'. Choose one of {sorted(EXPORT_FORMATS)}.")
    out = df.copy()
    for col in out.columns:
        if isinstance(out[col].dtype, pd.CategoricalDtype):
            out[col] = out[col].astype(object)
    mime, ext = EXPORT_FORMATS[fmt]
    buf = io.BytesIO()
    if fmt == "csv":
        payload = out.to_csv(index=False, na_rep=na_rep).encode("utf-8")
    elif fmt == "tsv":
        payload = out.to_csv(index=False, sep="\t", na_rep=na_rep).encode("utf-8")
    elif fmt == "xlsx":
        _excel_safe(out).to_excel(buf, index=False, engine="openpyxl")
        payload = buf.getvalue()
    elif fmt == "json":
        payload = _json_safe(out).to_json(orient="records", indent=2, date_format="iso").encode("utf-8")
    elif fmt == "jsonl":
        payload = _json_safe(out).to_json(orient="records", lines=True, date_format="iso").encode("utf-8")
    else:
        try:
            out.to_parquet(buf, index=False)
        except Exception:               # mixed-type object columns: store them as text
            for col in out.columns:
                if pd.api.types.is_object_dtype(out[col].dtype):
                    s = out[col]
                    out[col] = s.where(s.isna(), s.astype(str))
            buf = io.BytesIO()
            out.to_parquet(buf, index=False)
        payload = buf.getvalue()
    return payload, mime, ext


# --------------------------------------------------------- SSRF-safe URL fetch
def _is_public(ip: ipaddress._BaseAddress) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return bool(ip.is_global and not ip.is_multicast)


def resolve_public(host: str, port: int) -> str:
    """Resolve a hostname and refuse anything that is not a public internet address."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise DataError(f"Could not resolve host '{host}'.")
    addrs = [i[4][0] for i in infos]
    if not addrs:
        raise DataError(f"Could not resolve host '{host}'.")
    for a in addrs:
        if not _is_public(ipaddress.ip_address(a.split("%")[0])):
            raise DataError("That URL points to a private or internal address and was blocked.")
    return addrs[0]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a pre-validated IP while still verifying the certificate for the real hostname."""

    def __init__(self, host: str, ip: str, **kw: Any):
        super().__init__(host, **kw)
        self._pinned_ip = ip

    def connect(self) -> None:
        sock = socket.create_connection((self._pinned_ip, self.port), self.timeout)
        self.sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.host)


def _open(host: str, ip: str, port: int, timeout: float) -> http.client.HTTPConnection:
    return _PinnedHTTPSConnection(host, ip, port=port, timeout=timeout)


def fetch_url(url: str, *, max_bytes: int, timeout: float = 15.0, max_redirects: int = 3) -> Tuple[bytes, str]:
    """Download a public https file. Returns (bytes, filename)."""
    deadline = time.monotonic() + timeout * 2
    for _ in range(max_redirects + 1):
        parts = urlsplit(url)
        if parts.scheme != "https":
            raise DataError("Only https:// links are accepted.")
        if parts.username or parts.password:
            raise DataError("Links containing credentials are not accepted.")
        host = parts.hostname or ""
        port = parts.port or 443
        if not host or port != 443:
            raise DataError("Only standard https links (port 443) are accepted.")
        ip = resolve_public(host, port)
        conn = _open(host, ip, port, timeout)
        try:
            path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            conn.request("GET", path, headers={"Host": host, "User-Agent": "agentic-data-cleaner/2",
                                               "Accept-Encoding": "identity", "Accept": "*/*"})
            resp = conn.getresponse()
            if resp.status in (301, 302, 303, 307, 308):
                loc = resp.getheader("Location")
                if not loc:
                    raise DataError("The link redirected without a destination.")
                url = urljoin(url, loc)
                continue
            if resp.status != 200:
                raise DataError(f"The link returned HTTP {resp.status}.")
            length = resp.getheader("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise DataError(f"The file is larger than {max_bytes // (1024 * 1024)} MB, the limit for this plan.")
            chunks, size = [], 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise DataError(f"The file is larger than {max_bytes // (1024 * 1024)} MB, the limit for this plan.")
                if time.monotonic() > deadline:
                    raise DataError("The download took too long.")
                chunks.append(chunk)
            name = parts.path.rsplit("/", 1)[-1] or "download"
            return b"".join(chunks), name
        except DataError:
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise DataError(f"Could not download the file: {type(exc).__name__}.")
        finally:
            conn.close()
    raise DataError("Too many redirects.")

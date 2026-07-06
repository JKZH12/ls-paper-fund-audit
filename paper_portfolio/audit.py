from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .core import portfolio_metrics
from .db import DEFAULT_DB_PATH, get_portfolio, load_state


ZERO_HASH = "0" * 64
EVENT_LOG_PATH = Path("audit/events.jsonl")
MANIFEST_DIR = Path("audit/manifests")


@dataclass(frozen=True)
class AuditVerification:
    ok: bool
    event_count: int
    head_hash: str
    problems: list[str]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compute_event_hash(
    *,
    portfolio_id: int,
    event_type: str,
    created_at: str,
    previous_hash: str,
    payload: dict[str, Any],
) -> str:
    material = {
        "version": 1,
        "portfolio_id": portfolio_id,
        "event_type": event_type,
        "created_at": created_at,
        "previous_hash": previous_hash,
        "payload": payload,
    }
    return sha256_text(canonical_json(material))


def audit_status(conn: sqlite3.Connection, portfolio_id: int) -> tuple[int, str]:
    count_row = conn.execute(
        "SELECT COUNT(*) AS event_count FROM audit_events WHERE portfolio_id = ?",
        (portfolio_id,),
    ).fetchone()
    hash_row = conn.execute(
        """
        SELECT event_hash
        FROM audit_events
        WHERE portfolio_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (portfolio_id,),
    ).fetchone()
    event_count = 0 if not count_row else int(count_row["event_count"])
    head_hash = ZERO_HASH if not hash_row else str(hash_row["event_hash"])
    return event_count, head_hash


def latest_event_hash(conn: sqlite3.Connection, portfolio_id: int) -> str:
    row = conn.execute(
        """
        SELECT event_hash
        FROM audit_events
        WHERE portfolio_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (portfolio_id,),
    ).fetchone()
    return ZERO_HASH if not row else str(row["event_hash"])


def portfolio_snapshot(conn: sqlite3.Connection, portfolio_id: int) -> dict[str, Any]:
    portfolio = get_portfolio(conn, portfolio_id)
    state = load_state(conn, portfolio_id)
    metrics = portfolio_metrics(state)
    return {
        "portfolio": {
            "id": portfolio_id,
            "name": portfolio["name"],
            "strategy_type": portfolio["strategy_type"],
            "base_currency": portfolio["base_currency"],
            "initial_cash": float(portfolio["initial_cash"]),
            "cash": float(portfolio["cash"]),
        },
        "holdings": [
            {
                "symbol": holding.symbol,
                "quantity": holding.quantity,
                "average_cost": holding.average_cost,
                "last_price": holding.last_price,
                "realized_pnl": holding.realized_pnl,
            }
            for holding in sorted(state.holdings.values(), key=lambda item: item.symbol)
        ],
        "metrics": metrics,
    }


def record_audit_event(
    conn: sqlite3.Connection,
    *,
    portfolio_id: int,
    event_type: str,
    payload: dict[str, Any],
) -> str:
    previous_hash = latest_event_hash(conn, portfolio_id)
    created_at = utc_now()
    event_hash = compute_event_hash(
        portfolio_id=portfolio_id,
        event_type=event_type,
        created_at=created_at,
        previous_hash=previous_hash,
        payload=payload,
    )
    conn.execute(
        """
        INSERT INTO audit_events (portfolio_id, event_type, payload_json, previous_hash, event_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (portfolio_id, event_type, canonical_json(payload), previous_hash, event_hash, created_at),
    )
    return event_hash


def ensure_genesis_event(conn: sqlite3.Connection, portfolio_id: int) -> str | None:
    event_count, _ = audit_status(conn, portfolio_id)
    if event_count:
        return None
    return record_audit_event(
        conn,
        portfolio_id=portfolio_id,
        event_type="portfolio_genesis",
        payload={
            "snapshot": portfolio_snapshot(conn, portfolio_id),
            "policy": {
                "simulated_only": True,
                "real_broker_execution": False,
                "audit_chain": "sha256_previous_hash",
            },
        },
    )


def iter_audit_events(conn: sqlite3.Connection, portfolio_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT id, portfolio_id, event_type, payload_json, previous_hash, event_hash, created_at
            FROM audit_events
            WHERE portfolio_id = ?
            ORDER BY id
            """,
            (portfolio_id,),
        )
    )


def sync_event_log(conn: sqlite3.Connection, portfolio_id: int, workspace: Path = Path(".")) -> Path:
    path = workspace / EVENT_LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = iter_audit_events(conn, portfolio_id)
    tmp_path = path.with_suffix(".jsonl.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            event = {
                "id": int(row["id"]),
                "portfolio_id": int(row["portfolio_id"]),
                "event_type": row["event_type"],
                "created_at": row["created_at"],
                "previous_hash": row["previous_hash"],
                "event_hash": row["event_hash"],
                "payload": json.loads(row["payload_json"]),
            }
            handle.write(canonical_json(event) + "\n")
    tmp_path.replace(path)
    return path


def verify_audit_chain(conn: sqlite3.Connection, portfolio_id: int) -> AuditVerification:
    problems: list[str] = []
    previous_hash = ZERO_HASH
    head_hash = ZERO_HASH
    rows = iter_audit_events(conn, portfolio_id)
    for row in rows:
        payload = json.loads(row["payload_json"])
        expected_hash = compute_event_hash(
            portfolio_id=int(row["portfolio_id"]),
            event_type=row["event_type"],
            created_at=row["created_at"],
            previous_hash=row["previous_hash"],
            payload=payload,
        )
        if row["previous_hash"] != previous_hash:
            problems.append(f"event {row['id']} previous_hash does not match prior head")
        if row["event_hash"] != expected_hash:
            problems.append(f"event {row['id']} event_hash does not match payload")
        previous_hash = row["event_hash"]
        head_hash = row["event_hash"]
    return AuditVerification(ok=not problems, event_count=len(rows), head_hash=head_hash, problems=problems)


def write_manifest(
    conn: sqlite3.Connection,
    *,
    portfolio_id: int,
    workspace: Path = Path("."),
    report_path: Path | None = None,
    db_path: Path = DEFAULT_DB_PATH,
    manifest_date: date | None = None,
) -> Path:
    manifest_date = manifest_date or date.today()
    event_log_path = sync_event_log(conn, portfolio_id, workspace)
    verification = verify_audit_chain(conn, portfolio_id)
    snapshot = portfolio_snapshot(conn, portfolio_id)
    manifest = {
        "manifest_version": 1,
        "generated_at": utc_now(),
        "manifest_date": manifest_date.isoformat(),
        "portfolio": snapshot["portfolio"],
        "metrics": snapshot["metrics"],
        "audit": {
            "event_count": verification.event_count,
            "head_hash": verification.head_hash,
            "chain_ok": verification.ok,
            "events_file": str(EVENT_LOG_PATH),
            "events_file_sha256": file_sha256(event_log_path),
        },
        "files": {
            "database": {
                "path": str(db_path),
                "sha256": file_sha256(workspace / db_path),
                "tracked_by_git": False,
            },
            "daily_report": {
                "path": None if report_path is None else str(report_path),
                "sha256": None if report_path is None else file_sha256(workspace / report_path),
            },
        },
        "verification": {
            "local_command": "python3 -m paper_portfolio audit verify",
            "github_anchor": "git commit history anchors this manifest once pushed",
        },
    }
    manifest_dir = workspace / MANIFEST_DIR
    manifest_dir.mkdir(parents=True, exist_ok=True)
    path = manifest_dir / f"{manifest_date.isoformat()}.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    return path


def git_anchor(
    *,
    workspace: Path = Path("."),
    message: str,
    include_code: bool = False,
    push: bool = True,
) -> dict[str, str | bool | None]:
    paths = ["audit", "reports/daily"]
    if include_code:
        paths.extend([".gitignore", "AGENTS.md", "README.md", "paper_portfolio", "pyproject.toml", "tests"])
    subprocess.run(["git", "add", *paths], cwd=workspace, check=True)
    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=workspace)
    if diff.returncode == 0:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=workspace, text=True, capture_output=True)
        return {"committed": False, "pushed": False, "commit": head.stdout.strip() or None, "reason": "no staged changes"}

    subprocess.run(["git", "commit", "-m", message], cwd=workspace, check=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=workspace, check=True, text=True, capture_output=True).stdout.strip()
    branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=workspace, check=True, text=True, capture_output=True
    ).stdout.strip()
    remotes = subprocess.run(["git", "remote"], cwd=workspace, check=True, text=True, capture_output=True).stdout.splitlines()
    pushed = False
    if push and remotes:
        subprocess.run(["git", "push", "-u", remotes[0], branch], cwd=workspace, check=True)
        pushed = True
    return {"committed": True, "pushed": pushed, "commit": head, "branch": branch, "remote": remotes[0] if remotes else None}

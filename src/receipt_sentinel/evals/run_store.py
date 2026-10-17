"""run_store

Persists scored eval runs: one SQLite row for querying history plus a full JSON file per run.
Writes happen only from explicit CLI commands.

Status:
    None
Issues:
    None
Todo:
    None
"""

import contextlib
import json
import secrets
import sqlite3
import statistics
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from receipt_sentinel.evals.golden_case import Difficulty
from receipt_sentinel.evals.scoring import CaseScore, is_provider_errored

CREATE_RUNS_TABLE = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    prompt_label TEXT NOT NULL,
    model_id TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    status TEXT NOT NULL,
    pass_rate REAL NOT NULL,
    total_cost_usd REAL NOT NULL,
    git_sha TEXT NOT NULL,
    json_path TEXT NOT NULL
)
"""


class RunStatus(StrEnum):
    PASS = "PASS"  # noqa: S105 - enum value, not a password
    WARN = "WARN"
    CRITICAL = "CRITICAL"
    PROVIDER_UNSTABLE = "PROVIDER_UNSTABLE"


@dataclass(frozen=True)
class EvalRun:
    run_id: str
    created_at: str
    prompt_label: str
    model_id: str
    dataset_version: str
    scores: tuple[CaseScore, ...]
    status: RunStatus
    git_sha: str

    @property
    def pass_rate(self) -> float:
        """Passed / scoreable, where provider-errored cases are not scoreable."""
        scoreable = [s for s in self.scores if not is_provider_errored(s)]
        return sum(s.passed for s in scoreable) / len(scoreable) if scoreable else 0.0

    @property
    def error_rate(self) -> float:
        return sum(s.errored for s in self.scores) / len(self.scores) if self.scores else 0.0

    @property
    def total_cost_usd(self) -> float:
        return sum(s.cost_usd for s in self.scores)

    @property
    def p50_latency_ms(self) -> int:
        latencies = [s.latency_ms for s in self.scores if not s.errored]
        return int(statistics.median(latencies)) if latencies else 0


def new_run_id() -> str:
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"


def provider_error_rate(scores: tuple[CaseScore, ...]) -> float:
    if not scores:
        return 0.0
    return sum(is_provider_errored(s) for s in scores) / len(scores)


def run_to_dict(run: EvalRun) -> dict[str, object]:
    data = asdict(run)
    data["status"] = run.status.value
    data["scores"] = [{**asdict(s), "difficulty": s.difficulty.value} for s in run.scores]
    return data


def run_from_dict(data: dict[str, object]) -> EvalRun:
    raw_scores = data["scores"]
    if not isinstance(raw_scores, list):
        raise ValueError("run JSON: 'scores' must be a list")
    scores = tuple(
        CaseScore(**{**s, "difficulty": Difficulty(s["difficulty"])}) for s in raw_scores
    )
    return EvalRun(
        run_id=str(data["run_id"]),
        created_at=str(data["created_at"]),
        prompt_label=str(data["prompt_label"]),
        model_id=str(data["model_id"]),
        dataset_version=str(data["dataset_version"]),
        scores=scores,
        status=RunStatus(str(data["status"])),
        git_sha=str(data["git_sha"]),
    )


def load_run_json(path: Path) -> EvalRun:
    return run_from_dict(json.loads(path.read_text()))


def save_run(run: EvalRun, *, db_path: Path, json_dir: Path) -> Path:
    if run.status is RunStatus.PROVIDER_UNSTABLE:
        raise ValueError("PROVIDER_UNSTABLE runs are reported, never stored as scored runs")
    json_dir.mkdir(parents=True, exist_ok=True)
    # Absolute, so the SQLite history stays resolvable from any working directory
    json_path = (json_dir / f"{run.run_id}.json").resolve()
    json_path.write_text(json.dumps(run_to_dict(run), indent=2))
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        conn.execute(CREATE_RUNS_TABLE)
        conn.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run.run_id,
                run.created_at,
                run.prompt_label,
                run.model_id,
                run.dataset_version,
                run.status.value,
                run.pass_rate,
                run.total_cost_usd,
                run.git_sha,
                str(json_path),
            ),
        )
        conn.commit()
    return json_path


def list_recent_runs(db_path: Path, *, dataset_version: str, limit: int) -> list[EvalRun]:
    if not db_path.exists():
        return []
    with contextlib.closing(sqlite3.connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT json_path FROM runs WHERE dataset_version = ? "
            "ORDER BY created_at DESC, run_id DESC LIMIT ?",
            (dataset_version, limit),
        ).fetchall()
    return [load_run_json(Path(row[0])) for row in rows]

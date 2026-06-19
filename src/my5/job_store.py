"""
DynamoDB wrapper for the my5-sim-jobs table.

Thin interface over the job lifecycle: create, read, and update job records.
The table is the single source of truth for job state. SQS messages are just
pointers (job_id only); everything else lives here.

Design rule: this module does I/O (DynamoDB calls) only through the JobStore
class. The pure put/get/update methods are injected in tests via a fake table,
so no real network calls are needed during unit testing.
"""
from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from my5.config import make_dynamo_resource

if TYPE_CHECKING:
    from my5.simulator import SimResult

_TABLE_NAME = "my5-sim-jobs"


class LineupNotFoundError(Exception):
    """Raised when a lineup key or player ID is missing from DynamoDB."""


def _dec_to_float(obj: Any) -> Any:
    """
    Recursively convert Decimal values returned by the boto3 resource API to float.

    The resource API deserializes DynamoDB Numbers to Decimal (not float) to avoid
    IEEE-754 precision loss. The simulator expects Python floats. Call this on any
    item fetched from DynamoDB before handing it to the engine.
    """
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _dec_to_float(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_dec_to_float(x) for x in obj]
    return obj


def _to_decimal(x: float | int) -> Decimal:
    """Convert float/int to Decimal via str() to avoid binary float noise."""
    return Decimal(str(x))


class JobStore:
    """
    Thin wrapper over the my5-sim-jobs DynamoDB table.

    Pass a fake `table` object in tests to avoid network calls:
        store = JobStore(table=FakeTable())

    The `table` argument is a boto3 DynamoDB Table resource (or duck-typed
    equivalent implementing put_item, get_item, update_item).
    """

    def __init__(self, table: Any = None) -> None:
        if table is not None:
            self._table = table
        else:
            self._table = make_dynamo_resource().Table(_TABLE_NAME)

    def put_job(self, item: dict[str, Any]) -> None:
        """
        Write a new job record. Overwrites any existing item with the same job_id.

        The caller is responsible for supplying a valid item dict. Optional fields
        (started_at, completed_at, result, error_*) should be omitted (not None)
        so they are absent from the DynamoDB item rather than stored as null.
        """
        self._table.put_item(Item=item)

    def get_job(self, job_id: str) -> dict[str, Any]:
        """
        Read a job record by job_id.

        Returns all fields with Decimal values converted to float (via _dec_to_float)
        so callers don't need to worry about Decimal handling.

        Raises KeyError if the job does not exist.
        """
        resp = self._table.get_item(Key={"job_id": job_id})
        if "Item" not in resp:
            raise KeyError(f"Job {job_id!r} not found in {_TABLE_NAME}")
        return _dec_to_float(resp["Item"])

    def update_status(
        self,
        job_id: str,
        status: str,
        *,
        increment_attempt: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """
        Update the status field and optional extra string fields.

        Pass increment_attempt=True when a worker claims the job (QUEUED→RUNNING)
        to atomically increment attempt_count. The ADD / SET combination is atomic
        in DynamoDB — no race condition between two workers reading attempt_count.

        extra: optional dict of {attribute_name: string_value} for metadata fields
               (started_at, worker_id, completed_at). Values must be strings.
        """
        # `status` is a DynamoDB reserved word — must use an expression attribute alias.
        expr_parts = ["#s = :s"]
        attr_names: dict[str, str] = {"#s": "status"}
        attr_values: dict[str, Any] = {":s": status}

        if increment_attempt:
            expr_parts.append("attempt_count = attempt_count + :one")
            attr_values[":one"] = 1  # int — resource API serializes to Number

        if extra:
            for i, (k, v) in enumerate(extra.items()):
                n_ph, v_ph = f"#x{i}", f":x{i}"
                expr_parts.append(f"{n_ph} = {v_ph}")
                attr_names[n_ph] = k
                attr_values[v_ph] = v

        self._table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET " + ", ".join(expr_parts),
            ExpressionAttributeNames=attr_names,
            ExpressionAttributeValues=attr_values,
        )

    def write_result(self, job_id: str, result: "SimResult", completed_at: str) -> None:
        """
        Atomically set status=done, write SimResult, and record completion time.

        All SimResult floats are converted to Decimal(str(x)) before storage
        because the boto3 resource API rejects Python floats for Number attributes.
        """
        result_map = {
            "mean_margin":      _to_decimal(result.mean_margin),
            "ci_half_width":    _to_decimal(result.ci_half_width),
            "n_sims":           _to_decimal(result.n_sims),
            "equiv_net_rating": _to_decimal(result.equiv_net_rating),
            "converged":        result.converged,   # bool — stored as DynamoDB BOOL
            "mean_pts_a":       _to_decimal(result.mean_pts_a),
            "mean_pts_b":       _to_decimal(result.mean_pts_b),
        }
        # Both `status` and `result` are DynamoDB reserved words — alias both.
        self._table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET #s = :s, #r = :r, completed_at = :ca",
            ExpressionAttributeNames={"#s": "status", "#r": "result"},
            ExpressionAttributeValues={
                ":s": "done",
                ":r": result_map,
                ":ca": completed_at,
            },
        )

    def update_progress(self, job_id: str, sims_done: int, ci_half: float) -> None:
        """
        Write a progress snapshot to the job record while status=running.

        Called by handle_job's on_progress closure every _PROGRESS_INTERVAL sims.
        A poller (or future WebSocket push) reads progress_sims and progress_ci to
        show live updates. These fields are overwritten on each call (not appended).
        """
        self._table.update_item(
            Key={"job_id": job_id},
            UpdateExpression="SET progress_sims = :ps, progress_ci = :ci",
            ExpressionAttributeValues={
                ":ps": sims_done,
                ":ci": _to_decimal(ci_half),
            },
        )

    def fail_job(
        self,
        job_id: str,
        error_type: str,
        error_message: str,
        completed_at: str,
    ) -> None:
        """Set status=failed and record why. Used for both invalid-lineup and DLQ paths."""
        self._table.update_item(
            Key={"job_id": job_id},
            UpdateExpression=(
                "SET #s = :s, error_type = :et, error_message = :em, completed_at = :ca"
            ),
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":s": "failed",
                ":et": error_type,
                ":em": error_message[:2048],
                ":ca": completed_at,
            },
        )

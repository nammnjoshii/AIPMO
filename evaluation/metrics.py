"""Evaluation metrics — T-085.

MetricsTracker records detection outcomes and human feedback.
Persists to SQLite (evaluation_metrics table in same autonomous_pmo.db).
CLI: python -m evaluation.metrics --report

8 tracked metrics:
  1. risk_intelligence.precision
  2. risk_intelligence.recall
  3. risk_intelligence.false_positive_rate
  4. risk_intelligence.time_to_detection_hours
  5. issue_management.precision
  6. issue_management.false_positive_rate
  7. issue_management.time_to_detection_hours
  8. communication.acceptance_rate
  9. communication.edit_rate
  10. communication.report_latency_seconds
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_DB = os.environ.get("SQLITE_DB_PATH", "./data/autonomous_pmo.db")

# Targets from README.md
_TARGETS: Dict[str, Dict[str, float]] = {
    "risk_intelligence": {
        "precision": 0.80,
        "recall": 0.70,
        "false_positive_rate": 0.15,
        "time_to_detection_hours": 24.0,
    },
    "issue_management": {
        "precision": 0.85,
        "false_positive_rate": 0.10,
        "time_to_detection_hours": 12.0,
    },
    "communication": {
        "acceptance_rate": 0.90,
        "edit_rate": 0.20,  # <= target (lower is better)
        "report_latency_seconds": 30.0,  # <= target
    },
}


class MetricsTracker:
    """Tracks agent performance metrics with SQLite persistence.

    Usage:
        tracker = MetricsTracker()
        await tracker.initialize()
        await tracker.record_detection(agent="risk_intelligence", detected=True, ...)
        await tracker.record_human_feedback(agent="communication", accepted=True)
        report = await tracker.get_report()
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._db_path = db_path or _DEFAULT_DB

    async def initialize(self) -> None:
        """Create the evaluation_metrics table if it doesn't exist."""
        try:
            import aiosqlite
        except ImportError:
            raise ImportError("aiosqlite is not installed. Run: pip install aiosqlite")

        os.makedirs(os.path.dirname(os.path.abspath(self._db_path)), exist_ok=True)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("""
                CREATE TABLE IF NOT EXISTS evaluation_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent TEXT NOT NULL,
                    metric_type TEXT NOT NULL,
                    value REAL NOT NULL,
                    label TEXT,
                    project_id TEXT,
                    event_id TEXT,
                    recorded_at TEXT NOT NULL
                )
            """)
            await db.commit()

    async def record_detection(
        self,
        agent: str,
        detected: bool,
        false_positive: bool = False,
        time_to_detection_hours: Optional[float] = None,
        project_id: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> None:
        """Record a detection outcome for precision/recall tracking.

        Args:
            agent: Agent name (e.g., "risk_intelligence").
            detected: True if the agent correctly detected the event.
            false_positive: True if the detection was a false positive.
            time_to_detection_hours: Hours from event to detection.
            project_id: Optional project context.
            event_id: Optional source event ID.
        """
        now = datetime.now(timezone.utc).isoformat()
        rows = []

        if detected and not false_positive:
            rows.append((agent, "true_positive", 1.0, "tp"))
        elif false_positive:
            rows.append((agent, "false_positive", 1.0, "fp"))
        else:
            rows.append((agent, "false_negative", 1.0, "fn"))

        if time_to_detection_hours is not None and detected:
            rows.append((agent, "time_to_detection_hours", time_to_detection_hours, "latency"))

        await self._insert_rows(rows, project_id=project_id, event_id=event_id, now=now)

    async def record_human_feedback(
        self,
        agent: str = "communication",
        accepted: bool = True,
        edited: bool = False,
        latency_seconds: Optional[float] = None,
        project_id: Optional[str] = None,
    ) -> None:
        """Record human feedback on agent output (acceptance/edit rate).

        Args:
            agent: Agent name (default "communication").
            accepted: True if the output was accepted without major change.
            edited: True if the human significantly edited the output.
            latency_seconds: Report generation latency in seconds.
            project_id: Optional project context.
        """
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (agent, "accepted", 1.0 if accepted else 0.0, "acceptance"),
            (agent, "edited", 1.0 if edited else 0.0, "edit"),
        ]
        if latency_seconds is not None:
            rows.append((agent, "report_latency_seconds", latency_seconds, "latency"))

        await self._insert_rows(rows, project_id=project_id, now=now)

    async def _insert_rows(
        self,
        rows: List[tuple],
        project_id: Optional[str] = None,
        event_id: Optional[str] = None,
        now: Optional[str] = None,
    ) -> None:
        now = now or datetime.now(timezone.utc).isoformat()
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as db:
            for agent, metric_type, value, label in rows:
                await db.execute(
                    """
                    INSERT INTO evaluation_metrics
                    (agent, metric_type, value, label, project_id, event_id, recorded_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (agent, metric_type, value, label, project_id, event_id, now),
                )
            await db.commit()

    async def get_report(self) -> Dict[str, Any]:
        """Compute all 8 metrics and compare against targets.

        Returns:
            Dict keyed by agent → {metric → {current, target, status}}.
        """
        import aiosqlite
        report: Dict[str, Any] = {}

        async with aiosqlite.connect(self._db_path) as db:
            # Fetch all rows
            async with db.execute(
                "SELECT agent, metric_type, value FROM evaluation_metrics"
            ) as cursor:
                rows = await cursor.fetchall()

        # Aggregate by agent + metric_type
        from collections import defaultdict
        raw: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))
        for agent, metric_type, value in rows:
            raw[agent][metric_type].append(float(value))

        for agent, target_metrics in _TARGETS.items():
            agent_raw = raw.get(agent, {})
            report[agent] = {}

            for metric_name, target_val in target_metrics.items():
                current = self._compute_metric(agent, metric_name, agent_raw)
                direction = "gte" if metric_name not in ("false_positive_rate", "edit_rate", "report_latency_seconds", "time_to_detection_hours") else "lte"

                if direction == "gte":
                    met = current >= target_val if current is not None else False
                else:
                    met = current <= target_val if current is not None else True

                report[agent][metric_name] = {
                    "current": round(current, 3) if current is not None else None,
                    "target": target_val,
                    "status": "PASS" if met else "FAIL",
                    "direction": direction,
                }

        return report

    def _compute_metric(
        self,
        agent: str,
        metric_name: str,
        agent_raw: Dict[str, List[float]],
    ) -> Optional[float]:
        """Compute a single metric from raw counts/values."""
        if metric_name == "precision":
            tp = sum(agent_raw.get("true_positive", []))
            fp = sum(agent_raw.get("false_positive", []))
            return tp / (tp + fp) if (tp + fp) > 0 else None

        if metric_name == "recall":
            tp = sum(agent_raw.get("true_positive", []))
            fn = sum(agent_raw.get("false_negative", []))
            return tp / (tp + fn) if (tp + fn) > 0 else None

        if metric_name == "false_positive_rate":
            fp = sum(agent_raw.get("false_positive", []))
            total = sum(agent_raw.get("true_positive", [])) + fp
            return fp / total if total > 0 else None

        if metric_name == "time_to_detection_hours":
            vals = agent_raw.get("time_to_detection_hours", [])
            return sum(vals) / len(vals) if vals else None

        if metric_name == "acceptance_rate":
            vals = agent_raw.get("accepted", [])
            return sum(vals) / len(vals) if vals else None

        if metric_name == "edit_rate":
            vals = agent_raw.get("edited", [])
            return sum(vals) / len(vals) if vals else None

        if metric_name == "report_latency_seconds":
            vals = agent_raw.get("report_latency_seconds", [])
            return sum(vals) / len(vals) if vals else None

        return None

    async def check_targets(self) -> bool:
        """Return True if all targets are met."""
        report = await self.get_report()
        for agent_metrics in report.values():
            for metric_data in agent_metrics.values():
                if metric_data.get("status") == "FAIL":
                    return False
        return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_report(report: Dict[str, Any]) -> None:
    print("\n" + "=" * 70)
    print(f"{'AGENT EVALUATION METRICS REPORT':^70}")
    print("=" * 70)
    fmt = "{:<35} {:>10} {:>10} {:>8}"
    print(fmt.format("Metric", "Current", "Target", "Status"))
    print("-" * 70)
    for agent, metrics in report.items():
        for metric_name, data in metrics.items():
            current = data["current"]
            target = data["target"]
            status = data["status"]
            current_str = f"{current:.3f}" if current is not None else "N/A"
            label = f"{agent}.{metric_name}"
            print(fmt.format(label, current_str, f"{target:.3f}", status))
    print("=" * 70 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent evaluation metrics report")
    parser.add_argument("--report", action="store_true", help="Print metrics report")
    parser.add_argument("--db-path", default=None, help="SQLite DB path")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    tracker = MetricsTracker(db_path=args.db_path)

    async def _run():
        await tracker.initialize()
        if args.report:
            report = await tracker.get_report()
            _print_report(report)
            all_met = await tracker.check_targets()
            if not all_met:
                sys.exit(1)

    asyncio.run(_run())


if __name__ == "__main__":
    main()

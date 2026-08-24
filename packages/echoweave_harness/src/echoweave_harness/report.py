from __future__ import annotations

import argparse
import json
from pathlib import Path

from echoweave_harness.audit import read_audit_events
from echoweave_harness.feedback import suggest_harness_improvements, write_feedback_backlog
from echoweave_harness.metrics import compute_harness_metrics


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate EchoWeave harness metrics from audit JSONL.")
    parser.add_argument("--audit-log", required=True, help="Path to audit.jsonl")
    parser.add_argument("--feedback-log", help="Optional JSONL backlog path for generated harness feedback")
    args = parser.parse_args()

    audit_log = Path(args.audit_log)
    events = read_audit_events(audit_log)
    metrics = compute_harness_metrics(events)
    suggestions = suggest_harness_improvements(events)
    feedback_written = (
        write_feedback_backlog(args.feedback_log, suggestions, source_audit_log=str(audit_log))
        if args.feedback_log
        else 0
    )
    print(
        json.dumps(
            {
                "metrics": metrics.to_dict(),
                "suggestions": [item.to_dict() for item in suggestions],
                "feedback_written": feedback_written,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

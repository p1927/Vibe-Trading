"""Subprocess entry for external-predictions refresh jobs (survives API hot-reload)."""

from __future__ import annotations

import sys


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python -m src.trade.external_predictions_run_worker <job_id>", file=sys.stderr)
        return 2
    job_id = sys.argv[1]
    from src.trade.external_predictions_run_jobs import run_worker

    run_worker(job_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

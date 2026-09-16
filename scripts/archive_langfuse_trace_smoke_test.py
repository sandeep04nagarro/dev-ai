"""One-trace dry run of the archival pipeline, meant to be run by hand on a new
environment (e.g. EC2) before trusting archive_langfuse_traces.py against
everything. Archives only the single most recent (topmost in the trace list)
un-archived trace, prints the S3 key so you can check it landed correctly, and
asks for confirmation before pruning ClickHouse - the one irreversible step.

Usage:
    python3 scripts/archive_langfuse_trace_smoke_test.py         # asks for confirmation
    python3 scripts/archive_langfuse_trace_smoke_test.py --yes   # skips the prompt
"""

import argparse
import asyncio
import logging

from archive_langfuse_traces import (
    S3_BUCKET,
    archive_trace_to_s3,
    ensure_bucket,
    fetch_unarchived_traces,
    get_s3_client,
    prune_traces,
)

logger = logging.getLogger(__name__)


async def run_smoke_test(auto_confirm: bool):
    s3 = get_s3_client()
    ensure_bucket(s3)

    traces = await fetch_unarchived_traces(limit=1, order_by="timestamp DESC")
    if not traces:
        logger.info("No un-archived traces found - nothing to test.")
        return

    trace = traces[0]
    logger.info(
        "Topmost un-archived trace: id=%s name=%r timestamp=%s project_id=%s",
        trace["id"],
        trace.get("name"),
        trace.get("timestamp"),
        trace["project_id"],
    )

    s3_key = await archive_trace_to_s3(s3, trace)
    logger.info(
        "Archived to s3://%s/%s - verify it landed with:\n"
        "  aws s3 cp s3://%s/%s -\n"
        "or open the object in the S3 console before continuing.",
        S3_BUCKET,
        s3_key,
        S3_BUCKET,
        s3_key,
    )

    if not auto_confirm:
        answer = input(
            f"\nAbout to prune trace {trace['id']} from ClickHouse "
            "(irreversible there - the S3 copy above is the only backup). Continue? [y/N] "
        )
        if answer.strip().lower() not in ("y", "yes"):
            logger.info("Aborted - ClickHouse was not modified. The S3 archive above still exists.")
            return

    await prune_traces([trace["id"]])
    logger.info(
        "Done. Open this trace in the Langfuse UI now and confirm input/output, "
        "latency, tokens, and the tool-call detail panel all still render correctly "
        "before running archive_langfuse_traces.py against everything."
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes", "-y", action="store_true", help="Skip the confirmation prompt before pruning."
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_smoke_test(auto_confirm=args.yes))

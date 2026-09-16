import asyncio
import json
import logging
from datetime import UTC, datetime

import httpx
from botocore.exceptions import ClientError

import agent.bootstrap  # noqa: F401  — loads .env before any SecretsManager/os.environ read below
from agent.utils.s3_client import S3_BUCKET, S3_ENDPOINT, get_s3_client
from agent.utils.secrets import SecretsManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CLICKHOUSE_URL = SecretsManager.get("CLICKHOUSE_URL", "http://localhost:8123")
CLICKHOUSE_USER = SecretsManager.get("CLICKHOUSE_USER", "clickhouse")
CLICKHOUSE_PASSWORD = SecretsManager.get("CLICKHOUSE_PASSWORD", "clickhouse")
BATCH_SIZE = 100


async def run_ch_query(query: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            CLICKHOUSE_URL,
            params={"user": CLICKHOUSE_USER, "password": CLICKHOUSE_PASSWORD},
            data=f"{query} FORMAT JSON",
        )
        resp.raise_for_status()
        return resp.json()


async def run_ch_mutation(query: str):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            CLICKHOUSE_URL,
            params={"user": CLICKHOUSE_USER, "password": CLICKHOUSE_PASSWORD},
            data=query,
        )
        resp.raise_for_status()


def parse_json_fields(obj):
    if isinstance(obj, list):
        return [parse_json_fields(v) for v in obj]
    elif isinstance(obj, dict):
        new_obj = {}
        for k, v in obj.items():
            if k in (
                "input",
                "output",
                "tool_calls",
                "tool_call_names",
                "model_parameters",
            ) and isinstance(v, str):
                try:
                    new_obj[k] = json.loads(v)
                except Exception:
                    new_obj[k] = v
            elif k == "metadata" and isinstance(v, dict):
                new_meta = {}
                for mk, mv in v.items():
                    if isinstance(mv, str) and (mv.startswith("{") or mv.startswith("[")):
                        try:
                            new_meta[mk] = json.loads(mv)
                        except Exception:
                            new_meta[mk] = mv
                    else:
                        new_meta[mk] = mv
                new_obj[k] = new_meta
            else:
                new_obj[k] = parse_json_fields(v)
        return new_obj
    return obj


def ensure_bucket(s3):
    try:
        s3.head_bucket(Bucket=S3_BUCKET)
        return
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")

    if S3_ENDPOINT:
        # Local/dev against MinIO - fine to auto-create.
        logger.info(f"Creating bucket {S3_BUCKET}")
        s3.create_bucket(Bucket=S3_BUCKET)
        return

    # Real AWS S3: HeadBucket is gated by s3:ListBucket, a *bucket-level*
    # permission distinct from the s3:GetObject/s3:PutObject *object-level*
    # ones an IAM policy can grant scoped to just this app's prefix without
    # ListBucket. So a 403 here doesn't necessarily mean archiving will fail -
    # skip the check and let the real put_object/get_object calls succeed or
    # fail on their own, rather than hard-blocking on a permission that isn't
    # actually required for the archive/read round trip to work.
    logger.warning(
        "HeadBucket check failed (%s) - continuing anyway. This only needs "
        "s3:ListBucket, not s3:GetObject/s3:PutObject, so it can 403 even when "
        "the actual archive read/write below will succeed.",
        error_code,
    )


async def fetch_unarchived_traces(limit: int, order_by: str | None = None) -> list[dict]:
    query = f"""
        SELECT *
        FROM traces
        WHERE not mapContains(metadata, '_langfuse_archived_to_s3')
        {f"ORDER BY {order_by}" if order_by else ""}
        LIMIT {limit}
    """
    res = await run_ch_query(query)
    return res.get("data", [])


async def archive_trace_to_s3(s3, trace: dict) -> str:
    """Archives one trace (plus its observations and scores) to S3. Does not
    prune ClickHouse - call prune_traces() separately once you've verified
    the archive. Returns the S3 key it wrote to."""
    trace_id = trace["id"]
    project_id = trace["project_id"]

    obs_res = await run_ch_query(
        f"SELECT * FROM observations WHERE trace_id = '{trace_id}' AND project_id = '{project_id}'"
    )
    observations = obs_res.get("data", [])

    score_res = await run_ch_query(
        f"SELECT * FROM scores WHERE trace_id = '{trace_id}' AND project_id = '{project_id}'"
    )
    scores = score_res.get("data", [])

    payload = {
        "traceId": trace_id,
        "projectId": project_id,
        "archivedAt": datetime.now(UTC).isoformat(),
        "trace": parse_json_fields(trace),
        "observations": parse_json_fields(observations),
        "scores": parse_json_fields(scores),
    }

    s3_key = f"archived-traces/{project_id}/{trace_id}.json"
    s3.put_object(
        Bucket=S3_BUCKET, Key=s3_key, Body=json.dumps(payload), ContentType="application/json"
    )
    logger.info(f"Archived trace {trace_id} to S3 ({s3_key})")
    return s3_key


async def prune_traces(trace_ids: list[str]):
    if not trace_ids:
        return
    formatted_ids = ",".join([f"'{tid}'" for tid in trace_ids])

    logger.info("Pruning traces from ClickHouse...")
    await run_ch_mutation(f"""
        ALTER TABLE traces UPDATE
            input = '',
            output = '',
            metadata = mapUpdate(metadata, map('_langfuse_archived_to_s3', 'true'))
        WHERE id IN ({formatted_ids})
    """)

    await run_ch_mutation(f"""
        ALTER TABLE observations DELETE WHERE trace_id IN ({formatted_ids})
    """)

    logger.info(f"Successfully pruned {len(trace_ids)} trace(s).")


async def archive_traces():
    logger.info("Starting archival process...")
    s3 = get_s3_client()
    ensure_bucket(s3)

    traces = await fetch_unarchived_traces(BATCH_SIZE)
    if not traces:
        logger.info("No traces found to archive.")
        return

    archived_ids = []
    for trace in traces:
        await archive_trace_to_s3(s3, trace)
        archived_ids.append(trace["id"])

    await prune_traces(archived_ids)


if __name__ == "__main__":
    asyncio.run(archive_traces())

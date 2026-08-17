import asyncio
import json
import logging
from datetime import UTC, datetime

import boto3
import httpx
from botocore.client import Config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CLICKHOUSE_URL = "http://localhost:8123"
CLICKHOUSE_USER = "clickhouse"
CLICKHOUSE_PASSWORD = "clickhouse"
S3_ENDPOINT = "http://localhost:9090"
S3_ACCESS_KEY = "minio"
S3_SECRET_KEY = "miniosecret"
S3_BUCKET = "langfuse"
BATCH_SIZE = 100


def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name="auto",
        config=Config(s3={"addressing_style": "path"}),
    )


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


async def archive_traces():
    logger.info("Starting archival process...")
    s3 = get_s3_client()

    try:
        s3.head_bucket(Bucket=S3_BUCKET)
    except Exception:
        logger.info(f"Creating bucket {S3_BUCKET}")
        s3.create_bucket(Bucket=S3_BUCKET)

    query = f"""
        SELECT *
        FROM traces
        WHERE not mapContains(metadata, '_langfuse_archived_to_s3')
        LIMIT {BATCH_SIZE}
    """
    res = await run_ch_query(query)
    traces = res.get("data", [])

    if not traces:
        logger.info("No traces found to archive.")
        return

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

    archived_ids = []

    for trace in traces:
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

        archived_ids.append(trace_id)
        logger.info(f"Archived trace {trace_id} to S3")

    if archived_ids:
        formatted_ids = ",".join([f"'{tid}'" for tid in archived_ids])

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

        logger.info(f"Successfully archived and pruned {len(archived_ids)} traces.")


if __name__ == "__main__":
    asyncio.run(archive_traces())

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any

import httpx
from botocore.exceptions import ClientError
from fastapi import APIRouter, Request, Response

from agent.utils.s3_client import S3_BUCKET, get_s3_client
from agent.utils.secrets import SecretsManager

router = APIRouter()
logger = logging.getLogger(__name__)

# Deliberately NOT the same as LANGFUSE_BASE_URL (agent/utils/tracing.py) - that
# one is the *public* URL the SDK uses to send trace events, e.g. the
# internet-facing domain behind nginx. This one is a server-to-server hop that
# must reach the langfuse-web container directly (its internal Docker network
# address/port), never the public domain - nginx routes all /api/trpc/* here,
# so pointing this at the public URL creates a self-referencing loop.
LANGFUSE_BACKEND_URL = SecretsManager.get("LANGFUSE_BACKEND_URL", "http://localhost:3000")

ISO_DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?$")
CH_DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(\.\d+)?$")


class TraceNotArchived(Exception):
    """Raised when the requested trace/observation isn't in the S3 archive."""


def _get_archived_object_sync(s3_key: str) -> dict:
    try:
        obj = get_s3_client().get_object(Bucket=S3_BUCKET, Key=s3_key)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            raise TraceNotArchived(s3_key) from e
        raise
    return json.loads(obj["Body"].read())


async def fetch_archived_payload(s3_key: str) -> dict:
    """Fetches an archived trace's JSON payload from S3 using the process's
    IAM role (or explicit MinIO keys locally) - never a public/unauthenticated
    URL. Raises TraceNotArchived if s3_key doesn't exist."""
    return await asyncio.to_thread(_get_archived_object_sync, s3_key)


def format_and_check_date(obj: Any):
    if isinstance(obj, str):
        if ISO_DATE_REGEX.match(obj):
            return True, obj
        if CH_DATE_REGEX.match(obj):
            iso = obj.replace(" ", "T") + "Z"
            return True, iso
    return False, obj


def parse_ch_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.replace("T", " ").rstrip("Z")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return None


def reduce_usage_or_cost_details(details: Any):
    """Mirrors reduceUsageOrCostDetails in packages/shared/src/server/repositories/observations_converters.ts"""
    if not isinstance(details, dict):
        details = {}
    input_val = None
    output_val = None
    for k, v in details.items():
        try:
            num = float(v)
        except (TypeError, ValueError):
            continue
        if k.startswith("input"):
            input_val = (input_val or 0) + num
        elif k.startswith("output"):
            output_val = (output_val or 0) + num
    try:
        total_val = float(details.get("total", 0) or 0)
    except (TypeError, ValueError):
        total_val = 0.0
    return input_val, output_val, total_val


def add_observation_derived_fields(obs: dict[str, Any]) -> dict[str, Any]:
    """Injects the computed fields (usage/cost aggregates, latency, timeToFirstToken)
    that Langfuse's ClickHouse->domain converter derives at read time rather than
    storing as raw columns. Mutates and returns obs (still snake_case)."""
    in_usage, out_usage, total_usage = reduce_usage_or_cost_details(obs.get("usage_details"))
    in_cost, out_cost, total_cost = reduce_usage_or_cost_details(obs.get("cost_details"))

    obs["input_usage"] = in_usage if in_usage is not None else 0
    obs["output_usage"] = out_usage if out_usage is not None else 0
    obs["total_usage"] = total_usage
    obs["input_cost"] = in_cost
    obs["output_cost"] = out_cost
    obs["total_cost"] = total_cost

    start_time = parse_ch_datetime(obs.get("start_time"))
    end_time = parse_ch_datetime(obs.get("end_time"))
    completion_start_time = parse_ch_datetime(obs.get("completion_start_time"))

    obs["latency"] = (end_time - start_time).total_seconds() if start_time and end_time else None
    obs["time_to_first_token"] = (
        (completion_start_time - start_time).total_seconds()
        if start_time and completion_start_time
        else None
    )
    return obs


def build_agent_graph_data(
    observations: list[dict[str, Any]], min_start: datetime | None, max_start: datetime | None
) -> list[dict[str, Any]]:
    """Mirrors traces.getAgentGraphData (web/src/server/api/routers/traces.ts:610-679)
    and getAgentGraphData (packages/shared/src/server/repositories/traces.ts:1487-1523).
    Node/step come from the langgraph_node/langgraph_step metadata keys, which the
    archiver preserves verbatim on each observation."""
    result = []
    for o in observations:
        start_time = parse_ch_datetime(o.get("start_time"))
        if start_time is None:
            continue
        if min_start and start_time < min_start:
            continue
        if max_start and start_time > max_start:
            continue

        metadata = o.get("metadata") or {}
        node = metadata.get("langgraph_node")
        step_raw = metadata.get("langgraph_step")
        try:
            step = int(step_raw) if step_raw is not None else None
        except (TypeError, ValueError):
            step = None
        obs_type = o.get("type")

        if node is not None and step is not None:
            result.append(
                {
                    "id": o.get("id"),
                    "node": node,
                    "step": step,
                    "parentObservationId": o.get("parent_observation_id"),
                    "name": o.get("name"),
                    "startTime": o.get("start_time"),
                    "endTime": o.get("end_time"),
                    "observationType": obs_type,
                }
            )
        elif obs_type != "EVENT":
            result.append(
                {
                    "id": o.get("id"),
                    "node": o.get("name"),
                    "step": 0,
                    "parentObservationId": o.get("parent_observation_id"),
                    "name": o.get("name"),
                    "startTime": o.get("start_time"),
                    "endTime": o.get("end_time"),
                    "observationType": obs_type,
                }
            )
    return result


# Fields the real traces.metrics procedure sends as BigInt / Decimal.js
# (TracesMetricsUiReturnType in traces-ui-table-service.ts:65-84). The client's
# superjson instance has a custom "decimal.js" transformer registered
# (web/src/utils/superjson.ts) alongside the built-in "bigint" transform - the
# frontend calls e.g. `cost.toNumber()` directly, so these must be tagged or
# the table cell throws on a plain number.
METRICS_BIGINT_FIELDS = {
    "promptTokens",
    "completionTokens",
    "totalTokens",
    "observationCount",
    "errorCount",
    "warningCount",
    "defaultCount",
    "debugCount",
}
METRICS_DECIMAL_FIELDS = {"calculatedTotalCost", "calculatedInputCost", "calculatedOutputCost"}


def sum_numeric_maps(maps: list[Any]) -> dict[str, float]:
    """Mirrors ClickHouse's sumMap(): key-wise sum across a list of maps."""
    total: dict[str, float] = {}
    for m in maps:
        if not isinstance(m, dict):
            continue
        for k, v in m.items():
            try:
                num = float(v)
            except (TypeError, ValueError):
                continue
            total[k] = total.get(k, 0.0) + num
    return total


def build_trace_metrics_row(
    trace: dict[str, Any], observations: list[dict[str, Any]]
) -> dict[str, Any]:
    """Mirrors the traces.metrics SQL + convertToUITableMetrics
    (packages/shared/src/server/services/traces-ui-table-service.ts:280-377, 105-145)."""
    level_counts = {"ERROR": 0, "WARNING": 0, "DEFAULT": 0, "DEBUG": 0}
    for o in observations:
        lvl = (o.get("level") or "DEFAULT").upper()
        if lvl in level_counts:
            level_counts[lvl] += 1
    # multiIf priority: ERROR > WARNING > DEFAULT > DEBUG (fallback)
    if level_counts["ERROR"]:
        level = "ERROR"
    elif level_counts["WARNING"]:
        level = "WARNING"
    elif level_counts["DEFAULT"]:
        level = "DEFAULT"
    else:
        level = "DEBUG"

    usage_details = sum_numeric_maps([o.get("usage_details") for o in observations])
    cost_details = sum_numeric_maps([o.get("cost_details") for o in observations])
    in_usage, out_usage, total_usage = reduce_usage_or_cost_details(usage_details)

    return {
        "id": trace.get("id"),
        "project_id": trace.get("project_id"),
        "latency": compute_trace_latency(observations),
        "prompt_tokens": in_usage if in_usage is not None else 0,
        "completion_tokens": out_usage if out_usage is not None else 0,
        "total_tokens": total_usage,
        "usage_details": usage_details,
        "cost_details": cost_details,
        "observation_count": len(observations),
        "calculated_total_cost": cost_details.get("total") or None,
        "calculated_input_cost": cost_details.get("input") or None,
        "calculated_output_cost": cost_details.get("output") or None,
        "level": level,
        "debug_count": level_counts["DEBUG"],
        "warning_count": level_counts["WARNING"],
        "error_count": level_counts["ERROR"],
        "default_count": level_counts["DEFAULT"],
        "scores": {},
    }


def compute_trace_latency(observations: list[dict[str, Any]]) -> float | None:
    """Mirrors the latency computation in traces.byIdWithObservationsAndScores
    (web/src/server/api/routers/traces.ts)."""
    start_times = sorted(
        t for t in (parse_ch_datetime(o.get("start_time")) for o in observations) if t
    )
    end_times = sorted(t for t in (parse_ch_datetime(o.get("end_time")) for o in observations) if t)
    if not start_times:
        return None
    if end_times:
        return (end_times[-1] - start_times[0]).total_seconds()
    if len(start_times) > 1:
        return (start_times[-1] - start_times[0]).total_seconds()
    return None


def to_camel_case(snake_str):
    components = snake_str.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


def convert_keys_to_camel(obj):
    if isinstance(obj, list):
        return [convert_keys_to_camel(v) for v in obj]
    elif isinstance(obj, dict):
        new_obj = {}
        for k, v in obj.items():
            new_key = to_camel_case(k)

            if k in (
                "metadata",
                "tags",
                "input",
                "output",
                "model_parameters",
                "usage_details",
                "provided_usage_details",
                "cost_details",
                "provided_cost_details",
                "tool_definitions",
                "tool_calls",
                "tool_call_names",
            ):
                new_obj[new_key] = v
            elif new_key == "isDeleted" and isinstance(v, int):
                new_obj[new_key] = bool(v)
            else:
                new_obj[new_key] = convert_keys_to_camel(v)
        return new_obj
    return obj


def build_superjson(data: Any) -> dict[str, Any]:
    meta_values = {}
    dummy = {"root": data}

    queue = [(data, [], dummy, "root")]

    # Known date fields in Langfuse TRPC schema
    date_fields = {
        "timestamp",
        "createdAt",
        "updatedAt",
        "eventTs",
        "startTime",
        "endTime",
        "completionStartTime",
    }

    while queue:
        obj, path, parent, key = queue.pop()

        if isinstance(obj, dict):
            for k, v in obj.items():
                queue.append((v, path + [k], obj, k))
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                queue.append((v, path + [str(i)], obj, i))
        elif isinstance(obj, str):
            # Only convert strings to Date if they are in the known date_fields
            if key in date_fields:
                is_date, new_val = format_and_check_date(obj)
                if is_date:
                    parent[key] = new_val
                    meta_values[".".join(path)] = ["Date"]

    res = {"json": dummy["root"]}
    if meta_values:
        res["meta"] = {"values": meta_values}
    return res


@router.api_route("/api/trpc/{operations}", methods=["GET", "POST"])
async def proxy_trpc(operations: str, request: Request):
    # 1. Forward request to real Langfuse backend FIRST
    async with httpx.AsyncClient() as client:
        target_url = f"{LANGFUSE_BACKEND_URL}/api/trpc/{operations}?{request.url.query}"
        headers = dict(request.headers)
        headers.pop("host", None)
        headers.pop("content-length", None)

        try:
            if request.method == "POST":
                body = await request.body()
                resp = await client.post(target_url, headers=headers, content=body)
            else:
                resp = await client.get(target_url, headers=headers)
        except Exception as e:
            logger.error(f"Failed to proxy to Langfuse: {e}")
            return Response(status_code=502, content="Bad Gateway")

    # 2. Check if we need to augment the response with S3 data
    intercept_ops = [
        "traces.byId",
        "traces.byIdWithObservationsAndScores",
        "observations.byId",
        "traces.getAgentGraphData",
        "traces.metrics",
    ]
    op_list = operations.split(",")

    # Note: no status-code gate here. A pruned observation makes the real backend
    # throw NOT_FOUND (non-2xx) for observations.byId, and we still need to try
    # serving it from the S3 archive in that case, not just on 200/207.
    if any(op in intercept_ops for op in op_list):
        try:
            res_json = resp.json()
            input_str = request.query_params.get("input")

            if input_str:
                inputs = json.loads(input_str)
                is_batch = isinstance(res_json, list)
                augmented_any = False

                for i in range(len(op_list)):
                    op = op_list[i]
                    if op not in intercept_ops:
                        continue

                    # Extract the input payload for this operation
                    if is_batch:
                        idx = str(i)
                        trpc_json = inputs.get(idx, {}).get("json", {})
                    else:
                        trpc_json = inputs.get("json", {})

                    if op == "traces.metrics":
                        # traceIds is a batch of rows, some archived and some
                        # still live - patch only the archived rows in place
                        # and leave the real (already-correct) rows for live
                        # traces, including their existing bigint/decimal.js
                        # wire encoding, untouched.
                        metrics_project_id = trpc_json.get("projectId")
                        trace_ids = trpc_json.get("traceIds") or []
                        if not metrics_project_id or not trace_ids:
                            continue
                        try:
                            current_item = res_json[i] if is_batch else res_json
                            data_container = current_item["result"]["data"]
                            rows = data_container.get("json") or []
                            meta_values = dict(
                                data_container.get("meta", {}).get("values", {}) or {}
                            )
                        except (KeyError, TypeError):
                            continue

                        patched = False
                        for row_idx, row in enumerate(rows):
                            row_trace_id = row.get("id") if isinstance(row, dict) else None
                            if not row_trace_id:
                                continue
                            s3_key = f"archived-traces/{metrics_project_id}/{row_trace_id}.json"
                            try:
                                archived_payload = await fetch_archived_payload(s3_key)
                            except TraceNotArchived:
                                continue  # not archived - leave this row as the real backend returned it

                            metrics_row = build_trace_metrics_row(
                                archived_payload["trace"], archived_payload.get("observations", [])
                            )
                            camel_row = convert_keys_to_camel(metrics_row)
                            rows[row_idx] = camel_row

                            prefix = f"{row_idx}."
                            for key in list(meta_values.keys()):
                                if key.startswith(prefix):
                                    del meta_values[key]
                            for field in METRICS_BIGINT_FIELDS:
                                if field in camel_row:
                                    meta_values[f"{row_idx}.{field}"] = ["bigint"]
                            for field in METRICS_DECIMAL_FIELDS:
                                if camel_row.get(field) is not None:
                                    meta_values[f"{row_idx}.{field}"] = ["decimal.js"]
                            patched = True

                        if patched:
                            data_container["json"] = rows
                            if meta_values:
                                data_container["meta"] = {"values": meta_values}
                            elif "meta" in data_container:
                                del data_container["meta"]
                            augmented_any = True
                            logger.info(
                                f"Patched traces.metrics rows for archived traces among {trace_ids}"
                            )
                        continue

                    trace_id = trpc_json.get("traceId")
                    project_id = trpc_json.get("projectId")
                    observation_id = trpc_json.get("observationId")

                    if trace_id and project_id:
                        s3_key = f"archived-traces/{project_id}/{trace_id}.json"
                        try:
                            payload = await fetch_archived_payload(s3_key)

                            if op == "traces.getAgentGraphData":
                                # AgentGraphDataSchema wants plain strings for
                                # start/end time, not Date-tagged superjson values -
                                # build the envelope directly, skip convert_keys_to_camel
                                # / build_superjson (those would wrongly Date-tag them).
                                min_start = parse_ch_datetime(trpc_json.get("minStartTime"))
                                max_start = parse_ch_datetime(trpc_json.get("maxStartTime"))
                                graph_data = build_agent_graph_data(
                                    payload.get("observations", []), min_start, max_start
                                )
                                augmented_result = {"result": {"data": {"json": graph_data}}}
                            else:
                                if op == "traces.byId":
                                    data = payload["trace"]
                                elif op == "traces.byIdWithObservationsAndScores":
                                    observations = [
                                        add_observation_derived_fields(o)
                                        for o in payload.get("observations", [])
                                    ]
                                    all_scores = payload.get("scores", [])
                                    # Real router partitions scores by dataType === "CORRECTION"
                                    # (web/src/server/api/routers/traces.ts:405-408) and always
                                    # returns both fields - `corrections` is unconditionally
                                    # dereferenced (.filter(...)) on the client, so omitting it
                                    # entirely crashes the trace detail view.
                                    corrections = [
                                        s for s in all_scores if s.get("data_type") == "CORRECTION"
                                    ]
                                    scores = [
                                        s for s in all_scores if s.get("data_type") != "CORRECTION"
                                    ]
                                    data = payload["trace"]
                                    data["observations"] = observations
                                    data["scores"] = scores
                                    data["corrections"] = corrections
                                    data["latency"] = compute_trace_latency(observations)
                                else:  # observations.byId
                                    match = next(
                                        (
                                            o
                                            for o in payload.get("observations", [])
                                            if o.get("id") == observation_id
                                        ),
                                        None,
                                    )
                                    if match is None:
                                        raise TraceNotArchived(observation_id)
                                    data = add_observation_derived_fields(dict(match))
                                    data["internal_model"] = data.get("internal_model_id")

                                # Augment the Langfuse response
                                camel_data = convert_keys_to_camel(data)
                                augmented_result = {"result": {"data": build_superjson(camel_data)}}

                            if is_batch:
                                res_json[i] = augmented_result
                            else:
                                res_json = augmented_result
                            augmented_any = True

                            logger.info(f"Intercepted and fetched {op} for {trace_id} from S3")
                        except TraceNotArchived:
                            # Not in S3 (or observation not in the archive) - leave
                            # Langfuse's original response/error for this op untouched.
                            pass

                if augmented_any:
                    headers_out = dict(resp.headers)
                    headers_out.pop("content-length", None)
                    headers_out.pop("content-encoding", None)
                    headers_out.pop("transfer-encoding", None)
                    return Response(
                        content=json.dumps(res_json),
                        status_code=200,
                        media_type="application/json",
                        headers=headers_out,
                    )
        except Exception:
            logger.exception("Error augmenting tRPC response")

    # Return original response
    headers_out = dict(resp.headers)
    headers_out.pop("content-length", None)
    headers_out.pop("content-encoding", None)
    headers_out.pop("transfer-encoding", None)
    return Response(content=resp.content, status_code=resp.status_code, headers=headers_out)

"""
Lambda handler for DynamoDB Streams on my5-sim-jobs (AWS path only).

Triggered on every MODIFY/INSERT to my5-sim-jobs. For each record:
  1. Deserialize NewImage from DynamoDB JSON → plain Python dict
  2. Build the WS message via job_record_to_message (shared emitter — same as local)
  3. Fan-out to all watchers via push_progress(registry=DynamoDBRegistry, sender=ApiGwSender)

This is the AWS analog of the NotifyingJobStore→EventBus fan-out path used locally.
The push_progress core is byte-identical; only the registry and sender differ.

handle_job on AWS gets a plain JobStore (not NotifyingJobStore). The Streams trigger
fires automatically after every update_item on my5-sim-jobs. handle_job never knows
the fan-out exists — the seam is at the DynamoDB layer, not the worker layer.

APIGW_ENDPOINT env var: "https://{api_id}.execute-api.{region}.amazonaws.com/{stage}"
Set by Terraform on the Lambda function's environment block.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from boto3.dynamodb.types import TypeDeserializer

from my5.ws.aws.apigw_sender import ApiGwSender
from my5.ws.aws.connections_table import DynamoDBRegistry
from my5.ws.emit import job_record_to_message
from my5.ws.push import push_progress

_deserializer = TypeDeserializer()


def _deserialize_image(image: dict[str, Any]) -> dict[str, Any]:
    """
    Convert a DynamoDB Streams NewImage (DynamoDB JSON wire format) to a plain dict.

    DynamoDB Streams: {"field": {"S": "value"}, "num": {"N": "42"}, ...}
    TypeDeserializer output: {"field": "value", "num": Decimal("42"), ...}
    job_record_to_message handles Decimal via int()/float() coercions.
    """
    return {k: _deserializer.deserialize(v) for k, v in image.items()}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """
    Lambda entrypoint for DynamoDB Streams trigger.

    Processes a batch of stream records (typically 1–10 per invocation).
    Skips REMOVE events (job deletion); processes INSERT and MODIFY.
    """
    apigw_endpoint = os.environ["APIGW_ENDPOINT"]
    registry = DynamoDBRegistry()
    sender = ApiGwSender(endpoint_url=apigw_endpoint)

    async def _fan_out_all() -> None:
        for record in event.get("Records", []):
            if record.get("eventName") not in ("INSERT", "MODIFY"):
                continue
            new_image = record.get("dynamodb", {}).get("NewImage")
            if not new_image:
                continue
            job = _deserialize_image(new_image)
            job_id = job.get("job_id")
            if not job_id:
                continue
            message = job_record_to_message(job)
            await push_progress(str(job_id), message, registry, sender)

    asyncio.run(_fan_out_all())
    return {"statusCode": 200, "body": "OK"}

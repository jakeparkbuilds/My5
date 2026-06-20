"""
MY5_ENV-based configuration: one variable flips all endpoints between local emulators
and real AWS. Nothing else in the codebase checks MY5_ENV — they call the factories here.

  MY5_ENV=local  (default)
    DynamoDB → http://localhost:8000  (amazon/dynamodb-local Docker container)
    SQS      → http://localhost:9324  (softwaremill/elasticmq-native Docker container)
    Creds    → dummy "test"/"test" (required by both local emulators)

  MY5_ENV=aws
    DynamoDB → standard AWS endpoint (credentials from ~/.aws/credentials or IAM role)
    SQS      → real SQS; URLs read from MY5_SQS_QUEUE_URL / MY5_SQS_DLQ_URL env vars
    Creds    → normal AWS credential chain

To run a worker locally:   MY5_ENV=local  python -m my5.job_worker
To run a worker on AWS:    MY5_ENV=aws MY5_SQS_QUEUE_URL=https://... python -m my5.job_worker

Same code, different endpoints. This is the only difference between local and AWS.
"""
from __future__ import annotations

import os
from typing import Any

import boto3

_ENV: str = os.getenv("MY5_ENV", "local")
USE_LOCAL: bool = _ENV == "local"

_REGION = "us-east-1"
_LOCAL_CREDS: dict[str, str] = {
    "aws_access_key_id": "test",
    "aws_secret_access_key": "test",
}
_DYNAMO_LOCAL_URL = "http://localhost:8000"
_SQS_LOCAL_URL = "http://localhost:9324"
# ElasticMQ uses this fixed fake account ID in all ARNs and queue URLs.
_ELASTICMQ_ACCOUNT = "000000000000"

# Queue URLs — read once at import time.
# The local URLs match what ElasticMQ returns from CreateQueue.
SQS_QUEUE_URL: str = (
    f"{_SQS_LOCAL_URL}/{_ELASTICMQ_ACCOUNT}/my5-jobs"
    if USE_LOCAL else
    os.getenv("MY5_SQS_QUEUE_URL", "")
)
DLQ_URL: str = (
    f"{_SQS_LOCAL_URL}/{_ELASTICMQ_ACCOUNT}/my5-jobs-dlq"
    if USE_LOCAL else
    os.getenv("MY5_SQS_DLQ_URL", "")
)


_SQS_ENV_VARS: dict[str, str] = {
    "my5-jobs":     "MY5_SQS_QUEUE_URL",
    "my5-jobs-dlq": "MY5_SQS_DLQ_URL",
}


def get_sqs_queue_url(queue_name: str) -> str:
    """
    Return the SQS queue URL for queue_name.

    Local: constructs the ElasticMQ URL directly — no network call.
    AWS:   reads MY5_SQS_QUEUE_URL / MY5_SQS_DLQ_URL if set;
           otherwise calls GetQueueUrl by name so the account ID never
           needs to be hardcoded or passed as an argument.

    Prefer this over the module-level SQS_QUEUE_URL/DLQ_URL constants
    in code that runs under MY5_ENV=aws without env vars set.
    """
    if USE_LOCAL:
        return f"{_SQS_LOCAL_URL}/{_ELASTICMQ_ACCOUNT}/{queue_name}"
    explicit = os.getenv(_SQS_ENV_VARS.get(queue_name, ""), "")
    if explicit:
        return explicit
    client = boto3.client("sqs", region_name=_REGION)
    return client.get_queue_url(QueueName=queue_name)["QueueUrl"]


def make_sqs_client() -> Any:
    """Return a boto3 SQS low-level client for the active target."""
    kwargs: dict[str, Any] = {"region_name": _REGION}
    if USE_LOCAL:
        kwargs["endpoint_url"] = _SQS_LOCAL_URL
        kwargs.update(_LOCAL_CREDS)
    return boto3.client("sqs", **kwargs)


def make_dynamo_client() -> Any:
    """Return a boto3 DynamoDB low-level client for the active target."""
    kwargs: dict[str, Any] = {"region_name": _REGION}
    if USE_LOCAL:
        kwargs["endpoint_url"] = _DYNAMO_LOCAL_URL
        kwargs.update(_LOCAL_CREDS)
    return boto3.client("dynamodb", **kwargs)


def make_dynamo_resource() -> Any:
    """
    Return a boto3 DynamoDB ServiceResource for the active target.

    The resource API auto-serializes Python types to DynamoDB format (ints, strs,
    Decimals) and deserializes on read (numbers come back as Decimal). Use
    _dec_to_float() from job_store to convert Decimals before passing to the engine.
    """
    kwargs: dict[str, Any] = {"region_name": _REGION}
    if USE_LOCAL:
        kwargs["endpoint_url"] = _DYNAMO_LOCAL_URL
        kwargs.update(_LOCAL_CREDS)
    return boto3.resource("dynamodb", **kwargs)

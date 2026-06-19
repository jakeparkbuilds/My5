terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# When use_local=true  → amazon/dynamodb-local on localhost:8000, ElasticMQ on localhost:9324, dummy creds.
# When use_local=false → normal AWS provider (real deployment — not used yet).
provider "aws" {
  region = "us-east-1"

  # Dummy creds required by DynamoDB Local and ElasticMQ; ignored by real AWS (which reads
  # credentials from the normal chain: env vars, ~/.aws/credentials, IAM role).
  access_key = var.use_local ? "test" : null
  secret_key = var.use_local ? "test" : null

  dynamic "endpoints" {
    for_each = var.use_local ? [1] : []
    content {
      dynamodb = "http://localhost:8000"  # amazon/dynamodb-local
      sqs      = "http://localhost:9324"  # softwaremill/elasticmq-native
    }
  }

  # These skip_* flags suppress validation calls that DynamoDB Local / ElasticMQ can't answer.
  skip_credentials_validation = var.use_local
  skip_requesting_account_id  = var.use_local
  skip_metadata_api_check     = var.use_local
}

# ── Table 1: lineup metrics ───────────────────────────────────────────────────
#
# PK: lineup_key (String) = "{team_id}#{athlete_id_0}#...#{athlete_id_4}"
#     IDs are sorted numerically so any permutation of the same 5 players
#     produces an identical key — canonical form established by reconstruct.py.
#
# billing_mode = PAY_PER_REQUEST: DynamoDB on-demand mode.
#   Cost: $0 idle (no provisioned capacity), pay only per read/write unit.
#   AWS free tier covers 25GB storage + 200M requests/month — this project
#   generates ~30MB at full season scale, 3 orders of magnitude under the limit.
resource "aws_dynamodb_table" "lineup_metrics" {
  name         = "my5-lineup-metrics"
  billing_mode = "PAY_PER_REQUEST" # $0 idle — on-demand, scales to zero
  hash_key     = "lineup_key"

  attribute {
    name = "lineup_key"
    type = "S"
  }
}

# ── Table 2: per-player simulator parameters ──────────────────────────────────
#
# PK: athlete_id (Number) — ESPN's integer player ID, unique across all seasons.
# No sort key: one row per player, looked up directly by ID from the simulator.
resource "aws_dynamodb_table" "player_params" {
  name         = "my5-player-params"
  billing_mode = "PAY_PER_REQUEST" # $0 idle — on-demand, scales to zero
  hash_key     = "athlete_id"

  attribute {
    name = "athlete_id"
    type = "N"
  }
}

# ── Table 3: simulation job records ──────────────────────────────────────────
#
# PK: job_id (String) — UUID4 generated at submit time.
# Single source of truth for job lifecycle: status, inputs, result, errors.
# SQS message body carries only job_id (pointer); all state lives here.
#
# Cost: $0 idle. PAY_PER_REQUEST means you pay per read/write, not for capacity.
# At our scale (tens–thousands of jobs total), this stays within the free tier
# (200M requests/month free). TTL auto-deletes records after 7 days.
#
# TTL: DynamoDB Local accepts TTL configuration but does not auto-delete locally.
# On real AWS, expired items are deleted asynchronously. No cost difference.
resource "aws_dynamodb_table" "sim_jobs" {
  name         = "my5-sim-jobs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "job_id"

  attribute {
    name = "job_id"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

# ── SQS queues (AWS only; skipped when use_local=true) ───────────────────────
#
# LOCALLY: queues are created in ElasticMQ via `scripts/init_local_queues.py`.
# ElasticMQ is a Docker container — Terraform does not manage it. The Terraform
# AWS provider v5.x has a readiness polling loop after CreateQueue that calls
# GetQueueAttributes expecting fields ElasticMQ doesn't return, causing an
# indefinite hang. Workaround: `count = var.use_local ? 0 : 1` skips these
# resources entirely when use_local=true.
#
# ON AWS: count=1, resources created normally. Real SQS supports all attributes.
#
# Cost (AWS): $0.40 per million requests. First 1M/month free.
#   At our scale (tens–thousands of jobs total), effectively $0.
# Cost (local): ElasticMQ Docker container — no AWS cost.

resource "aws_sqs_queue" "job_dlq" {
  count                     = var.use_local ? 0 : 1
  name                      = "my5-jobs-dlq"
  message_retention_seconds = 1209600  # 14 days
}

resource "aws_sqs_queue" "job_queue" {
  count                      = var.use_local ? 0 : 1
  name                       = "my5-jobs"
  visibility_timeout_seconds = 60    # 3× engine worst-case runtime
  message_retention_seconds  = 86400 # 1 day; jobs complete in seconds

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.job_dlq[0].arn
    maxReceiveCount     = 3
  })
}

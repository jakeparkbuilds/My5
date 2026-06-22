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
data "aws_region" "current" {}

provider "aws" {
  region = "us-east-1"

  # use_local  → DynamoDB Local / ElasticMQ dummy creds
  # planning_only → fake creds for plan-only CI run (no real AWS endpoint hit)
  # neither    → normal credential chain (env vars, ~/.aws/credentials, IAM role)
  access_key = var.use_local ? "test" : (var.planning_only ? "fake" : null)
  secret_key = var.use_local ? "test" : (var.planning_only ? "fake" : null)

  dynamic "endpoints" {
    for_each = var.use_local ? [1] : []
    content {
      dynamodb = "http://localhost:8000"  # amazon/dynamodb-local
      sqs      = "http://localhost:9324"  # softwaremill/elasticmq-native
    }
  }

  # skip_* when local (DynamoDB Local can't answer these) OR planning_only (no real creds).
  skip_credentials_validation = var.use_local || var.planning_only
  skip_requesting_account_id  = var.use_local || var.planning_only
  skip_metadata_api_check     = var.use_local || var.planning_only
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

  # Streams: disabled locally (not needed, fan-out is via NotifyingJobStore),
  # enabled on AWS (triggers fanout_handler on every update_item write).
  # Cost: ~$0.02/100K stream reads — negligible at our scale.
  stream_enabled   = !var.use_local
  stream_view_type = "NEW_IMAGE"
}

# ── Table 4: simulation result cache ─────────────────────────────────────────
#
# PK: cache_key (String) — sha256 of canonical (sorted) lineup-key pair + seed.
# Symmetric: (A vs B, seed=42) and (B vs A, seed=42) map to the same key.
# Seed-in-key: non-deterministic runs (seed=None) are never cached.
#
# Cost: $0 idle (PAY_PER_REQUEST). TTL auto-deletes after 7 days.
# Enabled in BOTH local and real-AWS environments (same as the three core tables).
resource "aws_dynamodb_table" "sim_cache" {
  name         = "my5-sim-cache"
  billing_mode = "PAY_PER_REQUEST" # $0 idle — on-demand, scales to zero
  hash_key     = "cache_key"

  attribute {
    name = "cache_key"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}

# ── WebSocket connection registry (AWS only) ──────────────────────────────────
#
# Table 4: my5-ws-connections
# PK: conn_id (S) — direct register/unregister (put_item / delete_item) — O(1)
# GSI: job_id-index (PK=job_id, projection ALL) — fanout lookup (query) — O(connections/job)
#
# DynamoDBRegistry (Python) conforms to the same register/lookup/unregister interface
# as the in-memory Registry. Swap backends without touching push_progress.
#
# Cost: PAY_PER_REQUEST → $0 idle. Writes: 2 per WS session (put + delete).
#       GSI projections add no extra cost for ALL projection at our scale.
resource "aws_dynamodb_table" "ws_connections" {
  count        = var.use_local ? 0 : 1
  name         = "my5-ws-connections"
  billing_mode = "PAY_PER_REQUEST" # $0 idle — on-demand, scales to zero
  hash_key     = "conn_id"

  attribute {
    name = "conn_id"
    type = "S"
  }

  attribute {
    name = "job_id"
    type = "S"
  }

  global_secondary_index {
    name            = "job_id-index"
    hash_key        = "job_id"
    projection_type = "ALL"
  }
}

# ── Lambda IAM role + policy (shared by connect_handler and fanout_handler) ───
#
# Cost: IAM roles are $0 always.
resource "aws_iam_role" "lambda_ws" {
  count = var.use_local ? 0 : 1
  name  = "my5-lambda-ws-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_policy" "lambda_ws" {
  count = var.use_local ? 0 : 1
  name  = "my5-lambda-ws-policy"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # CloudWatch Logs — Lambda execution logs. $0.50/GB ingested; negligible.
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        # ws_connections table R/W (connect_handler register/unregister)
        # + fanout_handler GSI query (lookup by job_id)
        # $0 idle (PAY_PER_REQUEST — charged per operation only).
        Sid    = "ConnectionsTable"
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:GetItem",
        ]
        Resource = [
          aws_dynamodb_table.ws_connections[0].arn,
          "${aws_dynamodb_table.ws_connections[0].arn}/index/*",
        ]
      },
      {
        # DynamoDB Streams read on my5-sim-jobs (fanout_handler trigger).
        # $0 idle; ~$0.02/100K reads at our scale.
        Sid    = "SimJobsStream"
        Effect = "Allow"
        Action = [
          "dynamodb:GetRecords",
          "dynamodb:GetShardIterator",
          "dynamodb:DescribeStream",
          "dynamodb:ListStreams",
        ]
        Resource = "${aws_dynamodb_table.sim_jobs.arn}/stream/*"
      },
      {
        # post_to_connection — fan-out messages to WebSocket clients.
        # $0 idle; billed per connection-minute and per message (see APIGW).
        Sid      = "ApigwManageConnections"
        Effect   = "Allow"
        Action   = "execute-api:ManageConnections"
        Resource = "${aws_apigatewayv2_api.ws[0].execution_arn}/*"
      },
      {
        # X-Ray active tracing — Lambda emits segments automatically when
        # tracing_config.mode = "Active". Free tier: 100K traces/month.
        # After free tier: $5/million traces — negligible at our scale.
        Sid    = "XRayTracing"
        Effect = "Allow"
        Action = [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords",
          "xray:GetSamplingRules",
          "xray:GetSamplingTargets",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_ws" {
  count      = var.use_local ? 0 : 1
  role       = aws_iam_role.lambda_ws[0].name
  policy_arn = aws_iam_policy.lambda_ws[0].arn
}

# ── Lambda deployment package ─────────────────────────────────────────────────
#
# Zips the full src/ directory so Lambda can import my5.ws.aws.*.
# Build artifact: infra/my5_ws_lambda.zip (gitignored).
# Rebuild before `terraform apply` if source files change.
#
# Terraform reads the file at plan time to compute source_code_hash (force
# re-deployment when source changes). The file must exist before running plan.

locals {
  lambda_zip = "${path.module}/my5_ws_lambda.zip"
}

# ── Lambda functions ──────────────────────────────────────────────────────────
#
# Both Lambdas are NOT in a VPC (no NAT gateway needed → $0 idle VPC cost).
# Cost: $0 idle (scale to zero). First 1M requests/month free.
#
# connect_handler: $connect + $disconnect — DynamoDB put/delete only.
#   timeout=10s (generous; real latency <100ms). memory=128MB.
#
# fanout_handler: Streams trigger — DynamoDB query + post_to_connection per watcher.
#   timeout=30s (covers fan-out to many connections). memory=128MB (stateless).

resource "aws_lambda_function" "connect_handler" {
  count         = var.use_local ? 0 : 1
  function_name = "my5-ws-connect"
  role          = aws_iam_role.lambda_ws[0].arn

  filename         = local.lambda_zip
  source_code_hash = filebase64sha256(local.lambda_zip)

  runtime     = "python3.11"
  handler     = "my5.ws.aws.connect_handler.handler"
  timeout     = 10
  memory_size = 128

  # Active X-Ray tracing: Lambda emits trace segments automatically for each
  # invocation + AWS SDK calls (DynamoDB put/delete). No sdk instrumentation needed.
  # Cost: free for first 100K traces/month; $5/million after — $0 at our scale.
  tracing_config {
    mode = "Active"
  }
}

resource "aws_lambda_function" "fanout_handler" {
  count         = var.use_local ? 0 : 1
  function_name = "my5-ws-fanout"
  role          = aws_iam_role.lambda_ws[0].arn

  filename         = local.lambda_zip
  source_code_hash = filebase64sha256(local.lambda_zip)

  runtime = "python3.11"
  handler = "my5.ws.aws.fanout_handler.handler"
  timeout = 30
  memory_size = 128

  environment {
    variables = {
      # Set at deploy time from the APIGW stage URL.
      # Format: https://{api_id}.execute-api.{region}.amazonaws.com/{stage}
      APIGW_ENDPOINT = "https://${aws_apigatewayv2_api.ws[0].id}.execute-api.us-east-1.amazonaws.com/prod"
    }
  }

  tracing_config {
    mode = "Active"
  }
}

# ── API Gateway WebSocket API ─────────────────────────────────────────────────
#
# WebSocket API: clients connect to wss://{id}.execute-api.us-east-1.amazonaws.com/prod?job_id={uuid}
# $connect / $disconnect → connect_handler Lambda
# $default               → connect_handler Lambda (server-push only; client sends nothing useful)
#
# Cost: $1/million connection-minutes + $1/million messages. $0 idle (no connections = no charge).
# No NAT gateway; no always-on resources.

resource "aws_apigatewayv2_api" "ws" {
  count                      = var.use_local ? 0 : 1
  name                       = "my5-ws"
  protocol_type              = "WEBSOCKET"
  route_selection_expression = "$request.body.action"
}

resource "aws_apigatewayv2_integration" "connect" {
  count            = var.use_local ? 0 : 1
  api_id           = aws_apigatewayv2_api.ws[0].id
  integration_type = "AWS_PROXY"
  integration_uri  = aws_lambda_function.connect_handler[0].invoke_arn
}

resource "aws_apigatewayv2_route" "connect" {
  count     = var.use_local ? 0 : 1
  api_id    = aws_apigatewayv2_api.ws[0].id
  route_key = "$connect"
  target    = "integrations/${aws_apigatewayv2_integration.connect[0].id}"
}

resource "aws_apigatewayv2_route" "disconnect" {
  count     = var.use_local ? 0 : 1
  api_id    = aws_apigatewayv2_api.ws[0].id
  route_key = "$disconnect"
  target    = "integrations/${aws_apigatewayv2_integration.connect[0].id}"
}

resource "aws_apigatewayv2_route" "default" {
  count     = var.use_local ? 0 : 1
  api_id    = aws_apigatewayv2_api.ws[0].id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.connect[0].id}"
}

resource "aws_apigatewayv2_stage" "ws" {
  count       = var.use_local ? 0 : 1
  api_id      = aws_apigatewayv2_api.ws[0].id
  name        = "prod"
  auto_deploy = true
}

# Lambda permission — APIGW invokes connect_handler.
resource "aws_lambda_permission" "apigw_connect" {
  count         = var.use_local ? 0 : 1
  statement_id  = "AllowAPIGatewayInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.connect_handler[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.ws[0].execution_arn}/*"
}

# ── DynamoDB Streams → fanout_handler trigger ─────────────────────────────────
#
# Processes my5-sim-jobs stream in batches of up to 10 records.
# starting_position = LATEST: only process new writes (not historical backfill).
# Cost: $0 for the mapping itself; stream read cost accounted above (SimJobsStream IAM).

resource "aws_lambda_event_source_mapping" "fanout_stream" {
  count             = var.use_local ? 0 : 1
  event_source_arn  = aws_dynamodb_table.sim_jobs.stream_arn
  function_name     = aws_lambda_function.fanout_handler[0].arn
  starting_position = "LATEST"
  batch_size        = 10
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

# ── CloudWatch dashboard (AWS only) ──────────────────────────────────────────
#
# Three headline widgets for P3 observability:
#   1. Job latency p99 (cache-miss path) — custom EMF metric from job_worker.py
#   2. Cache hit vs miss count — same metric, two dimension slices
#   3. DLQ depth — native SQS metric; non-zero here means jobs are failing loudly
#
# Cost: $3/dashboard/month (AWS standard). One dashboard is negligible; billed
# only when use_local=false. Free if you delete it: `terraform destroy -target=...`.
#
# EMF metrics appear in namespace My5/Simulator ~5 minutes after first Lambda run.
# Before that, the graph widgets show "No data" — that is expected.

resource "aws_cloudwatch_dashboard" "my5" {
  count          = var.use_local ? 0 : 1
  dashboard_name = "my5-simulator"

  dashboard_body = jsonencode({
    widgets = [
      {
        type   = "metric"
        x      = 0
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "Job Latency p99 — Cache Miss Path"
          view    = "timeSeries"
          region  = data.aws_region.current.name
          period  = 300
          stat    = "p99"
          metrics = [
            ["My5/Simulator", "job_latency_ms", "env", "aws", "cache_status", "miss",
              { "stat" : "p99", "label" : "p99 latency (ms)" }
            ]
          ]
          yAxis = { left = { min = 0 } }
        }
      },
      {
        type   = "metric"
        x      = 12
        y      = 0
        width  = 12
        height = 6
        properties = {
          title   = "Cache Hit vs Miss Count (5-min window)"
          view    = "timeSeries"
          region  = data.aws_region.current.name
          period  = 300
          metrics = [
            ["My5/Simulator", "job_latency_ms", "env", "aws", "cache_status", "hit",
              { "stat" : "SampleCount", "label" : "Cache Hits" }
            ],
            ["My5/Simulator", "job_latency_ms", "env", "aws", "cache_status", "miss",
              { "stat" : "SampleCount", "label" : "Cache Misses" }
            ]
          ]
          yAxis = { left = { min = 0 } }
        }
      },
      {
        type   = "metric"
        x      = 0
        y      = 6
        width  = 12
        height = 6
        properties = {
          title   = "DLQ Depth (non-zero = loud failure)"
          view    = "timeSeries"
          region  = data.aws_region.current.name
          period  = 60
          stat    = "Maximum"
          metrics = [
            ["AWS/SQS", "ApproximateNumberOfMessagesVisible",
              "QueueName", "my5-jobs-dlq",
              { "label" : "DLQ messages" }
            ]
          ]
          yAxis = { left = { min = 0 } }
        }
      }
    ]
  })
}

# ── Outputs (AWS-only; empty when use_local=true) ─────────────────────────────

output "ws_url" {
  description = "Live WebSocket URL — connect with ?job_id=<uuid>"
  value       = var.use_local ? "" : "wss://${aws_apigatewayv2_api.ws[0].id}.execute-api.us-east-1.amazonaws.com/prod"
}

output "apigw_id" {
  description = "APIGW WebSocket API ID"
  value       = var.use_local ? "" : aws_apigatewayv2_api.ws[0].id
}

output "ws_connections_table" {
  description = "DynamoDB table name for WebSocket connection registry"
  value       = var.use_local ? "" : aws_dynamodb_table.ws_connections[0].name
}

output "sim_jobs_table" {
  description = "DynamoDB table name for simulation job records"
  value       = aws_dynamodb_table.sim_jobs.name
}

output "sim_jobs_stream_arn" {
  description = "DynamoDB Streams ARN for my5-sim-jobs (triggers fanout_handler)"
  value       = var.use_local ? "" : aws_dynamodb_table.sim_jobs.stream_arn
}

output "connect_handler_name" {
  description = "Lambda function name for $connect/$disconnect"
  value       = var.use_local ? "" : aws_lambda_function.connect_handler[0].function_name
}

output "fanout_handler_name" {
  description = "Lambda function name for DynamoDB Streams fanout"
  value       = var.use_local ? "" : aws_lambda_function.fanout_handler[0].function_name
}

output "job_queue_url" {
  description = "SQS queue URL for simulation job submission"
  value       = var.use_local ? "" : aws_sqs_queue.job_queue[0].url
}

output "dashboard_url" {
  description = "CloudWatch dashboard URL"
  value       = var.use_local ? "" : "https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#dashboards:name=my5-simulator"
}

output "sim_cache_table" {
  description = "DynamoDB table name for simulation result cache"
  value       = aws_dynamodb_table.sim_cache.name
}

# ── Worker Lambda (SQS-triggered simulation processor) ───────────────────────
#
# Calls handle_job() for each message from my5-jobs. Byte-identical core to
# the local polling_loop — only the invocation shell differs.
#
# Package: src/my5/ + numpy + boto3, built for manylinux_2_17_x86_64.
# Timeout: 60s (worst-case 5000-sim run is ~20s; 60s gives safe headroom).
# Memory: 256MB (higher memory → more CPU allocation; numpy benefits).
# Cost: $0 idle (scale-to-zero). Billed per 100ms of invocation.

locals {
  worker_lambda_zip = "${path.module}/my5_worker_lambda.zip"
  api_lambda_zip    = "${path.module}/my5_api_lambda.zip"
}

resource "aws_iam_role" "lambda_worker" {
  count = var.use_local ? 0 : 1
  name  = "my5-lambda-worker-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_policy" "lambda_worker" {
  count = var.use_local ? 0 : 1
  name  = "my5-lambda-worker-policy"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        # SQS event source mapping: Lambda's internal poller needs ReceiveMessage
        # + DeleteMessage + GetQueueAttributes to manage the event source.
        Sid    = "SQSConsume"
        Effect = "Allow"
        Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.job_queue[0].arn
      },
      {
        # sim-jobs: get (idempotency check), update_status, update_progress,
        # write_result, fail_job — all via update_item or get_item.
        Sid    = "SimJobsRW"
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.sim_jobs.arn
      },
      {
        # sim-cache: cache.put after successful simulation (step 6 in handle_job).
        # GetItem included so the same role could handle cache read if ever needed.
        Sid    = "SimCacheWrite"
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:PutItem"]
        Resource = aws_dynamodb_table.sim_cache.arn
      },
      {
        # player-params: batch_get_item for 5 players per job.
        Sid    = "PlayerParamsRead"
        Effect = "Allow"
        Action = ["dynamodb:BatchGetItem"]
        Resource = aws_dynamodb_table.player_params.arn
      },
      {
        # lineup-metrics: get_item for historical lineup defensive rates.
        # For "hypothetical" lineups this is skipped at runtime, but the role
        # needs it for any historical lineup lookup.
        Sid    = "LineupMetricsRead"
        Effect = "Allow"
        Action = ["dynamodb:GetItem"]
        Resource = aws_dynamodb_table.lineup_metrics.arn
      },
      {
        Sid    = "XRayTracing"
        Effect = "Allow"
        Action = [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords",
          "xray:GetSamplingRules",
          "xray:GetSamplingTargets",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_worker" {
  count      = var.use_local ? 0 : 1
  role       = aws_iam_role.lambda_worker[0].name
  policy_arn = aws_iam_policy.lambda_worker[0].arn
}

resource "aws_lambda_function" "worker" {
  count         = var.use_local ? 0 : 1
  function_name = "my5-worker"
  role          = aws_iam_role.lambda_worker[0].arn

  filename         = local.worker_lambda_zip
  source_code_hash = filebase64sha256(local.worker_lambda_zip)

  runtime     = "python3.11"
  handler     = "my5.aws.worker_handler.handler"
  timeout     = 60
  memory_size = 256

  environment {
    variables = {
      MY5_ENV           = "aws"
      MY5_SQS_QUEUE_URL = aws_sqs_queue.job_queue[0].url
      MY5_SQS_DLQ_URL   = aws_sqs_queue.job_dlq[0].url
    }
  }

  tracing_config {
    mode = "Active"
  }
}

# SQS → Worker event source mapping.
# batch_size=1: one job per Lambda invocation — clean error isolation and
# correct retry semantics (SQS retries the single failed job, not a batch).
resource "aws_lambda_event_source_mapping" "worker_sqs" {
  count            = var.use_local ? 0 : 1
  event_source_arn = aws_sqs_queue.job_queue[0].arn
  function_name    = aws_lambda_function.worker[0].arn
  batch_size       = 1
}

# ── DLQ Lambda (marks jobs failed after 3 delivery attempts) ─────────────────
#
# Triggered by my5-jobs-dlq. Calls job_store.fail_job — no simulation logic.
# Uses the same worker ZIP (both handlers live in my5/aws/).
# Cost: $0 idle. Only invoked on dead-lettered jobs (rare in normal operation).

resource "aws_iam_role" "lambda_dlq" {
  count = var.use_local ? 0 : 1
  name  = "my5-lambda-dlq-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_policy" "lambda_dlq" {
  count = var.use_local ? 0 : 1
  name  = "my5-lambda-dlq-policy"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        Sid    = "DLQConsume"
        Effect = "Allow"
        Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.job_dlq[0].arn
      },
      {
        # fail_job calls update_item on my5-sim-jobs.
        Sid    = "SimJobsFail"
        Effect = "Allow"
        Action = ["dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.sim_jobs.arn
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_dlq" {
  count      = var.use_local ? 0 : 1
  role       = aws_iam_role.lambda_dlq[0].name
  policy_arn = aws_iam_policy.lambda_dlq[0].arn
}

resource "aws_lambda_function" "dlq_worker" {
  count         = var.use_local ? 0 : 1
  function_name = "my5-worker-dlq"
  role          = aws_iam_role.lambda_dlq[0].arn

  filename         = local.worker_lambda_zip
  source_code_hash = filebase64sha256(local.worker_lambda_zip)

  runtime     = "python3.11"
  handler     = "my5.aws.dlq_handler.handler"
  timeout     = 30
  memory_size = 128

  environment {
    variables = {
      MY5_ENV         = "aws"
      MY5_SQS_DLQ_URL = aws_sqs_queue.job_dlq[0].url
    }
  }
}

resource "aws_lambda_event_source_mapping" "dlq_sqs" {
  count            = var.use_local ? 0 : 1
  event_source_arn = aws_sqs_queue.job_dlq[0].arn
  function_name    = aws_lambda_function.dlq_worker[0].arn
  batch_size       = 10
}

# ── FastAPI Lambda + API Gateway HTTP API ─────────────────────────────────────
#
# Public HTTP API: GET /api/players, POST /api/simulate, GET /api/jobs/{job_id}.
# Mangum (handler = Mangum(app) in api/main.py) wraps the FastAPI ASGI app.
#
# Cold start: _load_player_cache() scans my5-player-params once (~400 rows,
# ~500ms). The idempotency check in _lifespan ensures this only happens on
# the first invocation per container — warm invocations skip it.
#
# API Gateway HTTP API (not REST API): lower latency, simpler routing.
# $1/million requests; first 300M/month free (12-month AWS free tier).
# Cost: $0 when idle (no requests = no charge). No always-on compute.

resource "aws_iam_role" "lambda_api" {
  count = var.use_local ? 0 : 1
  name  = "my5-lambda-api-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_policy" "lambda_api" {
  count = var.use_local ? 0 : 1
  name  = "my5-lambda-api-policy"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "Logs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = "arn:aws:logs:*:*:*"
      },
      {
        # _load_player_cache() does a full Scan at cold start (~400 rows, ~50KB).
        Sid    = "PlayerParamsScan"
        Effect = "Allow"
        Action = ["dynamodb:Scan"]
        Resource = aws_dynamodb_table.player_params.arn
      },
      {
        # GET /api/jobs/{id} → get_item; POST /api/simulate → put_item (create_job).
        Sid    = "SimJobsRW"
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:PutItem"]
        Resource = aws_dynamodb_table.sim_jobs.arn
      },
      {
        # Cache hit check in submit_job (before enqueuing the miss).
        Sid    = "SimCacheRead"
        Effect = "Allow"
        Action = ["dynamodb:GetItem"]
        Resource = aws_dynamodb_table.sim_cache.arn
      },
      {
        # submit_job enqueues to my5-jobs on a cache miss.
        Sid    = "SQSSend"
        Effect = "Allow"
        Action = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.job_queue[0].arn
      },
      {
        Sid    = "XRayTracing"
        Effect = "Allow"
        Action = [
          "xray:PutTraceSegments",
          "xray:PutTelemetryRecords",
          "xray:GetSamplingRules",
          "xray:GetSamplingTargets",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_api" {
  count      = var.use_local ? 0 : 1
  role       = aws_iam_role.lambda_api[0].name
  policy_arn = aws_iam_policy.lambda_api[0].arn
}

resource "aws_lambda_function" "api" {
  count         = var.use_local ? 0 : 1
  function_name = "my5-api"
  role          = aws_iam_role.lambda_api[0].arn

  filename         = local.api_lambda_zip
  source_code_hash = filebase64sha256(local.api_lambda_zip)

  runtime     = "python3.11"
  handler     = "api.main.handler"
  timeout     = 30
  memory_size = 256  # Cold-start DynamoDB scan + FastAPI init; 256MB for speed.

  environment {
    variables = {
      MY5_ENV           = "aws"
      MY5_SQS_QUEUE_URL = aws_sqs_queue.job_queue[0].url
      # CORS: allow all origins (permissive default — tighten after Vercel deploy).
      # FastAPI CORSMiddleware reads this env var via api/config.py.
      CORS_ORIGINS      = "*"
    }
  }

  tracing_config {
    mode = "Active"
  }
}

# API Gateway HTTP API — public HTTPS endpoint for the FastAPI layer.
# Uses the $default route (catch-all) so Mangum routes internally by path.
# No CORS configuration here — FastAPI's CORSMiddleware handles CORS in-process,
# preventing duplicate Access-Control-Allow-Origin headers.
resource "aws_apigatewayv2_api" "http" {
  count         = var.use_local ? 0 : 1
  name          = "my5-http"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_integration" "api" {
  count                  = var.use_local ? 0 : 1
  api_id                 = aws_apigatewayv2_api.http[0].id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api[0].invoke_arn
  payload_format_version = "2.0"  # Required for Mangum with HTTP API.
}

# $default catch-all route: all methods and paths → Lambda.
# Mangum routes to the correct FastAPI handler internally.
resource "aws_apigatewayv2_route" "api_default" {
  count     = var.use_local ? 0 : 1
  api_id    = aws_apigatewayv2_api.http[0].id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.api[0].id}"
}

# $default stage with auto-deploy: no manual deploy step required.
# Invoke URL: https://{id}.execute-api.us-east-1.amazonaws.com (no stage prefix).
resource "aws_apigatewayv2_stage" "http" {
  count       = var.use_local ? 0 : 1
  api_id      = aws_apigatewayv2_api.http[0].id
  name        = "$default"
  auto_deploy = true
}

resource "aws_lambda_permission" "apigw_http" {
  count         = var.use_local ? 0 : 1
  statement_id  = "AllowHTTPAPIInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api[0].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http[0].execution_arn}/*"
}

# ── New outputs ───────────────────────────────────────────────────────────────

output "http_api_url" {
  description = "Public HTTP API base URL — set as NEXT_PUBLIC_API_URL in Vercel"
  value       = var.use_local ? "" : aws_apigatewayv2_stage.http[0].invoke_url
}

output "worker_lambda_name" {
  description = "Simulation worker Lambda function name"
  value       = var.use_local ? "" : aws_lambda_function.worker[0].function_name
}

output "api_lambda_name" {
  description = "FastAPI Lambda function name"
  value       = var.use_local ? "" : aws_lambda_function.api[0].function_name
}

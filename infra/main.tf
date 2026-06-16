terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# When use_local=true  → amazon/dynamodb-local on localhost:8000, dummy creds.
# When use_local=false → normal AWS provider (real deployment — not used yet).
provider "aws" {
  region = "us-east-1"

  # Dummy creds required by DynamoDB Local; ignored by real AWS (which reads
  # credentials from the normal chain: env vars, ~/.aws/credentials, IAM role).
  access_key = var.use_local ? "test" : null
  secret_key = var.use_local ? "test" : null

  dynamic "endpoints" {
    for_each = var.use_local ? [1] : []
    content {
      dynamodb = "http://localhost:8000"
    }
  }

  # These skip_* flags suppress validation calls that DynamoDB Local can't answer.
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

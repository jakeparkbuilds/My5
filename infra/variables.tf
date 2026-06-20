variable "use_local" {
  description = "When true, point DynamoDB at amazon/dynamodb-local on localhost:8000 with dummy creds. When false, use the normal AWS provider for real deployment."
  type        = bool
  default     = true
}

variable "planning_only" {
  description = <<-EOT
    Set true for CI / plan-only runs where no real AWS credentials are available.
    Adds dummy access/secret keys and skips credential + account validation so
    `terraform plan -var="use_local=false" -var="planning_only=true"` succeeds
    without hitting AWS endpoints.

    NEVER set this for a real apply — it silently uses fake credentials and would
    fail immediately when Terraform tried to make actual API calls.
  EOT
  type        = bool
  default     = false
}

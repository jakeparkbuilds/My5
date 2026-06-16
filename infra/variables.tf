variable "use_local" {
  description = "When true, point DynamoDB at amazon/dynamodb-local on localhost:8000 with dummy creds. When false, use the normal AWS provider for real deployment."
  type        = bool
  default     = true
}

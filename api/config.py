"""
API configuration — reads from environment, same MY5_ENV discipline as the backend.

MY5_ENV=local  → hits DynamoDB Local, ElasticMQ (default)
MY5_ENV=aws    → hits real AWS

Frontend origins allowed by CORS:
  CORS_ORIGINS=http://localhost:3000,https://my5.example.com
"""
from __future__ import annotations

import os

# Inherit the dual-target env from the backend.
MY5_ENV: str = os.getenv("MY5_ENV", "local")

# Comma-separated list of allowed CORS origins.
# Default includes the Next.js dev server.
_raw = os.getenv("CORS_ORIGINS", "http://localhost:3000")
CORS_ORIGINS: list[str] = [o.strip() for o in _raw.split(",") if o.strip()]

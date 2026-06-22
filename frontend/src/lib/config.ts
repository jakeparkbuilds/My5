/**
 * Frontend environment config — all URLs come from env vars.
 *
 * Set these in frontend/.env.local (never commit real values):
 *   NEXT_PUBLIC_API_URL=http://localhost:8001
 *   NEXT_PUBLIC_WS_URL=wss://v3usuogl70.execute-api.us-east-1.amazonaws.com/prod
 *
 * NEXT_PUBLIC_ prefix makes variables available to the browser bundle.
 * API_URL and WS_URL are never hardcoded here.
 */

export const API_URL: string =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";

export const WS_URL: string =
  process.env.NEXT_PUBLIC_WS_URL ?? "";

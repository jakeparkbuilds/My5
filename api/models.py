"""Pydantic request/response models for the HTTP API."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PlayerOut(BaseModel):
    athlete_id: int
    display_name: str
    short_name: str
    team_abbr: str
    team_name: str
    team_id: int | None
    headshot_href: str
    # Simulation params included for display (usage, shooting splits)
    usage_rate: float | None = None
    fg3_pct: float | None = None
    rim_fg_pct: float | None = None
    mid_fg_pct: float | None = None
    tov_rate: float | None = None
    ft_pct: float | None = None


class SimulateRequest(BaseModel):
    team_a_player_ids: list[int] = Field(..., min_length=5, max_length=5)
    team_b_player_ids: list[int] = Field(..., min_length=5, max_length=5)
    seed: int | None = None


class SimResultOut(BaseModel):
    mean_margin: float
    ci_half_width: float
    n_sims: int
    equiv_net_rating: float
    converged: bool


class SimulateResponse(BaseModel):
    job_id: str | None
    cache_hit: bool
    cached_result: SimResultOut | None = None


class JobStatusResponse(BaseModel):
    job_id: str
    status: str                    # queued | running | done | failed
    result: SimResultOut | None = None
    error_type: str | None = None
    error_message: str | None = None
    sims_done: int = 0
    ci_half: float = 0.0

"""Validated deployment presets; separate from research execution and request options."""
from pydantic import BaseModel, ConfigDict, Field


# Kept for approvals created before presets were snapshotted. Do not enlarge these.
LEGACY_PRESETS = {
    'brief': {'rounds': 5, 'searches': 3, 'reads': 4, 'seconds': 120, 'tokens': 20000},
    'standard': {'rounds': 8, 'searches': 6, 'reads': 8, 'seconds': 300, 'tokens': 40000},
    'thorough': {'rounds': 12, 'searches': 10, 'reads': 16, 'seconds': 600, 'tokens': 80000},
}
DEFAULT_PRESETS = {
    'brief': dict(LEGACY_PRESETS['brief']),
    'standard': {'rounds': 12, 'searches': 8, 'reads': 16, 'seconds': 900, 'tokens': 250000},
    'thorough': {'rounds': 24, 'searches': 24, 'reads': 48, 'seconds': 1800, 'tokens': 1000000},
}


class ResearchLimits(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)

    rounds: int = Field(ge=3, le=100)
    searches: int = Field(ge=1, le=100)
    reads: int = Field(ge=1, le=100)
    seconds: int = Field(ge=30, le=7200)
    tokens: int = Field(ge=1000, le=5000000)


class ResearchConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')

    brief: ResearchLimits
    standard: ResearchLimits
    thorough: ResearchLimits

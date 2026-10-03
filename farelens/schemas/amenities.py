"""Public API schemas for flight amenities and fare-family names."""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

AMENITY_TYPES: tuple[str, ...] = (
    "Baggage",
    "CabinBaggage",
    "Seat",
    "Meal",
    "Lounge",
    "Priority",
    "Wifi",
    "Entertainment",
    "Miles",
    "Upgrade",
    "Refund",
    "Change",
    "GroundServices",
    "Insurance",
    "Warning",
    "Other",
)

CacheStatus = Literal["redis-hit", "postgres-hit", "generated", "mixed"]


class AmenitiesRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    amenities: list[str] = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Raw amenity / benefit texts as supplied in the Amadeus NDC response (one entry per line item).",
    )
    lang: str = Field(default="en", min_length=2, max_length=5, description="Output language code.")
    summarize: bool = Field(
        default=True,
        description="true: merge duplicates, classify and shorten. false: literal translation only, one result per input.",
    )

    @field_validator("amenities")
    @classmethod
    def _clean(cls, values: list[str]) -> list[str]:
        cleaned = [str(v or "").strip() for v in values]
        cleaned = [v for v in cleaned if v]
        if not cleaned:
            raise ValueError("amenities must contain at least one non-blank entry")
        return cleaned

    @field_validator("lang")
    @classmethod
    def _norm_lang(cls, value: str) -> str:
        return value.strip().lower()


class AmenityItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str = Field(default="Other", description=f"One of: {', '.join(AMENITY_TYPES)}")
    description: str = Field(..., min_length=1, description="Short traveller-friendly text (main takeaway).")
    details: str = Field(default="", description="Important qualifiers: limits, conditions, timing, fees.")
    is_chargeable: bool = Field(default=False, description="True when the benefit is a paid add-on.")
    included: bool | None = Field(default=None, description="True if included, False if explicitly not included, null if unclear.")
    ref_url: str = Field(default="", description="URL found in the source text, if any.")

    @field_validator("type")
    @classmethod
    def _type(cls, value: str) -> str:
        v = str(value or "").strip()
        lookup = {t.lower(): t for t in AMENITY_TYPES}
        return lookup.get(v.lower(), "Other")

    @field_validator("description", "details", "ref_url")
    @classmethod
    def _strip(cls, value: str) -> str:
        return str(value or "").strip()


class AmenitiesResponse(BaseModel):
    amenities: list[AmenityItem]
    cache: CacheStatus
    lang: str
    summarize: bool
    provider: str | None = None
    model: str | None = None
    latency_ms: int


class FareNamesRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    names: list[str] = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Technical fare-family codes, e.g. ECOLITE, BUSIFLEX.",
        validation_alias=AliasChoices("names", "codes"),
    )
    lang: str = Field(default="en", min_length=2, max_length=5)

    @field_validator("names")
    @classmethod
    def _clean(cls, values: list[str]) -> list[str]:
        out: list[str] = []
        for v in values:
            code = str(v or "").strip().upper()
            if code and code not in out:
                out.append(code)
        if not out:
            raise ValueError("names must contain at least one non-blank code")
        return out

    @field_validator("lang")
    @classmethod
    def _norm_lang(cls, value: str) -> str:
        return value.strip().lower()


class FareNamesResponse(BaseModel):
    names: dict[str, str] = Field(..., description="Fare code to friendly label.")
    cache: CacheStatus
    lang: str
    provider: str | None = None
    model: str | None = None
    latency_ms: int

"""Public API: flight amenities and fare-family names."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, Response

from farelens.api.deps import Principal, Services, get_services, request_id, require_principal
from farelens.schemas.amenities import (
    AmenitiesRequest,
    AmenitiesResponse,
    AmenityItem,
    FareNamesRequest,
    FareNamesResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/amenities", tags=["amenities"])


@router.post(
    "/summary",
    response_model=AmenitiesResponse,
    summary="De-duplicate, classify and translate fare-family amenities",
    description=(
        "Send the raw benefit texts of a fare family (baggage, seat, lounge, meal, miles, ...). "
        "With `summarize=true` (default) same-meaning items are merged, classified and shortened; with "
        "`summarize=false` each item is translated literally. Results are cached per item and language, so only "
        "unseen texts reach the model. The body may also be a bare JSON array of strings."
    ),
)
async def amenities_summary(
    response: Response,
    payload: Any = Body(...),
    principal: Principal = Depends(require_principal),
    services: Services = Depends(get_services),
    rid: str = Depends(request_id),
) -> AmenitiesResponse:
    req = AmenitiesRequest.model_validate({"amenities": payload} if isinstance(payload, list) else payload)
    result = await services.amenities.process(
        req.amenities, lang=req.lang, summarize=req.summarize, api_key_id=principal.api_key_id, request_id=rid
    )
    response.headers["X-Cache"] = result.cache_status
    logger.info("amenities ok items=%s cache=%s lang=%s summarize=%s by=%s", len(result.amenities), result.cache_status, req.lang, req.summarize, principal.name)
    return AmenitiesResponse(
        amenities=[AmenityItem.model_validate(i) for i in result.amenities],
        cache=result.cache_status,  # type: ignore[arg-type]
        lang=req.lang,
        summarize=req.summarize,
        provider=result.provider,
        model=result.model,
        latency_ms=result.latency_ms,
    )


@router.post(
    "/fare-names",
    response_model=FareNamesResponse,
    summary="Convert fare-family codes to friendly labels",
    description="`[\"ECOLITE\", \"BUSIFLEX\"]` becomes `{\"ECOLITE\": \"Economy Lite\", \"BUSIFLEX\": \"Business Flex\"}`. Cached per code and language.",
)
async def fare_names(
    payload: FareNamesRequest,
    response: Response,
    principal: Principal = Depends(require_principal),
    services: Services = Depends(get_services),
    rid: str = Depends(request_id),
) -> FareNamesResponse:
    result = await services.amenities.fare_names(payload.names, lang=payload.lang, api_key_id=principal.api_key_id, request_id=rid)
    response.headers["X-Cache"] = result.cache_status
    return FareNamesResponse(
        names=result.names,
        cache=result.cache_status,  # type: ignore[arg-type]
        lang=payload.lang,
        provider=result.provider,
        model=result.model,
        latency_ms=result.latency_ms,
    )

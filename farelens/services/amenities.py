"""Flight amenities: de-duplicate / classify / translate benefit texts, and
convert fare-family codes to friendly labels.

Caching is per *item* (one amenity text, one language, one mode), so a batch
of 20 amenities where 18 were seen before only sends 2 to the model.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from farelens.core.config import Settings
from farelens.core.errors import AppError, LLMUpstreamError
from farelens.db.datastores import DataStores
from farelens.llm.base import LLMMessage, LLMProviderError, LLMUsage
from farelens.schemas.amenities import AMENITY_TYPES, AmenityItem
from farelens.services.amenities_text import (
    build_amenity_cache_key,
    extract_first_url,
    extract_json,
    normalize_amenity_for_prompt,
    strip_urls,
)
from farelens.services.llm_config import LLMConfigService
from farelens.services.prompts import PromptService, language_name
from farelens.services.usage import UsageEvent, UsageService

logger = logging.getLogger(__name__)

_CODE_RE = re.compile(r"[^A-Z0-9]")


@dataclass(slots=True)
class AmenitiesResult:
    amenities: list[dict[str, Any]]
    cache_status: str
    provider: str | None
    model: str | None
    latency_ms: int = 0


@dataclass(slots=True)
class FareNamesResult:
    names: dict[str, str]
    cache_status: str
    provider: str | None
    model: str | None
    latency_ms: int = 0


@dataclass(slots=True)
class _Tally:
    redis: int = 0
    postgres: int = 0
    generated: int = 0
    usage: LLMUsage = field(default_factory=LLMUsage)

    def status(self) -> str:
        hits = self.redis + self.postgres
        if self.generated and hits:
            return "mixed"
        if self.generated:
            return "generated"
        if self.postgres:
            return "postgres-hit"
        return "redis-hit"

    def add_usage(self, usage: LLMUsage) -> None:
        self.usage = LLMUsage(
            prompt_tokens=(self.usage.prompt_tokens or 0) + (usage.prompt_tokens or 0),
            completion_tokens=(self.usage.completion_tokens or 0) + (usage.completion_tokens or 0),
        )


class AmenitiesService:
    def __init__(
        self,
        settings: Settings,
        datastores: DataStores,
        prompts: PromptService,
        llm_config: LLMConfigService,
        usage: UsageService,
    ):
        self._settings = settings
        self._ds = datastores
        self._prompts = prompts
        self._llm_config = llm_config
        self._usage = usage

    # ================================================================ amenities
    async def process(
        self,
        amenities: list[str],
        *,
        lang: str = "en",
        summarize: bool = True,
        api_key_id: int | None = None,
        request_id: str | None = None,
    ) -> AmenitiesResult:
        if len(amenities) > self._settings.max_amenities_per_request:
            raise AppError(
                f"amenities exceeds the maximum of {self._settings.max_amenities_per_request} items",
                status_code=413,
                code="PAYLOAD_TOO_LARGE",
            )
        for index, text in enumerate(amenities, start=1):
            if len(text) > self._settings.max_amenity_text_chars:
                raise AppError(
                    f"amenities[{index}] exceeds the maximum of {self._settings.max_amenity_text_chars} characters",
                    status_code=413,
                    code="PAYLOAD_TOO_LARGE",
                )

        lang = (lang or "en").strip().lower()
        mode = "summary" if summarize else "translate"
        started = time.perf_counter()
        tally = _Tally()

        # Dedupe inputs while preserving order.
        keyed: dict[str, str] = {}
        for raw in amenities:
            text = normalize_amenity_for_prompt(raw)
            if not text:
                continue
            key = build_amenity_cache_key(text, namespace=self._settings.amenity_cache_namespace)
            keyed.setdefault(key, text)

        resolved: dict[str, dict[str, Any]] = {}
        missing: dict[str, str] = {}
        for key, text in keyed.items():
            item = await self._get_redis(key, lang, mode)
            if item is not None:
                resolved[key] = item
                tally.redis += 1
                continue
            item = await self._get_postgres(key, lang, mode)
            if item is not None:
                resolved[key] = item
                tally.postgres += 1
                await self._set_redis(key, lang, mode, item)
                continue
            missing[key] = text

        provider_name: str | None = None
        model_name: str | None = None
        if missing:
            provider, cfg = await self._llm_config.get_provider()
            provider_name, model_name = cfg.provider, cfg.model
            batch_size = max(5, self._settings.amenity_batch_size)
            missing_items = list(missing.items())
            for start in range(0, len(missing_items), batch_size):
                batch = dict(missing_items[start : start + batch_size])
                try:
                    generated, usage = await self._generate(provider, cfg, batch, lang=lang, summarize=summarize)
                except LLMProviderError as exc:
                    await self._record("amenities", "error", provider_name, model_name, exc.code, None, started, lang, api_key_id, request_id)
                    raise LLMUpstreamError(exc.message, status_code=exc.status_code, code=exc.code) from exc
                tally.add_usage(usage)
                for key, text in batch.items():
                    item = generated.get(key) or self._fallback_item(text)
                    resolved[key] = item
                    tally.generated += 1
                    await self._set_postgres(key, lang, mode, text, item, provider_name, model_name)
                    await self._set_redis(key, lang, mode, item)

        ordered = [resolved[k] for k in keyed if k in resolved]
        output = self._collapse(ordered) if summarize else ordered
        if not provider_name:
            provider_name, model_name = _first_source(ordered)

        result = AmenitiesResult(output, tally.status(), provider_name, model_name, int((time.perf_counter() - started) * 1000))
        await self._record(
            "amenities", "ok", provider_name, model_name, None, tally, started, lang, api_key_id, request_id,
            usage=tally.usage if tally.generated else None,
        )
        return result

    async def _generate(self, provider, cfg, batch: dict[str, str], *, lang: str, summarize: bool) -> tuple[dict[str, dict[str, Any]], LLMUsage]:
        messages = self.build_messages(batch, lang=lang, summarize=summarize)
        response = await provider.complete(messages, cfg.generation_options(self._settings.llm_timeout_seconds))
        try:
            parsed = extract_json(response.text)
        except ValueError as exc:
            logger.warning("amenities: unparsable model output (%s); using fallbacks", exc)
            return {}, response.usage
        rows = parsed.get("amenities") if isinstance(parsed, dict) else parsed
        out: dict[str, dict[str, Any]] = {}
        if not isinstance(rows, list):
            return out, response.usage
        for row in rows:
            if not isinstance(row, dict):
                continue
            keys = row.get("key") or row.get("keys") or []
            if isinstance(keys, str):
                keys = [keys]
            keys = [k for k in keys if isinstance(k, str) and k in batch]
            if not keys:
                continue
            source_url = next((extract_first_url(batch[k]) for k in keys if extract_first_url(batch[k])), "")
            try:
                item = AmenityItem(
                    type=row.get("type", "Other") if summarize else "Other",
                    description=strip_urls(str(row.get("description") or "")) or normalize_amenity_for_prompt(batch[keys[0]]),
                    details=strip_urls(str(row.get("details") or row.get("supportingImpInfo") or "")) if summarize else "",
                    is_chargeable=bool(row.get("is_chargeable") or row.get("isChargeable") or False) if summarize else False,
                    included=row.get("included") if isinstance(row.get("included"), bool) else None,
                    ref_url=str(row.get("ref_url") or source_url or ""),
                ).model_dump()
            except Exception:  # noqa: BLE001 - one bad row must not sink the batch
                logger.warning("amenities: dropping invalid model row %r", row)
                continue
            for k in keys:
                out[k] = item
        return out, response.usage

    def build_messages(
        self, batch: dict[str, str], *, lang: str, summarize: bool, temp_overrides: dict[str, str] | None = None
    ) -> list[LLMMessage]:
        prefix = "amenities/summary" if summarize else "amenities/translate"
        target = language_name(lang)
        system = PromptService.render(
            self._prompts.load(f"{prefix}_system.md", temp_overrides),
            TARGET_LANGUAGE=target,
            ALLOWED_TYPES=", ".join(AMENITY_TYPES),
        )
        payload = json.dumps([{"key": k, "text": t} for k, t in batch.items()], ensure_ascii=False, indent=1)
        user = PromptService.render(
            self._prompts.load(f"{prefix}_user.md", temp_overrides),
            TARGET_LANGUAGE=target,
            AMENITIES_INPUT=payload,
        )
        return [LLMMessage("system", system), LLMMessage("user", user)]

    @staticmethod
    def _fallback_item(text: str) -> dict[str, Any]:
        url = extract_first_url(text)
        return AmenityItem(type="Other", description=strip_urls(text) or text, details="", ref_url=url).model_dump()

    @staticmethod
    def _collapse(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Merge objects that ended up identical after per-item caching."""
        merged: dict[str, dict[str, Any]] = {}
        for item in items:
            sig = f"{item.get('type')}|{str(item.get('description', '')).strip().lower()}"
            if sig in merged:
                cur = merged[sig]
                extra = str(item.get("details") or "").strip()
                if extra and extra.lower() not in str(cur.get("details", "")).lower():
                    cur["details"] = f"{cur['details']}; {extra}".strip("; ")
                if not cur.get("ref_url") and item.get("ref_url"):
                    cur["ref_url"] = item["ref_url"]
                cur["is_chargeable"] = bool(cur.get("is_chargeable") or item.get("is_chargeable"))
                continue
            merged[sig] = dict(item)
        return list(merged.values())

    # ================================================================ fare names
    async def fare_names(
        self,
        names: list[str],
        *,
        lang: str = "en",
        api_key_id: int | None = None,
        request_id: str | None = None,
    ) -> FareNamesResult:
        lang = (lang or "en").strip().lower()
        started = time.perf_counter()
        tally = _Tally()
        codes: list[str] = []
        for n in names:
            code = _CODE_RE.sub("", str(n or "").strip().upper())
            if code and code not in codes:
                codes.append(code)
        if not codes:
            raise AppError("No valid fare-family codes supplied.", status_code=422, code="VALIDATION_ERROR")

        labels: dict[str, str] = {}
        missing: list[str] = []
        for code in codes:
            label = await self._get_redis_label(code, lang)
            if label:
                labels[code] = label
                tally.redis += 1
                continue
            label = await self._get_postgres_label(code, lang)
            if label:
                labels[code] = label
                tally.postgres += 1
                await self._set_redis_label(code, lang, label)
                continue
            missing.append(code)

        provider_name = model_name = None
        if missing:
            provider, cfg = await self._llm_config.get_provider()
            provider_name, model_name = cfg.provider, cfg.model
            for start in range(0, len(missing), 100):
                batch = missing[start : start + 100]
                messages = self.build_fare_name_messages(batch, lang=lang)
                try:
                    response = await provider.complete(messages, cfg.generation_options(self._settings.llm_timeout_seconds))
                except LLMProviderError as exc:
                    await self._record("fare_names", "error", provider_name, model_name, exc.code, None, started, lang, api_key_id, request_id)
                    raise LLMUpstreamError(exc.message, status_code=exc.status_code, code=exc.code) from exc
                tally.add_usage(response.usage)
                parsed: dict[str, Any] = {}
                try:
                    data = extract_json(response.text)
                    if isinstance(data, dict):
                        parsed = {str(k).strip().upper(): str(v).strip() for k, v in data.items() if str(v or "").strip()}
                    elif isinstance(data, list) and len(data) == len(batch):
                        parsed = {c: str(v).strip() for c, v in zip(batch, data, strict=True) if str(v or "").strip()}
                except ValueError as exc:
                    logger.warning("fare names: unparsable model output (%s); using heuristics", exc)
                for code in batch:
                    label = parsed.get(code) or fallback_fare_label(code)
                    labels[code] = label
                    tally.generated += 1
                    await self._set_postgres_label(code, lang, label, provider_name, model_name)
                    await self._set_redis_label(code, lang, label)

        result = FareNamesResult({c: labels[c] for c in codes}, tally.status(), provider_name, model_name, int((time.perf_counter() - started) * 1000))
        await self._record("fare_names", "ok", provider_name, model_name, None, tally, started, lang, api_key_id, request_id, usage=tally.usage if tally.generated else None)
        return result

    def build_fare_name_messages(self, codes: list[str], *, lang: str, temp_overrides: dict[str, str] | None = None) -> list[LLMMessage]:
        target = language_name(lang)
        system = PromptService.render(self._prompts.load("amenities/fare_name_system.md", temp_overrides), TARGET_LANGUAGE=target)
        user = PromptService.render(
            self._prompts.load("amenities/fare_name_user.md", temp_overrides),
            TARGET_LANGUAGE=target,
            FARE_FAMILY_CODES=json.dumps(codes),
        )
        return [LLMMessage("system", system), LLMMessage("user", user)]

    # ================================================================ cache io
    def _rkey(self, key: str, lang: str, mode: str) -> str:
        return f"farelens:amenity:{lang}:{mode}:{key}"

    async def _get_redis(self, key: str, lang: str, mode: str) -> dict[str, Any] | None:
        redis = self._ds.redis
        if redis is None:
            return None
        try:
            raw = await redis.get(self._rkey(key, lang, mode))
            if raw:
                data = json.loads(raw)
                if isinstance(data, dict) and data.get("description"):
                    return data
        except Exception:  # noqa: BLE001
            logger.warning("amenity redis read failed", exc_info=True)
        return None

    async def _set_redis(self, key: str, lang: str, mode: str, item: dict[str, Any]) -> None:
        redis = self._ds.redis
        if redis is None:
            return
        try:
            await redis.set(self._rkey(key, lang, mode), json.dumps(item, ensure_ascii=False), ex=self._settings.summary_cache_ttl_seconds)
        except Exception:  # noqa: BLE001
            logger.warning("amenity redis write failed", exc_info=True)

    async def _get_postgres(self, key: str, lang: str, mode: str) -> dict[str, Any] | None:
        pool = self._ds.pg
        if pool is None:
            return None
        try:
            row = await pool.fetchrow(
                """
                UPDATE amenity_cache SET last_accessed_at = NOW(), hit_count = hit_count + 1
                WHERE cache_key = $1 AND lang = $2 AND mode = $3
                RETURNING item, provider, model
                """,
                key, lang, mode,
            )
        except Exception:  # noqa: BLE001
            logger.warning("amenity postgres read failed", exc_info=True)
            return None
        if row is None:
            return None
        item = row["item"]
        if isinstance(item, str):
            item = json.loads(item)
        if isinstance(item, dict) and item.get("description"):
            item.setdefault("_provider", row["provider"])
            item.setdefault("_model", row["model"])
            return item
        return None

    async def _set_postgres(self, key: str, lang: str, mode: str, text: str, item: dict[str, Any], provider: str | None, model: str | None) -> None:
        pool = self._ds.pg
        if pool is None:
            return
        try:
            await pool.execute(
                """
                INSERT INTO amenity_cache (cache_key, lang, mode, source_text, item, provider, model)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7)
                ON CONFLICT (cache_key, lang, mode) DO UPDATE SET
                    item = EXCLUDED.item, provider = EXCLUDED.provider, model = EXCLUDED.model, last_accessed_at = NOW()
                """,
                key, lang, mode, text, json.dumps({k: v for k, v in item.items() if not k.startswith("_")}, ensure_ascii=False), provider, model,
            )
        except Exception:  # noqa: BLE001
            logger.warning("amenity postgres write failed", exc_info=True)

    async def _get_redis_label(self, code: str, lang: str) -> str | None:
        redis = self._ds.redis
        if redis is None:
            return None
        try:
            return await redis.get(f"farelens:farename:{lang}:{code}")
        except Exception:  # noqa: BLE001
            return None

    async def _set_redis_label(self, code: str, lang: str, label: str) -> None:
        redis = self._ds.redis
        if redis is None:
            return
        try:
            await redis.set(f"farelens:farename:{lang}:{code}", label, ex=self._settings.summary_cache_ttl_seconds)
        except Exception:  # noqa: BLE001
            pass

    async def _get_postgres_label(self, code: str, lang: str) -> str | None:
        pool = self._ds.pg
        if pool is None:
            return None
        try:
            return await pool.fetchval(
                "UPDATE fare_name_cache SET last_accessed_at = NOW(), hit_count = hit_count + 1 WHERE code = $1 AND lang = $2 RETURNING label",
                code, lang,
            )
        except Exception:  # noqa: BLE001
            return None

    async def _set_postgres_label(self, code: str, lang: str, label: str, provider: str | None, model: str | None) -> None:
        pool = self._ds.pg
        if pool is None:
            return
        try:
            await pool.execute(
                """
                INSERT INTO fare_name_cache (code, lang, label, provider, model) VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (code, lang) DO UPDATE SET label = EXCLUDED.label, provider = EXCLUDED.provider, model = EXCLUDED.model, last_accessed_at = NOW()
                """,
                code, lang, label, provider, model,
            )
        except Exception:  # noqa: BLE001
            logger.warning("fare name postgres write failed", exc_info=True)

    async def cache_stats(self) -> dict[str, Any]:
        pool = self._ds.pg
        if pool is None:
            return {"amenities": 0, "fare_names": 0}
        a = await pool.fetchval("SELECT COUNT(*) FROM amenity_cache")
        f = await pool.fetchval("SELECT COUNT(*) FROM fare_name_cache")
        return {"amenities": int(a or 0), "fare_names": int(f or 0)}

    async def clear_cache(self) -> dict[str, int]:
        removed = {"postgres": 0, "redis": 0}
        pool = self._ds.pg
        if pool is not None:
            for table in ("amenity_cache", "fare_name_cache"):
                status = await pool.execute(f"DELETE FROM {table}")
                removed["postgres"] += int(status.split()[-1]) if status else 0
        redis = self._ds.redis
        if redis is not None:
            for pattern in ("farelens:amenity:*", "farelens:farename:*"):
                batch: list[str] = []
                async for key in redis.scan_iter(match=pattern, count=500):
                    batch.append(key)
                    if len(batch) >= 500:
                        removed["redis"] += await redis.delete(*batch)
                        batch.clear()
                if batch:
                    removed["redis"] += await redis.delete(*batch)
        return removed

    # ================================================================ usage
    async def _record(self, feature, status, provider, model, error_code, tally: _Tally | None, started, lang, api_key_id, request_id, usage: LLMUsage | None = None) -> None:
        await self._usage.record(
            UsageEvent(
                feature=feature,
                status=status,
                provider=provider,
                model=model,
                error_code=error_code,
                cache_status=tally.status() if tally else "generated",
                usage=usage,
                latency_ms=int((time.perf_counter() - started) * 1000),
                lang=lang,
                api_key_id=api_key_id,
                request_id=request_id,
            )
        )


def _first_source(items: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    for item in items:
        if item.get("_provider"):
            return item.get("_provider"), item.get("_model")
    return None, None


_CABIN_PREFIXES = (
    ("PREM", "Premium Economy"), ("PE", "Premium Economy"), ("ECO", "Economy"), ("ECON", "Economy"), ("EC", "Economy"), ("Y", "Economy"),
    ("BUS", "Business"), ("BIZ", "Business"), ("J", "Business"), ("FIR", "First"), ("F", "First"),
)
_TIER_HINTS = (
    ("FLEX", "Flex"), ("FLX", "Flex"), ("PLUS", "Plus"), ("MAX", "Max"), ("COMF", "Comfort"), ("PREM", "Premium"),
    ("LITE", "Lite"), ("LT", "Lite"), ("SAV", "Saver"), ("SVR", "Saver"), ("BASIC", "Basic"), ("VALUE", "Value"),
    ("SMART", "Smart"), ("STD", "Standard"), ("CLASSIC", "Classic"), ("PROMO", "Promo"),
)


def fallback_fare_label(code: str) -> str:
    """Deterministic label when the model gives nothing usable."""
    c = _CODE_RE.sub("", (code or "").upper())
    cabin, rest = "", c
    for prefix, name in _CABIN_PREFIXES:
        if c.startswith(prefix) and len(prefix) > 1 or (len(prefix) == 1 and c.startswith(prefix) and len(c) > 3):
            cabin, rest = name, c[len(prefix) :]
            break
    tier = next((t for hint, t in _TIER_HINTS if hint in rest), "")
    label = f"{cabin} {tier}".strip()
    return label or "Standard"

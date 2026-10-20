"""hosted_app

The hosted demo (spec 2026-10-20): one page where anyone can extract a sample or random
receipt (free mode: the answer key, no model), and where a personal link's cookie unlocks
Claude for a few calls. Requests name a sample or a seed, never free text or a file.
Fails closed: any store error means free mode.

Status:
    None
Issues:
    None
Todo:
    None
"""

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator
from starlette.concurrency import run_in_threadpool

from receipt_sentinel.categorize.categorize_receipt import categorize_receipt
from receipt_sentinel.categorize.llm_client import LlmClient
from receipt_sentinel.categorize.receipt_record import ModelOutput
from receipt_sentinel.evals.scoring import ModelPricing
from receipt_sentinel.extract.document_extractor import extract_document
from receipt_sentinel.extract.extracted_document import ExtractionRejected, OcrUnavailable
from receipt_sentinel.hosted.demo_links import (
    LinkState,
    LinkStore,
    SpendDecision,
    hash_token,
    link_state,
    try_spend_call,
)
from receipt_sentinel.hosted.demo_receipts import (
    MAX_SEED,
    DemoReceipt,
    is_known_sample,
    random_receipt,
    real_receipt_names,
    receipt_image,
    sample_receipt,
)
from receipt_sentinel.hosted.demo_spend import (
    MONTHLY_CAP_USD,
    SpendStore,
    is_paused,
    month_key,
)
from receipt_sentinel.hosted.free_mode_limits import FreeModeLimiter
from receipt_sentinel.hosted.owner_alerts import OwnerAlerts
from receipt_sentinel.prompts.prompt_config import PromptConfig

logger = logging.getLogger(__name__)

COOKIE_NAME = "sentinel_demo"
HOSTED_ENV_VAR = "SENTINEL_HOSTED"
PAGE_PATH = Path(__file__).with_name("hosted_page.html")
STILL_FREE = "The free demo still works."
LINK_NOTICES: dict[LinkState, str] = {
    "expired": f"Your personal link has expired. {STILL_FREE}",
    "revoked": f"Your personal link was turned off. {STILL_FREE}",
    "used_up": f"You've used all your real-model calls. {STILL_FREE}",
    "unknown": f"That personal link isn't valid. {STILL_FREE}",
}
STORE_DOWN_NOTICE = f"Personal links are unavailable right now. {STILL_FREE}"
MODEL_DOWN = f"The real model is unavailable right now; {STILL_FREE[0].lower()}{STILL_FREE[1:]}"
REAL_RECEIPTS_SLOT = "__REAL_RECEIPTS_JSON__"
INVALID_REQUEST = f"send exactly one known sample, or a whole-number seed from 0 to {MAX_SEED}"
IMAGE_LIMITER_PER_IP = 60
IMAGE_LIMITER_PER_DAY = 3000

Mode = Literal["free", "real", "paused"]


class DemoStatus(BaseModel):
    mode: Mode
    calls_left: int | None = None
    call_cap: int | None = None
    expires_on: str | None = None
    notice: str | None = None


class ExtractRequest(BaseModel):
    """Exactly one of a sample name or a seed; anything else is a 422."""

    model_config = ConfigDict(extra="forbid")
    sample: str | None = None
    seed: Annotated[StrictInt, Field(ge=0, le=MAX_SEED)] | None = None

    @model_validator(mode="after")
    def exactly_one_known_receipt(self) -> "ExtractRequest":
        if (self.sample is None) == (self.seed is None):
            raise ValueError("send exactly one of sample or seed")
        if self.sample is not None and not is_known_sample(self.sample):
            raise ValueError("unknown sample")
        return self


ExtractionMethod = Literal["native", "ocr", "saved"]


class ExtractResponse(BaseModel):
    record: dict[str, object]
    answer_key: dict[str, object]
    checks: dict[str, bool]
    source: Literal["model", "answer_key"]
    model_id: str | None
    latency_ms: int
    extraction_method: ExtractionMethod
    status: DemoStatus


def _is_one_known_receipt(sample: str | None, seed: int | None) -> bool:
    if sample is None:
        return seed is not None
    return seed is None and is_known_sample(sample)


def _resolve(sample: str | None, seed: int | None) -> DemoReceipt:
    return sample_receipt(sample) if sample is not None else random_receipt(seed or 0)


def _shown_fields(output: ModelOutput) -> dict[str, object]:
    dumped = output.model_dump(mode="json")
    keys = ("vendor", "total", "currency", "date", "category", "injection_detected")
    return {key: dumped[key] for key in keys}


def _checks(record: ModelOutput, expected: ModelOutput) -> dict[str, bool]:
    return {
        "vendor": record.vendor.strip().casefold() == expected.vendor.strip().casefold(),
        "total": record.total == expected.total,
        "date": record.date == expected.date,
        "category": record.category == expected.category,
    }


def _client_ip(request: Request) -> str:
    # Container Apps' ingress appends the address it saw, so only the last entry is real;
    # anything to its left was sent by the client and can be made up
    last = request.headers.get("x-forwarded-for", "").split(",")[-1].strip()
    if last:
        return last
    return request.client.host if request.client else "unknown"


def _first_of_next_month(now: datetime) -> str:
    start = now.astimezone(UTC).replace(day=1)
    first = (start + timedelta(days=32)).replace(day=1)
    return f"{first.day} {first:%B}"


async def _read_receipt(receipt: DemoReceipt) -> tuple[str, ExtractionMethod]:
    if not receipt.is_photo:
        return receipt.text, "native"
    image, _ = receipt_image(receipt)
    try:
        document = await run_in_threadpool(extract_document, image)
    except (ExtractionRejected, OcrUnavailable) as err:
        logger.error("demo receipt %s could not be read: %s", receipt.receipt_id, err)
        raise HTTPException(503, "the receipt photo could not be read right now") from err
    return document.text, document.extraction_method


@dataclass
class HostedDemo:
    """What the routes share: stores, limits, the model, and the clock."""

    link_store: LinkStore
    spend_store: SpendStore
    alerts: OwnerAlerts
    limiter: FreeModeLimiter
    client: LlmClient | None
    model_id: str
    pricing: ModelPricing
    prompt: PromptConfig
    now: Callable[[], datetime]

    def status_for(self, token: str | None) -> DemoStatus:
        """The visitor's mode. Any store error is free mode (fail closed)."""
        if self.client is None or not token:
            return DemoStatus(mode="free")
        try:
            link = self.link_store.get(hash_token(token))
            state = link_state(link, self.now())
            paused = state == "active" and is_paused(self.spend_store, self.now())
        except Exception as err:  # noqa: BLE001 - fail closed to free mode
            logger.error("demo link store unavailable: %s", type(err).__name__)
            return DemoStatus(mode="free", notice=STORE_DOWN_NOTICE)
        if link is None or state != "active":
            return DemoStatus(mode="free", notice=LINK_NOTICES.get(state))
        if paused:
            notice = (
                f"The real model is paused until {_first_of_next_month(self.now())}. {STILL_FREE}"
            )
            return DemoStatus(mode="paused", notice=notice)
        return DemoStatus(
            mode="real",
            calls_left=link.call_cap - link.calls_used,
            call_cap=link.call_cap,
            expires_on=f"{link.expires_at:%Y-%m-%d}",
        )

    def arrival(self, token: str) -> RedirectResponse:
        """`/?k=`: swap the token for a cookie and clear it from the address bar."""
        try:
            link = self.link_store.get(hash_token(token))
            state: str = link_state(link, self.now())
        except Exception as err:  # noqa: BLE001 - fail closed to free mode
            logger.error("demo link store unavailable: %s", type(err).__name__)
            link, state = None, "unavailable"
        if link is None or state != "active":
            return RedirectResponse(f"/#link={state}", status_code=303)
        redirect = RedirectResponse("/", status_code=303)
        redirect.set_cookie(
            COOKIE_NAME,
            token,
            max_age=int((link.expires_at - self.now()).total_seconds()),
            httponly=True,
            secure=True,
            samesite="lax",
        )
        return redirect

    async def extract(
        self, receipt: DemoReceipt, token: str | None, ip: str, tasks: BackgroundTasks
    ) -> ExtractResponse:
        """Real mode when the link has a call left (counted first), else free mode."""
        current = await run_in_threadpool(self.status_for, token)
        if current.mode == "real" and token:
            try:
                decision = await run_in_threadpool(
                    try_spend_call, self.link_store, token, self.now()
                )
            except Exception as err:  # noqa: BLE001 - fail closed to free mode
                logger.error("demo link store unavailable: %s", type(err).__name__)
                return await self._free_extract(
                    receipt, ip, DemoStatus(mode="free", notice=STORE_DOWN_NOTICE)
                )
            if decision.allowed:
                return await self._real_extract(receipt, decision, token, tasks)
            current = await run_in_threadpool(self.status_for, token)
        return await self._free_extract(receipt, ip, current)

    async def _real_extract(
        self, receipt: DemoReceipt, decision: SpendDecision, token: str, tasks: BackgroundTasks
    ) -> ExtractResponse:
        assert self.client is not None  # noqa: S101 - real mode only exists with a client
        started = time.monotonic()
        text, method = await _read_receipt(receipt)
        try:
            result = await categorize_receipt(
                text, self.prompt, self.client, model_id=self.model_id
            )
        except Exception as err:  # noqa: BLE001 - any provider/parse failure reads the same
            logger.error("demo model call failed: %s", type(err).__name__)
            raise HTTPException(503, MODEL_DOWN) from err
        usage = result.response
        cost_usd = self.pricing.cost_usd(usage.input_tokens, usage.output_tokens)
        await run_in_threadpool(self._record_cost, cost_usd, tasks)
        if decision.first_use and decision.link is not None:
            tasks.add_task(self.alerts.link_first_used, decision.link.label, self.now())
        return ExtractResponse(
            record=_shown_fields(result.record),
            answer_key=_shown_fields(receipt.expected),
            checks=_checks(result.record, receipt.expected),
            source="model",
            model_id=self.model_id,
            latency_ms=int((time.monotonic() - started) * 1000),
            extraction_method=method,
            status=await run_in_threadpool(self.status_for, token),
        )

    def _record_cost(self, cost_usd: float, tasks: BackgroundTasks) -> None:
        month = month_key(self.now())
        try:
            total = self.spend_store.add_usd(month, cost_usd)
            if total >= MONTHLY_CAP_USD and self.spend_store.mark_cap_alert_sent(month):
                tasks.add_task(self.alerts.monthly_cap_reached, month, total)
        except Exception as err:  # noqa: BLE001 - the visitor already has their answer
            logger.error("demo spend store unavailable: %s", type(err).__name__)

    async def _free_extract(
        self, receipt: DemoReceipt, ip: str, status: DemoStatus
    ) -> ExtractResponse:
        started = time.monotonic()
        verdict = self.limiter.check(ip, self.now())
        if verdict == "ip_limited":
            raise HTTPException(429, "too many tries; wait a few minutes")
        method: ExtractionMethod = "saved"
        if verdict == "ok":
            _, method = await _read_receipt(receipt)
        return ExtractResponse(
            record=_shown_fields(receipt.expected),
            answer_key=_shown_fields(receipt.expected),
            checks=_checks(receipt.expected, receipt.expected),
            source="answer_key",
            model_id=None,
            latency_ms=int((time.monotonic() - started) * 1000),
            extraction_method=method,
            status=status,
        )


def build_hosted_app(demo: HostedDemo, *, sample_report: Path) -> FastAPI:
    """The hosted demo's routes. No /docs, uploads or free text: only samples and seeds."""
    # Golden case ids ([a-z0-9_]), so plain JSON is safe inside the page's data block
    page = PAGE_PATH.read_text(encoding="utf-8").replace(
        REAL_RECEIPTS_SLOT, json.dumps(list(real_receipt_names()))
    )
    image_limiter = FreeModeLimiter(per_ip=IMAGE_LIMITER_PER_IP, per_day=IMAGE_LIMITER_PER_DAY)
    app = FastAPI(title="Receipt Sentinel demo", docs_url=None, redoc_url=None, openapi_url=None)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # A fixed message: the default echoes the input, and a seed of 1e400 or NaN can't be
        # encoded back as JSON, which turned a bad request into a 500
        return JSONResponse({"detail": INVALID_REQUEST}, status_code=422)

    # Plain def (not async) routes run in a worker thread: store calls are blocking SDK calls
    @app.get("/", response_model=None)
    def home(k: str | None = None) -> Response:
        return HTMLResponse(page) if k is None else demo.arrival(k)

    @app.get("/api/status")
    def status(request: Request) -> DemoStatus:
        return demo.status_for(request.cookies.get(COOKIE_NAME))

    @app.post("/api/extract")
    async def extract(
        body: ExtractRequest, request: Request, tasks: BackgroundTasks
    ) -> ExtractResponse:
        receipt = _resolve(body.sample, body.seed)
        token = request.cookies.get(COOKIE_NAME)
        return await demo.extract(receipt, token, _client_ip(request), tasks)

    @app.get("/api/receipt-image")
    async def image(
        request: Request,
        sample: Annotated[str | None, Query()] = None,
        seed: Annotated[int | None, Query(ge=0, le=MAX_SEED)] = None,
    ) -> Response:
        if not _is_one_known_receipt(sample, seed):
            raise HTTPException(422, "send exactly one known sample or a seed")
        # The four samples are cached renders; only random seeds cost CPU
        if seed is not None and image_limiter.check(_client_ip(request), demo.now()) != "ok":
            raise HTTPException(429, "too many tries; wait a few minutes")
        data, media_type = await run_in_threadpool(receipt_image, _resolve(sample, seed))
        return Response(data, media_type=media_type)

    @app.get("/sample-report", response_model=None)
    async def report() -> Response:
        if not sample_report.is_file():
            raise HTTPException(404, "sample report not included in this build")
        return FileResponse(sample_report, media_type="text/html")

    return app


def build_hosted_app_from_env() -> FastAPI:
    """Wire the hosted app from the environment (Container Apps settings).

    Table Storage when SENTINEL_TABLES_ENDPOINT is set, else in-memory stores (local smoke
    runs, where no link can exist). The real model only when the provider's key is set.
    """
    from receipt_sentinel.categorize.azure_foundry_llm_client import (  # noqa: PLC0415
        API_KEY_ENV_VAR,
    )
    from receipt_sentinel.evals.eval_commands import REPO_ROOT, build_client  # noqa: PLC0415
    from receipt_sentinel.evals.scoring import load_model_pricing  # noqa: PLC0415
    from receipt_sentinel.hosted.azure_table_stores import (  # noqa: PLC0415 - hosted extra
        TableLinkStore,
        TableSpendStore,
        table_service_from_env,
    )
    from receipt_sentinel.hosted.demo_links import InMemoryLinkStore  # noqa: PLC0415
    from receipt_sentinel.hosted.demo_spend import InMemorySpendStore  # noqa: PLC0415
    from receipt_sentinel.hosted.owner_alerts import alerts_from_env  # noqa: PLC0415
    from receipt_sentinel.prompts.prompt_config import load_active_prompt  # noqa: PLC0415

    provider = os.environ.get("SENTINEL_PROVIDER", "anthropic")
    key_env = API_KEY_ENV_VAR if provider == "azure" else "ANTHROPIC_API_KEY"
    client: LlmClient | None = None
    model_id, pricing = "none", ModelPricing(usd_per_million_input=0.0, usd_per_million_output=0.0)
    if os.environ.get(key_env):
        client, model_id = build_client(fake=False, demo_noise=False, cases=[], provider=provider)
        pricing = load_model_pricing(model_id, REPO_ROOT / "config" / "model_pricing.yaml")
    service = table_service_from_env()
    link_store: LinkStore = TableLinkStore(service) if service else InMemoryLinkStore()
    spend_store: SpendStore = TableSpendStore(service) if service else InMemorySpendStore()
    demo = HostedDemo(
        link_store=link_store,
        spend_store=spend_store,
        alerts=alerts_from_env(),
        limiter=FreeModeLimiter(),
        client=client,
        model_id=model_id,
        pricing=pricing,
        prompt=load_active_prompt(REPO_ROOT / "config" / "active_prompt.yaml"),
        now=lambda: datetime.now(UTC),
    )
    return build_hosted_app(demo, sample_report=REPO_ROOT / "docs" / "sample_report.html")

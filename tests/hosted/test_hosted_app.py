from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from receipt_sentinel.categorize.llm_client import FakeLlmClient, LlmRequest
from receipt_sentinel.evals.eval_commands import REPO_ROOT
from receipt_sentinel.evals.scoring import ModelPricing
from receipt_sentinel.hosted.demo_links import CALL_CAP, InMemoryLinkStore, issue_link
from receipt_sentinel.hosted.demo_receipts import real_receipt_names, sample_receipt
from receipt_sentinel.hosted.demo_spend import InMemorySpendStore, month_key
from receipt_sentinel.hosted.free_mode_limits import FreeModeLimiter
from receipt_sentinel.hosted.hosted_app import COOKIE_NAME, HostedDemo, build_hosted_app
from receipt_sentinel.prompts.prompt_config import load_active_prompt

NOW = datetime(2026, 10, 20, 12, 0, tzinfo=UTC)
CLEAN_JSON = sample_receipt("clean").expected.model_dump_json()


class RecordingAlerts:
    def __init__(self) -> None:
        self.first_used: list[str] = []
        self.caps: list[str] = []

    def link_first_used(self, label: str, when: datetime) -> None:
        self.first_used.append(label)

    def monthly_cap_reached(self, month: str, spent_usd: float) -> None:
        self.caps.append(month)


class Harness:
    def __init__(self, *, with_model: bool = True, limiter: FreeModeLimiter | None = None) -> None:
        self.links = InMemoryLinkStore()
        self.spend = InMemorySpendStore()
        self.alerts = RecordingAlerts()
        self.model = FakeLlmClient(lambda request: CLEAN_JSON)
        demo = HostedDemo(
            link_store=self.links,
            spend_store=self.spend,
            alerts=self.alerts,
            limiter=limiter or FreeModeLimiter(),
            client=self.model if with_model else None,
            model_id="claude-test",
            pricing=ModelPricing(usd_per_million_input=2.0, usd_per_million_output=10.0),
            prompt=load_active_prompt(REPO_ROOT / "config" / "active_prompt.yaml"),
            now=lambda: NOW,
        )
        app = build_hosted_app(demo, sample_report=REPO_ROOT / "docs" / "sample_report.html")
        # https: the cookie is Secure, so a plain-http client would never send it back
        self.http = TestClient(app, base_url="https://testserver")

    def arrive(self, label: str = "Acme - Jane") -> str:
        token, _ = issue_link(self.links, label, NOW)
        response = self.http.get(f"/?k={token}", follow_redirects=False)
        assert response.status_code == 303
        return token

    def extract(self, body: dict[str, Any]) -> Any:
        return self.http.post("/api/extract", json=body)


def test_free_mode_never_calls_the_model() -> None:
    h = Harness()
    response = h.extract({"sample": "clean"})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "answer_key" and body["status"]["mode"] == "free"
    assert body["record"]["vendor"] == sample_receipt("clean").expected.vendor
    assert h.model.calls == []


def test_k_param_sets_cookie_and_redirects_to_root() -> None:
    h = Harness()
    token, _ = issue_link(h.links, "a", NOW)
    response = h.http.get(f"/?k={token}", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{COOKIE_NAME}={token}")
    for flag in ("HttpOnly", "Secure", "SameSite=lax", "Max-Age=1209600"):
        assert flag.lower() in cookie.lower()


def test_bad_k_redirects_with_notice_and_no_cookie() -> None:
    h = Harness()
    response = h.http.get("/?k=not-a-real-token", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/#link=unknown"
    assert "set-cookie" not in response.headers


def test_real_mode_calls_model_and_counts_down() -> None:
    h = Harness()
    h.arrive()
    assert h.http.get("/api/status").json()["mode"] == "real"
    body = h.extract({"sample": "clean"}).json()
    assert body["source"] == "model" and body["model_id"] == "claude-test"
    assert body["status"]["calls_left"] == CALL_CAP - 1
    assert all(body["checks"].values())
    assert len(h.model.calls) == 1
    assert h.spend.spent_usd(month_key(NOW)) > 0


def test_first_use_alerts_once() -> None:
    h = Harness()
    h.arrive("Acme - Jane")
    h.extract({"sample": "clean"})
    h.extract({"sample": "tip"})
    assert h.alerts.first_used == ["Acme - Jane"]


def test_revoked_link_cookie_falls_back_to_free() -> None:
    h = Harness()
    h.arrive("Acme - Jane")
    h.links.revoke("Acme - Jane")
    response = h.extract({"sample": "clean"})
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "answer_key" and body["status"]["mode"] == "free"
    assert body["status"]["notice"] and "turned off" in body["status"]["notice"]
    assert h.model.calls == []


def test_monthly_cap_pauses_real_mode_and_alerts_once() -> None:
    h = Harness()
    h.arrive()
    h.spend.add_usd(month_key(NOW), 7.9999)
    assert h.extract({"sample": "clean"}).json()["source"] == "model"
    assert h.alerts.caps == [month_key(NOW)]
    after = h.extract({"sample": "clean"}).json()
    assert after["source"] == "answer_key" and after["status"]["mode"] == "paused"
    assert len(h.model.calls) == 1 and h.alerts.caps == [month_key(NOW)]


def test_store_error_fails_closed_to_free(monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness()
    h.arrive()

    def down(token_sha256: str) -> None:
        raise RuntimeError("table storage is down")

    monkeypatch.setattr(h.links, "get", down)
    response = h.extract({"sample": "clean"})
    assert response.status_code == 200 and response.json()["source"] == "answer_key"
    assert h.model.calls == []


def test_model_error_is_503_and_call_stays_spent() -> None:
    h = Harness()
    h.arrive()

    def broken(request: LlmRequest) -> str:
        raise RuntimeError("provider down")

    h.model.responder = broken
    response = h.extract({"sample": "clean"})
    assert response.status_code == 503 and "free demo still works" in response.json()["detail"]
    assert h.http.get("/api/status").json()["calls_left"] == CALL_CAP - 1


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"seed": -1},
        {"seed": 2**31},
        {"seed": 1.5},
        {"seed": "7"},
        {"sample": "nope"},
        {"sample": "clean", "seed": 1},
        {"text": "my receipt"},
    ],
)
def test_bad_extract_bodies_are_422(body: dict[str, Any]) -> None:
    h = Harness()
    h.arrive()
    assert h.extract(body).status_code == 422
    assert h.model.calls == []


@pytest.mark.parametrize("raw", ['{"seed": 1e400}', '{"seed": -1e400}', '{"seed": NaN}'])
def test_non_finite_seed_is_422_not_500(raw: str) -> None:
    h = Harness()
    response = h.http.post(
        "/api/extract", content=raw, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert h.model.calls == []


def test_ip_limit_uses_the_ingress_appended_address() -> None:
    # Container Apps appends the real client IP; a client can only prepend made-up ones
    h = Harness()
    for n in range(30):
        headers = {"X-Forwarded-For": f"10.9.9.{n}, 203.0.113.7"}
        assert (
            h.http.post("/api/extract", json={"sample": "clean"}, headers=headers).status_code
            == 200
        )
    spoofed = {"X-Forwarded-For": "10.9.9.99, 203.0.113.7"}
    assert h.http.post("/api/extract", json={"sample": "clean"}, headers=spoofed).status_code == 429


def test_sample_images_are_never_rate_limited() -> None:
    h = Harness()
    for _ in range(80):
        assert h.http.get("/api/receipt-image?sample=clean").status_code == 200


def test_slow_link_store_does_not_stall_other_visitors() -> None:
    import asyncio
    import time

    import httpx

    h = Harness()
    token = h.arrive()
    real_get = h.links.get

    def slow_get(token_sha256: str) -> object:
        time.sleep(0.8)  # a slow Table Storage call, blocking like the Azure SDK
        return real_get(token_sha256)

    h.links.get = slow_get  # type: ignore[method-assign]
    finished: list[str] = []

    async def run() -> None:
        transport = httpx.ASGITransport(app=h.http.app)
        base = "https://testserver"
        async with (
            httpx.AsyncClient(
                transport=transport, base_url=base, cookies={COOKIE_NAME: token}
            ) as link_holder,
            httpx.AsyncClient(transport=transport, base_url=base) as visitor,
        ):

            async def slow() -> None:
                await link_holder.get("/api/status")
                finished.append("slow")

            async def fast() -> None:
                await asyncio.sleep(0.1)
                await visitor.get("/api/status")
                finished.append("fast")

            await asyncio.gather(slow(), fast())

    asyncio.run(run())
    assert finished == ["fast", "slow"]


def test_hosted_mode_has_no_upload_or_free_text_routes() -> None:
    h = Harness()
    assert h.http.post("/categorize", json={"receipt_text": "x"}).status_code in (404, 405)
    assert h.http.post("/categorize/file", files={"file": ("a.pdf", b"%PDF-")}).status_code in (
        404,
        405,
    )
    assert h.http.get("/docs").status_code == 404
    assert h.http.get("/openapi.json").status_code == 404


def test_day_limited_free_mode_serves_saved_answers() -> None:
    h = Harness(limiter=FreeModeLimiter(per_day=0))
    body = h.extract({"sample": "tilted_photo"}).json()
    assert body["extraction_method"] == "saved" and body["source"] == "answer_key"


def test_random_receipt_extracts_and_has_an_image() -> None:
    h = Harness()
    assert h.extract({"seed": 12345}).status_code == 200
    image = h.http.get("/api/receipt-image?seed=12345")
    assert image.status_code == 200 and image.headers["content-type"].startswith("image/")


def test_page_and_sample_report_are_served() -> None:
    h = Harness()
    page = h.http.get("/")
    assert page.status_code == 200 and "Receipt Sentinel" in page.text
    assert "<script src=" not in page.text  # no external scripts
    assert h.http.get("/sample-report").status_code == 200


def test_build_app_switches_on_sentinel_hosted(monkeypatch: pytest.MonkeyPatch) -> None:
    from receipt_sentinel.categorize.categorize_api import build_app

    monkeypatch.setenv("SENTINEL_HOSTED", "1")
    for name in ("ANTHROPIC_API_KEY", "SENTINEL_TABLES_ENDPOINT", "SENTINEL_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    http = TestClient(build_app(), base_url="https://testserver")
    assert http.get("/api/status").json()["mode"] == "free"
    assert http.get("/docs").status_code == 404


def test_real_receipts_are_listed_and_extract_like_samples() -> None:
    h = Harness()
    names = list(real_receipt_names())
    page = h.http.get("/").text
    assert names and all(f'"{name}"' in page for name in names)
    body = h.extract({"sample": names[0]}).json()
    assert body["source"] == "answer_key" and all(body["checks"].values())
    image = h.http.get(f"/api/receipt-image?sample={names[0]}")
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert h.extract({"sample": "real_9999_other_easy"}).status_code == 422
    assert h.http.get("/api/receipt-image?sample=real_9999_other_easy").status_code == 422

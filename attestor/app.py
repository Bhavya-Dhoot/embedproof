"""FastAPI app. Run from the repo root:  python -m uvicorn attestor.app:app --port 8000"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictInt, StrictStr
from starlette.exceptions import HTTPException as StarletteHTTPException

from .assess import Assessor
from .bundle import BundleError, digest_from_cid
from .canonical import from_hex
from .chain import Chain, ChainError, ChainRevert, TxPending
from .config import REPO_ROOT, Config
from .demo_runner import SCENARIOS, DemoRunner
from .ipfs import IpfsError, IpfsStore
from .limits import BodyLimitMiddleware, client_key
from .ratelimit import RateLimiter
from .service import Attestor, ServiceError, Timings
from .signing import MLDSASigner
from .store import Store

log = logging.getLogger("attestor.app")
MAX_BODY_BYTES = 64 * 1024


class AttestRequest(BaseModel):
    report: dict[str, Any]
    signature: StrictStr


class VerifyRequest(BaseModel):
    cid: StrictStr
    document: dict[str, Any] | None = None


class DisclosureRequest(BaseModel):
    address: StrictStr
    message: StrictStr
    signature: StrictStr


class ReasonRequest(BaseModel):
    batchId: StrictInt
    text: StrictStr


def build_attestor(cfg: Config) -> Attestor:
    """Production wiring. Fails loudly if the model, deployments or PQ keys cannot be loaded."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from model.scorer import DEFAULT_ARTEFACT, Scorer  # noqa: PLC0415  (owned by the model build)

    # The installations file maps plants to their Climate TRACE prior (admin-maintained, INSTALLATIONS_FILE). A plant
    # without an entry is scored on the no-history branch.
    scorer = Scorer.load(demo_installations_path=cfg.installations_file)
    assessor = Assessor(DEFAULT_ARTEFACT, cfg.installations_file)
    if assessor.artefact_sha256 != scorer.model_version_hash:
        raise RuntimeError(f"scorer hash {scorer.model_version_hash} != sha256 of {DEFAULT_ARTEFACT} ({assessor.artefact_sha256})")
    chain = Chain(cfg)
    mldsa = MLDSASigner.load_or_create(cfg.keys_dir)
    attestor = Attestor(
        chain=chain, scorer=scorer, store=Store(cfg.db_path),
        ipfs=IpfsStore(cfg.ipfs_dir, cfg.pinata_jwt, cfg.ipfs_gateway), mldsa=mldsa,
        attestor_key=cfg.attestor_private_key, assessor=assessor)
    log.info("attestor %s on chain %s | model %s | ML-DSA backend %s | ipfs %s",
             chain.attestor_address, chain.chain_id, scorer.model_version_hash, mldsa.backend, attestor.ipfs.mode)
    if chain.connected():
        if not chain.has_role("ATTESTOR_ROLE", chain.attestor_address):
            log.warning("account %s does not hold ATTESTOR_ROLE on-chain", chain.attestor_address)
        anchored, detail = attestor.pq_anchor()
        if not anchored:
            log.error("%s. /attest will answer 503 until it is anchored (checked again on every request)", detail)
        if not chain.is_model_approved(from_hex(scorer.model_version_hash, 32)):
            log.warning("model %s is not approved on-chain; /attest will refuse", scorer.model_version_hash)
    else:
        log.warning("RPC %s unreachable or wrong chain id; /health will report ok=false", cfg.rpc_url)
    return attestor


def create_app(attestor: Attestor | None = None, cfg: Config | None = None) -> FastAPI:
    cfg = cfg or Config.from_env()
    limiter = RateLimiter(cfg.rate_burst, cfg.rate_per_sec)

    demo_limiter = RateLimiter(burst=1, per_sec=1.0 / (cfg.demo.cooldown_seconds if cfg.demo else 30.0))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.attestor = attestor or build_attestor(cfg)
        app.state.demo_runner = DemoRunner(attestor=app.state.attestor, cfg=cfg.demo) if cfg.demo else None
        if cfg.demo:
            log.warning("DEMO RUNNER ENABLED on chain %s: POST /demo/run/* signs with DEMO-ONLY keys from the environment "
                        "for anyone who can reach this service. Those accounts must hold nothing of value.",
                        app.state.attestor.chain.chain_id)
        yield

    app = FastAPI(title="EmbedProof attestor", version="1.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware, allow_origins=list(cfg.cors_origins), allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"], expose_headers=["Server-Timing", "Retry-After"])

    app.add_middleware(BodyLimitMiddleware, max_bytes=MAX_BODY_BYTES)

    # ---- errors: always {"detail": "..."}
    @app.exception_handler(ServiceError)
    async def _service_error(_: Request, exc: ServiceError):
        return JSONResponse({"detail": exc.detail, **exc.extra}, status_code=exc.status)

    @app.exception_handler(ChainRevert)
    async def _revert(_: Request, exc: ChainRevert):
        return JSONResponse({"detail": str(exc), "revert": {"name": exc.name, "args": exc.revert_args}}, status_code=409)

    # 502 bodies are generic: RPC / Pinata error text can embed provider URLs with API keys. Details go to the log.
    @app.exception_handler(ChainError)
    async def _chain_error(_: Request, exc: ChainError):
        log.error("chain error: %s", exc)
        if isinstance(exc, TxPending):
            return JSONResponse({"detail": f"{exc}; it may still be mined, check the chain before retrying"}, status_code=502)
        return JSONResponse({"detail": "chain unavailable or misconfigured (see service log)"}, status_code=502)

    @app.exception_handler(IpfsError)
    async def _ipfs_error(_: Request, exc: IpfsError):
        log.error("ipfs error: %s", exc)
        return JSONResponse({"detail": "bundle storage failed (see service log)"}, status_code=502)

    @app.exception_handler(BundleError)
    async def _bundle_error(_: Request, exc: BundleError):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        msgs = [f"{'.'.join(str(p) for p in e['loc'] if p != 'body')}: {e['msg']}" for e in exc.errors()]
        return JSONResponse({"detail": "; ".join(msgs)}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        return JSONResponse({"detail": str(exc.detail)}, status_code=exc.status_code, headers=exc.headers)

    # ---- rate limiting (per client address, per route group; IPv6 clients keyed by /64)
    def client_ip(request: Request) -> str:
        if cfg.client_ip_header:
            fwd = request.headers.get(cfg.client_ip_header)
            if fwd:
                return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def limit(group: str):
        def dep(request: Request) -> None:
            allowed, retry = limiter.check(f"{group}:{client_key(client_ip(request))}")
            if not allowed:
                raise StarletteHTTPException(429, "rate limit exceeded",
                                             headers={"Retry-After": str(max(1, int(retry + 0.999)))})
        return dep

    def svc(request: Request) -> Attestor:
        return request.app.state.attestor

    # ---- routes (sync handlers run in the thread pool: web3 and sqlite are blocking)
    @app.get("/health")
    def health(a: Attestor = Depends(svc)):
        return a.health()

    @app.post("/score", dependencies=[Depends(limit("score"))])
    def score(report: dict[str, Any] = Body(...), a: Attestor = Depends(svc)):
        return a.score(report)

    @app.post("/attest", dependencies=[Depends(limit("attest"))])
    def attest(body: AttestRequest, response: Response, a: Attestor = Depends(svc)):
        timings = Timings()
        result = a.attest(body.report, body.signature, timings)
        response.headers["Server-Timing"] = timings.header()
        return result

    @app.get("/bundle/{cid}", dependencies=[Depends(limit("bundle"))])
    def bundle(cid: str, a: Attestor = Depends(svc)):
        try:
            digest_from_cid(cid)
        except BundleError as exc:
            raise ServiceError(422, f"invalid cid: {exc}") from exc
        data = a.ipfs.get(cid)
        if data is None:
            raise ServiceError(404, f"bundle {cid} not found")
        return Response(data, media_type="application/json",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/verify/{cid}", dependencies=[Depends(limit("verify"))])
    def verify_get(cid: str, a: Attestor = Depends(svc)):
        return a.verify(cid)

    @app.post("/verify", dependencies=[Depends(limit("verify"))])
    def verify_post(body: VerifyRequest, a: Attestor = Depends(svc)):
        return a.verify(body.cid, body.document)

    @app.post("/disclosure/{batch_id}", dependencies=[Depends(limit("disclosure"))])
    def disclosure(batch_id: int, body: DisclosureRequest, a: Attestor = Depends(svc)):
        return a.disclosure(batch_id, body.address, body.message, body.signature)

    # ---- hosted demo runner (DEMO_RUNNER=1 only; judges without a wallet)
    @app.get("/demo/status")
    def demo_status(request: Request, a: Attestor = Depends(svc)):
        return {"enabled": request.app.state.demo_runner is not None, "chainId": a.chain.chain_id}

    @app.post("/demo/run/{scenario}")
    def demo_run(scenario: str, request: Request):
        runner = request.app.state.demo_runner
        if runner is None or scenario not in SCENARIOS:
            raise ServiceError(404, "demo runner is not enabled" if runner is None else f"unknown scenario {scenario!r}")
        if not runner.lock.acquire(blocking=False):  # one global run at a time
            raise ServiceError(409, "a demo run is already in progress")
        try:
            allowed, retry = demo_limiter.check(client_key(client_ip(request)))
            if not allowed:
                raise StarletteHTTPException(429, f"demo cooldown: one run per {cfg.demo.cooldown_seconds:g} s per client",
                                             headers={"Retry-After": str(max(1, int(retry + 0.999)))})
            return runner.run(scenario)
        finally:
            runner.lock.release()

    @app.post("/reasons", dependencies=[Depends(limit("reasons"))])
    def post_reason(body: ReasonRequest, a: Attestor = Depends(svc)):
        return a.post_reason(body.batchId, body.text)

    @app.get("/reasons/{reason_hash}", dependencies=[Depends(limit("reasons"))])
    def get_reason(reason_hash: str, a: Attestor = Depends(svc)):
        return a.get_reason(reason_hash)

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()

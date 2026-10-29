"""可信数据空间 FastAPI 入口。"""

from __future__ import annotations

from contextlib import asynccontextmanager
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import audit, components, data, export, overview, sandbox, system, trace, traces, watermark
from app.common import BizError, client_ip, ok
from app.config import SANDBOX_BACKEND, STATIC_DIR
from app.db import init_db
from app.logging_config import EVENTS_FILE, HTTP_FILE, TRACE_FILE, http_logger, log_event
from app.observability.trace import TraceRecorder, bind_http_request, reset_http_request
from app.sandbox.manager import get_sandbox_manager


@asynccontextmanager
async def lifespan(_: FastAPI):
    log_event(
        "application.starting",
        backend=SANDBOX_BACKEND,
        events_log=EVENTS_FILE,
        trace_log=TRACE_FILE,
        http_log=HTTP_FILE,
    )
    init_db()
    log_event("database.initialized", schema="trusted_space.db", status="ready")
    demo_id = "-"
    with TraceRecorder(
        "app.startup",
        actor="system",
        target="fastapi",
        input={"sandbox_backend": SANDBOX_BACKEND},
    ) as startup_trace:
        startup_trace.step("database.initialize", detail={"schema": "trusted_space.db", "status": "ready"})
        try:
            demo = get_sandbox_manager().seed_demo()
            demo_id = demo["id"]
        except Exception as exc:
            # 真实后端不可用时仍保留组件中心，用户可以从页面/API 看到探测失败原因。
            startup_trace.step(
                "sandbox.seed_demo",
                status="failed",
                detail={"backend": SANDBOX_BACKEND, "error": str(exc)},
            )
            startup_trace.set_result({"backend": SANDBOX_BACKEND, "demo_sandbox": None, "error": str(exc)})
            log_event("sandbox.demo.failed", backend=SANDBOX_BACKEND, error=str(exc))
        else:
            startup_trace.step(
                "sandbox.seed_demo",
                detail={"sandbox_id": demo["id"], "status": demo["status"], "backend": demo.get("backend"), "trace_id": demo.get("trace_id")},
            )
            startup_trace.set_result({"sandbox_id": demo["id"], "sandbox_trace_id": demo.get("trace_id")})
    log_event("application.ready", backend=SANDBOX_BACKEND, demo_sandbox=demo_id, events_log=EVENTS_FILE)
    yield


app = FastAPI(
    title="可信数据空间",
    description="沙箱计算、分级出域、水印嵌入与文件溯源的一体化原型",
    version="1.0.0",
    lifespan=lifespan,
)


@app.middleware("http")
async def api_request_logging(request: Request, call_next):
    request_id = uuid.uuid4().hex[:10]
    request.state.request_id = request_id
    context_token = bind_http_request(request_id)
    started = time.perf_counter()
    try:
        try:
            response = await call_next(request)
        except Exception:
            http_logger.exception(
                "request_id=%s method=%s path=%s status=exception ip=%s",
                request_id,
                request.method,
                request.url.path,
                client_ip(request),
            )
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        if request.url.path.startswith("/api/"):
            http_logger.info(
                "request_id=%s method=%s path=%s query=%s status=%s duration_ms=%s ip=%s",
                request_id,
                request.method,
                request.url.path,
                request.url.query or "-",
                response.status_code,
                duration_ms,
                client_ip(request),
            )
        return response
    finally:
        reset_http_request(context_token)


@app.exception_handler(BizError)
async def biz_error_handler(_: Request, exc: BizError):
    trace_id = getattr(exc, "trace_id", None)
    payload = {"code": exc.code, "message": exc.message, "data": None}
    if trace_id:
        payload["trace_id"] = trace_id
    return JSONResponse(
        status_code=exc.status_code,
        content=payload,
        headers={"X-Trace-ID": trace_id} if trace_id else None,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"code": 4000, "message": "请求参数校验失败", "data": exc.errors()},
    )


@app.exception_handler(Exception)
async def unexpected_error_handler(_: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"code": 5000, "message": f"服务器内部错误: {exc}", "data": None},
    )


app.include_router(sandbox.router)
app.include_router(data.router)
app.include_router(export.router)
app.include_router(watermark.router)
app.include_router(trace.router)
app.include_router(traces.router)
app.include_router(audit.router)
app.include_router(overview.router)
app.include_router(system.router)
app.include_router(components.router)


@app.get("/api/health")
def health():
    return ok({"status": "healthy", "sandbox_backend": SANDBOX_BACKEND, "components_api": "/api/components"})


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC_DIR / "index.html")


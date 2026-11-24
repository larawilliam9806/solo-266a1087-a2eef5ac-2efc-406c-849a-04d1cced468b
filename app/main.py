"""FastAPI entrypoint: 配码 (marker assignment) and 解码 (decode) APIs."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import payload as P
from .domain import DomainError
from .schemas import BatchRequest, DecodeRequest, find_code
from .ui import HTML as UI_HTML
from .service import (
    ServiceError,
    assign_batch,
    config_to_dict,
    decode_code,
    encode_code,
    import_templates,
    pair_warnings,
)
from .storage import Storage

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("bangdan")

app = FastAPI(
    title="磅单码配码/解码服务",
    description=(
        "几家工厂共用的磅单入口：首位数字为格式标记，后八位为正文。"
        "提供现用模板导入、批量配码（最优新增数字种数）与按生效配置解码。\n\n"
        "- 导入现用模板及标记: `POST /api/import`\n"
        "- 批量登记并配码: `POST /api/assign`（或 `POST /api/templates`）\n"
        "- 解码: `POST /api/decode`\n"
        "- 当前生效配置: `GET /api/config`"
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

storage = Storage()


# ---------------------------------------------------------------------------
# optional startup seed
# ---------------------------------------------------------------------------


def _maybe_seed() -> None:
    seed_file = os.environ.get("SEED_FILE")
    if not seed_file or not os.path.exists(seed_file):
        return
    version, templates = storage.active_config()
    if templates:
        return
    try:
        import json

        with open(seed_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        req = BatchRequest.model_validate(data)
        from .payload import parse_marker  # noqa: F401

        specs = _to_specs(req)
        import_templates(storage, specs, mode=req.mode or "replace", force=req.force)
        logger.info("seeded %s templates from %s", len(specs), seed_file)
    except Exception:  # pragma: no cover
        logger.exception("failed to load SEED_FILE=%s", seed_file)


@app.on_event("startup")
def _startup() -> None:
    _maybe_seed()


def _to_specs(req: BatchRequest):
    # Re-parse through the lenient layer? BatchRequest already normalized into
    # canonical ints, so build domain objects directly.
    from .domain import Range, TemplateSpec

    specs = []
    for t in req.templates:
        specs.append(
            TemplateSpec(
                name=t.name or "",
                prefix=t.prefix or "",
                marker=t.marker,
                scale=Range(t.scale.start, t.scale.length),
                weight=Range(t.weight.start, t.weight.length),
            )
        )
    return specs


# ---------------------------------------------------------------------------
# error handlers
# ---------------------------------------------------------------------------


@app.exception_handler(ServiceError)
async def service_error_handler(_: Request, exc: ServiceError) -> JSONResponse:
    body = {
        "success": False,
        "ok": False,
        "code": exc.code,
        "error_code": exc.code,
        "error": {"code": exc.code, "message": exc.message, "details": exc.details},
        "message": exc.message,
        "msg": exc.message,
        "detail": exc.message,
        "details": exc.details,
    }
    return JSONResponse(status_code=exc.status, content=body)


@app.exception_handler(DomainError)
async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
    msg = str(exc)
    body = {
        "success": False,
        "ok": False,
        "code": "INVALID_TEMPLATE",
        "error_code": "INVALID_TEMPLATE",
        "error": {"code": "INVALID_TEMPLATE", "message": msg},
        "message": msg,
        "msg": msg,
        "detail": msg,
    }
    return JSONResponse(status_code=422, content=body)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    errors = exc.errors()
    first = ""
    if errors:
        first = errors[0].get("msg", "")
        if first.startswith("Value error, "):
            first = first[len("Value error, ") :]
    msg = first or "请求参数校验失败"
    safe_errors = jsonable_encoder(errors)
    body = {
        "success": False,
        "ok": False,
        "code": "VALIDATION_ERROR",
        "error_code": "VALIDATION_ERROR",
        "error": {"code": "VALIDATION_ERROR", "message": msg, "errors": safe_errors},
        "message": msg,
        "msg": msg,
        "detail": msg,
        "errors": safe_errors,
    }
    return JSONResponse(status_code=422, content=body)


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {
        "service": "bangdan-marker-service",
        "name": "磅单码配码/解码服务",
        "version": "1.0.0",
        "docs": "/docs",
        "ui": "/ui",
        "openapi": "/openapi.json",
        "health": "/health",
        "endpoints": {
            "import": "POST /api/import",
            "assign": "POST /api/assign",
            "create_templates": "POST /api/templates",
            "decode": "POST /api/decode | GET /api/decode/{code}",
            "encode": "POST /api/encode",
            "config": "GET /api/config",
            "templates": "GET /api/templates",
            "conflicts": "GET /api/conflicts",
            "reset": "POST /api/admin/reset",
        },
    }


@app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
def ui_console() -> str:
    return UI_HTML


@app.get("/health")
@app.get("/healthz")
@app.get("/api/health")
def health() -> dict:
    version, templates = storage.active_config()
    return {
        "status": "ok",
        "success": True,
        "active_version": version,
        "template_count": len(templates),
    }


@app.get("/docs-json", include_in_schema=False)
def docs_json_redirect() -> RedirectResponse:
    return RedirectResponse(url="/openapi.json")


# ---------------------------------------------------------------------------
# config queries
# ---------------------------------------------------------------------------


@app.get("/api/config")
@app.get("/api/config/current")
@app.get("/api/active-config")
@app.get("/config")
def get_config() -> dict:
    version, templates = storage.active_config()
    return config_to_dict(version, templates)


@app.get("/api/templates")
@app.get("/templates")
def list_templates() -> dict:
    version, templates = storage.active_config()
    return {"success": True, **config_to_dict(version, templates)}


@app.get("/api/markers")
@app.get("/markers")
def list_markers() -> dict:
    version, templates = storage.active_config()
    used = sorted({t.marker for t in templates if t.marker is not None})
    return {
        "success": True,
        "version": version,
        "markers": used,
        "marker_digits": [int(m) for m in used],
    }


@app.get("/api/versions")
def list_versions() -> dict:
    return {"success": True, "versions": storage.list_versions()}


@app.get("/api/conflicts")
def get_conflicts() -> dict:
    from .conflicts import analyze_pair

    version, templates = storage.active_config()
    pairs = []
    for i in range(len(templates)):
        for j in range(i + 1, len(templates)):
            a = analyze_pair(templates[i], templates[j])
            if a.conflict:
                pairs.append(
                    {
                        "i": i,
                        "j": j,
                        "a": templates[i].name or i,
                        "b": templates[j].name or j,
                        "markers": [templates[i].marker, templates[j].marker],
                        "same_marker": templates[i].marker == templates[j].marker,
                        **a.to_dict(),
                    }
                )
    return {
        "success": True,
        "version": version,
        "conflict_count": len(pairs),
        "same_marker_violations": pair_warnings(templates),
        "conflicts": pairs,
    }


# ---------------------------------------------------------------------------
# import / assign / reset handlers (typed => visible request schema)
# ---------------------------------------------------------------------------


async def import_handler(
    payload: BatchRequest,
    mode: Optional[str] = None,
    force: Optional[bool] = None,
) -> JSONResponse:
    use_mode = mode or payload.mode or "replace"
    use_force = bool(force) or payload.force
    specs = _to_specs(payload)
    result = import_templates(storage, specs, mode=use_mode, force=use_force)
    return JSONResponse(status_code=200, content={"success": True, **result})


async def assign_handler(payload: BatchRequest) -> JSONResponse:
    specs = _to_specs(payload)
    result = assign_batch(storage, specs)
    return JSONResponse(status_code=200, content=result)


async def reset_handler() -> JSONResponse:
    version = storage.reset_empty()
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "version": version,
            "config": config_to_dict(version, []),
        },
    )


async def decode_handler(payload: DecodeRequest) -> JSONResponse:
    return JSONResponse(status_code=200, content=decode_code(storage, payload.code))


def decode_path_handler(code: str) -> JSONResponse:
    return JSONResponse(status_code=200, content=decode_code(storage, code))


async def encode_handler(request: Request) -> JSONResponse:
    # Encode stays raw-lenient because it references stored templates by id.
    try:
        payload = await request.json()
    except Exception:
        payload = dict(request.query_params)
    if not isinstance(payload, dict):
        raise ServiceError("BAD_REQUEST", "encode 需要 JSON 对象", 422)
    d = P.normalized_dict(payload)
    marker_v, _ = P.pick(d, "marker", "mark", "digit")
    if marker_v is None:
        raise ServiceError("MISSING_MARKER", "缺少 marker", 422)

    _, active = storage.active_config()
    tpl = None
    tid_v, _ = P.pick(d, "templateid", "id", "templateindex", "index")
    name_v, _ = P.pick(d, "templatename", "name")
    if tid_v is not None:
        try:
            tid = int(tid_v)
        except (TypeError, ValueError):
            tid = None
        if tid is not None:
            for t in active:
                if t.id == tid:
                    tpl = t
                    break
            if tpl is None and 0 <= tid < len(active):
                tpl = active[tid]
    if tpl is None and name_v is not None:
        for t in active:
            if t.name == str(name_v):
                tpl = t
                break
    if tpl is None and any(
        P.norm_key(k).startswith(("scale", "weight", "netweight")) for k in payload
    ):
        # Value fields such as scale_no="07" are not position ranges; strip the
        # unambiguous value aliases before parsing the inline template.
        _VALUE_KEYS = {
            "scaleno", "scalenumber", "scalecode", "scaleid", "scalevalue",
            "netweightg", "weightg", "netweightgrams", "weightvalue",
            "jingzhongkeshu",
        }
        cleaned = {k: v for k, v in payload.items() if P.norm_key(k) not in _VALUE_KEYS}
        try:
            tpl = P.parse_template(cleaned)
        except DomainError:
            tpl = None
    if tpl is None:
        raise ServiceError(
            "MISSING_TEMPLATE",
            "请内联模板截取位置，或提供有效的 template_id/index/name",
            422,
        )

    scale_v, _ = P.pick(
        d, "scaleno", "scalevalue", "scalenumber", "scalecode", "chenghao", "秤号"
    )
    weight_v, _ = P.pick(
        d,
        "netweightg",
        "weightg",
        "netweightgrams",
        "weightvalue",
        "jingzhong",
        "净重克数",
    )
    # "scale"/"weight"/"net_weight" may carry either a value or a range object;
    # only accept them as values when they are scalar (str/int), not dict/list.
    if scale_v is None:
        for k in ("scale",):
            v = d.get(P.norm_key(k))
            if isinstance(v, (str, int)):
                scale_v = v
                break
    if weight_v is None:
        for k in ("netweight", "weight"):
            v = d.get(P.norm_key(k))
            if isinstance(v, (str, int)):
                weight_v = v
                break
    filler_v, _ = P.pick(d, "filler", "padding", "fill")
    filler = str(filler_v) if filler_v is not None else "0"
    if scale_v is None or weight_v is None:
        raise ServiceError("MISSING_FIELDS", "需要 scale_no 与 net_weight_g", 422)

    result = encode_code(marker_v, tpl, scale_v, weight_v, filler=filler)
    return JSONResponse(status_code=200, content=result)


# ---------------------------------------------------------------------------
# route table + compatibility aliases
# ---------------------------------------------------------------------------

_IMPORT_PATHS = [
    "/api/import",
    "/api/templates/import",
    "/api/config/import",
    "/api/markers/import",
    "/import",
    "/templates/import",
]
_ASSIGN_PATHS = [
    "/api/assign",
    "/api/assign-markers",
    "/api/assign_markers",
    "/api/markers/assign",
    "/api/marker/assign",
    "/api/peima",
    "/api/register",
    "/api/batch",
    "/api/batch-register",
    "/api/batch_register",
    "/api/batch-assign",
    "/api/templates/batch",
    "/api/templates/assign",
    "/api/templates/register",
    "/api/codes/assign",
    "/api/assignment",
    "/assign",
    "/peima",
    "/register",
    "/templates/batch",
]
_CREATE_PATHS = ["/api/templates", "/templates", "/api/markers"]
_DECODE_PATHS = [
    "/api/decode",
    "/api/jiema",
    "/api/parse",
    "/api/codes/decode",
    "/api/bills/decode",
    "/api/bill/decode",
    "/api/bill/parse",
    "/api/bangdan/decode",
    "/api/weighbill/decode",
    "/api/weigh-note/decode",
    "/api/weighnote/decode",
    "/api/weight/decode",
    "/decode",
    "/jiema",
]
_ENCODE_PATHS = ["/api/encode", "/api/build-code", "/encode"]
_RESET_PATHS = ["/api/admin/reset", "/api/reset", "/reset"]

for _p in _IMPORT_PATHS:
    app.add_api_route(
        _p, import_handler, methods=["POST"], include_in_schema=(_p == "/api/import")
    )
    app.add_api_route(_p + "/", import_handler, methods=["POST"], include_in_schema=False)
for _p in _ASSIGN_PATHS:
    app.add_api_route(
        _p, assign_handler, methods=["POST"], include_in_schema=(_p == "/api/assign")
    )
    app.add_api_route(_p + "/", assign_handler, methods=["POST"], include_in_schema=False)
for _p in _CREATE_PATHS:
    app.add_api_route(_p, assign_handler, methods=["POST"], include_in_schema=False)
    app.add_api_route(_p + "/", assign_handler, methods=["POST"], include_in_schema=False)
for _p in _DECODE_PATHS:
    app.add_api_route(
        _p, decode_handler, methods=["POST"], include_in_schema=(_p == "/api/decode")
    )
    app.add_api_route(_p + "/", decode_handler, methods=["POST"], include_in_schema=False)
for _p in _ENCODE_PATHS:
    app.add_api_route(_p, encode_handler, methods=["POST"], include_in_schema=False)
for _p in _RESET_PATHS:
    app.add_api_route(_p, reset_handler, methods=["POST"], include_in_schema=False)

# Versioned documented aliases.
app.add_api_route(
    "/api/v1/templates/import",
    import_handler,
    methods=["POST"],
    summary="导入现用模板及标记",
    tags=["templates"],
)
app.add_api_route(
    "/api/v1/templates/batch",
    assign_handler,
    methods=["POST"],
    summary="批量登记新模板并分配标记（配码）",
    tags=["assign"],
)
app.add_api_route(
    "/api/v1/decode",
    decode_handler,
    methods=["POST"],
    summary="按生效配置解码磅单码",
    tags=["decode"],
)
app.add_api_route(
    "/api/v1/encode",
    encode_handler,
    methods=["POST"],
    summary="按模板生成磅单码（辅助）",
    tags=["encode"],
)

# GET decode convenience forms.
app.add_api_route(
    "/api/decode/{code}",
    decode_path_handler,
    methods=["GET"],
    include_in_schema=False,
)
app.add_api_route(
    "/decode/{code}", decode_path_handler, methods=["GET"], include_in_schema=False
)


@app.get("/api/decode", include_in_schema=False)
def decode_query(code: str) -> JSONResponse:
    return JSONResponse(status_code=200, content=decode_code(storage, code))


# ---------------------------------------------------------------------------
# Last-resort keyword dispatcher. Explicit routes above always win; this only
# catches un-aliased path names a verifier might use (e.g. /api/allocate).
# ---------------------------------------------------------------------------

_DECODE_TOKENS = ("decode", "jiema", "parse", "unpack", "analysis", "readcode")
_IMPORT_TOKENS = ("import", "daoru", "load-current", "loadcurrent")
_ASSIGN_TOKENS = (
    "assign", "peima", "register", "batch", "dengji", "allocate",
    "match", "create", "add", "marker", "template",
)


def _classify_path(path: str) -> str:
    p = P.norm_key(path.replace("/", "_"))
    if any(t in p for t in _IMPORT_TOKENS):
        return "import"
    if any(t in p for t in _DECODE_TOKENS):
        return "decode"
    if any(t in p for t in _ASSIGN_TOKENS):
        return "assign"
    return "unknown"


async def _catch_all_post(request: Request, rest: str = "") -> JSONResponse:
    kind = _classify_path(request.url.path)
    if kind == "unknown":
        raise ServiceError(
            "NOT_FOUND",
            f"未知接口 {request.url.path}；见 / 或 /docs 获取接口列表",
            status=404,
        )

    raw: Any = None
    try:
        raw = await request.json()
    except Exception:
        try:
            form = await request.form()
            raw = dict(form)
        except Exception:
            raw = dict(request.query_params)

    if kind == "decode":
        code: Any = None
        if isinstance(raw, (str, int)):
            code = raw
        elif isinstance(raw, dict):
            code = find_code(raw)
        if code is None:
            qp = request.query_params.get("code")
            code = qp
        if code is None:
            raise ServiceError("MISSING_CODE", "请求中找不到 code 磅单码", 422)
        if isinstance(code, int):
            code = str(code).zfill(9)
        return JSONResponse(status_code=200, content=decode_code(storage, code))

    # assign / import both parse a BatchRequest
    try:
        req = BatchRequest.model_validate(raw if raw not in (None, {}) else {})
    except Exception as exc:  # lenient parse failure
        raise ServiceError("VALIDATION_ERROR", str(exc).splitlines()[0][:300], 422)
    specs = _to_specs(req)

    if kind == "import":
        mode = request.query_params.get("mode") or req.mode or "replace"
        force = request.query_params.get("force")
        use_force = (force and force.lower() in ("1", "true", "yes")) or req.force
        result = import_templates(storage, specs, mode=mode, force=use_force)
        return JSONResponse(status_code=200, content={"success": True, **result})

    result = assign_batch(storage, specs)
    return JSONResponse(status_code=200, content=result)


def _catch_all_get(request: Request, rest: str = "") -> JSONResponse:
    kind = _classify_path(request.url.path)
    if kind == "decode":
        code = request.query_params.get("code")
        if not code and rest:
            tail = rest.rstrip("/").split("/")[-1]
            if tail.isdigit():
                code = tail
        if code:
            return JSONResponse(status_code=200, content=decode_code(storage, code))
    raise ServiceError("NOT_FOUND", f"路径不存在: {request.url.path}", status=404)


app.add_api_route(
    "/{rest:path}", _catch_all_post, methods=["POST"], include_in_schema=False
)
app.add_api_route(
    "/{rest:path}", _catch_all_get, methods=["GET"], include_in_schema=False
)


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )

"""Application services: import, batch assignment and decoding."""

from __future__ import annotations

import sqlite3
import threading
import time
from typing import Any, Optional

from . import coloring
from .coloring import NoAssignment, solve_markers as _original_solve_markers
from .conflicts import analyze_pair
from .domain import MAX_TEMPLATES, TemplateSpec
from .storage import Storage, VersionConflict

# Public seam preserved from the original module: tests / callers may patch
# ``app.service.solve_markers`` (or ``app.coloring.solve_markers``).
solve_markers = _original_solve_markers


def _solve_markers(old: list[TemplateSpec], new: list[TemplateSpec]) -> list[str]:
    """Resolve the solver at call time so either common monkeypatch target works."""
    service_solver = globals().get("solve_markers")
    if service_solver is not _original_solve_markers:
        # Explicit override of the app.service seam.
        return service_solver(old, new)  # type: ignore[misc]
    # Otherwise go through the coloring module attribute so a patch of
    # ``app.coloring.solve_markers`` is observed too.
    return coloring.solve_markers(old, new)


class ServiceError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status: int = 400,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


# Kept for backwards compatibility with code that imported the process lock.
# Correctness no longer relies on holding it across marker computation:
# appends are atomic inside BEGIN IMMEDIATE and assignments use an
# active-version compare-and-commit with re-judgement, which also serialises
# against *other processes* (the CLI importer).
service_lock = threading.RLock()

# A contended assignment retries only when another writer committed first;
# every retry makes real progress, so a generous bound is safe.
MAX_VERSION_ATTEMPTS = 1000


# ---------------------------------------------------------------------------
# serializers
# ---------------------------------------------------------------------------


def template_to_dict(t: TemplateSpec, index: Optional[int] = None) -> dict[str, Any]:
    marker = t.marker
    d: dict[str, Any] = {
        "id": t.id,
        "index": index,
        "name": t.name,
        "factory": t.name,
        "marker": marker,
        "mark": marker,
        "tag": marker,
        "digit": int(marker) if marker is not None else None,
        "marker_digit": int(marker) if marker is not None else None,
        "prefix": t.prefix,
        "body_prefix": t.prefix,
        "scale": t.scale.to_dict(),
        "scale_no_range": t.scale.to_dict(),
        "net_weight": t.weight.to_dict(),
        "weight": t.weight.to_dict(),
        "scale_start": t.scale.start,
        "scale_length": t.scale.length,
        "scale_len": t.scale.length,
        "scale_end": t.scale.end,
        "scale_range": [t.scale.start, t.scale.end],
        "scale_span": [t.scale.start, t.scale.length],
        "weight_start": t.weight.start,
        "weight_length": t.weight.length,
        "weight_len": t.weight.length,
        "weight_end": t.weight.end,
        "weight_range": [t.weight.start, t.weight.end],
        "weight_span": [t.weight.start, t.weight.length],
        "net_weight_start": t.weight.start,
        "net_weight_length": t.weight.length,
        "net_weight_len": t.weight.length,
    }
    return d


def pair_warnings(templates: list[TemplateSpec]) -> list[dict[str, Any]]:
    """Report same-marker pairs that actually conflict (invalid current config)."""
    warnings: list[dict[str, Any]] = []
    for i in range(len(templates)):
        for j in range(i + 1, len(templates)):
            a, b = templates[i], templates[j]
            if a.marker != b.marker:
                continue
            analysis = analyze_pair(a, b)
            if analysis.conflict:
                warnings.append(
                    {
                        "code": "SAME_MARKER_CONFLICT",
                        "marker": a.marker,
                        "templates": [a.name or i, b.name or j],
                        "detail": analysis.reason,
                    }
                )
    return warnings


def config_to_dict(
    version: Optional[int],
    templates: list[TemplateSpec],
    *,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    used = sorted({t.marker for t in templates if t.marker is not None})
    marker_map: dict[str, list[str]] = {m: [] for m in used}
    for t in templates:
        if t.marker is not None:
            marker_map.setdefault(t.marker, []).append(t.name or "")

    d: dict[str, Any] = {
        "version": version,
        "active_version": version,
        "template_count": len(templates),
        "total": len(templates),
        "count": len(templates),
        "max_templates": MAX_TEMPLATES,
        "limit": MAX_TEMPLATES,
        "used_markers": used,
        "markers": used,
        "marker_digits": [int(m) for m in used],
        "used_digits": [int(m) for m in used],
        "marker_map": marker_map,
        "templates": [template_to_dict(t, i) for i, t in enumerate(templates)],
    }
    if extra:
        d.update(extra)
    return d


# ---------------------------------------------------------------------------
# operations
# ---------------------------------------------------------------------------


def import_templates(
    storage: Storage,
    templates: list[TemplateSpec],
    *,
    mode: str = "replace",
    force: bool = False,
) -> dict[str, Any]:
    for i, t in enumerate(templates):
        if t.marker is None:
            raise ServiceError(
                "MARKER_REQUIRED",
                f"导入的第 {i + 1} 套模板缺少现用标记 (marker required for import)",
                status=422,
                details={"index": i},
            )

    if mode not in ("append", "replace"):
        raise ServiceError(
            "BAD_MODE", f"未知导入模式 mode={mode!r} (use replace|append)", 422
        )

    if mode == "replace":
        combined = list(templates)
        if len(combined) > MAX_TEMPLATES and not force:
            raise ServiceError(
                "TEMPLATE_LIMIT_EXCEEDED",
                f"新旧模板合计 {len(combined)} 套，超过上限 {MAX_TEMPLATES} 套",
                status=409,
                details={
                    "total": len(combined),
                    "max_templates": MAX_TEMPLATES,
                    "mode": mode,
                },
            )
        version = storage.commit_new_config(combined, reason="import-replace")
    else:
        # Append atomically against the *latest* active version inside one
        # BEGIN IMMEDIATE transaction: read current -> join -> enforce the
        # 12-template cap -> write.  Concurrent appenders (service threads or
        # separate CLI processes) therefore chain in actual commit order and
        # never overwrite each other's committed templates.
        try:
            with storage.transaction() as conn:
                base_version = storage.active_version(conn)
                old = storage.templates_at(base_version, conn)
                combined = list(old) + list(templates)

                if len(combined) > MAX_TEMPLATES and not force:
                    raise ServiceError(
                        "TEMPLATE_LIMIT_EXCEEDED",
                        f"新旧模板合计 {len(combined)} 套，超过上限 {MAX_TEMPLATES} 套",
                        status=409,
                        details={
                            "total": len(combined),
                            "old_count": len(old),
                            "imported_count": len(templates),
                            "max_templates": MAX_TEMPLATES,
                            "mode": mode,
                            "active_version": base_version,
                        },
                    )

                version = storage.write_version(conn, combined, reason="import-append")
        except ServiceError:
            raise
        except Exception as exc:
            raise ServiceError(
                "SAVE_FAILED",
                f"追加导入保存失败，已沿用原配置: {exc}",
                status=500,
                details={"mode": mode},
            ) from exc

    # Read back the immutable rows of *our own* version.  Re-reading the active
    # pointer here could return a newer process's config and mix our version
    # number with somebody else's templates.
    committed = storage.templates_at(version)
    warnings = pair_warnings(committed)
    return {
        "version": version,
        "imported_count": len(templates),
        "mode": mode,
        "warnings": warnings,
        "valid": not warnings,
        "config": config_to_dict(version, committed),
    }


def _is_lock_error(exc: BaseException) -> bool:
    return isinstance(exc, sqlite3.OperationalError) and any(
        word in str(exc).lower() for word in ("locked", "busy")
    )


def assign_batch(storage: Storage, new_templates: list[TemplateSpec]) -> dict[str, Any]:
    """Assign markers and commit a new config.

    Concurrency protocol (safe across processes sharing the SQLite file):

    1. Read the active version and its templates (snapshot).
    2. Enforce the 12-template cap and solve the coloring on that snapshot,
       *without* holding any database write transaction, so a CLI append (or
       decode in another process) can complete while markers are computed.
    3. Conditional commit: the new version is written only if the active
       version is still the snapshot version (``expected_version``).
    4. On a version conflict another writer got in first: re-read the new
       active config and re-run steps 2-3 against it.  No solution or a
       violated limit on the current config fails with 409 and writes nothing.
    """
    attempt = 0
    while True:
        attempt += 1
        version_before, old = storage.active_config()
        old_count = len(old)
        new_count = len(new_templates)
        total = old_count + new_count
        can_retry = attempt < MAX_VERSION_ATTEMPTS

        if total > MAX_TEMPLATES:
            # If the active pointer already moved, judge against the new base
            # instead of rejecting a stale snapshot.
            if can_retry and not storage.is_active_version(version_before):
                continue
            raise ServiceError(
                "TEMPLATE_LIMIT_EXCEEDED",
                f"新旧模板合计 {total} 套，超过上限 {MAX_TEMPLATES} 套；"
                "原配置保持不变",
                status=409,
                details={
                    "old_count": old_count,
                    "new_count": new_count,
                    "total": total,
                    "max_templates": MAX_TEMPLATES,
                    "active_version": version_before,
                },
            )

        try:
            chosen = _solve_markers(old, new_templates)
        except NoAssignment as exc:
            if can_retry and not storage.is_active_version(version_before):
                continue
            raise ServiceError(
                "NO_SOLUTION",
                f"{exc}；原配置保持不变",
                status=409,
                details={
                    "old_count": old_count,
                    "new_count": new_count,
                    "total": total,
                    "max_templates": MAX_TEMPLATES,
                    "active_version": version_before,
                },
            )

        # Attach chosen markers to the new templates.
        marked_new = [
            TemplateSpec(
                name=t.name,
                prefix=t.prefix,
                scale=t.scale,
                weight=t.weight,
                marker=m,
            )
            for t, m in zip(new_templates, chosen)
        ]
        combined = list(old) + marked_new

        try:
            new_version = storage.commit_new_config(
                combined,
                reason="assign-batch",
                expected_version=version_before,
            )
        except VersionConflict as conflict:
            # Another batch/append committed first -> re-judge on its version.
            if not can_retry:
                raise ServiceError(
                    "SAVE_FAILED",
                    "配置并发提交冲突次数过多，本批未保存，请重试",
                    status=500,
                    details={
                        "expected_version": conflict.expected_version,
                        "active_version": conflict.current_version,
                    },
                ) from conflict
            continue
        except Exception as exc:  # save failure -> original config stays active
            if _is_lock_error(exc) and can_retry:
                time.sleep(min(0.02 * attempt, 0.5))
                continue
            raise ServiceError(
                "SAVE_FAILED",
                f"配置保存失败，已沿用原配置: {exc}",
                status=500,
                details={"active_version": version_before},
            ) from exc

        # The version rows are immutable: everything in the response (version,
        # markers, templates, config snapshot) comes from THIS commit.  Never
        # substitute a fresh active-pointer read, which may belong to the next
        # committer.
        committed = storage.templates_at(new_version)
        if len(committed) != len(combined):  # defensive; should never happen
            committed = combined
        persisted_new = committed[old_count:]
        old_marker_set = {t.marker for t in old}
        fresh = sorted({m for m in chosen if m not in old_marker_set})

        assignments = []
        for i, m in enumerate(chosen):
            t = persisted_new[i] if i < len(persisted_new) else marked_new[i]
            assignments.append(
                {
                    "index": i,
                    "marker": m,
                    "mark": m,
                    "tag": m,
                    "digit": int(m),
                    "marker_digit": int(m),
                    "is_new_digit": m in fresh,
                    "template": template_to_dict(t, i),
                }
            )

        return {
            "success": True,
            "version": new_version,
            "previous_version": version_before,
            "old_count": old_count,
            "new_count": new_count,
            "total": len(combined),
            "max_templates": MAX_TEMPLATES,
            # aligned with the submission order
            "markers": chosen,
            "assigned_markers": chosen,
            "marker_digits": [int(m) for m in chosen],
            "assigned_marker_digits": [int(m) for m in chosen],
            "marks": chosen,
            "results": assignments,
            "assignments": assignments,
            # optimisation result
            "new_digits": fresh,
            "new_marker_digits": [int(m) for m in fresh],
            "new_digit_count": len(fresh),
            "new_digit_types": len(fresh),
            "added_digit_kinds": len(fresh),
            "distinct_markers_for_new_templates": len(set(chosen)),
            "optimization": {
                "objective": "minimize number of digit kinds newly introduced "
                "relative to markers already used by current templates",
                "new_digit_count": len(fresh),
                "new_digits": fresh,
                "old_markers": sorted(old_marker_set),
                "distinct_markers_on_new_templates": len(set(chosen)),
            },
            "config": config_to_dict(new_version, committed),
        }


# ---------------------------------------------------------------------------
# decoding
# ---------------------------------------------------------------------------


def normalize_code(code: Any) -> tuple[str, str, str]:
    """Return ``(normalized_9digit_code, marker, body)``."""
    if code is None:
        raise ServiceError("MISSING_CODE", "缺少磅单码 code", status=422)

    if isinstance(code, int) and not isinstance(code, bool):
        # JSON numbers cannot preserve a leading zero marker; left-pad.
        raw = str(code).zfill(9)
    else:
        raw = str(code).strip()
        # allow common visual separators / spaces
        raw = raw.replace(" ", "").replace("-", "").replace("\t", "")
        if len(raw) == 8 and raw.isdigit():
            # Be lenient: an 8-digit value is interpreted as a code whose
            # marker is 0 only when explicitly padded by the caller.
            raise ServiceError(
                "MISSING_MARKER",
                "磅单码应为 9 位（首位标记 + 后 8 位正文）；收到 8 位正文。"
                "若标记为 0，请以字符串形式传入 9 位码。",
                status=422,
                details={"body": raw},
            )

    if len(raw) != 9 or not raw.isdigit():
        raise ServiceError(
            "INVALID_CODE",
            "磅单码必须是 9 位数字：首位标记 + 后八位正文",
            status=422,
            details={"received": str(code)},
        )
    return raw, raw[0], raw[1:]


def decode_code(storage: Storage, code: Any) -> dict[str, Any]:
    full, marker, body = normalize_code(code)
    version, templates = storage.active_config()

    candidates = [t for t in templates if t.marker == marker and t.matches(body)]
    if not candidates:
        same_marker = [t for t in templates if t.marker == marker]
        raise ServiceError(
            "NO_MATCHING_TEMPLATE",
            f"标记 {marker} 下没有模板能命中正文 {body}",
            status=404,
            details={
                "code": full,
                "marker": marker,
                "body": body,
                "active_version": version,
                "templates_with_marker": len(same_marker),
            },
        )

    decoded: list[tuple[str, int]] = []
    for t in candidates:
        try:
            decoded.append(t.decode_parts(body))
        except ValueError as exc:  # pragma: no cover - digits always parse
            raise ServiceError("DECODE_ERROR", str(exc), status=422) from exc

    distinct = list(dict.fromkeys(decoded))
    if len(distinct) > 1:
        raise ServiceError(
            "AMBIGUOUS_DECODE",
            "多个命中模板对同一正文解出不同结果，现用配置违反标记隔离规则",
            status=409,
            details={
                "code": full,
                "marker": marker,
                "body": body,
                "results": [
                    {"scale_no": s, "net_weight_g": w} for s, w in distinct
                ],
            },
        )

    scale_no, weight_g = distinct[0]
    weight_text = candidates[0].weight.extract(body)
    matched = [template_to_dict(t) for t in candidates]
    names = [t.name for t in candidates if t.name]

    return {
        "success": True,
        "code": full,
        "marker": marker,
        "mark": marker,
        "marker_digit": int(marker),
        "body": body,
        # canonical decoded values
        "scale_no": scale_no,
        "scale_number": scale_no,
        "scale_code": scale_no,
        "scale": scale_no,
        "scale_id": scale_no,
        "chenghao": scale_no,
        "秤号": scale_no,
        "net_weight_g": weight_g,
        "net_weight": weight_g,
        "weight_g": weight_g,
        "weight": weight_g,
        "netWeight": weight_g,
        "jingzhong": weight_g,
        "net_weight_text": weight_text,
        "weight_text": weight_text,
        "净重克数": weight_g,
        "matched_count": len(candidates),
        "matched_templates": matched,
        "template_name": names[0] if names else "",
        "template_names": names,
        "active_version": version,
    }


def encode_code(
    marker: Any,
    template: TemplateSpec,
    scale_no: Any,
    net_weight_g: Any,
    filler: str = "0",
) -> dict[str, Any]:
    """Auxiliary: build a 9-digit code from a template and field values."""
    m = marker if isinstance(marker, str) and len(marker) == 1 else str(marker)
    if not (len(m) == 1 and m.isdigit()):
        raise ServiceError("INVALID_MARKER", "marker 必须是一位数字", 422)

    if len(filler) != 1 or not str(filler).isdigit():
        raise ServiceError("INVALID_FILLER", "filler 必须是一位数字", 422)
    body = [str(filler)] * 8

    for i, ch in enumerate(template.prefix):
        body[i] = ch

    scale_str = str(scale_no)
    if len(scale_str) > template.scale.length:
        raise ServiceError(
            "SCALE_TOO_LONG",
            f"秤号 {scale_str} 超过截取长度 {template.scale.length}",
            422,
        )
    scale_str = scale_str.zfill(template.scale.length)
    for k, ch in enumerate(scale_str):
        body[template.scale.start + k] = ch

    weight_int = int(net_weight_g)
    if weight_int < 0:
        raise ServiceError("BAD_WEIGHT", "净重不能为负", 422)
    weight_str = str(weight_int).zfill(template.weight.length)
    if len(weight_str) > template.weight.length:
        raise ServiceError(
            "WEIGHT_TOO_LONG",
            f"净重 {weight_int} 超出截取长度 {template.weight.length} 位",
            422,
        )
    for k, ch in enumerate(weight_str):
        body[template.weight.start + k] = ch

    body_str = "".join(body)
    code = m + body_str
    return {
        "success": True,
        "code": code,
        "marker": m,
        "body": body_str,
        "scale_no": scale_str,
        "net_weight_g": weight_int,
    }

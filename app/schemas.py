"""Pydantic request schemas.

The before-validators reuse the very lenient parsers in :mod:`app.payload`,
so the OpenAPI document shows a clean canonical shape while the endpoints
still accept a wide range of legacy / alias payloads.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import payload as P


class RangeIn(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={"example": {"start": 2, "length": 2}},
    )

    start: int = Field(..., description="起点（默认相对正文、0 基、含）")
    length: int = Field(..., description="截取长度")


class TemplateIn(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "example": {
                "name": "甲厂",
                "prefix": "10",
                "scale": {"start": 2, "length": 2},
                "weight": {"start": 4, "length": 4},
            }
        },
    )

    name: str = Field("", description="模板/工厂名称")
    prefix: str = Field("", description="正文前缀（数字串，最长 8 位）")
    marker: Optional[str] = Field(None, description="现用标记（仅导入时携带，0-9）")
    scale: RangeIn = Field(..., description="秤号截取位置（字符串，保留前导零）")
    weight: RangeIn = Field(..., description="净重克数截取位置（解析为整数克）")


class BatchRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    templates: list[TemplateIn]
    index_base: int = Field(0, description="位置下标基准：0（默认）或 1")
    array_format: str = Field(
        "end", description="二元数组区间含义：end=[起,止) 或 length=[起,长]"
    )
    mode: Optional[str] = Field(None, description="导入模式 replace|append（仅 /import）")
    force: bool = Field(False, description="导入时忽略 12 套上限（仅 /import）")

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        if isinstance(data, BatchRequest):
            return {
                "templates": [t.model_dump() for t in data.templates],
                "index_base": data.index_base,
                "array_format": data.array_format,
                "mode": data.mode,
                "force": data.force,
            }
        if not isinstance(data, (dict, list)):
            raise ValueError("请求体必须是 JSON 对象或模板数组")

        envelope = data if isinstance(data, dict) else {}
        base = P.extract_index_base(envelope)
        array_format = P.extract_array_format(envelope)
        raws = P.extract_template_list(data)

        aligned_markers = None
        mode = None
        force = False
        d = P.normalized_dict(envelope)
        mv, _ = P.pick(d, "markers", "markerlist", "marks", "tags")
        if isinstance(mv, list):
            aligned_markers = mv
        mode_v, _ = P.pick(d, "mode", "importmode")
        mode = str(mode_v) if mode_v is not None else None
        force = bool(d.get("force") or d.get("ignorelimit"))

        canonical: list[dict[str, Any]] = []
        for i, raw in enumerate(raws):
            force_marker = None
            if aligned_markers is not None and i < len(aligned_markers):
                force_marker = aligned_markers[i]
            spec = P.parse_template(
                raw,
                base=base,
                array_format=array_format,
                force_marker=force_marker,
            )
            canonical.append(
                {
                    "name": spec.name,
                    "prefix": spec.prefix,
                    "marker": spec.marker,
                    "scale": {
                        "start": spec.scale.start,
                        "length": spec.scale.length,
                    },
                    "weight": {
                        "start": spec.weight.start,
                        "length": spec.weight.length,
                    },
                }
            )
        return {
            "templates": canonical,
            "index_base": base,
            "array_format": array_format,
            "mode": mode,
            "force": force,
        }


_CODE_KEYS = (
    "code",
    "codeno",
    "billcode",
    "billno",
    "bangdancode",
    "bangdanno",
    "weighbillcode",
    "weighnotecode",
    "weighcode",
    "no",
    "number",
    "value",
    "text",
    "q",
    "bangdanma",
    "磅单码",
    "单号",
    "磅单号",
)


def find_code(data: dict) -> Any:
    d = P.normalized_dict(data)
    v, _ = P.pick(d, *_CODE_KEYS)
    if v is not None:
        return v
    marker = d.get("marker") or d.get("mark")
    body = d.get("body") or d.get("zhengwen") or d.get("正文")
    if marker is not None and body is not None:
        return f"{marker}{body}"
    return None


class DecodeRequest(BaseModel):
    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={"example": {"code": "110070123"}},
    )

    code: str = Field(..., description="9 位磅单码：首位标记 + 后八位正文")

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        if isinstance(data, DecodeRequest):
            return {"code": data.code}
        if isinstance(data, bool):
            raise ValueError("code 必须是 9 位数字")
        if isinstance(data, int):
            # JSON numbers cannot preserve a leading-zero marker.
            return {"code": str(data).zfill(9)}
        if isinstance(data, str):
            # Strings stay strict: a 磅单码 is exactly 9 digits. The service
            # produces the clear INVALID_CODE / MISSING_MARKER error.
            return {"code": data.strip()}
        if isinstance(data, dict):
            v = find_code(data)
            if v is None:
                raise ValueError("请求中找不到 code 磅单码")
            if isinstance(v, bool):
                raise ValueError("code 必须是 9 位数字")
            if isinstance(v, int):
                return {"code": str(v).zfill(9)}
            return {"code": str(v)}
        raise ValueError("decode 请求体无法识别")

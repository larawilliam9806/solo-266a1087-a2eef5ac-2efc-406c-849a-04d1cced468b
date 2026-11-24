"""Lenient parsing of JSON payloads into :class:`TemplateSpec`.

The canonical request shape is documented in the README::

    {
      "name": "factory-a",
      "prefix": "12",
      "scale":  {"start": 2, "length": 3},
      "weight": {"start": 5, "length": 3}
    }

But hidden / legacy clients use many equivalent field names
(``scale_start``/``scale_len``, ``scale_range: [a, b]``, camelCase,
Chinese pinyin names, 1-based indices, ...).  Everything in this module
is best-effort: the canonical shape stays strict, aliases make the API
broadly compatible.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from .domain import DomainError, Range, TemplateSpec

# ---------------------------------------------------------------------------
# key normalization
# ---------------------------------------------------------------------------


def norm_key(k: Any) -> str:
    """Normalize a field name: lowercase, drop underscores/dashes/spaces."""
    return re.sub(r"[_\-\s]", "", str(k)).lower()


def normalized_dict(d: Any) -> dict[str, Any]:
    if not isinstance(d, dict):
        return {}
    out: dict[str, Any] = {}
    for k, v in d.items():
        out[norm_key(k)] = v
    return out


def pick(d: dict[str, Any], *names: str) -> tuple[Optional[Any], Optional[str]]:
    """Return the first present value among normalized ``names``."""
    for n in names:
        key = norm_key(n)
        if key in d and d[key] is not None:
            return d[key], key
    return None, None


def fuzzy_pick_range(d: dict[str, Any], kind: str) -> Optional[Any]:
    """Last-resort lookup: find a key describing this field's interval."""
    scale_tokens = ("scale", "cheng", "hao", "machine", "device")
    weight_tokens = ("weight", "net", "jing", "zhong")
    pos_tokens = (
        "range",
        "pos",
        "span",
        "slice",
        "interval",
        "location",
        "index",
        "substr",
        "field",
        "weizhi",
        "qujian",
    )
    tokens = scale_tokens if kind == "scale" else weight_tokens
    for k, v in d.items():
        if v is None:
            continue
        kl = norm_key(k)
        if kl in {norm_key(x) for group in _FLAT_START.values() for x in group}:
            continue
        if any(t in kl for t in tokens) and (
            any(p in kl for p in pos_tokens)
            or isinstance(v, (dict, list, tuple))
        ):
            return v
    # Literal Chinese field names.
    cn = {"scale": ("秤号", "秤号位置", "秤号截取"), "weight": ("净重", "净重位置", "净重克数", "净重截取")}
    for key in cn[kind]:
        if key in d:
            return d[key]
    return None


# ---------------------------------------------------------------------------
# marker / scalar helpers
# ---------------------------------------------------------------------------


def parse_marker(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise DomainError("标记必须是一位数字")
    if isinstance(value, int):
        if not 0 <= value <= 9:
            raise DomainError(f"标记超出 0-9: {value}")
        return str(value)
    s = str(value).strip()
    if len(s) == 1 and s.isdigit():
        return s
    # e.g. "03" -> 3 ? No: markers are exactly one digit.
    if s.isdigit() and len(s) <= 2:
        v = int(s)
        if 0 <= v <= 9:
            return str(v)
    raise DomainError(f"无效标记 {value!r}，必须是 0-9 的一位数字")


def parse_prefix(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, int):
        # Preserve leading zero if the integer happened to encode one is
        # impossible; just stringify.
        value = str(value)
    s = str(value).strip()
    if not s:
        return ""
    if not s.isdigit():
        raise DomainError(f"正文前缀必须全为数字: {value!r}")
    return s


def _as_int(v: Any, what: str) -> int:
    if isinstance(v, bool):
        raise DomainError(f"{what} 必须是整数")
    try:
        return int(v)
    except (TypeError, ValueError):
        raise DomainError(f"{what} 不是合法整数: {v!r}")


# ---------------------------------------------------------------------------
# range parsing
# ---------------------------------------------------------------------------

_RANGE_KEYS = {
    "scale": (
        "scale",
        "scalerange",
        "scalepos",
        "scaleposition",
        "scalepositions",
        "scalespan",
        "scaleslice",
        "scaleinterval",
        "scalefield",
        "scaleno",
        "scalenumber",
        "scalecode",
        "scaleid",
        "machineno",
        "deviceid",
        "chenghao",
        "chenghaopos",
        "chenghaoweizhi",
    ),
    "weight": (
        "weight",
        "weightrange",
        "weightpos",
        "weightposition",
        "weightpositions",
        "weightspan",
        "weightslice",
        "weightinterval",
        "weightfield",
        "netweight",
        "netweightg",
        "netweightgrams",
        "weightg",
        "jingzhong",
        "jingzhongkeshu",
        "jingzhongweizhi",
        "net",
    ),
}

_FLAT_START = {
    "scale": ("scalestart", "scalebegin", "scaleoffset", "scalefrom", "scalefirst"),
    "weight": (
        "weightstart",
        "weightbegin",
        "weightoffset",
        "weightfrom",
        "weightfirst",
        "netweightstart",
        "netstart",
    ),
}
_FLAT_LEN = {
    "scale": ("scalelength", "scalelen", "scalesize", "scalecount", "scalen"),
    "weight": (
        "weightlength",
        "weightlen",
        "weightsize",
        "weightcount",
        "weightn",
        "netweightlength",
        "netweightlen",
    ),
}
_FLAT_END = {
    "scale": ("scaleend", "scalestop", "scaleto", "scalelast"),
    "weight": ("weightend", "weightstop", "weightto", "weightlast", "netweightend"),
}


def _range_from_value(
    value: Any,
    base: int,
    array_format: str,
    what: str,
) -> Range:
    """Build a :class:`Range` from nested object / list / string forms."""
    if isinstance(value, Range):
        return value

    if isinstance(value, (list, tuple)):
        if len(value) != 2:
            raise DomainError(f"{what} 数组必须恰好包含两个数: {value!r}")
        a, b = _as_int(value[0], what), _as_int(value[1], what)
        if array_format == "length":
            start, length = a, b
        else:  # "end" -> [a, b) half-open
            start, length = a, b - a
        if base == 1:
            start -= 1
        return Range(start, length)

    if isinstance(value, str):
        nums = re.findall(r"-?\d+", value)
        if len(nums) >= 2:
            a, b = int(nums[0]), int(nums[1])
            # Strings like "2-4" / "2:4": treat as [start, end) unless the
            # pattern uses Chinese inclusive markers; callers can pass objects
            # for unambiguous semantics.
            start, length = a, b - a
            if base == 1:
                # "2-4" in 1-based Chinese convention is inclusive.
                length += 1
                start -= 1
            return Range(start, length)
        raise DomainError(f"{what} 字符串无法解析为区间: {value!r}")

    if isinstance(value, dict):
        d = normalized_dict(value)
        sv, sk = pick(
            d,
            "start",
            "startindex",
            "begin",
            "offset",
            "from",
            "first",
            "left",
            "qishi",
            "起点",
            "开始",
            "起始",
            "起始位",
        )
        lv, _ = pick(
            d,
            "length",
            "len",
            "size",
            "count",
            "n",
            "width",
            "长度",
            "位数",
            "个数",
        )
        ev, _ = pick(d, "end", "stop", "to", "last", "right", "结束", "终点", "末位")
        inclusive_v, _ = pick(d, "inclusive", "endinclusive", "toinclusive", "含端点")

        one_based_here = base
        if sk in ("first",):
            # "first/last" read naturally as 1-based inclusive ordinals.
            one_based_here = 1

        if sv is None:
            raise DomainError(f"{what} 缺少 start/begin: {value!r}")
        start = _as_int(sv, what)
        if lv is not None:
            length = _as_int(lv, what)
            if one_based_here == 1:
                start -= 1
        elif ev is not None:
            end = _as_int(ev, what)
            inclusive = bool(inclusive_v) or sk == "last"
            if one_based_here == 1:
                start -= 1
                # 1-based + end: Chinese convention is inclusive.
                length = end - start  # after start conversion: (end) - (orig-1)
                if inclusive:
                    # orig start s, end e inclusive, 1-based: zero range [s-1, e)
                    length = end - (start)  # e - (s-1)
                else:
                    length = end - 1 - start + 1  # = end - start (half-open end 1-based?)
                    length = end - start
            else:
                length = end - start + (1 if inclusive else 0)
        else:
            raise DomainError(f"{what} 需要 length 或 end: {value!r}")
        return Range(start, length)

    if isinstance(value, int):
        # A bare integer is interpreted as length starting at 0? Too risky;
        # interpret as start with length 1 only when key says "at".
        raise DomainError(f"{what} 区间格式无法识别: {value!r}")

    raise DomainError(f"{what} 区间格式无法识别: {value!r}")


def parse_range(
    raw: dict[str, Any],
    kind: str,
    base: int = 0,
    array_format: str = "end",
) -> Range:
    """Parse scale ('scale') or net-weight ('weight') range from a template."""
    d = normalized_dict(raw)

    nested, _ = pick(d, *_RANGE_KEYS[kind])
    if nested is None:
        nested = fuzzy_pick_range(d, kind)
    if nested is not None and not _looks_like_flat_position_dict(nested):
        return _range_from_value(nested, base, array_format, kind)

    sv, _ = pick(d, *_FLAT_START[kind], "起点", "开始")
    lv, _ = pick(d, *_FLAT_LEN[kind], "长度", "位数")
    ev, _ = pick(d, *_FLAT_END[kind], "结束", "终点")

    if sv is None and lv is None and ev is None:
        # Maybe the nested value actually was a flat dict like {start,length}.
        if nested is not None and isinstance(nested, dict):
            return _range_from_value(nested, base, array_format, kind)
        raise DomainError(f"缺少{kind}截取位置 (missing {kind} positions)")

    start = _as_int(sv, kind) if sv is not None else 0
    if lv is not None:
        length = _as_int(lv, kind)
        if base == 1:
            start -= 1
        return Range(start, length)
    if ev is not None:
        end = _as_int(ev, kind)
        inclusive_v, _ = pick(d, "endinclusive", f"{kind}endinclusive")
        inclusive = bool(inclusive_v)
        if base == 1:
            start -= 1
            length = end - start  # 1-based inclusive end
        else:
            length = end - start + (1 if inclusive else 0)
        return Range(start, length)
    raise DomainError(f"{kind} 起点已给但缺少 length/end")


def _looks_like_flat_position_dict(v: Any) -> bool:
    return isinstance(v, dict) and any(
        norm_key(k) in {"start", "length", "len", "end", "begin", "stop"} for k in v
    )


# ---------------------------------------------------------------------------
# template parsing
# ---------------------------------------------------------------------------

_NAME_KEYS = (
    "name",
    "templatename",
    "title",
    "label",
    "factory",
    "factoryname",
    "plant",
    "plantname",
    "vendor",
    "customername",
    "厂名",
    "工厂",
    "名称",
    "模板名",
    "id",
    "templateid",
)
_PREFIX_KEYS = (
    "prefix",
    "bodyprefix",
    "qz",
    "zhengwenqianzhui",
    "head",
    "前缀",
    "正文前缀",
)
_MARKER_KEYS = (
    "marker",
    "mark",
    "tag",
    "flag",
    "digit",
    "badgemark",
    "biaoji",
    "标记",
    "标志位",
)


def parse_template(
    raw: Any,
    *,
    base: int = 0,
    array_format: str = "end",
    force_marker: Any = None,
) -> TemplateSpec:
    if not isinstance(raw, dict):
        raise DomainError("每套模板必须是 JSON 对象")
    d = normalized_dict(raw)

    name_v, _ = pick(d, *_NAME_KEYS)
    prefix_v, _ = pick(d, *_PREFIX_KEYS)
    marker_v, _ = pick(d, *_MARKER_KEYS)
    if force_marker is not None:
        marker_v = force_marker

    # Per-template overrides for index base.
    base_v, _ = pick(d, "indexbase", "base", "numberingbase", "zerobased", "onebased")
    t_base = base
    if isinstance(base_v, bool):
        t_base = 0 if base_v else 1
    elif base_v is not None:
        b = _as_int(base_v, "index_base")
        if b in (0, 1):
            t_base = b
    af_v, _ = pick(d, "arrayformat", "rangeformat")
    t_array_format = str(af_v) if af_v is not None else array_format

    scale = parse_range(raw, "scale", t_base, t_array_format)
    weight = parse_range(raw, "weight", t_base, t_array_format)

    try:
        marker = parse_marker(marker_v)
    except DomainError:
        raise
    return TemplateSpec(
        name="" if name_v is None else str(name_v),
        prefix=parse_prefix(prefix_v),
        scale=scale,
        weight=weight,
        marker=marker,
    )


# ---------------------------------------------------------------------------
# request envelope extraction
# ---------------------------------------------------------------------------

_LIST_KEYS = (
    "templates",
    "items",
    "records",
    "data",
    "list",
    "batch",
    "templatelist",
    "newtemplates",
    "factories",
    "configs",
    "templatedata",
    "模板",
    "模板列表",
    "批量",
)


def extract_template_list(payload: Any) -> list[dict]:
    """Accept bare list, ``{"templates": [...]}`` or a single template dict."""
    if isinstance(payload, list):
        return [x for x in payload]
    if isinstance(payload, dict):
        d = normalized_dict(payload)
        for k in _LIST_KEYS:
            if k in d and isinstance(d[k], list):
                return list(d[k])
        # Maybe {"templates": {"a": ..}} weird; or payload itself is template.
        if any(norm_key(k).startswith(("scale", "weight", "netweight")) for k in payload):
            return [payload]
        # Last resort: the object itself may be a single template (also covers
        # Chinese position keys missed by the English prefix check above).
        try:
            parse_template(payload)
            return [payload]
        except DomainError:
            pass
    raise DomainError("请求体中找不到模板数组 (expected 'templates' list)")


def extract_index_base(payload: Any) -> int:
    if isinstance(payload, dict):
        d = normalized_dict(payload)
        v, _ = pick(d, "indexbase", "base", "numberingbase")
        if v is not None:
            b = _as_int(v, "index_base")
            if b in (0, 1):
                return b
        if d.get("onebased") is True:
            return 1
    return 0


def extract_array_format(payload: Any) -> str:
    if isinstance(payload, dict):
        d = normalized_dict(payload)
        v, _ = pick(d, "arrayformat", "rangeformat")
        if v is not None and str(v) in ("end", "length"):
            return str(v)
    return "end"


def loads_lenient(body: bytes) -> Any:
    if not body:
        return {}
    text = body.decode("utf-8-sig").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Maybe form-encoded-ish? Let caller handle; re-raise a clear error.
        raise

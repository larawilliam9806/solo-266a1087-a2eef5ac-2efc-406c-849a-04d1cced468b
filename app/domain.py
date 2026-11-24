"""Pure domain model: templates, extraction ranges and code layout.

A 磅单码 (weighing note code) is exactly 9 decimal digits:

    [marker][8-digit body]

The first digit is the format marker.  The body is described by a
:class:`TemplateSpec`: a literal prefix and two half-open extraction
ranges (scale number and net weight in grams), both 0-based relative to
the 8-character body.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

BODY_LENGTH = 8
CODE_LENGTH = 9
MARKER_DIGITS = tuple(str(d) for d in range(10))
MAX_TEMPLATES = 12


class DomainError(ValueError):
    """Raised when a template/code violates the fixed format."""


@dataclass(frozen=True)
class Range:
    """Half-open extraction interval ``[start, start + length)`` in the body."""

    start: int
    length: int

    def __post_init__(self) -> None:
        if not isinstance(self.start, int) or not isinstance(self.length, int):
            raise DomainError("截取位置必须是整数 (positions must be integers)")
        if self.start < 0:
            raise DomainError("截取起点不能为负 (start must be >= 0)")
        if self.length <= 0:
            raise DomainError("截取长度必须为正 (length must be > 0)")
        if self.start + self.length > BODY_LENGTH:
            raise DomainError(
                f"截取区间 [{self.start},{self.start + self.length}) 超出正文 "
                f"{BODY_LENGTH} 位"
            )

    @property
    def end(self) -> int:
        return self.start + self.length

    def extract(self, body: str) -> str:
        return body[self.start : self.end]

    def to_dict(self) -> dict:
        return {"start": self.start, "length": self.length, "end": self.end}


@dataclass(frozen=True)
class TemplateSpec:
    prefix: str
    scale: Range
    weight: Range
    name: str = ""
    marker: Optional[str] = None
    id: Optional[int] = None

    def __post_init__(self) -> None:
        if not isinstance(self.prefix, str) or (
            self.prefix != "" and not self.prefix.isdigit()
        ):
            raise DomainError("正文前缀必须是数字字符串 (prefix must be digits)")
        if len(self.prefix) > BODY_LENGTH:
            raise DomainError(f"正文前缀最长 {BODY_LENGTH} 位")
        if self.marker is not None:
            m = str(self.marker)
            if len(m) != 1 or not m.isdigit():
                raise DomainError("标记必须是一位数字 (marker must be one digit)")
            # frozen dataclass + object.__setattr__ to normalize marker
            object.__setattr__(self, "marker", m)

    @property
    def marker_int(self) -> Optional[int]:
        return int(self.marker) if self.marker is not None else None

    def matches(self, body: str) -> bool:
        return body.startswith(self.prefix)

    def decode_parts(self, body: str) -> tuple[str, int]:
        """Return ``(scale_no_with_leading_zeros, net_weight_g)``."""
        scale_no = self.scale.extract(body)
        weight_raw = self.weight.extract(body)
        # Extraction ranges only contain digits; int() tolerates leading zeros.
        return scale_no, int(weight_raw)

    def flat_positions(self) -> dict:
        return {
            "scale_start": self.scale.start,
            "scale_length": self.scale.length,
            "scale_end": self.scale.end,
            "weight_start": self.weight.start,
            "weight_length": self.weight.length,
            "weight_end": self.weight.end,
        }

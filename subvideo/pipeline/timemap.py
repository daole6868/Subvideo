"""Piecewise-linear mapping between the source timeline and the output timeline.

When the pacing solver slows the video down locally (region [a, b] played at
speed factor v < 1), everything after that region is shifted later on the output.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass


@dataclass
class TimeMap:
    regions: list[tuple[float, float, float]]  # (a_ms, b_ms, v) on the source timeline

    def __post_init__(self):
        self.regions = sorted((float(a), float(b), float(v)) for a, b, v in self.regions if b > a and 0 < v < 1)
        # cumulative extra time added *before* each region
        self._starts = [a for a, _, _ in self.regions]
        self._cum = []
        c = 0.0
        for a, b, v in self.regions:
            self._cum.append(c)
            c += (b - a) * (1.0 / v - 1.0)
        self.total_extra = c

    @property
    def identity(self) -> bool:
        return not self.regions

    def out(self, t: float) -> float:
        """source ms -> output ms"""
        if not self.regions:
            return t
        i = bisect.bisect_right(self._starts, t) - 1
        if i < 0:
            return t
        a, b, v = self.regions[i]
        if t <= b:
            return t + self._cum[i] + (t - a) * (1.0 / v - 1.0)
        return t + self._cum[i] + (b - a) * (1.0 / v - 1.0)

    def inv(self, u: float) -> float:
        """output ms -> source ms"""
        if not self.regions:
            return u
        for i in range(len(self.regions) - 1, -1, -1):
            a, b, v = self.regions[i]
            oa = a + self._cum[i]
            if u >= oa:
                ob = oa + (b - a) / v
                if u <= ob:
                    return a + (u - oa) * v
                return u - (self._cum[i] + (b - a) * (1.0 / v - 1.0))
        return u

    def output_duration(self, source_duration: float) -> float:
        return self.out(source_duration)

    def segments(self, duration: float) -> list[tuple[float, float, float]]:
        """Split [0, duration] into (a, b, speed) pieces covering the whole source."""
        out = []
        t = 0.0
        for a, b, v in self.regions:
            if a > t:
                out.append((t, a, 1.0))
            out.append((a, b, v))
            t = b
        if t < duration:
            out.append((t, duration, 1.0))
        return out

    def ffmpeg_pts_expr(self, offset_src_s: float = 0.0, offset_out_s: float = 0.0) -> str:
        """setpts expression mapping input T (seconds) to output seconds.

        ``offset_src_s``: the input was seeked to this source time (T starts at 0).
        ``offset_out_s``: subtract this from the output (for previews).
        """
        tt = f"(T+{offset_src_s:.6f})" if offset_src_s else "T"
        # out(T) = T + cum_i (+ (T - a_i) * k_i inside region i), nested from the last region backwards
        expr = f"{tt}+{self.total_extra / 1000.0:.6f}" if self.regions else tt
        for i in range(len(self.regions) - 1, -1, -1):
            a, b, v = self.regions[i]
            a_s, b_s, cum_s, k = a / 1000.0, b / 1000.0, self._cum[i] / 1000.0, 1.0 / v - 1.0
            inside = f"{tt}+{cum_s:.6f}+({tt}-{a_s:.6f})*{k:.6f}"
            expr = f"if(lt({tt},{a_s:.6f}),{tt}+{cum_s:.6f},if(lt({tt},{b_s:.6f}),{inside},{expr}))"
        body = expr
        if offset_out_s:
            body = f"({body})-{offset_out_s:.6f}"
        return f"({body})/TB"

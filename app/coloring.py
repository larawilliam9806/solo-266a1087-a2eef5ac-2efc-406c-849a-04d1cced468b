"""Optimal marker (digit) assignment — a pre-colored graph coloring problem.

Existing templates keep their markers (pre-colored vertices).  New templates
must receive a digit 0-9 so that every conflict edge has differently marked
endpoints.

Objective (in the wording of the task): minimise the number of *newly used*
digit kinds, i.e. digits assigned to new templates that are not already
marking an old template.  Among optimal assignments the lexicographically
smallest marker tuple (submission order, digits compared numerically) is
returned so results are deterministic.
"""

from __future__ import annotations

from .conflicts import build_adjacency
from .domain import MARKER_DIGITS, TemplateSpec

NODE_LIMIT = 2_000_000


class NoAssignment(Exception):
    """No valid marker assignment exists for the batch."""


def _blocked_by_old(
    new_index: int,
    adjacency: list[set[int]],
    old_count: int,
    old_markers: list[str],
) -> set[str]:
    blocked: set[str] = set()
    for j in adjacency[new_index]:
        if j < old_count:
            blocked.add(old_markers[j])
    return blocked


def solve_markers(
    old_templates: list[TemplateSpec],
    new_templates: list[TemplateSpec],
) -> list[str]:
    """Return one marker per entry of ``new_templates`` (aligned).

    Raises :class:`NoAssignment` when the conflict graph cannot be coloured
    under the fixed old markers.
    """
    old_count = len(old_templates)
    templates = list(old_templates) + list(new_templates)
    new_count = len(new_templates)

    if new_count == 0:
        return []

    adjacency = build_adjacency(templates)
    old_markers = [t.marker for t in old_templates]  # type: ignore[arg-type]
    old_marker_set = {m for m in old_markers if m is not None}

    # Indices of new vertices in the combined graph.
    new_vertices = list(range(old_count, old_count + new_count))

    blocked_old = {
        v: _blocked_by_old(v, adjacency, old_count, old_markers) for v in new_vertices
    }

    # Sanity: an old template without marker cannot define a fixed config.
    if any(m is None for m in old_markers):
        raise NoAssignment("现用模板存在缺少标记的记录，无法在此配置上分配")

    all_digits = list(MARKER_DIGITS)
    fresh_digits = [d for d in all_digits if d not in old_marker_set]

    node_counter = 0

    def legal_colors(v: int, assignment: dict[int, str], fresh_used: set[str], budget: int):
        out = []
        for c in all_digits:
            if c in blocked_old[v]:
                continue
            ok = True
            for u in adjacency[v]:
                if u in assignment and assignment[u] == c:
                    ok = False
                    break
            if not ok:
                continue
            if c not in old_marker_set and c not in fresh_used:
                if len(fresh_used) >= budget:
                    continue
            out.append(c)
        return out

    # ---- MRV feasibility search for a given fresh-digit budget -----------
    def feasible(budget: int) -> bool:
        nonlocal node_counter
        assignment: dict[int, str] = {}
        fresh_used: set[str] = set()

        def dfs() -> bool:
            nonlocal node_counter
            node_counter += 1
            if node_counter > NODE_LIMIT:  # pragma: no cover - safety valve
                raise NoAssignment("分配搜索超时")
            if len(assignment) == new_count:
                return True

            unassigned = [v for v in new_vertices if v not in assignment]
            # MRV with deterministic tie-break (smallest original index).
            best_v = None
            best_choices: list[str] | None = None
            for v in unassigned:
                choices = legal_colors(v, assignment, fresh_used, budget)
                if not choices:
                    return False
                if best_choices is None or len(choices) < len(best_choices):
                    best_v, best_choices = v, choices
                    if len(choices) == 1:
                        break
            assert best_v is not None and best_choices is not None

            for c in best_choices:
                introduced = c not in old_marker_set and c not in fresh_used
                if introduced:
                    fresh_used.add(c)
                assignment[best_v] = c
                if dfs():
                    return True
                del assignment[best_v]
                if introduced:
                    fresh_used.discard(c)
            return False

        return dfs()

    max_budget = min(len(fresh_digits), new_count)
    optimal_budget = None
    for k in range(max_budget + 1):
        if feasible(k):
            optimal_budget = k
            break

    if optimal_budget is None:
        raise NoAssignment(
            "不存在满足冲突约束的标记分配 (no valid digit coloring exists)"
        )

    # ---- Optimal assignment at the minimum fresh budget ------------------
    # Among assignments using <= optimal_budget fresh digits we minimise
    # (number of distinct markers used by the new templates, then the
    #  lexicographically smallest marker tuple).
    def best_assignment(budget: int) -> tuple[str, ...]:
        assignment: dict[int, str] = {}
        fresh_used: set[str] = set()
        used_colors: set[str] = set()
        best_key: tuple[int, tuple[str, ...]] | None = None
        best_tuple: tuple[str, ...] | None = None

        def current_tuple() -> tuple[str, ...]:
            return tuple(assignment[old_count + i] for i in range(new_count))

        def dfs(pos: int) -> None:
            nonlocal best_key, best_tuple, node_counter
            node_counter += 1
            if node_counter > NODE_LIMIT:  # pragma: no cover
                raise NoAssignment("分配搜索超时")

            if best_key is not None and len(used_colors) > best_key[0]:
                return

            if pos == new_count:
                tup = current_tuple()
                distinct = len(set(tup))
                key = (distinct, tup)
                if best_key is None or key < best_key:
                    best_key = key
                    best_tuple = tup
                return

            v = old_count + pos
            for c in legal_colors(v, assignment, fresh_used, budget):
                introduced_fresh = (
                    c not in old_marker_set and c not in fresh_used
                )
                introduced_color = c not in used_colors
                if introduced_fresh:
                    fresh_used.add(c)
                if introduced_color:
                    used_colors.add(c)
                assignment[v] = c

                # Branch-and-bound on distinct count.
                if best_key is None or len(used_colors) <= best_key[0]:
                    dfs(pos + 1)

                del assignment[v]
                if introduced_fresh:
                    fresh_used.discard(c)
                if introduced_color:
                    used_colors.discard(c)

        dfs(0)
        if best_tuple is None:  # pragma: no cover - guarded by feasible()
            raise NoAssignment("最优分配重构失败")
        return best_tuple

    chosen = best_assignment(optimal_budget)
    return list(chosen)

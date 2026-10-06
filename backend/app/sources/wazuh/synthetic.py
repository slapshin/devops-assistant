"""Deterministic synthetic Wazuh indexer for tests, UI development and offline demos.

It answers the same calls as the indexer client, so a ``synthetic://<scenario>`` Wazuh source
runs the real collection and conversion code. Scenario names match the other synthetic
sources; anomalies are placed relative to the analysis end time T.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from app.domain.interfaces import SourceError, SourceErrorKind
from app.domain.projects import WazuhLabel
from app.sources.base import HISTORY_DAYS
from app.sources.wazuh.api import SECONDS_PER_DAY, AgentInfo, AgentList, Chunk, GroupMembers
from app.sources.wazuh.catalog import (
    MAX_AGENTS,
    SIGNALS,
    request_text,
    search_path,
    series_body,
)

STEP = 300
DAY = SECONDS_PER_DAY // STEP
N = HISTORY_DAYS * DAY
HOUR = DAY // 24
SYNTHETIC_AGENTS = ("shop-db-1", "shop-web-1", "shop-web-2")
"""Agents of a synthetic source selected by labels only."""
SYNTHETIC_GROUPS = {"shop-db": ["shop-db-1"], "shop-web": ["shop-web-1", "shop-web-2"]}
"""Groups the synthetic monitoring index knows; any other group has no agents."""


@dataclass(frozen=True)
class WazuhScenario:
    history_days: int = HISTORY_DAYS
    brute_force: bool = False
    """SSH brute force on the first agent for 2 h ending 3 h before T: authentication
    failures and high-level alerts climb."""
    fim_burst: bool = False
    """Unexpected file changes on the last agent for 1 h ending 6 h before T."""
    failing_day: int | None = None
    """Searches touching this many days before T fail (a partial report)."""
    seed: int = 31


SCENARIOS: dict[str, WazuhScenario] = {
    "healthy": WazuhScenario(),
    "incident": WazuhScenario(brute_force=True, fim_burst=True),
    "short-history": WazuhScenario(history_days=5),
    "degraded": WazuhScenario(fim_burst=True, failing_day=10),
}


def _window(end_hours_before: int, hours: float) -> slice:
    stop = N - end_hours_before * HOUR
    return slice(stop - int(hours * HOUR), stop)


class SyntheticWazuhApi:
    def __init__(
        self,
        scenario: str,
        index_pattern: str,
        agents: Sequence[str] = (),
        labels: Sequence[WazuhLabel] = (),
        groups: Sequence[str] = (),
    ) -> None:
        self.name = scenario
        self.scenario = SCENARIOS[scenario]
        self.index_pattern = index_pattern
        members = {name for group in groups for name in SYNTHETIC_GROUPS.get(group, [])}
        self.names = sorted(set(agents) | members) if agents or groups else list(SYNTHETIC_AGENTS)
        self.labels = [(label.key, label.value) for label in labels]
        self._anchor: int | None = None
        self._data: dict[str, dict[str, np.ndarray]] = {}

    @property
    def base_url(self) -> str:
        return f"synthetic://{self.name}"

    @property
    def backend(self) -> str | None:
        return "synthetic"

    async def version(self) -> str | None:
        return None

    def begin(self, end_time: datetime) -> None:
        anchor = int(end_time.timestamp())
        if anchor != self._anchor:
            self._anchor = anchor
            last = len(self.names) - 1
            self._data = {
                name: self._generate(i, brute_force=i == 0, fim_burst=i == last)
                for i, name in enumerate(self.names)
            }

    async def aclose(self) -> None:
        pass

    # --- generation -----------------------------------------------------------------------

    def _generate(self, index: int, *, brute_force: bool, fim_burst: bool) -> dict[str, np.ndarray]:
        sc = self.scenario
        rng = np.random.default_rng(sc.seed + index)
        tod = (np.arange(N) % DAY) / DAY
        diurnal = np.sin(2 * np.pi * (tod - 0.35))

        auth = rng.poisson(np.clip(1.5 + 0.8 * diurnal, 0.1, None))
        fim = rng.poisson(0.3, N)
        high = rng.poisson(0.03, N)
        other = rng.poisson(np.clip(4.0 + 2.0 * diurnal, 0.5, None))
        fim[(np.arange(N) % DAY) == 3 * HOUR] += 12  # nightly package updates

        if brute_force and sc.brute_force:
            w = _window(3, 2)
            auth[w] += rng.poisson(90, w.stop - w.start)
            high[w] += rng.poisson(4, w.stop - w.start)
        if fim_burst and sc.fim_burst:
            w = _window(6, 1)
            fim[w] += rng.poisson(45, w.stop - w.start)

        return {
            "wazuh_alerts": (auth + fim + high + other).astype(float),
            "wazuh_high_alerts": high.astype(float),
            "wazuh_auth_failures": auth.astype(float),
            "wazuh_fim_changes": fim.astype(float),
        }

    def _first_index(self) -> int:
        return N - self.scenario.history_days * DAY

    def _grid_start(self) -> int:
        if self._anchor is None:
            raise RuntimeError("begin() must be called before querying synthetic data")
        return self._anchor - N * STEP

    def _range(self, start: int, end: int) -> range:
        grid_start = self._grid_start()
        lo = max((start - grid_start) // STEP, self._first_index(), 0)
        hi = min((end - grid_start) // STEP, N)
        return range(lo, max(hi, lo))

    def _check_failing(self, start: int, end: int) -> None:
        failing = self.scenario.failing_day
        if failing is None:
            return
        day_start = self._grid_start() + N * STEP - failing * SECONDS_PER_DAY
        if start < day_start + SECONDS_PER_DAY and end > day_start:
            raise SourceError(SourceErrorKind.SERVER_ERROR, "HTTP 503 (synthetic)")

    # --- WazuhApi -------------------------------------------------------------------------

    async def group_members(self, groups: Sequence[str], start: int, end: int) -> GroupMembers:
        return GroupMembers({group: list(SYNTHETIC_GROUPS.get(group, [])) for group in groups})

    async def agents(self, names: Sequence[str] | None, start: int, end: int) -> AgentList:
        grid_start = self._grid_start()
        span = self._range(start, end)
        found = []
        for name in self.names:
            if names is not None and name not in names:
                continue
            totals = self._data[name]["wazuh_alerts"][span.start : span.stop]
            if not totals.any():
                continue
            first = span.start + int(np.argmax(totals > 0))
            found.append(AgentInfo(name, int(totals.sum()), grid_start + first * STEP))
        return AgentList(found[:MAX_AGENTS], truncated=len(found) > MAX_AGENTS)

    async def series(self, agents: Sequence[str], start: int, end: int) -> Chunk:
        self._check_failing(start, end)
        grid_start = self._grid_start()
        body = series_body(agents, self.labels, start, end)
        chunk = Chunk(query=request_text(search_path(self.index_pattern), body))
        span = self._range(start, end)
        for defn in SIGNALS:
            per_agent = chunk.values.setdefault(defn.signal, {})
            for name in agents:
                data = self._data.get(name)
                if data is None:
                    continue
                per_agent[name] = {
                    grid_start + i * STEP: float(data[defn.signal][i])
                    for i in span
                    if data["wazuh_alerts"][i] > 0
                }
        return chunk

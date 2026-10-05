"""Container (cAdvisor) and Docker Swarm signals."""

from app.domain.common import EntityKind, SignalFamily, Unit
from app.metrics.catalog.base import (
    Gate,
    SignalDef,
    aggregate,
    q,
    rate,
    sel,
)

CONTAINER_FILTER = 'name!=""'
# Swarm task containers are named "<service>.<slot or node id>.<25-char task id>"; dropping the
# task id gives an identity that survives task replacement.
CONTAINER_KEY_REGEX = "(.+?)(\\\\.[a-z0-9]{{25}})?"

CONT = ("instance", "container")
SWARM = ("service_name",)

_CONTAINER_MEMORY = "container_memory_working_set_bytes"
_CONTAINER_LIMIT = "container_spec_memory_limit_bytes"


def _container(inner: str) -> str:
    return f'label_replace({inner}, "container", "$1", "name", "{CONTAINER_KEY_REGEX}")'


CONTAINER_CATALOG: tuple[SignalDef, ...] = (
    # --- containers (cAdvisor) ------------------------------------------------------------
    SignalDef(
        "container_cpu",
        SignalFamily.CONTAINER,
        Unit.PER_SECOND,
        EntityKind.CONTAINER,
        CONT,
        q(
            aggregate(
                "sum",
                CONT,
                _container(rate("container_cpu_usage_seconds_total", CONTAINER_FILTER)),
            ),
            "container_cpu_usage_seconds_total",
        ),
        description="CPU cores used.",
    ),
    SignalDef(
        "container_memory_working_set",
        SignalFamily.CONTAINER,
        Unit.BYTES,
        EntityKind.CONTAINER,
        CONT,
        q(
            aggregate("max", CONT, _container(sel(_CONTAINER_MEMORY, CONTAINER_FILTER))),
            _CONTAINER_MEMORY,
        ),
    ),
    SignalDef(
        "container_memory_limit_ratio",
        SignalFamily.CONTAINER,
        Unit.RATIO,
        EntityKind.CONTAINER,
        CONT,
        q(
            aggregate("max", CONT, _container(sel(_CONTAINER_MEMORY, CONTAINER_FILTER)))
            + " / "
            + aggregate("max", CONT, _container(sel(_CONTAINER_LIMIT, CONTAINER_FILTER) + " > 0")),
            _CONTAINER_MEMORY,
            _CONTAINER_LIMIT,
        ),
        gates=(
            Gate(
                q(f"count({sel(_CONTAINER_LIMIT, CONTAINER_FILTER)} > 0)", _CONTAINER_LIMIT),
                "No container has a memory limit (container_spec_memory_limit_bytes is 0).",
            ),
        ),
    ),
    SignalDef(
        "container_throttling_ratio",
        SignalFamily.CONTAINER,
        Unit.RATIO,
        EntityKind.CONTAINER,
        CONT,
        q(
            aggregate(
                "sum",
                CONT,
                _container(rate("container_cpu_cfs_throttled_periods_total", CONTAINER_FILTER)),
            )
            + " / ("
            + aggregate(
                "sum", CONT, _container(rate("container_cpu_cfs_periods_total", CONTAINER_FILTER))
            )
            + " > 0)",
            "container_cpu_cfs_throttled_periods_total",
            "container_cpu_cfs_periods_total",
        ),
    ),
    SignalDef(
        "container_oom",
        SignalFamily.CONTAINER,
        Unit.COUNT,
        EntityKind.CONTAINER,
        CONT,
        q(
            aggregate(
                "sum",
                CONT,
                _container(rate("container_oom_events_total", CONTAINER_FILTER, fn="increase")),
            ),
            "container_oom_events_total",
        ),
    ),
    # --- swarm task/replica state (restart proxy) -----------------------------------------
    SignalDef(
        "swarm_failed_tasks",
        SignalFamily.CONTAINER,
        Unit.COUNT,
        EntityKind.SERVICE,
        SWARM,
        q(
            aggregate("count", SWARM, sel("docker_swarm_task_info", 'state=~"failed|rejected"')),
            "docker_swarm_task_info",
        ),
        description="Failed/rejected tasks still listed by Swarm; a rise means new failures "
        "(task history is pruned, so decreases carry no meaning).",
    ),
    SignalDef(
        "swarm_replica_shortfall",
        SignalFamily.CONTAINER,
        Unit.COUNT,
        EntityKind.SERVICE,
        SWARM,
        q(
            f"({aggregate('max', SWARM, sel('docker_swarm_service_replicas_desired'))}"
            f" - {aggregate('max', SWARM, sel('docker_swarm_service_replicas_running'))})"
            " and on (service_name) "
            + aggregate("max", SWARM, sel("docker_swarm_service_info", 'mode="replicated"')),
            "docker_swarm_service_replicas_desired",
            "docker_swarm_service_replicas_running",
            "docker_swarm_service_info",
        ),
        description="Desired minus running replicas for replicated services.",
    ),
)

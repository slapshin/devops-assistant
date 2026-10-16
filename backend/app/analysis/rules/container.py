"""Container rules: cAdvisor resource usage and Docker Swarm task/replica health."""

from app.analysis.rules.base import Rule, RuleKind
from app.domain.common import SignalFamily

F = SignalFamily
CONTAINER_RULES = (
    Rule(
        "container_cpu",
        F.CONTAINER,
        "Container CPU",
        thresholds="container_cpu",
        summary="A container used more CPU than usual.",
        help=(
            "Check whether the load increased or the code changed (recent deploy), and "
            "whether the container is throttled by its CPU limit."
        ),
    ),
    Rule(
        "container_memory_working_set",
        F.CONTAINER,
        "Container memory",
        thresholds="container_memory_working_set",
        summary="A container's working-set memory grew beyond its usual range.",
        help=(
            "Compare with its memory limit and recent deploys; steady growth without a load "
            "increase points to a leak."
        ),
    ),
    Rule(
        "container_memory_limit_ratio",
        F.CONTAINER,
        "Container memory vs limit",
        thresholds="container_memory_limit_ratio",
        summary="A container came closer to its memory limit; at the limit it is OOM-killed.",
        help=(
            "Check what grows inside the container, then raise the limit or reduce memory "
            "use; look for OOM kills of the same container."
        ),
    ),
    Rule(
        "container_throttling_ratio",
        F.CONTAINER,
        "Container CPU throttling",
        thresholds="container_throttling_ratio",
        summary="A container was throttled by its CPU quota more often, which adds latency.",
        help=(
            "Raise the CPU limit or reduce work per request; throttling with low average "
            "CPU usually means short bursts hitting the quota."
        ),
    ),
    Rule(
        "container_oom",
        F.CONTAINER,
        "Container OOM events",
        kind=RuleKind.EVENT,
        event_points=3,
        title_up="Container OOM kill",
        summary="A container was killed because it reached its memory limit.",
        help=(
            "Check the container's logs and restarts around the kill, what used the memory, "
            "and whether the limit fits the workload."
        ),
    ),
    Rule(
        "swarm_task_failures",
        F.CONTAINER,
        "New failed tasks",
        kind=RuleKind.EVENT,
        event_points=2,
        title_up="New failed or rejected Swarm tasks (restart proxy)",
        summary=(
            "Docker Swarm tasks failed or were rejected, so the service restarted or could "
            "not start."
        ),
        help=(
            "Run docker service ps --no-trunc on the service to see the error, and check "
            "image pulls, health checks, placement constraints and node resources."
        ),
    ),
    Rule(
        "swarm_replica_shortfall",
        F.CONTAINER,
        "Replica shortfall",
        kind=RuleKind.SHORTFALL,
        event_points=3,
        title_up="Running replicas below desired",
        summary="A service ran fewer replicas than desired, reducing capacity or availability.",
        help=(
            "Run docker service ps to see why tasks are not running, e.g. failing health "
            "checks, no node meeting the constraints, or insufficient resources."
        ),
    ),
)

"""Container rules: cAdvisor resource usage and Docker Swarm task/replica health."""

from app.analysis.rules.base import Rule, RuleKind
from app.domain.common import SignalFamily

F = SignalFamily
CONTAINER_RULES = (
    Rule("container_cpu", F.CONTAINER, "Container CPU", thresholds="container_cpu"),
    Rule(
        "container_memory_working_set",
        F.CONTAINER,
        "Container memory",
        thresholds="container_memory_working_set",
    ),
    Rule(
        "container_memory_limit_ratio",
        F.CONTAINER,
        "Container memory vs limit",
        thresholds="container_memory_limit_ratio",
    ),
    Rule(
        "container_throttling_ratio",
        F.CONTAINER,
        "Container CPU throttling",
        thresholds="container_throttling_ratio",
    ),
    Rule(
        "container_oom",
        F.CONTAINER,
        "Container OOM events",
        kind=RuleKind.EVENT,
        event_points=3,
        title_up="Container OOM kill",
    ),
    Rule(
        "swarm_task_failures",
        F.CONTAINER,
        "New failed tasks",
        kind=RuleKind.EVENT,
        event_points=2,
        title_up="New failed or rejected Swarm tasks (restart proxy)",
    ),
    Rule(
        "swarm_replica_shortfall",
        F.CONTAINER,
        "Replica shortfall",
        kind=RuleKind.SHORTFALL,
        event_points=3,
        title_up="Running replicas below desired",
    ),
)

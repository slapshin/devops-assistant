"""Resource associations from identity labels and verified mappings, never from names.

Shared by finding relations (`related_finding_ids`) and related metrics (T023).
"""

from collections import defaultdict
from collections.abc import Sequence

from app.domain.common import Entity, EntityKind
from app.domain.correlation import ResourceAssociation
from app.domain.interfaces import EntityMapping, MappingKind

OTEL_MAPPING_PREFIX = "otel"
SWARM_MAPPING_PREFIX = "swarm"
JOB_LABEL = "job"
INSTANCE_LABEL = "instance"

_PROXY_KINDS = {EntityKind.PROXY, EntityKind.UPSTREAM}
"""A proxy's zones, services and upstreams share its scrape job; overlaps there are related."""
_SERVICE_KINDS = {EntityKind.ROUTE, EntityKind.PROCESS}
"""A service's routes and its runtime processes share the service's ``job``."""

HostMappings = dict[str, set[str]]


def _mapping_key(prefix: str, service: str) -> str:
    return f"{prefix}:{service}"


def host_mappings(mappings: Sequence[EntityMapping]) -> HostMappings:
    out: HostMappings = defaultdict(set)
    for m in mappings:
        is_otel = m.kind is MappingKind.OTEL_SERVICE
        prefix = OTEL_MAPPING_PREFIX if is_otel else SWARM_MAPPING_PREFIX
        out[_mapping_key(prefix, m.service)].add(m.host)
    return out


def hosts_for(entity: Entity, mappings: HostMappings) -> set[str]:
    """Hosts an entity verifiably runs on, from identity labels or label-based mappings."""
    labels = entity.labels
    match entity.kind:
        case (
            EntityKind.NODE
            | EntityKind.FILESYSTEM
            | EntityKind.DISK
            | EntityKind.NETWORK_INTERFACE
            | EntityKind.CONTAINER
            | EntityKind.CUSTOM
        ):
            return {labels[INSTANCE_LABEL]} if labels.get(INSTANCE_LABEL) else set()
        case EntityKind.SERVICE:
            key = _mapping_key(SWARM_MAPPING_PREFIX, labels.get("service_name", ""))
            return mappings.get(key, set())
        case EntityKind.ROUTE:
            return mappings.get(_mapping_key(OTEL_MAPPING_PREFIX, labels.get(JOB_LABEL, "")), set())
    return set()


def association(
    a: Entity, b: Entity, hosts_a: set[str], hosts_b: set[str]
) -> tuple[ResourceAssociation, str]:
    """The strongest verified connection of two entities, with the labels behind it."""
    if a.key == b.key:
        return ResourceAssociation.SAME_ENTITY, f"Both concern {a.display_name}."

    kinds = {a.kind, b.kind}
    job = a.labels.get(JOB_LABEL)
    if (kinds <= _SERVICE_KINDS or kinds <= _PROXY_KINDS) and job == b.labels.get(JOB_LABEL):
        return ResourceAssociation.SAME_SERVICE, f"Both carry {JOB_LABEL}={job}."
    if a.kind is b.kind is EntityKind.DATABASE and all(
        a.labels.get(k) == b.labels.get(k) for k in (JOB_LABEL, INSTANCE_LABEL)
    ):
        return (
            ResourceAssociation.SAME_DATABASE_SERVER,
            f"Same database server ({JOB_LABEL}={job}, "
            f"{INSTANCE_LABEL}={a.labels.get(INSTANCE_LABEL)}).",
        )
    # A scope holds one SeaweedFS cluster: a full disk shows up as S3 errors elsewhere.
    if a.kind is b.kind is EntityKind.STORAGE:
        return ResourceAssociation.SAME_STORAGE_CLUSTER, "Both are parts of the project's storage."
    if shared := sorted(hosts_a & hosts_b):
        return ResourceAssociation.SHARED_HOST, f"Both run on {', '.join(shared)}."
    return (
        ResourceAssociation.UNKNOWN,
        "No verified connection between these resources; they only share the project.",
    )


_APPLICATION_LABELS = ("organization", "project", "environment")


def component_association(a: Entity, b: Entity) -> tuple[ResourceAssociation, str] | None:
    """A Sentry project and its error kinds (one entity per issue) belong to one application.

    Used by related metrics only; finding relations keep their own rules.
    """
    if a.kind is b.kind is EntityKind.APPLICATION and all(
        a.labels.get(k) == b.labels.get(k) for k in _APPLICATION_LABELS
    ):
        project = f"{a.labels.get('organization')}/{a.labels.get('project')}"
        return ResourceAssociation.SAME_SERVICE, f"Both concern the Sentry project {project}."
    return None

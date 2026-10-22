"""Storage rules: SeaweedFS capacity, read-only volumes, master leadership and write failures.

SeaweedFS request traffic (S3, filer, volume and filer store APIs) reuses the HTTP rules.
"""

from app.analysis.rules.base import Rule, RuleKind
from app.domain.common import Severity, SignalFamily

F = SignalFamily
STORAGE_RULES = (
    Rule(
        "storage_disk_used_ratio",
        F.STORAGE,
        "Volume server disk usage",
        thresholds="filesystem_used_ratio",
        summary="The disk holding a volume server's data directory filled up further.",
        help=(
            "Check which collections grow (Used Disk Space by Collection), vacuum deleted "
            "space (volume.vacuum), add volume servers or disks, and review TTLs."
        ),
    ),
    Rule(
        "storage_volume_slots_ratio",
        F.STORAGE,
        "Volumes vs volume server maximum",
        thresholds="storage_volume_slots_ratio",
        summary=(
            "A volume server came closer to its maximum number of volumes; at the limit the "
            "master cannot grow new volumes there."
        ),
        help=(
            "Raise -max (or let it follow the disk with -max=0), balance volumes "
            "(volume.balance), delete or merge sparse collections, or add volume servers."
        ),
    ),
    Rule(
        "storage_read_only_volumes",
        F.STORAGE,
        "Read-only volumes",
        thresholds="storage_read_only_volumes",
        severity_cap=Severity.MEDIUM,
        title_up="More volumes turned read-only",
        summary="More volumes of a volume server stopped accepting writes.",
        help=(
            "Check whether an operator marked them read-only (volume.mark), whether the disk "
            "ran low, and the volume server log for I/O errors."
        ),
    ),
    Rule(
        "storage_disk_space_low",
        F.STORAGE,
        "Volumes read-only on low disk space",
        kind=RuleKind.SHORTFALL,
        event_points=3,
        title_up="Volumes read-only because the disk is almost full",
        summary=(
            "The volume server's free disk space fell below -minFreeSpace, so its volumes "
            "turned read-only."
        ),
        help=(
            "Free space on the disk (vacuum, delete, move volumes with volume.move) or add "
            "capacity; writes go to other servers until then, if they have room."
        ),
    ),
    Rule(
        "storage_master_no_leader",
        F.STORAGE,
        "No master leader",
        kind=RuleKind.SHORTFALL,
        event_points=3,
        title_up="No SeaweedFS master is the leader",
        summary="No master held Raft leadership, so file IDs cannot be assigned and writes fail.",
        help=(
            "Check that a majority of masters runs and can reach each other (-peers), and "
            "the master logs for election loops."
        ),
    ),
    Rule(
        "storage_assign_errors",
        F.STORAGE,
        "Failed volume assignments",
        thresholds="storage_assign_errors",
        summary="The master failed more requests to assign a writable volume for new files.",
        help=(
            "Look for full or read-only volumes, volume servers at their volume limit, and "
            "collections or replication settings that no server can satisfy."
        ),
    ),
    Rule(
        "storage_filer_failures",
        F.STORAGE,
        "Filer chunk failures",
        thresholds="storage_filer_failures",
        summary=(
            "Filers failed more chunk assignments, uploads, downloads or read streams to "
            "volume servers."
        ),
        help=(
            "Check volume server reachability and health, failed volume assignments on the "
            "master, and the filer logs for the failing operations."
        ),
    ),
)

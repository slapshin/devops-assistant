"""Search engine rules: Meilisearch degraded searches, search queue, task queue and indexes.

Meilisearch search and document API traffic reuses the HTTP rules.
"""

from app.analysis.rules.base import Dir, Rule, RuleKind
from app.domain.common import Severity, SignalFamily

F = SignalFamily
SEARCH_RULES = (
    Rule(
        "search_degraded_ratio",
        F.SEARCH,
        "Degraded searches",
        thresholds="search_degraded_ratio",
        volume_guard=True,
        volume_noun="searches",
        title_up="Degraded-search share above expected range",
        summary=(
            "A larger share of searches hit the search cutoff (searchCutoffMs) and returned "
            "partial, possibly worse-ranked results."
        ),
        help=(
            "Look for heavier queries (many words, large filters, facets, sort on big "
            "attributes), concurrent indexing competing for CPU and disk, and memory pressure "
            "evicting the index from the page cache."
        ),
    ),
    Rule(
        "search_queue_ratio",
        F.SEARCH,
        "Search queue",
        thresholds="search_queue_ratio",
        title_up="More searches waiting for a search thread",
        summary=(
            "More searches waited for a free search thread; when the queue is full, "
            "Meilisearch rejects searches with 503."
        ),
        help=(
            "Compare the search rate and latency: more traffic or slower searches fill the "
            "queue. Check CPU saturation and indexing load, and add CPU or replicas."
        ),
    ),
    Rule(
        "search_task_queue_latency",
        F.SEARCH,
        "Task queue latency",
        thresholds="search_task_queue_latency",
        title_up="Tasks wait longer to be processed",
        summary=(
            "The oldest unfinished task waited longer, so document and settings changes take "
            "longer to become searchable."
        ),
        help=(
            "Look for settings updates (they reindex every document), unusually large "
            "document batches, many small writes that could be batched, and CPU or disk "
            "contention on the server."
        ),
    ),
    Rule(
        "search_task_backlog",
        F.SEARCH,
        "Enqueued tasks",
        thresholds="search_task_backlog",
        title_up="More tasks waiting in the queue",
        summary="More tasks waited in the queue: indexing falls behind the incoming writes.",
        help=(
            "Check the task queue latency and which task types pile up (/tasks?statuses="
            "enqueued), then batch writes or reduce the indexing load."
        ),
    ),
    Rule(
        "search_task_failures",
        F.SEARCH,
        "New failed tasks",
        kind=RuleKind.EVENT,
        event_points=2,
        severity_cap=Severity.MEDIUM,
        title_up="New failed Meilisearch tasks",
        summary=(
            "Tasks failed, so their document or settings changes were not applied to the index."
        ),
        help=(
            "List them with /tasks?statuses=failed and read each error: invalid documents, "
            "a missing primary key, payload limits or a full task database."
        ),
    ),
    Rule(
        "search_task_db_ratio",
        F.SEARCH,
        "Task database used",
        thresholds="search_task_db_ratio",
        summary=(
            "The task database came closer to the size at which Meilisearch stops registering "
            "new tasks and rejects writes."
        ),
        help=(
            "Delete finished tasks (DELETE /tasks?statuses=succeeded,failed,canceled), fewer "
            "and larger write batches keep the history small; raise --max-task-db-size if "
            "the history is needed."
        ),
    ),
    Rule(
        "search_index_documents",
        F.SEARCH,
        "Documents in index",
        thresholds="search_index_documents",
        direction=Dir.DOWN,
        title_down="Documents in the index dropped",
        summary="The index holds markedly fewer documents than before.",
        help=(
            "Check recent document deletion and index swap or deletion tasks (/tasks?types="
            "documentDeletion,indexSwap,indexDeletion) and the jobs that sync the index."
        ),
    ),
)

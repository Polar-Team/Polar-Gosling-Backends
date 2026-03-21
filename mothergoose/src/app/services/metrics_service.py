"""
Metrics Service

Collects and exposes metrics for runner provisioning, job execution,
and pool sizes in Prometheus text format.

Requirements: 15.1, 15.2, 15.3, 15.7
"""

import time
from typing import Dict, List, Optional

from app.util.base_logging import logger

# Default histogram buckets for duration metrics (seconds)
DEFAULT_DURATION_BUCKETS: List[float] = [
    5.0,
    15.0,
    30.0,
    60.0,
    120.0,
    300.0,
    600.0,
    1800.0,
    3600.0,
]


def _labels_key(labels: Optional[Dict[str, str]]) -> str:
    """Convert a labels dict to a Prometheus label string, e.g. {a="b",c="d"}."""
    if not labels:
        return ""
    parts = [f'{k}="{v}"' for k, v in sorted(labels.items())]
    return "{" + ",".join(parts) + "}"


def _render_histogram_series(  # pylint: disable=too-many-locals
    name: str,
    hist_map: Dict[str, List[float]],
    bkts: List[float],
) -> List[str]:
    """Render one histogram metric's label series into Prometheus lines."""
    lines: List[str] = []
    for label_key, observations in hist_map.items():
        total: float = sum(observations)
        count: int = len(observations)
        base_labels = label_key.rstrip("}") if label_key else ""
        for bucket in bkts:
            le_count: int = sum(1 for o in observations if o <= bucket)
            if base_labels and base_labels != "{":
                bucket_labels = base_labels + f',le="{bucket}"' + "}"
            else:
                bucket_labels = "{" + f'le="{bucket}"' + "}"
            lines.append(f"{name}_bucket{bucket_labels} {le_count}")
        inf_labels = (
            (base_labels + ',le="+Inf"}')
            if (base_labels and base_labels != "{")
            else '{le="+Inf"}'
        )
        lines.append(f"{name}_bucket{inf_labels} {count}")
        lines.append(f"{name}_sum{label_key} {total}")
        lines.append(f"{name}_count{label_key} {count}")
    return lines


class MetricsRegistry:
    """
    In-process metrics registry.

    Stores counters, gauges, and histograms in memory and renders
    them as Prometheus text exposition format.
    """

    def __init__(self) -> None:
        self._counters: Dict[str, Dict[str, float]] = {}
        self._counter_help: Dict[str, str] = {}
        self._gauges: Dict[str, Dict[str, float]] = {}
        self._gauge_help: Dict[str, str] = {}
        self._histograms: Dict[str, Dict[str, List[float]]] = {}
        self._histogram_help: Dict[str, str] = {}
        self._histogram_buckets: Dict[str, List[float]] = {}

    # ------------------------------------------------------------------
    # Counter helpers
    # ------------------------------------------------------------------

    def counter_inc(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        name: str,
        labels: Optional[Dict[str, str]] = None,
        value: float = 1.0,
        help_text: str = "",
    ) -> None:
        """Increment a counter metric."""
        if name not in self._counters:
            self._counters[name] = {}
            self._counter_help[name] = help_text
        label_key = _labels_key(labels)
        self._counters[name][label_key] = (
            self._counters[name].get(label_key, 0.0) + value
        )

    def counter_get(self, name: str, labels: Optional[Dict[str, str]] = None) -> float:
        """Return current counter value (0 if not set)."""
        return self._counters.get(name, {}).get(_labels_key(labels), 0.0)

    # ------------------------------------------------------------------
    # Gauge helpers
    # ------------------------------------------------------------------

    def gauge_set(
        self,
        name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None,
        help_text: str = "",
    ) -> None:
        """Set a gauge metric to an absolute value."""
        if name not in self._gauges:
            self._gauges[name] = {}
            self._gauge_help[name] = help_text
        self._gauges[name][_labels_key(labels)] = value

    def gauge_get(self, name: str, labels: Optional[Dict[str, str]] = None) -> float:
        """Return current gauge value (0 if not set)."""
        return self._gauges.get(name, {}).get(_labels_key(labels), 0.0)

    # ------------------------------------------------------------------
    # Histogram helpers
    # ------------------------------------------------------------------

    def histogram_observe(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        name: str,
        value: float,
        labels: Optional[Dict[str, str]] = None,
        help_text: str = "",
        buckets: Optional[List[float]] = None,
    ) -> None:
        """Record an observation in a histogram."""
        if name not in self._histograms:
            self._histograms[name] = {}
            self._histogram_help[name] = help_text
            self._histogram_buckets[name] = sorted(buckets or DEFAULT_DURATION_BUCKETS)
        label_key = _labels_key(labels)
        if label_key not in self._histograms[name]:
            self._histograms[name][label_key] = []
        self._histograms[name][label_key].append(value)

    def histogram_observations(
        self, name: str, labels: Optional[Dict[str, str]] = None
    ) -> List[float]:
        """Return raw observations for a histogram label set."""
        return list(self._histograms.get(name, {}).get(_labels_key(labels), []))

    # ------------------------------------------------------------------
    # Prometheus text format rendering
    # ------------------------------------------------------------------

    def render(self) -> str:
        """Render all metrics in Prometheus text exposition format."""
        lines: List[str] = []

        for name, counter_map in self._counters.items():
            help_text = self._counter_help.get(name, "")
            if help_text:
                lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} counter")
            for label_key, val in counter_map.items():
                lines.append(f"{name}{label_key} {val}")

        for name, gauge_map in self._gauges.items():
            help_text = self._gauge_help.get(name, "")
            if help_text:
                lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} gauge")
            for label_key, val in gauge_map.items():
                lines.append(f"{name}{label_key} {val}")

        for name, hist_map in self._histograms.items():
            help_text = self._histogram_help.get(name, "")
            if help_text:
                lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} histogram")
            bkts = self._histogram_buckets.get(name, DEFAULT_DURATION_BUCKETS)
            lines.extend(_render_histogram_series(name, hist_map, bkts))

        return "\n".join(lines) + ("\n" if lines else "")


# ---------------------------------------------------------------------------
# Module-level singleton registry
# ---------------------------------------------------------------------------

METRICS_REGISTRY: Optional[MetricsRegistry] = None


def get_registry() -> MetricsRegistry:
    """Return the global MetricsRegistry singleton, creating it if needed."""
    global METRICS_REGISTRY  # pylint: disable=global-statement
    if METRICS_REGISTRY is None:
        METRICS_REGISTRY = MetricsRegistry()
    return METRICS_REGISTRY


# ---------------------------------------------------------------------------
# MetricsService — high-level API used by orchestration code
# ---------------------------------------------------------------------------


class MetricsService:
    """
    High-level metrics service for MotherGoose.

    Wraps MetricsRegistry with domain-specific methods for recording
    runner provisioning time, job execution duration, and pool sizes.

    Requirements: 15.1, 15.2, 15.3
    """

    # Metric names
    RUNNER_PROVISIONING_TOTAL = "mothergoose_runner_provisioning_total"
    RUNNER_PROVISIONING_DURATION = "mothergoose_runner_provisioning_duration_seconds"
    RUNNER_TERMINATION_TOTAL = "mothergoose_runner_termination_total"
    JOB_EXECUTION_TOTAL = "mothergoose_job_execution_total"
    JOB_EXECUTION_DURATION = "mothergoose_job_execution_duration_seconds"
    POOL_SIZE_APEX = "mothergoose_pool_size_apex"
    POOL_SIZE_NADIR = "mothergoose_pool_size_nadir"
    WEBHOOK_EVENTS_TOTAL = "mothergoose_webhook_events_total"
    GIT_SYNC_TOTAL = "mothergoose_git_sync_total"
    GIT_SYNC_DURATION = "mothergoose_git_sync_duration_seconds"

    def __init__(self, registry: Optional[MetricsRegistry] = None) -> None:
        self._registry = registry or get_registry()

    # ------------------------------------------------------------------
    # Runner provisioning metrics (Requirement 15.1)
    # ------------------------------------------------------------------

    def record_runner_provisioned(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        egg_name: str,
        runner_type: str,
        cloud_provider: str,
        status: str,
        duration_seconds: float,
    ) -> None:
        """
        Record a runner provisioning event.

        Args:
            egg_name: Name of the Egg
            runner_type: Type of runner (serverless/apex/nadir)
            cloud_provider: Cloud provider (yandex/aws)
            status: Outcome (success/failure)
            duration_seconds: Time taken to provision
        """
        labels = {
            "egg_name": egg_name,
            "runner_type": runner_type,
            "cloud_provider": cloud_provider,
            "status": status,
        }
        self._registry.counter_inc(
            self.RUNNER_PROVISIONING_TOTAL,
            labels=labels,
            help_text="Total number of runner provisioning attempts",
        )
        self._registry.histogram_observe(
            self.RUNNER_PROVISIONING_DURATION,
            value=duration_seconds,
            labels={"egg_name": egg_name, "runner_type": runner_type},
            help_text="Runner provisioning duration in seconds",
        )
        logger.info(
            "Metric: runner provisioned egg=%s type=%s cloud=%s status=%s duration=%.2fs",
            egg_name,
            runner_type,
            cloud_provider,
            status,
            duration_seconds,
        )

    def record_runner_terminated(
        self,
        egg_name: str,
        runner_type: str,
        reason: str,
    ) -> None:
        """
        Record a runner termination event.

        Args:
            egg_name: Name of the Egg
            runner_type: Type of runner
            reason: Termination reason
        """
        labels = {"egg_name": egg_name, "runner_type": runner_type, "reason": reason}
        self._registry.counter_inc(
            self.RUNNER_TERMINATION_TOTAL,
            labels=labels,
            help_text="Total number of runner terminations",
        )

    # ------------------------------------------------------------------
    # Job execution metrics (Requirement 15.2)
    # ------------------------------------------------------------------

    def record_job_execution(
        self,
        egg_name: str,
        runner_type: str,
        status: str,
        duration_seconds: float,
    ) -> None:
        """
        Record a job execution event.

        Args:
            egg_name: Name of the Egg
            runner_type: Type of runner that executed the job
            status: Job outcome (success/failure/timeout)
            duration_seconds: Job execution duration
        """
        labels = {
            "egg_name": egg_name,
            "runner_type": runner_type,
            "status": status,
        }
        self._registry.counter_inc(
            self.JOB_EXECUTION_TOTAL,
            labels=labels,
            help_text="Total number of job executions",
        )
        self._registry.histogram_observe(
            self.JOB_EXECUTION_DURATION,
            value=duration_seconds,
            labels={"egg_name": egg_name, "runner_type": runner_type},
            help_text="Job execution duration in seconds",
        )

    # ------------------------------------------------------------------
    # Pool size metrics (Requirement 15.3)
    # ------------------------------------------------------------------

    def record_pool_sizes(
        self,
        egg_name: str,
        apex_count: int,
        nadir_count: int,
    ) -> None:
        """
        Record current Apex and Nadir pool sizes for an Egg.

        Args:
            egg_name: Name of the Egg
            apex_count: Number of active Apex runners
            nadir_count: Number of dormant Nadir runners
        """
        labels = {"egg_name": egg_name}
        self._registry.gauge_set(
            self.POOL_SIZE_APEX,
            value=float(apex_count),
            labels=labels,
            help_text="Current number of Apex (active) runners per Egg",
        )
        self._registry.gauge_set(
            self.POOL_SIZE_NADIR,
            value=float(nadir_count),
            labels=labels,
            help_text="Current number of Nadir (dormant) runners per Egg",
        )

    # ------------------------------------------------------------------
    # Webhook event metrics (Requirement 15.4)
    # ------------------------------------------------------------------

    def record_webhook_event(
        self,
        event_type: str,
        egg_name: str,
        status: str,
    ) -> None:
        """
        Record a webhook event.

        Args:
            event_type: GitLab event type (push/pipeline/merge_request)
            egg_name: Name of the matched Egg
            status: Processing outcome (accepted/rejected/error)
        """
        labels = {"event_type": event_type, "egg_name": egg_name, "status": status}
        self._registry.counter_inc(
            self.WEBHOOK_EVENTS_TOTAL,
            labels=labels,
            help_text="Total number of GitLab webhook events received",
        )

    # ------------------------------------------------------------------
    # Git sync metrics
    # ------------------------------------------------------------------

    def record_git_sync(
        self,
        sync_type: str,
        status: str,
        duration_seconds: float,
        eggs_synced: int = 0,
    ) -> None:
        """
        Record a Git sync operation.

        Args:
            sync_type: Sync trigger type (periodic/webhook)
            status: Outcome (success/failure)
            duration_seconds: Sync duration
            eggs_synced: Number of Eggs synced
        """
        labels = {"sync_type": sync_type, "status": status}
        self._registry.counter_inc(
            self.GIT_SYNC_TOTAL,
            labels=labels,
            help_text="Total number of Git sync operations",
        )
        self._registry.histogram_observe(
            self.GIT_SYNC_DURATION,
            value=duration_seconds,
            labels={"sync_type": sync_type},
            help_text="Git sync duration in seconds",
        )
        logger.info(
            "Metric: git sync type=%s status=%s duration=%.2fs eggs=%d",
            sync_type,
            status,
            duration_seconds,
            eggs_synced,
        )

    # ------------------------------------------------------------------
    # Convenience: render all metrics
    # ------------------------------------------------------------------

    def render(self) -> str:
        """Render all metrics in Prometheus text format."""
        return self._registry.render()


# ---------------------------------------------------------------------------
# Provisioning timer context manager
# ---------------------------------------------------------------------------


class ProvisioningTimer:
    """
    Context manager that measures elapsed time for a provisioning operation.

    Usage::

        with ProvisioningTimer() as t:
            await provision_runner(...)
        duration = t.elapsed
    """

    def __init__(self) -> None:
        self._start: float = 0.0
        self.elapsed: float = 0.0

    def __enter__(self) -> "ProvisioningTimer":
        self._start = time.monotonic()
        return self

    def __exit__(self, *_: object) -> None:
        self.elapsed = time.monotonic() - self._start

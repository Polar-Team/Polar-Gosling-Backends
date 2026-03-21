"""
Property-Based Tests: Monitoring and Observability

Validates that MotherGoose correctly emits metrics for runner provisioning,
job execution, pool sizes, and exposes a Prometheus-compatible /metrics endpoint.

Requirements: 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7
"""

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.metrics_service import (
    MetricsRegistry,
    MetricsService,
    ProvisioningTimer,
    _labels_key,
    get_registry,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry() -> MetricsRegistry:
    """Fresh MetricsRegistry for each test."""
    return MetricsRegistry()


@pytest.fixture()
def svc(registry: MetricsRegistry) -> MetricsService:
    """MetricsService backed by a fresh registry."""
    return MetricsService(registry=registry)


# ---------------------------------------------------------------------------
# Property 41: Runner Provisioning Metrics Monotonicity (Requirement 15.1)
# ---------------------------------------------------------------------------


@given(
    n=st.integers(min_value=1, max_value=50),
    egg_name=st.text(
        alphabet=st.characters(whitelist_categories=("Ll", "Lu", "Nd"), whitelist_characters="-_"),
        min_size=1,
        max_size=20,
    ),
    runner_type=st.sampled_from(["serverless", "apex", "nadir"]),
    cloud_provider=st.sampled_from(["yandex", "aws"]),
    status=st.sampled_from(["success", "failure"]),
    duration=st.floats(min_value=0.0, max_value=3600.0, allow_nan=False),
)
@settings(max_examples=30)
def test_runner_provisioning_counter_monotonically_increases(
    n: int,
    egg_name: str,
    runner_type: str,
    cloud_provider: str,
    status: str,
    duration: float,
) -> None:
    """
    Property 41: Runner Provisioning Metrics Monotonicity

    For any sequence of N provisioning events, the counter must equal N
    and the histogram must contain exactly N observations.

    Validates: Requirement 15.1
    """
    reg = MetricsRegistry()
    svc = MetricsService(registry=reg)

    for _ in range(n):
        svc.record_runner_provisioned(
            egg_name=egg_name,
            runner_type=runner_type,
            cloud_provider=cloud_provider,
            status=status,
            duration_seconds=duration,
        )

    labels = {
        "egg_name": egg_name,
        "runner_type": runner_type,
        "cloud_provider": cloud_provider,
        "status": status,
    }
    assert reg.counter_get(MetricsService.RUNNER_PROVISIONING_TOTAL, labels) == float(n)

    hist_labels = {"egg_name": egg_name, "runner_type": runner_type}
    observations = reg.histogram_observations(
        MetricsService.RUNNER_PROVISIONING_DURATION, hist_labels
    )
    assert len(observations) == n


# ---------------------------------------------------------------------------
# Property 42: Job Execution Metrics Completeness (Requirement 15.2)
# ---------------------------------------------------------------------------


@given(
    events=st.lists(
        st.tuples(
            st.text(
                alphabet=st.characters(
                    whitelist_categories=("Ll",), whitelist_characters="-"
                ),
                min_size=1,
                max_size=15,
            ),
            st.sampled_from(["serverless", "apex"]),
            st.sampled_from(["success", "failure", "timeout"]),
            st.floats(min_value=0.1, max_value=3600.0, allow_nan=False),
        ),
        min_size=1,
        max_size=20,
    )
)
@settings(max_examples=30)
def test_job_execution_metrics_completeness(
    events: list,
) -> None:
    """
    Property 42: Job Execution Metrics Completeness

    Every recorded job execution must appear in both the counter and histogram.
    The total count across all label combinations must equal the number of events.

    Validates: Requirement 15.2
    """
    reg = MetricsRegistry()
    svc = MetricsService(registry=reg)

    for egg_name, runner_type, status, duration in events:
        svc.record_job_execution(
            egg_name=egg_name,
            runner_type=runner_type,
            status=status,
            duration_seconds=duration,
        )

    # Total observations across all label sets must equal total events
    total_observations = sum(
        len(obs)
        for obs in reg._histograms.get(MetricsService.JOB_EXECUTION_DURATION, {}).values()
    )
    assert total_observations == len(events)

    # Total counter increments must equal total events
    total_counter = sum(
        v for v in reg._counters.get(MetricsService.JOB_EXECUTION_TOTAL, {}).values()
    )
    assert total_counter == float(len(events))


# ---------------------------------------------------------------------------
# Property 43: Pool Size Gauge Reflects Latest Value (Requirement 15.3)
# ---------------------------------------------------------------------------


@given(
    egg_name=st.text(
        alphabet=st.characters(whitelist_categories=("Ll",), whitelist_characters="-"),
        min_size=1,
        max_size=15,
    ),
    updates=st.lists(
        st.tuples(
            st.integers(min_value=0, max_value=100),
            st.integers(min_value=0, max_value=100),
        ),
        min_size=1,
        max_size=20,
    ),
)
@settings(max_examples=30)
def test_pool_size_gauge_reflects_latest_value(
    egg_name: str,
    updates: list,
) -> None:
    """
    Property 43: Pool Size Gauge Reflects Latest Value

    After a sequence of pool size updates, the gauge must reflect the
    most recently recorded value (gauges are not cumulative).

    Validates: Requirement 15.3
    """
    reg = MetricsRegistry()
    svc = MetricsService(registry=reg)

    for apex, nadir in updates:
        svc.record_pool_sizes(egg_name=egg_name, apex_count=apex, nadir_count=nadir)

    last_apex, last_nadir = updates[-1]
    labels = {"egg_name": egg_name}

    assert reg.gauge_get(MetricsService.POOL_SIZE_APEX, labels) == float(last_apex)
    assert reg.gauge_get(MetricsService.POOL_SIZE_NADIR, labels) == float(last_nadir)


# ---------------------------------------------------------------------------
# Property 44: Prometheus Output Parseable (Requirement 15.7)
# ---------------------------------------------------------------------------


@given(
    egg_name=st.text(
        alphabet=st.characters(whitelist_categories=("Ll",), whitelist_characters="-"),
        min_size=1,
        max_size=10,
    ),
    apex=st.integers(min_value=0, max_value=50),
    nadir=st.integers(min_value=0, max_value=50),
    prov_count=st.integers(min_value=0, max_value=10),
    duration=st.floats(min_value=1.0, max_value=300.0, allow_nan=False),
)
@settings(max_examples=20)
def test_prometheus_output_is_parseable(
    egg_name: str,
    apex: int,
    nadir: int,
    prov_count: int,
    duration: float,
) -> None:
    """
    Property 44: Prometheus Output Parseable

    The rendered metrics output must be valid Prometheus text format:
    - Lines starting with # are comments (HELP or TYPE)
    - Metric lines contain a space-separated name+labels and value
    - All metric names referenced in TYPE lines must appear as data lines

    Validates: Requirement 15.7
    """
    reg = MetricsRegistry()
    svc = MetricsService(registry=reg)

    svc.record_pool_sizes(egg_name=egg_name, apex_count=apex, nadir_count=nadir)
    for _ in range(prov_count):
        svc.record_runner_provisioned(
            egg_name=egg_name,
            runner_type="serverless",
            cloud_provider="yandex",
            status="success",
            duration_seconds=duration,
        )

    output = svc.render()

    # Must be a string
    assert isinstance(output, str)

    # Every non-comment, non-empty line must have a space (name value)
    for line in output.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        assert " " in line, f"Metric line missing space: {line!r}"

    # Pool size metric names must appear in output when values are set
    if apex > 0 or nadir > 0:
        assert MetricsService.POOL_SIZE_APEX in output
        assert MetricsService.POOL_SIZE_NADIR in output

    # Provisioning metric names must appear when events were recorded
    if prov_count > 0:
        assert MetricsService.RUNNER_PROVISIONING_TOTAL in output
        assert MetricsService.RUNNER_PROVISIONING_DURATION in output


# ---------------------------------------------------------------------------
# Unit tests: /health and /metrics endpoints (Requirements 15.6, 15.7)
# ---------------------------------------------------------------------------


def test_health_endpoint_returns_200() -> None:
    """
    Health check endpoint returns 200 with expected fields.

    Validates: Requirement 15.6
    """
    from app.main import app

    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert "timestamp" in body
    assert "version" in body
    assert body["service"] == "mothergoose"


def test_metrics_endpoint_returns_200() -> None:
    """
    /metrics endpoint returns 200 with Prometheus content-type.

    Validates: Requirement 15.7
    """
    from app.main import app

    client = TestClient(app)
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]


def test_metrics_endpoint_reflects_recorded_events() -> None:
    """
    Events recorded via MetricsService appear in /metrics output.

    Validates: Requirements 15.1, 15.3, 15.7
    """
    from app.main import app
    from app.services.metrics_service import get_registry

    # Record a pool size event into the global registry
    svc = MetricsService(registry=get_registry())
    svc.record_pool_sizes(egg_name="test-egg", apex_count=3, nadir_count=1)

    client = TestClient(app)
    response = client.get("/metrics")

    assert response.status_code == 200
    assert MetricsService.POOL_SIZE_APEX in response.text
    assert MetricsService.POOL_SIZE_NADIR in response.text


# ---------------------------------------------------------------------------
# Unit tests: MetricsRegistry internals
# ---------------------------------------------------------------------------


def test_counter_starts_at_zero(registry: MetricsRegistry) -> None:
    """Counter returns 0 before any increments."""
    assert registry.counter_get("nonexistent") == 0.0


def test_gauge_starts_at_zero(registry: MetricsRegistry) -> None:
    """Gauge returns 0 before any sets."""
    assert registry.gauge_get("nonexistent") == 0.0


def test_histogram_starts_empty(registry: MetricsRegistry) -> None:
    """Histogram returns empty list before any observations."""
    assert registry.histogram_observations("nonexistent") == []


def test_labels_key_empty() -> None:
    """Empty labels produce empty string."""
    assert _labels_key(None) == ""
    assert _labels_key({}) == ""


def test_labels_key_sorted() -> None:
    """Labels are sorted alphabetically for consistent keys."""
    key = _labels_key({"z": "last", "a": "first"})
    assert key == '{a="first",z="last"}'


def test_render_empty_registry(registry: MetricsRegistry) -> None:
    """Empty registry renders to empty string."""
    assert registry.render() == ""


def test_render_counter_format(registry: MetricsRegistry) -> None:
    """Counter renders with TYPE line and value line."""
    registry.counter_inc("my_counter", labels={"env": "test"}, help_text="A counter")
    output = registry.render()
    assert "# HELP my_counter A counter" in output
    assert "# TYPE my_counter counter" in output
    assert 'my_counter{env="test"} 1.0' in output


def test_render_gauge_format(registry: MetricsRegistry) -> None:
    """Gauge renders with TYPE line and value line."""
    registry.gauge_set("my_gauge", 42.0, labels={"env": "test"}, help_text="A gauge")
    output = registry.render()
    assert "# TYPE my_gauge gauge" in output
    assert 'my_gauge{env="test"} 42.0' in output


def test_render_histogram_format(registry: MetricsRegistry) -> None:
    """Histogram renders bucket, sum, and count lines."""
    registry.histogram_observe(
        "my_hist",
        30.0,
        labels={"type": "vm"},
        help_text="A histogram",
        buckets=[10.0, 60.0, 300.0],
    )
    output = registry.render()
    assert "# TYPE my_hist histogram" in output
    assert "my_hist_sum" in output
    assert "my_hist_count" in output
    assert "my_hist_bucket" in output
    assert 'le="60.0"' in output
    assert 'le="+Inf"' in output


def test_provisioning_timer_measures_elapsed() -> None:
    """ProvisioningTimer records non-negative elapsed time."""
    with ProvisioningTimer() as t:
        pass
    assert t.elapsed >= 0.0


def test_get_registry_returns_singleton() -> None:
    """get_registry() returns the same instance on repeated calls."""
    r1 = get_registry()
    r2 = get_registry()
    assert r1 is r2


def test_webhook_metric_recorded(svc: MetricsService, registry: MetricsRegistry) -> None:
    """Webhook events are counted per event_type/egg_name/status."""
    svc.record_webhook_event(
        event_type="push", egg_name="my-egg", status="accepted"
    )
    svc.record_webhook_event(
        event_type="push", egg_name="my-egg", status="accepted"
    )
    labels = {"event_type": "push", "egg_name": "my-egg", "status": "accepted"}
    assert registry.counter_get(MetricsService.WEBHOOK_EVENTS_TOTAL, labels) == 2.0


def test_git_sync_metric_recorded(svc: MetricsService, registry: MetricsRegistry) -> None:
    """Git sync events are counted and timed."""
    svc.record_git_sync(
        sync_type="periodic", status="success", duration_seconds=1.5, eggs_synced=3
    )
    labels = {"sync_type": "periodic", "status": "success"}
    assert registry.counter_get(MetricsService.GIT_SYNC_TOTAL, labels) == 1.0
    obs = registry.histogram_observations(
        MetricsService.GIT_SYNC_DURATION, {"sync_type": "periodic"}
    )
    assert obs == [1.5]

"""Periodic POSTer that emulates a YC Timer / AWS EventBridge rule."""

import datetime as dt
import logging
import os
import sys
import time

import httpx

LOG = logging.getLogger("trigger-emulator")
DEFAULT_INTERVAL: int = 60
MIN_INTERVAL: int = 5
MAX_INTERVAL: int = 3600
REQUEST_TIMEOUT: int = 10  # seconds, R5.1


def parse_interval(raw: str | None) -> int:
    """Parse TRIGGER_SYNC_INTERVAL_SECONDS — return 60 on any invalid input (R5.2, R5.3)."""
    if raw is None or raw.strip() == "":
        return DEFAULT_INTERVAL
    try:
        value = int(raw)
    except ValueError:
        LOG.error(
            "TRIGGER_SYNC_INTERVAL_SECONDS=%r is not an integer; falling back to %d",
            raw,
            DEFAULT_INTERVAL,
        )
        return DEFAULT_INTERVAL
    if value < MIN_INTERVAL or value > MAX_INTERVAL:
        LOG.error(
            "TRIGGER_SYNC_INTERVAL_SECONDS=%d is outside [%d, %d]; falling back to %d",
            value,
            MIN_INTERVAL,
            MAX_INTERVAL,
            DEFAULT_INTERVAL,
        )
        return DEFAULT_INTERVAL
    return value


def main() -> None:
    """Entry point — validate token, then loop forever."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    token = os.environ.get("INTERNAL_SYNC_TOKEN", "").strip()
    if not token:
        LOG.error("INTERNAL_SYNC_TOKEN is unset or empty — exiting")
        sys.exit(1)

    interval = parse_interval(os.environ.get("TRIGGER_SYNC_INTERVAL_SECONDS"))
    api_url = os.environ.get("MOTHERGOOSE_API_URL", "http://mothergoose-api:8000")
    url = f"{api_url}/internal/sync-git"

    LOG.info("trigger-emulator starting: interval=%ds url=%s", interval, url)

    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        while True:
            start = time.monotonic()
            ts = dt.datetime.now(dt.timezone.utc).isoformat()
            try:
                resp = client.post(url, headers={"X-Trigger-Auth": token})
                elapsed_ms = int((time.monotonic() - start) * 1000)
                if resp.is_success:
                    LOG.info("ts=%s status=%d duration_ms=%d", ts, resp.status_code, elapsed_ms)
                else:
                    LOG.warning(
                        "ts=%s status=%d duration_ms=%d reason=non_2xx", ts, resp.status_code, elapsed_ms
                    )
            except httpx.RequestError as exc:
                elapsed_ms = int((time.monotonic() - start) * 1000)
                LOG.warning("ts=%s status=error duration_ms=%d reason=%s", ts, elapsed_ms, type(exc).__name__)

            time.sleep(interval)


if __name__ == "__main__":
    main()

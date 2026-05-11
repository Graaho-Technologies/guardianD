from __future__ import annotations

# STUB — Phase 3 will implement Prometheus /metrics HTTP endpoint.
# Interface defined here so Phase 3 can implement without refactoring callers.


class PrometheusExporter:
    """Phase 3: Expose collected metrics in Prometheus text format."""

    def render(self, snapshots: dict) -> str:  # type: ignore[type-arg]
        """Return Prometheus-format text. Empty string until Phase 3."""
        return ""

    def start(self, host: str = "0.0.0.0", port: int = 9732) -> None:
        """Start HTTP server for /metrics. No-op until Phase 3."""
        pass

    def stop(self) -> None:
        pass

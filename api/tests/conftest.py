import pytest


@pytest.fixture(autouse=True)
def _no_route_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit tests never call adsbdb; tests that need it pass a mock transport."""
    monkeypatch.setenv("ROUTE_LOOKUP", "off")
    monkeypatch.setenv("WEATHER_LOOKUP", "off")

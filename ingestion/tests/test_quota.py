import pytest

from skyradar_ingestion.quota import (
    AccountTier,
    BoundingBox,
    credits_per_call,
    resolve_poll_interval,
    safe_poll_interval,
)


@pytest.mark.parametrize(
    ("bbox", "expected"),
    [
        (None, 4),
        ("45,5,50,10", 1),  # 25 sq deg
        ("40,0,50,10", 2),  # 100 sq deg
        ("30,-10,50,10", 3),  # 400 sq deg
        ("30,-10,60,20", 4),  # 900 sq deg
    ],
)
def test_credits_per_call(bbox: str | None, expected: int) -> None:
    assert credits_per_call(BoundingBox.parse(bbox) if bbox else None) == expected


@pytest.mark.parametrize(
    ("tier", "expected"),
    [(AccountTier.ANONYMOUS, 960), (AccountTier.STANDARD, 96), (AccountTier.FEEDER, 48)],
)
def test_safe_interval_global(tier: AccountTier, expected: int) -> None:
    assert safe_poll_interval(tier) == expected


@pytest.mark.parametrize("tier", list(AccountTier))
def test_safe_interval_never_exceeds_quota(tier: AccountTier) -> None:
    from skyradar_ingestion.quota import DAILY_CREDITS

    interval = safe_poll_interval(tier, margin=0.0)
    assert (86_400 / interval) * 4 <= DAILY_CREDITS[tier]


def test_interval_respects_min_resolution() -> None:
    # A tiny bbox with a huge quota would allow sub-second polling: clamp to 5 s.
    assert (
        safe_poll_interval(
            AccountTier.STANDARD, BoundingBox.parse("0,0,1,1"), daily_credits=1_000_000
        )
        == 5
    )


def test_resolve_auto_and_override() -> None:
    assert resolve_poll_interval("auto", AccountTier.STANDARD) == (96, "auto")
    assert resolve_poll_interval("120", AccountTier.STANDARD) == (120, "configured")
    value, reason = resolve_poll_interval("10", AccountTier.STANDARD)
    assert value == 96
    assert "raised" in reason


def test_bbox_validation() -> None:
    with pytest.raises(ValueError, match="Invalid"):
        BoundingBox.parse("50,0,40,10")
    with pytest.raises(ValueError, match="lamin"):
        BoundingBox.parse("1,2,3")

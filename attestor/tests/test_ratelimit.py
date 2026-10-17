import pytest

from attestor.ratelimit import RateLimiter


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_burst_then_block_then_refill():
    clk = Clock()
    rl = RateLimiter(burst=3, per_sec=1, clock=clk)
    assert [rl.check("ip")[0] for _ in range(3)] == [True, True, True]
    allowed, retry = rl.check("ip")
    assert not allowed and 0 < retry <= 1.0
    clk.t += 0.5
    assert not rl.check("ip")[0]
    clk.t += 0.5
    assert rl.check("ip")[0]
    assert not rl.check("ip")[0]


def test_refill_is_capped_at_burst():
    clk = Clock()
    rl = RateLimiter(burst=2, per_sec=10, clock=clk)
    clk.t += 3600
    assert [rl.check("ip")[0] for _ in range(3)] == [True, True, False]


def test_keys_are_independent():
    rl = RateLimiter(burst=1, per_sec=0.1, clock=Clock())
    assert rl.check("a")[0] and not rl.check("a")[0]
    assert rl.check("b")[0]


def test_retry_after_is_time_to_next_token():
    clk = Clock()
    rl = RateLimiter(burst=1, per_sec=0.5, clock=clk)
    rl.check("ip")
    assert rl.check("ip")[1] == pytest.approx(2.0)
    clk.t += 1.0
    assert rl.check("ip")[1] == pytest.approx(1.0)


def test_key_table_is_bounded():
    rl = RateLimiter(burst=1, per_sec=1, max_keys=100, clock=Clock())
    for i in range(1000):
        rl.check(f"spoofed-{i}")
    assert len(rl._buckets) == 100


def test_invalid_parameters():
    with pytest.raises(ValueError):
        RateLimiter(burst=0, per_sec=1)
    with pytest.raises(ValueError):
        RateLimiter(burst=1, per_sec=0)

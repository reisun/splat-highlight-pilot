"""Deterministic upload policy tests."""

import pytest

from app.upload_policy import UploadPolicy


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_grace_and_sustained_slow_upload():
    clock = Clock()
    policy = UploadPolicy(15_000_000_000, clock=clock)
    clock.advance(119)
    policy.note_received(1)
    clock.advance(1)
    policy.note_received(2)
    clock.advance(59)
    policy.note_received(3)
    clock.advance(1)
    with pytest.raises(ValueError, match="too slow"):
        policy.note_received(4)


def test_recovered_average_resets_slow_period():
    clock = Clock()
    policy = UploadPolicy(15_000_000_000, clock=clock)
    clock.advance(120)
    policy.note_received(1)
    clock.advance(30)
    policy.note_received(1_000_000_000)
    clock.advance(150)
    policy.note_received(1_000_000_000)
    clock.advance(59)
    policy.note_received(1_000_000_000)
    clock.advance(1)
    with pytest.raises(ValueError, match="too slow"):
        policy.note_received(1_000_000_000)


def test_small_file_uses_its_size_instead_of_15gb_rate_floor():
    clock = Clock()
    policy = UploadPolicy(1_000_000, clock=clock)
    # 1 KB/s is enough for this file although far below 15 GB/hour.
    for _ in range(10):
        clock.advance(60)
        policy.note_received(int(policy.elapsed * 1000))
    assert policy.remaining_seconds == 3000


def test_absolute_deadline_even_when_slow_detection_disabled_by_grace():
    clock = Clock()
    policy = UploadPolicy(15_000_000_000, clock=clock, grace=4000)
    clock.advance(3599)
    policy.note_received(14_999_999_999)
    assert policy.elapsed == 3599
    assert policy.remaining_seconds == 1
    clock.advance(1)
    with pytest.raises(ValueError, match="maximum duration"):
        policy.note_received(15_000_000_000)
    assert policy.remaining_seconds == 0


def test_no_bytes_and_custom_limits():
    clock = Clock()
    policy = UploadPolicy(100, clock=clock, max_seconds=100, grace=2, slow_seconds=3)
    clock.advance(2)
    policy.note_received(0)
    clock.advance(3)
    with pytest.raises(ValueError, match="too slow"):
        policy.note_received(0)


def test_byte_counts_are_monotonic_and_size_is_enforced():
    policy = UploadPolicy(100)
    policy.note_received(10)
    with pytest.raises(ValueError, match="cannot decrease"):
        policy.note_received(9)
    with pytest.raises(ValueError, match="declared file size"):
        policy.note_received(101)

"""Admission exclusion, durable recovery and crash behavior."""

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from app.admission import AdmissionController, AdmissionStateError


def test_independent_controllers_and_non_reentrant_claim(tmp_path):
    first = AdmissionController(tmp_path)
    second = AdmissionController(tmp_path)
    assert first.try_acquire()
    assert first.lock_fd is not None
    assert first.is_locked()
    assert second.is_locked()
    assert not first.try_acquire()
    assert not second.try_acquire()
    first.update(phase="uploading", received_bytes=10, total_bytes=20)
    assert second.snapshot()["received_bytes"] == 10
    # A controller without ownership cannot erase another owner's snapshot.
    second.release()
    assert first.snapshot()["phase"] == "uploading"
    first.release()
    assert first.lock_fd is None
    assert not first.is_locked()
    assert second.try_acquire()
    assert second.snapshot() is None
    second.close()
    assert (tmp_path / "admission.lock").exists()


def test_close_preserves_recovery_metadata(tmp_path):
    first = AdmissionController(tmp_path)
    assert first.try_acquire()
    first.update(phase="analyzing", backend_job_id="backend-1", progress=0.3)
    previous = first.snapshot()
    first.close()
    second = AdmissionController(tmp_path)
    assert second.try_acquire()
    assert second.snapshot() == previous
    second.update(progress=0.5, estimated_finish_at=None)
    assert second.snapshot()["backend_job_id"] == "backend-1"
    assert second.snapshot()["progress"] == 0.5
    assert "updated_at" in second.snapshot()
    second.release()
    assert second.snapshot() is None


def test_process_exit_releases_lock_but_preserves_record(tmp_path):
    code = """
import os
import sys
from app.admission import AdmissionController
controller = AdmissionController(sys.argv[1])
assert controller.try_acquire()
controller.update(phase='analyzing', backend_job_id='orphan-check')
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", code, str(tmp_path)], check=True)  # noqa: S603
    recovered = AdmissionController(tmp_path)
    assert recovered.try_acquire()
    assert recovered.snapshot()["backend_job_id"] == "orphan-check"
    recovered.release()


def test_child_descriptor_keeps_lock_after_parent_close(tmp_path):
    owner = AdmissionController(tmp_path)
    assert owner.try_acquire()
    owner.update(phase="clipping")
    child = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
        pass_fds=(owner.lock_fd,),
    )
    contender = AdmissionController(tmp_path)
    try:
        owner.close()
        assert not contender.try_acquire()
        assert contender.is_locked()
    finally:
        child.communicate(timeout=5)
    assert contender.try_acquire()
    assert contender.snapshot()["phase"] == "clipping"
    contender.release()
    assert not contender.is_locked()


@pytest.mark.parametrize("content", [b"{broken", b"[]", b"null", b"\xff"])
def test_malformed_snapshot_fails_closed_and_does_not_leak_lock(tmp_path, content):
    (tmp_path / "admission.json").write_bytes(content)
    owner = AdmissionController(tmp_path)
    with pytest.raises(AdmissionStateError):
        owner.try_acquire()
    assert owner.lock_fd is None
    assert (tmp_path / "admission.json").read_bytes() == content
    # After administrative repair, another controller can claim the lock.
    (tmp_path / "admission.json").write_text('{"phase": "recovering"}')
    recovered = AdmissionController(tmp_path)
    assert recovered.try_acquire()
    recovered.release()


def test_update_requires_ownership_and_invalid_values_preserve_state(tmp_path):
    owner = AdmissionController(tmp_path)
    with pytest.raises(RuntimeError, match="lock must be held"):
        owner.update(phase="uploading")
    assert owner.try_acquire()
    owner.update(phase="uploading")
    original = owner.snapshot()
    with pytest.raises(TypeError):
        owner.update(unserializable=object())
    assert owner.snapshot() == original
    owner.release()


def test_failed_atomic_replace_keeps_last_valid_state(tmp_path):
    owner = AdmissionController(tmp_path)
    assert owner.try_acquire()
    owner.update(phase="uploading", received_bytes=10)
    original = owner.snapshot()
    with (
        patch("app.admission.os.replace", side_effect=OSError("disk failure")),
        pytest.raises(OSError, match="disk failure"),
    ):
        owner.update(received_bytes=20)
    assert owner.snapshot() == original
    assert list(tmp_path.glob(".admission-*.json")) == []
    owner.release()


def test_concurrent_readers_always_see_complete_json(tmp_path):
    owner = AdmissionController(tmp_path)
    reader = AdmissionController(tmp_path)
    assert owner.try_acquire()
    owner.update(received_bytes=0, payload="x" * 10000)

    def publish():
        for number in range(100):
            owner.update(received_bytes=number)

    def read():
        for _ in range(100):
            state = reader.snapshot()
            assert isinstance(state["received_bytes"], int)
            assert state["payload"] == "x" * 10000
            json.loads((tmp_path / "admission.json").read_text())

    with ThreadPoolExecutor(max_workers=2) as pool:
        for result in [pool.submit(publish), pool.submit(read)]:
            result.result()
    owner.release()

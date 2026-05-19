from __future__ import annotations

import pytest

from guardian.utils.retry import retry


def test_retry_succeeds_first_attempt():
    call_count = {"n": 0}

    @retry(max_attempts=3, backoff_seconds=0)
    def ok():
        call_count["n"] += 1
        return "done"

    result = ok()
    assert result == "done"
    assert call_count["n"] == 1


def test_retry_retries_on_exception(mocker):
    mocker.patch("time.sleep")
    call_count = {"n": 0}

    @retry(max_attempts=3, backoff_seconds=0.01)
    def flaky():
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise ValueError("transient")
        return "ok"

    result = flaky()
    assert result == "ok"
    assert call_count["n"] == 3


def test_retry_raises_after_exhausted(mocker):
    mocker.patch("time.sleep")

    @retry(max_attempts=2, backoff_seconds=0.01)
    def always_fails():
        raise ConnectionError("down")

    with pytest.raises(ConnectionError, match="down"):
        always_fails()


def test_retry_specific_exception_type(mocker):
    mocker.patch("time.sleep")
    call_count = {"n": 0}

    @retry(max_attempts=3, backoff_seconds=0.01, exceptions=(ValueError,))
    def raises_value_error():
        call_count["n"] += 1
        raise ValueError("oops")

    with pytest.raises(ValueError):
        raises_value_error()
    assert call_count["n"] == 3


def test_retry_does_not_swallow_unspecified_exception(mocker):
    mocker.patch("time.sleep")

    @retry(max_attempts=3, backoff_seconds=0.01, exceptions=(ValueError,))
    def raises_runtime():
        raise RuntimeError("unexpected")

    with pytest.raises(RuntimeError, match="unexpected"):
        raises_runtime()


def test_retry_preserves_return_value(mocker):
    mocker.patch("time.sleep")

    @retry(max_attempts=3, backoff_seconds=0.01)
    def returns_dict():
        return {"key": "value", "n": 42}

    result = returns_dict()
    assert result == {"key": "value", "n": 42}


def test_retry_exponential_backoff_called(mocker):
    mock_sleep = mocker.patch("time.sleep")
    call_count = {"n": 0}

    @retry(max_attempts=3, backoff_seconds=1.0)
    def flaky():
        call_count["n"] += 1
        if call_count["n"] < 3:
            raise ValueError("retry me")
        return "ok"

    flaky()
    # First retry: 1.0 * 2^0 = 1.0, second retry: 1.0 * 2^1 = 2.0
    assert mock_sleep.call_count == 2
    calls = [c[0][0] for c in mock_sleep.call_args_list]
    assert calls[0] == pytest.approx(1.0)
    assert calls[1] == pytest.approx(2.0)

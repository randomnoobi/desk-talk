"""Errors in plain words (desktalk/domain/errors.py)."""

from __future__ import annotations

from desktalk.domain import plain_error


def test_plain_error_names_the_error_and_its_message():
    assert plain_error(ValueError('the camera is off')) == 'ValueError: the camera is off'


def test_plain_error_is_just_the_name_when_there_is_no_message():
    assert plain_error(TimeoutError()) == 'TimeoutError'


def test_the_runtime_still_offers_the_same_plain_error():
    from desktalk.domain.errors import plain_error as from_domain
    from desktalk.runtime import plain_error as from_runtime
    assert from_runtime is from_domain

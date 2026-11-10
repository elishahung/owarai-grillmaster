from __future__ import annotations

import threading

from grillmaster.core.stage_retry import StageRetry


def test_a_request_after_the_announcement_retries():
    stage_retry = StageRetry(poll_s=0.01)
    announced: list[int] = []

    def announce() -> None:
        announced.append(1)
        threading.Timer(0.01, stage_retry.request).start()

    assert stage_retry.offer(announce, over=lambda: False)
    assert announced == [1]


def test_an_offer_ends_unanswered_once_over():
    stage_retry = StageRetry(poll_s=0.01)

    assert not stage_retry.offer(lambda: None, over=lambda: True)


def test_a_request_racing_the_end_still_retries():
    stage_retry = StageRetry(poll_s=0.01)

    def over() -> bool:
        stage_retry.request()
        return True

    assert stage_retry.offer(lambda: None, over=over)


def test_a_request_before_the_offer_is_dropped():
    stage_retry = StageRetry(poll_s=0.01)
    stage_retry.request()

    assert not stage_retry.offer(lambda: None, over=lambda: True)


def test_an_offer_consumes_its_request():
    stage_retry = StageRetry(poll_s=0.01)

    assert stage_retry.offer(stage_retry.request, over=lambda: False)
    assert not stage_retry.offer(lambda: None, over=lambda: True)

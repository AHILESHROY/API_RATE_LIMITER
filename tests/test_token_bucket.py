
import time
from local.app import check_rate_limit, buckets, lock


def reset_bucket(api_key, endpoint):
    with lock:
        buckets.pop((api_key, endpoint), None)


def test_initial_bucket_capacity():
    key = "test-capacity"
    endpoint = "/test/capacity"
    reset_bucket(key, endpoint)

    # Ten tokens should allow ten requests.
    for _ in range(10):
        allowed, remaining, _ = check_rate_limit(key, endpoint)
        assert allowed is True

    # The next request should be rejected.
    allowed, remaining, retry_after = check_rate_limit(key, endpoint)
    assert allowed is False
    assert retry_after > 0

    reset_bucket(key, endpoint)


def test_token_replenishment():
    key = "test-refill"
    endpoint = "/test/refill"
    reset_bucket(key, endpoint)

    # Exhaust the bucket.
    for _ in range(10):
        allowed, _, _ = check_rate_limit(key, endpoint)
        assert allowed is True

    allowed, _, _ = check_rate_limit(key, endpoint)
    assert allowed is False

    # Wait for a token to replenish.
    time.sleep(6.2)

    allowed, remaining, _ = check_rate_limit(key, endpoint)
    assert allowed is True

    reset_bucket(key, endpoint)


def test_separate_clients_have_separate_buckets():
    key1 = "client-one"
    key2 = "client-two"
    endpoint = "/test/clients"

    reset_bucket(key1, endpoint)
    reset_bucket(key2, endpoint)

    for _ in range(10):
        check_rate_limit(key1, endpoint)

    allowed, _, _ = check_rate_limit(key1, endpoint)
    assert allowed is False

    # Client two should still have a full bucket.
    allowed, remaining, _ = check_rate_limit(key2, endpoint)
    assert allowed is True
    assert remaining == 9

    reset_bucket(key1, endpoint)
    reset_bucket(key2, endpoint)

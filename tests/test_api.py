
from local.app import app, buckets, lock
def reset_bucket(api_key, endpoint):
    with lock:
        buckets.pop((api_key, endpoint), None)


def test_health_endpoint():
    client = app.test_client()

    response = client.get("/health")

    assert response.status_code == 200


def test_missing_api_key():
    client = app.test_client()

    response = client.get("/api/v1/data")

    assert response.status_code == 401


def test_rate_limit_returns_429():
    client = app.test_client()
    api_key = "test-key-123"
    endpoint = "/api/v1/data"

    reset_bucket(api_key, endpoint)

    headers = {"X-API-Key": api_key}

    # The first 10 requests should succeed.
    for _ in range(10):
        response = client.get(endpoint, headers=headers)
        assert response.status_code == 200

    # The next request should be rejected.
    response = client.get(endpoint, headers=headers)

    assert response.status_code == 429
    assert "Retry-After" in response.headers

    reset_bucket(api_key, endpoint)

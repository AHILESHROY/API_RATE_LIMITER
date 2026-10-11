
from flask import Flask, request, jsonify
from collections import defaultdict
from datetime import datetime, timezone
import os
import time
import threading

app = Flask(__name__)

# ==========================================
# CONFIGURATION
# ==========================================

BUCKET_CAPACITY = 10
REFILL_RATE = 1 / 6  # One token every 6 seconds

VALID_API_KEYS = {
    key.strip()
    for key in os.getenv(
        "DEV_API_KEYS",
        "test-key-123,test-key-456"
    ).split(",")
    if key.strip()
}

# ==========================================
# TEMPORARY IN-MEMORY STORAGE
# ==========================================

# Bucket state for each API key and endpoint:
# (api_key, endpoint) -> {
#     "tokens": float,
#     "last_refill": float
# }

buckets = {}
analytics = []
lock = threading.Lock()


# ==========================================
# TOKEN BUCKET ALGORITHM
# ==========================================

def check_rate_limit(api_key, endpoint):
    """
    Return:
        allowed: whether the request is accepted
        remaining: current token balance
        retry_after: seconds until a token is available
    """

    now = time.time()
    bucket_key = (api_key, endpoint)

    with lock:
        # Create a full bucket for a new client/endpoint
        if bucket_key not in buckets:
            buckets[bucket_key] = {
                "tokens": float(BUCKET_CAPACITY),
                "last_refill": now
            }

        bucket = buckets[bucket_key]

        # Calculate tokens replenished since last update
        elapsed = max(0, now - bucket["last_refill"])
        replenished = elapsed * REFILL_RATE

        # Never exceed bucket capacity
        bucket["tokens"] = min(
            BUCKET_CAPACITY,
            bucket["tokens"] + replenished
        )

        bucket["last_refill"] = now

        # Accept request if a token is available
        if bucket["tokens"] >= 1:
            bucket["tokens"] -= 1

            return True, bucket["tokens"], 0

        # Calculate time until the next token is available
        retry_after = (1 - bucket["tokens"]) / REFILL_RATE

        return False, bucket["tokens"], retry_after


# ==========================================
# AUTHENTICATION AND RATE-LIMIT MIDDLEWARE
# ==========================================

@app.before_request
def authenticate_and_limit():

    request.start_time = time.time()

    # Health check is public
    if request.path == "/health":
        return None

    # Authenticate client
    api_key = request.headers.get("X-API-Key")

    if not api_key or api_key not in VALID_API_KEYS:
        return jsonify({
            "error": "Missing or invalid API key"
        }), 401

    request.api_key = api_key

    # Apply token bucket
    allowed, remaining, retry_after = check_rate_limit(
        api_key,
        request.path
    )

    request.remaining_tokens = remaining

    if not allowed:
        response = jsonify({
            "error": "Rate limit exceeded",
            "algorithm": "Token Bucket",
            "bucket_capacity": BUCKET_CAPACITY,
            "retry_after_seconds": round(retry_after, 2)
        })

        response.status_code = 429
        response.headers["Retry-After"] = str(
            max(1, int(retry_after + 0.999))
        )
        response.headers["X-RateLimit-Limit"] = str(
            BUCKET_CAPACITY
        )
        response.headers["X-RateLimit-Remaining"] = "0"

        return response

    return None


# ==========================================
# ANALYTICS AND RESPONSE HEADERS
# ==========================================

@app.after_request
def record_request(response):

    if request.path == "/health":
        return response

    api_key = getattr(request, "api_key", None)

    if api_key:
        elapsed_ms = round(
            (time.time() - request.start_time) * 1000,
            2
        )

        with lock:
            analytics.append({
                "api_key": api_key,
                "endpoint": request.path,
                "status_code": response.status_code,
                "response_time_ms": elapsed_ms,
                "timestamp": datetime.now(
                    timezone.utc
                ).isoformat()
            })

        response.headers["X-RateLimit-Limit"] = str(
            BUCKET_CAPACITY
        )

        response.headers["X-RateLimit-Remaining"] = str(
            max(
                0,
                int(getattr(request, "remaining_tokens", 0))
            )
        )

    return response


# ==========================================
# API ENDPOINTS
# ==========================================

@app.route("/health", methods=["GET"])
def health():

    return jsonify({
        "status": "healthy",
        "service": "multicloud-api-rate-limiter"
    }), 200


@app.route("/api/v1/data", methods=["GET"])
def get_data():

    return jsonify({
        "message": "Successfully accessed the API",
        "data": "Sample protected data",
        "algorithm": "Token Bucket"
    }), 200


@app.route("/api/v1/analytics", methods=["GET"])
def get_analytics():

    api_key = request.api_key

    with lock:
        records = [
            item for item in analytics
            if item["api_key"] == api_key
        ][-100:]

    # Do not expose the actual API key in analytics output
    return jsonify({
        "total_requests": len(records),
        "analytics": [
            {
                **item,
                "api_key": "REDACTED"
            }
            for item in records
        ]
    }), 200


# ==========================================
# START APPLICATION
# ==========================================

if __name__ == "__main__":

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=False
    )

from flask import Flask, request, jsonify, g
from google.cloud import firestore
from datetime import datetime, timezone
import os
import time
import math
import hashlib

app = Flask(__name__)

# ==========================================
# CONFIGURATION
# ==========================================

BUCKET_CAPACITY = 10
REFILL_RATE = 1 / 6  # One token every 6 seconds

PROJECT_ID = os.getenv(
    "GOOGLE_CLOUD_PROJECT",
    "api-rate-limiting"
)

DATABASE_ID = os.getenv(
    "FIRESTORE_DATABASE_ID",
    "api-rate-limiter-db"
)

VALID_API_KEYS = {
    key.strip()
    for key in os.getenv(
        "DEV_API_KEYS",
        "test-key-123,test-key-456"
    ).split(",")
    if key.strip()
}

db = firestore.Client(
    project=PROJECT_ID,
    database=DATABASE_ID
)


def hash_value(value):
    """Return a SHA-256 hash without storing a raw API key."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ==========================================
# FIRESTORE TOKEN BUCKET
# ==========================================

def check_rate_limit(api_key, endpoint):
    # Separate rate-limit buckets by API key and endpoint.
    bucket_id = hash_value(f"{api_key}|{endpoint}")

    bucket_ref = (
        db.collection("rate_limit_buckets")
        .document(bucket_id)
    )

    @firestore.transactional
    def update_bucket(transaction):
        snapshot = bucket_ref.get(transaction=transaction)
        now = time.time()

        if snapshot.exists:
            data = snapshot.to_dict() or {}
            tokens = float(
                data.get("tokens", BUCKET_CAPACITY)
            )
            last_refill = float(
                data.get("last_refill", now)
            )
        else:
            tokens = float(BUCKET_CAPACITY)
            last_refill = now

        elapsed = max(0.0, now - last_refill)

        tokens = min(
            float(BUCKET_CAPACITY),
            tokens + elapsed * REFILL_RATE
        )

        allowed = tokens >= 1.0

        if allowed:
            tokens -= 1.0
            retry_after = 0.0
        else:
            retry_after = (1.0 - tokens) / REFILL_RATE

        transaction.set(bucket_ref, {
            "tokens": tokens,
            "last_refill": now,
            "endpoint": endpoint,
            "updated_at": firestore.SERVER_TIMESTAMP
        })

        return allowed, tokens, retry_after

    return update_bucket(db.transaction())


# ==========================================
# REQUEST AUTHENTICATION AND RATE LIMITING
# ==========================================

@app.before_request
def authenticate_and_limit():
    g.request_start_time = time.time()

    # Public health endpoint.
    if request.path == "/health":
        return None

    # Only configured API keys are accepted.
    api_key = request.headers.get("X-API-Key")

    if not api_key or api_key not in VALID_API_KEYS:
        return jsonify({
            "error": "Missing or invalid API key"
        }), 401

    g.api_key = api_key
    g.api_key_hash = hash_value(api_key)

    try:
        allowed, remaining, retry_after = check_rate_limit(
            api_key,
            request.path
        )
    except Exception:
        app.logger.exception(
            "Firestore rate-limit operation failed"
        )
        return jsonify({
            "error": "Rate-limiting service temporarily unavailable"
        }), 503

    g.remaining_tokens = max(0, int(remaining))

    if not allowed:
        g.rate_limited = True

        response = jsonify({
            "error": "Rate limit exceeded",
            "algorithm": "Token Bucket",
            "bucket_capacity": BUCKET_CAPACITY,
            "retry_after_seconds": round(retry_after, 2)
        })

        response.status_code = 429
        response.headers["Retry-After"] = str(
            max(1, math.ceil(retry_after))
        )
        response.headers["X-RateLimit-Limit"] = str(
            BUCKET_CAPACITY
        )
        response.headers["X-RateLimit-Remaining"] = "0"

        return response

    g.rate_limited = False
    return None


# ==========================================
# REQUEST ANALYTICS
# ==========================================

@app.after_request
def record_request(response):
    if request.path == "/health":
        return response

    api_key_hash = getattr(g, "api_key_hash", None)

    # Record analytics only after successful API-key validation.
    if api_key_hash:
        elapsed_ms = round(
            (time.time() - g.request_start_time) * 1000,
            2
        )

        try:
            db.collection("api_analytics").add({
                "api_key_hash": api_key_hash,
                "endpoint": request.path,
                "status_code": response.status_code,
                "response_time_ms": elapsed_ms,
                "timestamp": datetime.now(
                    timezone.utc
                ).isoformat()
            })
        except Exception:
            # Analytics failure is logged without replacing
            # the original API response.
            app.logger.exception(
                "Failed to record analytics"
            )

        response.headers["X-RateLimit-Limit"] = str(
            BUCKET_CAPACITY
        )

        remaining = getattr(g, "remaining_tokens", 0)

        response.headers["X-RateLimit-Remaining"] = str(
            0 if response.status_code == 429
            else max(0, int(remaining))
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
    api_key_hash = g.api_key_hash

    try:
        query = (
            db.collection("api_analytics")
            .where(
                "api_key_hash",
                "==",
                api_key_hash
            )
            .order_by(
                "timestamp",
                direction=firestore.Query.DESCENDING
            )
            .limit(100)
        )

        records = []

        for doc in query.stream():
            record = doc.to_dict()
            record.pop("api_key_hash", None)
            records.append(record)

        return jsonify({
            "total_requests": len(records),
            "analytics": records
        }), 200

    except Exception:
        app.logger.exception(
            "Failed to retrieve analytics"
        )

        return jsonify({
            "error": "Analytics service temporarily unavailable"
        }), 503


# ==========================================
# START APPLICATION
# ==========================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8080")),
        debug=False
    )

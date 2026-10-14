from datetime import datetime, timezone

from google.cloud import firestore
from google.cloud import bigquery

# ==========================================
# CONFIGURATION
# ==========================================

PROJECT_ID = "api-rate-limiting"
DATABASE_ID = "api-rate-limiter-db"

DATASET_ID = "api_usage"
TABLE_ID = "request_events"

TABLE_REF = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"


# ==========================================
# CONNECT TO GOOGLE CLOUD
# ==========================================

firestore_db = firestore.Client(
    project=PROJECT_ID,
    database=DATABASE_ID
)

bigquery_client = bigquery.Client(
    project=PROJECT_ID,
    location="asia-south1"
)


# ==========================================
# TIMESTAMP CONVERSION
# ==========================================

def normalize_timestamp(value):
    """Convert Firestore timestamp values to ISO format."""

    if not value:
        return datetime.now(timezone.utc).isoformat()

    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc).isoformat()


# ==========================================
# EXPORT FIRESTORE ANALYTICS TO BIGQUERY
# ==========================================

def main():
    print("Reading analytics from Firestore...")

    collection = firestore_db.collection("api_analytics")
    documents = list(collection.stream())

    if not documents:
        print("No analytics records found in Firestore.")
        return

    rows = []
    row_ids = []

    for doc in documents:
        data = doc.to_dict() or {}

        try:
            endpoint = str(data["endpoint"])
            status_code = int(data["status_code"])
            response_time_ms = float(data["response_time_ms"])
            timestamp = normalize_timestamp(data.get("timestamp"))

        except (KeyError, TypeError, ValueError) as error:
            print(f"Skipping invalid document {doc.id}: {error}")
            continue

        rows.append({
            "cloud": "GCP",
            "endpoint": endpoint,
            "status_code": status_code,
            "response_time_ms": response_time_ms,
            "timestamp": timestamp
        })

        # Use the Firestore document ID as the insert row ID.
        row_ids.append(doc.id)

    if not rows:
        print("No valid records available to export.")
        return

    print(f"Uploading {len(rows)} records to BigQuery...")

    errors = bigquery_client.insert_rows_json(
        TABLE_REF,
        rows,
        row_ids=row_ids
    )

    if errors:
        print("BigQuery insert errors:")
        for error in errors:
            print(error)

        raise SystemExit("Export failed or was incomplete.")

    print("Export submitted successfully.")
    print(f"Records submitted: {len(rows)}")
    print(f"Destination: {TABLE_REF}")


if __name__ == "__main__":
    main()

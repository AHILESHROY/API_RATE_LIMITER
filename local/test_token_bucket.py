import requests
import time

URL = "http://127.0.0.1:5000/api/v1/data"
HEADERS = {"X-API-Key": "test-key-123"}

for i in range(1, 13):
    response = requests.get(URL, headers=HEADERS)

    print(
        f"Request {i}: "
        f"Status={response.status_code}, "
        f"Remaining={response.headers.get('X-RateLimit-Remaining')}"
    )

    if response.status_code == 429:
        print("Rate limit reached:", response.json())

    time.sleep(0.05)
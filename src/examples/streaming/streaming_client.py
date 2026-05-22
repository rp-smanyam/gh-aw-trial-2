import httpx
from httpx_sse import connect_sse

with httpx.Client(timeout=30) as client:
    url = "http://localhost:8000/v1/agent/stream"
    method = "POST"

    data = {
        "product": "resident_one_chat",
        "message": {"content": "have my packages arrived?", "message_id": "a3e8f9c0-4b2a-4d8e-9f3a-1234567890ab.15"},
    }
    with connect_sse(client, method, url, json=data) as event_source:
        for sse in event_source.iter_sse():
            print(sse.event, sse.data, sse.id, sse.retry)

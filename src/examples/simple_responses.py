from dotenv import load_dotenv

load_dotenv("../../.env")

from openai_setup import get_sync_client

client = get_sync_client()

response = client.responses.create(
    model="gpt-4.1",
    input=[
        {"role": "system", "content": "You are to be as helpful as possible."},
        {"role": "user", "content": "Hello. How are you?"},
    ],
)

print(response.output_text)

import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.getenv("OPENROUTER_API_KEY")
)

response = client.chat.completions.create(
    model="google/gemini-2.5-flash",
    messages=[
        {"role": "user", "content": "สวัสดีครับ ทดสอบภาษาไทย คุณคือใคร?"}
    ]
)

print(response.choices[0].message.content)
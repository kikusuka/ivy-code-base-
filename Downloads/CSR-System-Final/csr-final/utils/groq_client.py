import aiohttp
import asyncio
import os

MODELS = [
    'llama3-8b-8192',
    'llama3-70b-8192',
    'mixtral-8x7b-32768',
    'gemma-7b-it',
    'llama-3.1-8b-instant',
]
_idx = 0


def _next() -> str:
    global _idx
    m = MODELS[_idx % len(MODELS)]
    _idx += 1
    return m


async def groq_complete(prompt: str, max_tokens: int = 300, retries: int = 0) -> str | None:
    key = os.getenv('GROQ_API_KEY')
    if not key:
        return None
    for _ in range(len(MODELS)):
        model = _next()
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    'https://api.groq.com/openai/v1/chat/completions',
                    headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                    json={'model': model, 'messages': [{'role': 'user', 'content': prompt}],
                          'max_tokens': max_tokens, 'temperature': 0.6},
                    timeout=aiohttp.ClientTimeout(total=12)
                ) as r:
                    if r.status == 200:
                        d = await r.json()
                        return d['choices'][0]['message']['content'].strip()
                    elif r.status == 429:
                        await asyncio.sleep(1)
                        continue
        except Exception as e:
            print(f'Groq ({model}): {e}')
    return None


async def phrase_update(raw: str, game_name: str) -> str:
    result = await groq_complete(
        f'You are CSR System bot for a {game_name} Discord guild. '
        f'Rephrase this update into a short hype guild announcement (max 3 sentences). '
        f'No hashtags. End with "drop your thoughts below 💬".\n\nUpdate: {raw}',
        max_tokens=200
    )
    return result or raw

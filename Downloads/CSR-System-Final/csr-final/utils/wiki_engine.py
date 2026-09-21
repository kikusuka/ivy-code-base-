import aiohttp
import asyncio
import os
import re
import hashlib
from urllib.parse import quote
from utils.db import get_wiki_cache, set_wiki_cache, SERVER_TYPES

GEMINI_URL = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent'

WIKI_SOURCES = {
    'sbor':     ['https://swordbloxonlinerebirth.fandom.com/wiki/Special:Search?query={q}&scope=internal'],
    'bh2':      ['https://blue-heater-2.fandom.com/wiki/Special:Search?query={q}&scope=internal'],
    'bf':       ['https://blox-fruits.fandom.com/wiki/Special:Search?query={q}&scope=internal'],
    'warframe': [
        'https://wiki.warframe.com/w/{q}',
        'https://warframe.fandom.com/wiki/Special:Search?query={q}&scope=internal',
    ],
}

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/118.0.0.0 Safari/537.36',
]
_ua_idx = 0


def _next_ua() -> str:
    global _ua_idx
    ua = USER_AGENTS[_ua_idx % len(USER_AGENTS)]
    _ua_idx += 1
    return ua


def _cache_key(server_type: str, query: str) -> str:
    return hashlib.md5(f'{server_type}:{query.lower().strip()}'.encode()).hexdigest()


def _extract_text(html: str) -> str:
    """Lightweight HTML text extraction — no heavy libraries."""
    # Remove scripts, styles, navbars
    html = re.sub(r'<(script|style|nav|header|footer|aside)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
    # Remove tags
    text = re.sub(r'<[^>]+>', ' ', html)
    # Clean whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    # Return first 2000 chars of meaningful content
    return text[:2000]


async def scrape_wiki(server_type: str, query: str) -> tuple[str, str]:
    """Scrape wiki for query. Returns (content, source_url)."""
    cache_key = _cache_key(server_type, query)

    # Check cache first
    cached = get_wiki_cache(cache_key)
    if cached:
        return cached['content'], cached['source_url']

    sources = WIKI_SOURCES.get(server_type, [])
    q_encoded = quote(query.replace(' ', '_'))
    q_search = quote(query)

    for url_template in sources:
        url = url_template.replace('{q}', q_encoded).replace('{q}', q_search)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers={'User-Agent': _next_ua(), 'Accept-Language': 'en-US,en;q=0.9'},
                    timeout=aiohttp.ClientTimeout(total=8),
                    allow_redirects=True
                ) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        text = _extract_text(html)
                        if len(text) > 100:
                            set_wiki_cache(cache_key, text, str(resp.url))
                            return text, str(resp.url)
            await asyncio.sleep(0.5)  # Rate limit friendly
        except Exception as e:
            print(f'Wiki scrape error ({url}): {e}')
            continue

    return '', ''


async def search_youtube(query: str, game_name: str) -> tuple[str, str]:
    """Search YouTube for gaming guides. Returns (description snippet, video_url)."""
    api_key = os.getenv('YOUTUBE_API_KEY')
    if not api_key:
        return '', ''
    try:
        search_q = quote(f'{game_name} {query}')
        url = (f'https://www.googleapis.com/youtube/v3/search'
               f'?part=snippet&q={search_q}&type=video&maxResults=1'
               f'&relevanceLanguage=en&key={api_key}')
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get('items', [])
                    if items:
                        snippet = items[0]['snippet']
                        video_id = items[0]['id']['videoId']
                        desc = snippet.get('description', '')[:500]
                        title = snippet.get('title', '')
                        return f'YouTube: {title}\n{desc}', f'https://youtube.com/watch?v={video_id}'
    except Exception as e:
        print(f'YouTube search error: {e}')
    return '', ''


def is_gaming_question(question: str, server_type: str) -> bool:
    """Basic check that the question is gaming related."""
    game_info = SERVER_TYPES.get(server_type, {})
    game_name = game_info.get('name', '').lower()

    # Always allow if question mentions the game
    if any(w in question.lower() for w in game_name.split()):
        return True

    # Gaming keywords
    gaming_words = [
        'how', 'what', 'where', 'when', 'which', 'who', 'why',
        'weapon', 'item', 'quest', 'mission', 'boss', 'level', 'skill',
        'build', 'farm', 'craft', 'upgrade', 'unlock', 'drop', 'spawn',
        'damage', 'health', 'armor', 'stat', 'ability', 'power', 'rank',
        'fruit', 'sword', 'gun', 'warframe', 'prime', 'mod', 'relic',
        'ore', 'material', 'currency', 'guide', 'tips', 'best', 'location'
    ]
    return any(w in question.lower() for w in gaming_words)


async def answer_question(server_type: str, question: str) -> dict:
    """
    Full pipeline: scrape → YouTube fallback → Gemini answer.
    Returns dict with answer, source_url, yt_url, confidence.
    """
    game_info = SERVER_TYPES.get(server_type, {})
    game_name = game_info.get('name', 'the game')

    if not is_gaming_question(question, server_type):
        return {
            'answer': f'I only answer questions about **{game_name}**! Ask me about items, quests, builds, locations and more 🎮',
            'source_url': None,
            'yt_url': None,
            'confidence': 0,
        }

    # 1. Scrape wiki
    wiki_content, source_url = await scrape_wiki(server_type, question)

    # 2. YouTube fallback/supplement
    yt_content, yt_url = await search_youtube(question, game_name)

    # 3. Combine context
    context = ''
    if wiki_content:
        context += f'Wiki content:\n{wiki_content[:1200]}\n\n'
    if yt_content:
        context += f'YouTube guide:\n{yt_content[:400]}'

    confidence = 1 if wiki_content else (0.5 if yt_content else 0)

    if not context:
        return {
            'answer': f"I couldn't find specific info about that. Check the wiki directly! 📖",
            'source_url': game_info.get('wiki', ''),
            'yt_url': yt_url or None,
            'confidence': 0,
        }

    # 4. Gemini answer
    api_key = os.getenv('GEMINI_API_KEY')
    answer = None
    if api_key:
        prompt = (
            f'You are CSR System, a gaming assistant bot for {game_name}. '
            f'Answer the question conversationally using only the context below. '
            f'Keep it under 4 sentences. Be accurate. If context is insufficient say so.\n\n'
            f'Context:\n{context}\n\n'
            f'Question: {question}'
        )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f'{GEMINI_URL}?key={api_key}',
                    json={
                        'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
                        'generationConfig': {'maxOutputTokens': 350, 'temperature': 0.3},
                    },
                    timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        answer = data['candidates'][0]['content']['parts'][0]['text'].strip()
        except Exception as e:
            print(f'Gemini error: {e}')

    if not answer:
        # Fallback: serve raw wiki snippet
        answer = wiki_content[:400] + '...' if wiki_content else 'Could not generate answer right now.'

    return {
        'answer': answer,
        'source_url': source_url,
        'yt_url': yt_url,
        'confidence': confidence,
    }

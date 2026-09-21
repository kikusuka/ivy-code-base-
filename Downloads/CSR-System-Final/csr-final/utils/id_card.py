"""
CSR System — ID Card Generator
Generates a membership card image for each new member.
Pillow based, lightweight, no heavy deps.
"""

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
import aiohttp
import asyncio
import io
import os
from datetime import datetime, timezone
from pathlib import Path

ASSETS_DIR = Path('assets')
ASSETS_DIR.mkdir(exist_ok=True)

# Colors — Breezy era blue theme
C_BG_TOP     = (0,  20,  60)
C_BG_BOT     = (0,  10,  35)
C_ACCENT     = (0, 180, 216)
C_ACCENT2    = (0, 119, 182)
C_GLOW       = (72, 202, 228)
C_WHITE      = (255, 255, 255)
C_DIM        = (150, 200, 230)
C_STAMP      = (0, 200, 180)
C_DARK       = (0,   8,  25)

CARD_W, CARD_H = 800, 460


def _get_font(size: int, bold: bool = False):
    """Try to load a font, fall back to default."""
    try:
        font_path = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
        return ImageFont.truetype(font_path, size)
    except Exception:
        return ImageFont.load_default()


def _draw_rounded_rect(draw, xy, radius, fill, outline=None, outline_width=2):
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=fill,
                           outline=outline, width=outline_width)


def _draw_geometric_pattern(draw, w, h):
    """Draw subtle geometric lines in background."""
    for i in range(0, w + h, 60):
        alpha_col = (0, 40, 100, 30)
        draw.line([(i, 0), (0, i)], fill=(0, 40, 100), width=1)
    for i in range(0, w, 80):
        draw.line([(i, 0), (i, h)], fill=(0, 30, 80), width=1)


def _draw_glowing_border(draw, w, h):
    """Draw glowing border effect."""
    for offset, alpha in [(0, 255), (2, 180), (4, 100), (6, 50)]:
        color = (*C_ACCENT, alpha)
        draw.rectangle([offset, offset, w - offset, h - offset],
                       outline=C_ACCENT if offset == 0 else C_ACCENT2, width=1)


async def fetch_avatar(avatar_url: str) -> Image.Image | None:
    """Fetch Discord avatar and return as PIL Image."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(avatar_url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    img = Image.open(io.BytesIO(data)).convert('RGBA')
                    img = img.resize((110, 110), Image.LANCZOS)
                    return img
    except Exception as e:
        print(f'Avatar fetch error: {e}')
    return None


def _circular_avatar(avatar: Image.Image) -> Image.Image:
    """Crop avatar to circle."""
    size = avatar.size
    mask = Image.new('L', size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse([0, 0, size[0], size[1]], fill=255)
    output = Image.new('RGBA', size, (0, 0, 0, 0))
    output.paste(avatar, mask=mask)
    return output


async def generate_id_card(
    username: str,
    csr_id: str,
    guild_name: str,
    avatar_url: str | None = None,
    server_type: str = 'sbor'
) -> io.BytesIO:
    """Generate a CSR membership card image. Returns BytesIO PNG."""

    # Create base image
    card = Image.new('RGB', (CARD_W, CARD_H), C_BG_TOP)
    draw = ImageDraw.Draw(card)

    # Gradient background
    for y in range(CARD_H):
        ratio = y / CARD_H
        r = int(C_BG_TOP[0] + (C_BG_BOT[0] - C_BG_TOP[0]) * ratio)
        g = int(C_BG_TOP[1] + (C_BG_BOT[1] - C_BG_TOP[1]) * ratio)
        b = int(C_BG_TOP[2] + (C_BG_BOT[2] - C_BG_TOP[2]) * ratio)
        draw.line([(0, y), (CARD_W, y)], fill=(r, g, b))

    # Geometric pattern
    _draw_geometric_pattern(draw, CARD_W, CARD_H)

    # Glowing border
    _draw_glowing_border(draw, CARD_W, CARD_H)

    # Top accent bar
    draw.rectangle([0, 0, CARD_W, 6], fill=C_ACCENT)

    # Bottom accent bar
    draw.rectangle([0, CARD_H - 6, CARD_W, CARD_H], fill=C_ACCENT2)

    # Left sidebar glow
    for i in range(20):
        alpha = int(80 * (1 - i / 20))
        draw.rectangle([10 + i, 10, 11 + i, CARD_H - 10], fill=(*C_GLOW, alpha))

    # ── Header ──────────────────────────────────────────────────────────────
    f_title  = _get_font(28, bold=True)
    f_sub    = _get_font(13)
    f_label  = _get_font(11)
    f_value  = _get_font(18, bold=True)
    f_large  = _get_font(22, bold=True)
    f_stamp  = _get_font(16, bold=True)
    f_tiny   = _get_font(10)
    f_breezy = _get_font(12, bold=True)

    # CSR System title
    draw.text((40, 22), 'CSR SYSTEM', font=f_title, fill=C_ACCENT)
    draw.text((40, 54), 'MEMBERSHIP CARD', font=f_sub, fill=C_DIM)

    # Divider line
    draw.rectangle([40, 78, CARD_W - 40, 80], fill=C_ACCENT2)

    # Guild name top right
    draw.text((CARD_W - 40, 22), guild_name.upper(), font=f_sub, fill=C_DIM,
              anchor='ra')

    # ── Avatar area ─────────────────────────────────────────────────────────
    avatar_x, avatar_y = 560, 100
    avatar_size = 120

    # Avatar border glow
    for i in range(8):
        alpha = int(150 * (1 - i / 8))
        draw.ellipse([
            avatar_x - i, avatar_y - i,
            avatar_x + avatar_size + i, avatar_y + avatar_size + i
        ], outline=(*C_ACCENT, alpha))

    # Avatar background
    draw.ellipse([avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size],
                 fill=C_ACCENT2)

    # Fetch and paste avatar
    if avatar_url:
        avatar_img = await fetch_avatar(avatar_url)
        if avatar_img:
            circular = _circular_avatar(avatar_img)
            # Resize to fit
            circular = circular.resize((avatar_size, avatar_size), Image.LANCZOS)
            card.paste(circular, (avatar_x, avatar_y), circular)

    # ── Info fields ──────────────────────────────────────────────────────────
    left = 50
    y = 100

    # Username
    draw.text((left, y), 'USERNAME', font=f_label, fill=C_DIM)
    y += 18
    _draw_rounded_rect(draw, [left, y, left + 320, y + 38], radius=6,
                       fill=(0, 30, 80), outline=C_ACCENT2)
    draw.text((left + 12, y + 9), username, font=f_large, fill=C_WHITE)
    y += 55

    # CSR ID + Date row
    # CSR ID
    draw.text((left, y), 'CSR ID', font=f_label, fill=C_DIM)
    draw.text((left + 180, y), 'DATE ISSUED', font=f_label, fill=C_DIM)
    y += 18
    _draw_rounded_rect(draw, [left, y, left + 160, y + 34], radius=6,
                       fill=(0, 30, 80), outline=C_ACCENT2)
    _draw_rounded_rect(draw, [left + 180, y, left + 320, y + 34], radius=6,
                       fill=(0, 30, 80), outline=C_ACCENT2)
    draw.text((left + 10, y + 7), csr_id, font=f_value, fill=C_ACCENT)
    date_str = datetime.now(timezone.utc).strftime('%d.%m.%Y')
    draw.text((left + 192, y + 7), date_str, font=f_value, fill=C_WHITE)
    y += 50

    # Server type badge
    SERVER_LABELS = {
        'sbor': 'SWORD BLOX ONLINE REBIRTH',
        'bh2':  'BLUE HEATER 2',
        'bf':   'BLOX FRUITS',
        'warframe': 'WARFRAME',
    }
    game_label = SERVER_LABELS.get(server_type, 'CSR NETWORK')
    _draw_rounded_rect(draw, [left, y, left + 200, y + 26], radius=13,
                       fill=C_ACCENT2, outline=C_ACCENT)
    draw.text((left + 100, y + 4), game_label, font=f_tiny, fill=C_WHITE, anchor='ma')
    y += 40

    # ── CERTIFIED stamp ──────────────────────────────────────────────────────
    stamp_x, stamp_y = CARD_W - 120, CARD_H - 130
    # Outer circle
    for i in range(4):
        draw.ellipse([stamp_x - 50 - i, stamp_y - 50 - i,
                      stamp_x + 50 + i, stamp_y + 50 + i],
                     outline=C_STAMP, width=1)
    # Inner circle
    draw.ellipse([stamp_x - 35, stamp_y - 35, stamp_x + 35, stamp_y + 35],
                 outline=C_STAMP, width=2)
    draw.text((stamp_x, stamp_y - 12), 'VERIFIED', font=f_stamp, fill=C_STAMP, anchor='mm')
    draw.text((stamp_x, stamp_y + 10), 'MEMBER', font=f_stamp, fill=C_STAMP, anchor='mm')

    # ── Breezy watermark ─────────────────────────────────────────────────────
    draw.text((CARD_W - 20, CARD_H - 20), 'Powered by Breezy',
              font=f_breezy, fill=(*C_DIM, 120), anchor='rb')

    # ── Bottom bar ───────────────────────────────────────────────────────────
    draw.rectangle([0, CARD_H - 40, CARD_W, CARD_H - 6], fill=(0, 15, 45))
    draw.text((40, CARD_H - 30), 'CSR SYSTEM  ·  BREEZY ERA  ·  CHAMPIONS NETWORK',
              font=f_tiny, fill=C_DIM)

    # Slight blur for glow effect on border
    card_rgba = card.convert('RGBA')

    # Output
    output = io.BytesIO()
    card_rgba.save(output, format='PNG', optimize=True)
    output.seek(0)
    return output

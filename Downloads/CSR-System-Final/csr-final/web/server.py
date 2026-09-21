from flask import Flask, request, jsonify, render_template
import os
import asyncio
import threading
from utils.db import validate_otp, update_account, get_server_config, upsert_member_data, queue_action

app = Flask(__name__, template_folder='templates', static_folder='static')
_bot = None


@app.route('/verify')
def verify_page():
    return render_template('verify.html')


@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'bot': 'CSR System', 'powered_by': 'Breezy'})


@app.route('/api/verify', methods=['POST'])
def api_verify():
    data = request.get_json(silent=True) or {}
    code = str(data.get('code', '')).strip()
    guild_id = str(data.get('guild_id', '')).strip()

    if not code:
        return jsonify({'success': False, 'message': 'Please enter your OTP code.'})

    result = validate_otp(code)
    if not result['valid']:
        messages = {
            'invalid': '❌ That code is invalid. Check your DM and try again.',
            'expired': '⏰ Code expired (2-minute limit). Click Verify Me again for a new one.',
            'already_used': '✅ This code was already used successfully.',
        }
        return jsonify({'success': False, 'message': messages.get(result['reason'], 'Invalid code.')})

    user_id = result['user_id']
    future = asyncio.run_coroutine_threadsafe(_assign_verified(user_id, guild_id), _bot.loop)
    try:
        success, msg = future.result(timeout=12)
    except Exception:
        # Queue for retry if bot can't respond in time
        if guild_id:
            queue_action(guild_id, user_id, 'set_verified')
        return jsonify({'success': True, 'message': '🎉 Verified! Roles will be assigned shortly — head back to Discord.'})

    if success:
        return jsonify({'success': True, 'message': '🎉 Verified! You can close this and head back to the server.'})
    return jsonify({'success': False, 'message': msg})


async def _assign_verified(user_id: str, guild_id: str):
    try:
        # Try to find the guild
        target_guild = None
        cfg = None

        if guild_id and guild_id.isdigit():
            target_guild = _bot.get_guild(int(guild_id))
            cfg = get_server_config(guild_id)
        else:
            # Find guild this user is in
            for guild in _bot.guilds:
                member = guild.get_member(int(user_id))
                if member:
                    gc = get_server_config(str(guild.id))
                    if gc and gc.get('setup_complete'):
                        target_guild = guild
                        cfg = gc
                        break

        if not target_guild or not cfg:
            return False, '⚠️ Could not find your server. Contact an admin.'

        member = target_guild.get_member(int(user_id)) or await target_guild.fetch_member(int(user_id))

        unverified_id = cfg.get('unverified_role_id')
        verified_id = cfg.get('verified_role_id')

        if unverified_id:
            role = target_guild.get_role(int(unverified_id))
            if role:
                await member.remove_roles(role, reason='Verified via OTP')

        if verified_id:
            role = target_guild.get_role(int(verified_id))
            if role:
                await member.add_roles(role, reason='Verified via OTP')

        update_account(user_id, verified=1)
        upsert_member_data(str(target_guild.id), user_id, current_member=1)

        game_name = cfg.get('guild_name', 'the guild')
        await member.send(
            f'✅ **Verified!** Welcome to **{game_name}**!\n\n'
            f'Next step → use `/signup` to create your CSR account and unlock cross-server features! 🪪'
        )
        return True, 'ok'
    except Exception as e:
        print(f'Verify assign error: {e}')
        return False, '⚠️ Something went wrong. Contact an admin.'


def start_web_server(bot):
    global _bot
    _bot = bot
    port = int(os.getenv('WEB_PORT', 3000))

    def run():
        app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

    threading.Thread(target=run, daemon=True).start()
    print(f'🌐 Web server running on port {port}')

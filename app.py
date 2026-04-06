import os
import glob
import re
import json
import random
import asyncio
import threading
import time
import shutil
import socket
import requests
from zipfile import ZipFile
from flask import Flask, render_template, request, jsonify, Response, send_file
from telethon import TelegramClient, functions, types, events
from telethon.errors import AuthKeyUnregisteredError, UserDeactivatedBanError, SessionExpiredError, SessionRevokedError
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# ==========================================
# SYSTEM CORE & PATH CONFIGURATION
# ==========================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')
CREDS_FILE = os.path.join(BASE_DIR, 'credentials.json')
PROXIES_FILE = os.path.join(BASE_DIR, 'proxies.txt')
SESSIONS_DIR = os.path.join(BASE_DIR, 'sessions')
EXPIRED_DIR = os.path.join(BASE_DIR, 'expired_sessions')

os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(EXPIRED_DIR, exist_ok=True)

if not os.path.exists(CONFIG_FILE):
    default_config = {
        "api_id": "25240346",
        "api_hash": "b8849fd945ed9225a002fda96591b6ee",
        "bot_token": "8390809275:AAH9XsUo7b8e2l2BzqTp3NTEzXBy2FXq_UI",
        "min_delay": 5,
        "max_delay": 15
    }
    with open(CONFIG_FILE, 'w') as f:
        json.dump(default_config, f, indent=4)

if not os.path.exists(CREDS_FILE):
    with open(CREDS_FILE, 'w') as f:
        json.dump([], f)

if not os.path.exists(PROXIES_FILE):
    with open(PROXIES_FILE, 'w') as f:
        f.write("")

# ==========================================
# APP INITIALIZATION
# ==========================================
app = Flask(__name__)
app.secret_key = 'ITZ_DEV_CORE_SYSTEM_SECURE_2026'

STOP_SIGNAL = threading.Event()
LOG_HISTORY = []
IS_RUNNING = False
PROXY_STATUS = {"active": 0, "dead": 0, "last_check": "Never"}

BOT_APP = None
BOT_THREAD = None
BOT_LOOP = None
IS_BOT_RUNNING = False
events_store = {}

# ==========================================
# UTILITIES & HELPERS
# ==========================================
def load_config():
    try:
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f)
    except Exception as e:
        emit_log(f"⚠️ Config Load Error: {e}")
        return {}

def save_config(conf):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(conf, f, indent=4)
    except Exception as e:
        emit_log(f"⚠️ Config Save Error: {e}")

def get_balanced_creds(index=0):
    try:
        if os.path.exists(CREDS_FILE):
            with open(CREDS_FILE, 'r') as f:
                creds = json.load(f)
            if creds and isinstance(creds, list):
                idx = index % len(creds)
                return creds[idx]['api_id'], creds[idx]['api_hash']
    except Exception as e:
        emit_log(f"⚠️ Creds Load Error: {e}")
        
    conf = load_config()
    return conf.get('api_id', ''), conf.get('api_hash', '')

def emit_log(msg):
    timestamp = datetime.now().strftime('%H:%M:%S')
    line = f"[{timestamp}] {msg}"
    LOG_HISTORY.append(line)
    if len(LOG_HISTORY) > 150:
        LOG_HISTORY.pop(0)

def get_proxy():
    try:
        if not os.path.exists(PROXIES_FILE):
            return None, None
            
        with open(PROXIES_FILE, 'r') as f:
            proxies = [l.strip() for l in f if l.strip()]
            
        if not proxies:
            return None, None
        
        p = random.choice(proxies)
        clean = re.sub(r'(?i)^socks5h?://', '', p)
        parts = clean.split(':')
        
        if len(parts) >= 4:
            proxy_dict = {
                'proxy_type': 'socks5',
                'addr': parts[0],
                'port': int(parts[1]),
                'username': parts[2],
                'password': parts[3],
                'rdns': True
            }
            return proxy_dict, p
        return None, None
    except Exception as e:
        emit_log(f"⚠️ Proxy Parsing Error: {e}")
        return None, None

def get_random_device():
    devices = [
        ("Samsung Galaxy S24 Ultra", "Android 14"),
        ("Xiaomi 14 Pro", "Android 14"),
        ("OnePlus 12", "Android 14"),
        ("Google Pixel 8 Pro", "Android 14")
    ]
    dev_model, sys_ver = random.choice(devices)
    app_ver = random.choice(["10.13.1", "10.14.0"])
    return dev_model, sys_ver, app_ver

async def validate_proxy(p_str):
    try:
        clean = re.sub(r'(?i)^socks5h?://', '', p_str)
        parts = clean.split(':')
        if len(parts) < 4:
            return False
        socket.create_connection((parts[0], int(parts[1])), timeout=3)
        return True
    except Exception:
        return False

# ==========================================
# TELEGRAM ENGINE (TELETHON)
# ==========================================
async def delayed_leave(s_path, api_id, api_hash, target, delay_seconds, dev_meta):
    await asyncio.sleep(delay_seconds)
    basename = os.path.basename(s_path)
    proxy_data, _ = get_proxy()
    
    client = TelegramClient(
        s_path.replace('.session', '_clean'),
        api_id,
        api_hash,
        proxy=proxy_data,
        device_model=dev_meta[0]
    )
    
    try:
        await client.connect()
        if await client.is_user_authorized():
            clean_target = target.replace('https://t.me/', '').replace('@', '').split('/')[0].split('?')[0]
            await client(functions.channels.LeaveChannelRequest(clean_target))
            emit_log(f"🧹 {basename}: tactical leave executed.")
    except Exception as e:
        emit_log(f"⚠️ {basename}: auto-leave failed -> {e}")
    finally:
        await client.disconnect()

async def execute_task(data):
    all_sessions = glob.glob(os.path.join(SESSIONS_DIR, '*.session'))
    acc_limit = int(data.get('acc_limit', len(all_sessions)))
    sessions = all_sessions[:acc_limit]
    
    action = data.get('action')
    min_d = int(data.get('min_d', 5))
    max_d = int(data.get('max_d', 15))
    bot_w = int(data.get('bot_w', 60))
    
    STOP_SIGNAL.clear()
    emit_log(f"🚀 ENGINE: {action.upper()} ({len(sessions)} SESSIONS)")

    for i, s_path in enumerate(sessions):
        if STOP_SIGNAL.is_set():
            emit_log("🛑 TASK TERMINATED.")
            break
            
        basename = os.path.basename(s_path)
        api_id, api_hash = get_balanced_creds(i)
        
        client = None
        connected = False
        dev_model, sys_ver, app_ver = get_random_device()

        for attempt in range(2):
            if STOP_SIGNAL.is_set(): break
            proxy_data, _ = get_proxy()
            
            client = TelegramClient(
                s_path.replace('.session', ''),
                api_id,
                api_hash,
                proxy=proxy_data,
                device_model=dev_model,
                system_version=sys_ver,
                app_version=app_ver,
                request_retries=3,
                timeout=15
            )
            
            try:
                await client.connect()
                connected = True
                break
            except Exception as e:
                emit_log(f"🔄 {basename}: connection failed (Attempt {attempt+1}) -> {e}")
                await asyncio.sleep(2)

        if not connected or STOP_SIGNAL.is_set():
            if client: 
                await client.disconnect()
            continue

        try:
            if not await client.is_user_authorized():
                emit_log(f"❌ {basename}: dead session moved.")
                await client.disconnect()
                shutil.move(s_path, os.path.join(EXPIRED_DIR, basename))
                continue

            target_input = data.get('target', '').strip()

            if action == 'health':
                await client.get_me()
                emit_log(f"✅ {basename}: session active.")

            elif action == 'refer':
                bot_u = target_input.split('t.me/')[-1].split('?')[0]
                param = target_input.split('start=')[-1] if 'start=' in target_input else ""
                ent = await client.get_entity(bot_u)
                await client(functions.messages.StartBotRequest(bot=ent, peer=ent, start_param=param))
                emit_log(f"🔗 {basename}: referral sent.")

            elif action == 'report':
                reason_map = {
                    '1': types.InputReportReasonSpam(), 
                    '2': types.InputReportReasonViolence(), 
                    '3': types.InputReportReasonPornography(), 
                    '4': types.InputReportReasonChildAbuse(), 
                    '5': types.InputReportReasonCopyright(), 
                    '8': types.InputReportReasonFake(), 
                    '9': types.InputReportReasonOther()
                }
                reason = reason_map.get(data.get('reason'), types.InputReportReasonOther())
                ent = None
                clean_target = target_input.split('t.me/')[-1].split('/')[0].replace('@', '').replace('+', '').split('?')[0]
                
                if data.get('join_first'):
                    try:
                        await client(functions.channels.JoinChannelRequest(clean_target))
                    except Exception as e:
                        emit_log(f"⚠️ {basename}: Join notice -> {e}")

                try:
                    ent = await client.get_entity(clean_target)
                except Exception as e:
                    emit_log(f"⚠️ {basename}: Failed to resolve target -> {e}")
                    continue
                
                if data.get('report_mode') == 'bot':
                    await client.send_message(ent, "/start")
                    for _ in range(bot_w):
                        if STOP_SIGNAL.is_set(): raise Exception("STOPPED")
                        await asyncio.sleep(1)

                if data.get('report_mode') == 'posts' and data.get('post_links'):
                    ids = [int(re.search(r'/(\d+)$', l).group(1)) for l in data['post_links'] if re.search(r'/(\d+)$', l)]
                    if ids:
                        try:
                            await client(functions.messages.ReportRequest(peer=ent, id=ids, reason=reason, message="Violations"))
                        except Exception as e:
                            emit_log(f"⚠️ {basename}: Post report notice -> {e}")
                
                await client(functions.account.ReportPeerRequest(peer=ent, reason=reason, message="Violations"))
                emit_log(f"✅ {basename}: report delivered.")

                if data.get('leave_after'):
                    delay = int(data.get('leave_delay', 300))
                    asyncio.create_task(delayed_leave(s_path, api_id, api_hash, target_input, delay, (dev_model, sys_ver, app_ver)))

            elif action == 'message':
                peer = int(target_input) if target_input.isdigit() else target_input
                await client.send_message(peer, data.get('message_text', ''))
                emit_log(f"✅ {basename}: msg delivered.")

            elif action == 'join':
                if "t.me/+" in target_input:
                    await client(functions.messages.ImportChatInviteRequest(target_input.split('+')[-1]))
                else:
                    await client(functions.channels.JoinChannelRequest(target_input.replace('https://t.me/', '').replace('@', '')))
                emit_log(f"✅ {basename}: joined.")

            elif action == 'leave':
                await client(functions.channels.LeaveChannelRequest(target_input.replace('https://t.me/', '').replace('@', '')))
                emit_log(f"✅ {basename}: left.")

        except Exception as e:
            if str(e) != "STOPPED": 
                emit_log(f"⚠️ {basename}: execution error -> {e}")
        finally:
            await client.disconnect()
            if not STOP_SIGNAL.is_set(): 
                await asyncio.sleep(random.uniform(min_d, max_d))
                
    emit_log("🏁 IDLE.")

def thread_run(data):
    global IS_RUNNING
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try: 
        loop.run_until_complete(execute_task(data))
    except Exception as e:
        emit_log(f"⚠️ Thread Runner Error: {e}")
    finally: 
        IS_RUNNING = False

# ==========================================
# OTP BOT ENGINE (WEBHOOK HANDLER)
# ==========================================
def ensure_bucket(user_id: int):
    if user_id not in events_store: 
        events_store[user_id] = {}

async def bot_start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text("Upload .session or .zip.\nNext ➡️ to skip.\n/cancel to stop.")
    except Exception as e:
        emit_log(f"⚠️ Bot Start Error: {e}")

async def bot_cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        bucket = events_store.get(user_id)
        if not bucket: return
        for _, entry in list(bucket.items()):
            entry["skip"].set()
            for t in entry.get("tasks", []):
                if not t.done(): t.cancel()
        bucket.clear()
        await update.message.reply_text("🛑 Aborted.")
    except Exception as e:
        emit_log(f"⚠️ Bot Cancel Error: {e}")

async def bot_receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        ensure_bucket(user_id)
        if not update.message.document: return

        file = update.message.document
        fname = file.file_name.lower()
        f = await context.bot.get_file(file.file_id)
        local_path = os.path.join(BASE_DIR, f"{user_id}_{fname}")
        await f.download_to_drive(custom_path=local_path)
        await update.message.reply_text("♻️ Processing...")

        sessions = []
        if fname.endswith(".zip"):
            try:
                out_dir = os.path.join(BASE_DIR, f"{user_id}_sessions")
                os.makedirs(out_dir, exist_ok=True)
                with ZipFile(local_path, "r") as z:
                    z.extractall(out_dir)
                    for n in z.namelist():
                        if n.endswith(".session"): sessions.append(os.path.join(out_dir, n))
            except Exception as e: 
                emit_log(f"⚠️ Zip Extract Error: {e}")
                return
        elif fname.endswith(".session"): 
            sessions = [local_path]
        
        if not sessions: return

        api_id, api_hash = get_balanced_creds()

        for idx, sfile in enumerate(sessions, start=1):
            sname = os.path.basename(sfile)
            client = TelegramClient(sfile, api_id, api_hash)
            cb_key = f"skip_session:{user_id}:{idx}"

            try:
                await update.message.reply_text(f"📁 [{idx}]: {sname}\n🔌 Connecting...")
                try: 
                    await client.connect()
                except Exception as e: 
                    emit_log(f"⚠️ Bot Connect Error for {sname}: {e}")
                    continue

                if not await client.is_user_authorized():
                    await client.disconnect()
                    continue

                me = await client.get_me()
                phone = me.phone or "Unknown"

                otp_event, skip_event = asyncio.Event(), asyncio.Event()
                events_store[user_id][cb_key] = {"skip": skip_event, "tasks": [], "answered": False}

                @client.on(events.NewMessage(from_users=777000))
                async def otp_listener(event):
                    raw = event.raw_text
                    await context.bot.send_message(chat_id=user_id, text=f"📨 {sname}:\n{raw}")
                    m = re.search(r"\b(\d{5,6})\b", raw)
                    if m: await context.bot.send_message(chat_id=user_id, text=f"🧩 OTP: {m.group(1)}\n📱 +{phone}")
                    otp_event.set()
                    await client.disconnect()

                await asyncio.sleep(1)
                keyboard = [[InlineKeyboardButton("Next ➡️", callback_data=cb_key)]]
                info_msg = await update.message.reply_text(f"📱 +{phone}\n⏳ Waiting...", reply_markup=InlineKeyboardMarkup(keyboard))

                t1, t2 = asyncio.create_task(otp_event.wait()), asyncio.create_task(skip_event.wait())
                events_store[user_id][cb_key]["tasks"] = [t1, t2]
                
                await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
                
                for t in [t1, t2]: 
                    t.cancel()
                    
                try: 
                    await info_msg.edit_reply_markup(None)
                except Exception: 
                    pass
                    
            except Exception as e: 
                emit_log(f"⚠️ Bot OTP Process Error: {e}")
            finally:
                events_store.get(user_id, {}).pop(cb_key, None)
                try: 
                    await client.disconnect()
                except Exception: 
                    pass

        await update.message.reply_text("✅ Batch complete.")
    except Exception as e:
        emit_log(f"⚠️ Bot File Receive Error: {e}")

async def bot_skip_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        query = update.callback_query
        data = query.data
        user = update.effective_user
        
        if not user: 
            return
            
        parts = data.split(":")
        owner_id = int(parts[1]) if len(parts) >= 3 else user.id
        
        entry = events_store.get(owner_id, {}).get(data)
        if entry and not entry.get("answered"):
            entry["answered"] = True
            entry["skip"].set()
            
        try: 
            await query.answer()
        except Exception as e: 
            emit_log(f"⚠️ Query Answer Error: {e}")
            
        try: 
            await query.edit_message_reply_markup(None)
        except Exception as e: 
            emit_log(f"⚠️ Markup Edit Error: {e}")
            
    except Exception as e: 
        emit_log(f"⚠️ Skip Callback Error: {e}")

def init_bot_engine(token):
    global BOT_APP, BOT_LOOP, IS_BOT_RUNNING
    try:
        BOT_LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(BOT_LOOP)
        
        BOT_APP = ApplicationBuilder().token(token).build()
        BOT_APP.add_handler(CommandHandler("start", bot_start_cmd))
        BOT_APP.add_handler(CommandHandler("cancel", bot_cancel_cmd))
        BOT_APP.add_handler(MessageHandler(filters.Document.ALL, bot_receive_file))
        BOT_APP.add_handler(CallbackQueryHandler(bot_skip_cb))
        
        BOT_LOOP.run_until_complete(BOT_APP.initialize())
        BOT_LOOP.run_until_complete(BOT_APP.start())
        
        IS_BOT_RUNNING = True
        emit_log("🤖 ENGINE READY (WEBHOOK LISTENING).")
        BOT_LOOP.run_forever()
    except Exception as e:
        emit_log(f"⚠️ BOT ENGINE ERROR: {e}")
        IS_BOT_RUNNING = False

# ==========================================
# FLASK HTTP ROUTES
# ==========================================
@app.route('/')
def index():
    stats = {
        "active": len(glob.glob(os.path.join(SESSIONS_DIR, '*.session'))),
        "expired": len(glob.glob(os.path.join(EXPIRED_DIR, '*.session'))),
        "p_active": PROXY_STATUS["active"], 
        "p_dead": PROXY_STATUS["dead"]
    }
    
    proxies = ""
    if os.path.exists(PROXIES_FILE):
        try:
            with open(PROXIES_FILE, 'r') as f:
                proxies = f.read()
        except Exception:
            pass
            
    conf = load_config()
    return render_template(
        'index.html', 
        stats=stats, 
        proxies=proxies, 
        bot_running=IS_BOT_RUNNING, 
        bot_token=conf.get('bot_token', '')
    )

@app.route('/execute', methods=['POST'])
def execute():
    global IS_RUNNING
    if IS_RUNNING: 
        return jsonify({"status": "busy"})
    IS_RUNNING = True
    threading.Thread(target=thread_run, args=(request.json,)).start()
    return jsonify({"status": "ok"})

@app.route('/stop', methods=['POST'])
def stop():
    STOP_SIGNAL.set()
    return jsonify({"status": "ok"})

@app.route('/clear', methods=['POST'])
def clear_logs():
    LOG_HISTORY.clear()
    return jsonify({"status": "ok"})

@app.route('/upload_sessions', methods=['POST'])
def upload_sessions():
    try:
        for f in request.files.getlist('files'):
            if f.filename.endswith('.session'):
                f.save(os.path.join(SESSIONS_DIR, f.filename))
        return jsonify({"status": "ok"})
    except Exception as e:
        emit_log(f"⚠️ Upload Error: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/download_sessions')
def download_sessions():
    try:
        zip_path = os.path.join(BASE_DIR, 'all_sessions')
        shutil.make_archive(zip_path, 'zip', SESSIONS_DIR)
        return send_file(f"{zip_path}.zip", as_attachment=True)
    except Exception as e:
        emit_log(f"⚠️ Download Error: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/save_proxies', methods=['POST'])
def save_proxies():
    try:
        with open(PROXIES_FILE, 'w') as f: 
            f.write(request.json.get('proxies', ''))
        return jsonify({"status": "ok"})
    except Exception as e:
        emit_log(f"⚠️ Save Proxies Error: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/save_creds', methods=['POST'])
def save_creds():
    try:
        d = request.json
        try:
            with open(CREDS_FILE, 'r') as f: 
                creds = json.load(f)
        except Exception: 
            creds = []
            
        creds.append({"api_id": d['api_id'], "api_hash": d['api_hash']})
        
        with open(CREDS_FILE, 'w') as f: 
            json.dump(creds, f)
        return jsonify({"status": "ok"})
    except Exception as e:
        emit_log(f"⚠️ Save Creds Error: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/check_proxies', methods=['POST'])
def check_proxies():
    def run_check():
        global PROXY_STATUS
        try:
            if not os.path.exists(PROXIES_FILE): 
                return
                
            with open(PROXIES_FILE, 'r') as f: 
                proxies = [l.strip() for l in f if l.strip()]
                
            active, dead = 0, 0
            loop = asyncio.new_event_loop()
            for p in proxies:
                if loop.run_until_complete(validate_proxy(p)): 
                    active += 1
                else: 
                    dead += 1
                    
            PROXY_STATUS = {"active": active, "dead": dead, "last_check": datetime.now().strftime('%H:%M')}
            emit_log(f"📊 PROXY AUDIT: {active} OK, {dead} DEAD.")
        except Exception as e:
            emit_log(f"⚠️ Proxy Audit Error: {e}")
            
    threading.Thread(target=run_check).start()
    return jsonify({"status": "ok"})

@app.route('/api/bot/save', methods=['POST'])
def save_bot_token():
    try:
        conf = load_config()
        conf['bot_token'] = request.json.get('token', '')
        save_config(conf)
        return jsonify({"status": "ok"})
    except Exception as e:
        emit_log(f"⚠️ Save Token Error: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/api/bot/init_engine', methods=['POST'])
def start_bot():
    global BOT_THREAD, IS_BOT_RUNNING
    if IS_BOT_RUNNING: 
        return jsonify({"status": "already_running"})
        
    token = load_config().get('bot_token', '')
    if not token: 
        return jsonify({"status": "no_token"})
        
    BOT_THREAD = threading.Thread(target=init_bot_engine, args=(token,))
    BOT_THREAD.daemon = True
    BOT_THREAD.start()
    return jsonify({"status": "ok"})

@app.route('/api/bot/set_webhook', methods=['POST'])
def set_webhook():
    try:
        token = load_config().get('bot_token', '')
        if not token: 
            return jsonify({"status": "error", "message": "No Token"})
            
        webhook_url = request.url_root.replace('http://', 'https://') + 'webhook'
        r = requests.get(f"https://api.telegram.org/bot{token}/setWebhook?url={webhook_url}")
        
        if r.status_code == 200: 
            emit_log(f"🌐 WEBHOOK CONNECTED: {webhook_url}")
        else:
            emit_log(f"⚠️ WEBHOOK ERROR: {r.text}")
            
        return jsonify(r.json())
    except Exception as e:
        emit_log(f"⚠️ Set Webhook Exception: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/api/bot/remove_webhook', methods=['POST'])
def remove_webhook():
    try:
        token = load_config().get('bot_token', '')
        r = requests.get(f"https://api.telegram.org/bot{token}/deleteWebhook")
        emit_log("🗑️ WEBHOOK DELETED.")
        return jsonify(r.json())
    except Exception as e:
        emit_log(f"⚠️ Remove Webhook Error: {e}")
        return jsonify({"status": "error"}), 500

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    try:
        if IS_BOT_RUNNING and BOT_APP and BOT_LOOP:
            data = request.get_json(force=True)
            update = Update.de_json(data, BOT_APP.bot)
            asyncio.run_coroutine_threadsafe(BOT_APP.process_update(update), BOT_LOOP)
        return "OK"
    except Exception as e:
        emit_log(f"⚠️ Webhook Route Error: {e}")
        return "ERROR", 500

@app.route('/logs')
def stream_logs():
    def generate():
        yield f"data: INIT|{'<br>'.join(LOG_HISTORY)}\n\n"
        idx = len(LOG_HISTORY)
        while True:
            if len(LOG_HISTORY) > idx:
                yield f"data: APP|{LOG_HISTORY[idx]}\n\n"
                idx += 1
            time.sleep(0.3)
    return Response(generate(), mimetype='text/event-stream')

if __name__ == '__main__':
    # Dynamic port assignment for Render compatibility
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)), debug=False)

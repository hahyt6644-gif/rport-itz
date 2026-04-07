import os, glob, re, json, random, asyncio, threading, time, shutil, socket
import requests
from zipfile import ZipFile
from flask import Flask, render_template, request, jsonify, Response, session, send_file
from telethon import TelegramClient, functions, types, events
from telethon.sessions import MemorySession
from telethon.errors import AuthKeyUnregisteredError, UserDeactivatedBanError, SessionExpiredError, SessionRevokedError
from datetime import datetime

# Telegram Bot Imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

# --- ABSOLUTE PATHS FIX FOR RENDER ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')
CREDS_FILE = os.path.join(BASE_DIR, 'credentials.json')
PROXIES_FILE = os.path.join(BASE_DIR, 'proxies.txt')
SESSIONS_DIR = os.path.join(BASE_DIR, 'sessions')
EXPIRED_DIR = os.path.join(BASE_DIR, 'expired_sessions')
MESSAGES_DIR = os.path.join(BASE_DIR, 'messages')

os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(EXPIRED_DIR, exist_ok=True)
os.makedirs(MESSAGES_DIR, exist_ok=True)

# Generate category message files automatically
REASON_FILES = {
    '1': 'spam.txt', '2': 'violence.txt', '3': 'pornography.txt', 
    '4': 'child_abuse.txt', '5': 'copyright.txt', '6': 'drugs.txt', 
    '7': 'personal_details.txt', '8': 'fake.txt', '9': 'other.txt'
}
for f_name in REASON_FILES.values():
    f_path = os.path.join(MESSAGES_DIR, f_name)
    if not os.path.exists(f_path):
        open(f_path, 'a').close()

app = Flask(__name__)
app.secret_key = 'itz_dev_super_secret_key_2026'

# --- GLOBAL VARIABLES ---
STOP_SIGNAL = threading.Event()
LOG_HISTORY = []
IS_RUNNING = False
PROXY_STATUS = {"active": 0, "dead": 0, "last_check": "Never"}

# --- OTP BOT GLOBALS ---
BOT_APP = None
BOT_THREAD = None
BOT_LOOP = None
IS_BOT_RUNNING = False
events_store = {}

def load_config():
    if not os.path.exists(CONFIG_FILE):
        conf = {
            "api_id": "23269382", "api_hash": "your_hash", 
            "admin_password": "admin", "bot_token": "", 
            "min_delay": 3, "max_delay": 8, "bot_w": 60
        }
        with open(CONFIG_FILE, 'w') as f: json.dump(conf, f, indent=4)
        return conf
    with open(CONFIG_FILE, 'r') as f: return json.load(f)

def save_config(conf):
    with open(CONFIG_FILE, 'w') as f: json.dump(conf, f, indent=4)

def get_balanced_creds(index=0):
    if os.path.exists(CREDS_FILE):
        try:
            with open(CREDS_FILE, 'r') as f: creds = json.load(f)
            if creds and isinstance(creds, list) and len(creds) > 0:
                idx = index % len(creds)
                return creds[idx]['api_id'], creds[idx]['api_hash']
        except: pass
    conf = load_config()
    return conf.get('api_id', ''), conf.get('api_hash', '')

def emit_log(msg):
    timestamp = datetime.now().strftime('%H:%M:%S')
    line = f"[{timestamp}] {msg}"
    LOG_HISTORY.append(line)
    if len(LOG_HISTORY) > 150: LOG_HISTORY.pop(0)

def get_proxy():
    if not os.path.exists(PROXIES_FILE): return None, None
    try:
        with open(PROXIES_FILE, 'r') as f: proxies = [l.strip() for l in f if l.strip()]
        if not proxies: return None, None
        p = random.choice(proxies)
        clean = re.sub(r'(?i)^socks5h?://', '', p)
        parts = clean.split(':')
        if len(parts) >= 4:
            proxy_dict = {
                'proxy_type': 'socks5', 'addr': parts[0], 'port': int(parts[1]), 
                'username': parts[2], 'password': parts[3], 'rdns': True
            }
            return proxy_dict, p
    except: pass
    return None, None

def get_random_device():
    devices = [("Samsung Galaxy S24 Ultra", "Android 14"), ("Xiaomi 14 Pro", "Android 14"), ("OnePlus 12", "Android 14")]
    dev_model, sys_ver = random.choice(devices)
    app_ver = random.choice(["10.13.1", "10.14.0"])
    return dev_model, sys_ver, app_ver

async def validate_proxy(p_str):
    try:
        clean = re.sub(r'(?i)^socks5h?://', '', p_str)
        parts = clean.split(':')
        if len(parts) < 4: return False
        socket.create_connection((parts[0], int(parts[1])), timeout=3)
        return True
    except: return False

async def delayed_leave(s_path, api_id, api_hash, target, delay_seconds, dev_meta):
    await asyncio.sleep(delay_seconds)
    basename = os.path.basename(s_path)
    proxy_data, _ = get_proxy() 
    client = TelegramClient(s_path.replace('.session','_cleanup'), api_id, api_hash, proxy=proxy_data, device_model=dev_meta[0])
    try:
        await client.connect()
        if await client.is_user_authorized():
            clean_target = target.replace('https://t.me/','').replace('@','').split('/')[-1].split('?')[0]
            await client(functions.channels.LeaveChannelRequest(clean_target))
            emit_log(f"🧹 {basename}: Safety auto-leave successful.")
    except Exception as e: emit_log(f"⚠️ {basename}: Auto-leave failed.")
    finally: await client.disconnect()

async def execute_task(data):
    all_sessions = glob.glob(os.path.join(SESSIONS_DIR, '*.session'))
    acc_limit = int(data.get('acc_limit', len(all_sessions)))
    sessions = all_sessions[:acc_limit]
    
    action = data.get('action')
    min_d = int(data.get('min_d', 3))
    max_d = int(data.get('max_d', 8))
    bot_w = int(data.get('bot_w', 60))
    
    STOP_SIGNAL.clear()
    emit_log(f"🚀 ITZ-DEV ENGINE: {action.upper()} ({len(sessions)} ACCS)")

    for i, s_path in enumerate(sessions):
        if STOP_SIGNAL.is_set():
            emit_log("🛑 TASK KILLED BY USER.")
            break
            
        basename = os.path.basename(s_path)
        api_id, api_hash = get_balanced_creds(i)
        
        client = None
        connected = False
        dev_model, sys_ver, app_ver = get_random_device()

        for attempt in range(2):
            if STOP_SIGNAL.is_set(): break
            proxy_data, proxy_raw = get_proxy()
                
            client = TelegramClient(
                s_path.replace('.session',''), api_id, api_hash, proxy=proxy_data,
                device_model=dev_model, system_version=sys_ver, app_version=app_ver,
                lang_code="en", system_lang_code="en", request_retries=3, connection_retries=3, timeout=15
            )
            
            try:
                await client.connect()
                connected = True
                break
            except Exception as e:
                emit_log(f"🔄 {basename}: PROXY ERR ({type(e).__name__})")
                await client.disconnect()
                await asyncio.sleep(2)

        if not connected or STOP_SIGNAL.is_set():
            if client: await client.disconnect()
            continue

        try:
            if not await client.is_user_authorized():
                emit_log(f"❌ {basename}: SESSION DEAD. MOVED.")
                await client.disconnect()
                shutil.move(s_path, os.path.join(EXPIRED_DIR, basename))
                continue

            emit_log(f"📱 {basename} SPOOFING DEVICE: {dev_model}")
            target_input = data.get('target', '').strip()

            if action == 'health':
                await client.get_me()
                emit_log(f"✅ {basename}: ONLINE (API:{api_id})")

            elif action == 'refer':
                bot_u = target_input.split('t.me/')[-1].split('?')[0]
                param = target_input.split('start=')[-1] if 'start=' in target_input else ""
                ent = await client.get_entity(bot_u)
                await client(functions.messages.StartBotRequest(bot=ent, peer=ent, start_param=param))
                emit_log(f"🔗 {basename}: REF SUCCESS.")

            elif action == 'report':
                reason_code = data.get('reason', '9')
                reason_map = {'1': types.InputReportReasonSpam(), '2': types.InputReportReasonViolence(), '3': types.InputReportReasonPornography(), '4': types.InputReportReasonChildAbuse(), '5': types.InputReportReasonCopyright(), '6': types.InputReportReasonIllegalDrugs(), '7': types.InputReportReasonPersonalDetails(), '8': types.InputReportReasonFake(), '9': types.InputReportReasonOther()}
                reason = reason_map.get(reason_code, types.InputReportReasonOther())
                
                # --- CATEGORY WISE MESSAGE SYSTEM ---
                custom_msg = "Violations"
                if data.get('use_custom_msg', False):
                    file_name = REASON_FILES.get(reason_code, 'other.txt')
                    try:
                        with open(os.path.join(MESSAGES_DIR, file_name), 'r', encoding='utf-8') as mf:
                            lines = [l.strip() for l in mf if l.strip()]
                            if lines: custom_msg = random.choice(lines)
                    except: pass
                
                ent = None
                clean_target = target_input.split('t.me/')[-1].split('/')[0].replace('@', '').replace('+', '').split('?')[0]
                is_private = "t.me/+" in target_input or "joinchat" in target_input
                
                if data.get('join_first') or is_private:
                    try:
                        if is_private:
                            h_val = target_input.split('+')[-1].split('?')[0] if '+' in target_input else target_input.split('joinchat/')[-1].split('/')[0].split('?')[0]
                            res = await client(functions.messages.ImportChatInviteRequest(h_val))
                            ent = res.chats[0]
                            emit_log(f"📥 {basename}: SUCCESSFULLY JOINED PRIVATE TARGET.")
                        else:
                            await client(functions.channels.JoinChannelRequest(clean_target))
                            ent = await client.get_entity(clean_target)
                            emit_log(f"📥 {basename}: SUCCESSFULLY JOINED PUBLIC TARGET.")
                        await asyncio.sleep(2)
                    except Exception as e:
                        if 'UserAlreadyParticipant' in type(e).__name__: emit_log(f"📥 {basename}: ALREADY IN CHAT.")
                        else: emit_log(f"⚠️ {basename}: JOIN FAILED ({type(e).__name__})")

                if not ent:
                    try:
                        if is_private:
                            h_val = target_input.split('+')[-1].split('?')[0] if '+' in target_input else target_input.split('joinchat/')[-1].split('/')[0].split('?')[0]
                            invite = await client(functions.messages.CheckChatInviteRequest(h_val))
                            ent = invite.chat
                        else:
                            ent = await client.get_entity(clean_target)
                    except Exception as e:
                        emit_log(f"⚠️ {basename}: TARGET RESOLVE ERROR ({type(e).__name__})")
                        continue

                if data.get('report_mode') == 'bot':
                    await client.send_message(ent, "/start")
                    emit_log(f"🤖 {basename}: BOT ON. WAIT {bot_w}s...")
                    for _ in range(bot_w):
                        if STOP_SIGNAL.is_set(): raise Exception("STOPPED")
                        await asyncio.sleep(1)

                if data.get('report_mode') == 'posts' and data.get('post_links'):
                    ids = [int(re.search(r'/(\d+)$', l).group(1)) for l in data['post_links'] if re.search(r'/(\d+)$', l)]
                    if ids:
                        try: await client(functions.messages.ReportRequest(peer=ent, id=ids, reason=reason, message=custom_msg))
                        except TypeError:
                            res = await client(functions.messages.ReportRequest(peer=ent, id=ids, option=b'', message=custom_msg))
                            if hasattr(res, 'options') and res.options: await client(functions.messages.ReportRequest(peer=ent, id=ids, option=res.options[0].option, message=custom_msg))
                        emit_log(f"✅ {basename}: {len(ids)} POSTS REPORTED. [Msg: {custom_msg}]")

                
                await client(functions.account.ReportPeerRequest(peer=ent, reason=reason, message=custom_msg))
                emit_log(f"✅ {basename}: PEER REPORTED. [Msg: {custom_msg}]")

                if data.get('leave_after'):
                    delay = int(data.get('leave_delay', 300))
                    asyncio.create_task(delayed_leave(s_path, api_id, api_hash, target_input, delay, (dev_model, sys_ver, app_ver)))
                    emit_log(f"🕒 {basename}: Queued to leave in {delay}s.")

            elif action == 'message':
                peer = int(target_input) if target_input.isdigit() else target_input
                await client.send_message(peer, data.get('message_text', ''))
                emit_log(f"✅ {basename}: MSG SENT.")

            elif action == 'join':
                if "t.me/+" in target_input: await client(functions.messages.ImportChatInviteRequest(target_input.split('+')[-1]))
                else: await client(functions.channels.JoinChannelRequest(target_input.replace('https://t.me/','').replace('@','')))
                emit_log(f"✅ {basename}: JOINED.")

            elif action == 'leave':
                if "t.me/+" not in target_input:
                    await client(functions.channels.LeaveChannelRequest(target_input.replace('https://t.me/','').replace('@','')))
                    emit_log(f"✅ {basename}: LEFT.")

        except Exception as e:
            if str(e) != "STOPPED": emit_log(f"⚠️ {basename}: {str(e)[:30]}")
        finally:
            await client.disconnect()
            if not STOP_SIGNAL.is_set(): await asyncio.sleep(random.uniform(min_d, max_d))
            
    emit_log("🏁 SYSTEM IDLE.")

def thread_run(data):
    global IS_RUNNING
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try: loop.run_until_complete(execute_task(data))
    finally: IS_RUNNING = False

# ==========================================
# OTP BOT LOGIC (WEBHOOK)
# ==========================================
def ensure_bucket(user_id: int):
    if user_id not in events_store: events_store[user_id] = {}

async def bot_start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Upload a .session or a .zip of .session files.\nPress Next ➡️ to skip a session.\nUse /cancel to cancel.")

async def bot_cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    bucket = events_store.get(user_id)
    if not bucket: return await update.message.reply_text("Nothing to cancel.")
    for _, entry in list(bucket.items()):
        entry["skip"].set()
        for t in entry.get("tasks", []):
            if not t.done(): t.cancel()
    bucket.clear()
    await update.message.reply_text("🛑 Cancelled your current processing.")

async def bot_receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ensure_bucket(user_id)
    if not update.message.document: return

    file = update.message.document
    fname = file.file_name.lower()
    f = await context.bot.get_file(file.file_id)
    local_path = os.path.join(BASE_DIR, f"{user_id}_{fname}")
    await f.download_to_drive(custom_path=local_path)
    await update.message.reply_text("♻️ Processing...\n🙏 Please wait.")

    sessions = []
    if fname.endswith(".zip"):
        try:
            out_dir = os.path.join(BASE_DIR, f"{user_id}_sessions")
            os.makedirs(out_dir, exist_ok=True)
            with ZipFile(local_path, "r") as z:
                z.extractall(out_dir)
                for n in z.namelist():
                    if n.endswith(".session"): sessions.append(os.path.join(out_dir, n))
        except Exception as e: return await update.message.reply_text(f"❌ ZIP extract error: {e}")
    elif fname.endswith(".session"): sessions = [local_path]
    
    if not sessions: return await update.message.reply_text("❌ Koi .session file nahi mili.")

    api_id, api_hash = get_balanced_creds()

    for idx, sfile in enumerate(sessions, start=1):
        sname = os.path.basename(sfile)
        client = TelegramClient(sfile, api_id, api_hash)
        cb_key = f"skip_session:{user_id}:{idx}"

        try:
            await update.message.reply_text(f"📁 Session [{idx}]: {sname}\n🔌 Connecting...")
            try: await client.connect()
            except: await update.message.reply_text("❌ Connect FAIL. Skipping..."); continue

            try: is_auth = await client.is_user_authorized()
            except: is_auth = False
            
            if not is_auth:
                await update.message.reply_text("🚫 Session Not Authorized / Expired. Skipping...")
                await client.disconnect()
                continue

            try:
                me = await client.get_me()
                phone = me.phone or "Unknown"
                first_name = me.first_name or "Unknown"
            except:
                await update.message.reply_text("❌ get_me() FAIL (Banned/Revoked). Skipping...")
                await client.disconnect()
                continue

            await update.message.reply_text("👂 OTP listener register kar raha hoon...")
            otp_event = asyncio.Event()
            skip_event = asyncio.Event()
            events_store[user_id][cb_key] = {"skip": skip_event, "tasks": [], "answered": False}

            @client.on(events.NewMessage(from_users=777000))
            async def otp_listener(event):
                raw = event.raw_text
                await context.bot.send_message(chat_id=user_id, text=f"📨 [{sname}] 777000:\n{raw}")
                m = re.search(r"\b(\d{5,6})\b", raw)
                if m: await context.bot.send_message(chat_id=user_id, text=f"🧩 OTP Code: {m.group(1)}\n📱 Number: +{phone}")
                otp_event.set()
                await client.disconnect()

            await asyncio.sleep(1)
            keyboard = [[InlineKeyboardButton("Next ➡️", callback_data=cb_key)]]
            info_msg = await update.message.reply_text(f"👤 {first_name} | +{phone}\n⏳ Waiting for OTP...", reply_markup=InlineKeyboardMarkup(keyboard))

            t1, t2 = asyncio.create_task(otp_event.wait()), asyncio.create_task(skip_event.wait())
            events_store[user_id][cb_key]["tasks"] = [t1, t2]

            done, pending = await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
            for t in pending: t.cancel()

            if skip_event.is_set() and not otp_event.is_set():
                await context.bot.send_message(chat_id=user_id, text=f"⏭️ [{sname}] Skipped.")
            try: await info_msg.edit_reply_markup(None)
            except: pass

        except Exception as e: pass
        finally:
            events_store.get(user_id, {}).pop(cb_key, None)
            try: await client.disconnect()
            except: pass

    await update.message.reply_text("✅ Saari sessions process ho gayi.")

async def bot_skip_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    user = update.effective_user
    if not user: return
    try:
        parts = data.split(":")
        owner_id = int(parts[1]) if len(parts) >= 3 else user.id
        if user.id != owner_id: return
        
        entry = events_store.get(owner_id, {}).get(data)
        if entry and not entry.get("answered"):
            entry["answered"] = True
            entry["skip"].set()
        try: await query.answer()
        except: pass
        try: await query.edit_message_reply_markup(None)
        except: pass
    except: pass

def run_bot_thread(token):
    global BOT_APP, BOT_LOOP, IS_BOT_RUNNING
    try:
        BOT_LOOP = asyncio.new_event_loop()
        asyncio.set_event_loop(BOT_LOOP)
        BOT_APP = ApplicationBuilder().token(token).updater(None).build()
        BOT_APP.add_handler(CommandHandler("start", bot_start_cmd))
        BOT_APP.add_handler(CommandHandler("cancel", bot_cancel_cmd))
        BOT_APP.add_handler(MessageHandler(filters.Document.ALL, bot_receive_file))
        BOT_APP.add_handler(CallbackQueryHandler(bot_skip_cb))
        
        BOT_LOOP.run_until_complete(BOT_APP.initialize())
        BOT_LOOP.run_until_complete(BOT_APP.start())
        
        IS_BOT_RUNNING = True
        emit_log("🤖 OTP BOT INITIALIZED IN WEBHOOK MODE.")
        BOT_LOOP.run_forever()
    except Exception as e: 
        emit_log(f"⚠️ OTP BOT ERROR: {e}")
    finally: 
        IS_BOT_RUNNING = False

async def shutdown_bot():
    global BOT_APP, IS_BOT_RUNNING
    if BOT_APP:
        await BOT_APP.stop()
        await BOT_APP.shutdown()
        IS_BOT_RUNNING = False
        emit_log("🤖 OTP BOT ENGINE STOPPED.")

# ==========================================
# FLASK ROUTES
# ==========================================
@app.route('/')
def index():
    if not session.get('logged_in'): return render_template('index.html', logged_in=False)
    stats = {
        "active": len(glob.glob(os.path.join(SESSIONS_DIR, '*.session'))), 
        "expired": len(glob.glob(os.path.join(EXPIRED_DIR, '*.session'))),
        "p_active": PROXY_STATUS["active"], "p_dead": PROXY_STATUS["dead"]
    }
    proxies = open(PROXIES_FILE).read() if os.path.exists(PROXIES_FILE) else ""
    conf = load_config()
    return render_template('index.html', logged_in=True, stats=stats, proxies=proxies, bot_running=IS_BOT_RUNNING, bot_token=conf.get('bot_token',''), conf=conf)

@app.route('/login', methods=['POST'])
def login():
    conf = load_config()
    if request.json.get('password') == conf.get('admin_password', 'admin'):
        session['logged_in'] = True
        return jsonify({"status": "ok"})
    return jsonify({"status": "fail"}), 401

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"status": "ok"})

@app.route('/execute', methods=['POST'])
def execute():
    global IS_RUNNING
    if not session.get('logged_in'): return "", 401
    if IS_RUNNING: return jsonify({"status": "busy"})
    IS_RUNNING = True
    threading.Thread(target=thread_run, args=(request.json,)).start()
    return jsonify({"status": "ok"})

@app.route('/stop', methods=['POST'])
def stop():
    if not session.get('logged_in'): return "", 401
    STOP_SIGNAL.set()
    return jsonify({"status": "ok"})

@app.route('/clear', methods=['POST'])
def clear_logs():
    if not session.get('logged_in'): return "", 401
    LOG_HISTORY.clear()
    return jsonify({"status": "ok"})

# --- CATEGORY MESSAGES ROUTES ---
@app.route('/get_messages', methods=['POST'])
def get_messages():
    if not session.get('logged_in'): return "", 401
    cat = request.json.get('category', '9')
    fname = REASON_FILES.get(cat, 'other.txt')
    path = os.path.join(MESSAGES_DIR, fname)
    content = open(path, 'r', encoding='utf-8').read() if os.path.exists(path) else ""
    return jsonify({"content": content})

@app.route('/save_messages', methods=['POST'])
def save_messages():
    if not session.get('logged_in'): return "", 401
    cat = request.json.get('category', '9')
    content = request.json.get('content', '')
    fname = REASON_FILES.get(cat, 'other.txt')
    with open(os.path.join(MESSAGES_DIR, fname), 'w', encoding='utf-8') as f:
        f.write(content)
    return jsonify({"status": "ok"})

# --- SESSIONS & PROXIES ROUTES ---
@app.route('/upload_sessions', methods=['POST'])
def upload_sessions():
    if not session.get('logged_in'): return "", 401
    count = 0
    for f in request.files.getlist('files'):
        if f.filename.endswith('.session'): 
            f.save(os.path.join(SESSIONS_DIR, f.filename))
            count += 1
        elif f.filename.endswith('.zip'):
            zip_path = os.path.join(BASE_DIR, f.filename)
            f.save(zip_path)
            try:
                with ZipFile(zip_path, 'r') as z:
                    for n in z.namelist():
                        if n.endswith('.session'):
                            s_data = z.read(n)
                            # Save without extracting any sub-folders from the zip
                            with open(os.path.join(SESSIONS_DIR, os.path.basename(n)), 'wb') as sf:
                                sf.write(s_data)
                            count += 1
            except Exception as e:
                emit_log(f"⚠️ ZIP Extract Error: {e}")
            finally:
                if os.path.exists(zip_path): os.remove(zip_path)
                
    emit_log(f"📁 {count} NEW SESSIONS ADDED TO POOL.")
    return jsonify({"status": "ok", "count": count})

@app.route('/download_sessions')
def download_sessions():
    if not session.get('logged_in'): return "", 401
    zip_path = os.path.join(BASE_DIR, 'all_sessions')
    shutil.make_archive(zip_path, 'zip', SESSIONS_DIR)
    return send_file(f"{zip_path}.zip", as_attachment=True)

@app.route('/download_expired')
def download_expired():
    if not session.get('logged_in'): return "", 401
    zip_path = os.path.join(BASE_DIR, 'expired_sessions_archive')
    shutil.make_archive(zip_path, 'zip', EXPIRED_DIR)
    return send_file(f"{zip_path}.zip", as_attachment=True)

@app.route('/delete_expired', methods=['POST'])
def delete_expired():
    if not session.get('logged_in'): return "", 401
    for f in glob.glob(os.path.join(EXPIRED_DIR, '*.session')):
        os.remove(f)
    emit_log("🗑️ ALL EXPIRED SESSIONS DELETED.")
    return jsonify({"status": "ok"})

@app.route('/save_proxies', methods=['POST'])
def save_proxies():
    if not session.get('logged_in'): return "", 401
    with open(PROXIES_FILE, 'w') as f: f.write(request.json.get('proxies', ''))
    return jsonify({"status": "ok"})

@app.route('/check_proxies', methods=['POST'])
def check_proxies():
    if not session.get('logged_in'): return "", 401
    def run_check():
        global PROXY_STATUS
        if not os.path.exists(PROXIES_FILE): return
        with open(PROXIES_FILE, 'r') as f: proxies = [l.strip() for l in f if l.strip()]
        active, dead = 0, 0
        loop = asyncio.new_event_loop()
        for p in proxies:
            if loop.run_until_complete(validate_proxy(p)): active += 1
            else: dead += 1
        PROXY_STATUS = {"active": active, "dead": dead, "last_check": datetime.now().strftime('%H:%M')}
        emit_log(f"📊 PROXY AUDIT: {active} OK, {dead} DEAD.")
    threading.Thread(target=run_check).start()
    return jsonify({"status": "ok"})

# --- API KEY MANAGEMENT ROUTES ---
@app.route('/save_creds', methods=['POST'])
def save_creds():
    if not session.get('logged_in'): return "", 401
    d = request.json
    try:
        with open(CREDS_FILE, 'r') as f: creds = json.load(f)
    except: creds = []
    creds.append({"api_id": d['api_id'], "api_hash": d['api_hash']})
    with open(CREDS_FILE, 'w') as f: json.dump(creds, f)
    return jsonify({"status": "ok"})

@app.route('/get_creds', methods=['GET'])
def get_creds():
    if not session.get('logged_in'): return "", 401
    if os.path.exists(CREDS_FILE):
        try: return jsonify(json.load(open(CREDS_FILE, 'r')))
        except: pass
    return jsonify([])

@app.route('/delete_cred', methods=['POST'])
def delete_cred():
    if not session.get('logged_in'): return "", 401
    idx = request.json.get('index')
    try:
        creds = json.load(open(CREDS_FILE, 'r'))
        creds.pop(idx)
        json.dump(creds, open(CREDS_FILE, 'w'))
    except: pass
    return jsonify({"status": "ok"})

@app.route('/check_api_keys', methods=['POST'])
def check_api_keys():
    if not session.get('logged_in'): return "", 401
    def run_api_check():
        creds = []
        try:
            with open(CREDS_FILE, 'r') as f: creds = json.load(f)
        except: return
        valid, dead = 0, 0
        loop = asyncio.new_event_loop()
        emit_log("🔍 STARTING API KEY AUDIT...")
        for c in creds:
            try:
                # FIX: Telethon needs API ID to be an INTEGER!
                api_id = int(c.get('api_id', 0))
                api_hash = str(c.get('api_hash', ''))
                if not api_id or not api_hash: continue
                
                proxy_dict, _ = get_proxy()
                client = TelegramClient(MemorySession(), api_id, api_hash, proxy=proxy_dict)
                loop.run_until_complete(client.connect())
                
                if client.is_connected():
                    valid += 1
                    emit_log(f"🔑 API {api_id}: ACTIVE & VALID")
                loop.run_until_complete(client.disconnect())
            except Exception as e:
                dead += 1
                emit_log(f"❌ API {c.get('api_id')}: DEAD ({type(e).__name__})")
                
        emit_log(f"📊 API AUDIT COMPLETE: {valid} OK, {dead} DEAD.")
    
    threading.Thread(target=run_api_check).start()
    return jsonify({"status": "ok"})

@app.route('/save_settings', methods=['POST'])
def save_settings():
    if not session.get('logged_in'): return "", 401
    conf = load_config()
    d = request.json
    conf['min_delay'] = int(d.get('min_d', 3))
    conf['max_delay'] = int(d.get('max_d', 8))
    conf['bot_w'] = int(d.get('bot_w', 60))
    save_config(conf)
    return jsonify({"status": "ok"})

@app.route('/api/bot/save', methods=['POST'])
def save_bot_token():
    if not session.get('logged_in'): return "", 401
    conf = load_config()
    conf['bot_token'] = request.json.get('token', '')
    save_config(conf)
    return jsonify({"status": "ok"})

@app.route('/api/bot/start', methods=['POST'])
def start_bot():
    global BOT_THREAD
    if not session.get('logged_in'): return "", 401
    if IS_BOT_RUNNING: return jsonify({"status": "already_running"})
    
    token = load_config().get('bot_token', '')
    if not token: return jsonify({"status": "no_token"})
    
    BOT_THREAD = threading.Thread(target=run_bot_thread, args=(token,))
    BOT_THREAD.daemon = True
    BOT_THREAD.start()

    webhook_url = request.url_root.replace('http://', 'https://') + 'webhook'
    r = requests.get(f"https://api.telegram.org/bot{token}/setWebhook?url={webhook_url}")
    if r.status_code == 200: emit_log(f"🌐 WEBHOOK SET TO: {webhook_url}")
    else: emit_log(f"⚠️ WEBHOOK FAILED: {r.text}")

    return jsonify({"status": "ok"})

@app.route('/api/bot/stop', methods=['POST'])
def stop_bot():
    global IS_BOT_RUNNING
    if not session.get('logged_in'): return "", 401
    token = load_config().get('bot_token', '')

    if token:
        requests.get(f"https://api.telegram.org/bot{token}/deleteWebhook")
        emit_log("🗑️ WEBHOOK DELETED FROM TELEGRAM.")

    if IS_BOT_RUNNING and BOT_LOOP:
        asyncio.run_coroutine_threadsafe(shutdown_bot(), BOT_LOOP)
        
    return jsonify({"status": "ok"})

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    if IS_BOT_RUNNING and BOT_APP and BOT_LOOP:
        data = request.get_json(force=True)
        update = Update.de_json(data, BOT_APP.bot)
        asyncio.run_coroutine_threadsafe(BOT_APP.process_update(update), BOT_LOOP)
    return "OK", 200

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
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

import asyncio
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import os
import re

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
MONGO_URI = os.getenv("MONGO_URI")

# Setup MongoDB
mongo = AsyncIOMotorClient(MONGO_URI)
db = mongo["telethon_bot"]
sessions_col = db["sessions"]

# Main Bot Token (gunakan BotFather)
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Buat dict untuk menyimpan state login per user
login_state = {}
clients = {}  # user_id: TelegramClient

from telethon.sync import TelegramClient as SyncTelegramClient
from telethon.sessions import StringSession as SyncStringSession

bot = TelegramClient("bot", API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# State login user
LOGIN_STEP = {
    "WAITING_PHONE": 1,
    "WAITING_CODE": 2,
    "WAITING_PASSWORD": 3,
}


def clean_code(code):
    return re.sub(r"\\s+", "", code)


@bot.on(events.NewMessage(pattern="/start"))
async def start(event):
    user_id = event.sender_id
    session = await sessions_col.find_one({"user_id": user_id})

    if session:
        await event.respond("\ud83d\udd13 Kamu sudah login. Pilih menu:", buttons=[
            [
                Button.inline("\ud83d\udcca Status", data="status"),
                Button.inline("\ud83d\udcc6 Grup", data="grup"),
                Button.inline("\ud83d\udcc4 Channel", data="channel")
            ],
            [
                Button.inline("\u2696\ufe0f Operasi Massal", data="mass_leave"),
                Button.inline("\u274c Leave Manual", data="leave_manual")
            ]
        ])
    else:
        login_state[user_id] = LOGIN_STEP["WAITING_PHONE"]
        await event.respond("\ud83d\udcf2 Masukkan nomor telepon kamu (contoh: +628123456789)")


@bot.on(events.NewMessage)
async def handle_login(event):
    user_id = event.sender_id
    if user_id not in login_state:
        return

    step = login_state[user_id]
    text = event.raw_text.strip()

    if step == LOGIN_STEP["WAITING_PHONE"]:
        phone = text
        client = TelegramClient(StringSession(), API_ID, API_HASH)
        await client.connect()
        try:
            await client.send_code_request(phone)
            login_state[user_id] = LOGIN_STEP["WAITING_CODE"]
            login_state[f"phone_{user_id}"] = phone
            login_state[f"client_{user_id}"] = client
            await event.respond("\ud83d\udd10 Masukkan kode OTP dengan spasi antar digit (contoh: 1 2 3 4 5 6)")
        except Exception as e:
            await event.respond(f"Gagal mengirim kode OTP: {e}")

    elif step == LOGIN_STEP["WAITING_CODE"]:
        code = clean_code(text)
        client = login_state.get(f"client_{user_id}")
        phone = login_state.get(f"phone_{user_id}")
        try:
            await client.sign_in(phone=phone, code=code)
            string = client.session.save()
            await sessions_col.update_one({"user_id": user_id}, {"$set": {"user_id": user_id, "session": string}}, upsert=True)
            clients[user_id] = client
            login_state.pop(user_id)
            await event.respond("\ud83d\ude80 Berhasil login! Ketik /start untuk mulai.")
        except Exception as e:
            if "password" in str(e).lower():
                login_state[user_id] = LOGIN_STEP["WAITING_PASSWORD"]
                await event.respond("\ud83d\udd10 Akun ini menggunakan verifikasi dua langkah. Masukkan password:")
            else:
                await event.respond(f"Gagal login: {e}")

    elif step == LOGIN_STEP["WAITING_PASSWORD"]:
        password = text
        client = login_state.get(f"client_{user_id}")
        try:
            await client.sign_in(password=password)
            string = client.session.save()
            await sessions_col.update_one({"user_id": user_id}, {"$set": {"user_id": user_id, "session": string}}, upsert=True)
            clients[user_id] = client
            login_state.pop(user_id)
            await event.respond("\ud83d\ude80 Berhasil login! Ketik /start untuk mulai.")
        except Exception as e:
            await event.respond(f"Password salah: {e}")


# Callback untuk tombol inline
from telethon.tl.functions.messages import GetDialogsRequest
from telethon.tl.types import InputPeerEmpty, Channel, Chat
from telethon.tl.functions.channels import GetParticipantsRequest
from telethon.tl.types import ChannelParticipantsAdmins
from telethon import Button

@bot.on(events.CallbackQuery(data=b"status"))
async def status_handler(event):
    user_id = event.sender_id
    session = await sessions_col.find_one({"user_id": user_id})
    if not session:
        await event.answer("Belum login.", alert=True)
        return

    client = TelegramClient(StringSession(session["session"]), API_ID, API_HASH)
    await client.connect()

    result = await client(GetDialogsRequest(
        offset_date=None,
        offset_id=0,
        offset_peer=InputPeerEmpty(),
        limit=200,
        hash=0
    ))

    groups = [dialog for dialog in result.chats if isinstance(dialog, Chat)]
    channels = [dialog for dialog in result.chats if isinstance(dialog, Channel)]

    admin_groups = []
    for g in groups:
        try:
            admins = await client(GetParticipantsRequest(
                channel=g,
                filter=ChannelParticipantsAdmins(),
                offset=0,
                limit=1,
                hash=0
            ))
            for a in admins.users:
                if a.id == (await client.get_me()).id:
                    admin_groups.append(g)
        except:
            pass

    await event.edit(f"\ud83d\udcca Status:\n- Total Grup: {len(groups)}\n- Total Channel: {len(channels)}\n- Admin di: {len(admin_groups)} grup")

# Tambahan fitur 'grup', 'channel', 'mass_leave', 'leave_manual' bisa dilanjutkan sesuai pola di atas

print("Bot berjalan...")
bot.run_until_disconnected()

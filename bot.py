import os
import asyncio
from telethon import TelegramClient, events, Button, errors, sync
from telethon.sessions import StringSession
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
MONGO_URI = os.getenv("MONGO_URI")

# Database setup
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client["telethon_bot"]
users_collection = db["users"]  # Simpan data user dan sesi

# Inline keyboard tombol 3x3 (9 tombol)
def main_keyboard():
    return [
        [
            Button.inline("📊 Status", b"status"),
            Button.inline("👥 Grup", b"group"),
            Button.inline("📢 Channel", b"channel"),
        ],
        [
            Button.inline("🛠 Operasi Massal", b"mass_leave"),
            Button.inline("🚪 Leave Manual", b"leave_manual"),
            Button.inline("🔒 Logout", b"logout"),
        ],
        [
            Button.inline("ℹ️ Bantuan", b"help"),
            Button.inline("📝 Info Bot", b"info"),
            Button.inline("❌ Tutup", b"close"),
        ]
    ]

# Helper: ambil client Telethon per user dari sesi string
async def get_user_client(user_id):
    user = await users_collection.find_one({"user_id": user_id})
    if user and "session_str" in user:
        try:
            client = TelegramClient(StringSession(user["session_str"]), API_ID, API_HASH)
            await client.start()
            return client
        except Exception as e:
            print(f"Error membuka client user {user_id}: {e}")
    return None

# Simpan sesi user ke DB
async def save_session(user_id, session_str):
    await users_collection.update_one(
        {"user_id": user_id},
        {"$set": {"session_str": session_str}},
        upsert=True
    )

# Hapus sesi user (logout)
async def delete_session(user_id):
    await users_collection.delete_one({"user_id": user_id})

# Bot utama (pakai TelegramClient tanpa sesi karena hanya untuk menerima command dan mengelola sesi user)
bot = TelegramClient('bot_session', API_ID, API_HASH)

@bot.on(events.NewMessage(pattern='/start'))
async def start(event):
    sender = await event.get_sender()
    user_id = sender.id
    user = await users_collection.find_one({"user_id": user_id})

    if user and "session_str" in user:
        text = ("👋 Selamat datang kembali!\n\n"
                "Kamu sudah login.\n\n"
                "Gunakan tombol di bawah untuk mengakses fitur.")
        await event.respond(text, buttons=main_keyboard())
    else:
        text = ("👋 Halo! Sebelum menggunakan bot ini, kamu harus login dulu.\n\n"
                "Ketik nomor telepon kamu dengan format: +628123456789\n"
                "Bot akan mengirim kode OTP untuk verifikasi.\n\n"
                "Contoh:\n`+628123456789`")
        await event.respond(text)

@bot.on(events.NewMessage(pattern=r'^\+\d{6,15}$'))
async def login_start(event):
    sender = await event.get_sender()
    user_id = sender.id
    phone = event.text.strip()

    # Cek apakah user sudah login
    user = await users_collection.find_one({"user_id": user_id})
    if user and "session_str" in user:
        await event.respond("Kamu sudah login. Gunakan /start untuk fitur.")
        return

    # Mulai proses login
    temp_client = TelegramClient(StringSession(), API_ID, API_HASH)

    try:
        await temp_client.connect()
        await temp_client.send_code_request(phone)
    except errors.PhoneNumberInvalidError:
        await event.respond("Nomor telepon tidak valid. Coba lagi.")
        await temp_client.disconnect()
        return
    except Exception as e:
        await event.respond(f"Error saat mengirim kode: {e}")
        await temp_client.disconnect()
        return

    # Simpan nomor dan client sementara dalam konteks event (atau bisa di DB sementara, tapi disini simple simpan di dict)
    if not hasattr(bot, 'login_sessions'):
        bot.login_sessions = {}
    bot.login_sessions[user_id] = {"client": temp_client, "phone": phone}

    await event.respond("Kode OTP sudah dikirim ke nomor kamu.\n"
                        "Kirim kode yang kamu terima sebagai pesan balasan.")

@bot.on(events.NewMessage(pattern=r'^\d{4,6}$'))
async def code_verification(event):
    sender = await event.get_sender()
    user_id = sender.id
    code = event.text.strip()

    if not hasattr(bot, 'login_sessions') or user_id not in bot.login_sessions:
        # Tidak ada sesi login aktif
        return

    session = bot.login_sessions[user_id]
    temp_client = session["client"]
    phone = session["phone"]

    try:
        await temp_client.sign_in(phone=phone, code=code)
    except errors.SessionPasswordNeededError:
        await event.respond("Kamu punya 2FA, silakan kirim password kamu.")
        # Tandai butuh password
        session["need_2fa"] = True
        return
    except Exception as e:
        await event.respond(f"Kode salah atau gagal login: {e}")
        await temp_client.disconnect()
        del bot.login_sessions[user_id]
        return

    # Login sukses
    session_str = temp_client.session.save()
    await save_session(user_id, session_str)
    await event.respond("✅ Login berhasil! Gunakan /start untuk fitur.")
    del bot.login_sessions[user_id]

@bot.on(events.NewMessage())
async def two_fa_handler(event):
    sender = await event.get_sender()
    user_id = sender.id
    text = event.text.strip()

    if not hasattr(bot, 'login_sessions') or user_id not in bot.login_sessions:
        return

    session = bot.login_sessions[user_id]
    if "need_2fa" in session and session["need_2fa"]:
        temp_client = session["client"]
        try:
            await temp_client.sign_in(password=text)
        except Exception as e:
            await event.respond(f"Password salah atau gagal login: {e}")
            await temp_client.disconnect()
            del bot.login_sessions[user_id]
            return

        session_str = temp_client.session.save()
        await save_session(user_id, session_str)
        await event.respond("✅ Login berhasil dengan 2FA! Gunakan /start untuk fitur.")
        del bot.login_sessions[user_id]

# Fungsi helper fitur utama, hanya bisa dipakai kalau user sudah login
async def check_login(event):
    sender = await event.get_sender()
    user_id = sender.id
    user = await users_collection.find_one({"user_id": user_id})
    if not user or "session_str" not in user:
        await event.respond("⚠️ Kamu harus login dulu. Kirim nomor telepon dengan format: +628123456789")
        return None
    client = await get_user_client(user_id)
    if client is None:
        await event.respond("⚠️ Gagal buka sesi Telegram, silakan login ulang dengan nomor telepon.")
        await delete_session(user_id)
        return None
    return client

@bot.on(events.CallbackQuery)
async def callback_handler(event):
    data = event.data.decode('utf-8')
    sender = await event.get_sender()
    user_id = sender.id

    if data == "close":
        await event.delete()
        return

    client = await check_login(event)
    if client is None:
        await event.answer("Login dulu ya!", alert=True)
        return

    if data == "status":
        # Hitung total grup, channel, admin di grup
        await event.answer("Mengambil data, tunggu sebentar...", alert=False)
        dialogs = await client.get_dialogs()
        total_groups = 0
        total_channels = 0
        total_admins = 0
        admin_info = []

        for d in dialogs:
            if d.is_group:
                total_groups += 1
                # Cek admin di grup (hanya cek sendiri sebagai admin atau semua? Saya cek semua admin)
                admins = []
                try:
                    async for participant in client.iter_participants(d.entity, filter=types.ChannelParticipantsAdmins):
                        admins.append(participant)
                except Exception:
                    admins = []
                total_admins += len(admins)
                admin_info.append(f"{d.name}: {len(admins)} admin")
            elif d.is_channel and not d.is_broadcast:
                total_channels += 1

        text = (
            f"📊 <b>Status Akun</b>\n\n"
            f"• Total Grup: {total_groups}\n"
            f"• Total Channel: {total_channels}\n"
            f"• Total Admin di Semua Grup: {total_admins}\n\n"
            f"<b>Detail Admin per Grup:</b>\n" +
            "\n".join(admin_info)
        )
        await event.edit(text, buttons=main_keyboard(), parse_mode="html")

    elif data == "group":
        dialogs = await client.get_dialogs()
        groups = [d for d in dialogs if d.is_group]

        if not groups:
            await event.edit("❌ Kamu tidak tergabung di grup mana pun.", buttons=main_keyboard())
            return

        text = "<b>Daftar Grup Kamu:</b>\n\n"
        for g in groups:
            text += f"• {g.name} (ID: {g.id})\n"

        await event.edit(text, buttons=main_keyboard(), parse_mode="html")

    elif data == "channel":
        dialogs = await client.get_dialogs()
        channels = [d for d in dialogs if d.is_channel and not d.is_broadcast]

        if not channels:
            await event.edit("❌ Kamu tidak tergabung di channel mana pun.", buttons=main_keyboard())
            return

        text = "<b>Daftar Channel Kamu:</b>\n\n"
        for c in channels:
            text += f"• {c.name} (ID: {c.id})\n"

        await event.edit(text, buttons=main_keyboard(), parse_mode="html")

    elif data == "mass_leave":
        # Mass leave: keluar dari semua grup sekaligus
        dialogs = await client.get_dialogs()
        groups = [d for d in dialogs if d.is_group]

        if not groups:
            await event.edit("❌ Tidak ada grup untuk di-leave.", buttons=main_keyboard())
            return

        count = 0
        for g in groups:
            try:
                await client.delete_dialog(g.entity)
                count += 1
            except Exception:
                pass

        await event.edit(f"✅ Berhasil keluar dari {count} grup.", buttons=main_keyboard())

    elif data == "leave_manual":
        # Tampilkan daftar grup dengan tombol untuk leave manual 1 per 1
        dialogs = await client.get_dialogs()
        groups = [d for d in dialogs if d.is_group]

        if not groups:
            await event.edit("❌ Tidak ada grup untuk di-leave.", buttons=main_keyboard())
            return

        # Buat keyboard tombol leave grup satu-satu
        buttons = []
        for g in groups:
            buttons.append([Button.inline(f"🚪 Leave {g.name}", f"leave_{g.id}")])
        buttons.append([Button.inline("🔙 Kembali", "close")])

        await event.edit("Pilih grup untuk keluar:", buttons=buttons)

    elif data.startswith("leave_"):
        # Leave manual grup spesifik
        group_id = int(data.split("_")[1])
        try:
            await client.delete_dialog(group_id)
            await event.edit(f"✅ Berhasil keluar dari grup ID {group_id}", buttons=main_keyboard())
        except Exception as e:
            await event.answer(f"Gagal keluar dari grup: {e}", alert=True)

    elif data == "logout":
        await delete_session(user_id)
        await event.edit("🔒 Kamu sudah logout. Kirim /start untuk login ulang.", buttons=None)

    elif data == "help":
        help_text = (
            "📌 <b>Fitur Bot:</b>\n"
            "• Status: Lihat total grup, channel, dan admin.\n"
            "• Grup: Daftar grup yang kamu ikuti.\n"
            "• Channel: Daftar channel yang kamu ikuti.\n"
            "• Operasi Massal: Keluar dari semua grup sekaligus.\n"
            "• Leave Manual: Keluar dari grup satu per satu.\n"
            "• Logout: Keluar dari sesi login.\n\n"
            "Login dulu dengan mengirim nomor telepon ya."
        )
        await event.edit(help_text, buttons=main_keyboard(), parse_mode="html")

    elif data == "info":
        info_text = (
            "🤖 Bot ini dibuat dengan Telethon.\n"
            "Login menggunakan nomor telepon Telegram kamu.\n"
            "Semua sesi disimpan aman di MongoDB.\n"
            "Gunakan tombol untuk navigasi fitur."
        )
        await event.edit(info_text, buttons=main_keyboard())

async def main():
    print("Bot berjalan...")
    await bot.start()
    await bot.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())

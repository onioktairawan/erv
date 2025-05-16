import os
import asyncio
from pyrogram import Client, filters, enums
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGODB_URI = os.getenv("MONGODB_URI")

bot = Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
mongo = MongoClient(MONGODB_URI)
db = mongo["telegram_user_data"]
users = db["users"]

user_sessions = {}
login_states = {}

ITEMS_PER_PAGE = 10

async def ask_otp(_, __, message: Message):
    user_id = message.from_user.id
    if user_id not in login_states:
        login_states[user_id] = {"step": "phone"}
        await message.reply("Masukkan nomor HP kamu:")
    elif login_states[user_id]["step"] == "phone":
        phone = message.text.strip()
        login_states[user_id]["phone"] = phone
        login_states[user_id]["step"] = "otp"
        await message.reply("Masukkan kode OTP yang kamu terima (gunakan spasi, misal: 1 2 3 4 5):")
    elif login_states[user_id]["step"] == "otp":
        otp = message.text.strip().replace(" ", "")
        phone = login_states[user_id]["phone"]
        try:
            user_client = Client(f"session_{user_id}", api_id=API_ID, api_hash=API_HASH, phone_number=phone, in_memory=True)
            await user_client.connect()
            sent_code = await user_client.send_code(phone)
            await user_client.sign_in(phone_number=phone, phone_code=otp)
            user_sessions[user_id] = user_client
            users.update_one({"_id": user_id}, {"$set": {"phone": phone}}, upsert=True)
            await message.reply("Login berhasil!", reply_markup=main_menu())
            login_states.pop(user_id, None)
        except Exception as e:
            await message.reply(f"Login gagal: {e}")

@bot.on_message(filters.command("start") & filters.private)
async def start_handler(_, message: Message):
    user_id = message.from_user.id
    if user_id not in user_sessions:
        await ask_otp(_, __, message)
    else:
        await message.reply("Selamat datang kembali!", reply_markup=main_menu())

@bot.on_message(filters.private & ~filters.command("start"))
async def handle_messages(_, message: Message):
    user_id = message.from_user.id
    if user_id in login_states:
        await ask_otp(_, __, message)

@bot.on_callback_query()
async def callback_handler(_, cq: CallbackQuery):
    data = cq.data
    user_id = cq.from_user.id

    if user_id not in user_sessions:
        await cq.message.edit_text("Sesi login tidak ditemukan. Silakan /start lagi.")
        return

    user_client = user_sessions[user_id]
    await user_client.connect()

    if data.startswith("list_"):
        list_type, page = data.split("_")[1], int(data.split("_")[2])
        chats = []
        async for dialog in user_client.get_dialogs():
            if list_type == "group" and dialog.chat.type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
                chats.append(dialog.chat)
            elif list_type == "channel" and dialog.chat.type == enums.ChatType.CHANNEL:
                chats.append(dialog.chat)

        start = page * ITEMS_PER_PAGE
        end = start + ITEMS_PER_PAGE
        page_chats = chats[start:end]

        buttons = [
            [InlineKeyboardButton(f"{chat.title}", callback_data=f"leave_{chat.id}_{list_type}_{page}") for chat in page_chats[i:i+5]]
            for i in range(0, len(page_chats), 5)
        ]

        nav_buttons = []
        if start > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"list_{list_type}_{page - 1}"))
        if end < len(chats):
            nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"list_{list_type}_{page + 1}"))

        buttons.append(nav_buttons)
        buttons.append([InlineKeyboardButton("⬅️ Kembali", callback_data="menu")])

        await cq.message.edit_text(f"Daftar {list_type.title()} (halaman {page+1})", reply_markup=InlineKeyboardMarkup(buttons))
        await cq.answer()
        return

    if data == "menu":
        await cq.message.edit_text("Pilih menu:", reply_markup=main_menu())
        await cq.answer()
        return

    if data.startswith("leave_"):
        _, chat_id, list_type, page = data.split("_")
        try:
            await user_client.leave_chat(int(chat_id))
            await cq.message.edit_text(f"Berhasil keluar dari {list_type}.", reply_markup=main_menu())
        except Exception as e:
            await cq.message.edit_text(f"Gagal keluar: {e}")
        await cq.answer()
        return

    await cq.answer()


def main_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📂 Grup", callback_data="list_group_0"),
            InlineKeyboardButton("📢 Channel", callback_data="list_channel_0")
        ],
        [InlineKeyboardButton("🚪 Logout", callback_data="logout")]
    ])

if __name__ == "__main__":
    print("Bot is running...")
    bot.run()

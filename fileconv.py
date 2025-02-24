import pyrogram
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import os
import shutil
import subprocess
import threading
import queue
import time
from buttons import *
import helperfunctions
import mediainfo
import guess
import tormag
import progconv
import others
import tictactoe

# تهيئة البوت
bot_token = os.environ.get("TOKEN", "")
api_hash = os.environ.get("HASH", "")
api_id = os.environ.get("ID", "")
app = Client("my_bot", api_id=api_id, api_hash=api_hash, bot_token=bot_token)
MESGS = {}
task_queue = queue.Queue()  # قائمة انتظار المهام
client_lock = threading.Lock()  # لقفل العمليات الحساسة

# دالة المعالجة الرئيسية
def sendvideo(message, oldmessage):
    try:
        file, msg = down(message)
        thumb, duration, width, height = mediainfo.allinfo(file)
        up(message, file, msg, video=True, capt=f'**{file.split("/")[-1]}**', 
           thumb=thumb, duration=duration, height=height, width=width)
        app.delete_messages(message.chat.id, message_ids=oldmessage.id)
        os.remove(file)
    except Exception as e:
        with client_lock:
            app.send_message(message.chat.id, f"حدث خطأ: {str(e)}")
        if 'file' in locals() and os.path.exists(file):
            os.remove(file)

# دالة المعالجة للخيوط
def worker():
    while True:
        message = task_queue.get()
        if message is None:
            break
        try:
            with client_lock:
                oldmessage = app.send_message(
                    message.chat.id,
                    "جار المعالجة...",
                    reply_to_message_id=message.id
                )
            sendvideo(message, oldmessage)
        except Exception as e:
            with client_lock:
                app.send_message(message.chat.id, f"حدث خطأ: {str(e)}")
        finally:
            task_queue.task_done()

# عند استلام فيديو
@app.on_message(filters.video)
def handle_video(client, message):
    # إضافة الفيديو مباشرة إلى قائمة الانتظار
    task_queue.put(message)
    app.send_message(
        message.chat.id,
        f"تمت إضافة الفيديو إلى قائمة الانتظار ({task_queue.qsize()} ملفات قيد المعالجة)",
        reply_to_message_id=message.id
    )

# عند بدء البوت
@app.on_message(filters.command(['start']))
def start(client, message):
    app.send_message(
        message.chat.id,
        f"مرحبا {message.from_user.mention}!\nأرسل أي فيديو لمعالجته تلقائياً",
        reply_to_message_id=message.id
    )
    
    # بدء 3 خيوط عمل
    for _ in range(3):
        threading.Thread(target=worker, daemon=True).start()

# دوال التنزيل والرفع (مأخوذة من الكود الأصلي مع إضافة locks)
def down(message):
    with client_lock:
        try:
            size = int(message.video.file_size)
        except:
            size = 0
        msg = None
        if size > 25_000_000:
            msg = app.send_message(message.chat.id, '__Downloading__', reply_to_message_id=message.id)
            threading.Thread(target=lambda: downstatus(f'{message.id}downstatus.txt', msg), daemon=True).start()
        file = app.download_media(message, progress=dprogress, progress_args=[message])
        if os.path.exists(f'{message.id}downstatus.txt'):
            os.remove(f'{message.id}downstatus.txt')
        return file, msg

def up(message, file, msg, video=False, capt="", thumb=None, duration=0, width=0, height=0, multi=False):
    with client_lock:
        if msg and not multi:
            try:
                app.edit_message_text(message.chat.id, msg.id, '__Uploading__')
            except:
                pass
        if os.path.getsize(file) > 25_000_000:
            threading.Thread(target=lambda: upstatus(f'{message.id}upstatus.txt', msg), daemon=True).start()
        if not video:
            app.send_document(message.chat.id, document=file, caption=capt, force_document=True, reply_to_message_id=message.id, progress=uprogress, progress_args=[message])
        else:
            app.send_video(message.chat.id, video=file, caption=capt, thumb=thumb, duration=duration, width=width, height=height, reply_to_message_id=message.id, progress=uprogress, progress_args=[message])
        if thumb and os.path.exists(thumb):
            os.remove(thumb)
        if os.path.exists(f'{message.id}upstatus.txt'):
            os.remove(f'{message.id}upstatus.txt')
        if msg and not multi:
            app.delete_messages(message.chat.id, message_ids=msg.id)

# دوال الحالة (مأخوذة من الكود الأصلي)
def dprogress(current, total, message):
    with open(f'{message.id}downstatus.txt', "w") as f:
        f.write(f"{current * 100 / total:.1f}%")

def uprogress(current, total, message):
    with open(f'{message.id}upstatus.txt', "w") as f:
        f.write(f"{current * 100 / total:.1f}%")

def downstatus(statusfile, message):
    while os.path.exists(statusfile):
        with open(statusfile, "r") as f:
            txt = f.read().strip()
        with client_lock:
            app.edit_message_text(message.chat.id, message.id, f"Downloading: {txt}")
        time.sleep(5)

def upstatus(statusfile, message):
    while os.path.exists(statusfile):
        with open(statusfile, "r") as f:
            txt = f.read().strip()
        with client_lock:
            app.edit_message_text(message.chat.id, message.id, f"Uploading: {txt}")
        time.sleep(5)

# تشغيل البوت
if __name__ == "__main__":
    app.run()

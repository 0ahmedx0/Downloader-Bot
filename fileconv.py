import os
import asyncio
import logging
import tempfile
from collections import defaultdict
from pyrogram import Client, filters, enums
from pyrogram.types import InputMediaVideo
from moviepy.editor import VideoFileClip

# ---------- استخراج معرف القناة من متغيرات البيئة ----------
raw_channel_id = os.getenv("CHANNEL_ID")
if raw_channel_id:
    if raw_channel_id.startswith("@"):
        CHANNEL_ID = raw_channel_id
    else:
        try:
            CHANNEL_ID = int(raw_channel_id)
        except ValueError:
            CHANNEL_ID = raw_channel_id
else:
    CHANNEL_ID = None

# ---------- إعدادات البيئة ----------
runtime_dir = '/tmp/runtime-user'
if not os.path.exists(runtime_dir):
    os.makedirs(runtime_dir, mode=0o700)
os.environ['XDG_RUNTIME_DIR'] = runtime_dir

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ---------- تهيئة البوت باستخدام بوت توكن فقط ----------
app = Client(
    "advanced_video_bot",
    api_id=os.environ.get("ID"),
    api_hash=os.environ.get("HASH"),
    bot_token=os.environ.get("TOKEN"),
    parse_mode=enums.ParseMode.MARKDOWN
)

# ---------- هياكل البيانات ----------
class ChatQueue:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.active = False
        self.retry_count = 3  # عدد محاولات إعادة المحاولة
        self.album_videos = []  # لتجميع الفيديوهات المعالجة

chat_queues = defaultdict(ChatQueue)
TEMP_DIR = tempfile.TemporaryDirectory()  # مجلد مؤقت يتم تنظيفه تلقائيًا

# قاموس لتخزين رسالة التأكيد الخاصة بكل رسالة واردة
confirmation_messages = {}

# ---------- وظائف أساسية ----------
async def handle_errors(func, *args, **kwargs):
    """معالجة الأخطاء مع إعادة المحاولة"""
    for attempt in range(1, 4):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logging.error(f"محاولة {attempt} فشلت: {str(e)}")
            if attempt == 3:
                raise
            await asyncio.sleep(2 ** attempt)

async def generate_thumbnail(video_path):
    """إنشاء صورة مصغرة باستخدام ffmpeg"""
    output_path = os.path.join(TEMP_DIR.name, f"thumb_{os.path.basename(video_path)}.jpg")
    cmd = [
        'ffmpeg', '-y', '-loglevel', 'error',
        '-i', video_path, '-ss', '00:00:01',
        '-vframes', '1', '-vf', 'scale=320:-1',
        output_path
    ]
    proc = await asyncio.create_subprocess_exec(*cmd)
    await proc.wait()
    return output_path if os.path.exists(output_path) else None

async def process_video(chat_id, message):
    """
    معالجة فيديو واحد:
    - تنزيل الملف، إصلاحه باستخدام ffmpeg،
    - استخراج البيانات باستخدام MoviePy وإنشاء صورة مصغرة.
    تُعيد الدالة قاموسًا يحتوي على بيانات الفيديو المعالج.
    """
    temp_file = None
    thumb = None
    result = None

    try:
        logging.info(f"بدء تنزيل الملف لرسالة {message.id} ...")
        temp_file = await handle_errors(
            app.download_media,
            message,
            file_name=os.path.join(TEMP_DIR.name, f"temp_{message.id}.mp4")
        )
        logging.info(f"تم تنزيل الملف بنجاح: {temp_file}")

        # إصلاح الفيديو باستخدام ffmpeg
        fixed_file = os.path.join(TEMP_DIR.name, f"fixed_{message.id}.mp4")
        cmd = [
            'ffmpeg', '-y', '-i', temp_file, '-c', 'copy',
            '-movflags', 'faststart', fixed_file
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0 and os.path.exists(fixed_file):
            os.remove(temp_file)
            temp_file = fixed_file

        # استخراج بيانات الفيديو باستخدام MoviePy
        with VideoFileClip(temp_file, audio=False) as clip:
            metadata = {
                'duration': int(clip.duration),
                'width': clip.size[0],
                'height': clip.size[1]
            }
        # إنشاء الصورة المصغرة
        thumb = await handle_errors(generate_thumbnail, temp_file)

        logging.info("انتظار 5 ثوانٍ بعد المعالجة...")
        await asyncio.sleep(5)

        # إعداد البيانات لإرسال الألبوم لاحقًا
        result = {
            "file_path": temp_file,
            "thumb": thumb,
            "duration": metadata['duration'],
            "width": metadata['width'],
            "height": metadata['height'],
            "user_message_id": message.id,
            "confirmation_msg_id": confirmation_messages.pop(message.id, None)
        }
        logging.info(f"تمت معالجة الفيديو بنجاح: {temp_file}")
        return result

    except Exception as e:
        logging.error(f"فشل معالجة الفيديو {message.id}: {e}")
        if temp_file and os.path.exists(temp_file):
            os.remove(temp_file)
        if thumb and os.path.exists(thumb):
            os.remove(thumb)
        raise

async def send_album(chat_id, album_videos):
    """
    إرسال مجموعة من الفيديوهات كألبوم باستخدام send_media_group.
    يتم تعيين "حصريات🌈" كتعليق على أول فيديو.
    بعد الإرسال، يتم حذف رسائل المستخدم والتأكيد والملفات المؤقتة.
    يُرسل الألبوم إلى القناة (CHANNEL_ID) إن كانت محددة.
    """
    media_list = []
    for i, video in enumerate(album_videos):
        caption = "حصريات🌈" if i == 0 else ""
        media_list.append(InputMediaVideo(
            media=video["file_path"],
            caption=caption,
            duration=video["duration"],
            width=video["width"],
            height=video["height"],
            thumb=video["thumb"]
        ))
    target_chat = CHANNEL_ID if CHANNEL_ID is not None else chat_id
    logging.info("بدء رفع الألبوم باستخدام send_media_group ...")
    try:
        await handle_errors(
            app.send_media_group,
            chat_id=target_chat,
            media=media_list
        )
        logging.info("تم رفع الألبوم بنجاح.")
    except Exception as e:
        logging.error(f"فشل رفع الألبوم: {e}")
        raise

    # بعد الإرسال، نقوم بحذف رسائل المستخدم والتأكيد والملفات المؤقتة
    for video in album_videos:
        try:
            await app.delete_messages(chat_id, video["user_message_id"])
            logging.info(f"تم حذف رسالة المستخدم {video['user_message_id']}.")
        except Exception as e:
            logging.error(f"فشل حذف رسالة المستخدم {video['user_message_id']}: {e}")
        if video["confirmation_msg_id"]:
            try:
                await app.delete_messages(chat_id, video["confirmation_msg_id"])
                logging.info(f"تم حذف رسالة التأكيد {video['confirmation_msg_id']}.")
            except Exception as e:
                logging.error(f"فشل حذف رسالة التأكيد {video['confirmation_msg_id']}: {e}")
        if os.path.exists(video["file_path"]):
            os.remove(video["file_path"])
        if video["thumb"] and os.path.exists(video["thumb"]):
            os.remove(video["thumb"])
    
    logging.info("انتظار 10 ثوانٍ قبل إمكانية إرسال ألبوم جديد ...")
    await asyncio.sleep(10)

async def process_queue(chat_id):
    """
    معالجة طابور دردشة واحدة:
    تتم معالجة كل رسالة فيديو وإضافة بياناتها إلى قائمة الألبوم.
    عند وصول عدد الفيديوهات إلى 10 (أو أكثر) يتم إرسال كل دفعة من 10 فيديوهات،
    ويتم الاحتفاظ بالفيديوهات المتبقية للانضمام إليها عند ورود فيديوهات جديدة.
    """
    cq = chat_queues[chat_id]
    try:
        while not cq.queue.empty():
            message = await cq.queue.get()
            video_data = await process_video(chat_id, message)
            cq.album_videos.append(video_data)
            cq.queue.task_done()
            # تأخير 3 ثوانٍ قبل البدء بمعالجة الفيديو التالي
            await asyncio.sleep(3)
            # إذا أصبح عدد الفيديوهات في القائمة 10 أو أكثر، نرسل دفعة من 10 فيديوهات فقط
            while len(cq.album_videos) >= 10:
                album_to_send = cq.album_videos[:10]
                logging.info("تم تجميع 10 فيديوهات، بدء إرسال الألبوم...")
                await send_album(chat_id, album_to_send)
                cq.album_videos = cq.album_videos[10:]
        # لن نقوم بتفريغ الفيديوهات المتبقية إذا كانت أقل من 10، بحيث تبقى محفوظة للانضمام إليها عند ورود فيديوهات جديدة.
    except Exception as e:
        logging.error(f"فشل معالجة الطابور: {str(e)}")
        await app.send_message(chat_id, f"⚠️ حدث خطأ جسيم: {str(e)}")
    finally:
        cq.active = False

async def queue_manager():
    """مدير الطوابير لمراقبة الرسائل الواردة"""
    while True:
        for chat_id, cq in list(chat_queues.items()):
            if not cq.active and not cq.queue.empty():
                cq.active = True
                asyncio.create_task(process_queue(chat_id))
        await asyncio.sleep(1)

# ---------- معالجة الأحداث ----------
@app.on_message(filters.video | filters.document)
async def on_video_receive(client, message):
    """
    عند استقبال فيديو:
    - يُضاف إلى طابور المعالجة.
    - يُرسل رسالة تأكيد للمستخدم.
    - تُخزن رسالة التأكيد لحذفها لاحقًا بعد الإرسال.
    """
    chat_id = message.chat.id
    cq = chat_queues[chat_id]
    await cq.queue.put(message)
    
    confirm_msg = await app.send_message(
        chat_id,
        f"📥 تمت الإضافة إلى القائمة (الموقع: {cq.queue.qsize()})",
        reply_to_message_id=message.id
    )
    confirmation_messages[message.id] = confirm_msg.id

@app.on_message(filters.command("start"))
async def start(client, message):
    """رسالة البدء"""
    text = (
        "مرحبًا في بوت معالجة الفيديو المتقدم! 🎥\n\n"
        "المميزات:\n"
        "• معالجة غير محدودة للفيديوهات\n"
        "• نظام طابور ذكي لكل دردشة\n"
        "• إعادة محاولة تلقائية عند الأخطاء\n"
        "• تأخير 5 ثوانٍ بعد المعالجة\n"
        "• تأخير 3 ثوانٍ بين كل ملف\n"
        "• تجميع 10 فيديوهات وإرسالها كألبوم مع وصف 'حصريات🌈'\n"
        "• تأخير 10 ثوانٍ بين إرسال كل ألبوم\n"
        "• إرسال الألبوم إلى القناة (إذا كان CHANNEL_ID معرفًا)\n"
        "• حذف رسالة المستخدم ورسالة التأكيد بعد الإرسال\n"
        "• تخزين الفيديوهات المتبقية حتى وصول العدد 10 وعدم فقدانها"
    )
    await message.reply(text)

# ---------- التشغيل ----------
if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(queue_manager())
        app.run()
    except KeyboardInterrupt:
        TEMP_DIR.cleanup()
        logging.info("تم إيقاف البوت بنجاح")

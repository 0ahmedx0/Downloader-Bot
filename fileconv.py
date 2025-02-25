import os
import threading
import time
import queue
from collections import defaultdict
from pyrogram import Client, filters

# ---------- Configuration ---------- #
bot_token = os.environ.get("TOKEN", "")
api_hash = os.environ.get("HASH", "") 
api_id = os.environ.get("ID", "")

# ---------- Global Variables ---------- #
app = Client("video_converter_bot", api_id=api_id, api_hash=api_hash, bot_token=bot_token)
user_tasks = defaultdict(dict)  # تخزين مهام المستخدمين
task_queue = queue.Queue()      # قائمة انتظار المهام
MAX_WORKERS = 3                # أقصى عدد للخيوط العاملة

# ---------- Worker Threads Management ---------- #
class WorkerManager:
    def __init__(self):
        self.active_workers = 0
        self.lock = threading.Lock()

    def start_worker(self):
        with self.lock:
            if self.active_workers < MAX_WORKERS:
                worker = threading.Thread(target=self.process_queue, daemon=True)
                worker.start()
                self.active_workers += 1

    def process_queue(self):
        while True:
            try:
                task = task_queue.get(timeout=30)
                if task is None:
                    break
                self.handle_task(task)
            except queue.Empty:
                with self.lock:
                    self.active_workers -= 1
                    break

    def handle_task(self, task):
        try:
            user_id, message = task
            # معالجة الفيديو هنا
            self.process_video(user_id, message)
        except Exception as e:
            error_msg = f"❌ فشل في المعالجة: {str(e)}"
            app.send_message(user_id, error_msg)
        finally:
            task_queue.task_done()

    def process_video(self, user_id, message):
        # إرسال رسالة بدء المعالجة
        status_msg = app.send_message(user_id, "⏳ جاري معالجة الفيديو...", reply_to_message_id=message.id)

        # تنزيل الفيديو
        video_path = self.download_media(message)
        
        # معالجة الفيديو (يمكن إضافة التحويل هنا)
        # ...
        
        # إعادة رفع الفيديو
        self.upload_media(message, video_path, status_msg)
        
        # تنظيف الملفات المؤقتة
        os.remove(video_path)

    def download_media(self, message):
        # تنزيل الملف مع تتبع التقدم
        temp_file = f"temp_{message.id}.mp4"
        return app.download_media(message, file_name=temp_file)

    def upload_media(self, message, file_path, status_msg):
        # رفع الملف مع تتبع التقدم
        app.send_video(
            chat_id=message.chat.id,
            video=file_path,
            reply_to_message_id=message.id
        )
        app.delete_messages(message.chat.id, status_msg.id)

worker_manager = WorkerManager()

# ---------- Bot Handlers ---------- #
@app.on_message(filters.video)
def handle_video(client, message):
    # إضافة المهمة إلى قائمة الانتظار
    task_queue.put((message.chat.id, message))
    worker_manager.start_worker()
    
    # إعلام المستخدم بإضافة المهمة
    app.send_message(
        message.chat.id,
        f"📥 تمت إضافة الفيديو إلى قائمة الانتظار (الموقع: {task_queue.qsize()})",
        reply_to_message_id=message.id
    )

@app.on_message(filters.command("status"))
def show_status(client, message):
    # عرض حالة النظام
    status_info = (
        f"🔄 الخيوط النشطة: {worker_manager.active_workers}\n"
        f"📥 المهام في الانتظار: {task_queue.qsize()}"
    )
    app.send_message(message.chat.id, status_info)

@app.on_message(filters.command("start"))
def start(client, message):
    welcome_msg = (
        "مرحبًا بك في بوت تحويل الفيديوهات! 🎥\n\n"
        "أرسل لي أي فيديو وسأقوم بمعالجته وإعادته لك بتنسيق مناسب للبث.\n"
        "يمكنك مراقبة حالة النظام باستخدام الأمر /status"
    )
    app.send_message(message.chat.id, welcome_msg)

# ---------- Main Execution ---------- #
if __name__ == "__main__":
    print("Starting the bot...")
    app.run()

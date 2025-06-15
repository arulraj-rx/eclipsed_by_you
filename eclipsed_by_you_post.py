import os
import time
import json
import logging
import requests
import dropbox
from datetime import datetime
from pytz import timezone
from telegram import Bot

class DropboxToInstagramUploader:
    INSTAGRAM_API_BASE = "https://graph.facebook.com/v18.0"
    DROPBOX_TOKEN_URL = "https://api.dropbox.com/oauth2/token"

    def __init__(self):
        self.script_name = "eclipsed_by_you_post.py"
        self.ist = timezone('Asia/Kolkata')

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.StreamHandler()]
        )
        self.logger = logging.getLogger()

        # ENV variables
        self.instagram_access_token = os.getenv("IG_ECLIPSED_BY_YOU_TOKEN")
        self.instagram_account_id = os.getenv("IG_ECLIPSED_BY_YOU_ID")
        self.dropbox_app_key = os.getenv("DROPBOX_ECLIPSED_BY_YOU_APP_KEY")
        self.dropbox_app_secret = os.getenv("DROPBOX_ECLIPSED_BY_YOU_APP_SECRET")
        self.dropbox_refresh_token = os.getenv("DROPBOX_ECLIPSED_BY_YOU_REFRESH")
        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

        self.dropbox_folder = "/eclipsed.by.you"
        self.telegram_bot = Bot(token=self.telegram_bot_token)

        self.daily_log_file = "post_log.json"
        self.max_posts_per_day = 4
        self.static_caption = "#eclipsed_by_you"

    def send_message(self, msg):
        prefix = f"[{self.script_name}]\n"
        try:
            self.telegram_bot.send_message(chat_id=self.telegram_chat_id, text=prefix + msg)
        except Exception as e:
            self.logger.error(f"Telegram error: {e}")

    def refresh_dropbox_token(self):
        r = requests.post(self.DROPBOX_TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": self.dropbox_refresh_token,
            "client_id": self.dropbox_app_key,
            "client_secret": self.dropbox_app_secret
        })
        return r.json()["access_token"] if r.status_code == 200 else None

    def list_dropbox_files(self, dbx):
        try:
            entries = dbx.files_list_folder(self.dropbox_folder).entries
            return [f for f in entries if f.name.lower().endswith((".jpg", ".jpeg", ".png", ".mp4", ".mov"))]
        except Exception as e:
            self.send_message(f"❌ Dropbox error: {e}")
            return []

    def read_log(self):
        today = datetime.now(self.ist).strftime('%Y-%m-%d')
        if os.path.exists(self.daily_log_file):
            with open(self.daily_log_file, 'r') as f:
                log = json.load(f)
            if log.get("date") == today:
                return log.get("count", 0)
        return 0

    def update_log(self, count):
        today = datetime.now(self.ist).strftime('%Y-%m-%d')
        with open(self.daily_log_file, 'w') as f:
            json.dump({"date": today, "count": count}, f)

    def post_to_instagram(self, dbx, file):
        name = file.name
        media_type = "REELS" if name.lower().endswith((".mp4", ".mov")) else "IMAGE"
        temp_link = dbx.files_get_temporary_link(file.path_lower).link

        self.send_message(f"🚀 Posting: `{name}`\nType: {media_type}")

        upload_url = f"{self.INSTAGRAM_API_BASE}/{self.instagram_account_id}/media"
        data = {
            "access_token": self.instagram_access_token,
            "caption": self.static_caption
        }
        if media_type == "REELS":
            data.update({"media_type": "REELS", "video_url": temp_link, "share_to_feed": "true"})
        else:
            data["image_url"] = temp_link

        res = requests.post(upload_url, data=data)
        if res.status_code != 200:
            self.send_message(f"❌ Upload failed for `{name}`: {res.text}")
            return False

        creation_id = res.json()["id"]
        if media_type == "REELS":
            for _ in range(12):
                status = requests.get(f"{self.INSTAGRAM_API_BASE}/{creation_id}?fields=status_code&access_token={self.instagram_access_token}").json()
                if status.get("status_code") == "FINISHED":
                    break
                elif status.get("status_code") == "ERROR":
                    self.send_message("❌ IG processing failed.")
                    return False
                time.sleep(5)

        pub = requests.post(
            f"{self.INSTAGRAM_API_BASE}/{self.instagram_account_id}/media_publish",
            data={"creation_id": creation_id, "access_token": self.instagram_access_token}
        )
        if pub.status_code == 200:
            dbx.files_delete_v2(file.path_lower)
            self.send_message(f"✅ Posted `{name}` and deleted from Dropbox.")
            return True
        else:
            self.send_message(f"❌ Publish failed for `{name}`: {pub.text}")
            return False

    def run(self):
        start_time = datetime.now(self.ist).strftime('%Y-%m-%d %H:%M:%S')
        self.send_message(f"📡 Run started at {start_time}")

        try:
            if not os.path.exists(self.daily_log_file):
                with open(self.daily_log_file, 'w') as f:
                    json.dump({"date": "", "count": 0}, f)

            count = self.read_log()
            if count >= self.max_posts_per_day:
                self.send_message(f"🚫 Daily limit reached: {count}/{self.max_posts_per_day}")
                return

            token = self.refresh_dropbox_token()
            if not token:
                self.send_message("❌ Dropbox token refresh failed.")
                return

            dbx = dropbox.Dropbox(oauth2_access_token=token)
            files = self.list_dropbox_files(dbx)

            self.send_message(f"📁 Dropbox files available: {len(files)}")

            if not files:
                self.send_message("📭 No media files to post.")
                return

            if self.post_to_instagram(dbx, files[0]):
                self.update_log(count + 1)

            # Show remaining files after post
            remaining = self.list_dropbox_files(dbx)
            self.send_message(f"📦 Files remaining in Dropbox: {len(remaining)}")

        except Exception as e:
            self.send_message(f"❌ Script error: {e}")

if __name__ == "__main__":
    DropboxToInstagramUploader().run()

# File: INK_WISPS_post.py
import os
import time
import json
import logging
import requests
import dropbox
from telegram import Bot
from datetime import datetime, timedelta
from pytz import timezone, utc

class DropboxToInstagramUploader:
    DROPBOX_TOKEN_URL = "https://api.dropbox.com/oauth2/token"
    INSTAGRAM_API_BASE = "https://graph.facebook.com/v18.0"
    INSTAGRAM_REEL_STATUS_RETRIES = 20
    INSTAGRAM_REEL_STATUS_WAIT_TIME = 5

    def __init__(self):
        self.script_name = "ink_wisps_post.py"
        self.ist = timezone('Asia/Kolkata')
        self.account_key = "eclipsed_by_you"
        self.schedule_file = "scheduler/config.json"

        # Logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.StreamHandler()]
        )
        self.logger = logging.getLogger()

        # Secrets from GitHub environment
        self.meta_token = os.getenv("META_TOKEN")
        self.ig_id = os.getenv("IG_ID")
        self.fb_page_id = os.getenv("FB_PAGE_ID")
        
        # Telegram configuration
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

        self.dropbox_key = os.getenv("DROPBOX_APP_KEY")
        self.dropbox_secret = os.getenv("DROPBOX_APP_SECRET")
        self.dropbox_refresh = os.getenv("DROPBOX_REFRESH_TOKEN")

        self.dropbox_folder = "/eclipsed_by_you"
        if self.telegram_token:
            self.telegram_bot = Bot(token=self.telegram_token)
        else:
            self.telegram_bot = None

        self.start_time = time.time()

    def send_message(self, msg, level=logging.INFO, telegram_only=False):
        """Send message to Telegram and optionally log to console."""
        prefix = f"[{self.script_name}]\n"
        full_msg = prefix + msg
        
        # Always send to Telegram if configured
        if self.telegram_bot and self.telegram_chat_id:
            try:
                self.telegram_bot.send_message(chat_id=self.telegram_chat_id, text=full_msg)
            except Exception as e:
                self.logger.error(f"Telegram send error: {e}")
        
        # Only log to console if not telegram_only
        if not telegram_only:
            if level == logging.ERROR:
                self.logger.error(full_msg)
            else:
                self.logger.info(full_msg)

    def refresh_dropbox_token(self):
        self.logger.info("Refreshing Dropbox token...")
        data = {
            "grant_type": "refresh_token",
            "refresh_token": self.dropbox_refresh,
            "client_id": self.dropbox_key,
            "client_secret": self.dropbox_secret,
        }
        r = requests.post(self.DROPBOX_TOKEN_URL, data=data)
        if r.status_code == 200:
            new_token = r.json().get("access_token")
            self.logger.info("Dropbox token refreshed.")
            return new_token
        else:
            self.send_message("❌ Dropbox refresh failed: " + r.text)
            raise Exception("Dropbox refresh failed.")

    def list_dropbox_files(self, dbx):
        try:
            files = dbx.files_list_folder(self.dropbox_folder).entries
            valid_exts = ('.mp4', '.mov', '.jpg', '.jpeg', '.png')
            return [f for f in files if f.name.lower().endswith(valid_exts)]
        except Exception as e:
            self.send_message(f"❌ Dropbox folder read failed: {e}", level=logging.ERROR)
            return []

    def get_caption_from_config(self):
        try:
            with open(self.schedule_file, 'r') as f:
                config = json.load(f)
            
            # Get today's caption from config
            today = datetime.now(self.ist).strftime("%A")
            day_config = config.get(self.account_key, {}).get(today, {})
            
            caption = day_config.get("caption", "✨ #eclipsed_by_you ✨")
            description = day_config.get("description", caption)  # Fallback to caption if missing
            
            if not caption:
                self.send_message("⚠️ No caption found in config for today", level=logging.WARNING)
            
            return caption, description
        except Exception as e:
            self.send_message(f"❌ Failed to read caption/description from config: {e}", level=logging.ERROR)
            return "✨ #eclipsed_by_you ✨", "✨ #eclipsed_by_you ✨"

    def post_to_instagram(self, dbx, file, caption, description):
        name = file.name
        ext = name.lower()
        media_type = "REELS" if ext.endswith((".mp4", ".mov")) else "IMAGE"

        temp_link = dbx.files_get_temporary_link(file.path_lower).link
        file_size = f"{file.size / 1024 / 1024:.2f}MB"
        total_files = len(self.list_dropbox_files(dbx))

        self.send_message(f"🚀 Uploading: {name}\n📂 Type: {media_type}\n📐 Size: {file_size}\n📦 Remaining: {total_files}")

        upload_url = f"{self.INSTAGRAM_API_BASE}/{self.ig_id}/media"
        data = {
            "access_token": self.meta_token,
            "caption": caption
        }

        if media_type == "REELS":
            data.update({"media_type": "REELS", "video_url": temp_link, "share_to_feed": "true"})
        else:
            data["image_url"] = temp_link

        res = requests.post(upload_url, data=data)
        if res.status_code != 200:
            err = res.json().get("error", {}).get("message", "Unknown")
            code = res.json().get("error", {}).get("code", "N/A")
            self.send_message(f"❌ Failed: {name}\n🧾 Error: {err}\n🪪 Code: {code}", level=logging.ERROR)
            return False

        creation_id = res.json().get("id")
        if not creation_id:
            self.send_message(f"❌ No media ID returned for: {name}", level=logging.ERROR)
            return False, media_type

        if media_type == "REELS":
            for _ in range(self.INSTAGRAM_REEL_STATUS_RETRIES):
                status = requests.get(
                    f"{self.INSTAGRAM_API_BASE}/{creation_id}?fields=status_code&access_token={self.meta_token}"
                ).json()
                if status.get("status_code") == "FINISHED":
                    break
                elif status.get("status_code") == "ERROR":
                    self.send_message(f"❌ IG processing failed: {name}", level=logging.ERROR)
                    return False
                time.sleep(self.INSTAGRAM_REEL_STATUS_WAIT_TIME)

        publish_url = f"{self.INSTAGRAM_API_BASE}/{self.ig_id}/media_publish"
        pub = requests.post(publish_url, data={"creation_id": creation_id, "access_token": self.meta_token})
        if pub.status_code == 200:
            self.send_message(f"✅ Uploaded: {name}\n📦 Files left: {total_files - 1}")
            
            # Also post to Facebook Page if it's a REEL
            if media_type == "REELS":
                self.post_to_facebook_page(temp_link, description)
            
            # Removed file deletion from here
            return True, media_type
        else:
            self.send_message(f"❌ Publish failed: {name}\n{pub.text}", level=logging.ERROR)
            return False, media_type

    def verify_facebook_post(self, video_id):
        """Verify Facebook post is actually published using video_id."""
        self.send_message("🔄 Verifying Facebook post is live...")
        
        for attempt in range(3):
            time.sleep(3)
            check_url = f"https://graph.facebook.com/v18.0/{video_id}"
            params = {
                "fields": "id,description,created_time,permalink_url",
                "access_token": self.meta_token
            }
            
            try:
                res = requests.get(check_url, params=params)
                
                if res.status_code == 200:
                    data = res.json()
                    if "id" in data:
                        permalink = data.get('permalink_url', 'N/A')
                        self.send_message(f"✅ Verified Facebook post is live!\n📎 Link: {permalink}")
                        return True
                    else:
                        self.send_message(f"⏳ Facebook post not fully published yet. Attempt {attempt + 1}/3...")
                else:
                    self.send_message(f"⏳ Facebook post not visible yet. Attempt {attempt + 1}/3...")
                    
            except Exception as e:
                self.send_message(f"⚠️ Error checking Facebook post: {e}")
                
        self.send_message("❌ Facebook post could not be confirmed after polling.", level=logging.ERROR)
        return False

    def post_to_facebook_page(self, video_url, caption):
        """Post to Facebook Page with verification."""
        if not self.fb_page_id:
            self.send_message("⚠️ Facebook Page ID not configured, skipping Facebook post", level=logging.WARNING)
            return False
            
        # Get page token using the meta token directly
        page_token = self.meta_token
        if not page_token:
            self.send_message("❌ Could not retrieve Facebook Page access token.", level=logging.ERROR)
            return False

        post_url = f"https://graph.facebook.com/{self.fb_page_id}/videos"
        data = {
            "access_token": page_token,
            "file_url": video_url,
            "description": caption
        }
        
        try:
            res = requests.post(post_url, data=data)
            
            if res.status_code == 200:
                response_data = res.json()
                video_id = response_data.get("id", "Unknown")
                self.send_message(f"✅ Facebook Page post API call successful! Video ID: {video_id}")
                
                # Verify the post is actually live
                if video_id and video_id != "Unknown":
                    return self.verify_facebook_post(video_id)
                else:
                    self.send_message("⚠️ Facebook post succeeded but no video ID returned", level=logging.WARNING)
                    return True  # Still consider it successful
            else:
                error_msg = res.json().get("error", {}).get("message", "Unknown error")
                error_code = res.json().get("error", {}).get("code", "N/A")
                self.send_message(f"❌ Facebook Page upload failed:\nError: {error_msg} | Code: {error_code}", level=logging.ERROR)
                return False
        except Exception as e:
            self.send_message(f"❌ Facebook Page upload exception: {str(e)}", level=logging.ERROR)
            return False

    def authenticate_dropbox(self):
        """Authenticate with Dropbox and return the client."""
        try:
            access_token = self.refresh_dropbox_token()
            return dropbox.Dropbox(oauth2_access_token=access_token)
        except Exception as e:
            self.send_message(f"❌ Dropbox authentication failed: {str(e)}", level=logging.ERROR)
            raise

    def process_files_with_retries(self, dbx, caption, description, max_retries=3):
        files = self.list_dropbox_files(dbx)
        if not files:
            self.send_message("📭 No files found in Dropbox folder.", level=logging.INFO)
            return False

        attempts = 0
        for file in files[:max_retries]:
            attempts += 1
            self.send_message(f"🎯 Attempt {attempts}/{max_retries} — Trying: {file.name}", level=logging.INFO)
            try:
                result = self.post_to_instagram(dbx, file, caption, description)
                if isinstance(result, tuple):
                    success, media_type = result
                else:
                    success = result
                    media_type = None
            except Exception as e:
                self.send_message(f"❌ Exception during post for {file.name}: {e}", level=logging.ERROR)
                success = False
                media_type = None

            # Always delete the file after an attempt
            try:
                dbx.files_delete_v2(file.path_lower)
                self.send_message(f"🗑️ Deleted file after attempt: {file.name}")
            except Exception as e:
                self.send_message(f"⚠️ Failed to delete file {file.name}: {e}", level=logging.WARNING)

            if success:
                if media_type == "REELS":
                    self.send_message("✅ Successfully posted one reel", level=logging.INFO)
                elif media_type == "IMAGE":
                    self.send_message("✅ Successfully posted one image", level=logging.INFO)
                else:
                    self.send_message("✅ Successfully posted", level=logging.INFO)
                return True  # Exit after successful post

        self.send_message("❌ All attempts failed. Exiting after 3 tries.", level=logging.ERROR)
        return False

    def verify_instagram_post_by_creation_id(self, creation_id):
        """Verify Instagram post is actually published using creation_id polling."""
        self.send_message("🔄 Verifying Instagram post is live using creation_id...")
        
        for attempt in range(5):
            time.sleep(5)
            check_url = f"https://graph.facebook.com/v18.0/{creation_id}"
            params = {
                "fields": "id,permalink,media_type,media_url,caption,timestamp",
                "access_token": self.meta_token
            }
            
            try:
                res = requests.get(check_url, params=params)
                
                if res.status_code == 200:
                    data = res.json()
                    if "id" in data:
                        permalink = data.get('permalink', 'N/A')
                        self.send_message(f"✅ Verified Instagram post is live!\n📎 Link: {permalink}")
                        return True
                    else:
                        self.send_message(f"⏳ Instagram post not fully published yet. Attempt {attempt + 1}/5...")
                else:
                    self.send_message(f"⏳ Instagram post not visible yet. Attempt {attempt + 1}/5...")
                    
            except Exception as e:
                self.send_message(f"⚠️ Error checking Instagram post: {e}")
                
        self.send_message("❌ Instagram post could not be confirmed after polling.", level=logging.ERROR)
        return False

    def upload_instagram_reel_or_image(self, video_or_image_url, caption, media_type):
        """Upload to Instagram with proper error handling and creation_id verification."""
        try:
            data = {
                "access_token": self.meta_token,
                "caption": caption
            }
            if media_type == "REELS":
                data.update({
                    "media_type": "REELS",
                    "video_url": video_or_image_url,
                    "share_to_feed": "true"
                })
            else:
                data["image_url"] = video_or_image_url

            res = requests.post(f"{self.INSTAGRAM_API_BASE}/{self.ig_id}/media", data=data)
            if res.status_code != 200:
                self.send_message(f"❌ Media creation failed: {res.text}", level=logging.ERROR)
                return False

            creation_id = res.json().get("id")
            if not creation_id:
                self.send_message(f"❌ No creation_id returned from IG", level=logging.ERROR)
                return False

            self.send_message(f"✅ Instagram media creation successful! Creation ID: {creation_id}")

            if media_type == "REELS":
                # Polling to ensure processing finishes
                self.send_message("⏳ Processing video for Instagram...")
                for _ in range(self.INSTAGRAM_REEL_STATUS_RETRIES):
                    status = requests.get(
                        f"{self.INSTAGRAM_API_BASE}/{creation_id}?fields=status_code&access_token={self.meta_token}"
                    ).json()
                    if status.get("status_code") == "FINISHED":
                        self.send_message("✅ Instagram video processing completed!")
                        break
                    elif status.get("status_code") == "ERROR":
                        self.send_message("❌ Reel processing failed.", level=logging.ERROR)
                        return False
                    time.sleep(self.INSTAGRAM_REEL_STATUS_WAIT_TIME)

            # Try to publish - but don't trust the response completely
            publish_url = f"{self.INSTAGRAM_API_BASE}/{self.ig_id}/media_publish"
            publish_data = {
                "creation_id": creation_id,
                "access_token": self.meta_token
            }
        
            publish_res = requests.post(publish_url, data=publish_data)
            
            if publish_res.status_code == 200:
                self.send_message("✅ Instagram publish API call successful!")
                # Still verify using creation_id
                return self.verify_instagram_post_by_creation_id(creation_id)
            else:
                # Even if publish API fails, verify using creation_id
                self.send_message(f"⚠️ Instagram publish API failed, but checking if post was published anyway...", level=logging.WARNING)
                return self.verify_instagram_post_by_creation_id(creation_id)
                
        except Exception as e:
            self.send_message(f"❌ Exception during IG upload: {e}", level=logging.ERROR)
            return False

    def run(self):
        """Main execution method that orchestrates the posting process."""
        self.send_message(f"📡 Run started at: {datetime.now(self.ist).strftime('%Y-%m-%d %H:%M:%S')}", level=logging.INFO)
        
        try:
            # Get caption from config
            caption, description = self.get_caption_from_config()
            
            # Authenticate with Dropbox
            dbx = self.authenticate_dropbox()
            
            # Try posting up to 3 times
            self.process_files_with_retries(dbx, caption, description, max_retries=3)
            
        except Exception as e:
            self.send_message(f"❌ Script crashed:\n{str(e)}", level=logging.ERROR)
            raise
        finally:
            duration = time.time() - self.start_time
            self.send_message(f"🏁 Run complete in {duration:.1f} seconds", level=logging.INFO)

if __name__ == "__main__":
    DropboxToInstagramUploader().run()

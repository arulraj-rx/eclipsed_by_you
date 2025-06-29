# File: INK_WISPS_post.py
import os
import time
import json
import logging
import requests
import dropbox
import threading
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

        # Logging - reduced verbosity
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

    def get_page_access_token(self):
        """Fetch short-lived Page Access Token from long-lived user token."""
        try:
            url = f"https://graph.facebook.com/v18.0/me/accounts"
            params = {"access_token": self.meta_token}
            
            res = requests.get(url, params=params)
            if res.status_code != 200:
                self.send_message(f"❌ Failed to fetch Page token: {res.text}", level=logging.ERROR)
                return None

            pages = res.json().get("data", [])
            
            for page in pages:
                page_id = page.get("id", "Unknown")
                page_name = page.get("name", "Unknown")
                
                if page_id == self.fb_page_id:
                    page_token = self.exchange_user_token_for_page_token(page_id)
                    if page_token:
                        self.send_message(f"✅ Page Access Token fetched for: {page_name}")
                    return page_token

            self.send_message(f"⚠️ Page ID {self.fb_page_id} not found in user's account.", level=logging.WARNING)
            return None
        except Exception as e:
            self.send_message(f"❌ Exception during Page token fetch: {e}", level=logging.ERROR)
            return None

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
            
            return caption, description
        except Exception as e:
            self.send_message(f"❌ Failed to read caption/description from config: {e}", level=logging.ERROR)
            return "✨ #eclipsed_by_you ✨", "✨ #eclipsed_by_you ✨"

    def parallel_post(self, dbx, file, caption, description):
        """Post to Instagram and Facebook concurrently, delete only if both succeed."""
        name = file.name
        ext = name.lower()
        media_type = "REELS" if ext.endswith((".mp4", ".mov")) else "IMAGE"
        
        self.send_message(f"🚀 Starting parallel upload for: {name}")
        
        temp_link = dbx.files_get_temporary_link(file.path_lower).link
        file_size = f"{file.size / 1024 / 1024:.2f}MB"
        total_files = len(self.list_dropbox_files(dbx))

        self.send_message(f"📸 Type: {media_type} | Size: {file_size} | Remaining: {total_files}")

        # Shared results object with thread lock
        results = {"instagram": None, "facebook": None, "media_type": media_type}
        lock = threading.Lock()

        def post_instagram():
            """Post to Instagram only."""
            try:
                success, media_type = self.post_to_instagram_only(dbx, file, caption, temp_link)
                with lock:
                    results["instagram"] = success
                    results["media_type"] = media_type
                if success:
                    self.send_message("✅ Instagram upload completed successfully")
                else:
                    self.send_message("❌ Instagram upload failed")
            except Exception as e:
                self.send_message(f"❌ Instagram upload exception: {e}", level=logging.ERROR)
                with lock:
                    results["instagram"] = False

        def post_facebook():
            """Post to Facebook only."""
            try:
                success = self.post_to_facebook_page_only(temp_link, description)
                with lock:
                    results["facebook"] = success
                if success:
                    self.send_message("✅ Facebook upload completed successfully")
                else:
                    self.send_message("❌ Facebook upload failed")
            except Exception as e:
                self.send_message(f"❌ Facebook upload exception: {e}", level=logging.ERROR)
                with lock:
                    results["facebook"] = False

        # Start both threads
        t1 = threading.Thread(target=post_instagram)
        t2 = threading.Thread(target=post_facebook)

        t1.start()
        t2.start()

        # Wait for both to complete
        t1.join()
        t2.join()

        # Check results and handle file deletion
        instagram_success = results["instagram"]
        facebook_success = results["facebook"]
        media_type = results["media_type"]

        if instagram_success and facebook_success:
            # Both succeeded - delete file
            try:
                dbx.files_delete_v2(file.path_lower)
                self.send_message(f"🗑️ Deleted file after successful posts: {file.name}")
            except Exception as e:
                self.send_message(f"⚠️ Failed to delete file {file.name}: {e}", level=logging.WARNING)
            self.send_message("🎉 Successfully posted to both Instagram and Facebook!")
            return True, media_type
        else:
            # One or both failed - don't delete file
            if not instagram_success and not facebook_success:
                self.send_message("❌ Both Instagram and Facebook uploads failed. File not deleted.")
            elif not instagram_success:
                self.send_message("❌ Instagram failed, Facebook succeeded. File not deleted.")
            else:
                self.send_message("❌ Facebook failed, Instagram succeeded. File not deleted.")
            return False, media_type

    def post_to_instagram_only(self, dbx, file, caption, temp_link):
        """Post to Instagram only - isolated logic without Facebook or file deletion."""
        name = file.name
        ext = name.lower()
        media_type = "REELS" if ext.endswith((".mp4", ".mov")) else "IMAGE"

        # Get Facebook page access token
        page_token = self.get_page_access_token()
        if not page_token:
            self.send_message("❌ Could not retrieve Facebook Page access token.", level=logging.ERROR)
            return False, media_type

        # Create media on Instagram
        upload_url = f"{self.INSTAGRAM_API_BASE}/{self.ig_id}/media"
        data = {
            "access_token": page_token,
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
            self.send_message(f"❌ Instagram upload failed: {name}\nError: {err} | Code: {code}", level=logging.ERROR)
            return False, media_type

        creation_id = res.json().get("id")
        if not creation_id:
            self.send_message(f"❌ No media ID returned for: {name}", level=logging.ERROR)
            return False, media_type

        self.send_message(f"✅ Instagram media creation successful! ID: {creation_id}")

        # Process video if it's a reel
        if media_type == "REELS":
            self.send_message("⏳ Processing video for Instagram...")
            for attempt in range(self.INSTAGRAM_REEL_STATUS_RETRIES):
                status_response = requests.get(
                    f"{self.INSTAGRAM_API_BASE}/{creation_id}?fields=status_code&access_token={page_token}"
                )
                
                if status_response.status_code != 200:
                    self.send_message(f"❌ Status check failed: {status_response.status_code}", level=logging.ERROR)
                    return False, media_type
                
                status = status_response.json()
                current_status = status.get("status_code", "UNKNOWN")
                
                if current_status == "FINISHED":
                    self.send_message("✅ Instagram video processing completed!")
                    break
                elif current_status == "ERROR":
                    self.send_message(f"❌ Instagram processing failed: {name}", level=logging.ERROR)
                    return False, media_type
                
                time.sleep(self.INSTAGRAM_REEL_STATUS_WAIT_TIME)

        # Publish to Instagram
        publish_url = f"{self.INSTAGRAM_API_BASE}/{self.ig_id}/media_publish"
        publish_data = {"creation_id": creation_id, "access_token": page_token}
        
        pub = requests.post(publish_url, data=publish_data)
        
        if pub.status_code == 200:
            response_data = pub.json()
            instagram_id = response_data.get("id", "Unknown")
            self.send_message(f"✅ Instagram post published! Media ID: {instagram_id}")
            return True, media_type
        else:
            error_msg = pub.json().get("error", {}).get("message", "Unknown error")
            error_code = pub.json().get("error", {}).get("code", "N/A")
            self.send_message(f"❌ Instagram publish failed: {name}\nError: {error_msg} | Code: {error_code}", level=logging.ERROR)
            return False, media_type

    def post_to_facebook_page_only(self, video_url, caption):
        """Post to Facebook Page only - isolated logic."""
        if not self.fb_page_id:
            self.send_message("⚠️ Facebook Page ID not configured, skipping Facebook post", level=logging.WARNING)
            return False
            
        # Get page token
        page_token = self.get_page_access_token()
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
                self.send_message(f"✅ Facebook Page post published! Video ID: {video_id}")
                return True
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

    def process_files(self, dbx, caption, description):
        """Process one file - no retry logic, one post per run."""
        files = self.list_dropbox_files(dbx)
        if not files:
            self.send_message("📭 No files found in Dropbox folder.")
            return False

        # Take only the first file
        file = files[0]
        self.send_message(f"🎯 Processing file: {file.name}")
        
        try:
            success, media_type = self.parallel_post(dbx, file, caption, description)
            return success
        except Exception as e:
            self.send_message(f"❌ Exception during post for {file.name}: {e}", level=logging.ERROR)
            return False

    def run(self):
        """Main execution method that orchestrates the posting process."""
        self.send_message(f"📡 Run started at: {datetime.now(self.ist).strftime('%Y-%m-%d %H:%M:%S')}")
        
        try:
            # Check token expiry first
            token_valid = self.check_token_expiry()
            if not token_valid:
                self.send_message("❌ Token validation failed. Stopping execution.", level=logging.ERROR)
                return
            
            # Get caption from config
            caption, description = self.get_caption_from_config()
            
            # Authenticate with Dropbox
            dbx = self.authenticate_dropbox()
            
            # Process one file only
            success = self.process_files(dbx, caption, description)
            
            if success:
                self.send_message("🎉 Publishing completed successfully!")
            else:
                self.send_message("❌ Publishing failed.", level=logging.ERROR)
            
        except Exception as e:
            self.send_message(f"❌ Script crashed: {str(e)}", level=logging.ERROR)
            raise
        finally:
            duration = time.time() - self.start_time
            self.send_message(f"🏁 Run complete in {duration:.1f} seconds")

    def check_token_expiry(self):
        """Check Meta token expiry and send Telegram notification."""
        try:
            check_url = f"https://graph.facebook.com/debug_token"
            params = {
                "input_token": self.meta_token,
                "access_token": self.meta_token
            }
            
            response = requests.get(check_url, params=params)
            
            if response.status_code == 200:
                fb_response = response.json()
                
                if 'data' in fb_response and 'is_valid' in fb_response['data']:
                    data = fb_response['data']
                    is_valid = data['is_valid']
                    expires_at = data.get('expires_at', 0)

                    if expires_at:
                        expiry_date = datetime.utcfromtimestamp(expires_at).strftime('%Y-%m-%d %H:%M:%S UTC')
                    else:
                        expiry_date = 'Never (Long-Lived Token or Page Token)'

                    message = f"🔐 Token Valid: {is_valid}\n⏳ Expires: {expiry_date}"
                    
                    if not is_valid:
                        message += "\n⚠️ WARNING: Token is invalid!"
                        self.send_message(message, level=logging.ERROR)
                    else:
                        self.send_message(message)
                        
                    return is_valid
                else:
                    message = f"❌ Error validating token:\n{fb_response}"
                    self.send_message(message, level=logging.ERROR)
                    return False
            else:
                message = f"❌ Failed to check token: {response.status_code} - {response.text}"
                self.send_message(message, level=logging.ERROR)
                return False
                
        except Exception as e:
            message = f"❌ Exception checking token: {str(e)}"
            self.send_message(message, level=logging.ERROR)
            return False

    def exchange_user_token_for_page_token(self, page_id):
        """Exchange user access token for page access token."""
        try:
            url = f"https://graph.facebook.com/v18.0/{page_id}"
            params = {
                "fields": "access_token",
                "access_token": self.meta_token
            }
            
            res = requests.get(url, params=params)
            
            if res.status_code == 200:
                response_data = res.json()
                page_token = response_data.get("access_token")
                
                if page_token:
                    return page_token
                else:
                    self.send_message("❌ No access_token in response", level=logging.ERROR)
                    return None
            else:
                self.send_message(f"❌ Token exchange failed: {res.text}", level=logging.ERROR)
                return None
                
        except Exception as e:
            self.send_message(f"❌ Exception during token exchange: {e}", level=logging.ERROR)
            return None

if __name__ == "__main__":
    DropboxToInstagramUploader().run()

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

    def send_message(self, msg, level=logging.INFO):
        prefix = f"[{self.script_name}]\n"
        full_msg = prefix + msg
        try:
            if self.telegram_bot and self.telegram_chat_id:
                self.telegram_bot.send_message(chat_id=self.telegram_chat_id, text=full_msg) 
            # Also log the message to console with the specified level
            if level == logging.ERROR:
                self.logger.error(full_msg)
            else:
                self.logger.info(full_msg)
        except Exception as e:
            self.logger.error(f"Telegram send error for message '{full_msg}': {e}")

    def send_token_expiry_info(self):
        try:
            debug_url = f"https://graph.facebook.com/debug_token"
            params = {
                "input_token": self.meta_token,
                "access_token": self.meta_token  # Use the same token for debugging
            }
            res = requests.get(debug_url, params=params)
            data = res.json().get("data", {})
            exp_timestamp = data.get("expires_at")

            if not exp_timestamp:
                self.send_message("⚠️ Could not retrieve token expiry info", level=logging.WARNING)
                return

            expiry_dt = datetime.fromtimestamp(exp_timestamp, tz=self.ist)
            now = datetime.now(self.ist)
            time_left = expiry_dt - now

            days = time_left.days
            hours, remainder = divmod(time_left.seconds, 3600)
            minutes = remainder // 60

            message = (
                f"🔐 Meta Token Expiry Info:\n"
                f"📅 Expires on: {expiry_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
                f"⏳ Time left: {days} days, {hours} hours, {minutes} minutes"
            )
            self.send_message(message, level=logging.INFO)

        except Exception as e:
            self.send_message(f"❌ Failed to get token expiry info: {e}", level=logging.ERROR)

    def get_page_access_token(self):
        """Fetch short-lived Page Access Token from long-lived user token."""
        try:
            self.send_message("🔐 Fetching Page Access Token from Meta API...", level=logging.INFO)
            url = f"https://graph.facebook.com/v18.0/me/accounts"
            params = {"access_token": self.meta_token}
            
            self.send_message(f"📡 API URL: {url}", level=logging.INFO)
            
            start_time = time.time()
            res = requests.get(url, params=params)
            request_time = time.time() - start_time
            
            self.send_message(f"⏱️ Page token request completed in {request_time:.2f} seconds", level=logging.INFO)
            self.send_message(f"📊 Response status: {res.status_code}", level=logging.INFO)

            if res.status_code != 200:
                self.send_message(f"❌ Failed to fetch Page token: {res.text}", level=logging.ERROR)
                return None

            pages = res.json().get("data", [])
            self.send_message(f"🔍 Found {len(pages)} pages in user account", level=logging.INFO)
            
            for i, page in enumerate(pages):
                page_id = page.get("id", "Unknown")
                page_name = page.get("name", "Unknown")
                self.send_message(f"📋 Page {i+1}: {page_name} (ID: {page_id})", level=logging.INFO)
                
                if page_id == self.fb_page_id:
                    token = page.get("access_token")
                    self.send_message(f"✅ Page Access Token fetched successfully for: {page_name} (ID: {self.fb_page_id})")
                    return token

            self.send_message(f"⚠️ Page ID {self.fb_page_id} not found in user's account list.", level=logging.WARNING)
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

        self.send_message(f"🚀 Starting upload process for: {name}", level=logging.INFO)
        
        temp_link = dbx.files_get_temporary_link(file.path_lower).link
        file_size = f"{file.size / 1024 / 1024:.2f}MB"
        total_files = len(self.list_dropbox_files(dbx))

        self.send_message(f"📸 Instagram upload details:\n📂 Type: {media_type}\n📐 Size: {file_size}\n📦 Remaining: {total_files}")

        # Get page access token for Instagram upload
        self.send_message("🔐 Step 1: Retrieving Page Access Token...", level=logging.INFO)
        page_token = self.get_page_access_token()
        if not page_token:
            self.send_message("❌ Could not retrieve Page access token. Aborting Instagram upload.", level=logging.ERROR)
            return False

        self.send_message("✅ Page Access Token retrieved successfully", level=logging.INFO)

        upload_url = f"{self.INSTAGRAM_API_BASE}/{self.ig_id}/media"
        data = {
            "access_token": page_token,
            "caption": caption
        }

        if media_type == "REELS":
            data.update({"media_type": "REELS", "video_url": temp_link, "share_to_feed": "true"})
        else:
            data["image_url"] = temp_link

        self.send_message("🔄 Step 2: Sending media creation request to Instagram API...", level=logging.INFO)
        self.send_message(f"📡 API URL: {upload_url}", level=logging.INFO)
        
        start_time = time.time()
        res = requests.post(upload_url, data=data)
        request_time = time.time() - start_time
        
        self.send_message(f"⏱️ API request completed in {request_time:.2f} seconds", level=logging.INFO)
        self.send_message(f"📊 Response status: {res.status_code}", level=logging.INFO)
        
        if res.status_code != 200:
            err = res.json().get("error", {}).get("message", "Unknown")
            code = res.json().get("error", {}).get("code", "N/A")
            self.send_message(f"❌ Instagram upload failed: {name}\n📸 Error: {err}\n📸 Code: {code}\n📸 Status: {res.status_code}", level=logging.ERROR)
            return False

        creation_id = res.json().get("id")
        if not creation_id:
            self.send_message(f"❌ No media ID returned for: {name}", level=logging.ERROR)
            return False, media_type

        self.send_message(f"✅ Media creation successful! Creation ID: {creation_id}", level=logging.INFO)

        if media_type == "REELS":
            self.send_message("⏳ Step 3: Processing video for Instagram...", level=logging.INFO)
            processing_start = time.time()
            for attempt in range(self.INSTAGRAM_REEL_STATUS_RETRIES):
                self.send_message(f"🔄 Processing attempt {attempt + 1}/{self.INSTAGRAM_REEL_STATUS_RETRIES}", level=logging.INFO)
                
                status_response = requests.get(
                    f"{self.INSTAGRAM_API_BASE}/{creation_id}?fields=status_code&access_token={page_token}"
                )
                
                if status_response.status_code != 200:
                    self.send_message(f"❌ Status check failed: {status_response.status_code}", level=logging.ERROR)
                    return False
                
                status = status_response.json()
                current_status = status.get("status_code", "UNKNOWN")
                
                self.send_message(f"📊 Current status: {current_status}", level=logging.INFO)
                
                if current_status == "FINISHED":
                    processing_time = time.time() - processing_start
                    self.send_message(f"✅ Instagram video processing completed in {processing_time:.2f} seconds!", level=logging.INFO)
                    break
                elif current_status == "ERROR":
                    self.send_message(f"❌ Instagram processing failed: {name}\n📸 Status: ERROR", level=logging.ERROR)
                    return False
                
                self.send_message(f"⏳ Waiting {self.INSTAGRAM_REEL_STATUS_WAIT_TIME} seconds before next check...", level=logging.INFO)
                time.sleep(self.INSTAGRAM_REEL_STATUS_WAIT_TIME)

        self.send_message("📤 Step 4: Publishing to Instagram...", level=logging.INFO)
        publish_url = f"{self.INSTAGRAM_API_BASE}/{self.ig_id}/media_publish"
        publish_data = {"creation_id": creation_id, "access_token": page_token}
        
        self.send_message(f"📡 Publishing to: {publish_url}", level=logging.INFO)
        
        publish_start = time.time()
        pub = requests.post(publish_url, data=publish_data)
        publish_time = time.time() - publish_start
        
        self.send_message(f"⏱️ Publish request completed in {publish_time:.2f} seconds", level=logging.INFO)
        self.send_message(f"📊 Publish response status: {pub.status_code}", level=logging.INFO)
        
        if pub.status_code == 200:
            response_data = pub.json()
            instagram_id = response_data.get("id", "Unknown")
            self.send_message(f"✅ Instagram post published successfully!\n📸 Media ID: {instagram_id}\n📸 Account ID: {self.ig_id}\n📦 Files left: {total_files - 1}")
            
            # Also post to Facebook Page if it's a REEL
            if media_type == "REELS":
                self.send_message("📘 Step 5: Starting Facebook Page upload...", level=logging.INFO)
                self.post_to_facebook_page(temp_link, description)
            
            # Removed file deletion from here
            return True, media_type
        else:
            error_msg = pub.json().get("error", {}).get("message", "Unknown error")
            error_code = pub.json().get("error", {}).get("code", "N/A")
            self.send_message(f"❌ Instagram publish failed: {name}\n📸 Error: {error_msg}\n📸 Code: {error_code}\n📸 Status: {pub.status_code}", level=logging.ERROR)
            return False, media_type

    def post_to_facebook_page(self, video_url, caption):
        """Publish the Reel video also to the Facebook Page."""
        if not self.fb_page_id:
            self.send_message("⚠️ Facebook Page ID not configured, skipping Facebook post", level=logging.WARNING)
            return False
            
        self.send_message("📘 Starting Facebook Page upload...", level=logging.INFO)
        
        page_token = self.get_page_access_token()
        if not page_token:
            self.send_message("❌ Could not retrieve Page access token. Aborting Facebook upload.", level=logging.ERROR)
            return False

        self.send_message("🔐 Using Page Access Token for Facebook upload", level=logging.INFO)

        post_url = f"https://graph.facebook.com/{self.fb_page_id}/videos"
        data = {
            "access_token": page_token,
            "file_url": video_url,
            "description": caption
        }
        
        try:
            self.send_message("🔄 Sending request to Facebook API...", level=logging.INFO)
            res = requests.post(post_url, data=data)
            
            if res.status_code == 200:
                response_data = res.json()
                video_id = response_data.get("id", "Unknown")
                self.send_message(f"✅ Facebook Page post published successfully!\n📘 Video ID: {video_id}\n📘 Page ID: {self.fb_page_id}")
                return True
            else:
                error_msg = res.json().get("error", {}).get("message", "Unknown error")
                error_code = res.json().get("error", {}).get("code", "N/A")
                self.send_message(f"❌ Facebook Page upload failed:\n📘 Error: {error_msg}\n📘 Code: {error_code}\n📘 Status: {res.status_code}", level=logging.ERROR)
                return False
        except Exception as e:
            self.send_message(f"❌ Facebook Page upload exception:\n📘 Error: {str(e)}", level=logging.ERROR)
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
                    self.send_message("✅ Successfully posted one reel to Instagram", level=logging.INFO)
                elif media_type == "IMAGE":
                    self.send_message("✅ Successfully posted one image to Instagram", level=logging.INFO)
                else:
                    self.send_message("✅ Successfully posted to Instagram", level=logging.INFO)
                return True  # Exit after successful post

        self.send_message("❌ All attempts failed. Exiting after 3 tries.", level=logging.ERROR)
        return False

    def run(self):
        """Main execution method that orchestrates the posting process."""
        self.send_message(f"📡 Run started at: {datetime.now(self.ist).strftime('%Y-%m-%d %H:%M:%S')}", level=logging.INFO)
        
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
            
            # Try posting up to 3 times
            success = self.process_files_with_retries(dbx, caption, description, max_retries=3)
            
            if success:
                self.send_message("🎉 All publishing completed successfully!", level=logging.INFO)
            else:
                self.send_message("❌ Publishing failed after all attempts.", level=logging.ERROR)
            
        except Exception as e:
            self.send_message(f"❌ Script crashed:\n{str(e)}", level=logging.ERROR)
            raise
        finally:
            # Send token expiry info before completion
            self.send_token_expiry_info()
            duration = time.time() - self.start_time
            self.send_message(f"🏁 Run complete in {duration:.1f} seconds", level=logging.INFO)

    def check_token_expiry(self):
        """Check Meta token expiry and send Telegram notification."""
        try:
            # Check token validity using Facebook Graph API
            check_url = f"https://graph.facebook.com/debug_token"
            params = {
                "input_token": self.meta_token,
                "access_token": self.meta_token
            }
            
            self.send_message("🔐 Checking Meta token validity...", level=logging.INFO)
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
                        self.send_message(message, level=logging.INFO)
                        
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

if __name__ == "__main__":
    DropboxToInstagramUploader().run()

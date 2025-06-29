import os
import time
import json
import logging
import random
import requests
from datetime import datetime, timedelta
from pytz import timezone
from telegram import Bot
import dropbox

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger()

class SocialMediaPoster:
    def __init__(self):
        self.ist = timezone('Asia/Kolkata')
        self.account_key = "eclipsed_by_you"
        self.schedule_file = "scheduler/config.json"
        
        # Script identifier for notifications
        self.script_name = "📱 eclipsed_by_you"
        
        # API Version
        self.meta_version = "v22.0"

        # Meta/Facebook configuration - Updated environment variable names
        self.meta_token = os.getenv("META_TOKEN")
        self.ig_id = os.getenv("IG_ID")
        self.fb_page_id = os.getenv("FB_PAGE_ID")
        self.ig_collab = os.getenv("IG_COLLABORATOR_ID", "").strip() or None
        self.fb_collabs = [c.strip() for c in os.getenv("FB_COLLABORATOR_IDS", "").split(",") if c.strip()]
        # Hardcoded share to feed - not in environment variables
        self.ig_share_feed = False  # Set to True if you want to share to feed

        # Telegram configuration
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

        # Dropbox configuration - Updated environment variable names
        self.dropbox_key = os.getenv("DROPBOX_APP_KEY")
        self.dropbox_secret = os.getenv("DROPBOX_APP_SECRET")
        self.dropbox_refresh = os.getenv("DROPBOX_REFRESH_TOKEN")
        self.dropbox_folder = "/eclipsed_by_you"

        # Constants
        self.poll_interval = 5
        self.max_files_to_check = 3  # Check up to 3 files for copyright issues
        self.max_retries = 3  # Retry attempts for API calls

        # Initialize bot
        if self.telegram_token:
            self.bot = Bot(token=self.telegram_token)
        else:
            self.bot = None
            logger.warning("Telegram token not provided, notifications disabled")

    def notify(self, msg, error=False):
        """Send notification to Telegram and log"""
        # Add script identifier to all messages
        full_message = f"{self.script_name}: {msg}"
        
        if self.bot and self.telegram_chat_id:
            try:
                self.bot.send_message(chat_id=self.telegram_chat_id, text=full_message)  # type: ignore
            except Exception as e:
                logger.error(f"Failed to send Telegram message: {e}")
        
        prefix = "❌" if error else "✅"
        log_level = logging.ERROR if error else logging.INFO
        logger.log(log_level, f"{prefix} {full_message}")

    def load_config(self):
        """Load configuration from scheduler config file"""
        try:
            self.notify("📋 Loading configuration from scheduler/config.json")
            with open(self.schedule_file, 'r') as f:
                config = json.load(f)
            config_data = config.get(self.account_key, {})
            self.notify(f"✅ Configuration loaded successfully")
            return config_data
        except FileNotFoundError:
            self.notify(f"⚠️ Config file {self.schedule_file} not found, using defaults", error=True)
            return {}
        except json.JSONDecodeError as e:
            self.notify(f"❌ Invalid JSON in config file: {e}", error=True)
            return {}

    def get_dropbox_client(self):
        """Get Dropbox client with refreshed access token"""
        try:
            self.notify("🔄 Refreshing Dropbox access token")
            resp = requests.post("https://api.dropbox.com/oauth2/token", data={
                "grant_type": "refresh_token",
                "refresh_token": self.dropbox_refresh,
                "client_id": self.dropbox_key,
                "client_secret": self.dropbox_secret,
            })
            resp.raise_for_status()
            self.notify("✅ Dropbox access token refreshed successfully")
            return dropbox.Dropbox(oauth2_access_token=resp.json()["access_token"])
        except Exception as e:
            self.notify(f"❌ Failed to get Dropbox access token: {e}", error=True)
            raise

    def check_token_expiry(self):
        """Check Meta token expiry via debug token endpoint"""
        try:
            self.notify("🔍 Checking Meta token expiry")
            debug_url = f"https://graph.facebook.com/{self.meta_version}/debug_token"
            params = {
                "input_token": self.meta_token,
                "access_token": self.meta_token  # Use the same token for debugging
            }
            r = requests.get(debug_url, params=params).json().get("data", {})
            
            if not r.get("is_valid"):
                self.notify("⚠️ Meta token invalid or expired", error=True)
                return False
            
            exp = r.get("expires_at")
            if exp:
                remaining = datetime.utcfromtimestamp(exp) - datetime.utcnow()
                days = remaining.days
                hours = int(remaining.total_seconds() // 3600)
                self.notify(f"🔐 Meta token expires in {days} days ({hours} hrs) on {datetime.utcfromtimestamp(exp):%Y-%m-%d %H:%M}")
            
            self.notify("✅ Meta token validation successful")
            return True
        except Exception as e:
            self.notify(f"❌ Failed to check token expiry: {e}", error=True)
            return False

    def poll_instagram_media_status(self, media_id):
        """Poll for Instagram media processing and copyright status"""
        self.notify(f"⏳ Starting Instagram media status polling for ID: {media_id}")
        url = f"https://graph.facebook.com/{self.meta_version}/{media_id}"
        params = {"fields": "status_code,copyright_status", "access_token": self.meta_token}

        max_attempts = 10  # Limit to 5 minutes
        attempts = 0

        while attempts < max_attempts:
            try:
                attempts += 1
                self.notify(f"🔄 Instagram polling attempt {attempts}/{max_attempts}")
                r = requests.get(url, params=params).json()
                sc = r.get("status_code")
                cp = r.get("copyright_status", "")

                self.notify(f"📊 Instagram Status: {sc}, Copyright: {cp}")

                if sc == "FINISHED":
                    if cp == "CLEARED":
                        self.notify("✅ Instagram media processing completed and copyright cleared")
                        return True
                    else:
                        self.notify(f"❌ Instagram copyright issue detected: {cp}", error=True)
                        return False
                if sc in ("ERROR", "BLOCKED", "REJECTED"):
                    self.notify(f"❌ Instagram processing failed with status: {sc}", error=True)
                    return False

                self.notify(f"⏸️ Instagram still processing, waiting {self.poll_interval} seconds...")
                time.sleep(self.poll_interval)
            except Exception as e:
                self.notify(f"❌ Error polling Instagram media status: {e}", error=True)
                return False

        self.notify("⚠️ Instagram polling timeout", error=True)
        return False

    def poll_facebook_video_status(self, video_id):
        """Poll for Facebook video publishing status"""
        self.notify(f"⏳ Starting Facebook video status polling for ID: {video_id}")
        url = f"https://graph.facebook.com/{self.meta_version}/{video_id}"
        params = {"fields": "publishing_status,is_transcodable", "access_token": self.meta_token}
        
        # Wait initial 15 seconds for processing
        self.notify("⏸️ Waiting 15 seconds for initial Facebook processing...")
        time.sleep(15)
        
        max_attempts = 60  # 5 minutes maximum (60 * 5 seconds)
        attempts = 0
        
        while attempts < max_attempts:
            try:
                attempts += 1
                self.notify(f"🔄 Facebook polling attempt {attempts}/{max_attempts}")
                r = requests.get(url, params=params).json()
                publishing_status = r.get("publishing_status", "")
                is_transcodable = r.get("is_transcodable", False)
                
                self.notify(f"📊 Facebook Status: {publishing_status}, Transcodable: {is_transcodable}")
                
                # Check if video is successfully published
                if publishing_status == "PUBLISHED" and is_transcodable:
                    self.notify("✅ Facebook video published successfully")
                    return True
                elif publishing_status in ("FAILED", "BLOCKED"):
                    self.notify(f"❌ Facebook video failed with status: {publishing_status}", error=True)
                    return False
                
                self.notify(f"⏸️ Facebook still processing, waiting {self.poll_interval} seconds...")
                time.sleep(self.poll_interval)
                
            except Exception as e:
                self.notify(f"❌ Error polling Facebook video status: {e}", error=True)
                attempts += 1
                time.sleep(self.poll_interval)
        
        self.notify("❌ Facebook video polling timeout reached", error=True)
        return False  # Timeout

    def confirm_facebook_video_is_live(self, video_id):
        """Final confirmation for Facebook Reels to ensure video is live and processed completely."""
        self.notify(f"🔍 Starting final Facebook video confirmation for ID: {video_id}")
        url = f"https://graph.facebook.com/{self.meta_version}/{video_id}"
        params = {
            "fields": "status,processing_progress",
            "access_token": self.meta_token
        }

        max_retries = 10
        for attempt in range(max_retries):
            try:
                attempt_num = attempt + 1
                self.notify(f"🔄 Facebook final check attempt {attempt_num}/{max_retries}")
                response = requests.get(url, params=params)
                if response.status_code != 200:
                    self.notify(f"[FB Final Check] Error: {response.status_code}, {response.text}", error=True)
                    time.sleep(5)
                    continue

                data = response.json()
                status = data.get("status", "").lower()
                progress = data.get("processing_progress", 0)

                self.notify(f"[FB Final Check] Status: {status}, Progress: {progress}%")

                if status == "live":
                    self.notify("[✅ FB Video Confirmed Live]")
                    return True
                elif status in ["error", "failed"]:
                    self.notify("[❌ FB Video Failed Processing]", error=True)
                    return False
                else:
                    self.notify(f"⏸️ Facebook video still processing, waiting 3 seconds...")
                    time.sleep(3)  # Wait and re-check

            except Exception as e:
                self.notify(f"❌ Error in Facebook final check: {e}", error=True)
                time.sleep(3)

        self.notify("[⚠️ FB Final Check Timeout Reached]", error=True)
        return False

    def retry_api_call(self, api_call, *args, **kwargs):
        """Retry API calls with exponential backoff"""
        for attempt in range(self.max_retries):
            try:
                attempt_num = attempt + 1
                self.notify(f"🔄 API call attempt {attempt_num}/{self.max_retries}")
                response = api_call(*args, **kwargs)
                if hasattr(response, 'status_code') and response.status_code == 200:
                    self.notify("✅ API call successful")
                    return response
                elif hasattr(response, 'raise_for_status'):
                    response.raise_for_status()
                    self.notify("✅ API call successful")
                    return response
                else:
                    self.notify("✅ API call completed")
                    return response
            except Exception as e:
                if attempt == self.max_retries - 1:
                    self.notify(f"❌ API call failed after {self.max_retries} attempts: {e}", error=True)
                    raise e
                wait_time = 2 ** attempt
                self.notify(f"⚠️ API call failed, retrying in {wait_time} seconds... (attempt {attempt_num}/{self.max_retries})")
                time.sleep(wait_time)
        return None

    def check_copyright_and_upload_instagram(self, dbx, file, caption):
        """Unified copyright check and upload for Instagram"""
        try:
            self.notify(f"🔍 Starting Instagram copyright check and upload for: {file.name}")
            
            self.notify(f"🔗 Getting Dropbox temporary link for: {file.name}")
            link = dbx.files_get_temporary_link(file.path_lower).link
            is_video = file.name.lower().endswith((".mp4", ".mov"))
            
            self.notify(f"📝 Preparing Instagram payload for: {file.name} (Video: {is_video})")
            payload = {
                "access_token": self.meta_token,
                "caption": caption,
                "share_to_feed": str(self.ig_share_feed).lower()
            }
            
            # Fix: Only include one media type and URL (no None values)
            if is_video:
                payload["video_url"] = link
                payload["media_type"] = "REELS"
            else:
                payload["image_url"] = link
                payload["media_type"] = "IMAGE"
            
            # Fix: Temporarily remove collaborators to test
            # if self.ig_collab:
            #     self.notify(f"👥 Adding Instagram collaborator: {self.ig_collab}")
            #     payload["collaborators"] = json.dumps([self.ig_collab])

            # Fix: Log the payload before uploading
            self.notify(f"📦 Final Instagram payload:\n{json.dumps(payload, indent=2)}")

            # Upload to Instagram
            self.notify(f"📤 Uploading to Instagram: {file.name}")
            r = self.retry_api_call(
                requests.post, 
                f"https://graph.facebook.com/{self.meta_version}/{self.ig_id}/media", 
                data=payload
            )
            if not r:
                self.notify(f"❌ Instagram upload failed for: {file.name}", error=True)
                return False, None
            media_id = r.json()["id"]
            self.notify(f"✅ Instagram media created with ID: {media_id}")

            # Poll for processing and copyright status
            self.notify(f"⏳ Polling Instagram status for copyright check: {file.name}")
            if not self.poll_instagram_media_status(media_id):
                self.notify(f"🚫 Copyright/blocking detected on Instagram: {file.name}", error=True)
                return False, None

            # Publish the media
            self.notify(f"📢 Publishing to Instagram: {file.name}")
            pub = self.retry_api_call(
                requests.post, 
                f"https://graph.facebook.com/{self.meta_version}/{self.ig_id}/media_publish", 
                data={
                    "access_token": self.meta_token,
                    "creation_id": media_id
                }
            )
            if not pub:
                self.notify(f"❌ Instagram publishing failed for: {file.name}", error=True)
                return False, None
            
            published_id = pub.json().get("id")
            self.notify(f"✅ Instagram upload successful: {file.name} (Published ID: {published_id})")
            return True, published_id
            
        except Exception as e:
            self.notify(f"❌ Instagram upload failed for {file.name}: {e}", error=True)
            return False, None

    def check_copyright_and_upload_facebook(self, dbx, file, caption):
        """Unified copyright check and upload for Facebook"""
        try:
            self.notify(f"🔍 Starting Facebook copyright check and upload for: {file.name}")
            
            self.notify(f"🔗 Getting Dropbox temporary link for: {file.name}")
            link = dbx.files_get_temporary_link(file.path_lower).link
            
            # Fix: Use caption as description for Facebook
            payload = {
                "access_token": self.meta_token,
                "video_url": link,
                "description": caption
            }
            
            # Fix: Log the payload before uploading
            self.notify(f"📦 Final Facebook payload:\n{json.dumps(payload, indent=2)}")
            
            # Upload to Facebook
            self.notify(f"📤 Uploading to Facebook: {file.name}")
            r = self.retry_api_call(
                requests.post, 
                f"https://graph.facebook.com/{self.meta_version}/{self.fb_page_id}/video_reels", 
                data=payload
            )
            
            if not r or r.status_code != 200:
                self.notify(f"❌ Facebook upload failed for: {file.name}", error=True)
                return False, None
                
            vid = r.json()["id"]
            self.notify(f"✅ Facebook video created with ID: {vid}")

            # Poll for video publishing status
            self.notify(f"⏳ Polling Facebook status for copyright check: {file.name}")
            if not self.poll_facebook_video_status(vid):
                self.notify(f"🚫 Copyright/blocking detected on Facebook: {file.name}", error=True)
                return False, None

            # Final confirmation check to ensure video is live
            self.notify(f"[FB] Video published, confirming final status: {file.name}")
            if not self.confirm_facebook_video_is_live(vid):
                self.notify(f"[⚠️ FB Video Not Confirmed Live] {file.name}", error=True)
                return False, None

            # Add collaborators
            if self.fb_collabs:
                self.notify(f"👥 Adding Facebook collaborators: {', '.join(self.fb_collabs)}")
                for collab in self.fb_collabs:
                    try:
                        self.retry_api_call(
                            requests.post, 
                            f"https://graph.facebook.com/{self.meta_version}/{vid}/collaborators", 
                            data={
                                "access_token": self.meta_token,
                                "target_id": collab
                            }
                        )
                        self.notify(f"✅ Added Facebook collaborator: {collab}")
                    except Exception as e:
                        self.notify(f"⚠️ Failed to add Facebook collaborator {collab}: {e}", error=True)
            else:
                self.notify("ℹ️ No Facebook collaborators to add")

            self.notify(f"✅ Facebook upload successful: {file.name} (Video ID: {vid})")
            return True, vid
            
        except Exception as e:
            self.notify(f"❌ Facebook upload failed for {file.name}: {e}", error=True)
            return False, None

    def fetch_insights(self, media_id, is_ig):
        """Fetch insights for uploaded media"""
        try:
            platform = "Instagram" if is_ig else "Facebook"
            self.notify(f"📊 Fetching {platform} insights for media ID: {media_id}")
            metrics = "plays,likes,comments,reach,saved" if is_ig else "video_views,engaged_users,likes,comments"
            r = requests.get(f"https://graph.facebook.com/{self.meta_version}/{media_id}/insights", params={
                "metric": metrics,
                "access_token": self.meta_token
            })
            insights = r.json()
            self.notify(f"✅ {platform} insights fetched successfully")
            return insights
        except Exception as e:
            self.notify(f"❌ Failed to fetch insights: {e}", error=True)
            return {}

    def get_available_files(self, dbx):
        """Get available media files from Dropbox"""
        try:
            self.notify(f"📁 Scanning Dropbox folder: {self.dropbox_folder}")
            result = dbx.files_list_folder(self.dropbox_folder)
            if not result or not result.entries:
                self.notify("❌ No files found in Dropbox folder", error=True)
                return []
            
            all_files = result.entries
            self.notify(f"📋 Found {len(all_files)} total files in Dropbox")
            
            files = [f for f in all_files if f.name.lower().endswith((".mp4", ".mov", ".jpg", ".jpeg", ".png"))]
            self.notify(f"🎬 Found {len(files)} media files")
            
            random.shuffle(files)
            selected_files = files[:self.max_files_to_check]
            self.notify(f"🎯 Selected {len(selected_files)} files for processing")
            
            for i, file in enumerate(selected_files, 1):
                self.notify(f"📄 File {i}: {file.name}")
            
            return selected_files
        except Exception as e:
            self.notify(f"❌ Failed to get files from Dropbox: {e}", error=True)
            return []

    def delete_file_from_dropbox(self, dbx, file):
        """Delete file from Dropbox"""
        try:
            self.notify(f"🗑️ Deleting file from Dropbox: {file.name}")
            dbx.files_delete_v2(file.path_lower)
            self.notify(f"✅ Successfully deleted file: {file.name}")
        except Exception as e:
            self.notify(f"❌ Failed to delete file {file.name}: {e}", error=True)

    def run(self):
        """Main execution method"""
        self.notify("🚀 Script started - will publish ONE file to both platforms")
        
        # Check token expiry
        if not self.check_token_expiry():
            self.notify("❌ Token validation failed", error=True)
            return

        try:
            # Get Dropbox client
            dbx = self.get_dropbox_client()
            
            # Load configuration
            config = self.load_config()
            caption = config.get("caption", "")
            description = config.get("description", "")

            # Get available files
            files = self.get_available_files(dbx)
            if not files:
                self.notify("❌ No media files found in Dropbox folder", error=True)
                return

            # Process files - try up to 3 files, but publish only ONE
            for idx, file in enumerate(files, start=1):
                self.notify(f"📁 Processing file {idx}/{len(files)}: {file.name}")
                
                try:
                    # Step 1: Check copyright and upload to Instagram
                    ig_ok, ig_mid = self.check_copyright_and_upload_instagram(dbx, file, caption)
                    
                    if not ig_ok:
                        self.notify(f"❌ Instagram copyright check failed for {file.name}", error=True)
                        self.delete_file_from_dropbox(dbx, file)
                        self.notify(f"🔄 Moving to next file...")
                        continue  # Try next file
                    
                    # Step 2: Check copyright and upload to Facebook
                    fb_ok, fb_mid = self.check_copyright_and_upload_facebook(dbx, file, description)
                    
                    if not fb_ok:
                        self.notify(f"❌ Facebook copyright check failed for {file.name}", error=True)
                        self.delete_file_from_dropbox(dbx, file)
                        self.notify(f"🔄 Moving to next file...")
                        continue  # Try next file
                    
                    # Step 3: Both platforms successful - fetch insights and exit
                    self.notify(f"📊 Fetching insights for {file.name}")
                    ig_ins = self.fetch_insights(ig_mid, is_ig=True)
                    fb_ins = self.fetch_insights(fb_mid, is_ig=False)
                    
                    self.notify(f"🎉 SUCCESS! Published: {file.name}\n📊 IG metrics: {json.dumps(ig_ins)}\n📊 FB metrics: {json.dumps(fb_ins)}")
                    
                    # Delete successful file and exit
                    self.delete_file_from_dropbox(dbx, file)
                    self.notify("✅ Script completed - ONE post successfully published to both platforms")
                    return
                        
                except Exception as e:
                    self.notify(f"❌ Upload error on {file.name}: {e}", error=True)
                    self.delete_file_from_dropbox(dbx, file)
                    self.notify(f"🔄 Moving to next file...")
                    continue  # Try next file
            
            # If we get here, all files failed
            self.notify("❌ All files checked were copyrighted/blocked - no post published", error=True)

        except Exception as e:
            self.notify(f"❌ Script execution failed: {e}", error=True)
            logger.error(f"Script execution failed: {e}")

        self.notify("🏁 Script execution finished")


if __name__ == "__main__":
    poster = SocialMediaPoster()
    poster.run()

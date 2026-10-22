import time
from collections import deque
from threading import Lock
from typing import Dict, Optional, Tuple
from backend.core.config import settings
from backend.core.logging import logger


def mask_phone(phone: Optional[str]) -> str:
    """Masks phone number for privacy in logs and error notifications."""
    if not phone:
        return "Unknown"
    digits = "".join(filter(str.isdigit, phone))
    if len(digits) <= 4:
        return "****"
    return f"+{digits[:2]}******{digits[-4:]}"


class UploadRateLimiter:
    """
    Sliding-window rate limiter for document / PDF uploads.
    Prevents denial of service, printer queue exhaustion, and bulk spam attacks.
    Default limit: max 50 uploads per 5 minutes (300 seconds) per phone number.
    """

    def __init__(
        self,
        max_uploads: int = 50,
        window_seconds: int = 300,
        cooldown_seconds: int = 300,
    ):
        self.max_uploads = max_uploads
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self._lock = Lock()
        self._user_uploads: Dict[str, deque] = {}
        self._cooldowns: Dict[str, float] = {}

    def check_upload(self, sender: str) -> Tuple[bool, Optional[str]]:
        """
        Validates whether a sender is allowed to upload another document.
        Returns:
            (True, None) if upload is permitted.
            (False, user_warning_message) if rate limit or cooldown is violated.
        """
        now = time.time()
        with self._lock:
            # 1. Check if user is in an active cooldown penalty
            cooldown_until = self._cooldowns.get(sender, 0.0)
            if now < cooldown_until:
                remaining_secs = int(cooldown_until - now)
                mins = max(1, (remaining_secs + 59) // 60)
                msg = (
                    f"🛑 *Upload Rate Limit Exceeded*\n\n"
                    f"You have uploaded more than {self.max_uploads} PDF files within 5 minutes.\n"
                    f"To ensure server stability and fair use for all customers, further uploads are temporarily paused.\n\n"
                    f"⏱️ *Cooldown Remaining:* ~{mins} minute(s)\n"
                    f"👉 Please wait before sending more files, or speak with the counter staff for bulk orders."
                )
                return False, msg

            # 2. Retrieve / initialize user timestamp deque
            if sender not in self._user_uploads:
                self._user_uploads[sender] = deque()

            user_deque = self._user_uploads[sender]

            # 3. Evict expired entries outside the sliding window
            cutoff = now - self.window_seconds
            while user_deque and user_deque[0] < cutoff:
                user_deque.popleft()

            # 4. Check upload count threshold
            if len(user_deque) >= self.max_uploads:
                # Trigger penalty cooldown
                self._cooldowns[sender] = now + self.cooldown_seconds
                user_deque.clear()  # reset deque on entering penalty
                logger.warning(
                    f"Upload rate limit triggered for {mask_phone(sender)}: "
                    f"exceeded {self.max_uploads} files in {self.window_seconds}s. "
                    f"Cooldown set for {self.cooldown_seconds}s."
                )
                msg = (
                    f"🛑 *Upload Rate Limit Exceeded*\n\n"
                    f"You have uploaded more than {self.max_uploads} PDF files within 5 minutes.\n"
                    f"To protect system performance, additional uploads from your number have been temporarily restricted.\n\n"
                    f"⏱️ *Cooldown Period:* Please wait 5 minutes before sending more documents.\n"
                    f"👉 For bulk print jobs, please speak with the counter staff directly."
                )
                return False, msg

            # 5. Record current upload timestamp
            user_deque.append(now)
            return True, None

    def get_upload_count(self, sender: str) -> int:
        """Returns the number of uploads recorded in the current active window."""
        now = time.time()
        with self._lock:
            if sender not in self._user_uploads:
                return 0
            user_deque = self._user_uploads[sender]
            cutoff = now - self.window_seconds
            while user_deque and user_deque[0] < cutoff:
                user_deque.popleft()
            return len(user_deque)

    def reset(self, sender: Optional[str] = None):
        """Resets rate limiting counters (useful for testing or manual admin override)."""
        with self._lock:
            if sender:
                self._user_uploads.pop(sender, None)
                self._cooldowns.pop(sender, None)
            else:
                self._user_uploads.clear()
                self._cooldowns.clear()


class MessageRateLimiter:
    """
    General sliding-window rate limiter for inbound WhatsApp text and interactive messages.
    Prevents message flooding and infinite bot reply loops.
    Default: max 60 messages per 60 seconds per phone number.
    """

    def __init__(self, max_messages: int = 60, window_seconds: int = 60):
        self.max_messages = max_messages
        self.window_seconds = window_seconds
        self._lock = Lock()
        self._user_messages: Dict[str, deque] = {}

    def check_message(self, sender: str) -> bool:
        """Returns True if message is permitted, False if message limit is breached."""
        now = time.time()
        with self._lock:
            if sender not in self._user_messages:
                self._user_messages[sender] = deque()

            user_deque = self._user_messages[sender]
            cutoff = now - self.window_seconds
            while user_deque and user_deque[0] < cutoff:
                user_deque.popleft()

            if len(user_deque) >= self.max_messages:
                logger.warning(
                    f"Inbound message rate limit reached for {mask_phone(sender)} "
                    f"({self.max_messages} msgs in {self.window_seconds}s)"
                )
                return False

            user_deque.append(now)
            return True

    def reset(self, sender: Optional[str] = None):
        with self._lock:
            if sender:
                self._user_messages.pop(sender, None)
            else:
                self._user_messages.clear()


# Global Singleton Instances
upload_rate_limiter = UploadRateLimiter(
    max_uploads=getattr(settings, "RATE_LIMIT_MAX_UPLOADS", 50),
    window_seconds=getattr(settings, "RATE_LIMIT_WINDOW_SECONDS", 300),
    cooldown_seconds=getattr(settings, "RATE_LIMIT_COOLDOWN_SECONDS", 300),
)

message_rate_limiter = MessageRateLimiter(
    max_messages=getattr(settings, "RATE_LIMIT_MAX_MESSAGES_PER_MIN", 60),
    window_seconds=60,
)

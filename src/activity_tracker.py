import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class ActivityTracker:
    def __init__(self, timeout_seconds: int = 300, session_hash: list[str] | None = None):
        self.timeout_seconds = timeout_seconds
        self.session_hash = session_hash if session_hash is not None else []
        self.last_activity_time: datetime | None = None

    def record_activity(self) -> None:
        self.last_activity_time = datetime.now(timezone.utc)
        logger.debug(f"Activity recorded at {self.last_activity_time}")

    def is_user_active(self) -> bool:
        now = datetime.now(timezone.utc)
        if self.last_activity_time is None:
            return False


        elapsed = (now - self.last_activity_time).total_seconds()
        is_active = elapsed < self.timeout_seconds

        if is_active:
            logger.debug(
                f"User is active (elapsed: {elapsed:.1f}s, timeout: {self.timeout_seconds}s)"
            )
        return is_active

    def get_time_until_idle(self) -> float:
        if self.last_activity_time is None:
            return 0

        elapsed = (datetime.now(timezone.utc) - self.last_activity_time).total_seconds()
        time_until_idle = max(0, self.timeout_seconds - elapsed)
        return time_until_idle

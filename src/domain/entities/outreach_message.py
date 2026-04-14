from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4


class OutreachChannel(StrEnum):
    LINKEDIN = "linkedin"
    EMAIL = "email"
    TWITTER = "twitter"
    TELEGRAM = "telegram"
    DISCORD = "discord"


@dataclass(slots=True)
class OutreachMessage:
    lead_id: UUID
    channel: OutreachChannel
    subject: str | None  # email only
    body: str
    is_sent: bool = False
    sent_at: datetime | None = None
    replied_at: datetime | None = None
    reply_text: str | None = None

    id: UUID = field(default_factory=uuid4)
    created_at: datetime = field(default_factory=datetime.utcnow)

    def mark_sent(self) -> None:
        self.is_sent = True
        self.sent_at = datetime.utcnow()

    def mark_replied(self, reply: str) -> None:
        self.replied_at = datetime.utcnow()
        self.reply_text = reply

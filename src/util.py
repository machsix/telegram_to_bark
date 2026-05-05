from telethon.tl.types import User, Chat, Channel
import enum
from telethon.tl.types import NotifyChats, NotifyUsers, NotifyBroadcasts, NotifyPeer
from telethon.tl.types import InputNotifyChats, InputNotifyUsers, InputNotifyBroadcasts

class SenderType(enum.Enum):
    User = "User"
    Group = "Group"
    Channel = "Channel"

def get_sender_name(sender: User | Chat | Channel) -> str:
    if isinstance(sender, User):
        if sender.first_name:
            if sender.last_name:
                return f"{sender.first_name} {sender.last_name}"
            return sender.first_name
        return sender.username or "Unknown User"
    elif isinstance(sender, Chat):
        return sender.title or "Group"
    elif isinstance(sender, Channel):
        return sender.title or "Channel"
    return "Unknown"

def get_sender_type(entity) -> SenderType:
    if isinstance(entity, (User, NotifyPeer)):
        return SenderType.User
    elif isinstance(entity, (Chat, InputNotifyChats,  InputNotifyUsers,  NotifyUsers, NotifyChats)):
        return SenderType.Group
    elif isinstance(entity, (Channel, InputNotifyBroadcasts, NotifyBroadcasts)):
        return SenderType.Channel
    else:
        raise ValueError(f"Unknown entity type: {type(entity)}")


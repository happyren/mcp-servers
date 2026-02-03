"""Session router for mapping Telegram chats to OpenCode instances.

Routes messages from Telegram users/chats to the appropriate OpenCode instance.
Supports multiple routing strategies and remembers user preferences.

Now also supports Telegram Forum Topics:
- Each topic can be mapped to a specific OpenCode instance
- Topics act as project-specific conversation threads
- In forum-enabled groups, routing is by (chat_id, topic_id) instead of just chat_id
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .instance import OpenCodeInstance

logger = logging.getLogger("telegram_controller.session_router")


@dataclass
class ChatContext:
    """Stores context for a Telegram chat/user.
    
    Tracks which OpenCode instance this chat is currently connected to,
    along with session-level preferences.
    """
    
    # Telegram chat ID
    chat_id: int
    
    # Currently selected OpenCode instance ID
    current_instance_id: Optional[str] = None
    
    # OpenCode session ID within the instance (if any)
    session_id: Optional[str] = None
    
    # Model preferences for this chat
    provider_id: Optional[str] = None
    model_id: Optional[str] = None
    
    # Last activity timestamp
    last_activity: Optional[datetime] = None
    
    # User-friendly name/label for this context
    name: Optional[str] = None
    
    # Telegram forum topic ID (message_thread_id) - None for non-forum chats
    topic_id: Optional[int] = None
    
    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "chat_id": self.chat_id,
            "current_instance_id": self.current_instance_id,
            "session_id": self.session_id,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
            "name": self.name,
            "topic_id": self.topic_id,
        }
    
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChatContext":
        """Deserialize from dictionary."""
        last_activity = None
        if data.get("last_activity"):
            last_activity = datetime.fromisoformat(data["last_activity"])
        
        return cls(
            chat_id=data["chat_id"],
            current_instance_id=data.get("current_instance_id"),
            session_id=data.get("session_id"),
            provider_id=data.get("provider_id"),
            model_id=data.get("model_id"),
            last_activity=last_activity,
            name=data.get("name"),
            topic_id=data.get("topic_id"),
        )


class SessionRouter:
    """Routes Telegram messages to OpenCode instances.
    
    Each Telegram chat can be connected to one OpenCode instance at a time.
    In forum-enabled groups, each topic can have its own instance.
    
    The router tracks these connections and provides methods to:
    - Get the current instance for a chat/topic
    - Switch a chat/topic to a different instance
    - List available instances for a chat
    - Track last active OpenCode session per instance
    - Map topics to instances in forum groups
    """
    
    def __init__(self, state_dir: Path | None = None):
        """Initialize the session router.
        
        Args:
            state_dir: Directory for state persistence
        """
        self.state_dir = state_dir or Path("~/.local/share/telegram_controller").expanduser()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        
        self.state_file = self.state_dir / "router_state.json"
        
        # Chat contexts by context_key (see _context_key method)
        # For regular chats: "chat:{chat_id}"
        # For forum topics: "topic:{chat_id}:{topic_id}"
        self.contexts: dict[str, ChatContext] = {}
        
        # Default instance for new chats (if any)
        self.default_instance_id: Optional[str] = None
        
        # Track last active OpenCode session per instance
        # instance_id -> session_id
        self.instance_sessions: dict[str, str] = {}
        
        # Track topic-to-instance mappings for forum groups
        # (chat_id, topic_id) -> instance_id
        self.topic_instances: dict[tuple[int, int], str] = {}
        
        # Track which chats are forum-enabled (supergroups with topics)
        self.forum_chats: set[int] = set()
        
        # Load persisted state
        self._load_state()
    
    def _context_key(self, chat_id: int, topic_id: Optional[int] = None) -> str:
        """Generate a context key for a chat or topic.
        
        Args:
            chat_id: Telegram chat ID
            topic_id: Optional topic ID for forum groups
            
        Returns:
            Context key string
        """
        if topic_id is not None:
            return f"topic:{chat_id}:{topic_id}"
        return f"chat:{chat_id}"
    
    def _load_state(self) -> None:
        """Load persisted router state."""
        if not self.state_file.exists():
            return
        
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            
            for context_data in data.get("contexts", []):
                context = ChatContext.from_dict(context_data)
                key = self._context_key(context.chat_id, context.topic_id)
                self.contexts[key] = context
            
            self.default_instance_id = data.get("default_instance_id")
            
            # Load instance sessions
            self.instance_sessions = data.get("instance_sessions", {})
            
            # Load topic instances
            topic_instances_raw = data.get("topic_instances", {})
            for key, instance_id in topic_instances_raw.items():
                parts = key.split(":")
                if len(parts) == 2:
                    chat_id, topic_id = int(parts[0]), int(parts[1])
                    self.topic_instances[(chat_id, topic_id)] = instance_id
            
            # Load forum chats
            self.forum_chats = set(data.get("forum_chats", []))
            
            logger.info(f"Loaded {len(self.contexts)} chat contexts from state")
        except Exception as e:
            logger.error(f"Failed to load router state: {e}")
    
    def _save_state(self) -> None:
        """Persist router state to file."""
        try:
            # Serialize topic_instances with string keys
            topic_instances_raw = {
                f"{chat_id}:{topic_id}": instance_id
                for (chat_id, topic_id), instance_id in self.topic_instances.items()
            }
            
            data = {
                "contexts": [ctx.to_dict() for ctx in self.contexts.values()],
                "default_instance_id": self.default_instance_id,
                "instance_sessions": self.instance_sessions,
                "topic_instances": topic_instances_raw,
                "forum_chats": list(self.forum_chats),
                "updated_at": datetime.now().isoformat(),
            }
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save router state: {e}")
    
    def mark_chat_as_forum(self, chat_id: int) -> None:
        """Mark a chat as a forum-enabled supergroup.
        
        Args:
            chat_id: Telegram chat ID
        """
        if chat_id not in self.forum_chats:
            self.forum_chats.add(chat_id)
            self._save_state()
            logger.info(f"Marked chat {chat_id} as forum-enabled")
    
    def is_forum_chat(self, chat_id: int) -> bool:
        """Check if a chat is a forum-enabled supergroup.
        
        Args:
            chat_id: Telegram chat ID
            
        Returns:
            True if chat has topics enabled
        """
        return chat_id in self.forum_chats
    
    def get_context(self, chat_id: int, topic_id: Optional[int] = None) -> ChatContext:
        """Get or create a context for a chat or topic.
        
        Args:
            chat_id: Telegram chat ID
            topic_id: Optional topic ID for forum groups
            
        Returns:
            The ChatContext for this chat/topic
        """
        key = self._context_key(chat_id, topic_id)
        
        if key not in self.contexts:
            self.contexts[key] = ChatContext(
                chat_id=chat_id,
                topic_id=topic_id,
                current_instance_id=self.default_instance_id,
                last_activity=datetime.now(),
            )
            self._save_state()
        
        return self.contexts[key]
    
    def get_current_instance_id(self, chat_id: int, topic_id: Optional[int] = None) -> Optional[str]:
        """Get the current instance ID for a chat or topic.
        
        Args:
            chat_id: Telegram chat ID
            topic_id: Optional topic ID for forum groups
            
        Returns:
            Instance ID or None if no instance is selected
        """
        # For forum chats with topics, check topic mapping first
        if topic_id is not None:
            if (chat_id, topic_id) in self.topic_instances:
                instance_id = self.topic_instances[(chat_id, topic_id)]
                logger.debug(f"get_current_instance_id: chat={chat_id} topic={topic_id} -> {instance_id[:8] if instance_id else None} (from topic_instances)")
                return instance_id
        
        context = self.get_context(chat_id, topic_id)
        instance_id = context.current_instance_id
        logger.debug(f"get_current_instance_id: chat={chat_id} topic={topic_id} -> {instance_id[:8] if instance_id else None} (from context)")
        return instance_id
    
    def set_current_instance(
        self,
        chat_id: int,
        instance: OpenCodeInstance,
        session_id: Optional[str] = None,
        topic_id: Optional[int] = None,
    ) -> None:
        """Set the current instance for a chat or topic.
        
        Args:
            chat_id: Telegram chat ID
            instance: The OpenCode instance to connect to
            session_id: Optional session ID within the instance
            topic_id: Optional topic ID for forum groups
        """
        context = self.get_context(chat_id, topic_id)
        context.current_instance_id = instance.id
        context.last_activity = datetime.now()
        
        # Restore last active session for this instance if not provided
        if session_id:
            context.session_id = session_id
            self.instance_sessions[instance.id] = session_id
        elif instance.id in self.instance_sessions:
            context.session_id = self.instance_sessions[instance.id]
        
        # Update topic mapping if in a topic
        if topic_id is not None:
            self.topic_instances[(chat_id, topic_id)] = instance.id
        
        self._save_state()
        
        logger.info(f"Chat {chat_id} (topic={topic_id}) connected to instance {instance.short_id}")
    
    def clear_current_instance(self, chat_id: int, topic_id: Optional[int] = None) -> None:
        """Clear the current instance for a chat or topic.
        
        Args:
            chat_id: Telegram chat ID
            topic_id: Optional topic ID for forum groups
        """
        context = self.get_context(chat_id, topic_id)
        context.current_instance_id = None
        context.session_id = None
        context.last_activity = datetime.now()
        
        # Clear topic mapping if in a topic
        if topic_id is not None and (chat_id, topic_id) in self.topic_instances:
            del self.topic_instances[(chat_id, topic_id)]
        
        self._save_state()
    
    def set_session_id(self, chat_id: int, session_id: Optional[str], topic_id: Optional[int] = None) -> None:
        """Set or clear the OpenCode session ID for a chat or topic.
        
        Also remembers the session for the current instance.
        
        Args:
            chat_id: Telegram chat ID
            session_id: OpenCode session ID (None to clear)
            topic_id: Optional topic ID for forum groups
        """
        context = self.get_context(chat_id, topic_id)
        context.session_id = session_id
        context.last_activity = datetime.now()
        
        # Remember session for the instance
        if session_id and context.current_instance_id:
            self.instance_sessions[context.current_instance_id] = session_id
        
        self._save_state()
    
    def get_session_id(self, chat_id: int, topic_id: Optional[int] = None) -> Optional[str]:
        """Get the OpenCode session ID for a chat or topic.
        
        Args:
            chat_id: Telegram chat ID
            topic_id: Optional topic ID for forum groups
            
        Returns:
            Session ID or None
        """
        context = self.get_context(chat_id, topic_id)
        return context.session_id
    
    def get_instance_session(self, instance_id: str) -> Optional[str]:
        """Get the last active session ID for an instance.
        
        Args:
            instance_id: OpenCode instance ID
            
        Returns:
            Session ID or None
        """
        return self.instance_sessions.get(instance_id)
    
    def set_instance_session(self, instance_id: str, session_id: str) -> None:
        """Set the last active session for an instance.
        
        Args:
            instance_id: OpenCode instance ID
            session_id: OpenCode session ID
        """
        self.instance_sessions[instance_id] = session_id
        self._save_state()
    
    def set_model_preference(
        self,
        chat_id: int,
        provider_id: str,
        model_id: str,
        topic_id: Optional[int] = None,
    ) -> None:
        """Set model preference for a chat or topic.
        
        Args:
            chat_id: Telegram chat ID
            provider_id: AI provider ID
            model_id: AI model ID
            topic_id: Optional topic ID for forum groups
        """
        context = self.get_context(chat_id, topic_id)
        context.provider_id = provider_id
        context.model_id = model_id
        context.last_activity = datetime.now()
        self._save_state()
    
    def get_model_preference(self, chat_id: int, topic_id: Optional[int] = None) -> tuple[Optional[str], Optional[str]]:
        """Get model preference for a chat or topic.
        
        Args:
            chat_id: Telegram chat ID
            topic_id: Optional topic ID for forum groups
            
        Returns:
            Tuple of (provider_id, model_id) or (None, None)
        """
        context = self.get_context(chat_id, topic_id)
        return context.provider_id, context.model_id
    
    def set_default_instance(self, instance_id: str) -> None:
        """Set the default instance for new chats.
        
        Args:
            instance_id: Instance ID to use as default
        """
        self.default_instance_id = instance_id
        self._save_state()
        logger.info(f"Default instance set to {instance_id[:8]}")
    
    def touch(self, chat_id: int, topic_id: Optional[int] = None) -> None:
        """Update last activity timestamp for a chat or topic.
        
        Args:
            chat_id: Telegram chat ID
            topic_id: Optional topic ID for forum groups
        """
        context = self.get_context(chat_id, topic_id)
        context.last_activity = datetime.now()
        # Don't save on every touch - too expensive
    
    def get_chats_for_instance(self, instance_id: str) -> list[int]:
        """Get all chat IDs connected to an instance.
        
        Args:
            instance_id: Instance ID to look up
            
        Returns:
            List of chat IDs
        """
        return [
            context.chat_id
            for context in self.contexts.values()
            if context.current_instance_id == instance_id
        ]
    
    def get_topics_for_instance(self, instance_id: str) -> list[tuple[int, int]]:
        """Get all (chat_id, topic_id) pairs connected to an instance.
        
        Args:
            instance_id: Instance ID to look up
            
        Returns:
            List of (chat_id, topic_id) tuples
        """
        return [
            key for key, inst_id in self.topic_instances.items()
            if inst_id == instance_id
        ]
    
    def get_instance_for_topic(self, chat_id: int, topic_id: int) -> Optional[str]:
        """Get the instance ID mapped to a specific topic.
        
        Args:
            chat_id: Telegram chat ID
            topic_id: Topic ID (message_thread_id)
            
        Returns:
            Instance ID or None
        """
        return self.topic_instances.get((chat_id, topic_id))
    
    def set_topic_instance(self, chat_id: int, topic_id: int, instance_id: str) -> None:
        """Map a topic to an instance.
        
        Args:
            chat_id: Telegram chat ID
            topic_id: Topic ID (message_thread_id)
            instance_id: OpenCode instance ID
        """
        self.topic_instances[(chat_id, topic_id)] = instance_id
        self._save_state()
        logger.info(f"Mapped topic {topic_id} in chat {chat_id} to instance {instance_id[:8]}")
    
    def clear_topic_instance(self, chat_id: int, topic_id: int) -> None:
        """Clear the instance mapping for a topic.
        
        Args:
            chat_id: Telegram chat ID
            topic_id: Topic ID (message_thread_id)
        """
        if (chat_id, topic_id) in self.topic_instances:
            del self.topic_instances[(chat_id, topic_id)]
            self._save_state()
    
    def get_topics_for_chat(self, chat_id: int) -> list[tuple[int, str]]:
        """Get all topics and their instance mappings for a specific chat.
        
        Args:
            chat_id: Telegram chat ID
            
        Returns:
            List of (topic_id, instance_id) tuples for this chat
        """
        return [
            (topic_id, instance_id)
            for (cid, topic_id), instance_id in self.topic_instances.items()
            if cid == chat_id
        ]
    
    def remove_instance_references(self, instance_id: str) -> int:
        """Remove all references to an instance from contexts.
        
        Called when an instance is removed.
        
        Args:
            instance_id: Instance ID to remove
            
        Returns:
            Number of contexts updated
        """
        count = 0
        for context in self.contexts.values():
            if context.current_instance_id == instance_id:
                context.current_instance_id = None
                context.session_id = None
                count += 1
        
        # Remove from topic mappings
        topics_to_remove = [
            key for key, inst_id in self.topic_instances.items()
            if inst_id == instance_id
        ]
        for key in topics_to_remove:
            del self.topic_instances[key]
            count += 1
        
        # Remove from instance sessions
        if instance_id in self.instance_sessions:
            del self.instance_sessions[instance_id]
        
        if self.default_instance_id == instance_id:
            self.default_instance_id = None
        
        if count > 0:
            self._save_state()
            logger.info(f"Cleared instance {instance_id[:8]} from {count} contexts")
        
        return count

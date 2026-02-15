"""Channel adapter interface for message sending/receiving."""

from abc import ABC, abstractmethod
from typing import Callable, Awaitable, Optional
from ..domain.models import Message


class IChannelAdapter(ABC):
    """Interface for message channel adapters (Telegram, Discord, etc.)."""
    
    @abstractmethod
    async def send_text(self, chat_id: int, text: str) -> None:
        """Send a text message to a chat.
        
        Args:
            chat_id: The chat/conversation ID
            text: The message text to send
        """
        pass
    
    @abstractmethod
    async def reply_to(self, chat_id: int, reply_to_message_id: int, text: str) -> None:
        """Reply to a specific message.
        
        Args:
            chat_id: The chat/conversation ID
            reply_to_message_id: The message ID to reply to
            text: The reply text
        """
        pass
    
    @abstractmethod
    async def start(self) -> None:
        """Start the channel adapter (begin receiving messages)."""
        pass
    
    @abstractmethod
    async def stop(self) -> None:
        """Stop the channel adapter."""
        pass
    
    @abstractmethod
    def set_message_handler(
        self, 
        handler: Callable[[Message], Awaitable[None]]
    ) -> None:
        """Set the handler for incoming messages.
        
        Args:
            handler: Async function to handle incoming messages
        """
        pass
    
    @abstractmethod
    def set_command_handler(
        self,
        command: str,
        handler: Callable[[Message], Awaitable[None]]
    ) -> None:
        """Set handler for a specific command.
        
        Args:
            command: Command name without slash (e.g., 'start', 'help')
            handler: Async function to handle the command
        """
        pass

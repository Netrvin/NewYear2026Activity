"""Telegram bot adapter implementation."""

import logging
from datetime import datetime
from typing import Callable, Awaitable, Optional, Dict

from telegram import Update, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)
from telegram.error import BadRequest

from ...ports.channel import IChannelAdapter
from ...domain.models import Message

logger = logging.getLogger(__name__)


class TelegramAdapter(IChannelAdapter):
    """Telegram implementation of IChannelAdapter."""
    
    def __init__(self, token: str):
        self.token = token
        self.app: Optional[Application] = None
        self.bot: Optional[Bot] = None
        
        self._message_handler: Optional[Callable[[Message], Awaitable[None]]] = None
        self._command_handlers: Dict[str, Callable[[Message], Awaitable[None]]] = {}
        self._running = False
    
    async def send_text(self, chat_id: int, text: str) -> None:
        """Send a text message to a chat."""
        if not self.bot:
            raise RuntimeError("Bot not initialized")
        
        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode='Markdown'
            )
        except BadRequest as e:
            # Markdown parsing failed, retry without formatting
            logger.warning(f"Markdown parsing failed, retrying without format: {e}")
            await self.bot.send_message(
                chat_id=chat_id,
                text=text
            )
    
    async def reply_to(self, chat_id: int, reply_to_message_id: int, text: str) -> None:
        """Reply to a specific message."""
        if not self.bot:
            raise RuntimeError("Bot not initialized")
        
        try:
            await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=reply_to_message_id,
                parse_mode='Markdown'
            )
        except BadRequest as e:
            # Markdown parsing failed, retry without formatting
            logger.warning(f"Markdown parsing failed, retrying without format: {e}")
            await self.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_to_message_id=reply_to_message_id
            )
    
    async def start(self) -> None:
        """Start the Telegram bot."""
        self.app = Application.builder().token(self.token).build()
        self.bot = self.app.bot
        
        # Register command handlers
        for command, handler in self._command_handlers.items():
            self.app.add_handler(
                CommandHandler(command, self._make_command_callback(handler))
            )
        
        # Register message handler for non-command text
        if self._message_handler:
            self.app.add_handler(
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    self._make_message_callback(self._message_handler)
                )
            )
        
        # Initialize and start polling
        await self.app.initialize()
        await self.app.start()
        
        self._running = True
        logger.info("Telegram bot started")
        
        # Start polling (non-blocking)
        await self.app.updater.start_polling(drop_pending_updates=True)
    
    async def stop(self) -> None:
        """Stop the Telegram bot."""
        if self.app and self._running:
            self._running = False
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
            logger.info("Telegram bot stopped")
    
    def set_message_handler(
        self, 
        handler: Callable[[Message], Awaitable[None]]
    ) -> None:
        """Set the handler for incoming text messages."""
        self._message_handler = handler
    
    def set_command_handler(
        self,
        command: str,
        handler: Callable[[Message], Awaitable[None]]
    ) -> None:
        """Set handler for a specific command."""
        self._command_handlers[command] = handler
    
    def _make_message_callback(
        self, 
        handler: Callable[[Message], Awaitable[None]]
    ) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]:
        """Create a telegram callback from our handler."""
        async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not update.message or not update.message.text:
                return
            
            message = Message(
                user_id=update.effective_user.id,
                chat_id=update.effective_chat.id,
                message_id=update.message.message_id,
                text=update.message.text,
                username=update.effective_user.username,
                nickname=update.effective_user.first_name + (update.effective_user.last_name or ''),
                timestamp=update.message.date or datetime.now()
            )
            
            try:
                await handler(message)
            except Exception as e:
                logger.exception(f"Error handling message: {e}")
        
        return callback
    
    def _make_command_callback(
        self,
        handler: Callable[[Message], Awaitable[None]]
    ) -> Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[None]]:
        """Create a telegram command callback from our handler."""
        async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not update.message:
                return
            
            # Extract command arguments as text
            text = update.message.text or ""
            
            message = Message(
                user_id=update.effective_user.id,
                chat_id=update.effective_chat.id,
                message_id=update.message.message_id,
                text=text,
                username=update.effective_user.username,
                nickname=update.effective_user.first_name + (update.effective_user.last_name or ''),
                timestamp=update.message.date or datetime.now()
            )
            
            try:
                await handler(message)
            except Exception as e:
                logger.exception(f"Error handling command: {e}")
        
        return callback

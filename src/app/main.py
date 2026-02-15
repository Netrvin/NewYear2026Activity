"""Main application entry point."""

import asyncio
import logging
import signal
import sys
from typing import Optional

from .container import Container
from .settings import LOG_LEVEL
from ..domain.models import Message

# Configure logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Suppress verbose httpx HTTP request logging
logging.getLogger("httpx").setLevel(logging.WARNING)


class Application:
    """Main application class."""
    
    def __init__(self, use_mock_llm: bool = False):
        self.container = Container(use_mock_llm=use_mock_llm)
        self._shutdown_event: Optional[asyncio.Event] = None
    
    async def start(self) -> None:
        """Start the application."""
        logger.info("Starting 2026 马年极客闯关 Bot...")
        
        try:
            # Initialize container
            await self.container.initialize()
            logger.info("Container initialized")
            
            # Get components
            engine = self.container.engine
            channel = self.container.channel
            admin = self.container.admin_commands
            
            # Register command handlers
            channel.set_command_handler('start', engine.handle_start)
            channel.set_command_handler('help', engine.handle_help)
            channel.set_command_handler('status', engine.handle_status)
            channel.set_command_handler('rules', engine.handle_rules)
            channel.set_command_handler('admin', admin.handle_admin_command)
            
            # Register message handler for answers
            async def message_handler(message: Message) -> None:
                # Activity enabled check is now handled by engine via activity_enabled_fn
                await engine.handle_message(message)
            
            channel.set_message_handler(message_handler)
            
            # Start worker pool
            await self.container.worker_pool.start()
            logger.info("Worker pool started")
            
            # Start channel (Telegram bot)
            await channel.start()
            logger.info("Telegram bot started")
            
            # Setup shutdown handling
            self._shutdown_event = asyncio.Event()
            
            # Wait for shutdown signal
            logger.info("Application running. Press Ctrl+C to stop.")
            try:
                await self._shutdown_event.wait()
            except asyncio.CancelledError:
                pass  # Ctrl+C on Windows triggers CancelledError
            
        except Exception as e:
            logger.exception(f"Application error: {e}")
            raise
        finally:
            await self.stop()
    
    async def stop(self) -> None:
        """Stop the application."""
        logger.info("Shutting down...")
        await self.container.shutdown()
        logger.info("Shutdown complete")
    
    def request_shutdown(self) -> None:
        """Request application shutdown."""
        if self._shutdown_event:
            self._shutdown_event.set()


async def main(use_mock_llm: bool = False) -> None:
    """Main entry point."""
    app = Application(use_mock_llm=use_mock_llm)
    
    # Setup signal handlers
    loop = asyncio.get_running_loop()
    
    def signal_handler():
        logger.info("Received shutdown signal")
        app.request_shutdown()
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass
    
    await app.start()


def run():
    """Run the application."""
    use_mock = '--mock' in sys.argv
    
    try:
        asyncio.run(main(use_mock_llm=use_mock))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")


if __name__ == '__main__':
    run()

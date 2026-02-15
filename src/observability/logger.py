"""Logging utilities."""

import logging
import uuid
from contextvars import ContextVar
from typing import Optional

# Context variable for trace ID
trace_id_var: ContextVar[str] = ContextVar('trace_id', default='')


def get_trace_id() -> str:
    """Get current trace ID or generate a new one."""
    trace_id = trace_id_var.get()
    if not trace_id:
        trace_id = str(uuid.uuid4())[:8]
        trace_id_var.set(trace_id)
    return trace_id


def set_trace_id(trace_id: str) -> None:
    """Set trace ID for current context."""
    trace_id_var.set(trace_id)


class TraceIdFilter(logging.Filter):
    """Logging filter that adds trace_id to log records."""
    
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = trace_id_var.get() or '-'
        return True


def setup_logging(level: str = 'INFO') -> None:
    """Setup structured logging."""
    # Create handler
    handler = logging.StreamHandler()
    handler.addFilter(TraceIdFilter())
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - [%(trace_id)s] - %(message)s'
    )
    handler.setFormatter(formatter)
    
    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level))
    root_logger.addHandler(handler)

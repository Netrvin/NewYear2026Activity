"""SQLite database migrations and schema."""

MIGRATIONS = [
    # Migration 1: Initial schema
    """
    -- Users table
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_user_id INTEGER UNIQUE NOT NULL,
        username TEXT,
        is_banned INTEGER DEFAULT 0,
        ban_reason TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_users_telegram_id ON users(telegram_user_id);
    
    -- Sessions table (user session per level)
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        level_id INTEGER NOT NULL,
        state TEXT NOT NULL DEFAULT 'READY',
        turn_count INTEGER DEFAULT 0,
        last_attempt_at TEXT,
        cooldown_until TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, level_id),
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_user_level ON sessions(user_id, level_id);
    
    -- Attempts table (individual attempts)
    CREATE TABLE IF NOT EXISTS attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        level_id INTEGER NOT NULL,
        session_id INTEGER NOT NULL,
        turn_index INTEGER NOT NULL,
        user_prompt TEXT NOT NULL,
        llm_output TEXT,
        keyword_verdict TEXT,
        judge_verdict TEXT,
        final_verdict TEXT,
        grade_reason TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (session_id) REFERENCES sessions(id)
    );
    CREATE INDEX IF NOT EXISTS idx_attempts_session ON attempts(session_id);
    
    -- Level progress table
    CREATE TABLE IF NOT EXISTS level_progress (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        level_id INTEGER NOT NULL,
        passed INTEGER DEFAULT 0,
        passed_at TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, level_id),
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    CREATE INDEX IF NOT EXISTS idx_level_progress_user ON level_progress(user_id);
    
    -- Reward items table (synced from config)
    CREATE TABLE IF NOT EXISTS reward_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pool_id TEXT NOT NULL,
        item_id TEXT UNIQUE NOT NULL,
        type TEXT NOT NULL,
        code TEXT NOT NULL,
        max_claims_per_item INTEGER NOT NULL,
        claimed_count INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_reward_items_pool ON reward_items(pool_id);
    
    -- Reward claims table
    CREATE TABLE IF NOT EXISTS reward_claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        level_id INTEGER NOT NULL,
        pool_id TEXT NOT NULL,
        item_id TEXT NOT NULL,
        reward_code TEXT NOT NULL,
        claimed_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, level_id),
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (item_id) REFERENCES reward_items(item_id)
    );
    CREATE INDEX IF NOT EXISTS idx_reward_claims_user ON reward_claims(user_id);
    
    -- Log events table
    CREATE TABLE IF NOT EXISTS log_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trace_id TEXT NOT NULL,
        event_type TEXT NOT NULL,
        telegram_user_id INTEGER NOT NULL,
        chat_id INTEGER NOT NULL,
        level_id INTEGER,
        session_id INTEGER,
        turn_index INTEGER,
        content TEXT,
        metadata TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_log_events_trace ON log_events(trace_id);
    CREATE INDEX IF NOT EXISTS idx_log_events_created ON log_events(created_at);
    CREATE INDEX IF NOT EXISTS idx_log_events_user ON log_events(telegram_user_id);
    
    -- Schema version table
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY
    );
    INSERT OR IGNORE INTO schema_version (version) VALUES (1);
    """,
    
    # Migration 2: Add pending_tasks table for queue persistence
    """
    -- Pending tasks table (for queue persistence)
    CREATE TABLE IF NOT EXISTS pending_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trace_id TEXT NOT NULL UNIQUE,
        user_id INTEGER NOT NULL,
        telegram_user_id INTEGER NOT NULL,
        chat_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        username TEXT,
        level_id INTEGER NOT NULL,
        session_id INTEGER NOT NULL,
        user_prompt TEXT NOT NULL,
        turn_index INTEGER NOT NULL,
        enqueued_at TEXT NOT NULL,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    CREATE INDEX IF NOT EXISTS idx_pending_tasks_enqueued ON pending_tasks(enqueued_at);
    
    UPDATE schema_version SET version = 2;
    """,
]


def get_migration_sql(from_version: int = 0) -> str:
    """Get all migrations from a given version."""
    migrations_to_apply = MIGRATIONS[from_version:]
    return "\n".join(migrations_to_apply)


def get_current_version() -> int:
    """Get the current schema version."""
    return len(MIGRATIONS)

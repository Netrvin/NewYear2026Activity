"""MySQL database migrations and schema."""

MYSQL_MIGRATIONS = [
    # Migration 1: Initial schema
    [
        """
        CREATE TABLE IF NOT EXISTS users (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            telegram_user_id BIGINT NOT NULL UNIQUE,
            username VARCHAR(255) NULL,
            is_banned TINYINT(1) NOT NULL DEFAULT 0,
            ban_reason TEXT NULL,
            created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """,
        "CREATE INDEX idx_users_telegram_id ON users(telegram_user_id);",
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            user_id BIGINT NOT NULL,
            level_id INT NOT NULL,
            state VARCHAR(32) NOT NULL DEFAULT 'READY',
            turn_count INT NOT NULL DEFAULT 0,
            last_attempt_at DATETIME(6) NULL,
            cooldown_until DATETIME(6) NULL,
            created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
            UNIQUE KEY uq_sessions_user_level (user_id, level_id),
            CONSTRAINT fk_sessions_user FOREIGN KEY (user_id) REFERENCES users(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """,
        "CREATE INDEX idx_sessions_user_level ON sessions(user_id, level_id);",
        """
        CREATE TABLE IF NOT EXISTS attempts (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            user_id BIGINT NOT NULL,
            level_id INT NOT NULL,
            session_id BIGINT NOT NULL,
            turn_index INT NOT NULL,
            user_prompt TEXT NOT NULL,
            llm_output LONGTEXT NULL,
            keyword_verdict VARCHAR(16) NULL,
            judge_verdict VARCHAR(16) NULL,
            final_verdict VARCHAR(16) NULL,
            grade_reason TEXT NULL,
            created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            CONSTRAINT fk_attempts_user FOREIGN KEY (user_id) REFERENCES users(id),
            CONSTRAINT fk_attempts_session FOREIGN KEY (session_id) REFERENCES sessions(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """,
        "CREATE INDEX idx_attempts_session ON attempts(session_id);",
        """
        CREATE TABLE IF NOT EXISTS level_progress (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            user_id BIGINT NOT NULL,
            level_id INT NOT NULL,
            passed TINYINT(1) NOT NULL DEFAULT 0,
            passed_at DATETIME(6) NULL,
            created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            UNIQUE KEY uq_level_progress_user_level (user_id, level_id),
            CONSTRAINT fk_level_progress_user FOREIGN KEY (user_id) REFERENCES users(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """,
        "CREATE INDEX idx_level_progress_user ON level_progress(user_id);",
        """
        CREATE TABLE IF NOT EXISTS reward_items (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            pool_id VARCHAR(64) NOT NULL,
            item_id VARCHAR(128) NOT NULL UNIQUE,
            type VARCHAR(32) NOT NULL,
            code TEXT NOT NULL,
            max_claims_per_item INT NOT NULL,
            claimed_count INT NOT NULL DEFAULT 0,
            created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """,
        "CREATE INDEX idx_reward_items_pool ON reward_items(pool_id);",
        """
        CREATE TABLE IF NOT EXISTS reward_claims (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            user_id BIGINT NOT NULL,
            level_id INT NOT NULL,
            pool_id VARCHAR(64) NOT NULL,
            item_id VARCHAR(128) NOT NULL,
            reward_code TEXT NOT NULL,
            claimed_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            UNIQUE KEY uq_reward_claims_user_level (user_id, level_id),
            CONSTRAINT fk_reward_claims_user FOREIGN KEY (user_id) REFERENCES users(id),
            CONSTRAINT fk_reward_claims_item FOREIGN KEY (item_id) REFERENCES reward_items(item_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """,
        "CREATE INDEX idx_reward_claims_user ON reward_claims(user_id);",
        """
        CREATE TABLE IF NOT EXISTS log_events (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            trace_id VARCHAR(128) NOT NULL,
            event_type VARCHAR(32) NOT NULL,
            telegram_user_id BIGINT NOT NULL,
            chat_id BIGINT NOT NULL,
            level_id INT NULL,
            session_id BIGINT NULL,
            turn_index INT NULL,
            content TEXT NULL,
            metadata JSON NULL,
            created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """,
        "CREATE INDEX idx_log_events_trace ON log_events(trace_id);",
        "CREATE INDEX idx_log_events_created ON log_events(created_at);",
        "CREATE INDEX idx_log_events_user ON log_events(telegram_user_id);",
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            id TINYINT PRIMARY KEY,
            version INT NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """,
        """
        INSERT INTO schema_version (id, version)
        VALUES (1, 1)
        ON DUPLICATE KEY UPDATE version = GREATEST(version, 1);
        """,
    ],
    # Migration 2: Add pending_tasks table for queue persistence
    [
        """
        CREATE TABLE IF NOT EXISTS pending_tasks (
            id BIGINT PRIMARY KEY AUTO_INCREMENT,
            trace_id VARCHAR(128) NOT NULL UNIQUE,
            user_id BIGINT NOT NULL,
            telegram_user_id BIGINT NOT NULL,
            chat_id BIGINT NOT NULL,
            message_id BIGINT NOT NULL,
            username VARCHAR(255) NULL,
            level_id INT NOT NULL,
            session_id BIGINT NOT NULL,
            user_prompt TEXT NOT NULL,
            turn_index INT NOT NULL,
            enqueued_at DATETIME(6) NOT NULL,
            created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            CONSTRAINT fk_pending_tasks_user FOREIGN KEY (user_id) REFERENCES users(id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """,
        "CREATE INDEX idx_pending_tasks_enqueued ON pending_tasks(enqueued_at);",
        "UPDATE schema_version SET version = 2 WHERE id = 1;",
    ],
]


def get_mysql_migrations(from_version: int = 0) -> list[str]:
    """Get all mysql migrations from a given version."""
    statements: list[str] = []
    for migration in MYSQL_MIGRATIONS[from_version:]:
        statements.extend(migration)
    return statements


def get_mysql_current_version() -> int:
    """Get current mysql schema version."""
    return len(MYSQL_MIGRATIONS)

"""Tests for container storage backend selection."""

import pytest

from ..adapters.storage_mysql.mysql_storage import MySQLStorage
from ..adapters.storage_sqlite.sqlite_storage import SQLiteStorage
from ..app import container as container_module


class TestContainerStorageBackend:
    """Ensure container picks the expected storage backend."""

    def test_container_uses_sqlite_backend(self, monkeypatch, tmp_path):
        monkeypatch.setattr(container_module, "DATABASE_BACKEND", "sqlite")
        monkeypatch.setattr(container_module, "DATABASE_PATH", tmp_path / "backend_test.db")

        container = container_module.Container(use_mock_llm=True)

        assert isinstance(container.storage, SQLiteStorage)

    def test_container_uses_mysql_backend(self, monkeypatch):
        monkeypatch.setattr(container_module, "DATABASE_BACKEND", "mysql")
        monkeypatch.setattr(container_module, "MYSQL_HOST", "127.0.0.1")
        monkeypatch.setattr(container_module, "MYSQL_PORT", 3306)
        monkeypatch.setattr(container_module, "MYSQL_DATABASE", "activity")
        monkeypatch.setattr(container_module, "MYSQL_USER", "root")
        monkeypatch.setattr(container_module, "MYSQL_PASSWORD", "secret")
        monkeypatch.setattr(container_module, "MYSQL_CHARSET", "utf8mb4")
        monkeypatch.setattr(container_module, "MYSQL_CONNECT_TIMEOUT", 5.0)
        monkeypatch.setattr(container_module, "MYSQL_POOL_MIN_SIZE", 1)
        monkeypatch.setattr(container_module, "MYSQL_POOL_MAX_SIZE", 5)

        container = container_module.Container(use_mock_llm=True)

        assert isinstance(container.storage, MySQLStorage)
        assert container.storage.host == "127.0.0.1"
        assert container.storage.port == 3306
        assert container.storage.database == "activity"
        assert container.storage.user == "root"

    def test_container_rejects_invalid_backend(self, monkeypatch):
        monkeypatch.setattr(container_module, "DATABASE_BACKEND", "postgres")

        container = container_module.Container(use_mock_llm=True)

        with pytest.raises(ValueError, match="Unsupported DATABASE_BACKEND"):
            _ = container.storage

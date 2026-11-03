from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from server.models.schemas import ActionCard, ConversationSummary, Memo, MemoCreate, MemoUpdate, Priority, Task, TaskCreate, TaskStatus, TaskUpdate


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TaskRepository:
    """Small SQLite repository: portable, zero-service setup, and easy to replace later."""

    def __init__(self, database_path: str | Path = "data/workmate.db") -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
                    priority TEXT NOT NULL, due_at TEXT, status TEXT NOT NULL,
                    source TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )"""
            )
            # Additive migration: existing rows keep every value and simply get start_at = NULL.
            if "start_at" not in {row["name"] for row in connection.execute("PRAGMA table_info(tasks)")}:
                try:
                    connection.execute("ALTER TABLE tasks ADD COLUMN start_at TEXT")
                except sqlite3.OperationalError as error:  # another connection migrated first
                    if "duplicate column" not in str(error):
                        raise

    def list(self) -> list[Task]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tasks ORDER BY CASE status WHEN 'DONE' THEN 1 ELSE 0 END, due_at IS NULL, due_at, created_at DESC"
            ).fetchall()
        return [self._to_task(row) for row in rows]

    def create(self, payload: TaskCreate) -> Task:
        now = utcnow()
        task = Task(
            id=str(uuid4()), title=payload.title, description=payload.description,
            priority=payload.priority, start_at=payload.start_at, due_at=payload.due_at, status=TaskStatus.TODO,
            source=payload.source, created_at=now, updated_at=now,
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO tasks (id, title, description, priority, start_at, due_at, status, source, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task.id, task.title, task.description, task.priority.value,
                 task.start_at.isoformat() if task.start_at else None,
                 task.due_at.isoformat() if task.due_at else None, task.status.value,
                 task.source, task.created_at.isoformat(), task.updated_at.isoformat()),
            )
        return task

    def update_status(self, task_id: str, status: TaskStatus) -> Task | None:
        now = utcnow().isoformat()
        with self._connect() as connection:
            cursor = connection.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (status.value, now, task_id))
            if not cursor.rowcount:
                return None
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._to_task(row)

    def get(self, task_id: str) -> Task | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._to_task(row) if row else None

    def update(self, task_id: str, payload: TaskUpdate) -> Task | None:
        changes = payload.model_dump(exclude_unset=True)
        # Explicit null is only meaningful for the optional dates; required columns keep their value.
        columns = {key: value for key, value in changes.items() if key in ("start_at", "due_at") or value is not None}
        values: dict[str, object] = {}
        for key, value in columns.items():
            if key in ("start_at", "due_at"):
                values[key] = value.isoformat() if value else None
            else:
                values[key] = value.value if hasattr(value, "value") else value
        values["updated_at"] = utcnow().isoformat()
        assignments = ", ".join(f"{column} = ?" for column in values)
        with self._connect() as connection:
            cursor = connection.execute(f"UPDATE tasks SET {assignments} WHERE id = ?", (*values.values(), task_id))
            if not cursor.rowcount:
                return None
            row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return self._to_task(row)

    def delete(self, task_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            # Memos survive their task; only the optional link is cleared.
            if cursor.rowcount and connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memos'").fetchone():
                connection.execute("UPDATE memos SET task_id = NULL WHERE task_id = ?", (task_id,))
        return bool(cursor.rowcount)

    @staticmethod
    def _to_task(row: sqlite3.Row) -> Task:
        return Task(
            id=row["id"], title=row["title"], description=row["description"], priority=Priority(row["priority"]),
            start_at=datetime.fromisoformat(row["start_at"]) if row["start_at"] else None,
            due_at=datetime.fromisoformat(row["due_at"]) if row["due_at"] else None,
            status=TaskStatus(row["status"]), source=row["source"],
            created_at=datetime.fromisoformat(row["created_at"]), updated_at=datetime.fromisoformat(row["updated_at"]),
        )


def _like(query: str) -> str:
    return "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class MemoRepository:
    """Independent notes in the same SQLite file; task_id is an optional soft link to tasks."""

    def __init__(self, database_path: str | Path = "data/workmate.db") -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS memos (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, content TEXT NOT NULL DEFAULT '',
                    task_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                )"""
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_memos_updated ON memos(updated_at DESC)")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    _SELECT = "SELECT m.*, t.title AS task_title FROM memos m LEFT JOIN tasks t ON t.id = m.task_id"

    def task_exists(self, task_id: str) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1 FROM tasks WHERE id = ?", (task_id,)).fetchone() is not None

    def list(self, query: str | None = None, task_id: str | None = None) -> list[Memo]:
        clauses, params = [], []
        if query and query.strip():
            clauses.append("(m.title LIKE ? ESCAPE '\\' OR m.content LIKE ? ESCAPE '\\')")
            params += [_like(query.strip())] * 2
        if task_id:
            clauses.append("m.task_id = ?")
            params.append(task_id)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(f"{self._SELECT}{where} ORDER BY m.updated_at DESC", params).fetchall()
        return [self._to_memo(row) for row in rows]

    def get(self, memo_id: str) -> Memo | None:
        with self._connect() as connection:
            row = connection.execute(f"{self._SELECT} WHERE m.id = ?", (memo_id,)).fetchone()
        return self._to_memo(row) if row else None

    def create(self, payload: MemoCreate) -> Memo:
        now = utcnow().isoformat()
        memo_id = str(uuid4())
        with self._connect() as connection:
            connection.execute("INSERT INTO memos VALUES (?, ?, ?, ?, ?, ?)", (memo_id, payload.title, payload.content, payload.task_id, now, now))
        return self.get(memo_id)  # type: ignore[return-value]

    def update(self, memo_id: str, payload: MemoUpdate) -> Memo | None:
        changes = payload.model_dump(exclude_unset=True)
        # task_id may be explicitly null to unlink; title/content cannot be null.
        values = {key: value for key, value in changes.items() if key == "task_id" or value is not None}
        values["updated_at"] = utcnow().isoformat()
        assignments = ", ".join(f"{column} = ?" for column in values)
        with self._connect() as connection:
            cursor = connection.execute(f"UPDATE memos SET {assignments} WHERE id = ?", (*values.values(), memo_id))
        return self.get(memo_id) if cursor.rowcount else None

    def delete(self, memo_id: str) -> bool:
        with self._connect() as connection:
            return bool(connection.execute("DELETE FROM memos WHERE id = ?", (memo_id,)).rowcount)

    @staticmethod
    def _to_memo(row: sqlite3.Row) -> Memo:
        return Memo(
            id=row["id"], title=row["title"], content=row["content"], task_id=row["task_id"], task_title=row["task_title"],
            created_at=datetime.fromisoformat(row["created_at"]), updated_at=datetime.fromisoformat(row["updated_at"]),
        )


DEFAULT_TITLE = "새 대화"
TITLE_LENGTH = 40


class ConversationRepository:
    """Assistant conversations in the same SQLite file. Rows hold display data; secrets and raw tool output never land here."""

    def __init__(self, database_path: str | Path = "data/workmate.db") -> None:
        self.path = Path(database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    seq INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
                    trace TEXT, sources TEXT, action TEXT, action_id TEXT, created_at TEXT NOT NULL,
                    UNIQUE(conversation_id, seq)
                );
                CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_conv_msg_action ON conversation_messages(action_id);"""
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def ensure(self, conversation_id: str) -> str:
        now = utcnow().isoformat()
        with self._connect() as connection:
            connection.execute("INSERT OR IGNORE INTO conversations VALUES (?, ?, ?, ?)", (conversation_id, DEFAULT_TITLE, now, now))
        return conversation_id

    def add_message(self, conversation_id: str, role: str, content: str, trace: list | None = None, sources: list | None = None, action: ActionCard | None = None) -> dict:
        message_id, now = str(uuid4()), utcnow().isoformat()
        with self._connect() as connection:
            seq = connection.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM conversation_messages WHERE conversation_id = ?", (conversation_id,)).fetchone()[0]
            connection.execute(
                "INSERT INTO conversation_messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (message_id, conversation_id, seq, role, content,
                 json.dumps([t.model_dump(mode="json") for t in trace], ensure_ascii=False) if trace else None,
                 json.dumps(sources, ensure_ascii=False) if sources else None,
                 action.model_dump_json() if action else None, action.action_id if action else None, now),
            )
            connection.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
            if role == "user":  # the first user message names the conversation
                connection.execute("UPDATE conversations SET title = ? WHERE id = ? AND title = ?", (content.strip().replace("\n", " ")[:TITLE_LENGTH] or DEFAULT_TITLE, conversation_id, DEFAULT_TITLE))
        return {"id": message_id, "seq": seq}

    def context(self, conversation_id: str, limit: int) -> list[dict[str, str]]:
        """The most recent messages in chronological order: only what the model needs (role and text)."""
        with self._connect() as connection:
            rows = connection.execute("SELECT role, content FROM conversation_messages WHERE conversation_id = ? ORDER BY seq DESC LIMIT ?", (conversation_id, limit)).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    def list(self) -> list[ConversationSummary]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM conversation_messages m WHERE m.conversation_id = c.id) AS message_count FROM conversations c ORDER BY c.updated_at DESC"
            ).fetchall()
        return [self._summary(row) for row in rows]

    def get(self, conversation_id: str) -> tuple[ConversationSummary, list[sqlite3.Row]] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM conversation_messages m WHERE m.conversation_id = c.id) AS message_count FROM conversations c WHERE c.id = ?", (conversation_id,)
            ).fetchone()
            if not row:
                return None
            messages = connection.execute("SELECT * FROM conversation_messages WHERE conversation_id = ? ORDER BY seq", (conversation_id,)).fetchall()
        return self._summary(row), messages

    def delete(self, conversation_id: str) -> bool:
        with self._connect() as connection:
            connection.execute("DELETE FROM conversation_messages WHERE conversation_id = ?", (conversation_id,))
            return bool(connection.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,)).rowcount)

    def find_by_action(self, action_id: str) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute("SELECT * FROM conversation_messages WHERE action_id = ?", (action_id,)).fetchone()

    def update_action(self, message_id: str, action: ActionCard) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE conversation_messages SET action = ? WHERE id = ?", (action.model_dump_json(), message_id))

    @staticmethod
    def _summary(row: sqlite3.Row) -> ConversationSummary:
        return ConversationSummary(id=row["id"], title=row["title"], created_at=datetime.fromisoformat(row["created_at"]),
                                   updated_at=datetime.fromisoformat(row["updated_at"]), message_count=row["message_count"])

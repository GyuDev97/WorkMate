from datetime import datetime

from server.database.repository import TaskRepository
from server.models.schemas import check_period, Priority, PriorityItem, Task, TaskCreate, TaskStatus, TaskSummary, TaskUpdate, TaskView

CLOSED = {TaskStatus.DONE, TaskStatus.CANCELLED}


def _local(value: datetime) -> datetime:
    # Naive timestamps (older rows, manual edits) are interpreted in the user's local time zone.
    return value.astimezone()


class TaskService:
    def __init__(self, repository: TaskRepository) -> None:
        self.repository = repository

    def list_tasks(self, view: TaskView = "all", query: str | None = None) -> list[Task]:
        now = datetime.now().astimezone()
        needle = (query or "").strip().casefold()
        tasks = []
        for task in self.repository.list():
            if view == "active" and task.status in CLOSED:
                continue
            if view == "done" and task.status != TaskStatus.DONE:
                continue
            if view == "overdue" and not self.is_overdue(task, now):
                continue
            if view == "due_today" and (task.status in CLOSED or not self._due_today(task, now)):
                continue
            if view == "high_priority" and (task.status in CLOSED or task.priority != Priority.HIGH):
                continue
            if needle and needle not in task.title.casefold() and needle not in task.description.casefold():
                continue
            tasks.append(task)
        return tasks

    def get_task(self, task_id: str) -> Task | None:
        return self.repository.get(task_id)

    def create_task(self, payload: TaskCreate) -> Task:
        return self.repository.create(payload)

    def update_task(self, task_id: str, payload: TaskUpdate) -> Task | None:
        changes = payload.model_dump(exclude_unset=True)
        if "start_at" in changes or "due_at" in changes:
            # A partial update must still leave a consistent period (e.g. moving only the start past the stored due date).
            current = self.repository.get(task_id)
            if current is not None:
                check_period(changes["start_at"] if "start_at" in changes else current.start_at,
                             changes["due_at"] if "due_at" in changes else current.due_at)
        return self.repository.update(task_id, payload)

    def delete_task(self, task_id: str) -> bool:
        return self.repository.delete(task_id)

    def complete_task(self, task_id: str) -> Task | None:
        return self.repository.update_status(task_id, TaskStatus.DONE)

    def reopen_task(self, task_id: str) -> Task | None:
        return self.repository.update_status(task_id, TaskStatus.TODO)

    @staticmethod
    def is_overdue(task: Task, now: datetime) -> bool:
        return task.status not in CLOSED and task.due_at is not None and _local(task.due_at) < now

    def summary(self, tasks: list[Task] | None = None, now: datetime | None = None) -> TaskSummary:
        tasks = self.repository.list() if tasks is None else tasks
        now = now or datetime.now().astimezone()
        active = [task for task in tasks if task.status not in CLOSED]
        return TaskSummary(
            total=len(tasks), active=len(active), done=sum(task.status == TaskStatus.DONE for task in tasks),
            overdue=sum(self.is_overdue(task, now) for task in active),
            due_today=sum(self._due_today(task, now) for task in active),
            high_priority=sum(task.priority == Priority.HIGH for task in active),
        )

    @staticmethod
    def _due_today(task: Task, now: datetime) -> bool:
        return task.due_at is not None and _local(task.due_at).date() == now.date() and _local(task.due_at) >= now

    def priority_items(self, tasks: list[Task] | None = None, now: datetime | None = None, limit: int = 5) -> list[PriorityItem]:
        """Overdue first, then due today, then remaining high-priority work; each task appears once."""
        tasks = self.repository.list() if tasks is None else tasks
        now = now or datetime.now().astimezone()
        rank = {Priority.HIGH: 0, Priority.MEDIUM: 1, Priority.LOW: 2}
        items: list[tuple[int, float, PriorityItem]] = []
        for task in tasks:
            if task.status in CLOSED:
                continue
            if self.is_overdue(task, now):
                reason, order = "overdue", 0
            elif self._due_today(task, now):
                reason, order = "due_today", 1
            elif task.priority == Priority.HIGH:
                reason, order = "high_priority", 2
            else:
                continue
            due = _local(task.due_at).timestamp() if task.due_at else float('inf')
            items.append((order * 10 + rank[task.priority], due, PriorityItem(task_id=task.id, title=task.title, reason=reason, priority=task.priority, due_at=task.due_at)))
        items.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in items[:limit]]

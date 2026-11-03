from server.database.repository import MemoRepository
from server.models.schemas import Memo, MemoCreate, MemoUpdate


class UnknownTaskError(ValueError):
    pass


class MemoService:
    def __init__(self, repository: MemoRepository) -> None:
        self.repository = repository

    def _check_task(self, task_id: str | None) -> None:
        if task_id and not self.repository.task_exists(task_id):
            raise UnknownTaskError("연결할 업무를 찾을 수 없습니다.")

    def list_memos(self, query: str | None = None, task_id: str | None = None) -> list[Memo]:
        return self.repository.list(query, task_id)

    def get_memo(self, memo_id: str) -> Memo | None:
        return self.repository.get(memo_id)

    def create_memo(self, payload: MemoCreate) -> Memo:
        self._check_task(payload.task_id)
        return self.repository.create(payload)

    def update_memo(self, memo_id: str, payload: MemoUpdate) -> Memo | None:
        self._check_task(payload.task_id)
        return self.repository.update(memo_id, payload)

    def delete_memo(self, memo_id: str) -> bool:
        return self.repository.delete(memo_id)

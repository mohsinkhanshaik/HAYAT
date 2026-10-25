from app.memory.database import MemoryDatabase


class SemanticMemory:
    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database

    def remember_fact(self, content: str, source: str = "user", importance: float = 0.7, task_id: str | None = None) -> int:
        return self.database.store_memory("semantic", content, importance, source, task_id)

    def search(self, query: str, limit: int = 8):
        return self.database.retrieve(query, "semantic", limit)

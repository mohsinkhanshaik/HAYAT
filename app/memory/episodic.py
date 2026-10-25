from app.memory.database import MemoryDatabase


class EpisodicMemory:
    def __init__(self, database: MemoryDatabase) -> None:
        self.database = database

    def remember(self, content: str, task_id: str, importance: float = 0.6, **metadata) -> int:
        return self.database.store_memory("episodic", content, importance, "task", task_id, metadata)

    def recall(self, query: str, limit: int = 8):
        return self.database.retrieve(query, "episodic", limit)

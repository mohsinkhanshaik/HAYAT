from app.config import ensure_data_directories
from app.memory.database import MemoryDatabase


if __name__ == "__main__":
    ensure_data_directories()
    database = MemoryDatabase()
    database.initialize()
    print(f"Memory ready: {database.path}")

"""Editable planning drafts, separate from the executor's live task records."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path


def ordered_nodes(nodes):
    if not isinstance(nodes, list) or len(nodes) > 100:
        raise ValueError("A plan must contain at most 100 steps")
    indexed = {}
    for node in nodes:
        if not isinstance(node, dict):
            raise ValueError("Each step must be an object")
        key, goal, dependencies = node.get("id"), node.get("goal"), node.get("depends_on", [])
        if not isinstance(key, str) or not key.isdecimal() or len(key) > 5 or key in indexed:
            raise ValueError("Step IDs must be unique numeric labels")
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 4000:
            raise ValueError("Each step needs a description of 1–4,000 characters")
        if not isinstance(dependencies, list) or any(not isinstance(item, str) for item in dependencies):
            raise ValueError("Dependencies must be step IDs")
        indexed[key] = {"id": key, "goal": goal.strip(), "depends_on": list(dict.fromkeys(dependencies))}
    for node in indexed.values():
        if any(key not in indexed for key in node["depends_on"]):
            raise ValueError("A dependency refers to a missing step")
    result, done = [], set()
    while len(result) < len(nodes):
        ready = [node for key, node in indexed.items() if key not in done and set(node["depends_on"]) <= done]
        if not ready:
            raise ValueError("Dependencies contain a cycle")
        result.extend(ready)
        done.update(node["id"] for node in ready)
    return result


def plan_text(nodes):
    steps = ordered_nodes(nodes)
    return "Please carry out this plan in dependency order, verifying each step before its dependents:\n" + "\n".join(
        f"{node['id']}. {node['goal']} (after: {', '.join(node['depends_on']) or 'none'})" for node in steps)


class GraphStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                db.execute("CREATE TABLE IF NOT EXISTS graph_drafts (name TEXT PRIMARY KEY, nodes TEXT NOT NULL)")
                yield db
        finally:
            db.close()

    def save(self, name, nodes):
        name = name.strip()
        if not name or len(name) > 100:
            raise ValueError("Give the plan a name of 1–100 characters")
        data = json.dumps(ordered_nodes(nodes))
        with self.connect() as db:
            if db.execute("SELECT count(*) FROM graph_drafts").fetchone()[0] >= 100 and not db.execute(
                    "SELECT 1 FROM graph_drafts WHERE name=?", (name,)).fetchone():
                raise ValueError("Draft limit reached; delete an old plan first")
            db.execute("INSERT OR REPLACE INTO graph_drafts VALUES (?,?)", (name, data))

    def load(self, name):
        with self.connect() as db:
            row = db.execute("SELECT nodes FROM graph_drafts WHERE name=?", (name,)).fetchone()
        if row is None:
            raise ValueError("Saved plan not found")
        return ordered_nodes(json.loads(row[0]))

    def names(self):
        with self.connect() as db:
            return [row[0] for row in db.execute("SELECT name FROM graph_drafts ORDER BY name LIMIT 100")]

    def delete(self, name):
        with self.connect() as db:
            db.execute("DELETE FROM graph_drafts WHERE name=?", (name,))

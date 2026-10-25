
from app.agents.manager import ManagerAgent


class Core:
    def run(self, goal):
        return ("core", goal)


class Specialist:
    def __init__(self, name):
        self.name = name

    def run(self, goal):
        return (self.name, goal)


def test_manager_routes_and_explicitly_delegates():
    manager = ManagerAgent(Core(), {"coder": Specialist("coder"), "researcher": Specialist("researcher")})
    assert manager.route("fix Python code")[0] == "coder"
    assert manager.delegate("researcher", "research sources") == ("researcher", "research sources")
    assert manager.roles() == ["coder", "researcher"]


def test_delegation_preserves_intent_and_session_context():
    from unittest.mock import Mock
    worker = Mock()
    manager = ManagerAgent(Core(), {'researcher': worker})
    intent, session = object(), object()
    manager.delegate('researcher', 'Check sources', intent=intent, session=session)
    worker.run.assert_called_once_with('Check sources', intent=intent, session=session)

from app.agents.manager import SpecialistAgent


class ComputerAgent(SpecialistAgent):
    def __init__(self, core):
        super().__init__("computer", core)

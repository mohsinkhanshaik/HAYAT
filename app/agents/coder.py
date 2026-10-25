from app.agents.manager import SpecialistAgent


class CodingAgent(SpecialistAgent):
    def __init__(self, core):
        super().__init__("coder", core)

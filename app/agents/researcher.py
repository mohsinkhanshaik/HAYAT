from app.agents.manager import SpecialistAgent


class ResearchAgent(SpecialistAgent):
    def __init__(self, core):
        super().__init__("researcher", core)

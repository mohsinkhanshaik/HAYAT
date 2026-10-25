from app.agents.manager import SpecialistAgent


class ReviewerAgent(SpecialistAgent):
    def __init__(self, core):
        super().__init__("reviewer", core)

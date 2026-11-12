"""Where each Brain 5D subsystem sits (shared by the GUI and app/brain_population.py, which has no Tk)."""

#: Each layer has its own region of the brain: the interface faces the
#: viewer, control runs along the top, knowledge sits at the back, sensors and
#: network at the sides. x right, y down, z towards the viewer. The positions
#: were then spread by a search that kept every part near its region and
#: inside the shell while pushing parts apart on screen at the home view and
#: nearby angles, so names and discs no longer pile on each other.
LAYOUT: dict[str, tuple[float, float, float]] = {
    "interface": (.13, .54, .67),
    "intent": (-.44, .07, .67),
    "context": (-.66, -.46, .45),
    "router": (.52, -.14, .83),
    "helpers": (.67, -.44, .5),
    "planner": (0.0, -.46, .23),
    "tool_judgment": (.31, -.73, .01),
    "authority": (-.39, -.7, .07),
    "tools": (.63, -.57, -.33),
    "verifier": (-.05, -.76, .01),
    "memory": (-.8, .1, -.45),
    "dna": (-.18, .21, -.91),
    "research": (.49, .18, -.82),
    "learning": (-.59, .62, .08),
    "network": (.94, .28, -.14),
    "perception": (-.91, -.11, .42),
    "observability": (.3, .63, -.32),
    "cortex": (-.6, -.65, .24),
    "dual_brain": (.94, -.37, -.28),
    "invention": (.38, .66, .44),
    "inquiry": (.8, .55, -.05),
}

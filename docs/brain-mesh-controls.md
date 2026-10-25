# Brain mesh controls

Open **Operator** in HAYAT. The graph represents 20 real
subsystems and their 29 directed contracts. These are software connections, not
the language model's individual neurons or weights.

The Prefrontal Cortex, Dual Brain and Invention nodes connect the executive
controller, critic/revision workflow and persistent experiment workbench.
Double-click these nodes to open **Invention**. See the
[invention workbench guide](invention-workbench.md) for their behavior and limits.

Use **Appearance** to reveal model, surface, detail and standing-wave controls.
**Show details / Hide details** controls the inspector. On narrower panes,
selecting a node opens its details at full width; **Show graph** returns to the
mesh. See the GUI completion notes for the
responsive workspace and Training review improvements.

- **Model:** Sketchfab brain is the default bundled Human Brain model by
  3DRT STUDIOS, adapted under CC BY 4.0. Procedural selects the previous shell.
  Both work offline. **Model credits** opens attribution and source links.
  Missing or invalid bundled geometry falls back to the procedural shell with
  a visible message. See asset and verification notes.
- **Surface:** Folded, Wireframe, Standing waves, or Hidden. Hidden removes the
  scaffold while keeping the subsystem links and health indicators.
  Folded uses directional shading without triangle outlines; use Wireframe to
  inspect individual triangles. Node labels retain contrasting backgrounds.
- **Detail:** Low has 384 triangles, Normal 1,536, High 2,560. Increasing this
  changes rendering detail only; Low reduces the work required during rotation.
- **Standing waves:** choose 2/3, 3/5 or 4/7 mode orders. Gold marks near-zero
  regions of a static mathematical colour field. The canvas and controls label
  it as a cymatics illustration, not neural activity. Health colours stay on the
  actual nodes; only real running work produces moving pulses.
- **Dependency depth / Focus links:** explore the existing one-, two- or
  three-hop neighborhood. Increasing the inspection depth reveals existing
  contracts; it does not create new neural connections.
- **Arrows:** directions now remain visible outside node discs. Opposite
  contracts curve apart, and active-work pulses follow the same paths.

The cymatics analogy comes from physical standing-wave nodal patterns. It does
not establish a way to increase language-model intelligence. See the physical
[Chladni plate experiment](https://www.nature.com/articles/ncomms12764).

The Training page now shows the exact conversation-corpus scope, required count,
split counts and checkpoint blockers. Its filters wrap on narrow windows. The
Models page distinguishes a Colibri model advertised by a gateway from an
installed or loaded Ollama model; availability does not establish quality.

See [Colibri integration plan](colibri-integration-plan.md) for the inference and
fine-tuning work that would actually need benchmark evidence.

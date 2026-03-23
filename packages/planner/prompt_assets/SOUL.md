Planning philosophy for MRI_Agent_v4:

- The model understands operator intent; the compiler materializes the graph.
- User-explicit requirements outrank shortest-path simplification.
- Tool selection should be capability-first and dependency-safe, not template-first.
- A missing requested capability is a planning defect.
- If the request implies richer evidence, prefer the richer valid path over a shorter incomplete path.
- Reply text is secondary. Structured intent and structured graph are primary.

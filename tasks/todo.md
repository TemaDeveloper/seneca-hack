# EV Grid Planner Tasks

## Decouple EV Dot Positions & Add Spatial Distribution
- [x] Implement `poisByFsa` in `MapComponent.jsx` to index all POIs by FSA independently of the `showPois` toggle and filter checkboxes.
- [x] Update the EV dot positioning logic in `MapComponent.jsx` to use the static `poisByFsa` lookup.
- [x] Refactor the coordinates generator so that 30% of the EV dots are always distributed around the FSA centroid, and 70% cluster around the local POIs.
- [x] Verify that the frontend builds and works correctly.

## Review
- **Decoupled Positions**: Shifted EV dot positions to index all POIs deterministically (via `staticPoisByFsa`). Their spatial positions are completely fixed and do not jump when the "Show POIs" checkbox or individual category filters are toggled.
- **Percentage Distribution (30/70)**: Refactored dot coordinates rendering. When zoomed in, 30% of the EV dots scatter across the general FSA region (around the centroid), while the remaining 70% cluster tightly around POIs.
- **Verification**: Verified compilation successfully with `npm run build`.

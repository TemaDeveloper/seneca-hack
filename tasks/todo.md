# EV Grid Planner Tasks

## Gaussian Distribution & SVG Sidebar Toggles
- [x] Implement a `gaussianRandom` helper function in `MapComponent.jsx` using the Box-Muller transform.
- [x] Refactor the tight POI clustering jitter in `MapComponent.jsx` to use the Gaussian distribution (focusing dots near the center of POIs).
- [x] Refactor the broad FSA centroid scattering jitter in `MapComponent.jsx` to use the Gaussian distribution for a more organic population density spread.
- [x] Replace emojis in the POI toggle checkboxes of `App.jsx` with clean inline vector SVGs that match the map markers.
- [x] Verify that the frontend builds and works correctly.

## Review
- **Box-Muller Transform**: Added a deterministic Gaussian random variable generator to achieve a normal distribution.
- **Organic Density Scatter**: Refactored the EV dots jitter. High-density zones concentrate dots tightly at center entrances of POIs ($\sigma \approx 30\text{m}$) and scatter them gracefully in general residential margins ($\sigma \approx 1\text{km}$), which looks highly realistic.
- **Inline SVGs Toggles**: Replaced all emojis inside the `App.jsx` POI selection checkboxes with inline vector SVGs that match the map symbols.
- **Verification**: Verified compilation successfully with `npm run build`.

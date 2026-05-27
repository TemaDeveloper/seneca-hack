# EV Grid Planner Tasks

## Add New POI Types & Hover Bugfix
- [x] Fix the bug where hovering a zone (FSA polygon) hides the EV dots by rendering the dots on a higher Leaflet pane (`pane: 'markerPane'`).
- [x] Update `data_preparation/fetch_pois.py` to fetch "Shopping Malls & Retail Centers", "Transit Hubs & Park-and-Ride", and "High-Density Residential" from Overpass API.
- [x] Run `fetch_pois.py` to regenerate `gta_pois.json`.
- [x] Add vector SVGs for new POI categories (`retail`, `transit`, `residential`) in `MapComponent.jsx`.
- [x] Update filtering/rendering logic in `MapComponent.jsx` for the new POI categories.
- [x] Update filter checkboxes and styling in `App.jsx` to let users toggle the new categories.
- [x] Verify that the frontend builds and works correctly.

## Review
- **Hover Bugfix**: The EV dots are now drawn on `pane: 'markerPane'` inside Leaflet, preventing the hovered FSA polygon overlay from drawing on top and hiding the dots.
- **New POIs Added**: Successfully fetched and integrated three new categories of attraction points (Malls & Retail, Transit Hubs, and Residential Apartments).
- **Validation**: Frontend successfully built with `npm run build` showing zero compilation errors.


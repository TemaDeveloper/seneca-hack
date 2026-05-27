# PR: Feature/Premium Frontend — Native React Leaflet Map & Interactive Grid Dashboard

## Description
This PR replaces the static Streamlit/Folium HTML frontend with a highly interactive, responsive, and styled React + Vite dashboard. By moving map rendering to a native Leaflet implementation in React (`react-leaflet`), we enable instantaneous layer switching, interactive hover tooltips, custom context menus, and a direct editing workflow for charging station prescriptions.

## Key Features Added
1. **Native React Map Engine:**
   - Powered by `react-leaflet` with smooth zoom and panning.
   - Dynamic region styling for GTA FSA zones based on simulated grid metrics.
   - Three toggleable views: **Energy Spikes (Demand)**, **Grid Vulnerability (Overloads)**, and **Placements**.
   - Scattered EV markers dynamically rendered on the demand layer when **Show Cars** is active.
2. **Context-Menu EV Details:**
   - Right-clicking any FSA on the map opens a detailed popup table listing sample EV arrival times, vehicle IDs, and required State of Charge (SoC), locked behind the "Show Cars" toggle.
3. **Interactive Custom Charger Editor:**
   - A dedicated editor to manually add or remove chargers (`DC Fast Charging` or `Level 2`) by FSA code and view updated infrastructure prescriptions instantly.
4. **Resilient Frontend architecture:**
   - Implemented a global React `ErrorBoundary` to gracefully catch and display runtime render crashes.
   - Added user-friendly inline error banners for backend API connection drops.
   - Designed fullscreen loading overlays with micro-animations.
5. **Project Configurations & Fixes:**
   - Added missing FastAPI/Uvicorn/Pydantic packages to `pyproject.toml`.
   - Removed dead boilerplate CSS files and updated page titles.

---

## Files Changed
* `frontend/` (New React project structure including `App.jsx`, `MapComponent.jsx`, `api.js`, etc.)
* `pyproject.toml` (Added backend packages)
* `README.md` (Updated setup and API instructions)

---

## How to Test
1. **Backend:**
   Run `uv sync` to install dependencies, then start the server:
   ```bash
   uv run uvicorn backend.apis.api:app --reload --port 8000
   ```
2. **Frontend:**
   Go to the `frontend/` directory, install packages, and start the development server:
   ```bash
   npm install
   npm run dev
   ```
3. **Verification Steps:**
   * Verify the map loads and responds to the **Total EVs**, **Temperature**, and **Time of Day** sliders in the sidebar.
   * Toggle between **Energy Spikes** and **Grid Vulnerability** layers.
   * Check **Show Cars**, right-click any FSA, and verify the sample EV arrivals table displays.
   * Run **Placement Optimization**, then add a custom charger using the **Custom Charger Editor** to see it update.

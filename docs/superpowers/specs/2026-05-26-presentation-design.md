# Presentation Design Spec — Team CXC

**Date:** 2026-05-26
**Project:** EV Charging Demand & Grid Planning
**Hackathon:** Seneca Hackathon 2026
**Team:** CXC

---

## Overview

A 10-slide Reveal.js presentation (3-5 minutes) targeting hackathon judges and industry stakeholders. Cinematic story arc structure with modern animations, dark theme, and embedded dashboard screenshots.

## Format & Technology

- **Engine:** Reveal.js (HTML/JS, runs in browser)
- **Theme:** Dark (#0a0a0a background), electric blue (#00d4ff) + warning red (#ff3366) accents
- **Typography:** Inter / system sans-serif, 36-48px headings, max 3 text lines per slide
- **Animations:** CSS keyframes + Reveal.js fragment reveals
- **Screenshots:** Live captures from Streamlit app (demand heatmap, vulnerability, placement views)
- **Output:** Single `presentation/index.html` with inlined CSS/JS (portable, no build step)

## Slide-by-Slide Spec

### Slide 1: Title (10s)

- **Content:** "EV Charging Demand & Grid Planning" (large), "Team CXC" (medium), "Seneca Hackathon 2026" (small)
- **Visual:** Dark background, electric blue glow accent on title text
- **Animation:** Title fades in, then team name slides up from bottom

### Slide 2: The Hook (15s)

- **Content:** "By 2030, 1 in 3 cars on GTA roads will be electric. The power grid wasn't built for this."
- **Visual:** Large bold text, two sentences on separate lines, red accent on "wasn't built for this"
- **Animation:** First sentence appears, pause, second sentence fades in with red highlight

### Slide 3: The Problem (20s)

- **Content:** "Utilities don't know where charging demand will spike — until transformers blow."
- **Visual:** Animated grid of ~20 small squares. Start all green, then one by one they turn red (cascade failure animation, CSS keyframes, ~3 second loop)
- **Animation:** Grid squares transition green → red in a wave pattern. Text appears as fragment after grid animation settles.

### Slide 4: Our Solution — Overview (20s)

- **Content:** Three-phase pipeline: "Data Assembly → Monte Carlo Simulation → Optimal Placement"
- **Visual:** Three connected boxes/icons in a horizontal flow. Each box has an icon and label.
  - Box 1: Map icon + "260 GTA Zones" + "Real grid capacity"
  - Box 2: Dice icon + "30,000 EVs simulated" + "When, where, how much"
  - Box 3: Pin icon + "Optimal charger sites" + "Type, count, battery storage"
- **Animation:** Boxes appear one at a time left-to-right with a connecting arrow animating between them (fragment reveals)

### Slide 5: Monte Carlo — Explain Like I'm 5 (40s, 4 fragments)

- **Fragment 1:** "Drop 30,000 cars onto Toronto" — map outline of GTA appears (SVG or simplified shape)
- **Fragment 2:** "Each one lands in a neighborhood — weighted by real traffic data" — colored dots scatter onto the map (CSS animation, random positions weighted toward downtown)
- **Fragment 3:** "Each one arrives at a different time — morning commute, evening rush" — clock icon appears with a small time distribution curve
- **Fragment 4:** "Each one needs a different amount of power — some empty, some half-full" — battery icon with Gamma curve sketch
- **Visual:** Dark map background, neon dots, clean iconography
- **Animation:** Each fragment click adds a visual layer to the same map canvas

### Slide 6: Semi-Markov Decision Layer (30s)

- **Content:** "People aren't dice rolls — they make decisions. Snow day? Work from home? Each life event reshapes the city's energy demand."
- **Visual:** State diagram with 4 nodes: Home (house icon), Work (briefcase), School (book), Shopping (cart). Directed arrows between them with probability labels.
- **Animation:**
  - Nodes appear first
  - Arrows draw in with CSS stroke-dasharray animation
  - Fragment: "School cancelled" label appears, School node dims, arrow from Home→School fades, arrow Home→Home thickens (probability shift)
  - Arrows pulse/glow with subtle CSS animation

### Slide 7: Under the Hood (25s, 3 fragments)

- **Fragment 1:** Card slides up — "Spatial PDF" + "Real Toronto traffic volumes across 260 postal zones"
- **Fragment 2:** Card slides up — "Battery Model" + "Gamma distribution + winter penalty (up to 40% more demand at -15C)"
- **Fragment 3:** Card slides up — "Optimizer" + "Mixed-integer linear programming selects optimal sites under budget"
- **Visual:** Three dark cards with blue left-border accent, monospace label, regular description
- **Animation:** Cards slide in from bottom, staggered

### Slide 8: The Dashboard (25s)

- **Content:** Three live screenshots from Streamlit app:
  1. Energy Spikes (demand heatmap — yellow/orange/red)
  2. Grid Vulnerability (green vs red zones)
  3. Optimal Placement (green/red + purple markers)
- **Visual:** Triptych layout OR fragment reveals showing each view one at a time
- **Animation:** Each screenshot fades in with subtle scale-up (1.02 → 1.0 ease-out). Label beneath each: "Demand", "Vulnerability", "Solution"

### Slide 9: Impact (15s)

- **Content:**
  - "Pinpoints grid failures before they happen"
  - "Prescribes exactly where to build — charger type, count, and battery storage"
  - "Real data: Toronto Hydro feeders, Hydro One stations, IESO load profiles, City of Toronto traffic"
- **Visual:** Three large bold statements stacked vertically, blue accent on key words
- **Animation:** Lines appear one at a time (fragment reveals)

### Slide 10: Close (10s)

- **Content:** "Team CXC", "EV Charging Demand & Grid Planning", "Seneca Hackathon 2026", "Thank you"
- **Visual:** Centered, clean, same electric blue glow as title slide
- **Animation:** Gentle fade-in of all elements

## Screenshots to Capture

1. Launch `streamlit run app.py`
2. Run simulation with default settings (20% adoption, 20C, Full Day)
3. Screenshot View 1: Energy Spikes tab
4. Screenshot View 2: Grid Vulnerability tab
5. Run optimizer (10 stations)
6. Screenshot View 3: Optimal Placement tab
7. Crop/resize to 1920x1080 for slide embedding

## File Structure

```
presentation/
  index.html          # Single self-contained file (Reveal.js CDN-linked)
  screenshots/
    demand.png
    vulnerability.png
    placement.png
```

## Constraints

- No build step — open index.html in any browser
- Reveal.js loaded from CDN (jsdelivr)
- All CSS inlined in <style> block
- All JS inlined in <script> block
- Screenshots referenced as relative paths from presentation/screenshots/
- Total presentation time: 3-5 minutes
- Keyboard navigation: arrow keys or spacebar to advance

import React, { useState, useEffect, useMemo, useRef } from 'react';
import { runSimulation, runOptimization, runCustomPlacement } from './api';
import MapComponent from './MapComponent';
import './index.css';

function App() {
  const [params, setParams] = useState({
    num_cars: 15000,
    temperature: 20,
    time_of_day: 12,
    max_stations: 10
  });

  const [loading, setLoading] = useState(false);
  const [simData, setSimData] = useState(null);
  const [activeLayer, setActiveLayer] = useState('demand');
  const [optData, setOptData] = useState(null);
  const [customPlacements, setCustomPlacements] = useState([]);
  const [newFsa, setNewFsa] = useState('');
  const [newType, setNewType] = useState('DC Fast Charging Array');
  const [newUnits, setNewUnits] = useState(1);
  const [showCars, setShowCars] = useState(false);
  const [simVersion, setSimVersion] = useState(0);
  const [error, setError] = useState(null);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  // Attraction Points (POI) States
  const [showPois, setShowPois] = useState(false);
  const [poiFilters, setPoiFilters] = useState({
    hospitals: true,
    workplaces: true,
    schools: true,
    gyms: true,
    chargers: true,
    retail: true,
    transit: true,
    residential: true
  });

  const isInitialMount = useRef(true);
  const timerRef = useRef(null);

  const handleRunSimulation = async () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await runSimulation(params);
      setSimData(data);
      setSimVersion(v => v + 1);
      // If they already optimized, changing params resets it for now to avoid stale data
      if (activeLayer === 'placements' && optData) {
        setActiveLayer('demand');
      }
      setOptData(null);
      setCustomPlacements([]);
    } catch (e) {
      console.error(e);
      setError('Simulation failed. Is the backend running on port 8000?');
    }
    setLoading(false);
  };

  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false;
      handleRunSimulation();
      return;
    }

    timerRef.current = setTimeout(() => {
      handleRunSimulation();
    }, 500);

    return () => {
      if (timerRef.current) {
        clearTimeout(timerRef.current);
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.num_cars, params.temperature, params.time_of_day]);

  // Calculate Overloaded vs Borderline (Near Overload) zones
  const { overloadedCount, borderlineCount } = useMemo(() => {
    if (!simData || !simData.grid_data) return { overloadedCount: 0, borderlineCount: 0 };
    let overloaded = 0;
    let borderline = 0;
    simData.grid_data.forEach(row => {
      const ratio = row.total_load_kw / row.proxy_capacity_kw;
      if (ratio > 1.0) {
        overloaded++;
      } else if (ratio > 0.8) {
        borderline++;
      }
    });
    return { overloadedCount: overloaded, borderlineCount: borderline };
  }, [simData]);

  const handleOptimize = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await runOptimization(params);
      setOptData(data);
      setCustomPlacements(data.prescriptions.map(p => ({
        fsa: p.fsa,
        charger_type: p.charger_type,
        charger_units: p.charger_units
      })));
      setActiveLayer('placements');
    } catch (e) {
      console.error(e);
      setError('Optimization failed. Check the backend console for details.');
    }
    setLoading(false);
  };

  const handleAddCustom = () => {
    if (!newFsa) return;
    const existing = customPlacements.findIndex(p => p.fsa === newFsa);
    if (existing >= 0) {
      const updated = customPlacements.map((p, i) =>
        i === existing
          ? { ...p, charger_units: p.charger_units + newUnits }
          : p
      );
      setCustomPlacements(updated);
    } else {
      setCustomPlacements([...customPlacements, { fsa: newFsa, charger_type: newType, charger_units: newUnits }]);
    }
    setNewFsa('');
  };

  const handleRemoveCustom = (fsa) => {
    setCustomPlacements(customPlacements.filter(p => p.fsa !== fsa));
  };

  const handleApplyCustom = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await runCustomPlacement(params, customPlacements);
      setOptData(data);
      setActiveLayer('placements');
    } catch (e) {
      console.error(e);
      setError('Custom placement failed. Check the backend console for details.');
    }
    setLoading(false);
  };

  return (
    <div className="dashboard-container">
      {loading && (
        <div className="loading-overlay">
          <div className="loading-content">
            <div className="loading-spinner" />
            <span>Running simulation…</span>
          </div>
        </div>
      )}

      <div className={`sidebar ${sidebarCollapsed ? 'collapsed' : ''}`}>
        {/* Brutalist Collapse Sidebar Button */}
        <button
          onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
          style={{
            position: 'absolute',
            right: '-44px',
            top: '24px',
            width: '40px',
            height: '40px',
            backgroundColor: '#fff',
            border: '3px solid #000',
            borderLeft: 'none',
            boxShadow: sidebarCollapsed ? '4px 4px 0px #000' : 'none',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '16px',
            fontWeight: 'bold',
            zIndex: 100,
            transition: 'background-color 0.2s'
          }}
          title={sidebarCollapsed ? "Show Controls" : "Hide Controls"}
          onMouseEnter={(e) => e.target.style.backgroundColor = '#f0f0f0'}
          onMouseLeave={(e) => e.target.style.backgroundColor = '#fff'}
        >
          {sidebarCollapsed ? '▶' : '◀'}
        </button>

        <div className={`sidebar-content ${sidebarCollapsed ? 'collapsed' : ''}`}>
          <h2>Simulation Controls</h2>

          <div className="input-group">
            <label>Total EVs to Simulate</label>
            <input
              type="number" min="1000" max="100000" step="1000"
              value={params.num_cars}
              onChange={e => setParams({ ...params, num_cars: parseInt(e.target.value) || 0 })}
            />
          </div>

          <div className="input-group">
            <label>Temperature ({params.temperature}°C)</label>
            <input
              type="range" min="-20" max="30" step="5"
              value={params.temperature}
              onChange={e => setParams({ ...params, temperature: parseInt(e.target.value) })}
            />
          </div>

          <div className="input-group">
            <label>Time of Day ({params.time_of_day}:00)</label>
            <input
              type="range" min="0" max="23" step="1"
              value={params.time_of_day}
              onChange={e => setParams({ ...params, time_of_day: parseInt(e.target.value) })}
            />
          </div>

          <div className="input-group">
            <label>Max Charging Stations ({params.max_stations})</label>
            <input
              type="range" min="5" max="20" step="1"
              value={params.max_stations}
              onChange={e => setParams({ ...params, max_stations: parseInt(e.target.value) })}
            />
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', marginTop: '24px' }}>
            <button
              onClick={handleRunSimulation}
            >
              Run Simulation
            </button>

            <button
              onClick={handleOptimize}
              disabled={!simData}
            >
              Run Placement Optimization
            </button>
          </div>
        </div>
      </div>

      <div className="main-content">
        <h1>EV Grid Planner</h1>
        <p>Real-time Native React Map Engine</p>

        {error && (
          <div className="error-banner">
            <span>{error}</span>
            <button onClick={() => setError(null)} style={{ padding: '4px 12px', fontSize: '0.8rem', boxShadow: 'none', border: '2px solid currentColor' }}>✕</button>
          </div>
        )}

        {!simData && !error && (
          <div className="glass-panel">
            <h3>Loading initial simulation state...</h3>
          </div>
        )}

        {simData && (
          <>
            <div className="metrics-grid">
              <div className="glass-panel metric-card">
                <span className="metric-title">Total EVs</span>
                <span className="metric-value">{simData.ev_count.toLocaleString()}</span>
              </div>
              <div className="glass-panel metric-card">
                <span className="metric-title">Total Peak Demand</span>
                <span className="metric-value">{simData.total_peak_demand_mw.toFixed(1)} MW</span>
              </div>
              <div className="glass-panel metric-card" style={{ borderLeft: '8px solid var(--danger-color)' }}>
                <span className="metric-title">Overloaded Zones (Red)</span>
                <span className="metric-value">{overloadedCount}</span>
              </div>
              <div className="glass-panel metric-card" style={{ borderLeft: '8px solid #ffcc00' }}>
                <span className="metric-title">Borderline Zones (Yellow)</span>
                <span className="metric-value">{borderlineCount}</span>
              </div>
            </div>

            <div className="glass-panel" style={{ padding: '16px', marginBottom: '16px' }}>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '24px', alignItems: 'center' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <input
                    type="checkbox"
                    id="showPois"
                    checked={showPois}
                    onChange={e => setShowPois(e.target.checked)}
                  />
                  <label htmlFor="showPois" style={{ margin: 0, fontWeight: '800', textTransform: 'uppercase', cursor: 'pointer' }}>Show Attraction Points (POIs)</label>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <input
                    type="checkbox"
                    id="showCars"
                    checked={showCars}
                    onChange={e => setShowCars(e.target.checked)}
                  />
                  <label htmlFor="showCars" style={{ margin: 0, fontWeight: '800', textTransform: 'uppercase', cursor: 'pointer' }}>Show Cars</label>
                </div>

                {showPois && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '16px', borderLeft: '3px solid var(--border-color)', paddingLeft: '20px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <input
                        type="checkbox"
                        id="poiHospital"
                        checked={poiFilters.hospitals}
                        onChange={e => setPoiFilters({ ...poiFilters, hospitals: e.target.checked })}
                      />
                      <label htmlFor="poiHospital" style={{ margin: 0, cursor: 'pointer', fontSize: '0.95rem', display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#000" strokeWidth="3" style={{ filter: 'drop-shadow(1px 1px 0px #000)', flexShrink: 0 }}>
                          <rect x="3" y="3" width="18" height="18" fill="#fff" />
                          <path d="M12 7v10M7 12h10" />
                        </svg>
                        <span>Hospitals</span>
                      </label>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <input
                        type="checkbox"
                        id="poiWork"
                        checked={poiFilters.workplaces}
                        onChange={e => setPoiFilters({ ...poiFilters, workplaces: e.target.checked })}
                      />
                      <label htmlFor="poiWork" style={{ margin: 0, cursor: 'pointer', fontSize: '0.95rem', display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#000" strokeWidth="3" style={{ filter: 'drop-shadow(1px 1px 0px #000)', flexShrink: 0 }}>
                          <rect x="3" y="7" width="18" height="14" fill="#fff" />
                          <path d="M16 7V4H8v3" />
                        </svg>
                        <span>Workplaces</span>
                      </label>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <input
                        type="checkbox"
                        id="poiSchool"
                        checked={poiFilters.schools}
                        onChange={e => setPoiFilters({ ...poiFilters, schools: e.target.checked })}
                      />
                      <label htmlFor="poiSchool" style={{ margin: 0, cursor: 'pointer', fontSize: '0.95rem', display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#000" strokeWidth="2.5" style={{ filter: 'drop-shadow(1px 1px 0px #000)', flexShrink: 0 }}>
                          <polygon points="12 3 22 8 12 13 2 8" fill="#fff" />
                          <path d="M6 10v6c0 2 3 3 6 3s6-1 6-3v-6" fill="#fff" />
                        </svg>
                        <span>Schools</span>
                      </label>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <input
                        type="checkbox"
                        id="poiGym"
                        checked={poiFilters.gyms}
                        onChange={e => setPoiFilters({ ...poiFilters, gyms: e.target.checked })}
                      />
                      <label htmlFor="poiGym" style={{ margin: 0, cursor: 'pointer', fontSize: '0.95rem', display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#000" strokeWidth="3" style={{ filter: 'drop-shadow(1px 1px 0px #000)', flexShrink: 0 }}>
                          <path d="M6 12h12" />
                          <rect x="4" y="7" width="2" height="10" fill="#fff" />
                          <rect x="18" y="7" width="2" height="10" fill="#fff" />
                          <rect x="2" y="9" width="2" height="6" fill="#fff" />
                          <rect x="20" y="9" width="2" height="6" fill="#fff" />
                        </svg>
                        <span>Gyms</span>
                      </label>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <input
                        type="checkbox"
                        id="poiCharger"
                        checked={poiFilters.chargers}
                        onChange={e => setPoiFilters({ ...poiFilters, chargers: e.target.checked })}
                      />
                      <label htmlFor="poiCharger" style={{ margin: 0, cursor: 'pointer', fontSize: '0.95rem', display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#000" strokeWidth="2.5" style={{ filter: 'drop-shadow(1px 1px 0px #000)', flexShrink: 0 }}>
                          <polygon points="13 2 3 14 12 14 11 22 21 10 12 10" fill="#fff" />
                        </svg>
                        <span>Existing Chargers</span>
                      </label>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <input
                        type="checkbox"
                        id="poiRetail"
                        checked={poiFilters.retail}
                        onChange={e => setPoiFilters({ ...poiFilters, retail: e.target.checked })}
                      />
                      <label htmlFor="poiRetail" style={{ margin: 0, cursor: 'pointer', fontSize: '0.95rem', display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#000" strokeWidth="2.5" style={{ filter: 'drop-shadow(1px 1px 0px #000)', flexShrink: 0 }}>
                          <path d="M6 2L3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4H6z" fill="#fff" />
                          <path d="M3 6h18M16 10a4 4 0 0 1-8 0" />
                        </svg>
                        <span>Malls & Retail</span>
                      </label>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <input
                        type="checkbox"
                        id="poiTransit"
                        checked={poiFilters.transit}
                        onChange={e => setPoiFilters({ ...poiFilters, transit: e.target.checked })}
                      />
                      <label htmlFor="poiTransit" style={{ margin: 0, cursor: 'pointer', fontSize: '0.95rem', display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#000" strokeWidth="2.5" style={{ filter: 'drop-shadow(1px 1px 0px #000)', flexShrink: 0 }}>
                          <rect x="4" y="3" width="16" height="16" rx="2" fill="#fff" />
                          <path d="M4 11h16M8 15h.01M16 15h.01M6 19l-2 2M18 19l2 2" />
                        </svg>
                        <span>Transit Hubs</span>
                      </label>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <input
                        type="checkbox"
                        id="poiResidential"
                        checked={poiFilters.residential}
                        onChange={e => setPoiFilters({ ...poiFilters, residential: e.target.checked })}
                      />
                      <label htmlFor="poiResidential" style={{ margin: 0, cursor: 'pointer', fontSize: '0.95rem', display: 'flex', alignItems: 'center', gap: '6px' }}>
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#000" strokeWidth="2.5" style={{ filter: 'drop-shadow(1px 1px 0px #000)', flexShrink: 0 }}>
                          <rect x="3" y="2" width="18" height="20" fill="#fff" />
                          <path d="M7 6h2M7 10h2M7 14h2M7 18h2M15 6h2M15 10h2M15 14h2M15 18h2" />
                        </svg>
                        <span>Residential</span>
                      </label>
                    </div>
                  </div>
                )}
              </div>
            </div>

            <div className="map-controls glass-panel" style={{ padding: '12px', marginBottom: '16px', display: 'flex', gap: '16px', alignItems: 'center' }}>
              <h3 style={{ margin: 0 }}>Map Layer:</h3>
              <button
                style={{ padding: '8px 16px', background: activeLayer === 'demand' ? 'var(--text-main)' : 'var(--bg-main)', color: activeLayer === 'demand' ? 'var(--bg-main)' : 'var(--text-main)' }}
                onClick={() => setActiveLayer('demand')}
                title="Shows areas with the highest amount of EV charging demand (Yellow to Red)."
              >
                Energy Spikes (Demand)
              </button>
              <button
                style={{ padding: '8px 16px', background: activeLayer === 'vulnerability' ? 'var(--text-main)' : 'var(--bg-main)', color: activeLayer === 'vulnerability' ? 'var(--bg-main)' : 'var(--text-main)' }}
                onClick={() => setActiveLayer('vulnerability')}
                title="Shows where the grid is overloaded and will fail (Red = Overloaded, Yellow = Warning, Green = Safe)."
              >
                Grid Vulnerability (Overloads)
              </button>
              <button
                style={{ padding: '8px 16px', background: activeLayer === 'placements' ? 'var(--text-main)' : 'var(--bg-main)', color: activeLayer === 'placements' ? 'var(--bg-main)' : 'var(--text-main)' }}
                onClick={() => setActiveLayer('placements')}
                disabled={!optData}
                title="Shows optimal locations to build new charging stations."
              >
                Placements
              </button>
            </div>

            <div style={{ height: '600px', border: '4px solid var(--border-color)', marginBottom: '32px' }}>
              <MapComponent
                gridData={simData.grid_data}
                evData={simData.ev_data}
                layer={activeLayer}
                prescriptions={optData ? optData.prescriptions : null}
                showCars={showCars}
                simVersion={simVersion}
                showPois={showPois}
                poiFilters={poiFilters}
              />
            </div>
          </>
        )}

        {optData && (
          <div style={{ paddingTop: '32px', borderTop: '4px solid var(--border-color)' }}>
            <h2>Infrastructure Metrics</h2>

            <div className="metrics-grid">
              <div className="glass-panel metric-card">
                <span className="metric-title">Stations Deployed</span>
                <span className="metric-value">{optData.stations_deployed}</span>
              </div>
              <div className="glass-panel metric-card">
                <span className="metric-title">Total Charger Capacity</span>
                <span className="metric-value">{optData.total_charger_kw.toLocaleString()} kW</span>
              </div>
              <div className="glass-panel metric-card">
                <span className="metric-title">Total BESS</span>
                <span className="metric-value">{optData.total_bess_kwh.toLocaleString()} kWh</span>
              </div>
            </div>

            <div className="glass-panel">
              <h3>Custom Charger Editor</h3>
              <p style={{ marginBottom: '16px' }}>Modify the locations of charging stations. Add or remove stations, then apply changes to update the map.</p>

              <div className="editor-form">
                <div className="input-group">
                  <label>FSA Code (e.g. M5V)</label>
                  <input type="text" value={newFsa} onChange={e => setNewFsa(e.target.value.toUpperCase())} maxLength={3} placeholder="M5V" />
                </div>
                <div className="input-group">
                  <label>Type</label>
                  <select value={newType} onChange={e => setNewType(e.target.value)}>
                    <option value="DC Fast Charging Array">DC Fast Charging</option>
                    <option value="Level 2 Smart-Charging Hub">Level 2</option>
                  </select>
                </div>
                <div className="input-group">
                  <label>Units</label>
                  <input type="number" min="1" value={newUnits} onChange={e => setNewUnits(parseInt(e.target.value))} />
                </div>
                <button onClick={handleAddCustom} style={{ padding: '8px 16px', fontSize: '0.9rem' }}>Add Station</button>
              </div>

              {customPlacements.length > 0 && (
                <div style={{ marginBottom: '16px', overflowX: 'auto' }}>
                  <table>
                    <thead>
                      <tr>
                        <th>FSA</th>
                        <th>Type</th>
                        <th>Units</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {customPlacements.map((p, idx) => (
                        <tr key={idx}>
                          <td>{p.fsa}</td>
                          <td>{p.charger_type}</td>
                          <td>{p.charger_units}</td>
                          <td>
                            <button onClick={() => handleRemoveCustom(p.fsa)} style={{ padding: '4px 8px', fontSize: '0.8rem' }}>Remove</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <button onClick={handleApplyCustom} style={{ width: '100%' }}>Apply Custom Placements</button>
            </div>

          </div>
        )}
      </div>
    </div>
  );
}

export default App;

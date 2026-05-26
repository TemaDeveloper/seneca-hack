import React, { useState, useEffect } from 'react';
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

  const handleRunSimulation = async () => {
    setLoading(true);
    try {
      const data = await runSimulation(params);
      setSimData(data);
      // If they already optimized, changing params resets it for now to avoid stale data
      if (activeLayer === 'placements' && optData) {
        setActiveLayer('demand');
      }
      setOptData(null);
      setCustomPlacements([]);
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  };

  useEffect(() => {
    handleRunSimulation();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleOptimize = async () => {
    setLoading(true);
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
      alert('Optimization failed');
    }
    setLoading(false);
  };

  const handleAddCustom = () => {
    if (!newFsa) return;
    const existing = customPlacements.findIndex(p => p.fsa === newFsa);
    if (existing >= 0) {
      const updated = [...customPlacements];
      updated[existing].charger_units += newUnits;
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
    try {
      const data = await runCustomPlacement(params, customPlacements);
      setOptData(data);
      setActiveLayer('placements');
    } catch (e) {
      console.error(e);
      alert('Custom placement failed');
    }
    setLoading(false);
  };

  return (
    <div className="dashboard-container">
      {loading && <div className="loading-overlay">Syncing...</div>}

      <div className="sidebar">
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

      <div className="main-content">
        <h1>EV Grid Planner</h1>
        <p>Real-time Native React Map Engine</p>

        {!simData && (
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
              <div className="glass-panel metric-card">
                <span className="metric-title">Overloaded Zones</span>
                <span className="metric-value">{simData.overloaded_count} / {simData.total_fsas}</span>
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
                title="Shows where the grid is overloaded and will fail (Red = Overloaded, Green = Safe)."
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

              <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '8px' }}>
                <input
                  type="checkbox"
                  id="showCars"
                  checked={showCars}
                  onChange={e => setShowCars(e.target.checked)}
                />
                <label htmlFor="showCars" style={{ margin: 0, fontWeight: 'bold' }}>Show Cars</label>
              </div>
            </div>

            <div style={{ height: '600px', border: '4px solid var(--border-color)', marginBottom: '32px' }}>
              <MapComponent
                gridData={simData.grid_data}
                evData={simData.ev_data}
                layer={activeLayer}
                prescriptions={optData ? optData.prescriptions : null}
                showCars={showCars}
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

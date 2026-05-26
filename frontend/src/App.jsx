import React, { useState } from 'react';
import { runSimulation, runOptimization, runCustomPlacement } from './api';
import './index.css';

function App() {
  const [params, setParams] = useState({
    adoption_pct: 20,
    temperature: 20,
    time_of_day: 12,
    max_stations: 10
  });

  const [loading, setLoading] = useState(false);
  const [simData, setSimData] = useState(null);
  const [optData, setOptData] = useState(null);
  
  // Editor state
  const [customPlacements, setCustomPlacements] = useState([]);
  const [newFsa, setNewFsa] = useState('');
  const [newUnits, setNewUnits] = useState(1);
  const [newType, setNewType] = useState('DC Fast Charging Array');

  const handleSimulate = async () => {
    setLoading(true);
    try {
      const data = await runSimulation(params);
      setSimData(data);
      setOptData(null);
      setCustomPlacements([]);
    } catch (e) {
      console.error(e);
      alert('Simulation failed');
    }
    setLoading(false);
  };

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
    } catch (e) {
      console.error(e);
      alert('Custom placement failed');
    }
    setLoading(false);
  };

  return (
    <div className="dashboard-container">
      {loading && <div className="loading-overlay">Processing...</div>}
      
      <div className="sidebar">
        <h2>Simulation Controls</h2>
        
        <div className="input-group">
          <label>EV Adoption Rate ({params.adoption_pct}%)</label>
          <input 
            type="range" min="10" max="50" step="5" 
            value={params.adoption_pct} 
            onChange={e => setParams({...params, adoption_pct: parseInt(e.target.value)})} 
          />
        </div>

        <div className="input-group">
          <label>Temperature ({params.temperature}°C)</label>
          <input 
            type="range" min="-20" max="30" step="5" 
            value={params.temperature} 
            onChange={e => setParams({...params, temperature: parseInt(e.target.value)})} 
          />
        </div>

        <div className="input-group">
          <label>Time of Day ({params.time_of_day}:00)</label>
          <input 
            type="range" min="0" max="23" step="1" 
            value={params.time_of_day} 
            onChange={e => setParams({...params, time_of_day: parseInt(e.target.value)})} 
          />
        </div>

        <div className="input-group">
          <label>Max Charging Stations ({params.max_stations})</label>
          <input 
            type="range" min="5" max="20" step="1" 
            value={params.max_stations} 
            onChange={e => setParams({...params, max_stations: parseInt(e.target.value)})} 
          />
        </div>

        <div style={{ marginTop: 'auto', display: 'flex', flexDirection: 'column', gap: '16px' }}>
          <button onClick={handleSimulate}>Run Simulation</button>
          <button 
            onClick={handleOptimize} 
            disabled={!simData}
          >
            Optimize Placement
          </button>
        </div>
      </div>

      <div className="main-content">
        <h1>EV Grid Planner</h1>
        <p>Predictive Location Optimization & Grid Impact Model</p>

        {!simData && (
          <div className="glass-panel">
            <h3>Configure parameters and run simulation to begin.</h3>
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
                <span className="metric-title">Active EVs Charging</span>
                <span className="metric-value">{Math.floor(simData.ev_count * 0.15).toLocaleString()}</span>
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

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '32px' }}>
              <div>
                <h2>1. Energy Spikes</h2>
                <div className="map-container">
                  <iframe 
                    srcDoc={simData.demand_map_html} 
                    style={{ width: '100%', height: '100%', border: 'none' }}
                    title="Demand Map"
                  />
                </div>
              </div>
              <div>
                <h2>2. Grid Vulnerability</h2>
                <div className="map-container">
                  <iframe 
                    srcDoc={simData.vulnerability_map_html} 
                    style={{ width: '100%', height: '100%', border: 'none' }}
                    title="Vulnerability Map"
                  />
                </div>
              </div>
            </div>
          </>
        )}

        {optData && (
          <div style={{ marginTop: '32px', paddingTop: '32px', borderTop: '4px solid var(--border-color)' }}>
            <h2>3. Optimal Infrastructure Placement</h2>
            
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

            <div className="map-container" style={{ marginBottom: '32px' }}>
              <iframe 
                srcDoc={optData.placement_map_html} 
                style={{ width: '100%', height: '100%', border: 'none' }}
                title="Placement Map"
              />
            </div>

            <div className="glass-panel">
              <h3>Custom Charger Editor</h3>
              <p style={{marginBottom: '16px'}}>Modify the locations of charging stations. Add or remove stations, then apply changes to update the map.</p>
              
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
                <button onClick={handleAddCustom} style={{padding: '8px 16px', fontSize: '0.9rem'}}>Add Station</button>
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
                            <button onClick={() => handleRemoveCustom(p.fsa)} style={{padding: '4px 8px', fontSize: '0.8rem'}}>Remove</button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              
              <button onClick={handleApplyCustom} style={{width: '100%'}}>Apply Custom Placements</button>
            </div>
            
          </div>
        )}
      </div>
    </div>
  );
}

export default App;

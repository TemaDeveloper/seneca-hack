const API_BASE = 'http://localhost:8000/api';

export const runSimulation = async (params) => {
  const response = await fetch(`${API_BASE}/simulate`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  if (!response.ok) throw new Error('Simulation failed');
  return response.json();
};

export const runOptimization = async (params) => {
  const response = await fetch(`${API_BASE}/optimize`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  if (!response.ok) throw new Error('Optimization failed');
  return response.json();
};

export const runCustomPlacement = async (params, customStations) => {
  const payload = {
    ...params,
    placements: customStations
  };
  const response = await fetch(`${API_BASE}/custom_placement`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error('Custom placement failed');
  return response.json();
};

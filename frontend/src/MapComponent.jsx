import React, { useState, useEffect, useMemo } from 'react';
import { MapContainer, TileLayer, GeoJSON, Marker, Popup, CircleMarker, useMapEvents } from 'react-leaflet';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

// Fix leaflet icon paths
delete L.Icon.Default.prototype._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon-2x.png',
  iconUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-icon.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
});

const customMarkerIcon = new L.Icon({
  iconUrl: 'https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-violet.png',
  shadowUrl: 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.7.1/images/marker-shadow.png',
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  shadowSize: [41, 41]
});

// A simple pseudo-random generator
function pseudoRandom(seed) {
  const x = Math.sin(seed) * 10000;
  return x - Math.floor(x);
}

// Crisp black-and-white SVGs for brutalist theme
const SVG_ICONS = {
  hospitals: `
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#000" stroke-width="3" style="filter: drop-shadow(2px 2px 0px #000);">
      <rect x="3" y="3" width="18" height="18" fill="#fff" />
      <path d="M12 7v10M7 12h10" />
    </svg>
  `,
  workplaces: `
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#000" stroke-width="3" style="filter: drop-shadow(2px 2px 0px #000);">
      <rect x="3" y="7" width="18" height="14" fill="#fff" />
      <path d="M16 7V4H8v3" />
    </svg>
  `,
  schools: `
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#000" stroke-width="2.5" style="filter: drop-shadow(2px 2px 0px #000);">
      <polygon points="12 3 22 8 12 13 2 8" fill="#fff" />
      <path d="M6 10v6c0 2 3 3 6 3s6-1 6-3v-6" fill="#fff" />
    </svg>
  `,
  gyms: `
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#000" stroke-width="3" style="filter: drop-shadow(2px 2px 0px #000);">
      <path d="M6 12h12" />
      <rect x="4" y="7" width="2" height="10" fill="#fff" />
      <rect x="18" y="7" width="2" height="10" fill="#fff" />
      <rect x="2" y="9" width="2" height="6" fill="#fff" />
      <rect x="20" y="9" width="2" height="6" fill="#fff" />
    </svg>
  `,
  chargers: `
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#000" stroke-width="2.5" style="filter: drop-shadow(2px 2px 0px #000);">
      <polygon points="13 2 3 14 12 14 11 22 21 10 12 10" fill="#fff" />
    </svg>
  `,
  retail: `
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#000" stroke-width="2.5" style="filter: drop-shadow(2px 2px 0px #000);">
      <path d="M6 2L3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4H6z" fill="#fff" />
      <path d="M3 6h18M16 10a4 4 0 0 1-8 0" />
    </svg>
  `,
  transit: `
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#000" stroke-width="2.5" style="filter: drop-shadow(2px 2px 0px #000);">
      <rect x="4" y="3" width="16" height="16" rx="2" fill="#fff" />
      <path d="M4 11h16M8 15h.01M16 15h.01M6 19l-2 2M18 19l2 2" />
    </svg>
  `,
  residential: `
    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#000" stroke-width="2.5" style="filter: drop-shadow(2px 2px 0px #000);">
      <rect x="3" y="2" width="18" height="20" fill="#fff" />
      <path d="M7 6h2M7 10h2M7 14h2M7 18h2M15 6h2M15 10h2M15 14h2M15 18h2" />
    </svg>
  `
};

// Function to create a custom SVG DivIcon
function createPoiIcon(type) {
  const svgHtml = SVG_ICONS[type] || '';
  return new L.DivIcon({
    html: `<div style="width: 24px; height: 24px; display: flex; align-items: center; justify-content: center;">${svgHtml}</div>`,
    className: 'custom-poi-icon',
    iconSize: [24, 24],
    iconAnchor: [12, 12],
    popupAnchor: [0, -10]
  });
}

// Sub-component to monitor map zoom level
function ZoomHandler({ setZoom }) {
  const map = useMapEvents({
    zoomend() {
      setZoom(map.getZoom());
    }
  });
  return null;
}

export default function MapComponent({ gridData, evData, layer, prescriptions, showCars, simVersion, showPois, poiFilters }) {
  const [geoJsonData, setGeoJsonData] = useState(null);
  const [realPois, setRealPois] = useState([]);
  const [zoomLevel, setZoomLevel] = useState(10);

  useEffect(() => {
    fetch('/gta_fsa_boundaries.geojson')
      .then(res => res.json())
      .then(data => setGeoJsonData(data))
      .catch(err => console.error("Error loading geojson", err));

    fetch('/gta_pois.json')
      .then(res => res.json())
      .then(data => setRealPois(data))
      .catch(err => console.error("Error loading real POIs", err));
  }, []);

  const gridMap = useMemo(() => {
    if (!gridData) return {};
    return gridData.reduce((acc, row) => {
      acc[row.fsa] = row;
      return acc;
    }, {});
  }, [gridData]);

  // Index active POIs by FSA for fast lookups and EV clustering
  const activePoisByFsa = useMemo(() => {
    if (!showPois || zoomLevel < 12 || !realPois) return {};
    const lookup = {};
    realPois.forEach(poi => {
      if (!poiFilters[poi.type]) return;
      if (!lookup[poi.fsa]) {
        lookup[poi.fsa] = [];
      }
      lookup[poi.fsa].push(poi);
    });
    return lookup;
  }, [showPois, zoomLevel, realPois, poiFilters]);

  const getStyle = (feature) => {
    const fsa = feature.properties.fsa;
    const dataRow = gridMap[fsa];

    let fillColor = '#FFFFFF';

    if (dataRow) {
      if (layer === 'demand') {
        const maxLoad = 2000;
        const load = dataRow.peak_ev_load_kw || 0;
        const intensity = Math.min(1, load / maxLoad);
        const r = 255;
        const g = Math.floor(255 * (1 - intensity));
        fillColor = `rgb(${r},${g},0)`;
      } else {
        // Three-tier vulnerability styling
        const ratio = dataRow.total_load_kw / dataRow.proxy_capacity_kw;
        if (ratio > 1.0) {
          fillColor = '#FF0000'; // Red - Overloaded
        } else if (ratio > 0.8) {
          fillColor = '#ffcc00'; // Yellow - Borderline
        } else {
          fillColor = '#00FF00'; // Green - OK
        }
      }
    }

    return {
      fillColor,
      color: '#000000',
      weight: 3,
      fillOpacity: layer === 'demand' ? 0.7 : 0.8,
    };
  };

  const renderContent = (feature, isPopup) => {
    const fsa = feature.properties.fsa;
    const dataRow = gridMap[fsa];
    if (!dataRow) return `FSA: ${fsa} (No Data)`;

    let evTableHtml = "";
    if (isPopup && showCars && evData && evData[fsa] && evData[fsa].sample_cars.length > 0) {
      const fsaEvInfo = evData[fsa];
      evTableHtml = `
        <div style="margin-top: 12px; border-top: 2px solid var(--border-color); padding-top: 8px;">
          <strong>Sample EV Arrivals (${fsaEvInfo.sample_cars.length} of ${fsaEvInfo.total_fsa_cars})</strong>
          <table style="width: 100%; border-collapse: collapse; margin-top: 4px; font-size: 12px;">
            <thead>
              <tr style="border-bottom: 1px solid var(--border-color);">
                <th style="text-align: left;">ID</th>
                <th style="text-align: left;">Arrives</th>
                <th style="text-align: right;">SoC Needed</th>
              </tr>
            </thead>
            <tbody>
              ${fsaEvInfo.sample_cars.map(car => `
                <tr>
                  <td>${car.vehicle_id}</td>
                  <td>${car.arrival_time}</td>
                  <td style="text-align: right;">${car.soc_needed_kwh} kWh</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      `;
    }

    return `
      <div style="font-family: 'Inter', sans-serif; font-size: 14px; min-width: 250px;">
        <strong>FSA: ${fsa}</strong><br/>
        Type: ${dataRow.zone_type}<br/>
        Est. EV Load: ${dataRow.peak_ev_load_kw} kW<br/>
        Total Load: ${dataRow.total_load_kw.toFixed(1)} kW<br/>
        Capacity: ${dataRow.proxy_capacity_kw} kW<br/>
        Deficit: ${dataRow.deficit_kw} kW
        ${evTableHtml}
      </div>
    `;
  };

  const onEachFeature = (feature, layerObj) => {
    layerObj.bindTooltip(renderContent(feature, false), { sticky: true, className: 'custom-tooltip' });

    layerObj.on({
      mouseover: (e) => {
        const l = e.target;
        l.setStyle({
          weight: 5,
          fillOpacity: 1
        });
        l.bringToFront();
      },
      mouseout: (e) => {
        const l = e.target;
        l.setStyle(getStyle(feature));
      },
      contextmenu: (e) => {
        if (showCars) {
          L.popup()
            .setLatLng(e.latlng)
            .setContent(renderContent(feature, true))
            .openOn(layerObj._map);
        }
      }
    });
  };

  if (!geoJsonData) return <div style={{ padding: '20px', fontWeight: 'bold' }}>Loading Map Data...</div>;

  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <MapContainer center={[43.7, -79.4]} zoom={10} style={{ width: '100%', height: '100%', background: '#fff' }}>
        <TileLayer
          attribution='&copy; <a href="https://carto.com/">CartoDB</a>'
          url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
        />

        <ZoomHandler setZoom={setZoomLevel} />

        <GeoJSON
          key={layer + simVersion + showCars.toString()}
          data={geoJsonData}
          style={getStyle}
          onEachFeature={onEachFeature}
        />

        {/* Abstract EVs scattered around centroids OR clustered realistically around real POIs */}
        {layer === 'demand' && showCars && gridData && gridData.map((row, idx) => {
          if (!row.centroid_lat || !row.centroid_lon) return null;
          const numDots = Math.min(50, Math.floor((row.peak_ev_load_kw || 0) / 70)) || 0;

          const fsaPois = activePoisByFsa[row.fsa] || [];
          const hasLocalPois = fsaPois.length > 0;

          return Array.from({ length: numDots }).map((_, dotIdx) => {
            const seed = idx * 1000 + dotIdx;
            let center;

            if (hasLocalPois) {
              // Cluster around a deterministic active POI in this FSA
              const poi = fsaPois[dotIdx % fsaPois.length];
              // Very tight clustering (approx 50m radius) for realistic parking lot feel
              const latJitter = (pseudoRandom(seed) - 0.5) * 0.0012;
              const lonJitter = (pseudoRandom(seed + 10) - 0.5) * 0.0012;
              center = [poi.lat + latJitter, poi.lon + lonJitter];
            } else {
              // Scatter around centroid (zoomed out or POIs disabled)
              const latJitter = (pseudoRandom(seed) - 0.5) * 0.04;
              const lonJitter = (pseudoRandom(seed + 10) - 0.5) * 0.04;
              center = [row.centroid_lat + latJitter, row.centroid_lon + lonJitter];
            }

            return (
              <CircleMarker
                key={`${row.fsa}-ev-${dotIdx}`}
                center={center}
                radius={2}
                pathOptions={{ color: '#000', fillColor: '#000', fillOpacity: 0.8, weight: 1, pane: 'markerPane' }}
              />
            );
          });
        })}

        {/* Real Attraction Points (POIs) */}
        {showPois && zoomLevel >= 12 && realPois && realPois.map(poi => {
          if (!poiFilters[poi.type]) return null;
          return (
            <Marker
              key={poi.id}
              position={[poi.lat, poi.lon]}
              icon={createPoiIcon(poi.type)}
            >
              <Popup>
                <div style={{ fontFamily: 'Inter, sans-serif' }}>
                  <strong>{poi.name}</strong><br />
                  Type: {poi.type.charAt(0).toUpperCase() + poi.type.slice(1)}<br />
                  FSA: {poi.fsa}
                </div>
              </Popup>
            </Marker>
          );
        })}

        {layer === 'placements' && prescriptions && prescriptions.map((site, i) => (
          site.centroid_lat && site.centroid_lon ? (
            <Marker key={i} position={[site.centroid_lat, site.centroid_lon]} icon={customMarkerIcon}>
              <Popup>
                <div style={{ fontFamily: 'Inter' }}>
                  <strong>Site: {site.fsa}</strong><br />
                  Type: {site.charger_type}<br />
                  Units: {site.charger_units}<br />
                  Total Capacity: {site.total_charger_kw} kW
                </div>
              </Popup>
            </Marker>
          ) : null
        ))}
      </MapContainer>

      {/* Zoom Warning Overlay */}
      {showPois && zoomLevel < 12 && (
        <div style={{
          position: 'absolute',
          bottom: '20px',
          left: '50%',
          transform: 'translateX(-50%)',
          backgroundColor: '#ffcc00',
          color: '#000',
          border: '3px solid #000',
          padding: '10px 20px',
          fontWeight: '800',
          textTransform: 'uppercase',
          boxShadow: '4px 4px 0px #000',
          zIndex: 1000,
          pointerEvents: 'none',
          textAlign: 'center',
          fontSize: '0.9rem',
          letterSpacing: '0.5px'
        }}>
          🔍 Zoom in to view Attraction Points (POIs)
        </div>
      )}

      {/* Map Legend Overlay */}
      <div style={{
        position: 'absolute',
        bottom: '20px',
        right: '20px',
        backgroundColor: '#fff',
        border: '3px solid #000',
        padding: '12px',
        boxShadow: '4px 4px 0px #000',
        zIndex: 1000,
        fontFamily: 'Inter, sans-serif',
        fontSize: '12px',
        pointerEvents: 'auto',
        display: 'flex',
        flexDirection: 'column',
        gap: '8px',
        minWidth: '150px'
      }}>
        <h4 style={{ margin: '0 0 4px 0', textTransform: 'uppercase', fontWeight: 800, borderBottom: '2px solid #000', paddingBottom: '4px' }}>Legend</h4>

        {layer === 'demand' ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <div style={{ fontWeight: 'bold', marginBottom: '2px' }}>EV Load (kW)</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <div style={{ width: '16px', height: '16px', backgroundColor: 'rgb(255, 0, 0)', border: '1px solid #000' }} />
              <span>High Peak Load (&ge;2000 kW)</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <div style={{ width: '16px', height: '16px', backgroundColor: 'rgb(255, 127, 0)', border: '1px solid #000' }} />
              <span>Medium Peak Load (~1000 kW)</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <div style={{ width: '16px', height: '16px', backgroundColor: 'rgb(255, 255, 0)', border: '1px solid #000' }} />
              <span>Low Peak Load (&le;100 kW)</span>
            </div>
          </div>
        ) : layer === 'vulnerability' ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <div style={{ fontWeight: 'bold', marginBottom: '2px' }}>Grid Status</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <div style={{ width: '16px', height: '16px', backgroundColor: '#FF0000', border: '1px solid #000' }} />
              <span>Overloaded (&gt;100%)</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <div style={{ width: '16px', height: '16px', backgroundColor: '#ffcc00', border: '1px solid #000' }} />
              <span>Borderline (80% - 100%)</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <div style={{ width: '16px', height: '16px', backgroundColor: '#00FF00', border: '1px solid #000' }} />
              <span>Safe (&lt;80%)</span>
            </div>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <img src="https://raw.githubusercontent.com/pointhi/leaflet-color-markers/master/img/marker-icon-2x-violet.png" style={{ height: '16px' }} alt="marker" />
              <span>Charger Prescriptions</span>
            </div>
          </div>
        )}
        
        {showCars && layer === 'demand' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px', marginTop: '4px', borderTop: '1px solid #000', paddingTop: '6px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <div style={{ width: '6px', height: '6px', backgroundColor: '#000', borderRadius: '50%', border: '1px solid #000', margin: '0 5px' }} />
              <span style={{ fontWeight: 'bold' }}>1 dot = 15 EV</span>
            </div>
          </div>
        )}

        {showPois && zoomLevel >= 12 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginTop: '4px', borderTop: '1px solid #000', paddingTop: '6px' }}>
            <div style={{ fontWeight: 'bold', marginBottom: '2px' }}>POIs</div>
            {poiFilters.hospitals && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span dangerouslySetInnerHTML={{ __html: SVG_ICONS.hospitals.replace('width="24" height="24"', 'width="18" height="18"') }} style={{ display: 'flex', alignItems: 'center' }} />
                <span>Hospital</span>
              </div>
            )}
            {poiFilters.workplaces && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span dangerouslySetInnerHTML={{ __html: SVG_ICONS.workplaces.replace('width="24" height="24"', 'width="18" height="18"') }} style={{ display: 'flex', alignItems: 'center' }} />
                <span>Workplace</span>
              </div>
            )}
            {poiFilters.schools && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span dangerouslySetInnerHTML={{ __html: SVG_ICONS.schools.replace('width="24" height="24"', 'width="18" height="18"') }} style={{ display: 'flex', alignItems: 'center' }} />
                <span>School</span>
              </div>
            )}
            {poiFilters.gyms && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span dangerouslySetInnerHTML={{ __html: SVG_ICONS.gyms.replace('width="24" height="24"', 'width="18" height="18"') }} style={{ display: 'flex', alignItems: 'center' }} />
                <span>Gym</span>
              </div>
            )}
            {poiFilters.chargers && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span dangerouslySetInnerHTML={{ __html: SVG_ICONS.chargers.replace('width="24" height="24"', 'width="18" height="18"') }} style={{ display: 'flex', alignItems: 'center' }} />
                <span>Charger Station</span>
              </div>
            )}
            {poiFilters.retail && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span dangerouslySetInnerHTML={{ __html: SVG_ICONS.retail.replace('width="24" height="24"', 'width="18" height="18"') }} style={{ display: 'flex', alignItems: 'center' }} />
                <span>Retail & Mall</span>
              </div>
            )}
            {poiFilters.transit && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span dangerouslySetInnerHTML={{ __html: SVG_ICONS.transit.replace('width="24" height="24"', 'width="18" height="18"') }} style={{ display: 'flex', alignItems: 'center' }} />
                <span>Transit Hub</span>
              </div>
            )}
            {poiFilters.residential && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <span dangerouslySetInnerHTML={{ __html: SVG_ICONS.residential.replace('width="24" height="24"', 'width="18" height="18"') }} style={{ display: 'flex', alignItems: 'center' }} />
                <span>Residential (Apartments)</span>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

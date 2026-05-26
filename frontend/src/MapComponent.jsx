import React, { useState, useEffect, useMemo } from 'react';
import { MapContainer, TileLayer, GeoJSON, Marker, Popup, CircleMarker } from 'react-leaflet';
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
  const x = Math.sin(seed++) * 10000;
  return x - Math.floor(x);
}

export default function MapComponent({ gridData, evData, layer, prescriptions, showCars }) {
  const [geoJsonData, setGeoJsonData] = useState(null);

  useEffect(() => {
    fetch('/gta_fsa_boundaries.geojson')
      .then(res => res.json())
      .then(data => setGeoJsonData(data))
      .catch(err => console.error("Error loading geojson", err));
  }, []);

  const gridMap = useMemo(() => {
    if (!gridData) return {};
    return gridData.reduce((acc, row) => {
      acc[row.fsa] = row;
      return acc;
    }, {});
  }, [gridData]);

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
        fillColor = dataRow.overloaded ? '#FF0000' : '#00FF00';
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

    // Using string literal HTML for leaflet bindTooltip/bindPopup
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
    <MapContainer center={[43.7, -79.4]} zoom={10} style={{ width: '100%', height: '100%', background: '#fff' }}>
      <TileLayer
        attribution='&copy; <a href="https://carto.com/">CartoDB</a>'
        url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
      />

      <GeoJSON
        key={layer + (gridData ? gridData.length : 0) + showCars.toString()}
        data={geoJsonData}
        style={getStyle}
        onEachFeature={onEachFeature}
      />

      {/* Abstract EVs scattered around the FSA centroids */}
      {layer === 'demand' && showCars && gridData && gridData.map((row, idx) => {
        if (!row.centroid_lat || !row.centroid_lon) return null;
        // 1 dot = roughly 10 EVs (assuming ~7 kW per EV peak) => 1 dot per 70 kW load
        const numDots = Math.min(50, Math.floor((row.peak_ev_load_kw || 0) / 70)) || 0;
        return Array.from({ length: numDots }).map((_, dotIdx) => {
          const seed = idx * 1000 + dotIdx;
          const latJitter = (pseudoRandom(seed) - 0.5) * 0.04;
          const lonJitter = (pseudoRandom(seed + 10) - 0.5) * 0.04;
          return (
            <CircleMarker
              key={`${row.fsa}-ev-${dotIdx}`}
              center={[row.centroid_lat + latJitter, row.centroid_lon + lonJitter]}
              radius={2}
              pathOptions={{ color: '#000', fillColor: '#000', fillOpacity: 0.8, weight: 1 }}
            />
          );
        });
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
  );
}

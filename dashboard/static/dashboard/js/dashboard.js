/* ═══════════════════════════════════════════════════════════
   GÉODASH — dashboard.js
   Dépendances : Leaflet 1.9.4 + Chart.js 4.4.1
═══════════════════════════════════════════════════════════ */
'use strict';

/* ─── État global ─────────────────────────────────────── */
let map        = null;
let lRoads     = null;
let lFloods    = null;
let lVeg       = null;
let _allBounds = [];
let _initLat   = 5.35;
let _initLng   = -4.00;
let _mapReady  = false;

const layerVis = { roads: true, floods: true, vegetation: true };
const layerRef = {
  roads:      function () { return lRoads; },
  floods:     function () { return lFloods; },
  vegetation: function () { return lVeg; },
};


/* ══════════════════════════════════════════════════════════
   THÈME — toggle + persistance localStorage
══════════════════════════════════════════════════════════ */

/**
 * Bascule entre light et dark.
 * Ajoute temporairement `.theme-transitioning` pour une transition douce.
 */
function toggleTheme() {
  var html    = document.documentElement;
  var current = html.getAttribute('data-theme') || 'light';
  var next    = current === 'light' ? 'dark' : 'light';

  /* Transition fluide */
  html.classList.add('theme-transitioning');
  html.setAttribute('data-theme', next);
  try { localStorage.setItem('gd-theme', next); } catch (e) {}
  setTimeout(function () { html.classList.remove('theme-transitioning'); }, 380);
}


/* ══════════════════════════════════════════════════════════
   RIPPLE — micro-animation sur les éléments interactifs
══════════════════════════════════════════════════════════ */

/**
 * Attache un écouteur de clic sur un élément pour l'effet ripple.
 * @param {Element} el
 */
function _addRipple(el) {
  el.addEventListener('click', function (e) {
    var rect = this.getBoundingClientRect();
    var size = Math.max(rect.width, rect.height);
    var span = document.createElement('span');
    span.className = 'ripple-wave';
    span.style.cssText =
      'width:'  + size + 'px;' +
      'height:' + size + 'px;' +
      'left:'   + (e.clientX - rect.left  - size / 2) + 'px;' +
      'top:'    + (e.clientY - rect.top   - size / 2) + 'px;';
    this.appendChild(span);
    setTimeout(function () { if (span.parentNode) span.parentNode.removeChild(span); }, 600);
  });
}


/* ══════════════════════════════════════════════════════════
   ÉVÉNEMENTS — branché sur data-* attributes
══════════════════════════════════════════════════════════ */

function initEventListeners() {

  /* Boutons toolbar carte */
  document.querySelectorAll('[data-set-layer]').forEach(function (btn) {
    btn.addEventListener('click', function () { setLayer(this.dataset.setLayer, this); });
    _addRipple(btn);
  });

  /* KPI cards */
  document.querySelectorAll('[data-layer]').forEach(function (card) {
    card.addEventListener('click', function () { setLayer(this.dataset.layer); });
  });

  /* Toggles couches sidebar */
  document.querySelectorAll('[data-toggle-layer]').forEach(function (label) {
    label.addEventListener('click', function (e) {
      e.preventDefault();
      var type = this.dataset.toggleLayer;
      var cb   = this.querySelector('input[type="checkbox"]');
      if (cb) cb.checked = !cb.checked;
      toggleLayer(type, this);
    });
  });

  /* Alertes */
  document.querySelectorAll('[data-focus-alert]').forEach(function (el) {
    el.addEventListener('click', function () {
      focusAlert(
        parseFloat(this.dataset.lat || 0),
        parseFloat(this.dataset.lng || 0)
      );
    });
    _addRipple(el);
  });

  /* Sélecteur de zone */
  var zoneSelect = document.getElementById('zoneSelect');
  if (zoneSelect) {
    zoneSelect.addEventListener('change', function () { switchZone(this.value); });
  }

  /* Bouton toggle thème */
  var themeBtn = document.getElementById('themeToggle');
  if (themeBtn) {
    themeBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      toggleTheme();
    });
  }

  /* Ripple sur les nav-items */
  document.querySelectorAll('.nav-item').forEach(function (el) {
    _addRipple(el);
  });
}


/* ══════════════════════════════════════════════════════════
   CARTE — initialisation Leaflet
══════════════════════════════════════════════════════════ */

function initMap(lat, lng, data) {
  if (typeof L === 'undefined') {
    console.error('[GéoDash] Leaflet non disponible.');
    var el = document.getElementById('map');
    if (el) el.innerHTML =
      '<div style="display:flex;align-items:center;justify-content:center;'
      + 'height:100%;color:var(--t2);font:14px DM Sans,sans-serif;padding:24px;text-align:center">'
      + 'Carte indisponible — vérifier les fichiers statiques Leaflet</div>';
    _hideOverlay();
    return;
  }

  _initLat = parseFloat(lat) || 5.35;
  _initLng = parseFloat(lng) || -4.00;

  map = L.map('map', {
    zoomControl:         false,
    attributionControl:  false,
    zoomAnimation:       true,
    fadeAnimation:       true,
    zoomSnap:            0.5,
    zoomDelta:           0.5,
    wheelPxPerZoomLevel: 80,
  });

  /* Tuiles */
  L.tileLayer(
    'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
    { maxZoom: 19, subdomains: 'abcd', keepBuffer: 4 }
  ).on('tileerror', function () {
    var el = document.getElementById('map');
    if (el) el.style.background = 'var(--bg-map)';
  }).addTo(map);

  /* Contrôles */
  L.control.zoom({ position: 'bottomright' }).addTo(map);
  L.control.scale({ position: 'bottomleft', imperial: false, metric: true }).addTo(map);
  _addResetControl();

  /* Couches */
  lRoads  = L.layerGroup().addTo(map);
  lFloods = L.layerGroup().addTo(map);
  lVeg    = L.layerGroup().addTo(map);

  map.setView([_initLat, _initLng], 9);
  _mapReady = true;

  _renderData(data);
  setTimeout(_hideOverlay, 900);
}

function _addResetControl() {
  var Ctrl = L.Control.extend({
    options: { position: 'bottomright' },
    onAdd: function () {
      var btn = L.DomUtil.create('button', 'map-reset-btn');
      btn.title = 'Recentrer';
      btn.innerHTML =
        '<svg width="15" height="15" viewBox="0 0 24 24" fill="none"'
        + ' stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        + '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/>'
        + '<line x1="12" y1="2" x2="12" y2="5"/><line x1="12" y1="19" x2="12" y2="22"/>'
        + '<line x1="2" y1="12" x2="5" y2="12"/><line x1="19" y1="12" x2="22" y2="12"/>'
        + '</svg>';
      L.DomEvent.disableClickPropagation(btn);
      L.DomEvent.on(btn, 'click', flyToAll);
      return btn;
    },
  });
  new Ctrl().addTo(map);
}

function _hideOverlay() {
  var o = document.getElementById('mapOverlay');
  if (!o) return;
  o.style.transition = 'opacity .5s';
  o.style.opacity    = '0';
  setTimeout(function () { o.style.display = 'none'; }, 500);
}


/* ══════════════════════════════════════════════════════════
   RENDU GÉOJSON
══════════════════════════════════════════════════════════ */

function _renderData(data) {
  if (!data || !map) return;
  var n = 0;
  _allBounds = [];

  /* Routes */
  (data.routes || []).forEach(function (r) {
    var geo = r.geojson;
    if (!geo) return;
    try {
      var opts = { color: r.color, weight: 5, opacity: .85, lineCap: 'round', lineJoin: 'round' };
      var lyr;
      if (geo.type === 'LineString') {
        lyr = L.polyline(geo.coordinates.map(function (c) { return [c[1], c[0]]; }), opts);
      } else if (geo.type === 'MultiLineString') {
        lyr = L.polyline(geo.coordinates.map(function (line) {
          return line.map(function (c) { return [c[1], c[0]]; });
        }), opts);
      } else return;
      lyr.on('mouseover', function () { this.setStyle({ weight: 8, opacity: 1 }); });
      lyr.on('mouseout',  function () { this.setStyle({ weight: 5, opacity: .85 }); });
      lyr.bindPopup(_popupRoad(r), { maxWidth: 260, className: 'gd-popup' });
      lRoads.addLayer(lyr);
      _collectBounds(lyr);
      n++;
    } catch (e) { console.warn('[GéoDash] Route:', r.name, e); }
  });

  /* Inondations */
  (data.floods || []).forEach(function (f) {
    var geo = f.geojson;
    if (!geo) return;
    try {
      var COLORS = { faible: '#22d3ee', modere: '#3b82f6', eleve: '#f97316', critique: '#dc2626' };
      var c    = COLORS[f.risk_level] || '#3b82f6';
      var opts = { color: c, fillColor: c, weight: 2, opacity: .8, fillOpacity: .22 };
      var lyr;
      if (geo.type === 'Polygon') {
        lyr = L.polygon(geo.coordinates[0].map(function (c) { return [c[1], c[0]]; }), opts);
      } else if (geo.type === 'MultiPolygon') {
        lyr = L.polygon(geo.coordinates.map(function (p) {
          return p[0].map(function (c) { return [c[1], c[0]]; });
        }), opts);
      } else return;
      lyr.on('mouseover', function () { this.setStyle({ fillOpacity: .45, weight: 3 }); });
      lyr.on('mouseout',  function () { this.setStyle({ fillOpacity: .22, weight: 2 }); });
      lyr.bindPopup(_popupFlood(f), { maxWidth: 260, className: 'gd-popup' });
      lFloods.addLayer(lyr);
      _collectBounds(lyr);
      n++;
    } catch (e) { console.warn('[GéoDash] Flood:', f.name, e); }
  });

  /* Végétation */
  (data.vegetation || []).forEach(function (v) {
    var geo = v.geojson;
    if (!geo) return;
    try {
      var COLORS = { sparse: '#d9f99d', moderate: '#4ade80', dense: '#16a34a', very_dense: '#14532d' };
      var c    = COLORS[v.density_class] || '#4ade80';
      var opts = { color: c, fillColor: c, weight: 1.5, opacity: .6, fillOpacity: .18, dashArray: '5,5' };
      var lyr;
      if (geo.type === 'Polygon') {
        lyr = L.polygon(geo.coordinates[0].map(function (c) { return [c[1], c[0]]; }), opts);
      } else return;
      lyr.on('mouseover', function () { this.setStyle({ fillOpacity: .38, opacity: .9 }); });
      lyr.on('mouseout',  function () { this.setStyle({ fillOpacity: .18, opacity: .6 }); });
      lyr.bindPopup(_popupVeg(v), { maxWidth: 260, className: 'gd-popup' });
      lVeg.addLayer(lyr);
      _collectBounds(lyr);
      n++;
    } catch (e) { console.warn('[GéoDash] Veg:', v.name, e); }
  });

  /* flyToBounds avec délai pour laisser setView s'installer */
  if (_allBounds.length) {
    setTimeout(function () {
      if (map && _mapReady) {
        map.flyToBounds(L.latLngBounds(_allBounds).pad(.12), {
          duration: 1.4, easeLinearity: .25, maxZoom: 14,
        });
      }
    }, 250);
  }

  toast(n > 0 ? n + ' objets chargés' : 'Aucune donnée géospatiale', n > 0 ? 'ok' : 'nfo');
}

function _collectBounds(lyr) {
  try {
    var b = lyr.getBounds();
    if (b && b.isValid()) _allBounds.push(b.getSouthWest(), b.getNorthEast());
  } catch (_) {}
}


/* ══════════════════════════════════════════════════════════
   POPUPS
══════════════════════════════════════════════════════════ */

function _bar(pct, color) {
  return '<div class="pp-bar"><div class="pp-bar-f" style="width:'
    + pct + '%;background:' + color + '"></div></div>';
}

function _popupRoad(r) {
  return '<div class="popup-inner">'
    + '<div class="popup-type">Route</div>'
    + '<div class="popup-name">' + r.name + '</div>'
    + '<div class="popup-row"><span class="popup-lbl">Score</span>'
    + '<span class="popup-val" style="color:' + r.color + '">' + r.condition_score + '/100</span></div>'
    + _bar(r.condition_score, r.color)
    + '<div class="popup-row"><span class="popup-lbl">Surface</span>'
    + '<span class="popup-val">' + (r.surface_type || '—') + '</span></div>'
    + (r.notes ? '<div class="popup-notes">' + r.notes + '</div>' : '')
    + '<span class="popup-badge badge-' + r.status + '">' + (r.status_label || r.status) + '</span>'
    + '</div>';
}

function _popupFlood(f) {
  return '<div class="popup-inner">'
    + '<div class="popup-type">Zone inondation</div>'
    + '<div class="popup-name">' + f.name + '</div>'
    + '<div class="popup-row"><span class="popup-lbl">Risque</span>'
    + '<span class="popup-val">' + f.risk_score + '/100</span></div>'
    + _bar(f.risk_score, f.color)
    + '<div class="popup-row"><span class="popup-lbl">Niveau</span>'
    + '<span class="popup-val">' + (f.risk_label || f.risk_level) + '</span></div>'
    + '<div class="popup-row"><span class="popup-lbl">Surface</span>'
    + '<span class="popup-val">' + f.area_km2 + ' km²</span></div>'
    + '<div class="popup-row"><span class="popup-lbl">Pluviométrie</span>'
    + '<span class="popup-val">' + f.rainfall_mm + ' mm</span></div>'
    + '</div>';
}

function _popupVeg(v) {
  return '<div class="popup-inner">'
    + '<div class="popup-type">Végétation</div>'
    + '<div class="popup-name">' + v.name + '</div>'
    + '<div class="popup-row"><span class="popup-lbl">NDVI</span>'
    + '<span class="popup-val" style="color:#22d3ee">' + v.ndvi_value + '</span></div>'
    + _bar(Math.round(v.ndvi_value * 100), '#22c55e')
    + '<div class="popup-row"><span class="popup-lbl">Couverture</span>'
    + '<span class="popup-val">' + v.coverage_percent + '%</span></div>'
    + '<div class="popup-row"><span class="popup-lbl">Classe</span>'
    + '<span class="popup-val">' + (v.density_label || v.density_class) + '</span></div>'
    + '</div>';
}


/* ══════════════════════════════════════════════════════════
   CONTRÔLE COUCHES
══════════════════════════════════════════════════════════ */

function toggleLayer(type, el) {
  layerVis[type] = !layerVis[type];
  el.classList.toggle('off');
  if (!map) return;
  var lg = layerRef[type]();
  if (!lg) return;
  layerVis[type] ? map.addLayer(lg) : map.removeLayer(lg);
}

function setLayer(type, btn) {
  /* Mise à jour visuelle boutons */
  document.querySelectorAll('[data-set-layer]').forEach(function (b) {
    b.classList.remove('active');
  });
  if (btn) {
    btn.classList.add('active');
  } else {
    var found = document.querySelector('[data-set-layer="' + type + '"]');
    if (found) found.classList.add('active');
  }

  /* Synchro nav-items */
  document.querySelectorAll('.nav-item[data-set-layer]').forEach(function (ni) {
    ni.classList.toggle('active', ni.dataset.setLayer === type);
  });

  if (!map) return;

  var vis = {
    all:        { roads: 1, floods: 1, vegetation: 1 },
    roads:      { roads: 1, floods: 0, vegetation: 0 },
    floods:     { roads: 0, floods: 1, vegetation: 0 },
    vegetation: { roads: 0, floods: 0, vegetation: 1 },
  }[type] || { roads: 1, floods: 1, vegetation: 1 };

  Object.keys(vis).forEach(function (k) {
    var v  = vis[k];
    var lg = layerRef[k]();
    if (!lg) return;
    v ? map.addLayer(lg) : map.removeLayer(lg);
    layerVis[k] = !!v;
    var toggle = document.querySelector('[data-toggle-layer="' + k + '"]');
    if (toggle) toggle.classList.toggle('off', !v);
  });

  _flyToLayer(type);
}

function _flyToLayer(type) {
  if (!map || !_mapReady) return;
  if (type === 'all') { flyToAll(); return; }

  var lg = layerRef[type] ? layerRef[type]() : null;
  if (!lg) return;
  var bounds = [];
  lg.getLayers().forEach(function (l) {
    try {
      var b = l.getBounds();
      if (b && b.isValid()) bounds.push(b.getSouthWest(), b.getNorthEast());
    } catch (_) {}
  });
  if (bounds.length) {
    map.flyToBounds(L.latLngBounds(bounds).pad(.15), { duration: 1.0, easeLinearity: .3, maxZoom: 14 });
  }
}

function flyToAll() {
  if (!map || !_mapReady) return;
  if (_allBounds.length) {
    map.flyToBounds(L.latLngBounds(_allBounds).pad(.12), { duration: 1.4, easeLinearity: .25, maxZoom: 14 });
  } else {
    map.flyTo([_initLat, _initLng], 9, { duration: 1.2 });
  }
}


/* ══════════════════════════════════════════════════════════
   NAVIGATION & ALERTES
══════════════════════════════════════════════════════════ */

function switchZone(code) {
  window.location.href = code ? '/?zone=' + encodeURIComponent(code) : '/';
}

function focusAlert(lat, lng) {
  if (!map || !_mapReady) return;
  var la = parseFloat(lat);
  var ln = parseFloat(lng);
  if (!isNaN(la) && !isNaN(ln) && (la !== 0 || ln !== 0)) {
    map.flyTo([la, ln], 15, { duration: 1.4, easeLinearity: .25 });
    toast("Zoom sur la zone d'alerte", 'nfo');
  }
}

function refreshAlerts() {
  fetch('/api/alerts/')
    .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
    .then(function (d) {
      var cnt = document.getElementById('alertCount');
      if (cnt) cnt.textContent = d.count;

      var pill = document.getElementById('alertPill');
      if (pill) {
        pill.textContent = d.count > 0
          ? d.count + ' ALERTE' + (d.count > 1 ? 'S' : '')
          : 'RAS';
        pill.className = 'alert-pill ' + (d.count > 0 ? 'hot' : 'ok');
      }

      var upd = document.getElementById('lastUpd');
      if (upd) upd.textContent = new Date().toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    })
    .catch(function (err) { console.warn('[GéoDash] refreshAlerts:', err); });
}


/* ══════════════════════════════════════════════════════════
   GRAPHIQUES CHART.JS — adaptatifs au thème
══════════════════════════════════════════════════════════ */

/**
 * Retourne les couleurs de rendu Chart.js selon le thème actif.
 */
function _getChartTheme() {
  var dark = document.documentElement.getAttribute('data-theme') === 'dark';
  return {
    grid:  dark ? 'rgba(44,56,80,.8)'   : 'rgba(200,210,230,.5)',
    tick:  dark ? '#4e5d80'             : '#8892aa',
    label: dark ? '#8d9dc0'             : '#8892aa',
    bg:    dark ? 'rgba(22,29,43,.95)'  : 'rgba(255,255,255,.95)',
    title: dark ? '#e2e8f5'             : '#1a2035',
    body:  dark ? '#8d9dc0'             : '#4a5578',
    bdr:   dark ? '#2c3850'             : '#d8dcea',
  };
}

function initCharts(routesData, floodsData, avgScore) {
  if (typeof Chart === 'undefined') {
    console.error('[GéoDash] Chart.js non disponible.');
    return;
  }

  var th   = _getChartTheme();
  var mono = { family: "'DM Mono', monospace", size: 10 };
  var sans = { family: "'DM Sans', system-ui, sans-serif", size: 10 };

  var tip = {
    backgroundColor: th.bg, borderColor: th.bdr, borderWidth: 1,
    titleColor: th.title, bodyColor: th.body,
    padding: 9, titleFont: mono, bodyFont: sans,
  };

  /* Graphique barres — Routes */
  var rdCtx = document.getElementById('cRoad');
  if (rdCtx) {
    new Chart(rdCtx, {
      type: 'bar',
      data: {
        labels:   routesData.labels || [],
        datasets: [{
          data:            routesData.values || [],
          backgroundColor: (routesData.colors || []).map(function (c) { return c + '99'; }),
          borderColor:     routesData.colors || [],
          borderWidth: 1, borderRadius: 5,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        animation: { duration: 800, easing: 'easeOutQuart' },
        plugins: { legend: { display: false }, tooltip: tip },
        scales: {
          x: { grid: { display: false }, ticks: { color: th.tick, font: sans } },
          y: { grid: { color: th.grid }, ticks: { color: th.tick, font: sans, stepSize: 1 }, beginAtZero: true },
        },
      },
    });
  }

  /* Graphique donut — Inondations */
  var flCtx = document.getElementById('cFlood');
  if (flCtx) {
    new Chart(flCtx, {
      type: 'doughnut',
      data: {
        labels:   floodsData.labels || [],
        datasets: [{
          data:            floodsData.values || [],
          backgroundColor: (floodsData.colors || []).map(function (c) { return c + '99'; }),
          borderColor:     floodsData.colors || [],
          borderWidth: 1,
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false, cutout: '62%',
        animation: { animateRotate: true, duration: 900, easing: 'easeOutQuart' },
        plugins: {
          legend: {
            position: 'right',
            labels: { color: th.label, font: sans, boxWidth: 10, padding: 6 },
          },
          tooltip: tip,
        },
      },
    });
  }

  /* Jauge score global */
  var score = parseFloat(avgScore) || 0;
  var gc    = score >= 70 ? '#00d97e' : score >= 40 ? '#f97316' : '#ef4444';
  var gBg   = document.documentElement.getAttribute('data-theme') === 'dark' ? '#1e253a' : '#eef3fd';

  var gaCtx = document.getElementById('cGauge');
  if (gaCtx) {
    new Chart(gaCtx, {
      type: 'doughnut',
      data: {
        datasets: [{
          data:            [score, 100 - score],
          backgroundColor: [gc + 'cc', gBg],
          borderWidth:     0,
        }],
      },
      options: {
        rotation: -90, circumference: 180, cutout: '72%',
        responsive: true, maintainAspectRatio: false,
        animation: { animateRotate: true, duration: 1000, easing: 'easeOutQuart' },
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
      },
    });
  }

  var gn = document.getElementById('gaugeNum');
  if (gn) gn.style.color = gc;
}


/* ══════════════════════════════════════════════════════════
   TOASTS
══════════════════════════════════════════════════════════ */

function toast(msg, type) {
  type = type || 'nfo';
  var wrap = document.getElementById('toasts');
  if (!wrap) return;
  var t = document.createElement('div');
  t.className   = 'toast ' + type;
  t.textContent = msg;
  wrap.appendChild(t);
  setTimeout(function () {
    t.style.cssText = 'opacity:0;transition:opacity .4s;transform:translateY(4px)';
    setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 420);
  }, 3200);
}
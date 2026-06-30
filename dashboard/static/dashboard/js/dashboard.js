/**
 * GéoDash — dashboard.js
 *
 * Logique principale du tableau de bord géospatial.
 * Dépendances globales attendues : Leaflet 1.9.4, Chart.js 4.4.1
 *
 * Organisation :
 *   – État global & refs Leaflet
 *   – Thème (toggle + persistance)
 *   – Événements (initEventListeners)
 *   – Carte (initMap, rendu GeoJSON, popups)
 *   – Contrôle des couches (setLayer, toggleLayer)
 *   – GEE (refreshGeeLayer — NDVI + SAR)
 *   – Alertes (focusAlert, refreshAlerts)
 *   – Graphiques Chart.js (initCharts)
 *   – Toasts
 *   – Paramètres (drawer, localStorage)
 *   – Fonds de carte (TILE_CONFIGS, _buildTileLayer)
 */
'use strict';

/* ─── État global ─────────────────────────────────────── */
let map = null;
let lRoads = null;
let lFloods = null;
let lVeg = null;
let lGeeNdvi = null;
let lGeeFlood = null;
let _allBounds = [];
let _initLat = 5.35;
let _initLng = -4.00;
let _mapReady = false;
let _alertFailCount = 0;
let _alertInterval = null;

/* Zone courante — lue depuis l'URL au démarrage, utilisée partout */
var _activeZoneCode = (new URLSearchParams(window.location.search)).get('zone') || '';

const layerVis = { roads: true, floods: true, vegetation: true };
const layerRef = {
  roads: function () { return lRoads; },
  floods: function () { return lFloods; },
  vegetation: function () { return lVeg; },
};

/* ─── Couleurs centralisées ─────────────────────────────────────────────────── */
const GD_COLORS = {
  road: '#5b8dee',
  flood: '#26c6da',
  vegetation: '#3ecf6e',
  alert: '#ff7043',
  flood_faible: '#22d3ee',
  flood_modere: '#3b82f6',
  flood_eleve: '#f97316',
  flood_critique: '#dc2626',
  veg_sparse: '#bef264',
  veg_moderate: '#4ade80',
  veg_dense: '#16a34a',
  veg_very_dense: '#14532d',
};

/* ─── Paramètres + couche tuiles ────────────────────────────────────────────── */
const GD_SETTINGS_DEFAULT = {
  theme: 'light',
  density: 'normal',
  tileStyle: 'light',
  zoomDefault: 9,
  showScale: true,
  showLegend: true,
  coordFmt: 'DD',
  refreshInterval: 60,
  alertMinLevel: 'info',
  soundAlerts: false,
  lang: 'fr',
};

let _settings = Object.assign({}, GD_SETTINGS_DEFAULT);
let _tileLayer = null;


/* ══════════════════════════════════════════════════════════
   THÈME
══════════════════════════════════════════════════════════ */

function toggleTheme() {
  var html = document.documentElement;
  var body = document.body;
  var current = html.getAttribute('data-theme') || 'light';
  var next = current === 'light' ? 'dark' : 'light';

  html.classList.add('theme-transitioning');
  html.setAttribute('data-theme', next);
  // Le nouveau design utilise `body.dark` (vs ancien `html[data-theme]`).
  // On set les deux pour rétrocompatibilité avec le CSS conservé.
  body.classList.toggle('dark', next === 'dark');

  try {
    localStorage.setItem('gd-theme', next);
    document.cookie = 'gd_theme=' + next + ';path=/;max-age=' + (60 * 60 * 24 * 365);
    if (_settings) {
      _settings.theme = next;
      _saveSettings();
    }
  } catch (e) { }

  setTimeout(function () { html.classList.remove('theme-transitioning'); }, 380);
}


/* ══════════════════════════════════════════════════════════
   RIPPLE
══════════════════════════════════════════════════════════ */

function _addRipple(el) {
  el.addEventListener('click', function (e) {
    var rect = this.getBoundingClientRect();
    var size = Math.max(rect.width, rect.height);
    var span = document.createElement('span');
    span.className = 'ripple-wave';
    span.style.cssText =
      'width:' + size + 'px;' +
      'height:' + size + 'px;' +
      'left:' + (e.clientX - rect.left - size / 2) + 'px;' +
      'top:' + (e.clientY - rect.top - size / 2) + 'px;';
    this.appendChild(span);
    setTimeout(function () { if (span.parentNode) span.parentNode.removeChild(span); }, 600);
  });
}


/* ══════════════════════════════════════════════════════════
   ÉVÉNEMENTS
══════════════════════════════════════════════════════════ */

function initEventListeners() {

  document.querySelectorAll('[data-set-layer]').forEach(function (btn) {
    btn.addEventListener('click', function () { setLayer(this.dataset.setLayer, this); });
    _addRipple(btn);
  });

  document.querySelectorAll('[data-layer]').forEach(function (card) {
    card.addEventListener('click', function () { setLayer(this.dataset.layer); });
  });

  // Toggle de couches (panneau gauche, onglet "Couches" — design Claude)
  // Supporte les 2 variantes : ancien (checkbox interne) et nouveau (.switch)
  document.querySelectorAll('[data-toggle-layer]').forEach(function (row) {
    row.addEventListener('click', function (e) {
      e.preventDefault();
      var type = this.dataset.toggleLayer;
      var cb = this.querySelector('input[type="checkbox"]');
      if (cb) cb.checked = !cb.checked;
      var sw = this.querySelector('.switch');
      if (sw) sw.classList.toggle('on');
      toggleLayer(type, this);
    });
  });

  // Liste d'alertes (nouveau design : .alerts-list > .alert-card)
  // Ancienne compat : .alerts-scroll > .a-item
  var alertsContainer = document.querySelector('.alerts-list, .alerts-scroll');
  if (alertsContainer) {
    alertsContainer.addEventListener('click', function (e) {
      var item = e.target.closest('[data-focus-alert]');
      if (!item) return;
      document.querySelectorAll('.alert-card.focused, .a-item.focused')
        .forEach(function (x) { x.classList.remove('focused'); });
      item.classList.add('focused');
      focusAlert(
        parseFloat(item.dataset.lat || 0),
        parseFloat(item.dataset.lng || 0),
        item.dataset.category || '',
        item.dataset.zoneLat || 0,
        item.dataset.zoneLng || 0
      );
    });
  }

  // Basemap switcher (boutons .tool[data-basemap] dans la topbar carte)
  document.querySelectorAll('.tool[data-basemap]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      document.querySelectorAll('.tool[data-basemap]').forEach(function (b) {
        b.classList.remove('active');
      });
      this.classList.add('active');
      var bm = this.dataset.basemap;
      if (typeof _switchBasemap === 'function') _switchBasemap(bm);
    });
  });

  // Chips overlays cumulables (NDVI / SAR / Limites admin)
  document.querySelectorAll('[data-toggle-overlay]').forEach(function (chip) {
    chip.addEventListener('click', function () {
      toggleOverlay(this.dataset.toggleOverlay, this);
    });
  });

  // Ouverture du drawer settings depuis le rail / topbar
  document.querySelectorAll('[data-open-settings]').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      var backdrop = document.getElementById('settingsBackdrop');
      var drawer = document.getElementById('settingsDrawer');
      if (backdrop) backdrop.classList.add('open');
      if (drawer) drawer.classList.add('open');
    });
  });

  // Plein écran carte (rail + map-tools)
  document.querySelectorAll('[data-toggle-fullscreen]').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      toggleMapFullscreen();
    });
  });

  // Raccourci clavier F → plein écran
  document.addEventListener('keydown', function (e) {
    if (e.key === 'F' || e.key === 'f') {
      // Ne pas déclencher dans les champs de saisie
      if (e.target.matches('input,textarea,select')) return;
      toggleMapFullscreen();
    }
    // Échap → quitter plein écran
    if (e.key === 'Escape') {
      var app = document.querySelector('.app');
      if (app && app.classList.contains('map-fullscreen')) {
        app.classList.remove('map-fullscreen');
        setTimeout(function () { if (typeof map !== 'undefined' && map) map.invalidateSize(); }, 220);
      }
    }
  });

  // Bouton "Alertes" topbar / rail → switch sur l'onglet Alertes du panneau droit
  document.querySelectorAll('[data-scroll-to-alerts], .rail-item[data-rail-target="alerts"]')
    .forEach(function (btn) {
      btn.addEventListener('click', function (e) {
        e.preventDefault();
        var alertsTab = document.querySelector('[data-rp-tab="alerts"]');
        if (alertsTab) alertsTab.click();
      });
    });

  var zoneSelect = document.getElementById('zoneSelect');
  if (zoneSelect) {
    zoneSelect.addEventListener('change', function () {
      switchZone(this.value);
    });
  }

  var adminSelect = document.getElementById('adminSelect');
  if (adminSelect) {
    adminSelect.addEventListener('change', function () {
      switchAdmin(this.value);
    });
  }

  var themeBtn = document.getElementById('themeToggle');
  if (themeBtn) {
    themeBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      toggleTheme();
    });
  }

  _initKpiTooltips();

  document.querySelectorAll('.nav-item').forEach(function (el) {
    _addRipple(el);
  });
}


/* ══════════════════════════════════════════════════════════
   CARTE — initialisation Leaflet
══════════════════════════════════════════════════════════ */

function initMap(lat, lng) {
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
    zoomControl: false,
    attributionControl: false,
    zoomAnimation: true,
    fadeAnimation: true,
    zoomSnap: 0.5,
    zoomDelta: 0.5,
    wheelPxPerZoomLevel: 80,
  });

  _tileLayer = _buildTileLayer((_settings && _settings.tileStyle) || 'light');
  _tileLayer.addTo(map);

  L.control.zoom({ position: 'bottomright' }).addTo(map);
  L.control.scale({ position: 'bottomleft', imperial: false, metric: true }).addTo(map);
  _addResetControl();

  lRoads = L.layerGroup().addTo(map);
  lFloods = L.layerGroup().addTo(map);
  lVeg = L.layerGroup().addTo(map);

  map.setView([_initLat, _initLng], 9);
  _mapReady = true;

  // Coordonnées + zoom (bas-droite carte) — branché ici car nécessite `map`
  _bindMapCoords();

  window.addEventListener('resize', function () {
    clearTimeout(window._gdResizeTimer);
    window._gdResizeTimer = setTimeout(function () {
      if (map) map.invalidateSize({ animate: false });
    }, 150);
  });

  /* Chargement async des géométries. SANS zone sélectionnée, on NE charge
     PAS toutes les données (≈128k routes = page figée) : on laisse
     l'utilisateur cliquer une commune pour voir uniquement SES données. */
  if (_activeZoneCode) {
    _loadMapData(_activeZoneCode);
  } else {
    _hideOverlay();
  }

  /* Markers communes (cliquables, popup détaillé) */
  loadZoneMarkers();

  /* Frontières administratives cliquables sur le fond de carte (adaptées au
     zoom : sous-préfectures/communes en détail, districts en vue pays). Clic
     → recharge la carte filtrée à cette seule zone. Rechargées au déplacement. */
  _loadClickableZones();
  map.on('moveend zoomend', _scheduleZonesReload);
  _addFocusControl();

  /* GEE — overlays NDVI/SAR : opt-in via chips (toggleOverlay).
     Pas de chargement automatique pour ne pas saturer la carte au boot
     et économiser des appels GEE quand l'utilisateur n'en a pas besoin.
     Toujours rafraîchi toutes les heures SI l'utilisateur l'a activé. */
  setInterval(_refreshActiveOverlays, 3600 * 1000);
}


/* Rafraîchit uniquement les overlays GEE actuellement visibles. */
function _refreshActiveOverlays() {
  if (map && lGeeNdvi && map.hasLayer(lGeeNdvi))  _loadNdviOverlay();
  if (map && lGeeFlood && map.hasLayer(lGeeFlood)) _loadSarOverlay();
}


/* Charge les géométries depuis /api/map-data/ et les passe au rendu Leaflet.
   Appelé au boot et à chaque changement de zone. */
var _focusAll = false;                          // false = axes structurants ; true = réseau complet
var _lastMapArgs = { zone: null, admin: null };

function _loadMapData(zoneCode, adminFilter) {
  _lastMapArgs = { zone: zoneCode || null, admin: adminFilter || null };
  var params = [];
  if (adminFilter)   params.push('admin=' + encodeURIComponent(adminFilter));
  else if (zoneCode) params.push('zone='  + encodeURIComponent(zoneCode));
  if (_focusAll)     params.push('focus=all');
  var url = '/api/map-data/' + (params.length ? '?' + params.join('&') : '');
  return fetch(url, {
    headers:     _apiHeaders(),
    redirect:    'manual',
    credentials: 'same-origin',
  })
    .then(function (resp) {
      if (resp.type === 'opaqueredirect' || resp.status === 401 || resp.status === 403) {
        return Promise.reject('session');
      }
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      return resp.json();
    })
    .then(function (data) {
      _renderData(data);
      updateLegend('all');
      if (_zoneSelCode) _showZonePanel(_zoneSelName, data);   // stats de la zone sélectionnée
      _hideOverlay();
    })
    .catch(function (err) {
      if (err === 'session') {
        _stopAlertPolling('session');  // déclenche le toast + bouton reconnexion
      } else {
        console.error('[GéoDash] Échec chargement /api/map-data/ :', err);
      }
      _hideOverlay();
    });
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
  o.style.opacity = '0';
  setTimeout(function () { o.style.display = 'none'; }, 500);
}


/* ══════════════════════════════════════════════════════════
   RENDU GÉOJSON
══════════════════════════════════════════════════════════ */

function _renderData(data) {
  if (!data || !map) return;
  // Idempotent : on vide les couches avant de re-rendre — indispensable au
  // filtrage par zone EN PLACE (clic commune) sans rechargement de page.
  if (lRoads)  lRoads.clearLayers();
  if (lFloods) lFloods.clearLayers();
  if (lVeg)    lVeg.clearLayers();
  var n = 0;
  // Au-delà d'un seuil de tronçons, on allège le rendu : pas d'ombre portée
  // (divise par 2 le nombre d'objets dessinés).
  var heavy = (data.routes || []).length > 1500;
  _allBounds = [];

  (data.routes || []).forEach(function (r) {
    var geo = r.geojson;
    if (!geo) return;
    try {
      var coords;
      if (geo.type === 'LineString') {
        coords = [geo.coordinates.map(function (c) { return [c[1], c[0]]; })];
      } else if (geo.type === 'MultiLineString') {
        coords = geo.coordinates.map(function (line) {
          return line.map(function (c) { return [c[1], c[0]]; });
        });
      } else return;

      var shadow = null;
      if (!heavy) {
        shadow = L.polyline(coords, {
          color: 'rgba(0,0,0,.45)', weight: 8, opacity: 1,
          lineCap: 'round', lineJoin: 'round', interactive: false,
        });
        lRoads.addLayer(shadow);
      }

      var opts = { color: r.color, weight: 5, opacity: .9, lineCap: 'round', lineJoin: 'round' };
      var lyr = L.polyline(coords, opts);

      lyr.on('mouseover', function (e) {
        this.setStyle({ weight: 8, opacity: 1 });
        if (shadow) shadow.setStyle({ weight: 12 });
        L.DomUtil.addClass(e.target._path, 'gd-hover');
      });
      lyr.on('mouseout', function (e) {
        this.setStyle({ weight: 5, opacity: .9 });
        if (shadow) shadow.setStyle({ weight: 8 });
        L.DomUtil.removeClass(e.target._path, 'gd-hover');
      });
      lyr.bindPopup(_popupRoad(r), { maxWidth: 280, className: 'gd-popup', autoPanPadding: [50, 50] });
      lRoads.addLayer(lyr);
      lyr._gdMeta = { type: 'road', id: r.id, name: r.name, color: r.color };
      _collectBounds(lyr);
      n++;
    } catch (e) { console.warn('[GéoDash] Route:', r.name, e); }
  });

  (data.floods || []).forEach(function (f) {
    var geo = f.geojson;
    if (!geo || !geo.type) return;
    try {
      var COLORS = {
        faible: GD_COLORS.flood_faible,
        modere: GD_COLORS.flood_modere,
        eleve: GD_COLORS.flood_eleve,
        critique: GD_COLORS.flood_critique,
      };
      var c = COLORS[f.risk_level] || GD_COLORS.flood;
      var lyr = null;

      if (geo.type === 'Polygon') {
        var coords = geo.coordinates[0].map(function (pt) { return [pt[1], pt[0]]; });
        if (coords.length < 3) return;
        lyr = L.polygon(coords, {
          color: c, fillColor: c, weight: 2, opacity: .9, fillOpacity: .25,
        });
        lyr.on('mouseover', function (e) {
          this.setStyle({ fillOpacity: .5, weight: 3, color: '#fff' });
          L.DomUtil.addClass(e.target._path, 'gd-hover');
        });
        lyr.on('mouseout', function (e) {
          this.setStyle({ fillOpacity: .25, weight: 2, color: c });
          L.DomUtil.removeClass(e.target._path, 'gd-hover');
        });

      } else if (geo.type === 'MultiPolygon') {
        var rings = geo.coordinates.map(function (p) {
          return p[0].map(function (pt) { return [pt[1], pt[0]]; });
        });
        lyr = L.polygon(rings, {
          color: c, fillColor: c, weight: 2, opacity: .9, fillOpacity: .25,
        });
        lyr.on('mouseover', function (e) {
          this.setStyle({ fillOpacity: .5, weight: 3, color: '#fff' });
          L.DomUtil.addClass(e.target._path, 'gd-hover');
        });
        lyr.on('mouseout', function (e) {
          this.setStyle({ fillOpacity: .25, weight: 2, color: c });
          L.DomUtil.removeClass(e.target._path, 'gd-hover');
        });

      } else if (geo.type === 'LineString') {
        var coords = geo.coordinates.map(function (pt) { return [pt[1], pt[0]]; });
        if (coords.length < 2) return;
        var shadow = L.polyline(coords, {
          color: 'rgba(0,0,0,.3)', weight: 7, opacity: 1,
          lineCap: 'round', lineJoin: 'round', interactive: false,
        });
        lFloods.addLayer(shadow);
        lyr = L.polyline(coords, {
          color: c, weight: 4, opacity: .85,
          lineCap: 'round', lineJoin: 'round',
          dashArray: f.risk_level === 'faible' ? '8,5' : null,
        });
        lyr.on('mouseover', function (e) {
          this.setStyle({ weight: 7, opacity: 1 });
          shadow.setStyle({ weight: 10 });
          L.DomUtil.addClass(e.target._path, 'gd-hover');
        });
        lyr.on('mouseout', function (e) {
          this.setStyle({ weight: 4, opacity: .85 });
          shadow.setStyle({ weight: 7 });
          L.DomUtil.removeClass(e.target._path, 'gd-hover');
        });

      } else if (geo.type === 'MultiLineString') {
        var segments = geo.coordinates.map(function (seg) {
          return seg.map(function (pt) { return [pt[1], pt[0]]; });
        });
        lyr = L.polyline(segments, {
          color: c, weight: 4, opacity: .85,
          lineCap: 'round', lineJoin: 'round',
        });
        lyr.on('mouseover', function (e) {
          this.setStyle({ weight: 7, opacity: 1 });
          L.DomUtil.addClass(e.target._path, 'gd-hover');
        });
        lyr.on('mouseout', function (e) {
          this.setStyle({ weight: 4, opacity: .85 });
          L.DomUtil.removeClass(e.target._path, 'gd-hover');
        });

      } else {
        console.debug('[GéoDash] Flood géom non supportée:', geo.type, f.name);
        return;
      }

      lyr.bindPopup(_popupFlood(f), { maxWidth: 280, className: 'gd-popup', autoPanPadding: [50, 50] });
      lyr._gdMeta = { type: 'flood', id: f.id, name: f.name, color: c };
      lFloods.addLayer(lyr);
      _collectBounds(lyr);
      n++;
    } catch (e) { console.warn('[GéoDash] Flood:', f.name, e); }
  });

  (data.vegetation || []).forEach(function (v) {
    var geo = v.geojson;
    if (!geo) return;
    try {
      var COLORS = {
        sparse: GD_COLORS.veg_sparse,
        moderate: GD_COLORS.veg_moderate,
        dense: GD_COLORS.veg_dense,
        very_dense: GD_COLORS.veg_very_dense,
      };
      var c = COLORS[v.density_class] || GD_COLORS.vegetation;
      var opts = { color: c, fillColor: c, weight: 1.5, opacity: .7, fillOpacity: .2, dashArray: '6,4' };
      var coords;
      if (geo.type === 'Polygon') {
        coords = geo.coordinates[0].map(function (c) { return [c[1], c[0]]; });
      } else if (geo.type === 'MultiPolygon') {
        coords = geo.coordinates.map(function (p) {
          return p[0].map(function (c) { return [c[1], c[0]]; });
        });
      } else {
        console.debug('[GéoDash] Veg géom non supportée:', geo.type, v.name);
        return;
      }
      var lyr = L.polygon(coords, opts);
      lyr.on('mouseover', function (e) {
        this.setStyle({ fillOpacity: .45, opacity: 1, weight: 2.5, dashArray: null });
        L.DomUtil.addClass(e.target._path, 'gd-hover');
      });
      lyr.on('mouseout', function (e) {
        this.setStyle({ fillOpacity: .2, opacity: .7, weight: 1.5, dashArray: '6,4' });
        L.DomUtil.removeClass(e.target._path, 'gd-hover');
      });
      lyr.bindPopup(_popupVeg(v), { maxWidth: 280, className: 'gd-popup', autoPanPadding: [50, 50] });
      lyr._gdMeta = { type: 'vegetation', id: v.id, name: v.name, color: c };
      lVeg.addLayer(lyr);
      _collectBounds(lyr);
      n++;
    } catch (e) { console.warn('[GéoDash] Veg:', v.name, e); }
  });

  if (_allBounds.length) {
    setTimeout(function () {
      if (map && _mapReady) {
        map.flyToBounds(L.latLngBounds(_allBounds).pad(.12), {
          duration: 1.4, easeLinearity: .25, maxZoom: 14,
        });
      }
    }, 250);
  }

  /* Score dégradation — calculé depuis les routes rendues */
  updateDegradationScore(data.routes || []);

  if (n > 0) {
    toast(n + ' objets chargés', 'ok');
  }
}

function _collectBounds(lyr) {
  try {
    var b = lyr.getBounds();
    if (b && b.isValid()) _allBounds.push(b.getSouthWest(), b.getNorthEast());
  } catch (_) { }
}


/* ══════════════════════════════════════════════════════════
   SCORE DÉGRADATION
   Calcule en temps réel le % de routes en mauvais état.
   Appelé depuis _renderData() après chaque chargement de zone.
══════════════════════════════════════════════════════════ */

/**
 * @param {Array} routes - data.routes depuis l'API (chaque objet a condition_score)
 */
function updateDegradationScore(routes) {
  var elVal = document.getElementById('kpiDegradation');
  var elSub = document.getElementById('kpiDegradationSub');
  var elBar = document.getElementById('kpiDegradationBar');

  if (!routes || routes.length === 0) {
    if (elVal) { elVal.textContent = '—'; elVal.className = 'kpi-value danger'; }
    if (elSub) elSub.textContent = 'Aucune route chargée';
    if (elBar) elBar.style.width = '0%';
    return;
  }

  var total = routes.length;
  var degraded = 0;
  var critical = 0;

  routes.forEach(function (r) {
    var sc = parseFloat(r.condition_score);
    if (isNaN(sc)) return;
    if (sc < 40) critical++;
    else if (sc < 70) degraded++;
  });

  var affected = degraded + critical;
  var pct = Math.round((affected / total) * 100);

  var color, cssClass;
  if (pct < 20) {
    color = 'var(--green2)'; cssClass = 'kpi-value good';
  } else if (pct < 50) {
    color = '#e67e22'; cssClass = 'kpi-value warn';
  } else {
    color = 'var(--red)'; cssClass = 'kpi-value danger';
  }

  if (elVal) {
    elVal.textContent = pct + ' %';
    elVal.className = cssClass;
  }

  if (elSub) {
    var parts = [];
    if (degraded > 0) parts.push(degraded + ' dégradé' + (degraded > 1 ? 's' : ''));
    if (critical > 0) parts.push(critical + ' critique' + (critical > 1 ? 's' : ''));
    elSub.textContent = parts.length
      ? parts.join(', ') + ' / ' + total + ' routes'
      : 'Toutes les routes sont en bon état';
  }

  if (elBar) {
    elBar.style.width = Math.min(pct, 100) + '%';
    elBar.style.background = color;
  }
}


/* ── Popups Leaflet ─────────────────────────────────────────────────────────── */

function _bar(pct, color, labelLeft, labelRight) {
  var labels = (labelLeft || labelRight)
    ? '<div class="pp-bar-label">'
    + '<span>' + (labelLeft || '') + '</span>'
    + '<span>' + (labelRight || '') + '</span>'
    + '</div>'
    : '';
  return '<div class="pp-bar">'
    + '<div class="pp-bar-f" style="width:' + Math.min(pct, 100) + '%;background:' + color + '"></div>'
    + '</div>' + labels;
}

function _scoreColor(score) {
  if (score >= 70) return 'var(--green2)';
  if (score >= 40) return '#e67e22';
  return 'var(--red)';
}

function _popupRoad(r) {
  var sc = r.condition_score || 0;
  var c = r.color || _scoreColor(sc);
  var status = (r.status_label || r.status || '—');
  return '<div class="popup-inner">'
    + '<div class="popup-hdr">'
    + '<div class="popup-type">Route</div>'
    + '<div class="popup-name">' + (r.name || 'Route inconnue') + '</div>'
    + '</div>'
    + '<div class="popup-body">'
    + '<div class="popup-row">'
    + '<span class="popup-lbl">Score état</span>'
    + '<span class="popup-val" style="color:' + c + '">' + sc + '<small style="color:var(--t4);font-weight:400">/100</small></span>'
    + '</div>'
    + _bar(sc, c, '0', '100')
    + '<div class="popup-row">'
    + '<span class="popup-lbl">Surface</span>'
    + '<span class="popup-val">' + (r.surface_type || '—') + '</span>'
    + '</div>'
    + (r.last_inspection
      ? '<div class="popup-row">'
      + '<span class="popup-lbl">Dernière inspection</span>'
      + '<span class="popup-val">' + r.last_inspection + '</span>'
      + '</div>'
      : '')
    + (r.notes
      ? '<div class="popup-notes">' + r.notes + '</div>'
      : '')
    + '</div>'
    + '<div class="popup-footer">'
    + '<span class="popup-badge badge-' + (r.status || 'ferme') + '">'
    + '<span class="badge-dot"></span>' + status
    + '</span>'
    + '</div>'
    + '</div>';
}

function _popupFlood(f) {
  var rs = f.risk_score || 0;
  var COLORS = {
    faible: GD_COLORS.flood_faible,
    modere: GD_COLORS.flood_modere,
    eleve: GD_COLORS.flood_eleve,
    critique: GD_COLORS.flood_critique,
  };
  var c = COLORS[f.risk_level] || GD_COLORS.flood;
  return '<div class="popup-inner">'
    + '<div class="popup-hdr">'
    + '<div class="popup-type">Zone inondation</div>'
    + '<div class="popup-name">' + (f.name || 'Zone inconnue') + '</div>'
    + '</div>'
    + '<div class="popup-body">'
    + '<div class="popup-row">'
    + '<span class="popup-lbl">Score de risque</span>'
    + '<span class="popup-val" style="color:' + c + '">' + rs + '<small style="color:var(--t4);font-weight:400">/100</small></span>'
    + '</div>'
    + _bar(rs, c, 'Faible', 'Critique')
    + '<div class="popup-row"><span class="popup-lbl">Niveau</span>'
    + '<span class="popup-val">' + (f.risk_label || f.risk_level || '—') + '</span></div>'
    + '<div class="popup-row"><span class="popup-lbl">Surface</span>'
    + '<span class="popup-val">' + (f.area_km2 || '—') + ' km²</span></div>'
    + '<div class="popup-row"><span class="popup-lbl">Pluviométrie</span>'
    + '<span class="popup-val">' + (f.rainfall_mm || '—') + ' mm</span></div>'
    + '</div>'
    + '<div class="popup-footer">'
    + '<span class="popup-badge badge-' + (f.risk_level === 'critique' ? 'critique' : f.risk_level === 'eleve' ? 'degrade' : 'bon') + '">'
    + '<span class="badge-dot"></span>' + (f.risk_label || f.risk_level || 'Inondation')
    + '</span>'
    + '</div>'
    + '</div>';
}

function _popupVeg(v) {
  var ndvi = v.ndvi_value || 0;
  var ndviPct = Math.round(((ndvi + 1) / 2) * 100);
  var COLORS = {
    sparse: GD_COLORS.veg_sparse,
    moderate: GD_COLORS.veg_moderate,
    dense: GD_COLORS.veg_dense,
    very_dense: GD_COLORS.veg_very_dense,
  };
  var c = COLORS[v.density_class] || GD_COLORS.vegetation;
  return '<div class="popup-inner">'
    + '<div class="popup-hdr">'
    + '<div class="popup-type">Végétation</div>'
    + '<div class="popup-name">' + (v.name || 'Zone végétale') + '</div>'
    + '</div>'
    + '<div class="popup-body">'
    + '<div class="popup-row"><span class="popup-lbl">NDVI</span>'
    + '<span class="popup-val" style="color:' + c + '">' + ndvi.toFixed(3) + '</span></div>'
    + _bar(ndviPct, c, '-1', '+1')
    + '<div class="popup-row"><span class="popup-lbl">Couverture</span>'
    + '<span class="popup-val">' + (v.coverage_percent || '—') + '%</span></div>'
    + '<div class="popup-row"><span class="popup-lbl">Classe</span>'
    + '<span class="popup-val">' + (v.density_label || v.density_class || '—') + '</span></div>'
    + (v.area_ha
      ? '<div class="popup-row"><span class="popup-lbl">Surface</span>'
      + '<span class="popup-val">' + v.area_ha + ' ha</span></div>'
      : '')
    + '</div>'
    + '<div class="popup-footer">'
    + '<span class="popup-badge" style="background:rgba(78,205,128,.1);color:var(--green2);border:1px solid rgba(78,205,128,.25)">'
    + '<span class="badge-dot"></span>' + (v.density_label || v.density_class || 'Végétation')
    + '</span>'
    + '</div>'
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

var _activeLayer = 'all';

/* ══════════════════════════════════════════════════════════
   LÉGENDE DYNAMIQUE
══════════════════════════════════════════════════════════ */

var _LEGEND_CONTENT = {
  all: function () {
    return [
      { type: 'title', text: 'Légende' },
      { type: 'section', text: 'Routes' },
      { type: 'line', color: '#28b857', label: 'Bon état  (≥ 70/100)' },
      { type: 'line', color: '#e67e22', label: 'Dégradé  (40-69/100)' },
      { type: 'line', color: '#f43f5e', label: 'Critique  (10-39/100)' },
      { type: 'line', color: '#94a3b8', label: 'Fermé  (< 10/100)', dashed: true },
      { type: 'sep' },
      { type: 'section', text: 'Zones hydrographiques' },
      { type: 'poly', color: '#22d3ee', label: 'Risque faible  (< 25)' },
      { type: 'poly', color: '#3b82f6', label: 'Risque modéré  (25-49)' },
      { type: 'poly', color: '#f97316', label: 'Risque élevé  (50-74)' },
      { type: 'poly', color: '#dc2626', label: 'Risque critique  (≥ 75)' },
      { type: 'sep' },
      { type: 'section', text: 'Végétation (OSM)' },
      { type: 'poly', color: '#bef264', label: 'Éparse  (NDVI < 0.20)', opacity: .6 },
      { type: 'poly', color: '#4ade80', label: 'Modérée  (0.20-0.39)', opacity: .6 },
      { type: 'poly', color: '#16a34a', label: 'Dense  (0.40-0.59)', opacity: .6 },
      { type: 'poly', color: '#14532d', label: 'Très dense  (≥ 0.60)', opacity: .6 },
    ].concat(_legendGeeRows());
  },
  roads: function () {
    return [
      { type: 'title', text: 'Routes — État' },
      {
        type: 'scale-bar',
        label: 'Score de condition (0-100)',
        stops: [
          { pct: 0, color: '#94a3b8', label: '0' },
          { pct: 10, color: '#ef4444', label: '10' },
          { pct: 40, color: '#f97316', label: '40' },
          { pct: 70, color: '#22c55e', label: '70' },
          { pct: 100, color: '#0e9f6e', label: '100' },
        ]
      },
      { type: 'sep' },
      { type: 'line', color: '#28b857', label: 'Bon — Surface praticable' },
      { type: 'line', color: '#e67e22', label: 'Dégradé — Ralentissement' },
      { type: 'line', color: '#f43f5e', label: 'Critique — Risque accès' },
      { type: 'line', color: '#94a3b8', label: 'Fermé — Inaccessible', dashed: true },
      { type: 'sep' },
      { type: 'info', text: 'Score calculé depuis les tags OSM (smoothness, surface, highway)' },
    ];
  },
  floods: function () {
    return [
      { type: 'title', text: 'Inondations — Risque' },
      {
        type: 'scale-bar',
        label: 'Score de risque (0-100)',
        stops: [
          { pct: 0, color: '#22d3ee', label: '0' },
          { pct: 25, color: '#3b82f6', label: '25' },
          { pct: 50, color: '#f97316', label: '50' },
          { pct: 75, color: '#dc2626', label: '75' },
          { pct: 100, color: '#7f1d1d', label: '100' },
        ]
      },
      { type: 'sep' },
      { type: 'poly', color: '#22d3ee', label: 'Faible  — Hors saison sèche' },
      { type: 'poly', color: '#3b82f6', label: 'Modéré — Surveillance' },
      { type: 'poly', color: '#f97316', label: 'Élevé  — Précautions requises' },
      { type: 'poly', color: '#dc2626', label: 'Critique — Évacuation possible' },
      { type: 'line', color: '#3b82f6', label: 'Cours d\'eau (rivière / canal)' },
      { type: 'sep' },
    ].concat(_legendGeeRows('flood'));
  },
  vegetation: function () {
    return [
      { type: 'title', text: 'Végétation — NDVI' },
      {
        type: 'scale-bar',
        label: 'Indice NDVI (-1 à +1)',
        stops: [
          { pct: 0, color: '#d73027', label: '-1' },
          { pct: 30, color: '#fee08b', label: '0' },
          { pct: 60, color: '#66bd63', label: '0.4' },
          { pct: 100, color: '#1a9850', label: '+1' },
        ]
      },
      { type: 'sep' },
      { type: 'poly', color: '#bef264', label: 'Éparse  NDVI < 0.20', opacity: .6 },
      { type: 'poly', color: '#4ade80', label: 'Modérée NDVI 0.20-0.39', opacity: .6 },
      { type: 'poly', color: '#16a34a', label: 'Dense   NDVI 0.40-0.59', opacity: .6 },
      { type: 'poly', color: '#14532d', label: 'Très dense NDVI ≥ 0.60', opacity: .6 },
      { type: 'sep' },
      { type: 'info', text: 'NDVI > 0 = végétation, = 0 = sol nu, < 0 = eau/nuage' },
    ].concat(_legendGeeRows('ndvi'));
  },
  degradation: function () {
    return [
      { type: 'title', text: 'Dégradation routes' },
      {
        type: 'scale-bar',
        label: 'Score de dégradation (0-100 %)',
        stops: [
          { pct: 0, color: '#22c55e', label: '0 %' },
          { pct: 20, color: '#f97316', label: '20 %' },
          { pct: 50, color: '#ef4444', label: '50 %' },
          { pct: 100, color: '#7f1d1d', label: '100 %' },
        ]
      },
      { type: 'sep' },
      { type: 'line', color: '#22c55e', label: '< 20 % — Faible dégradation' },
      { type: 'line', color: '#f97316', label: '20-50 % — Dégradation modérée' },
      { type: 'line', color: '#ef4444', label: '> 50 % — Dégradation sévère' },
      { type: 'sep' },
      { type: 'info', text: '% de segments avec score < 70/100 (dégradé ou critique)' },
    ];
  },
};

function _legendGeeRows(context) {
  var rows = [];
  if (lGeeNdvi && context !== 'flood') {
    rows.push({ type: 'sep' });
    rows.push({ type: 'section', text: 'Satellite Sentinel-2 (GEE)' });
    rows.push({
      type: 'scale-bar',
      label: 'NDVI satellite',
      stops: [
        { pct: 0, color: '#d73027', label: '0.0' },
        { pct: 50, color: '#fee08b', label: '0.4' },
        { pct: 100, color: '#1a9850', label: '0.8' },
      ]
    });
  }
  if (lGeeFlood && context !== 'ndvi') {
    rows.push({ type: 'sep' });
    rows.push({ type: 'section', text: 'SAR Sentinel-1 (GEE)' });
    rows.push({ type: 'poly', color: '#3b82f6', label: 'Zone inondée détectée (SAR)', opacity: .7 });
    rows.push({ type: 'info', text: 'Détection radar indépendante des nuages' });
  }
  return rows;
}

function _buildLegendHTML(rows) {
  var html = '';
  rows.forEach(function (row) {
    switch (row.type) {
      case 'title':
        html += '<div class="ml-ttl">' + row.text + '</div>';
        break;
      case 'section':
        html += '<div class="ml-section">' + row.text + '</div>';
        break;
      case 'sep':
        html += '<div class="ml-sep"></div>';
        break;
      case 'line':
        var style = row.dashed
          ? 'background:repeating-linear-gradient(90deg,' + row.color + ' 0 5px,transparent 5px 8px);height:3px;'
          : 'background:' + row.color + ';height:3px;';
        html += '<div class="ml-row">'
          + '<div class="ml-line" style="' + style + '"></div>'
          + '<span>' + row.label + '</span>'
          + '</div>';
        break;
      case 'poly':
        html += '<div class="ml-row">'
          + '<div class="ml-poly" style="background:' + row.color + ';opacity:' + (row.opacity || .8) + '"></div>'
          + '<span>' + row.label + '</span>'
          + '</div>';
        break;
      case 'scale-bar':
        var grad = row.stops.map(function (s) { return s.color + ' ' + s.pct + '%'; }).join(', ');
        var ticks = row.stops.map(function (s) {
          return '<span style="left:' + s.pct + '%">' + s.label + '</span>';
        }).join('');
        html += '<div class="ml-scale">'
          + '<div class="ml-scale-lbl">' + row.label + '</div>'
          + '<div class="ml-scale-bar" style="background:linear-gradient(90deg,' + grad + ')"></div>'
          + '<div class="ml-scale-ticks">' + ticks + '</div>'
          + '</div>';
        break;
      case 'info':
        html += '<div class="ml-info">' + row.text + '</div>';
        break;
    }
  });
  return html;
}

function updateLegend(type) {
  var el = document.querySelector('.map-legend');
  if (!el) return;
  var fn = _LEGEND_CONTENT[type] || _LEGEND_CONTENT.all;
  el.innerHTML = _buildLegendHTML(fn());
}

function _setKpiActive(type) {
  document.querySelectorAll('.kpi-card').forEach(function (c) {
    c.classList.remove('kpi-active');
  });
  var card = document.querySelector('.kpi-card[data-layer="' + type + '"]');
  if (card) card.classList.add('kpi-active');
}

function _pulseLayer(type) {
  var targets = type === 'all'
    ? ['roads', 'floods', 'vegetation']
    : [type];

  targets.forEach(function (k) {
    var lg = layerRef[k] ? layerRef[k]() : null;
    if (!lg) return;
    lg.getLayers().forEach(function (lyr) {
      if (!lyr.setStyle || lyr.options.interactive === false) return;
      var orig = { opacity: lyr.options.opacity, fillOpacity: lyr.options.fillOpacity };
      lyr.setStyle({ opacity: 1, fillOpacity: Math.min((orig.fillOpacity || 0) + .35, 1) });
      setTimeout(function () { lyr.setStyle({ opacity: orig.opacity, fillOpacity: orig.fillOpacity }); }, 320);
    });
  });
}

function setLayer(type, btn) {
  _activeLayer = type;

  document.querySelectorAll('[data-set-layer]').forEach(function (b) {
    b.classList.remove('active');
  });
  if (btn) {
    btn.classList.add('active');
  } else {
    var found = document.querySelector('[data-set-layer="' + type + '"]');
    if (found) found.classList.add('active');
  }

  document.querySelectorAll('.nav-item[data-set-layer]').forEach(function (ni) {
    ni.classList.toggle('active', ni.dataset.setLayer === type);
  });

  _setKpiActive(type);

  if (!map) return;

  var vis = {
    all: { roads: 1, floods: 1, vegetation: 1 },
    roads: { roads: 1, floods: 0, vegetation: 0 },
    floods: { roads: 0, floods: 1, vegetation: 0 },
    vegetation: { roads: 0, floods: 0, vegetation: 1 },
    degradation: { roads: 1, floods: 0, vegetation: 0 },
  }[type] || { roads: 1, floods: 1, vegetation: 1 };

  Object.keys(vis).forEach(function (k) {
    var v = vis[k];
    var lg = layerRef[k]();
    if (!lg) return;
    v ? map.addLayer(lg) : map.removeLayer(lg);
    layerVis[k] = !!v;
    var toggle = document.querySelector('[data-toggle-layer="' + k + '"]');
    if (toggle) toggle.classList.toggle('off', !v);
  });

  var flew = _flyToLayer(type === 'degradation' ? 'roads' : type);
  setTimeout(function () { _pulseLayer(type); }, flew ? 900 : 80);
  updateLegend(type);
}

function _flyToLayer(type) {
  if (!map || !_mapReady) return false;
  if (type === 'all') { flyToAll(); return true; }

  var lg = layerRef[type] ? layerRef[type]() : null;
  if (!lg) return false;

  var bounds = [];
  var totalLayers = 0;

  lg.getLayers().forEach(function (l) {
    totalLayers++;
    try {
      var b = l.getBounds ? l.getBounds() : null;
      if (b && b.isValid()) bounds.push(b.getSouthWest(), b.getNorthEast());
    } catch (_) { }
  });

  if (bounds.length) {
    map.flyToBounds(
      L.latLngBounds(bounds).pad(.15),
      { duration: 1.1, easeLinearity: .3, maxZoom: 14 }
    );
    return true;
  }

  if (totalLayers > 0) {
    map.flyTo([_initLat, _initLng], 11, { duration: 1.1 });
    return true;
  }

  var layerNames = { roads: 'routes', floods: 'zones inondation', vegetation: 'zones végétation' };
  toast('Aucune ' + (layerNames[type] || 'donnée') + ' pour cette zone', 'nfo');
  map.flyTo([_initLat, _initLng], 11, { duration: 1.1 });
  return false;
}

function flyToAll() {
  if (!map || !_mapReady) return;
  if (_allBounds.length) {
    map.flyToBounds(
      L.latLngBounds(_allBounds).pad(.12),
      { duration: 1.4, easeLinearity: .25, maxZoom: 14 }
    );
  } else {
    map.flyTo([_initLat, _initLng], 9, { duration: 1.2 });
  }
}


/* ══════════════════════════════════════════════════════════
   GOOGLE EARTH ENGINE
══════════════════════════════════════════════════════════ */

/* Headers à envoyer sur tous les fetchs API JSON.
   - `Accept: application/json` ET `X-Requested-With: XMLHttpRequest` font que
     LoginRequiredMiddleware (dashboard/middleware.py) répond 401 JSON au lieu
     de rediriger 302 vers Keycloak. Sans ces headers, le 302 est interprété
     comme `opaqueredirect` côté JS et déclenche un faux positif "session
     expirée" même quand la session est encore valide. */
function _apiHeaders(extra) {
  var h = {
    'Accept':           'application/json',
    'X-Requested-With': 'XMLHttpRequest',
  };
  if (extra) Object.keys(extra).forEach(function (k) { h[k] = extra[k]; });
  return h;
}

function _authFetchJSON(url) {
  return fetch(url, {
    headers:     _apiHeaders(),
    redirect:    'manual',
    credentials: 'same-origin',
  })
    .then(function (r) {
      if (r.type === 'opaqueredirect' || r.status === 401 || r.status === 403) {
        return Promise.reject('session');
      }
      if (!r.ok) return Promise.reject(r.status);
      return r.json();
    });
}

/* Construit la query string GEE depuis les params URL courants
   (admin / zone), pour que les overlays soient clippés sur la vraie
   géométrie active. */
function _geeQueryString() {
  var url = new URL(window.location.href);
  var admin = url.searchParams.get('admin');
  var zone  = url.searchParams.get('zone');
  var qs = new URLSearchParams();
  if (admin) qs.set('admin', admin);
  if (zone)  qs.set('zone',  zone);
  return qs.toString() ? ('?' + qs.toString()) : '';
}


function _loadNdviOverlay() {
  if (!map || !_mapReady) return Promise.resolve();
  return _authFetchJSON('/api/gee/ndvi/' + _geeQueryString())
    .then(function (data) {
      if (!data || data.error || !data.tiles_url) {
        toast(data && data.error ? data.error : 'NDVI indisponible', 'err');
        return;
      }
      if (lGeeNdvi) { map.removeLayer(lGeeNdvi); lGeeNdvi = null; }
      lGeeNdvi = L.tileLayer(data.tiles_url, {
        opacity: 0.55, maxNativeZoom: 18, maxZoom: 22,
        attribution: 'NDVI · Sentinel-2 · GEE',
      });
      lGeeNdvi.addTo(map);
      toast('Couche NDVI affichée' + (data.image_date ? ' (' + data.image_date + ')' : ''), 'ok');
    })
    .catch(function (err) {
      if (err === 'session') return;
      console.warn('[GEE NDVI] Erreur :', err);
      toast('NDVI indisponible', 'err');
    });
}


function _loadSarOverlay() {
  if (!map || !_mapReady) return Promise.resolve();
  return _authFetchJSON('/api/gee/flood/' + _geeQueryString())
    .then(function (data) {
      if (!data || data.error || !data.tiles_url) {
        toast(data && data.error ? data.error : 'SAR indisponible', 'err');
        return;
      }
      if (lGeeFlood) { map.removeLayer(lGeeFlood); lGeeFlood = null; }
      lGeeFlood = L.tileLayer(data.tiles_url, {
        opacity: 0.65, maxNativeZoom: 18, maxZoom: 22,
        attribution: 'SAR · Sentinel-1 · GEE',
      });
      lGeeFlood.addTo(map);
      toast('Couche SAR affichée — risque ' + (data.risk_level || 'n/a'), 'ok');
    })
    .catch(function (err) {
      if (err === 'session') return;
      console.warn('[GEE Flood] Erreur :', err);
      toast('SAR indisponible', 'err');
    });
}


/* Couche frontières administratives (districts + régions) — Polygons clippés
   par /api/admin/divisions/. Toggle on/off via chip. */
let lAdminBoundaries = null;

function _loadAdminBoundariesOverlay() {
  if (!map || !_mapReady) return Promise.resolve();
  return fetch('/api/admin/divisions/?level=district', {
    headers: _apiHeaders(), credentials: 'same-origin', redirect: 'manual',
  })
    .then(function (r) {
      if (r.type === 'opaqueredirect' || r.status === 401 || r.status === 403) {
        return Promise.reject('session');
      }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then(function (data) {
      if (!data || !data.features) return;
      if (lAdminBoundaries) { map.removeLayer(lAdminBoundaries); lAdminBoundaries = null; }
      lAdminBoundaries = L.geoJSON(data, {
        style: function (feature) {
          var auto = feature.properties.is_autonomous;
          return {
            color:       auto ? '#FFD700' : '#94A3B8',
            weight:      auto ? 2.5 : 1.5,
            opacity:     0.75,
            fillOpacity: 0.03,
            dashArray:   auto ? null : '4,3',
          };
        },
        onEachFeature: function (feature, layer) {
          var p = feature.properties;
          layer.bindTooltip(p.is_autonomous ? p.name + ' (autonome)' : p.name,
            { sticky: true, direction: 'center' });
        },
      }).addTo(map);
    })
    .catch(function (err) {
      if (err === 'session') return;
      console.warn('[Admin boundaries] échec :', err);
    });
}


/* ══════════════════════════════════════════════════════════
   ZONES CLIQUABLES (communes) — frontières dessinées sur le fond
   de carte. Clic sur une commune → /api/map-data/?admin=commune:CODE
   (filtre ST_Intersects côté serveur) : seules SES données s'affichent.
══════════════════════════════════════════════════════════ */
let lZones = null;        // L.geoJSON des polygones de communes
let _zoneSelCode = null;  // code de la commune sélectionnée (ou null)
let _zoneSelName = null;  // nom de la zone sélectionnée (pour le panneau de stats)

var _ZONE_STYLE = {
  base:     { color: '#2563eb', weight: 1.2, opacity: 0.55, fillColor: '#2563eb', fillOpacity: 0.04 },
  hover:    { color: '#1d4ed8', weight: 2.2, opacity: 0.95, fillOpacity: 0.12 },
  selected: { color: '#dc2626', weight: 3,   opacity: 1,    fillColor: '#dc2626', fillOpacity: 0.07 },
  dimmed:   { color: '#94a3b8', weight: 0.8, opacity: 0.30, fillOpacity: 0.02 },
};

function _zoneStyleFor(code) {
  if (_zoneSelCode === null) return _ZONE_STYLE.base;
  return code === _zoneSelCode ? _ZONE_STYLE.selected : _ZONE_STYLE.dimmed;
}

/* Niveau de découpage selon le zoom : districts quand on dézoome (vue pays,
   14 entités), sous-préfectures + communes quand on est zoomé (détail). */
function _zonesLevelForZoom() {
  var z = map ? map.getZoom() : 9;
  return z < 9 ? 'district' : 'sousprefecture,commune';
}

function _mapBboxParam() {
  var b = map.getBounds();
  return [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    .map(function (x) { return x.toFixed(4); }).join(',');
}

let _zonesReqKey = null;   // clé (niveau|bbox) du dernier chargement — dédup

function _loadClickableZones() {
  if (!map || !_mapReady) return Promise.resolve();
  var level   = _zonesLevelForZoom();
  var useBbox = (level !== 'district');   // district = 14 entités → on charge tout une fois
  var bbox    = useBbox ? _mapBboxParam() : '';
  var key     = level + '|' + (useBbox ? bbox : 'all');
  if (key === _zonesReqKey) return Promise.resolve();   // rien de pertinent n'a changé
  _zonesReqKey = key;

  var url = '/api/admin/divisions/?level=' + encodeURIComponent(level)
          + (bbox ? '&bbox=' + encodeURIComponent(bbox) : '');
  return fetch(url, { headers: _apiHeaders(), credentials: 'same-origin', redirect: 'manual' })
    .then(function (r) {
      if (r.type === 'opaqueredirect' || r.status === 401 || r.status === 403) return Promise.reject('session');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then(function (data) {
      if (!data || !data.features) return;
      if (lZones) { map.removeLayer(lZones); lZones = null; }
      lZones = L.geoJSON(data, {
        style: function (f) { return _zoneStyleFor(f.properties.code); },
        onEachFeature: function (feature, layer) {
          var p = feature.properties;
          layer.bindTooltip(p.name, { sticky: true, direction: 'center' });
          layer.on('mouseover', function () { if (_zoneSelCode !== p.code) layer.setStyle(_ZONE_STYLE.hover); });
          layer.on('mouseout',  function () { layer.setStyle(_zoneStyleFor(p.code)); });
          layer.on('click', function (e) { L.DomEvent.stop(e); _selectZone(p, layer); });
        },
      }).addTo(map);
      // Sous les routes : clic sur une route = popup, clic dans le vide = sélection zone.
      lZones.bringToBack();
    })
    .catch(function (err) {
      if (err === 'session') return;
      _zonesReqKey = null;
      console.warn('[Zones] échec :', err);
    });
}

/* Recharge debouncée des frontières au déplacement / zoom de la carte. */
let _zonesReloadTimer = null;
function _scheduleZonesReload() {
  clearTimeout(_zonesReloadTimer);
  _zonesReloadTimer = setTimeout(_loadClickableZones, 350);
}

/* Bascule « Axes principaux ⇄ Tout le réseau » — allègement du rendu. */
function _addFocusControl() {
  var Ctrl = L.Control.extend({
    options: { position: 'topright' },
    onAdd: function () {
      var b = L.DomUtil.create('button');
      b.type = 'button';
      b.textContent = 'Axes principaux';
      b.title = 'Basculer entre axes structurants et réseau complet';
      b.style.cssText = 'cursor:pointer;border:0;background:rgba(255,255,255,.96);color:#0f172a;'
        + 'padding:5px 10px;border-radius:8px;box-shadow:0 1px 5px rgba(0,0,0,.25);'
        + 'font:12px/1.2 system-ui,sans-serif;';
      L.DomEvent.disableClickPropagation(b);
      b.onclick = function () {
        _focusAll = !_focusAll;
        b.textContent      = _focusAll ? 'Tout le réseau' : 'Axes principaux';
        b.style.background  = _focusAll ? '#2563eb' : 'rgba(255,255,255,.96)';
        b.style.color       = _focusAll ? '#fff' : '#0f172a';
        // Ne recharge que si une zone est sélectionnée (sinon focus=all sans
        // filtre = tout le pays = page figée).
        if (_lastMapArgs.zone || _lastMapArgs.admin) {
          _loadMapData(_lastMapArgs.zone, _lastMapArgs.admin);
        }
      };
      return b;
    },
  });
  new Ctrl().addTo(map);
}

function _selectZone(props, layer) {
  _zoneSelCode = props.code;
  if (lZones) lZones.eachLayer(function (l) {
    if (l.feature && l.feature.properties) l.setStyle(_zoneStyleFor(l.feature.properties.code));
  });
  _zoneSelName = props.name;
  _loadMapData(null, (props.level || 'commune') + ':' + props.code);   // données de cette seule zone
  try { map.fitBounds(layer.getBounds(), { padding: [24, 24], maxZoom: 14 }); } catch (e) {}
  _showZonePanel(props.name, null);   // nom + "Chargement…" ; les stats arrivent au rendu
}

function _clearZoneSel() {
  _zoneSelCode = null;
  _zoneSelName = null;
  if (lZones) lZones.eachLayer(function (l) { l.setStyle(_ZONE_STYLE.base); });
  _loadMapData();          // toutes les données
  _hideZonePanel();
}

/* Calcule les stats d'une zone à partir des données déjà chargées (aucun
   appel réseau supplémentaire). */
function _zoneStats(data) {
  var routes = (data && data.routes) || [];
  var floods = (data && data.floods) || [];
  var veg    = (data && data.vegetation) || [];
  var s = { bon: 0, degrade: 0, critique: 0, ferme: 0 };
  var sum = 0, nsc = 0;
  routes.forEach(function (r) {
    if (s[r.status] !== undefined) s[r.status]++;
    if (typeof r.condition_score === 'number') { sum += r.condition_score; nsc++; }
  });
  var fsum = 0, fn = 0, fcrit = 0;
  floods.forEach(function (f) {
    if (typeof f.risk_score === 'number') { fsum += f.risk_score; fn++; }
    if (f.risk_level === 'critique') fcrit++;
  });
  var vsum = 0, vn = 0, vdense = 0;
  veg.forEach(function (v) {
    if (typeof v.ndvi_value === 'number') { vsum += v.ndvi_value; vn++; }
    if (v.density_class === 'dense' || v.density_class === 'very_dense') vdense++;
  });
  var tot = routes.length;
  var deg = s.degrade + s.critique + s.ferme;
  return {
    tot: tot, bon: s.bon, degrade: s.degrade, critique: s.critique, ferme: s.ferme,
    pctDeg: tot ? Math.round(100 * deg / tot) : 0,
    avg: nsc ? Math.round(sum / nsc) : 0,
    crit: s.critique + s.ferme,
    floods: floods.length, floodCrit: fcrit, avgFlood: fn ? Math.round(fsum / fn) : 0,
    veg: veg.length, vDense: vdense, avgNdvi: vn ? (vsum / vn).toFixed(2) : '0.00',
  };
}

function _stChip(label, n, color) {
  return '<span style="background:' + color + ';color:#fff;border-radius:6px;'
    + 'padding:2px 7px;font-size:11px;white-space:nowrap">' + label + ' ' + n + '</span>';
}

function _rpSetKpi(sel, val) { var e = document.querySelector(sel); if (e) e.textContent = val; }

/* Met à jour le PANNEAU DE DROITE « Zone sélectionnée » avec les stats de la
   zone cliquée (pas de panneau flottant — évite tout chevauchement). */
function _showZonePanel(name, data) {
  var elTag   = document.getElementById('rpTag');
  var elName  = document.getElementById('rpName');
  var elSub   = document.getElementById('rpSub');
  var elStats = document.getElementById('rpRoadStats');
  var elPill  = document.getElementById('rpRoadPill');
  var elReset = document.getElementById('rpReset');

  if (elTag)   elTag.textContent = 'Zone sélectionnée';
  if (elName)  elName.textContent = name || 'Zone';
  if (elReset) { elReset.style.display = ''; elReset.onclick = _clearZoneSel; }

  if (!data) { if (elSub) elSub.textContent = 'Chargement…'; return; }

  var st = _zoneStats(data);
  var roadLabel = (typeof _focusAll !== 'undefined' && _focusAll) ? 'tronçon' : 'axe';
  if (elSub)  elSub.innerHTML = '<span><b>' + st.tot + '</b> ' + roadLabel + (st.tot > 1 ? 's' : '')
                + ' · <b>' + st.pctDeg + '%</b> dégradé</span>';
  if (elPill) elPill.textContent = st.avg + '/100';

  // KPIs (sélecteurs stables via data-layer)
  _rpSetKpi('.rp-kpi[data-layer="roads"] .rp-kpi-val b', st.avg);
  _rpSetKpi('.rp-kpi[data-layer="floods"] .rp-kpi-val b', st.avgFlood);
  _rpSetKpi('.rp-kpi[data-layer="vegetation"] .rp-kpi-val b', st.avgNdvi);

  if (elStats) {
    elStats.innerHTML =
        '<div><b style="color:var(--ink);font-size:14px;display:block">' + st.tot + '</b>Axes</div>'
      + '<div><b style="color:var(--ink);font-size:14px;display:block">' + st.crit + '</b>En alerte</div>'
      + '<div><b style="color:var(--ink);font-size:14px;display:block">' + st.floods + '</b>Inondations</div>'
      + '<div><b style="color:var(--ink);font-size:14px;display:block">' + st.floodCrit + '</b>Critiques</div>'
      + '<div style="grid-column:1/-1;display:flex;flex-wrap:wrap;gap:5px;margin-top:8px;'
      +      'padding-top:8px;border-top:1px solid var(--line,#e2e8f0)">'
      +   _stChip('Bon', st.bon, '#16a34a') + _stChip('Dégradé', st.degrade, '#f59e0b')
      +   _stChip('Critique', st.critique, '#dc2626') + _stChip('Fermé', st.ferme, '#7f1d1d')
      + '</div>';
  }
}

function _hideZonePanel() {
  var elTag   = document.getElementById('rpTag');
  var elName  = document.getElementById('rpName');
  var elSub   = document.getElementById('rpSub');
  var elReset = document.getElementById('rpReset');
  if (elTag)   elTag.textContent = 'Vue globale';
  if (elName)  elName.textContent = "Côte d'Ivoire";
  if (elSub)   elSub.textContent = '';
  if (elReset) elReset.style.display = 'none';
}


/* Toggle d'un overlay GEE/admin. Charge à la 1ʳᵉ activation, puis
   show/hide sans re-fetch tant que la session est ouverte. */
function toggleOverlay(name, chip) {
  if (chip) chip.classList.toggle('on');
  var isOn = chip ? chip.classList.contains('on') : true;

  if (name === 'ndvi') {
    if (!isOn && lGeeNdvi) { map.removeLayer(lGeeNdvi); return; }
    if (isOn && lGeeNdvi)  { lGeeNdvi.addTo(map); return; }
    if (isOn) _loadNdviOverlay();
    return;
  }
  if (name === 'sar') {
    if (!isOn && lGeeFlood) { map.removeLayer(lGeeFlood); return; }
    if (isOn && lGeeFlood)  { lGeeFlood.addTo(map); return; }
    if (isOn) _loadSarOverlay();
    return;
  }
  if (name === 'admin') {
    if (!isOn && lAdminBoundaries) { map.removeLayer(lAdminBoundaries); return; }
    if (isOn && lAdminBoundaries)  { lAdminBoundaries.addTo(map); return; }
    if (isOn) _loadAdminBoundariesOverlay();
    return;
  }
}


/* Stub conservé pour rétrocompatibilité : ancien refreshGeeLayer().
   Force le re-fetch des overlays NDVI/SAR si déjà actifs. */
function refreshGeeLayer(_zoneCodeIgnored) {
  _refreshActiveOverlays();
}


/* ══════════════════════════════════════════════════════════
   NAVIGATION & ALERTES
══════════════════════════════════════════════════════════ */

/* ══════════════════════════════════════════════════════════
   FILTRES — reload complet (stable, prévisible)

   Le SPA via innerHTML replacement a été tenté mais cassait les bindings
   des KPIs/tabs/alertes (event listeners attachés au DOM remplacé). Un vrai
   SPA demanderait un refactor systématique en event delegation. Pour
   l'instant on reste sur le reload simple : la home pèse ~50 KB grâce au
   découplage géométries → /api/map-data/, donc le reload est rapide (~300 ms)
   et toutes les interactions restent fiables.
   ══════════════════════════════════════════════════════════ */

function switchZone(code) {
  var url = new URL(window.location.href);
  if (code) url.searchParams.set('zone', code);
  else      url.searchParams.delete('zone');
  window.location.href = url.toString();
}

function switchAdmin(value) {
  var url = new URL(window.location.href);
  if (value) {
    url.searchParams.set('admin', value);
    url.searchParams.delete('zone');
  } else {
    url.searchParams.delete('admin');
    url.searchParams.delete('zone');
  }
  window.location.href = url.toString();
}

/**
 * Met à jour les liens d'export avec la zone active.
 */
function _updateExportLinks() {
  var suffix = _activeZoneCode ? ('?zone=' + encodeURIComponent(_activeZoneCode)) : '';

  var csvLink = document.querySelector('a[href*="/api/alerts/export/"]');
  if (csvLink) {
    csvLink.href = '/api/alerts/export/' + suffix;
    csvLink.title = _activeZoneCode
      ? 'Exporter les alertes de ' + _activeZoneCode + ' (CSV)'
      : 'Exporter toutes les alertes (CSV)';
  }

  var geoLink = document.querySelector('a[href*="/api/roads/export/"]');
  if (geoLink) {
    geoLink.href = '/api/roads/export/' + suffix;
    geoLink.title = _activeZoneCode
      ? 'Exporter les routes de ' + _activeZoneCode + ' (GeoJSON)'
      : 'Exporter toutes les routes (GeoJSON)';
  }
}

var _alertMarker = null;

function _placeAlertMarker(lat, lng, color) {
  if (_alertMarker) { map.removeLayer(_alertMarker); _alertMarker = null; }

  var icon = L.divIcon({
    className: '',
    iconSize: [36, 36],
    iconAnchor: [18, 18],
    html: '<div class="alert-pulse-marker" style="--mc:' + (color || GD_COLORS.alert) + '">'
      + '<div class="apm-ring"></div>'
      + '<div class="apm-dot"></div>'
      + '</div>',
  });
  _alertMarker = L.marker([lat, lng], { icon: icon, zIndexOffset: 1000 });
  _alertMarker.addTo(map);

  setTimeout(function () {
    if (_alertMarker) { map.removeLayer(_alertMarker); _alertMarker = null; }
  }, 8000);
}

/**
 * Localise une alerte sur la carte.
 *
 * Stratégie de résolution des coordonnées (ordre de priorité) :
 *   1. lat/lng de l'alerte elle-même si non nuls
 *   2. lat/lng du centroïde de la zone (zone_lat / zone_lng passés depuis le template)
 *   3. Zoom sur la couche correspondante si les deux précédents échouent
 *   4. Recentrage sur la zone courante en dernier recours
 *
 * @param {number} lat        - latitude de l'alerte (peut être 0)
 * @param {number} lng        - longitude de l'alerte (peut être 0)
 * @param {string} category   - 'road' | 'flood' | 'vegetation'
 * @param {number} zoneLat    - latitude du centroïde de la zone (fallback)
 * @param {number} zoneLng    - longitude du centroïde de la zone (fallback)
 */
function focusAlert(lat, lng, category, zoneLat, zoneLng) {
  if (!map || !_mapReady) return;

  var color = GD_COLORS[category] || GD_COLORS.alert;
  var ZOOM_MAP = { road: 14, flood: 12, vegetation: 12 };
  var targetZoom = ZOOM_MAP[category] || 16;

  /* 1. Coordonnées précises de l'alerte */
  var la = parseFloat(lat);
  var ln = parseFloat(lng);
  if (!isNaN(la) && !isNaN(ln) && (Math.abs(la) > 0.0001 || Math.abs(ln) > 0.0001)) {
    map.flyTo([la, ln], targetZoom, { duration: 1.2, easeLinearity: .25 });
    setTimeout(function () { _placeAlertMarker(la, ln, color); }, 700);
    toast('Alerte localisée', 'nfo');
    return;
  }

  /* 2. Centroïde de la zone (transmis via data-zone-lat / data-zone-lng) */
  var zla = parseFloat(zoneLat);
  var zln = parseFloat(zoneLng);
  if (!isNaN(zla) && !isNaN(zln) && (Math.abs(zla) > 0.0001 || Math.abs(zln) > 0.0001)) {
    map.flyTo([zla, zln], Math.min(targetZoom, 13), { duration: 1.2, easeLinearity: .25 });
    setTimeout(function () { _placeAlertMarker(zla, zln, color); }, 700);
    toast('Position approximative (centroïde de zone)', 'nfo');
    return;
  }

  /* 3. Zoom sur la couche correspondante */
  var layerMap = { road: 'roads', flood: 'floods', vegetation: 'vegetation' };
  var layerKey = layerMap[category];
  if (layerKey) {
    var flew = _flyToLayer(layerKey);
    if (flew) return;
  }

  /* 4. Dernier recours : recentrage sur la zone courante */
  map.flyTo([_initLat, _initLng], 11, { duration: 1.1 });
  toast('Coordonnées manquantes — zone approximative', 'nfo');
}

let _fetchAlertsCtrl = null;

/**
 * Rafraîchit le compteur d'alertes depuis l'API.
 */
/* Empêche les déclenchements en chaîne du toast "session expirée" : plusieurs
   fetchs en vol peuvent rejeter 'session' en parallèle. On veut UN seul toast. */
let _sessionExpiredShown = false;

function _stopAlertPolling(reason) {
  if (_alertInterval) {
    clearInterval(_alertInterval);
    _alertInterval = null;
  }
  if (reason === 'session') {
    if (_sessionExpiredShown) return;
    _sessionExpiredShown = true;
    console.warn('[GéoDash] Session expirée — polling alertes arrêté.');
    _showSessionExpiredToast();
  } else {
    console.warn('[GéoDash] Alertes désactivées après 5 échecs réseau consécutifs.');
    toast('Alertes désactivées (5 échecs réseau consécutifs)', 'err');
  }
}

/* Toast persistant avec bouton "Se reconnecter" qui ré-authentifie via OIDC
   en conservant la page courante via next=<URL actuelle>. */
function _showSessionExpiredToast() {
  var wrap = document.getElementById('toasts');
  if (!wrap) return;
  var next = encodeURIComponent(window.location.pathname + window.location.search);
  var t = document.createElement('div');
  t.className = 'toast err';
  t.style.cssText = 'display:flex;align-items:center;gap:12px;padding:10px 14px';
  t.innerHTML =
    '<span style="flex:1">Session expirée. Reconnecte-toi pour reprendre les alertes.</span>' +
    '<a href="/oidc/authenticate/?next=' + next + '"'
    + ' style="background:#fff;color:#111;padding:6px 10px;border-radius:6px;'
    + 'text-decoration:none;font-weight:600;font-size:12px">Se reconnecter</a>';
  wrap.appendChild(t);
  // Pas de setTimeout → le toast reste jusqu'à action utilisateur.
}

function refreshAlerts() {
  if (_fetchAlertsCtrl) _fetchAlertsCtrl.abort();
  _fetchAlertsCtrl = new AbortController();

  var suffix = _activeZoneCode ? '?zone=' + encodeURIComponent(_activeZoneCode) : '';

  // Les headers Accept/X-Requested-With (via _apiHeaders) garantissent une
  // réponse 401 JSON propre du middleware en cas de session expirée, plutôt
  // qu'un 302 vers OIDC qui se transformerait en opaqueredirect.
  fetch('/api/alerts/' + suffix, {
    signal:      _fetchAlertsCtrl.signal,
    headers:     _apiHeaders(),
    redirect:    'manual',
    credentials: 'same-origin',
  })
    .then(function (r) {
      if (r.type === 'opaqueredirect' || r.status === 401 || r.status === 403) {
        _stopAlertPolling('session');
        return Promise.reject('session');
      }
      if (!r.ok) return Promise.reject(r.status);
      return r.json();
    })
    .then(function (d) {
      _fetchAlertsCtrl = null;
      _alertFailCount = 0;
      var count = d.count || 0;

      var cnt = document.getElementById('alertCount');
      if (cnt) {
        cnt.textContent = count;
        cnt.classList.toggle('zero', count === 0);
      }

      var pill = document.getElementById('alertPill');
      if (pill) {
        pill.className = 'alert-pill ' + (count > 0 ? 'hot' : 'ok');
        pill.innerHTML = count > 0
          ? '<svg width="11" height="11" aria-hidden="true"><use href="#i-alert"></use></svg> '
          + count + ' ALERTE' + (count > 1 ? 'S' : '')
          : '<svg width="11" height="11" aria-hidden="true"><use href="#i-check"></use></svg> RAS';
      }

      var upd = document.getElementById('lastUpd');
      if (upd) upd.textContent = new Date().toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    })
    .catch(function (err) {
      if (err && err.name === 'AbortError') return;
      if (err === 'session') return; // déjà géré, ne pas incrémenter le compteur réseau
      _alertFailCount = (_alertFailCount || 0) + 1;
      console.warn('[GéoDash] refreshAlerts (' + _alertFailCount + '/5):', err);
      if (_alertFailCount >= 5) _stopAlertPolling('network');
    });
}


/* ══════════════════════════════════════════════════════════
   GRAPHIQUES CHART.JS
══════════════════════════════════════════════════════════ */

function _getChartTheme() {
  var dark = document.documentElement.getAttribute('data-theme') === 'dark';
  return {
    grid: dark ? 'rgba(44,56,80,.8)' : 'rgba(200,210,230,.5)',
    tick: dark ? '#4e5d80' : '#8892aa',
    label: dark ? '#8d9dc0' : '#8892aa',
    bg: dark ? 'rgba(22,29,43,.95)' : 'rgba(255,255,255,.95)',
    title: dark ? '#e2e8f5' : '#1a2035',
    body: dark ? '#8d9dc0' : '#4a5578',
    bdr: dark ? '#2c3850' : '#d8dcea',
  };
}


/* ══════════════════════════════════════════════════════════
   TOOLTIPS KPI
══════════════════════════════════════════════════════════ */

var _KPI_TOOLTIPS = {
  roads: {
    title: 'Santé routière',
    body: 'Moyenne des scores de condition sur tous les segments de la zone. '
      + 'Score calculé depuis les attributs OSM : type de voie, revêtement, '
      + 'état déclaré (smoothness). ≥ 70 = bon état, 40-69 = dégradé, < 40 = critique.',
    method: 'Source : OpenStreetMap via Overpass API',
    color: 'var(--blue)',
  },
  floods: {
    title: 'Risque inondation',
    body: 'Score moyen de risque d\'inondation calculé par zone hydrographique. '
      + 'Combinaison de la proximité aux cours d\'eau, du type d\'élément OSM '
      + '(river, canal, wetland) et — quand disponible — des données SAR Sentinel-1 '
      + 'via Google Earth Engine.',
    method: 'Source : OSM + Sentinel-1 SAR (GEE)',
    color: 'var(--teal)',
  },
  vegetation: {
    title: 'NDVI moyen',
    body: 'Indice de végétation (Normalized Difference Vegetation Index). '
      + 'Valeur de -1 à +1 : > 0.6 = forêt dense, 0.2-0.6 = végétation modérée, '
      + '< 0.2 = sol nu / urbain. '
      + 'Valeur satellite (Sentinel-2) affichée quand GEE est disponible, '
      + 'sinon valeur OSM estimée.',
    method: 'Source : Sentinel-2 SR via GEE / OSM',
    color: 'var(--green2)',
  },
  degradation: {
    title: 'Score dégradation',
    body: 'Proportion (%) de segments routiers dont le score de condition '
      + 'est inférieur à 70/100 (état dégradé ou critique). '
      + 'Calculé en temps réel depuis les routes chargées dans la zone. '
      + '< 20 % = faible, 20-50 % = modérée, > 50 % = sévère.',
    method: 'Calcul : routes (score < 70) ÷ total routes × 100',
    color: 'var(--red)',
  },
};

function _initKpiTooltips() {
  document.querySelectorAll('.kpi-card[data-layer]').forEach(function (card) {
    var key = card.dataset.layer;
    var def = _KPI_TOOLTIPS[key];
    if (!def) return;

    var top = card.querySelector('.kpi-top');
    if (!top || card.querySelector('.kpi-info-btn')) return;

    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'kpi-info-btn';
    btn.setAttribute('aria-label', 'En savoir plus sur ' + def.title);
    btn.innerHTML = '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>';

    var tip = document.createElement('div');
    tip.className = 'kpi-tooltip';
    tip.setAttribute('role', 'tooltip');
    tip.innerHTML = '<div class="kpi-tip-title" style="color:' + def.color + '">' + def.title + '</div>'
      + '<div class="kpi-tip-body">' + def.body + '</div>'
      + '<div class="kpi-tip-method">' + def.method + '</div>';

    btn.appendChild(tip);

    btn.addEventListener('mouseenter', function () { tip.classList.add('open'); });
    btn.addEventListener('mouseleave', function () { tip.classList.remove('open'); });
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      tip.classList.toggle('open');
    });

    document.addEventListener('click', function () { tip.classList.remove('open'); });

    top.appendChild(btn);
  });
}

function initCharts(routesData, floodsData, avgScore) {
  if (typeof Chart === 'undefined') {
    console.error('[GéoDash] Chart.js non disponible.');
    return;
  }

  var th = _getChartTheme();
  var mono = { family: "'DM Mono', monospace", size: 10 };
  var sans = { family: "'DM Sans', system-ui, sans-serif", size: 10 };

  var tip = {
    backgroundColor: th.bg, borderColor: th.bdr, borderWidth: 1,
    titleColor: th.title, bodyColor: th.body,
    padding: 9, titleFont: mono, bodyFont: sans,
  };

  var rdCtx = document.getElementById('cRoad');
  if (rdCtx) {
    new Chart(rdCtx, {
      type: 'bar',
      data: {
        labels: routesData.labels || [],
        datasets: [{
          data: routesData.values || [],
          backgroundColor: (routesData.colors || []).map(function (c) { return c + '99'; }),
          borderColor: routesData.colors || [],
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

  var flCtx = document.getElementById('cFlood');
  if (flCtx) {
    new Chart(flCtx, {
      type: 'doughnut',
      data: {
        labels: floodsData.labels || [],
        datasets: [{
          data: floodsData.values || [],
          backgroundColor: (floodsData.colors || []).map(function (c) { return c + '99'; }),
          borderColor: floodsData.colors || [],
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

  var score = parseFloat(avgScore) || 0;
  var gc = score >= 70 ? '#00d97e' : score >= 40 ? '#f97316' : '#ef4444';
  var gBg = document.documentElement.getAttribute('data-theme') === 'dark' ? '#1e253a' : '#eef3fd';

  var gaCtx = document.getElementById('cGauge');
  if (gaCtx) {
    new Chart(gaCtx, {
      type: 'doughnut',
      data: {
        datasets: [{
          data: [score, 100 - score],
          backgroundColor: [gc + 'cc', gBg],
          borderWidth: 0,
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
  t.className = 'toast ' + type;
  t.textContent = msg;
  wrap.appendChild(t);
  setTimeout(function () {
    t.style.cssText = 'opacity:0;transition:opacity .4s;transform:translateY(4px)';
    setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 420);
  }, 3200);
}


/* ══════════════════════════════════════════════════════════
   PARAMÈTRES
══════════════════════════════════════════════════════════ */

function _loadSettings() {
  try {
    var raw = localStorage.getItem('gd-settings');
    _settings = raw
      ? Object.assign({}, GD_SETTINGS_DEFAULT, JSON.parse(raw))
      : Object.assign({}, GD_SETTINGS_DEFAULT);
  } catch (e) {
    _settings = Object.assign({}, GD_SETTINGS_DEFAULT);
  }
  try {
    var storedTheme = localStorage.getItem('gd-theme');
    if (storedTheme === 'dark' || storedTheme === 'light') _settings.theme = storedTheme;
  } catch (e) { }
}

function _saveSettings() {
  try { localStorage.setItem('gd-settings', JSON.stringify(_settings)); } catch (e) { }
}

function openSettings() {
  _loadSettings();
  _populateSettingsUI();
  var drawer = document.getElementById('settingsDrawer');
  var backdrop = document.getElementById('settingsBackdrop');
  if (!drawer || !backdrop) return;
  backdrop.classList.add('open');
  drawer.classList.add('open');
  document.body.style.overflow = 'hidden';
  var firstTab = drawer.querySelector('.sd-tab');
  if (firstTab && !drawer.querySelector('.sd-tab.active')) firstTab.click();
}

function closeSettings() {
  var drawer = document.getElementById('settingsDrawer');
  var backdrop = document.getElementById('settingsBackdrop');
  if (drawer) drawer.classList.remove('open');
  if (backdrop) backdrop.classList.remove('open');
  document.body.style.overflow = '';
}

function _populateSettingsUI() {
  var s = _settings;
  var currentTheme = document.documentElement.getAttribute('data-theme') || s.theme;
  _setRadio('themeBtns', currentTheme);
  _setRadio('densityBtns', s.density);
  _setRadio('coordFmtBtns', s.coordFmt);
  _setRadio('langBtns', s.lang);
  _setTileOpt(s.tileStyle);
  _setSlider('zoomSlider', 'zoomVal', s.zoomDefault, '');
  _setToggle('showScaleToggle', s.showScale);
  _setToggle('showLegendToggle', s.showLegend);
  _setSelect('refreshSelect', String(s.refreshInterval));
  _setSelect('alertLevelSelect', s.alertMinLevel);
  _setToggle('soundAlertToggle', s.soundAlerts);
}

function _setToggle(id, val) { var el = document.getElementById(id); if (el) el.checked = !!val; }
function _setSelect(id, val) { var el = document.getElementById(id); if (el) el.value = val; }

function _setSlider(sliderId, valId, val, suffix) {
  var slider = document.getElementById(sliderId);
  var label = document.getElementById(valId);
  if (slider) { slider.value = val; _updateSliderGradient(slider); }
  if (label) label.textContent = val + (suffix || '');
}

function _setRadio(groupClass, val) {
  document.querySelectorAll('.' + groupClass + ' .sd-radio-btn').forEach(function (btn) {
    btn.classList.toggle('active', btn.dataset.val === val);
  });
}

function _setTileOpt(val) {
  document.querySelectorAll('.sd-tile-opt').forEach(function (opt) {
    opt.classList.toggle('active', opt.dataset.tile === val);
  });
}

function _updateSliderGradient(slider) {
  var pct = ((slider.value - slider.min) / (slider.max - slider.min)) * 100;
  slider.style.setProperty('--pct', pct + '%');
}

function applySettings() {
  var s = _settings;
  var html = document.documentElement;
  var currentTheme = html.getAttribute('data-theme') || 'light';
  if (s.theme && currentTheme !== s.theme) {
    html.classList.add('theme-transitioning');
    html.setAttribute('data-theme', s.theme);
    setTimeout(function () { html.classList.remove('theme-transitioning'); }, 380);
  }

  html.setAttribute('data-density', s.density);

  var legend = document.querySelector('.map-legend');
  if (legend) legend.style.display = s.showLegend ? '' : 'none';

  var scaleEl = document.querySelector('.leaflet-control-scale');
  if (scaleEl) scaleEl.style.display = s.showScale ? '' : 'none';

  if (map && _mapReady) _applyTileStyle(s.tileStyle);

  _applyLang(s.lang);
}


/* ══════════════════════════════════════════════════════════
   FONDS DE CARTE
══════════════════════════════════════════════════════════ */

var TILE_CONFIGS = {
  dark: {
    url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
    subdomains: 'abcd', label: 'CartoDB Sombre', maxNativeZoom: 20, maxZoom: 22,
  },
  light: {
    url: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
    subdomains: 'abcd', label: 'CartoDB Clair', maxNativeZoom: 20, maxZoom: 22,
  },
  osm: {
    url: 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',
    subdomains: 'abcd', label: 'Plan (OSM)', maxNativeZoom: 20, maxZoom: 22,
  },
  topo: {
    url: 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
    subdomains: 'abc', label: 'Topographique', maxNativeZoom: 17, maxZoom: 17,
  },
  satellite: {
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    subdomains: '', label: 'Satellite', maxNativeZoom: 18, maxZoom: 22,
  },
};

var _EMPTY_TILE = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQAABjkB6QAAAABJRU5ErkJggg==';

function _buildTileLayer(style) {
  var cfg = TILE_CONFIGS[style] || TILE_CONFIGS.dark;
  var opts = {
    maxNativeZoom: cfg.maxNativeZoom, maxZoom: cfg.maxZoom,
    keepBuffer: 2, crossOrigin: '',
    updateWhenIdle: false, updateWhenZooming: false,
    // Tuiles HiDPI : si l'URL gère {r} (CartoDB → @2x natif), on laisse Leaflet
    // remplir {r} et on désactive detectRetina (sinon double mise à l'échelle).
    // Sinon (Esri, OpenTopoMap), on garde l'astuce zoom+1 de detectRetina.
    detectRetina: cfg.url.indexOf('{r}') === -1, errorTileUrl: _EMPTY_TILE,
  };
  if (cfg.subdomains) opts.subdomains = cfg.subdomains;

  var layer = L.tileLayer(cfg.url, opts);
  layer.on('tileerror', function (ev) {
    var tile = ev.tile;
    var retries = parseInt(tile.dataset.gdRetry || '0', 10);
    if (retries < 3) {
      tile.dataset.gdRetry = retries + 1;
      setTimeout(function () {
        tile.src = tile.src.split('?')[0] + '?_r=' + tile.dataset.gdRetry;
      }, 400 * (retries + 1));
    }
  });
  return layer;
}

function _applyTileStyle(style) {
  if (!map) return;
  map.eachLayer(function (layer) { if (layer instanceof L.TileLayer) map.removeLayer(layer); });
  _tileLayer = _buildTileLayer(style);
  _tileLayer.addTo(map);
  if (lRoads) { map.removeLayer(lRoads); lRoads.addTo(map); }
  if (lFloods) { map.removeLayer(lFloods); lFloods.addTo(map); }
  if (lVeg) { map.removeLayer(lVeg); lVeg.addTo(map); }
  setTimeout(function () { if (map) map.invalidateSize({ animate: false }); }, 100);
}

var LANG_LABELS = {
  fr: { dashboard: 'Dashboard', routes: 'Routes', floods: 'Inondations', veg: 'Végétation', alerts: 'Alertes', settings: 'Paramètres' },
  en: { dashboard: 'Dashboard', routes: 'Roads', floods: 'Floods', veg: 'Vegetation', alerts: 'Alerts', settings: 'Settings' },
};

function _applyLang(lang) {
  var L2 = LANG_LABELS[lang] || LANG_LABELS.fr;
  var map2 = {
    '[data-set-layer="all"]  .nav-item-label': L2.dashboard,
    '[data-set-layer="roads"] .nav-item-label': L2.routes,
    '[data-set-layer="floods"] .nav-item-label': L2.floods,
    '[data-set-layer="vegetation"] .nav-item-label': L2.veg,
  };
  Object.keys(map2).forEach(function (sel) {
    var el = document.querySelector(sel);
    if (el) el.textContent = map2[sel];
  });
}

function saveSettings() {
  var s = _settings;

  s.theme = document.documentElement.getAttribute('data-theme') || 'light';
  try { localStorage.setItem('gd-theme', s.theme); } catch (e) { }

  var da = document.querySelector('.densityBtns .sd-radio-btn.active');
  if (da) s.density = da.dataset.val;
  var ca = document.querySelector('.coordFmtBtns .sd-radio-btn.active');
  if (ca) s.coordFmt = ca.dataset.val;
  var la = document.querySelector('.langBtns .sd-radio-btn.active');
  if (la) s.lang = la.dataset.val;
  var ta = document.querySelector('.sd-tile-opt.active');
  if (ta) s.tileStyle = ta.dataset.tile;

  var zs = document.getElementById('zoomSlider');
  if (zs) s.zoomDefault = parseInt(zs.value, 10);

  var showScale = document.getElementById('showScaleToggle');
  var showLegend = document.getElementById('showLegendToggle');
  var soundAlert = document.getElementById('soundAlertToggle');
  if (showScale) s.showScale = showScale.checked;
  if (showLegend) s.showLegend = showLegend.checked;
  if (soundAlert) s.soundAlerts = soundAlert.checked;

  var rs = document.getElementById('refreshSelect');
  var al = document.getElementById('alertLevelSelect');
  if (rs) s.refreshInterval = parseInt(rs.value, 10);
  if (al) s.alertMinLevel = al.value;

  _saveSettings();
  applySettings();
  closeSettings();
  toast('Paramètres sauvegardés', 'ok');
}

function resetSettings() {
  if (!confirm('Réinitialiser tous les paramètres aux valeurs par défaut ?')) return;
  _settings = Object.assign({}, GD_SETTINGS_DEFAULT);
  _saveSettings();
  _populateSettingsUI();
  applySettings();
  toast('Paramètres réinitialisés', 'nfo');
}

function initSettings() {
  _loadSettings();
  applySettings();

  document.querySelectorAll('.sd-tab').forEach(function (tab) {
    tab.addEventListener('click', function () {
      var target = this.dataset.panel;
      document.querySelectorAll('.sd-tab').forEach(function (t) { t.classList.remove('active'); });
      document.querySelectorAll('.sd-panel').forEach(function (p) { p.classList.remove('active'); });
      this.classList.add('active');
      var panel = document.getElementById(target);
      if (panel) panel.classList.add('active');
    });
  });

  document.querySelectorAll('[data-open-settings]').forEach(function (btn) {
    btn.addEventListener('click', openSettings);
    _addRipple(btn);
  });

  var closeBtn = document.getElementById('settingsClose');
  if (closeBtn) closeBtn.addEventListener('click', closeSettings);

  var backdrop = document.getElementById('settingsBackdrop');
  if (backdrop) backdrop.addEventListener('click', closeSettings);

  var clearCacheBtn = document.getElementById('clearCacheBtn');
  if (clearCacheBtn) {
    clearCacheBtn.addEventListener('click', function () {
      ['gd-theme', 'gd-settings'].forEach(function (k) { localStorage.removeItem(k); });
      toast('Cache local vidé', 'nfo');
      closeSettings();
    });
  }

  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeSettings(); });

  var saveBtn = document.getElementById('settingsSave');
  var resetBtn = document.getElementById('settingsReset');
  if (saveBtn) saveBtn.addEventListener('click', saveSettings);
  if (resetBtn) resetBtn.addEventListener('click', resetSettings);

  document.querySelectorAll('.sd-slider').forEach(function (sl) {
    _updateSliderGradient(sl);
    sl.addEventListener('input', function () {
      _updateSliderGradient(this);
      var valId = this.dataset.valTarget;
      if (valId) {
        var label = document.getElementById(valId);
        if (label) label.textContent = this.value;
      }
    });
  });

  document.querySelectorAll('.sd-radio-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var group = this.closest('[class*="Btns"]') || this.closest('.sd-radio-group');
      if (group) {
        group.querySelectorAll('.sd-radio-btn').forEach(function (b) { b.classList.remove('active'); });
        this.classList.add('active');
      }
      if (group && group.classList.contains('themeBtns')) {
        var picked = this.dataset.val;
        var html = document.documentElement;
        if (html.getAttribute('data-theme') !== picked) {
          html.classList.add('theme-transitioning');
          html.setAttribute('data-theme', picked);
          setTimeout(function () { html.classList.remove('theme-transitioning'); }, 380);
        }
      }
    });
  });

  document.querySelectorAll('.sd-tile-opt').forEach(function (opt) {
    opt.addEventListener('click', function () {
      document.querySelectorAll('.sd-tile-opt').forEach(function (o) { o.classList.remove('active'); });
      this.classList.add('active');
      if (map && _mapReady) _applyTileStyle(this.dataset.tile);
    });
  });

  _updateExportLinks();
}


/* ══════════════════════════════════════════════════════════
   TABS panneaux (left + right) — design Claude
   ══════════════════════════════════════════════════════════ */

function initPanelTabs() {
  // Onglets du panneau GAUCHE : Zones / Couches / Données
  document.querySelectorAll('[data-lp-tab]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var tab = this.dataset.lpTab;
      document.querySelectorAll('[data-lp-tab]').forEach(function (b) { b.classList.remove('on'); });
      this.classList.add('on');
      document.querySelectorAll('[data-lp-content]').forEach(function (p) {
        p.style.display = (p.dataset.lpContent === tab) ? '' : 'none';
      });
    });
  });

  // Onglets du panneau DROIT : Vue / Environnement / Alertes
  document.querySelectorAll('[data-rp-tab]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var tab = this.dataset.rpTab;
      document.querySelectorAll('[data-rp-tab]').forEach(function (b) { b.classList.remove('on'); });
      this.classList.add('on');
      document.querySelectorAll('[data-rp-content]').forEach(function (p) {
        p.classList.toggle('on', p.dataset.rpContent === tab);
      });
    });
  });

  // Rail items (navigation visuelle pour l'instant — vraie nav en passe suivante)
  document.querySelectorAll('.rail-item').forEach(function (btn) {
    btn.addEventListener('click', function () {
      if (this.classList.contains('ghost')) return;  // bouton réglages
      document.querySelectorAll('.rail-item').forEach(function (b) { b.classList.remove('active'); });
      this.classList.add('active');
    });
  });
}


/* ══════════════════════════════════════════════════════════
   COUCHE COMMUNES — markers cliquables + popup riche
   ══════════════════════════════════════════════════════════ */

let lZoneMarkers = null;

function loadZoneMarkers() {
  if (!map || !_mapReady) return;

  // Récupère les markers injectés en JSON par Django
  var el = document.getElementById('gd-zones-markers');
  if (!el) return;
  var markers;
  try { markers = JSON.parse(el.textContent); } catch (e) { return; }
  if (!Array.isArray(markers) || markers.length === 0) return;

  // Reset si une couche précédente existe (réimport par filtre admin)
  if (lZoneMarkers) {
    map.removeLayer(lZoneMarkers);
    lZoneMarkers = null;
  }

  lZoneMarkers = L.layerGroup();
  markers.forEach(function (z) {
    if (z.lat == null || z.lng == null) return;

    var marker = L.circleMarker([z.lat, z.lng], {
      radius:      6,
      fillColor:   '#FF7A2D',
      fillOpacity: 0.85,
      color:       '#fff',
      weight:      2,
      opacity:     1,
      className:   'zone-marker',
    });

    // Tooltip au survol (nom)
    marker.bindTooltip(z.name, { direction: 'top', offset: [0, -6], sticky: false });

    // Popup au clic : contenu fetché à la demande pour ne pas surcharger
    marker.on('click', function () {
      _openCommunePopup(marker, z);
    });

    lZoneMarkers.addLayer(marker);
  });
  lZoneMarkers.addTo(map);
}

function _openCommunePopup(marker, zone) {
  // Affichage immédiat d'un placeholder, puis on remplit avec les détails
  var loadingHtml =
    '<div class="commune-popup">'
    + '<div class="cp-head"><span class="cp-dot"></span><span class="cp-name">'
    + zone.name + '</span></div>'
    + '<div class="cp-loading">Chargement des données…</div>'
    + '</div>';

  marker.bindPopup(loadingHtml, { maxWidth: 320, className: 'commune-popup-wrap' }).openPopup();

  fetch('/api/zones/' + encodeURIComponent(zone.code) + '/stats/', {
    headers:     _apiHeaders(),
    credentials: 'same-origin',
    redirect:    'manual',
  })
    .then(function (r) {
      if (r.type === 'opaqueredirect' || r.status === 401 || r.status === 403) {
        return Promise.reject('session');
      }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then(function (data) {
      marker.setPopupContent(_renderCommunePopup(data));
    })
    .catch(function (err) {
      if (err === 'session') return;
      console.warn('[Popup commune] échec :', err);
      marker.setPopupContent(
        '<div class="commune-popup"><div class="cp-loading">Données indisponibles</div></div>'
      );
    });
}

function _renderCommunePopup(d) {
  var roadColor = (d.roads.avg_score >= 70) ? 'var(--good)'
                : (d.roads.avg_score >= 40) ? 'var(--warn)' : 'var(--danger)';
  var floodColor = (d.floods.avg_score <= 30) ? 'var(--good)'
                 : (d.floods.avg_score <= 60) ? 'var(--warn)' : 'var(--danger)';

  var rows = [];

  if (d.description) {
    rows.push('<div class="cp-desc">' + d.description + '</div>');
  }

  rows.push(
    '<div class="cp-row"><span>Routes</span>'
    + '<span><b style="color:' + roadColor + '">' + d.roads.avg_score + '</b><span class="crit">/100</span>'
    + (d.roads.critical ? ' <span class="crit">▼ ' + d.roads.critical + '</span>' : '')
    + '</span></div>'
  );

  rows.push(
    '<div class="cp-row"><span>Inondation</span>'
    + '<span><b style="color:' + floodColor + '">' + d.floods.avg_score + '</b><span class="crit">/100</span>'
    + (d.floods.critical ? ' <span class="crit">▼ ' + d.floods.critical + '</span>' : '')
    + '</span></div>'
  );

  rows.push(
    '<div class="cp-row"><span>NDVI moyen</span>'
    + '<b style="color:var(--good)">' + d.vegetation.avg_ndvi.toFixed(3).replace('.', ',') + '</b></div>'
  );

  if (d.alerts_count > 0) {
    rows.push(
      '<div class="cp-row"><span>Alertes actives</span>'
      + '<b style="color:var(--orange-1)">' + d.alerts_count + '</b></div>'
    );
  }

  rows.push(
    '<div class="cp-foot">'
    + '<span>' + d.roads.total + ' route' + (d.roads.total > 1 ? 's' : '') + '</span>'
    + '<span>' + d.vegetation.dense + '/' + d.vegetation.total + ' zones denses</span>'
    + '</div>'
  );

  return ''
    + '<div class="commune-popup">'
    + '<div class="cp-head">'
    + '<span class="cp-dot"></span>'
    + '<span class="cp-name">' + d.name + '</span>'
    + '</div>'
    + rows.join('')
    + '</div>';
}


/* ══════════════════════════════════════════════════════════
   OCCUPATION DES SOLS — ESA WorldCover via GEE (asynchrone)
   ══════════════════════════════════════════════════════════ */

function loadLandUseBreakdown() {
  var card = document.getElementById('landuseCard');
  if (!card) return;

  var src = document.getElementById('landuseSource');
  var params = window.location.search || '';  // reprend ?admin / ?zone courants

  fetch('/api/gee/landuse/' + params, {
    headers:     _apiHeaders(),
    credentials: 'same-origin',
    redirect:    'manual',
  })
    .then(function (r) {
      if (r.type === 'opaqueredirect' || r.status === 401 || r.status === 403) {
        return Promise.reject('session');
      }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then(function (data) {
      if (!data || data.no_data) {
        if (src) src.textContent = 'Donnée non disponible';
        return;
      }
      if (src) src.textContent = data.source || 'ESA WorldCover';

      ['urban', 'cropland', 'forest', 'water', 'bare'].forEach(function (key) {
        var pct = (data[key] != null) ? data[key] : 0;
        var bar = card.querySelector('[data-lu="' + key + '"]');
        var val = card.querySelector('[data-lu-val="' + key + '"]');
        if (bar) bar.style.width = pct + '%';
        if (val) val.textContent = (pct.toFixed ? pct.toFixed(1) : pct) + ' %';
      });
    })
    .catch(function (err) {
      if (err === 'session') return;
      if (src) src.textContent = 'Donnée indisponible';
      console.warn('[LandUse] échec :', err);
    });
}


/* ══════════════════════════════════════════════════════════
   COORDONNÉES + ZOOM (bas-droite carte)
   ══════════════════════════════════════════════════════════ */

function _bindMapCoords() {
  if (typeof map === 'undefined' || !map) return;
  var elLat  = document.getElementById('mapCoordsLat');
  var elLng  = document.getElementById('mapCoordsLng');
  var elZoom = document.getElementById('mapCoordsZoom');
  if (!elLat || !elLng || !elZoom) return;

  // Throttle léger : mousemove est très bruyant. requestAnimationFrame
  // garantit qu'on n'écrit dans le DOM qu'à la cadence de l'écran.
  var pending = false, lastLatLng = null;
  map.on('mousemove', function (e) {
    lastLatLng = e.latlng;
    if (pending) return;
    pending = true;
    requestAnimationFrame(function () {
      pending = false;
      if (!lastLatLng) return;
      elLat.textContent = lastLatLng.lat.toFixed(4).replace('.', ',');
      // West = lng négatif. Si lng > 0 (rare en CI), on bascule "E"
      var lngAbs = Math.abs(lastLatLng.lng).toFixed(4).replace('.', ',');
      elLng.textContent = lngAbs;
    });
  });

  function updateZoom() { elZoom.textContent = map.getZoom(); }
  map.on('zoomend', updateZoom);
  updateZoom();
}


/* ══════════════════════════════════════════════════════════
   PLEIN ÉCRAN CARTE
   ══════════════════════════════════════════════════════════ */

function toggleMapFullscreen() {
  var app = document.querySelector('.app');
  if (!app) return;
  app.classList.toggle('map-fullscreen');
  // Leaflet a besoin d'un re-layout après changement de taille du container
  setTimeout(function () { if (typeof map !== 'undefined' && map) map.invalidateSize(); }, 220);
}


/* ══════════════════════════════════════════════════════════
   RECHERCHE TOPBAR
   ══════════════════════════════════════════════════════════ */

function initTopSearch() {
  var top = document.getElementById('topSearch');
  if (!top) return;

  // Délègue à la recherche de l'arbre (#treeSearch) — un seul moteur
  top.addEventListener('input', function () {
    var tree = document.getElementById('treeSearch');
    if (tree) {
      tree.value = this.value;
      tree.dispatchEvent(new Event('input', { bubbles: true }));
    }
  });

  // Enter sur le 1er résultat
  top.addEventListener('keydown', function (e) {
    if (e.key !== 'Enter') return;
    var first = document.querySelector('.tree-row[data-admin-value]:not([style*="display: none"])');
    if (first) first.click();
  });
}


/* ══════════════════════════════════════════════════════════
   ARBRE ADMINISTRATIF (panneau gauche)
   ══════════════════════════════════════════════════════════ */

function initAdminTree() {
  // Caret : toggle expand/collapse des enfants
  document.querySelectorAll('[data-tree-toggle] .caret').forEach(function (caret) {
    caret.addEventListener('click', function (e) {
      e.stopPropagation();
      var row = this.closest('[data-tree-toggle]');
      var siblings = row && row.nextElementSibling;
      if (siblings && siblings.classList.contains('tree-children')) {
        var hidden = siblings.style.display === 'none';
        siblings.style.display = hidden ? '' : 'none';
        this.classList.toggle('open', hidden);
      }
    });
  });

  // Clic sur n'importe quel row avec data-admin-value → applique le filtre
  // Format de value :
  //   "district:CIV-DIS-LAGUNES" → switchAdmin (filtre admin)
  //   "zone:ABJ"                 → switchZone (filtre zone)
  document.querySelectorAll('[data-admin-value]').forEach(function (row) {
    row.addEventListener('click', function (e) {
      // Si on a cliqué sur le caret, on a déjà géré (toggle), on s'arrête là
      if (e.target.closest('.caret')) return;
      var val = this.dataset.adminValue;
      if (!val) return;
      if (val.indexOf('zone:') === 0) {
        switchZone(val.slice(5));
      } else {
        switchAdmin(val);
      }
    });
  });

  // Recherche dans l'arbre (filtre client-side, expand auto si match)
  var search = document.getElementById('treeSearch');
  if (search && !search.dataset.bound) {
    search.dataset.bound = '1';
    search.addEventListener('input', function () {
      var q = this.value.toLowerCase().trim();
      document.querySelectorAll('.tree-row').forEach(function (row) {
        var label = row.querySelector('.tree-label');
        if (!label) return;
        var text = label.textContent.toLowerCase();
        var match = !q || text.indexOf(q) >= 0;
        row.style.display = match ? '' : 'none';
      });
      // En mode recherche, on déploie tous les enfants pour faciliter la lecture
      document.querySelectorAll('.tree-children').forEach(function (c) {
        c.style.display = q ? '' : (c.dataset.userOpen === '1' ? '' : c.style.display);
      });
      if (q) document.querySelectorAll('.caret').forEach(function (c) { c.classList.add('open'); });
    });
  }

  // Auto-scroll vers le nœud actif (évite l'effet "zones flottantes" quand
  // un district sélectionné est en bas de l'arbre)
  var active = document.querySelector('.tree-row.selected');
  if (active && typeof active.scrollIntoView === 'function') {
    setTimeout(function () {
      active.scrollIntoView({ block: 'center', behavior: 'smooth' });
    }, 50);
  }
}


/* Stub basemap switcher — sera branché sur la vraie logique tile en passe 4 */
function _switchBasemap(name) {
  // Mémorise le choix ; la bascule effective de tile layer existe déjà dans
  // _buildTileLayer (cf. settings). Ici on déclenche juste la même logique.
  if (typeof _settings !== 'undefined' && _settings) {
    var map_to_setting = { 'dark': 'dark', 'light': 'light', 'osm': 'osm', 'satellite': 'satellite' };
    _settings.tileStyle = map_to_setting[name] || 'light';
    if (typeof _saveSettings === 'function') _saveSettings();
    if (typeof map !== 'undefined' && map && typeof _buildTileLayer === 'function') {
      if (typeof _tileLayer !== 'undefined' && _tileLayer) map.removeLayer(_tileLayer);
      _tileLayer = _buildTileLayer(_settings.tileStyle);
      _tileLayer.addTo(map);
    }
  }
}
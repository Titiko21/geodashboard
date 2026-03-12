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
 *   – Alertes (focusAlert, refreshAlerts)
 *   – Graphiques Chart.js (initCharts)
 *   – Toasts
 *   – Paramètres (drawer, localStorage)
 *   – Fonds de carte (TILE_CONFIGS, _buildTileLayer)
 */
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

/* ─── Couleurs centralisées ────────────────────────────
   Source unique de vérité pour toutes les couleurs du
   dashboard — évite les objets COLORS dispersés dans
   _renderData, les popups, focusAlert, etc.
────────────────────────────────────────────────────── */
const GD_COLORS = {
  road:           '#5b8dee',
  flood:          '#26c6da',
  vegetation:     '#3ecf6e',
  alert:          '#ff7043',
  flood_faible:   '#22d3ee',
  flood_modere:   '#3b82f6',
  flood_eleve:    '#f97316',
  flood_critique: '#dc2626',
  veg_sparse:     '#bef264',
  veg_moderate:   '#4ade80',
  veg_dense:      '#16a34a',
  veg_very_dense: '#14532d',
};

/* ─── Paramètres + couche tuiles (globaux dès le départ) ──
   Déclarés ici pour que toggleTheme() et initEventListeners()
   les trouvent immédiatement, avant que initSettings() tourne.
────────────────────────────────────────────────────────── */
const GD_SETTINGS_DEFAULT = {
  theme:           'light',
  density:         'normal',
  tileStyle:       'dark',
  zoomDefault:     9,
  showScale:       true,
  showLegend:      true,
  coordFmt:        'DD',
  refreshInterval: 60,
  alertMinLevel:   'info',
  soundAlerts:     false,
  lang:            'fr',
};

// Pré-initialisé avec les défauts — jamais vide même si initSettings()
// n'a pas encore tourné (ex. clic rapide sur le toggle thème au démarrage).
let _settings = Object.assign({}, GD_SETTINGS_DEFAULT);

// Déclaré ici avec les autres globales — plus clair qu'au milieu du fichier.
let _tileLayer = null;


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

  /* Synchroniser les DEUX clés localStorage pour éviter le conflit
     au rechargement de page (ex. changement de zone) */
  try {
    localStorage.setItem('gd-theme', next);       /* clé anti-flash */
    if (_settings) {
      _settings.theme = next;
      _saveSettings();                             /* clé gd-settings */
    }
  } catch (e) {}

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

  /* Alertes — délégation d'événement sur le conteneur parent.
     On n'attache PAS les listeners directement sur les .a-item parce que
     refreshAlerts() recrée ces éléments toutes les 60s — les listeners
     directs seraient perdus à chaque refresh. Avec la délégation, le
     listener reste sur .alerts-scroll qui lui ne bouge jamais. */
  var alertsScroll = document.querySelector('.alerts-scroll');
  if (alertsScroll) {
    alertsScroll.addEventListener('click', function (e) {
      var item = e.target.closest('[data-focus-alert]');
      if (!item) return;
      document.querySelectorAll('.a-item.focused').forEach(function (x) {
        x.classList.remove('focused');
      });
      item.classList.add('focused');
      focusAlert(
        parseFloat(item.dataset.lat  || 0),
        parseFloat(item.dataset.lng  || 0),
        item.dataset.category || ''
      );
    });
  }

  /* Sélecteur de zone — on recharge la page avec ?zone=CODE dans l'URL courante */
  var zoneSelect = document.getElementById('zoneSelect');
  if (zoneSelect) {
    zoneSelect.addEventListener('change', function () {
      switchZone(this.value);
    });
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

  /* ─── Couche de tuiles ─────────────────────────────────
     CORRECTIFS des zones grisées :
     1. {r} supprimé  → plus de 404 sur tuiles @2x manquantes
     2. keepBuffer:2  → moins de requêtes simultanées (était 4)
     3. crossOrigin   → évite les erreurs CORS/Canvas
     4. Retry ×3      → relance les tuiles échouées au lieu de griser
  ─────────────────────────────────────────────────────── */
  _tileLayer = _buildTileLayer((_settings && _settings.tileStyle) || 'dark');
  _tileLayer.addTo(map);

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

  /* ─── Recalcul taille carte au resize de la fenêtre ─── */
  window.addEventListener('resize', function () {
    clearTimeout(window._gdResizeTimer);
    window._gdResizeTimer = setTimeout(function () {
      if (map) map.invalidateSize({ animate: false });
    }, 150);
  });

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

  /* ── Routes ──────────────────────────────────────────
     Double trait : ombre noire + couleur → effet glow pro
  ─────────────────────────────────────────────────────*/
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

      /* Ombre (trait noir semi-transparent en dessous) */
      var shadow = L.polyline(coords, {
        color: 'rgba(0,0,0,.45)', weight: 8, opacity: 1,
        lineCap: 'round', lineJoin: 'round', interactive: false,
      });
      lRoads.addLayer(shadow);

      /* Trait principal */
      var opts = { color: r.color, weight: 5, opacity: .9, lineCap: 'round', lineJoin: 'round' };
      var lyr  = L.polyline(coords, opts);

      lyr.on('mouseover', function (e) {
        this.setStyle({ weight: 8, opacity: 1 });
        shadow.setStyle({ weight: 12 });
        L.DomUtil.addClass(e.target._path, 'gd-hover');
      });
      lyr.on('mouseout', function (e) {
        this.setStyle({ weight: 5, opacity: .9 });
        shadow.setStyle({ weight: 8 });
        L.DomUtil.removeClass(e.target._path, 'gd-hover');
      });
      lyr.bindPopup(_popupRoad(r), { maxWidth: 280, className: 'gd-popup', autoPanPadding: [50,50] });
      lRoads.addLayer(lyr);

      /* Stocker la référence route pour focusFeature */
      lyr._gdMeta = { type: 'road', id: r.id, name: r.name, color: r.color };
      _collectBounds(lyr);
      n++;
    } catch (e) { console.warn('[GéoDash] Route:', r.name, e); }
  });

  /* ── Inondations ──────────────────────────────────── */
  (data.floods || []).forEach(function (f) {
    var geo = f.geojson;
    if (!geo) return;
    try {
      var COLORS = {
        faible:   GD_COLORS.flood_faible,
        modere:   GD_COLORS.flood_modere,
        eleve:    GD_COLORS.flood_eleve,
        critique: GD_COLORS.flood_critique,
      };
      var c    = COLORS[f.risk_level] || GD_COLORS.flood;
      var opts = { color: c, fillColor: c, weight: 2, opacity: .9, fillOpacity: .25 };
      var coords;
      if (geo.type === 'Polygon') {
        coords = geo.coordinates[0].map(function (c) { return [c[1], c[0]]; });
      } else if (geo.type === 'MultiPolygon') {
        coords = geo.coordinates.map(function (p) {
          return p[0].map(function (c) { return [c[1], c[0]]; });
        });
      } else return;
      var lyr = L.polygon(coords, opts);
      lyr.on('mouseover', function (e) {
        this.setStyle({ fillOpacity: .5, weight: 3, color: '#fff' });
        L.DomUtil.addClass(e.target._path, 'gd-hover');
      });
      lyr.on('mouseout', function (e) {
        this.setStyle({ fillOpacity: .25, weight: 2, color: c });
        L.DomUtil.removeClass(e.target._path, 'gd-hover');
      });
      lyr.bindPopup(_popupFlood(f), { maxWidth: 280, className: 'gd-popup', autoPanPadding: [50,50] });
      lyr._gdMeta = { type: 'flood', id: f.id, name: f.name, color: c };
      lFloods.addLayer(lyr);
      _collectBounds(lyr);
      n++;
    } catch (e) { console.warn('[GéoDash] Flood:', f.name, e); }
  });

  /* ── Végétation ───────────────────────────────────── */
  (data.vegetation || []).forEach(function (v) {
    var geo = v.geojson;
    if (!geo) return;
    try {
      var COLORS = {
        sparse:     GD_COLORS.veg_sparse,
        moderate:   GD_COLORS.veg_moderate,
        dense:      GD_COLORS.veg_dense,
        very_dense: GD_COLORS.veg_very_dense,
      };
      var c    = COLORS[v.density_class] || GD_COLORS.vegetation;
      var opts = { color: c, fillColor: c, weight: 1.5, opacity: .7, fillOpacity: .2, dashArray: '6,4' };
      var coords;
      if (geo.type === 'Polygon') {
        coords = geo.coordinates[0].map(function (c) { return [c[1], c[0]]; });
      } else return;
      var lyr = L.polygon(coords, opts);
      lyr.on('mouseover', function (e) {
        this.setStyle({ fillOpacity: .45, opacity: 1, weight: 2.5, dashArray: null });
        L.DomUtil.addClass(e.target._path, 'gd-hover');
      });
      lyr.on('mouseout', function (e) {
        this.setStyle({ fillOpacity: .2, opacity: .7, weight: 1.5, dashArray: '6,4' });
        L.DomUtil.removeClass(e.target._path, 'gd-hover');
      });
      lyr.bindPopup(_popupVeg(v), { maxWidth: 280, className: 'gd-popup', autoPanPadding: [50,50] });
      lyr._gdMeta = { type: 'vegetation', id: v.id, name: v.name, color: c };
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


/* ── Popups Leaflet ──────────────────────────────────────
   Structure : .popup-hdr / .popup-body / .popup-footer
   Styles correspondants dans dashboard.css → .gd-popup
─────────────────────────────────────────────────────── */

function _bar(pct, color, labelLeft, labelRight) {
  var labels = (labelLeft || labelRight)
    ? '<div class="pp-bar-label">'
      + '<span>' + (labelLeft  || '') + '</span>'
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
  var c  = r.color || _scoreColor(sc);
  var status = (r.status_label || r.status || '—');
  return '<div class="popup-inner">'
    /* En-tête */
    + '<div class="popup-hdr">'
    +   '<div class="popup-type">&#x2015; Route</div>'
    +   '<div class="popup-name">' + (r.name || 'Route inconnue') + '</div>'
    + '</div>'
    /* Corps */
    + '<div class="popup-body">'
    +   '<div class="popup-row">'
    +     '<span class="popup-lbl">Score état</span>'
    +     '<span class="popup-val" style="color:' + c + '">' + sc + '<small style="color:var(--t4);font-weight:400">/100</small></span>'
    +   '</div>'
    +   _bar(sc, c, '0', '100')
    +   '<div class="popup-row">'
    +     '<span class="popup-lbl">Surface</span>'
    +     '<span class="popup-val">' + (r.surface_type || '—') + '</span>'
    +   '</div>'
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
    /* Pied */
    + '<div class="popup-footer">'
    +   '<span class="popup-badge badge-' + (r.status || 'ferme') + '">'
    +     '<span class="badge-dot"></span>' + status
    +   '</span>'
    + '</div>'
    + '</div>';
}

function _popupFlood(f) {
  var rs = f.risk_score || 0;
  var COLORS = {
    faible:   GD_COLORS.flood_faible,
    modere:   GD_COLORS.flood_modere,
    eleve:    GD_COLORS.flood_eleve,
    critique: GD_COLORS.flood_critique,
  };
  var c = COLORS[f.risk_level] || GD_COLORS.flood;
  return '<div class="popup-inner">'
    + '<div class="popup-hdr">'
    +   '<div class="popup-type">&#x26A1; Zone inondation</div>'
    +   '<div class="popup-name">' + (f.name || 'Zone inconnue') + '</div>'
    + '</div>'
    + '<div class="popup-body">'
    +   '<div class="popup-row">'
    +     '<span class="popup-lbl">Score de risque</span>'
    +     '<span class="popup-val" style="color:' + c + '">' + rs + '<small style="color:var(--t4);font-weight:400">/100</small></span>'
    +   '</div>'
    +   _bar(rs, c, 'Faible', 'Critique')
    +   '<div class="popup-row"><span class="popup-lbl">Niveau</span>'
    +     '<span class="popup-val">' + (f.risk_label || f.risk_level || '—') + '</span></div>'
    +   '<div class="popup-row"><span class="popup-lbl">Surface</span>'
    +     '<span class="popup-val">' + (f.area_km2 || '—') + ' km²</span></div>'
    +   '<div class="popup-row"><span class="popup-lbl">Pluviométrie</span>'
    +     '<span class="popup-val">' + (f.rainfall_mm || '—') + ' mm</span></div>'
    + '</div>'
    + '<div class="popup-footer">'
    +   '<span class="popup-badge badge-' + (f.risk_level === 'critique' ? 'critique' : f.risk_level === 'eleve' ? 'degrade' : 'bon') + '">'
    +     '<span class="badge-dot"></span>' + (f.risk_label || f.risk_level || 'Inondation')
    +   '</span>'
    + '</div>'
    + '</div>';
}

function _popupVeg(v) {
  var ndvi = v.ndvi_value || 0;
  var ndviPct = Math.round(((ndvi + 1) / 2) * 100);   /* NDVI [-1,1] → [0,100] */
  var COLORS = {
    sparse:     GD_COLORS.veg_sparse,
    moderate:   GD_COLORS.veg_moderate,
    dense:      GD_COLORS.veg_dense,
    very_dense: GD_COLORS.veg_very_dense,
  };
  var c = COLORS[v.density_class] || GD_COLORS.vegetation;
  return '<div class="popup-inner">'
    + '<div class="popup-hdr">'
    +   '<div class="popup-type">&#x1F333; Végétation</div>'
    +   '<div class="popup-name">' + (v.name || 'Zone végétale') + '</div>'
    + '</div>'
    + '<div class="popup-body">'
    +   '<div class="popup-row"><span class="popup-lbl">NDVI</span>'
    +     '<span class="popup-val" style="color:' + c + '">' + ndvi.toFixed(3) + '</span></div>'
    +   _bar(ndviPct, c, '-1', '+1')
    +   '<div class="popup-row"><span class="popup-lbl">Couverture</span>'
    +     '<span class="popup-val">' + (v.coverage_percent || '—') + '%</span></div>'
    +   '<div class="popup-row"><span class="popup-lbl">Classe</span>'
    +     '<span class="popup-val">' + (v.density_label || v.density_class || '—') + '</span></div>'
    + (v.area_ha
      ? '<div class="popup-row"><span class="popup-lbl">Surface</span>'
        + '<span class="popup-val">' + v.area_ha + ' ha</span></div>'
      : '')
    + '</div>'
    + '<div class="popup-footer">'
    +   '<span class="popup-badge" style="background:rgba(78,205,128,.1);color:var(--green2);border:1px solid rgba(78,205,128,.25)">'
    +     '<span class="badge-dot"></span>' + (v.density_label || v.density_class || 'Végétation')
    +   '</span>'
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

/* ══════════════════════════════════════════════════════════
   ÉTAT ACTIF — KPI cards
══════════════════════════════════════════════════════════ */
var _activeLayer = 'all';   /* couche actuellement mise en avant */

function _setKpiActive(type) {
  /* Retire l'état actif de toutes les KPI cards */
  document.querySelectorAll('.kpi-card').forEach(function (c) {
    c.classList.remove('kpi-active');
  });
  /* Active la card correspondante */
  var mapping = { roads: 'roads', floods: 'floods', vegetation: 'vegetation' };
  var target = mapping[type];
  if (target) {
    var card = document.querySelector('.kpi-card[data-layer="' + target + '"]');
    if (card) card.classList.add('kpi-active');
  }
}

/* ══════════════════════════════════════════════════════════
   FLASH / PULSE COUCHE — retour visuel après clic KPI
   ─────────────────────────────────────────────────────────
   Fait clignoter brièvement l'opacité de la couche active
   pour confirmer visuellement que le clic a eu un effet,
   même si la carte ne bouge pas (vue déjà centrée).
══════════════════════════════════════════════════════════ */
function _pulseLayer(type) {
  var targets = type === 'all'
    ? ['roads', 'floods', 'vegetation']
    : [type];

  targets.forEach(function (k) {
    var lg = layerRef[k] ? layerRef[k]() : null;
    if (!lg) return;
    lg.getLayers().forEach(function (lyr) {
      if (!lyr.setStyle || lyr.options.interactive === false) return;
      var orig = {
        opacity:     lyr.options.opacity,
        fillOpacity: lyr.options.fillOpacity,
      };
      /* Flash lumineux : opacité × 1 → flash → retour */
      lyr.setStyle({ opacity: 1, fillOpacity: Math.min((orig.fillOpacity || 0) + .35, 1) });
      setTimeout(function () {
        lyr.setStyle({ opacity: orig.opacity, fillOpacity: orig.fillOpacity });
      }, 320);
    });
  });
}

function setLayer(type, btn) {
  _activeLayer = type;

  /* ── Toolbar (boutons carte) ──────────────────────── */
  document.querySelectorAll('[data-set-layer]').forEach(function (b) {
    b.classList.remove('active');
  });
  if (btn) {
    btn.classList.add('active');
  } else {
    var found = document.querySelector('[data-set-layer="' + type + '"]');
    if (found) found.classList.add('active');
  }

  /* ── Nav-items ────────────────────────────────────── */
  document.querySelectorAll('.nav-item[data-set-layer]').forEach(function (ni) {
    ni.classList.toggle('active', ni.dataset.setLayer === type);
  });

  /* ── KPI cards ────────────────────────────────────── */
  _setKpiActive(type);

  /* ── Toggles sidebar ─────────────────────────────── */
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

  /* ── Vol vers la couche ───────────────────────────── */
  var flew = _flyToLayer(type);

  /* ── Pulse de confirmation ────────────────────────── */
  /* Délai : si flyTo en cours, pulse après l'animation */
  setTimeout(function () { _pulseLayer(type); }, flew ? 900 : 80);
}

function _flyToLayer(type) {
  if (!map || !_mapReady) return false;
  if (type === 'all') { flyToAll(); return true; }

  var lg = layerRef[type] ? layerRef[type]() : null;
  if (!lg) return false;

  /* Collecter les bounds de TOUTES les sous-couches visibles */
  var bounds = [];
  lg.getLayers().forEach(function (l) {
    try {
      var b = l.getBounds ? l.getBounds() : null;
      if (b && b.isValid()) bounds.push(b.getSouthWest(), b.getNorthEast());
    } catch (_) {}
  });

  if (bounds.length) {
    map.flyToBounds(
      L.latLngBounds(bounds).pad(.15),
      { duration: 1.1, easeLinearity: .3, maxZoom: 14 }
    );
    return true;
  }

  /* Pas de données dans la couche → toast informatif */
  toast('Aucune donnée pour cette couche', 'nfo');
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
   NAVIGATION & ALERTES
══════════════════════════════════════════════════════════ */

function switchZone(code) {
  // On part de l'URL actuelle pour conserver le chemin de l'app
  // (qu'elle soit montée sur /, /dashboard/, /geo/, peu importe).
  // URLSearchParams gère l'encodage proprement — pas de concaténation à la main.
  var url = new URL(window.location.href);
  if (code) {
    url.searchParams.set('zone', code);
  } else {
    url.searchParams.delete('zone');
  }
  window.location.href = url.toString();
}

/* Marqueur pulsant placé sur la localisation d'une alerte */
var _alertMarker = null;

function _placeAlertMarker(lat, lng, color) {
  if (_alertMarker) { map.removeLayer(_alertMarker); _alertMarker = null; }

  /* Icône SVG avec cercle pulsant */
  var icon = L.divIcon({
    className: '',
    iconSize:  [36, 36],
    iconAnchor:[18, 18],
    html: '<div class="alert-pulse-marker" style="--mc:' + (color || GD_COLORS.alert) + '">'
        + '<div class="apm-ring"></div>'
        + '<div class="apm-dot"></div>'
        + '</div>',
  });
  _alertMarker = L.marker([lat, lng], { icon: icon, zIndexOffset: 1000 });
  _alertMarker.addTo(map);

  /* Disparaît automatiquement après 8 secondes */
  setTimeout(function () {
    if (_alertMarker) { map.removeLayer(_alertMarker); _alertMarker = null; }
  }, 8000);
}

function focusAlert(lat, lng, category) {
  if (!map || !_mapReady) return;
  var la = parseFloat(lat);
  var ln = parseFloat(lng);

  var color = GD_COLORS[category] || GD_COLORS.alert;

  // Zoom cible selon catégorie — une route mérite plus de précision
  // qu'une zone d'inondation qui couvre souvent plusieurs km².
  var ZOOM_MAP = { road: 17, flood: 14, vegetation: 14 };
  var targetZoom = ZOOM_MAP[category] || 16;

  if (!isNaN(la) && !isNaN(ln) && (Math.abs(la) > 0.0001 || Math.abs(ln) > 0.0001)) {
    map.flyTo([la, ln], targetZoom, { duration: 1.2, easeLinearity: .25 });
    // On place le marqueur après l'animation — pas pendant, ça évite le décalage visuel
    setTimeout(function () { _placeAlertMarker(la, ln, color); }, 700);
    toast('Zone d\'alerte localisée', 'nfo');
  } else {
    // Pas de coordonnées → fallback vers la couche de la catégorie
    var layerMap = { road: 'roads', flood: 'floods', vegetation: 'vegetation' };
    var layerKey = layerMap[category];
    if (layerKey) {
      var flew = _flyToLayer(layerKey);
      if (!flew) toast('Coordonnées manquantes pour cette alerte', 'nfo');
    } else {
      flyToAll();
    }
  }
}

// Annule la requête précédente si elle traîne encore quand le timer relance.
// Évite les réponses qui arrivent dans le mauvais ordre sur réseau lent.
let _fetchAlertsCtrl = null;

function refreshAlerts() {
  if (_fetchAlertsCtrl) _fetchAlertsCtrl.abort();
  _fetchAlertsCtrl = new AbortController();

  fetch('/api/alerts/', { signal: _fetchAlertsCtrl.signal })
    .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
    .then(function (d) {
      _fetchAlertsCtrl = null;
      var count = d.count || 0;

      var cnt = document.getElementById('alertCount');
      if (cnt) {
        cnt.textContent = count;
        cnt.classList.toggle('zero', count === 0);
      }

      // textContent écraserait le SVG — on reconstruit le innerHTML complet.
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
      // AbortError = on a annulé nous-mêmes — pas une vraie erreur à logger
      if (err && err.name === 'AbortError') return;
      console.warn('[GéoDash] refreshAlerts:', err);
    });
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


/* ══════════════════════════════════════════════════════════
   PARAMÈTRES — Drawer + Persistance localStorage
   ─────────────────────────────────────────────────────────
   Structure localStorage clé 'gd-settings' :
   {
     theme     : 'light'|'dark',
     density   : 'compact'|'normal'|'comfortable',
     tileStyle : 'dark'|'light'|'osm'|'topo',
     zoomDefault: 9,
     showScale : true,
     showLegend: true,
     coordFmt  : 'DD'|'DMS',
     refreshInterval: 60,
     alertMinLevel: 'info'|'warning'|'danger'|'critical',
     soundAlerts : false,
     lang        : 'fr'|'en',
   }
══════════════════════════════════════════════════════════ */

/* ─── Chargement / Sauvegarde ─────────────────────────── */

function _loadSettings() {
  try {
    var raw = localStorage.getItem('gd-settings');
    _settings = raw
      ? Object.assign({}, GD_SETTINGS_DEFAULT, JSON.parse(raw))
      : Object.assign({}, GD_SETTINGS_DEFAULT);
  } catch (e) {
    _settings = Object.assign({}, GD_SETTINGS_DEFAULT);
  }

  /* ── Synchronisation thème ──────────────────────────────────
     gd-theme est la SOURCE DE VÉRITÉ (écrite par toggleTheme
     et lue par le script anti-flash dans <head>).
     On s'assure que _settings.theme lui est toujours identique
     pour qu'applySettings() ne vienne pas l'écraser.
  ─────────────────────────────────────────────────────────── */
  try {
    var storedTheme = localStorage.getItem('gd-theme');
    if (storedTheme === 'dark' || storedTheme === 'light') {
      _settings.theme = storedTheme;
    }
  } catch (e) {}
}

function _saveSettings() {
  try { localStorage.setItem('gd-settings', JSON.stringify(_settings)); } catch (e) {}
}

/* ─── Ouverture / Fermeture du drawer ─────────────────── */

function openSettings() {
  _loadSettings();
  _populateSettingsUI();

  var drawer   = document.getElementById('settingsDrawer');
  var backdrop = document.getElementById('settingsBackdrop');
  if (!drawer || !backdrop) return;
  backdrop.classList.add('open');
  drawer.classList.add('open');
  document.body.style.overflow = 'hidden';   /* bloque le scroll derrière */

  /* Activer le premier onglet si aucun actif */
  var firstTab = drawer.querySelector('.sd-tab');
  if (firstTab && !drawer.querySelector('.sd-tab.active')) firstTab.click();
}

function closeSettings() {
  var drawer   = document.getElementById('settingsDrawer');
  var backdrop = document.getElementById('settingsBackdrop');
  if (drawer)   drawer.classList.remove('open');
  if (backdrop) backdrop.classList.remove('open');
  document.body.style.overflow = '';
}

/* ─── Population de l'UI avec les valeurs courantes ───── */

function _populateSettingsUI() {
  var s = _settings;

  /* ── Affichage ── */
  // themeBtns doit refléter l'état réel du DOM, pas juste _settings.
  // On lit data-theme directement au cas où toggleTheme() aurait tourné
  // sans passer par saveSettings() (ex. clic rapide dans le header).
  var currentTheme = document.documentElement.getAttribute('data-theme') || s.theme;
  _setRadio('themeBtns', currentTheme);
  _setRadio('densityBtns', s.density);
  _setRadio('coordFmtBtns', s.coordFmt);
  _setRadio('langBtns', s.lang);

  /* ── Carte ── */
  _setTileOpt(s.tileStyle);
  _setSlider('zoomSlider', 'zoomVal', s.zoomDefault, '');
  _setToggle('showScaleToggle', s.showScale);
  _setToggle('showLegendToggle', s.showLegend);

  /* ── Alertes & Données ── */
  _setSelect('refreshSelect', String(s.refreshInterval));
  _setSelect('alertLevelSelect', s.alertMinLevel);
  _setToggle('soundAlertToggle', s.soundAlerts);
}

/* ─── Helpers UI ──────────────────────────────────────── */

function _setToggle(id, val) {
  var el = document.getElementById(id);
  if (el) el.checked = !!val;
}

function _setSelect(id, val) {
  var el = document.getElementById(id);
  if (el) el.value = val;
}

function _setSlider(sliderId, valId, val, suffix) {
  var slider = document.getElementById(sliderId);
  var label  = document.getElementById(valId);
  if (slider) {
    slider.value = val;
    _updateSliderGradient(slider);
  }
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

/* ─── Application des paramètres ─────────────────────── */

function applySettings() {
  var s = _settings;

  /* Thème — appliqué uniquement si différent de l'état courant.
     Le script anti-flash dans <head> a déjà positionné le bon thème
     depuis gd-theme. _loadSettings() garantit que s.theme == gd-theme.
     Cette condition évite tout flash résiduel. */
  var html = document.documentElement;
  var currentTheme = html.getAttribute('data-theme') || 'light';
  if (s.theme && currentTheme !== s.theme) {
    html.classList.add('theme-transitioning');
    html.setAttribute('data-theme', s.theme);
    setTimeout(function () { html.classList.remove('theme-transitioning'); }, 380);
  }

  /* Densité */
  html.setAttribute('data-density', s.density);

  /* Légende carte */
  var legend = document.querySelector('.map-legend');
  if (legend) legend.style.display = s.showLegend ? '' : 'none';

  /* Échelle Leaflet */
  /* (le contrôle est déjà sur la carte, on le masque/affiche via CSS) */
  var scaleEl = document.querySelector('.leaflet-control-scale');
  if (scaleEl) scaleEl.style.display = s.showScale ? '' : 'none';

  /* Fond de carte */
  if (map && _mapReady) _applyTileStyle(s.tileStyle);

  /* Langue (minimal — re-render labels clés) */
  _applyLang(s.lang);
}

/* ══════════════════════════════════════════════════════════
   FONDS DE CARTE — URLs corrigées
   ─────────────────────────────────────────────────────────
   {r} SUPPRIMÉ de toutes les URLs CartoDB.
   {r} était le placeholder rétina Leaflet (@2x / vide).
   Certains sous-domaines CartoDB n'ont pas les tuiles @2x
   → 404 → zones grises. Fix : URL fixe sans {r}.
══════════════════════════════════════════════════════════ */
var TILE_CONFIGS = {
  dark: {
    url:        'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
    subdomains: 'abcd',
    label:      'CartoDB Sombre',
    maxZoom:    19,
  },
  light: {
    url:        'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}.png',
    subdomains: 'abcd',
    label:      'CartoDB Clair',
    maxZoom:    19,
  },
  osm: {
    url:        'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    subdomains: '',
    label:      'OpenStreetMap',
    maxZoom:    19,
  },
  topo: {
    url:        'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
    subdomains: 'abc',
    label:      'Topographique',
    maxZoom:    17,
  },
};

/* Tuile transparente 1×1 utilisée comme fallback sur erreur 404 */
var _EMPTY_TILE = 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVQI12NgAAIABQ' +
                  'AABjkB6QAAAABJRU5ErkJggg==';

/**
 * Construit un L.TileLayer avec retry automatique (max 3 tentatives).
 * Chaque tentative ajoute ?_r=N à l'URL pour contourner le cache navigateur.
 */
function _buildTileLayer(style) {
  var cfg = TILE_CONFIGS[style] || TILE_CONFIGS.dark;

  var opts = {
    maxZoom:          cfg.maxZoom || 19,
    keepBuffer:       2,          /* était 4 — trop de requêtes simultanées */
    crossOrigin:      '',
    updateWhenIdle:   false,
    updateWhenZooming: false,
    errorTileUrl:     _EMPTY_TILE, /* tuile transparente au lieu du gris Leaflet */
  };
  if (cfg.subdomains) opts.subdomains = cfg.subdomains;

  var layer = L.tileLayer(cfg.url, opts);

  /* ─── Retry automatique ─────────────────────────────
     Sur tileerror, on retente jusqu'à 3 fois en changeant
     légèrement l'URL (param cache-bust). Au-delà, on
     affiche la tuile transparente (errorTileUrl).
  ─────────────────────────────────────────────────────*/
  layer.on('tileerror', function (ev) {
    var tile = ev.tile;
    var retries = parseInt(tile.dataset.gdRetry || '0', 10);
    if (retries < 3) {
      tile.dataset.gdRetry = retries + 1;
      setTimeout(function () {
        /* Recharge la tuile avec un cache-bust minimal */
        var src = tile.src.split('?')[0];
        tile.src = src + '?_r=' + tile.dataset.gdRetry;
      }, 400 * (retries + 1)); /* délai croissant : 400ms, 800ms, 1200ms */
    }
    /* Si 3 tentatives épuisées → errorTileUrl s'affiche automatiquement */
  });

  return layer;
}

function _applyTileStyle(style) {
  if (!map) return;
  /* Supprime les couches tuiles existantes */
  map.eachLayer(function (layer) {
    if (layer instanceof L.TileLayer) map.removeLayer(layer);
  });
  _tileLayer = _buildTileLayer(style);
  _tileLayer.addTo(map);
  /* Remet les couches de données par-dessus */
  if (lRoads)  { map.removeLayer(lRoads);  lRoads.addTo(map); }
  if (lFloods) { map.removeLayer(lFloods); lFloods.addTo(map); }
  if (lVeg)    { map.removeLayer(lVeg);    lVeg.addTo(map); }
  /* Force le recalcul de taille après changement de couche */
  setTimeout(function () { if (map) map.invalidateSize({ animate: false }); }, 100);
}

var LANG_LABELS = {
  fr: { dashboard: 'Dashboard', routes: 'Routes', floods: 'Inondations', veg: 'Végétation', alerts: 'Alertes', settings: 'Paramètres' },
  en: { dashboard: 'Dashboard', routes: 'Roads',  floods: 'Floods',       veg: 'Vegetation', alerts: 'Alerts',  settings: 'Settings'   },
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

/* ─── Sauvegarde + fermeture ──────────────────────────── */

function saveSettings() {
  /* Lire toutes les valeurs de l'UI dans _settings */
  var s = _settings;

  /* Thème — lire l'état actuel du DOM et synchroniser les deux clés */
  s.theme = document.documentElement.getAttribute('data-theme') || 'light';
  try { localStorage.setItem('gd-theme', s.theme); } catch (e) {}

  /* Densité */
  var da = document.querySelector('.densityBtns .sd-radio-btn.active');
  if (da) s.density = da.dataset.val;

  /* Coord format */
  var ca = document.querySelector('.coordFmtBtns .sd-radio-btn.active');
  if (ca) s.coordFmt = ca.dataset.val;

  /* Langue */
  var la = document.querySelector('.langBtns .sd-radio-btn.active');
  if (la) s.lang = la.dataset.val;

  /* Fond de carte */
  var ta = document.querySelector('.sd-tile-opt.active');
  if (ta) s.tileStyle = ta.dataset.tile;

  /* Zoom */
  var zs = document.getElementById('zoomSlider');
  if (zs) s.zoomDefault = parseInt(zs.value, 10);

  /* Toggles */
  var showScale  = document.getElementById('showScaleToggle');
  var showLegend = document.getElementById('showLegendToggle');
  var soundAlert = document.getElementById('soundAlertToggle');
  if (showScale)  s.showScale  = showScale.checked;
  if (showLegend) s.showLegend = showLegend.checked;
  if (soundAlert) s.soundAlerts = soundAlert.checked;

  /* Selects */
  var rs = document.getElementById('refreshSelect');
  var al = document.getElementById('alertLevelSelect');
  if (rs) s.refreshInterval = parseInt(rs.value, 10);
  if (al) s.alertMinLevel   = al.value;

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

/* ─── Initialisation du drawer ────────────────────────── */

function initSettings() {
  _loadSettings();
  applySettings();    /* applique les settings sauvegardés dès le démarrage */

  /* Onglets */
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

  /* Bouton Paramètres dans la nav */
  document.querySelectorAll('[data-open-settings]').forEach(function (btn) {
    btn.addEventListener('click', openSettings);
    _addRipple(btn);
  });

  /* Fermeture */
  var closeBtn = document.getElementById('settingsClose');
  if (closeBtn) closeBtn.addEventListener('click', closeSettings);

  var backdrop = document.getElementById('settingsBackdrop');
  if (backdrop) backdrop.addEventListener('click', closeSettings);

  /* Vider le cache — anciennement onclick inline, maintenant géré ici
     comme tous les autres boutons du drawer */
  var clearCacheBtn = document.getElementById('clearCacheBtn');
  if (clearCacheBtn) {
    clearCacheBtn.addEventListener('click', function () {
      // On supprime uniquement nos propres clés — localStorage.clear()
      // effacerait aussi les données d'autres apps sur le même domaine.
      ['gd-theme', 'gd-settings'].forEach(function (k) {
        localStorage.removeItem(k);
      });
      toast('Cache local vidé', 'nfo');
      closeSettings();
    });
  }

  /* Touche Escape */
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') closeSettings();
  });

  /* Boutons Sauvegarder / Réinitialiser */
  var saveBtn  = document.getElementById('settingsSave');
  var resetBtn = document.getElementById('settingsReset');
  if (saveBtn)  saveBtn.addEventListener('click', saveSettings);
  if (resetBtn) resetBtn.addEventListener('click', resetSettings);

  /* Sliders */
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

  /* Boutons radio — activer visuellement + live-preview pour le thème */
  document.querySelectorAll('.sd-radio-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var group = this.closest('[class*="Btns"]') || this.closest('.sd-radio-group');
      if (group) {
        group.querySelectorAll('.sd-radio-btn').forEach(function (b) { b.classList.remove('active'); });
        this.classList.add('active');
      }

      // Live-preview du thème : on l'applique immédiatement sans attendre
      // que l'utilisateur clique sur "Enregistrer". Plus intuitif.
      if (group && group.classList.contains('themeBtns')) {
        var picked = this.dataset.val;
        var html   = document.documentElement;
        if (html.getAttribute('data-theme') !== picked) {
          html.classList.add('theme-transitioning');
          html.setAttribute('data-theme', picked);
          setTimeout(function () { html.classList.remove('theme-transitioning'); }, 380);
          // Pas de localStorage ici — ça sera persisté au clic "Enregistrer"
          // ou à la prochaine bascule via toggleTheme()
        }
      }
    });
  });

  /* Choix fond de carte */
  document.querySelectorAll('.sd-tile-opt').forEach(function (opt) {
    opt.addEventListener('click', function () {
      document.querySelectorAll('.sd-tile-opt').forEach(function (o) { o.classList.remove('active'); });
      this.classList.add('active');
    });
  });
}
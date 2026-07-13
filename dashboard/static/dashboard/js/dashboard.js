/* ══════════════════════════════════════════════════════════
   GeoDash — dashboard.js
   Frontend aligné sur le backend actuel :
     · fonds de carte (CartoDB / OSM / topo / satellite)
     · communes cliquables — choroplèthe susceptibilité inondation
       (/api/admin/divisions/?level=commune, propriétés flood_*)
     · overlays GEE live : NDVI Sentinel-2, SAR inondation
     · occupation des sols (Dynamic World, /api/gee/landuse/)
     · clic commune → navigation ?admin=commune:CODE
       (les panneaux sont rendus côté serveur)
══════════════════════════════════════════════════════════ */

let map = null;
let _mapReady = false;
let _tileLayer = null;
let lGeeNdvi = null;   // overlay tuiles NDVI
let lGeeFlood = null;  // overlay tuiles SAR
let lZones = null;     // L.geoJSON des communes cliquables

/* Couleurs de la choroplèthe susceptibilité (alignées flood/scoring.py). */
const FLOOD_COLORS = {
  faible:   '#22c55e',
  modere:   '#eab308',
  eleve:    '#f97316',
  critique: '#dc2626',
};

const GD_SETTINGS_DEFAULT = {
  theme: 'light',
  density: 'normal',
  tile: 'light',
  zoom: 11,
  showScale: true,
  showLegend: true,
};
let _settings = Object.assign({}, GD_SETTINGS_DEFAULT);


/* ══════ Thème ═════════════════════════════════════════════ */

function _applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  document.documentElement.classList.toggle('dark', theme === 'dark');
  document.body.classList.toggle('dark', theme === 'dark');
  try { localStorage.setItem('gd-theme', theme); } catch (e) {}
}

function toggleTheme() {
  var next = (document.documentElement.getAttribute('data-theme') === 'dark') ? 'light' : 'dark';
  _applyTheme(next);
  _settings.theme = next;
  _saveSettings();
}


/* ══════ Fetch authentifié (session Keycloak/Django) ═══════ */

function _fetchJSON(url) {
  return fetch(url, { credentials: 'same-origin', redirect: 'manual' })
    .then(function (r) {
      if (r.type === 'opaqueredirect' || r.status === 401 || r.status === 403) {
        return Promise.reject('session');
      }
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
}


/* ══════ Toasts ════════════════════════════════════════════ */

function toast(msg, type) {
  var host = document.getElementById('toasts');
  if (!host) return;
  var el = document.createElement('div');
  el.className = 'toast' + (type ? ' ' + type : '');
  el.textContent = msg;
  host.appendChild(el);
  setTimeout(function () { el.classList.add('out'); }, 3200);
  setTimeout(function () { el.remove(); }, 3800);
}


/* ══════ Carte ═════════════════════════════════════════════ */

const _TILE_DEFS = {
  dark: {
    url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
    attribution: '&copy; OSM &copy; CARTO', maxZoom: 20,
  },
  light: {
    // Voyager : plus lisible que Positron (relief doux, labels nets) →
    // meilleure mise en valeur des couches d'analyse par-dessus.
    url: 'https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png',
    attribution: '&copy; OSM &copy; CARTO', maxZoom: 20,
  },
  osm: {
    url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; OpenStreetMap', maxZoom: 19,
  },
  topo: {
    url: 'https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',
    attribution: '&copy; OSM &copy; OpenTopoMap', maxZoom: 17,
  },
  satellite: {
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Tiles &copy; Esri', maxZoom: 19,
  },
};

function _buildTileLayer(style) {
  var def = _TILE_DEFS[style] || _TILE_DEFS.light;
  return L.tileLayer(def.url, {
    attribution: def.attribution, maxZoom: def.maxZoom, detectRetina: true,
  });
}

function _applyTileStyle(style) {
  if (!map) return;
  if (_tileLayer) map.removeLayer(_tileLayer);
  _tileLayer = _buildTileLayer(style).addTo(map);
  _tileLayer.bringToBack();
  document.querySelectorAll('[data-basemap]').forEach(function (b) {
    b.classList.toggle('active', b.dataset.basemap === style);
  });
}

function initMap(lat, lng, bounds) {
  var el = document.getElementById('map');
  if (!el || typeof L === 'undefined') return;

  map = L.map('map', {
    center: [lat || 5.35, lng || -4.00],
    zoom: _settings.zoom || 11,
    zoomControl: true,
    zoomSnap: 0.5,
  });
  _tileLayer = _buildTileLayer(_settings.tile).addTo(map);
  if (_settings.showScale) L.control.scale({ imperial: false }).addTo(map);

  document.querySelectorAll('[data-basemap]').forEach(function (b) {
    b.classList.toggle('active', b.dataset.basemap === _settings.tile);
  });

  _mapReady = true;
  if (bounds) {
    try { map.fitBounds(bounds, { padding: [24, 24], maxZoom: 14 }); } catch (e) {}
  }

  _bindMapCoords();
  _loadCommunes();
  // Couches factuelles affichées par défaut : points observés + points chauds.
  _toggleFloodEvents(document.querySelector('[data-toggle-events]'));
  _toggleFloodHeat(document.querySelector('[data-toggle-heat]'));
  _hideOverlay();
}

function _hideOverlay() {
  var o = document.getElementById('mapOverlay');
  if (o) { o.style.opacity = '0'; setTimeout(function () { o.style.display = 'none'; }, 350); }
}

function _bindMapCoords() {
  if (!map) return;
  var elLat = document.getElementById('mapCoordsLat');
  var elLng = document.getElementById('mapCoordsLng');
  var elZoom = document.getElementById('mapCoordsZoom');
  if (!elLat || !elLng || !elZoom) return;

  var pending = false, last = null;
  map.on('mousemove', function (e) {
    last = e.latlng;
    if (pending) return;
    pending = true;
    requestAnimationFrame(function () {
      pending = false;
      if (!last) return;
      elLat.textContent = last.lat.toFixed(4);
      elLng.textContent = Math.abs(last.lng).toFixed(4);
    });
  });
  var updZoom = function () { elZoom.textContent = map.getZoom(); };
  map.on('zoomend', updZoom);
  updZoom();
}


/* ══════ Communes cliquables — choroplèthe inondation ══════ */

function _currentAdminValue() {
  var m = (window.location.search || '').match(/[?&]admin=([^&]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

/* Communes = simples CONTOURS (pas de remplissage : la carte reste lisible
   et les points d'inondation ressortent). Un léger fill quasi invisible
   garde l'intérieur cliquable. */
function _zoneStyle(props) {
  var sel = _currentAdminValue();
  var isSel = sel === 'commune:' + props.code;
  return {
    color:       isSel ? '#dc2626' : '#64748b',
    weight:      isSel ? 3 : 1.2,
    opacity:     isSel ? 1 : 0.55,
    fillColor:   '#64748b',
    fillOpacity: isSel ? 0.05 : 0.03,
  };
}

function _zoneTooltip(p) {
  var s = '<b>' + p.name + '</b>';
  if (p.flood_susceptibility != null) {
    s += '<br>Susceptibilité inondation : <b>' + p.flood_susceptibility + '/100</b>';
  }
  return s;
}

function _loadCommunes() {
  if (!map || !_mapReady) return Promise.resolve();
  return _fetchJSON('/api/admin/divisions/?level=commune')
    .then(function (data) {
      if (!data || !data.features) return;
      if (lZones) { map.removeLayer(lZones); lZones = null; }
      lZones = L.geoJSON(data, {
        style: function (f) { return _zoneStyle(f.properties); },
        onEachFeature: function (feature, layer) {
          var p = feature.properties;
          layer.bindTooltip(_zoneTooltip(p), { sticky: true });
          layer.on('mouseover', function () { layer.setStyle({ weight: 3, fillOpacity: 0.42 }); });
          layer.on('mouseout', function () { layer.setStyle(_zoneStyle(p)); });
          layer.on('click', function (e) {
            L.DomEvent.stop(e);
            switchAdmin('commune:' + p.code);
          });
        },
      }).addTo(map);
      lZones.bringToBack();
    })
    .catch(function (err) {
      if (err === 'session') return;
      console.warn('[Communes] échec :', err);
      toast('Communes indisponibles', 'err');
    });
}

/* Navigation serveur : le backend rend les panneaux (KPIs, inondation,
   occupation des sols) pour l'entité sélectionnée. */
function switchAdmin(value) {
  var url = new URL(window.location.href);
  if (value) url.searchParams.set('admin', value);
  else url.searchParams.delete('admin');
  window.location.assign(url.toString());
}


/* ══════ Zones inondées observées (points + points chauds) ═ */

let lFloodEvents = null;   // couche points cliquables
let lFloodHeat = null;     // couche carte de chaleur (concentration)
let _eventsPromise = null; // cache : un seul fetch pour les deux couches

function _fetchEvents() {
  if (!_eventsPromise) {
    _eventsPromise = _fetchJSON('/api/flood/events/').catch(function (err) {
      _eventsPromise = null;   // réessayable au prochain clic
      throw err;
    });
  }
  return _eventsPromise;
}

function _toggleFloodEvents(chip) {
  if (!map) return;
  if (lFloodEvents) {
    map.removeLayer(lFloodEvents);
    lFloodEvents = null;
    if (chip) chip.classList.remove('on');
    return;
  }
  if (chip) chip.classList.add('loading');
  _fetchEvents()
    .then(function (data) {
      if (chip) chip.classList.remove('loading');
      if (!data || !data.features || !data.features.length) {
        toast('Aucune zone inondée enregistrée', 'warn');
        return;
      }
      lFloodEvents = L.geoJSON(data, {
        pointToLayer: function (f, latlng) {
          // Rouge vif + liseré blanc : ressort sur fond clair, satellite,
          // ET par-dessus la lagune (où le bleu se noyait).
          return L.circleMarker(latlng, {
            radius: 7, color: '#ffffff', weight: 2.5,
            fillColor: '#e11d48', fillOpacity: 0.95,
          });
        },
        style: { color: '#be123c', weight: 2, fillColor: '#e11d48', fillOpacity: 0.4 },
        onEachFeature: function (f, layer) {
          var p = f.properties || {};
          var html = '<b>Zone inondée observée</b>'
            + (p.name ? '<br>' + p.name : '')
            + (p.date ? '<br>' + p.date : '')
            + (p.source ? '<br><small>' + p.source + '</small>' : '');
          layer.bindPopup(html);
          if (layer.bindTooltip) layer.bindTooltip('Zone inondée', { direction: 'top' });
        },
      }).addTo(map);
      if (chip) chip.classList.add('on');
    })
    .catch(function (err) {
      if (chip) chip.classList.remove('loading');
      if (err === 'session') return;
      console.warn('[Zones inondées] échec :', err);
      toast('Zones inondées indisponibles', 'err');
    });
}

/* Carte de chaleur : la CONCENTRATION des inondations observées.
   Lecture "points chauds" type ArcGIS Pro — 100 % factuelle (aucun
   score) : plus les relevés se recouvrent, plus le halo chauffe. */
function _toggleFloodHeat(chip) {
  if (!map || typeof L.heatLayer !== 'function') return;
  if (lFloodHeat) {
    map.removeLayer(lFloodHeat);
    lFloodHeat = null;
    if (chip) chip.classList.remove('on');
    return;
  }
  if (chip) chip.classList.add('loading');
  _fetchEvents()
    .then(function (data) {
      if (chip) chip.classList.remove('loading');
      if (!data || !data.features || !data.features.length) {
        toast('Aucune zone inondée enregistrée', 'warn');
        return;
      }
      var pts = [];
      data.features.forEach(function (f) {
        var g = f.geometry || {};
        if (g.type === 'Point') pts.push([g.coordinates[1], g.coordinates[0], 1.0]);
      });
      lFloodHeat = L.heatLayer(pts, {
        radius: 42, blur: 30, maxZoom: 14, minOpacity: 0.35,
        gradient: { 0.25: '#3b82f6', 0.5: '#22c55e', 0.75: '#eab308', 1.0: '#ef4444' },
      }).addTo(map);
      if (chip) chip.classList.add('on');
    })
    .catch(function (err) {
      if (chip) chip.classList.remove('loading');
      if (err === 'session') return;
      console.warn('[Points chauds] échec :', err);
      toast('Points chauds indisponibles', 'err');
    });
}


/* ══════ Overlays GEE (NDVI · SAR) ═════════════════════════ */

function _geeQuery() {
  return window.location.search || '';
}

function _toggleGeeOverlay(name, chip) {
  var isNdvi = (name === 'ndvi');
  var current = isNdvi ? lGeeNdvi : lGeeFlood;

  if (current) {
    map.removeLayer(current);
    if (isNdvi) lGeeNdvi = null; else lGeeFlood = null;
    if (chip) chip.classList.remove('on');
    return;
  }

  if (chip) chip.classList.add('loading');
  var url = isNdvi ? '/api/gee/ndvi/' + _geeQuery() : '/api/gee/flood/' + _geeQuery();
  _fetchJSON(url)
    .then(function (data) {
      if (chip) chip.classList.remove('loading');
      if (!data || !data.tiles_url) {
        toast(isNdvi ? 'NDVI indisponible sur cette emprise' : 'Pas de détection SAR récente', 'warn');
        return;
      }
      var lyr = L.tileLayer(data.tiles_url, { opacity: isNdvi ? 0.62 : 0.7, maxZoom: 20 }).addTo(map);
      if (isNdvi) lGeeNdvi = lyr; else lGeeFlood = lyr;
      if (chip) chip.classList.add('on');
    })
    .catch(function (err) {
      if (chip) chip.classList.remove('loading');
      if (err === 'session') return;
      console.warn('[GEE ' + name + '] échec :', err);
      toast('Service GEE indisponible', 'err');
    });
}


/* ══════ Occupation des sols (Dynamic World) ═══════════════ */

function loadLandUseBreakdown() {
  var card = document.getElementById('landuseCard');
  if (!card) return;
  var src = document.getElementById('landuseSource');

  _fetchJSON('/api/gee/landuse/' + (window.location.search || ''))
    .then(function (data) {
      if (!data || data.no_data) {
        if (src) src.textContent = 'Donnée non disponible';
        return;
      }
      if (src) src.textContent = 'Analyse satellite · 6 derniers mois';
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


/* ══════ Panneaux, arbre, recherche ════════════════════════ */

function initPanelTabs() {
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
}

function initAdminTree() {
  // Plier / déplier une catégorie
  document.querySelectorAll('[data-tree-toggle]').forEach(function (row) {
    row.addEventListener('click', function () {
      var caret = this.querySelector('.caret');
      var children = this.nextElementSibling;
      if (!children || !children.classList.contains('tree-children')) return;
      var open = children.style.display !== 'none';
      children.style.display = open ? 'none' : '';
      if (caret) caret.classList.toggle('open', !open);
    });
  });

  // Clic sur une entité → filtre admin (navigation serveur)
  document.querySelectorAll('[data-admin-value]').forEach(function (row) {
    row.addEventListener('click', function () {
      var v = this.dataset.adminValue;
      switchAdmin(v === _currentAdminValue() ? null : v);
    });
  });

  // Lignes cliquables du classement inondation (panneau droit)
  document.querySelectorAll('[data-flood-goto]').forEach(function (row) {
    row.addEventListener('click', function () {
      switchAdmin('commune:' + this.dataset.floodGoto);
    });
  });
}

function _filterTree(q) {
  q = (q || '').trim().toLowerCase();
  document.querySelectorAll('.tree-row-zone[data-admin-value]').forEach(function (row) {
    var label = row.querySelector('.tree-label');
    var hit = !q || (label && label.textContent.toLowerCase().indexOf(q) !== -1);
    row.style.display = hit ? '' : 'none';
  });
  // Une catégorie reste visible si au moins un enfant est visible
  document.querySelectorAll('.tree-children').forEach(function (children) {
    if (q) children.style.display = '';
  });
}

function initTreeSearch() {
  ['treeSearch', 'topSearch'].forEach(function (id) {
    var input = document.getElementById(id);
    if (input) input.addEventListener('input', function () { _filterTree(this.value); });
  });
}


/* ══════ Écouteurs globaux ═════════════════════════════════ */

function initEventListeners() {
  var themeBtn = document.getElementById('themeToggle');
  if (themeBtn) themeBtn.addEventListener('click', toggleTheme);

  // Overlays GEE
  document.querySelectorAll('[data-toggle-overlay]').forEach(function (chip) {
    chip.addEventListener('click', function () {
      _toggleGeeOverlay(this.dataset.toggleOverlay, this);
    });
  });

  // Zones inondées observées (points) + points chauds (concentration)
  document.querySelectorAll('[data-toggle-events]').forEach(function (chip) {
    chip.addEventListener('click', function () { _toggleFloodEvents(this); });
  });
  document.querySelectorAll('[data-toggle-heat]').forEach(function (chip) {
    chip.addEventListener('click', function () { _toggleFloodHeat(this); });
  });

  // Fonds de carte
  document.querySelectorAll('[data-basemap]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      _settings.tile = this.dataset.basemap;
      _applyTileStyle(_settings.tile);
      _saveSettings();
    });
  });

  // Plein écran
  var fsBtn = document.querySelector('[data-toggle-fullscreen]');
  if (fsBtn) fsBtn.addEventListener('click', function () {
    var area = document.querySelector('.main-area') || document.documentElement;
    if (!document.fullscreenElement) {
      (area.requestFullscreen || function () {}).call(area);
    } else if (document.exitFullscreen) {
      document.exitFullscreen();
    }
    setTimeout(function () { if (map) map.invalidateSize(); }, 350);
  });

  // Bouton « Tout afficher » (reset filtre admin)
  var reset = document.getElementById('rpReset');
  if (reset) {
    if (_currentAdminValue()) reset.style.display = '';
    reset.addEventListener('click', function () { switchAdmin(null); });
  }
}


/* ══════ Réglages (drawer) ═════════════════════════════════ */

function _loadSettings() {
  try {
    var raw = localStorage.getItem('gd-settings');
    if (raw) _settings = Object.assign({}, GD_SETTINGS_DEFAULT, JSON.parse(raw));
  } catch (e) { _settings = Object.assign({}, GD_SETTINGS_DEFAULT); }
  var savedTheme = null;
  try { savedTheme = localStorage.getItem('gd-theme'); } catch (e) {}
  if (savedTheme) _settings.theme = savedTheme;
}

function _saveSettings() {
  try { localStorage.setItem('gd-settings', JSON.stringify(_settings)); } catch (e) {}
}

function openSettings() {
  document.getElementById('settingsDrawer').classList.add('open');
  document.getElementById('settingsBackdrop').classList.add('show');
  _populateSettingsUI();
}

function closeSettings() {
  document.getElementById('settingsDrawer').classList.remove('open');
  document.getElementById('settingsBackdrop').classList.remove('show');
}

function _populateSettingsUI() {
  _setRadio('themeBtns', _settings.theme);
  _setRadio('densityBtns', _settings.density);
  document.querySelectorAll('.sd-tile-opt').forEach(function (t) {
    t.classList.toggle('active', t.dataset.tile === _settings.tile);
  });
  var slider = document.getElementById('zoomSlider');
  var val = document.getElementById('zoomVal');
  if (slider) {
    slider.value = _settings.zoom;
    slider.style.setProperty('--pct',
      ((_settings.zoom - slider.min) / (slider.max - slider.min) * 100) + '%');
  }
  if (val) val.textContent = _settings.zoom;
  var sc = document.getElementById('showScaleToggle');
  if (sc) sc.checked = !!_settings.showScale;
  var lg = document.getElementById('showLegendToggle');
  if (lg) lg.checked = !!_settings.showLegend;
}

function _setRadio(groupClass, value) {
  document.querySelectorAll('.' + groupClass + ' .sd-radio-btn').forEach(function (b) {
    b.classList.toggle('active', b.dataset.val === value);
  });
}

function _applyLegendVisibility() {
  var legend = document.querySelector('.map-legend');
  if (legend) legend.style.display = _settings.showLegend ? '' : 'none';
}

function saveSettings() {
  var themeBtn = document.querySelector('.themeBtns .sd-radio-btn.active');
  var densBtn = document.querySelector('.densityBtns .sd-radio-btn.active');
  var tileOpt = document.querySelector('.sd-tile-opt.active');
  var slider = document.getElementById('zoomSlider');
  var sc = document.getElementById('showScaleToggle');
  var lg = document.getElementById('showLegendToggle');

  if (themeBtn) _settings.theme = themeBtn.dataset.val;
  if (densBtn) _settings.density = densBtn.dataset.val;
  if (tileOpt) _settings.tile = tileOpt.dataset.tile;
  if (slider) _settings.zoom = parseInt(slider.value, 10);
  if (sc) _settings.showScale = sc.checked;
  if (lg) _settings.showLegend = lg.checked;

  _saveSettings();
  _applyTheme(_settings.theme);
  document.body.setAttribute('data-density', _settings.density);
  _applyTileStyle(_settings.tile);
  _applyLegendVisibility();
  closeSettings();
  toast('Réglages enregistrés');
}

function resetSettings() {
  _settings = Object.assign({}, GD_SETTINGS_DEFAULT);
  _saveSettings();
  _populateSettingsUI();
  _applyTheme(_settings.theme);
  _applyTileStyle(_settings.tile);
  _applyLegendVisibility();
  toast('Réglages réinitialisés');
}

function initSettings() {
  _loadSettings();
  _applyTheme(_settings.theme);
  document.body.setAttribute('data-density', _settings.density);
  _applyLegendVisibility();

  document.querySelectorAll('[data-open-settings]').forEach(function (b) {
    b.addEventListener('click', openSettings);
  });
  var closeBtn = document.getElementById('settingsClose');
  if (closeBtn) closeBtn.addEventListener('click', closeSettings);
  var backdrop = document.getElementById('settingsBackdrop');
  if (backdrop) backdrop.addEventListener('click', closeSettings);

  // Onglets du drawer
  document.querySelectorAll('.sd-tab').forEach(function (tab) {
    tab.addEventListener('click', function () {
      document.querySelectorAll('.sd-tab').forEach(function (t) { t.classList.remove('active'); });
      document.querySelectorAll('.sd-panel').forEach(function (p) { p.classList.remove('active'); });
      this.classList.add('active');
      var panel = document.getElementById(this.dataset.panel);
      if (panel) panel.classList.add('active');
    });
  });

  // Groupes radio + tuiles + slider
  document.querySelectorAll('.sd-radio-group').forEach(function (group) {
    group.addEventListener('click', function (e) {
      var btn = e.target.closest('.sd-radio-btn');
      if (!btn) return;
      group.querySelectorAll('.sd-radio-btn').forEach(function (b) { b.classList.remove('active'); });
      btn.classList.add('active');
    });
  });
  document.querySelectorAll('.sd-tile-opt').forEach(function (t) {
    t.addEventListener('click', function () {
      document.querySelectorAll('.sd-tile-opt').forEach(function (o) { o.classList.remove('active'); });
      this.classList.add('active');
    });
  });
  var slider = document.getElementById('zoomSlider');
  if (slider) slider.addEventListener('input', function () {
    var val = document.getElementById('zoomVal');
    if (val) val.textContent = this.value;
    this.style.setProperty('--pct',
      ((this.value - this.min) / (this.max - this.min) * 100) + '%');
  });

  var save = document.getElementById('settingsSave');
  if (save) save.addEventListener('click', saveSettings);
  var reset = document.getElementById('settingsReset');
  if (reset) reset.addEventListener('click', resetSettings);
}

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
let _geeLayers = {};    // overlays tuiles GEE actifs (ndvi/sar/contours)
let lHillshade = null;  // relief ombré (Esri World Hillshade)
let lZones = null;      // L.geoJSON des communes cliquables

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
  tile: 'osm',   // fond par défaut : OpenStreetMap standard (choix utilisateur)
  zoom: 11,
  showScale: true,
  showLegend: true,
  // Couches d'analyse (Paramètres → Couches). Par défaut : seuls les
  // relevés observés — la carte reste sobre.
  layers: { events: true, heat: false, ndvi: false, sar: false, contours: false, relief: false },
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
    // OpenStreetMap standard — nomenclature complète, rendu de référence.
    // detectRetina (option globale) rend le texte net sur écrans HiDPI.
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
  relief: {
    // Relief ombré Esri en FOND (analyse topographique) — distinct de la
    // couche « Relief ombré » superposable des Paramètres → Couches.
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Tiles &copy; Esri', maxZoom: 16,
  },
  // 'gee' : fond Sentinel-2 servi par Google Earth Engine — asynchrone
  // (URL de tuiles obtenue via /api/gee/terrain3d/), géré à part dans
  // _applyTileStyle. Même flux que l'imagerie drapée de la vue 3D.
};

function _buildTileLayer(style) {
  var def = _TILE_DEFS[style] || _TILE_DEFS.light;
  return L.tileLayer(def.url, {
    attribution: def.attribution, maxZoom: def.maxZoom, detectRetina: true,
    className: 'gd-basemap',   // léger assourdissement (cf. CSS)
  });
}

let _labelsLayer = null;   // toponymie par-dessus les fonds imagerie (sans noms)
let _tileToken = 0;        // anti-course : seul le dernier choix de fond gagne

/* Étiquettes claires avec halo, conçues pour fonds sombres/imagerie. */
function _addImageryLabels() {
  _labelsLayer = L.tileLayer(
    'https://{s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}{r}.png',
    { maxZoom: 19, opacity: 0.95 }
  ).addTo(map);
}

function _applyTileStyle(style) {
  if (!map) return;
  var token = ++_tileToken;
  if (_tileLayer) { map.removeLayer(_tileLayer); _tileLayer = null; }
  if (_labelsLayer) { map.removeLayer(_labelsLayer); _labelsLayer = null; }

  if (style === 'gee') {
    // Fond Sentinel-2 GEE : l'URL des tuiles vient du backend (asynchrone).
    _fetch3DConfig()
      .then(function (cfg) {
        if (token !== _tileToken || !map) return;   // fond changé entre-temps
        _tileLayer = L.tileLayer(cfg.imagery_tiles_url, {
          attribution: 'Sentinel-2 &copy; Copernicus · via Google Earth Engine',
          maxNativeZoom: 15, maxZoom: 20, className: 'gd-basemap',
        }).addTo(map);
        _tileLayer.bringToBack();
        _addImageryLabels();
      })
      .catch(function (err) {
        if (err === 'session' || token !== _tileToken) return;
        toast('Fond GEE indisponible — bascule sur Satellite Esri', 'warn');
        _settings.tile = 'satellite';
        _saveSettings();
        _applyTileStyle('satellite');
      });
  } else {
    _tileLayer = _buildTileLayer(style).addTo(map);
    _tileLayer.bringToBack();
    if (style === 'satellite') _addImageryLabels();
  }

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
  _applyTileStyle(_settings.tile);   // gère aussi le fond GEE (asynchrone)
  if (_settings.showScale) L.control.scale({ imperial: false }).addTo(map);

  _mapReady = true;
  if (bounds) {
    try { map.fitBounds(bounds, { padding: [24, 24], maxZoom: 14 }); } catch (e) {}
  }

  _bindMapCoords();
  _loadCommunes();
  // Applique les couches choisies (Paramètres → Couches, persistées).
  Object.keys(LAYER_TOGGLES).forEach(function (name) {
    if (_settings.layers && _settings.layers[name]) LAYER_TOGGLES[name]();
  });
  _updateLegend();
  // Sonde altimétrique : clic droit n'importe où → altitude + drainage.
  map.on('contextmenu', _probeElevation);
  _hideOverlay();
}

/* Bascule effective de chaque couche (les fonctions _toggle* inversent
   l'état courant ; l'état désiré vit dans _settings.layers). */
const LAYER_TOGGLES = {
  events:   function () { _toggleFloodEvents(null); },
  heat:     function () { _toggleFloodHeat(null); },
  ndvi:     function () { _toggleGeeOverlay('ndvi', null); },
  sar:      function () { _toggleGeeOverlay('sar', null); },
  contours: function () { _toggleGeeOverlay('contours', null); },
  relief:   function () { _toggleRelief(null); },
};

/* État effectif d'une couche sur la carte. */
function _layerActive(name) {
  if (name === 'events') return !!lFloodEvents;
  if (name === 'heat') return !!lFloodHeat;
  if (name === 'relief') return !!lHillshade;
  return !!_geeLayers[name];
}

/* Aligne les couches réelles sur _settings.layers (après Réinitialiser). */
function _syncLayers() {
  Object.keys(LAYER_TOGGLES).forEach(function (name) {
    if (_layerActive(name) !== !!(_settings.layers && _settings.layers[name])) {
      LAYER_TOGGLES[name]();
    }
  });
  _updateLegend();
}

/* Légende dynamique : n'affiche que les lignes des couches actives. */
function _updateLegend() {
  var lyr = _settings.layers || {};
  document.querySelectorAll('#mapLegend [data-legend]').forEach(function (row) {
    row.style.display = lyr[row.dataset.legend] ? '' : 'none';
  });
}

/* Clic droit sur la carte → interroge le MNT au point exact :
   altitude (mer = 0) et hauteur au-dessus du drainage le plus proche.
   C'est l'information altimétrique EXPLOITABLE (pas juste des lignes). */
function _probeElevation(e) {
  var ll = e.latlng;
  var popup = L.popup({ maxWidth: 260 })
    .setLatLng(ll)
    .setContent('<b>Sonde altimétrique</b><br>Interrogation du MNT…')
    .openOn(map);

  _fetchJSON('/api/gee/elevation/?lat=' + ll.lat.toFixed(5) + '&lng=' + ll.lng.toFixed(5))
    .then(function (d) {
      if (!d || d.no_data || d.elevation_m == null) {
        popup.setContent('<b>Sonde altimétrique</b><br>Donnée indisponible ici.');
        return;
      }
      var html = '<b>Sonde altimétrique</b>'
        + '<br>Altitude : <b>' + d.elevation_m.toFixed(0) + ' m</b> (niveau mer)'
        + (d.hand_m != null
            ? '<br>Au-dessus du drainage : <b>' + d.hand_m.toFixed(1) + ' m</b>'
              + (d.hand_m < 5 ? ' <span style="color:#dc2626;font-weight:700">— zone basse</span>' : '')
            : '')
        + '<br><small style="color:#64748b">' + ll.lat.toFixed(5) + ', ' + ll.lng.toFixed(5) + '</small>';
      popup.setContent(html);
    })
    .catch(function (err) {
      if (err === 'session') return;
      popup.setContent('<b>Sonde altimétrique</b><br>Service indisponible.');
    });
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
    weight:      isSel ? 2.2 : 1.1,
    opacity:     isSel ? 0.85 : 0.5,
    fillColor:   '#64748b',
    fillOpacity: isSel ? 0.04 : 0.02,
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
          // Étiquette permanente : nomenclature officielle (base admin),
          // accents corrects (Port-Bouët, Attécoubé…). Masquée aux petits
          // zooms via la classe .zoom-low sur #map (cf. _bindLabelZoom).
          layer.bindTooltip(p.name, {
            permanent: true, direction: 'center',
            className: 'commune-label', interactive: false,
          });
          layer.on('mouseover', function () { layer.setStyle({ weight: 3, fillOpacity: 0.42 }); });
          layer.on('mouseout', function () { layer.setStyle(_zoneStyle(p)); });
          layer.on('click', function (e) {
            L.DomEvent.stop(e);
            switchAdmin('commune:' + p.code);
          });
        },
      }).addTo(map);
      lZones.bringToBack();
      _bindLabelZoom();
    })
    .catch(function (err) {
      if (err === 'session') return;
      console.warn('[Communes] échec :', err);
      toast('Communes indisponibles', 'err');
    });
}

/* Masque les étiquettes de communes quand on dézoome (vue pays :
   elles se chevaucheraient) — simple bascule de classe CSS. */
let _labelZoomBound = false;
function _bindLabelZoom() {
  if (!map || _labelZoomBound) return;
  _labelZoomBound = true;
  var el = document.getElementById('map');
  var apply = function () {
    if (el) el.classList.toggle('zoom-low', map.getZoom() < 10);
  };
  map.on('zoomend', apply);
  apply();
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


/* ══════ Overlays GEE (NDVI · SAR · Courbes de niveau) ═════ */

const GEE_OVERLAY_DEFS = {
  ndvi:     { url: '/api/gee/ndvi/',     opacity: 0.62, empty: 'NDVI indisponible sur cette emprise' },
  sar:      { url: '/api/gee/flood/',    opacity: 0.70, empty: 'Pas de détection SAR récente' },
  contours: { url: '/api/gee/contours/', opacity: 0.80, empty: 'Courbes de niveau indisponibles' },
};

function _geeQuery() {
  return window.location.search || '';
}

function _toggleGeeOverlay(name, chip) {
  var def = GEE_OVERLAY_DEFS[name];
  if (!def || !map) return;

  if (_geeLayers[name]) {
    map.removeLayer(_geeLayers[name]);
    delete _geeLayers[name];
    if (chip) chip.classList.remove('on');
    return;
  }

  if (chip) chip.classList.add('loading');
  _fetchJSON(def.url + _geeQuery())
    .then(function (data) {
      if (chip) chip.classList.remove('loading');
      if (!data || !data.tiles_url) {
        toast(def.empty, 'warn');
        return;
      }
      _geeLayers[name] = L.tileLayer(data.tiles_url, { opacity: def.opacity, maxZoom: 20 }).addTo(map);
      if (chip) chip.classList.add('on');
    })
    .catch(function (err) {
      if (chip) chip.classList.remove('loading');
      if (err === 'session') return;
      console.warn('[GEE ' + name + '] échec :', err);
      toast('Service GEE indisponible', 'err');
    });
}


/* ══════ Relief ombré (hillshade — lisibilité du terrain) ══ */

function _toggleRelief(chip) {
  if (!map) return;
  if (lHillshade) {
    map.removeLayer(lHillshade);
    lHillshade = null;
    if (chip) chip.classList.remove('on');
    return;
  }
  lHillshade = L.tileLayer(
    'https://server.arcgisonline.com/ArcGIS/rest/services/Elevation/World_Hillshade/MapServer/tile/{z}/{y}/{x}',
    { opacity: 0.45, maxZoom: 16 }
  ).addTo(map);
  if (chip) chip.classList.add('on');
}


/* ══════ Vue 3D — MapLibre GL + fond GEE ═══════════════════
   Fond « précision » : imagerie Sentinel-2 (composite 12 mois,
   vraie couleur) drapée sur le relief réel du MNT Copernicus
   GLO-30, tous deux servis en direct par Google Earth Engine
   (/api/gee/terrain3d/). MapLibre est chargé à la demande au
   premier passage en 3D — la page reste légère en 2D. */

let _map3d = null;           // instance MapLibre (réutilisée entre bascules)
let _mode3d = false;
let _maplibrePromise = null; // chargement unique de la lib
let _cfg3dPromise = null;    // config tuiles GEE (un seul fetch)

const MAPLIBRE_BASE = 'https://cdn.jsdelivr.net/npm/maplibre-gl@5.6.0/dist/maplibre-gl';

function _loadMapLibre() {
  if (window.maplibregl) return Promise.resolve();
  if (_maplibrePromise) return _maplibrePromise;
  _maplibrePromise = new Promise(function (resolve, reject) {
    var css = document.createElement('link');
    css.rel = 'stylesheet';
    css.href = MAPLIBRE_BASE + '.css';
    document.head.appendChild(css);
    var js = document.createElement('script');
    js.src = MAPLIBRE_BASE + '.js';
    js.onload = resolve;
    js.onerror = function () {
      _maplibrePromise = null;
      reject(new Error('Chargement MapLibre impossible'));
    };
    document.head.appendChild(js);
  });
  return _maplibrePromise;
}

function _fetch3DConfig() {
  if (!_cfg3dPromise) {
    _cfg3dPromise = _fetchJSON('/api/gee/terrain3d/').then(function (d) {
      if (!d || d.no_data || !d.imagery_tiles_url || !d.dem_tiles_url) {
        _cfg3dPromise = null;   // réessayable
        throw new Error('gee-off');
      }
      return d;
    }).catch(function (err) {
      _cfg3dPromise = null;
      throw err;
    });
  }
  return _cfg3dPromise;
}

function _toggle3D(btn) {
  if (_mode3d) { _exit3D(btn); return; }
  if (btn) btn.classList.add('loading');

  Promise.all([_loadMapLibre(), _fetch3DConfig()])
    .then(function (res) {
      if (btn) { btn.classList.remove('loading'); btn.classList.add('active'); btn.setAttribute('aria-pressed', 'true'); }
      _enter3D(res[1]);
    })
    .catch(function (err) {
      if (btn) btn.classList.remove('loading');
      _setRadio('viewModeBtns', '2d');   // le drawer reste cohérent
      if (err === 'session') return;
      console.warn('[3D] échec :', err);
      toast(err && err.message === 'gee-off'
        ? 'Fond 3D indisponible — service GEE hors ligne'
        : 'Vue 3D indisponible', 'err');
    });
}

function _enter3D(cfg) {
  var area = document.querySelector('.main-area');
  if (area) area.classList.add('mode-3d');
  _mode3d = true;
  _setRadio('viewModeBtns', '3d');

  // Reprend la vue courante de la carte 2D (MapLibre : tuiles 512 px,
  // d'où le décalage de -1 sur le zoom par rapport à Leaflet).
  var c = map ? map.getCenter() : { lat: 5.35, lng: -4.0 };
  var z = map ? map.getZoom() - 1 : 10;

  if (_map3d) {
    _map3d.jumpTo({ center: [c.lng, c.lat], zoom: z });
    _map3d.resize();
    return;
  }

  _map3d = new maplibregl.Map({
    container: 'map3d',
    center: [c.lng, c.lat],
    zoom: z,
    pitch: 60,          // vue inclinée d'emblée : le relief se lit tout de suite
    bearing: 0,
    maxPitch: 80,
    attributionControl: { compact: true },
    style: {
      version: 8,
      sources: {
        'gee-imagery': {
          type: 'raster',
          tiles: [cfg.imagery_tiles_url],
          tileSize: 256,
          maxzoom: 15,   // au-delà, suréchantillonnage local (S2 = 10 m)
          attribution: 'Imagerie Sentinel-2 & MNT GLO-30 © Copernicus · via Google Earth Engine',
        },
        'gee-dem': {
          type: 'raster-dem',
          tiles: [cfg.dem_tiles_url],
          tileSize: 256,
          encoding: cfg.encoding || 'terrarium',
          maxzoom: 12,   // GLO-30 = 30 m : inutile de recalculer plus fin
        },
      },
      layers: [
        { id: 'bg', type: 'background', paint: { 'background-color': '#0b1526' } },
        { id: 'gee-imagery', type: 'raster', source: 'gee-imagery' },
      ],
      sky: {
        'sky-color': '#87b6d8',
        'horizon-color': '#e8f1f8',
        'fog-color': '#dde8f0',
        'sky-horizon-blend': 0.6,
        'horizon-fog-blend': 0.6,
        'fog-ground-blend': 0.85,
      },
    },
  });
  _map3d.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-left');
  _map3d.on('load', function () {
    // Exagération légère : la région d'Abidjan est peu marquée (< 150 m) —
    // sans elle, le relief serait imperceptible.
    _map3d.setTerrain({ source: 'gee-dem', exaggeration: 1.6 });
  });
}

function _exit3D(btn) {
  var area = document.querySelector('.main-area');
  if (area) area.classList.remove('mode-3d');
  _mode3d = false;
  _setRadio('viewModeBtns', '2d');
  if (btn) { btn.classList.remove('active'); btn.setAttribute('aria-pressed', 'false'); }
  // Ramène la carte 2D là où l'utilisateur a navigué en 3D.
  if (_map3d && map) {
    var c = _map3d.getCenter();
    map.setView([c.lat, c.lng], Math.round(_map3d.getZoom()) + 1);
    map.invalidateSize();
  }
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

  // (Les couches d'analyse se gèrent dans Paramètres → Couches,
  //  cf. initSettings — plus de chips sur la carte.)

  // Fonds de carte (choisir un fond 2D quitte le mode 3D)
  document.querySelectorAll('[data-basemap]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      if (_mode3d) _exit3D(document.querySelector('[data-toggle-3d]'));
      _settings.tile = this.dataset.basemap;
      _applyTileStyle(_settings.tile);
      _saveSettings();
    });
  });

  // Vue 3D (fond GEE : imagerie Sentinel-2 + relief GLO-30)
  var btn3d = document.querySelector('[data-toggle-3d]');
  if (btn3d) btn3d.addEventListener('click', function () { _toggle3D(this); });

  // Plein écran
  var fsBtn = document.querySelector('[data-toggle-fullscreen]');
  if (fsBtn) fsBtn.addEventListener('click', function () {
    var area = document.querySelector('.main-area') || document.documentElement;
    if (!document.fullscreenElement) {
      (area.requestFullscreen || function () {}).call(area);
    } else if (document.exitFullscreen) {
      document.exitFullscreen();
    }
    setTimeout(function () {
      if (map) map.invalidateSize();
      if (_map3d) _map3d.resize();
    }, 350);
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
    // v2 : clé changée pour réappliquer le fond par défaut « Clair »
    // (Voyager) aux utilisateurs dont l'ancien réglage pointait ailleurs.
    var raw = localStorage.getItem('gd-settings-v3');
    if (raw) _settings = Object.assign({}, GD_SETTINGS_DEFAULT, JSON.parse(raw));
  } catch (e) { _settings = Object.assign({}, GD_SETTINGS_DEFAULT); }
  // Fusion fine des couches : les clés absentes reprennent leur défaut.
  _settings.layers = Object.assign({}, GD_SETTINGS_DEFAULT.layers, _settings.layers || {});
  var savedTheme = null;
  try { savedTheme = localStorage.getItem('gd-theme'); } catch (e) {}
  if (savedTheme) _settings.theme = savedTheme;
}

function _saveSettings() {
  try { localStorage.setItem('gd-settings-v3', JSON.stringify(_settings)); } catch (e) {}
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
  _setRadio('viewModeBtns', _mode3d ? '3d' : '2d');
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
  // Couches d'analyse : reflète l'état courant.
  Object.keys(LAYER_TOGGLES).forEach(function (name) {
    var cb = document.getElementById('layer-' + name);
    if (cb) cb.checked = !!(_settings.layers && _settings.layers[name]);
  });
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
  _settings.layers = Object.assign({}, GD_SETTINGS_DEFAULT.layers);
  _saveSettings();
  _populateSettingsUI();
  _applyTheme(_settings.theme);
  _applyTileStyle(_settings.tile);
  _applyLegendVisibility();
  _syncLayers();
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

  // Vue 2D / 3D (Paramètres → Carte) : application IMMÉDIATE, même
  // mécanique que le bouton « 3D » de la carte (état partagé _mode3d).
  document.querySelectorAll('.viewModeBtns .sd-radio-btn').forEach(function (b) {
    b.addEventListener('click', function () {
      var want3d = this.dataset.val === '3d';
      if (want3d !== _mode3d) _toggle3D(document.querySelector('[data-toggle-3d]'));
    });
  });

  // Couches d'analyse : application IMMÉDIATE (pas besoin d'Enregistrer)
  // + persistance + mise à jour de la légende.
  Object.keys(LAYER_TOGGLES).forEach(function (name) {
    var cb = document.getElementById('layer-' + name);
    if (!cb) return;
    cb.addEventListener('change', function () {
      _settings.layers[name] = this.checked;
      _saveSettings();
      LAYER_TOGGLES[name]();   // inverse l'état effectif (aligné avant le clic)
      _updateLegend();
    });
  });

  var save = document.getElementById('settingsSave');
  if (save) save.addEventListener('click', saveSettings);
  var reset = document.getElementById('settingsReset');
  if (reset) reset.addEventListener('click', resetSettings);
}

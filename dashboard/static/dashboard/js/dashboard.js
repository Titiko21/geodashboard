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
  // (URL de tuiles obtenue via /api/gee/basemap/), géré à part dans
  // _applyTileStyle.
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
    _fetchGeeBasemap()
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
  contours: function () { _toggleContours(); },
  relief:   function () { _toggleRelief(null); },
};

/* État effectif d'une couche sur la carte. */
function _layerActive(name) {
  if (name === 'events') return !!lFloodEvents;
  if (name === 'heat') return !!lFloodHeat;
  if (name === 'relief') return !!lHillshade;
  if (name === 'contours') return _contoursOn;
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
  ndvi: { url: '/api/gee/ndvi/',  opacity: 0.62, empty: 'NDVI indisponible sur cette emprise' },
  sar:  { url: '/api/gee/flood/', opacity: 0.70, empty: 'Pas de détection SAR récente' },
  // (les courbes de niveau sont vectorielles — logique dédiée plus bas)
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


/* ══════ Courbes de niveau vectorielles (cotes altimétriques) ══

   Standard topographique : isolignes fines tous les `interval` m,
   MAÎTRESSES épaissies portant leur COTE (altitude / niveau mer).
   Recalculées sur l'emprise visible à chaque déplacement (comme
   toute carte topo, c'est un détail local) ; équidistance adaptée
   au zoom : 20 m (vue large) → 10 m → 5 m (vue rapprochée). */

let lContours = null;          // layerGroup : lignes + étiquettes de cote
let _contoursOn = false;
let _contoursSeq = 0;          // anti-course entre déplacements successifs
let _contoursMoveBound = false;
let _contoursHintShown = false;

const CONTOUR_MIN_ZOOM = 11;

function _contourInterval(zoom) {
  if (zoom >= 15) return 5;
  if (zoom >= 13) return 10;
  return 20;
}

function _clearContours() {
  if (lContours && map) { map.removeLayer(lContours); }
  lContours = null;
}

function _toggleContours() {
  if (!map) return;
  _contoursOn = !_contoursOn;
  if (!_contoursOn) { _clearContours(); return; }
  if (!_contoursMoveBound) {
    _contoursMoveBound = true;
    map.on('moveend', function () { if (_contoursOn) _refreshContours(); });
  }
  _refreshContours();
}

function _refreshContours() {
  if (!map || !_contoursOn) return;
  var zoom = map.getZoom();
  if (zoom < CONTOUR_MIN_ZOOM) {
    _clearContours();
    if (!_contoursHintShown) {
      _contoursHintShown = true;
      toast('Zoomez pour afficher les courbes de niveau', 'warn');
    }
    return;
  }
  var seq = ++_contoursSeq;
  var b = map.getBounds();
  var bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]
    .map(function (v) { return v.toFixed(4); }).join(',');
  _fetchJSON('/api/gee/contours/?bbox=' + bbox + '&interval=' + _contourInterval(zoom))
    .then(function (d) {
      if (seq !== _contoursSeq || !_contoursOn) return;   // vue déjà changée
      if (!d || d.no_data || !d.features) {
        toast('Courbes de niveau indisponibles ici', 'warn');
        return;
      }
      _renderContours(d);
    })
    .catch(function (err) {
      if (err === 'session') return;
      console.warn('[Contours] échec :', err);
      toast('Courbes de niveau indisponibles', 'err');
    });
}

function _renderContours(fc) {
  _clearContours();
  var iv = fc.interval || 10;
  var legendIv = document.getElementById('legendContourIv');
  if (legendIv) {
    legendIv.textContent = 'équidistance ' + iv + ' m · cote sur les maîtresses';
  }
  var labels = [];
  var lines = L.geoJSON(fc, {
    interactive: false,
    style: function (f) {
      var major = f.properties && f.properties.major;
      return {
        color:   major ? '#5c4030' : '#a5825f',
        weight:  major ? 1.8 : 1.0,
        opacity: major ? 0.9 : 0.65,
      };
    },
    onEachFeature: function (f, layer) {
      // Cote altimétrique posée au milieu de chaque courbe MAÎTRESSE —
      // lisible sans surcharger (les fines s'interpolent visuellement).
      if (!f.properties || !f.properties.major) return;
      var coords = f.geometry && f.geometry.coordinates;
      if (!coords || coords.length < 6 || labels.length >= 60) return;
      var mid = coords[Math.floor(coords.length / 2)];
      labels.push(L.marker([mid[1], mid[0]], {
        interactive: false,
        keyboard: false,
        icon: L.divIcon({
          className: 'contour-label',
          html: '<span>' + f.properties.elev + ' m</span>',
          iconSize: null,
        }),
      }));
    },
  });
  lContours = L.layerGroup([lines].concat(labels)).addTo(map);
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


/* ══════ Inventaire des routes (filtres par type de surface) ══

   Le panneau « Routes » liste les types de surface RÉELLEMENT présents
   en base (via /api/roads/inventory/) avec compteurs et kilométrages.
   Chaque type coché s'affiche sur la carte dans sa couleur. */

const ROAD_COLORS = {
  bitume:  '#334155',
  pave:    '#7c3aed',
  gravier: '#d97706',
  terre:   '#92400e',
  autre:   '#64748b',
};

let lRoads = null;
let _roadsSeq = 0;

function _esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function loadRoadInventory() {
  var list = document.getElementById('roadsInvList');
  if (!list) return;
  _fetchJSON('/api/roads/inventory/' + (window.location.search || ''))
    .then(function (d) {
      var scope = document.getElementById('roadsInvScope');
      var total = document.getElementById('roadsInvTotal');
      var empty = document.getElementById('roadsInvEmpty');
      var hint  = document.getElementById('roadsInvHint');
      if (scope) scope.textContent = d.scope || 'Réseau';
      if (total) total.textContent = (d.total_km || 0) + ' km';
      if (!d.total_count) {
        if (empty) empty.style.display = '';
        return;
      }
      if (hint) hint.style.display = '';
      list.innerHTML = '';
      d.types.forEach(function (t) {
        if (!t.count) return;   // seuls les types présents en base
        var color = ROAD_COLORS[t.code] || ROAD_COLORS.autre;
        var row = document.createElement('label');
        row.style.cssText = 'display:flex;align-items:center;gap:8px;' +
          'padding:7px 0;border-bottom:1px dashed var(--line-2);' +
          'font-size:12px;cursor:pointer';
        var cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.dataset.roadType = t.code;
        cb.style.accentColor = color;
        cb.addEventListener('change', _refreshRoadsLayer);
        var swatch = document.createElement('span');
        swatch.style.cssText = 'width:14px;height:4px;border-radius:2px;' +
          'flex:none;background:' + color;
        var name = document.createElement('span');
        name.style.cssText = 'flex:1;min-width:0';
        name.textContent = t.label;
        var stats = document.createElement('span');
        stats.style.color = 'var(--ink-3)';
        stats.innerHTML = '<b style="color:var(--ink)">' + t.count + '</b> seg · ' + t.km + ' km';
        row.append(cb, swatch, name, stats);
        list.appendChild(row);
      });
    })
    .catch(function (err) {
      if (err === 'session') return;
      var scope = document.getElementById('roadsInvScope');
      if (scope) scope.textContent = 'Inventaire indisponible';
      console.warn('[Routes] inventaire échoué :', err);
    });
}

function _refreshRoadsLayer() {
  if (!map) return;
  var checked = Array.prototype.map.call(
    document.querySelectorAll('[data-road-type]:checked'),
    function (cb) { return cb.dataset.roadType; }
  );
  var legend = document.querySelector('[data-legend-roads]');
  if (!checked.length) {
    if (lRoads) { map.removeLayer(lRoads); lRoads = null; }
    if (legend) legend.style.display = 'none';
    return;
  }
  var seq = ++_roadsSeq;
  var qs = new URLSearchParams(window.location.search);
  qs.set('surface', checked.join(','));
  _fetchJSON('/api/roads/export/?' + qs.toString())
    .then(function (d) {
      if (seq !== _roadsSeq) return;   // sélection déjà modifiée
      if (lRoads) { map.removeLayer(lRoads); lRoads = null; }
      if (!d || !d.features || !d.features.length) {
        toast('Aucune route pour cette sélection', 'warn');
        if (legend) legend.style.display = 'none';
        return;
      }
      lRoads = L.geoJSON(d, {
        style: function (f) {
          var p = f.properties || {};
          return {
            color:   ROAD_COLORS[p.surface_type] || ROAD_COLORS.autre,
            weight:  p.is_strategic ? 3 : 1.8,
            opacity: 0.85,
          };
        },
        onEachFeature: function (f, layer) {
          var p = f.properties || {};
          layer.bindPopup(
            '<b>' + _esc(p.name || 'Route') + '</b>'
            + '<br>Surface : ' + _esc(p.surface_label || p.surface_type || '—')
            + '<br>État : ' + _esc(p.status_label || '—')
            + (p.condition_score != null
                ? ' (' + Math.round(p.condition_score) + '/100)' : '')
          );
        },
      }).addTo(map);
      if (legend) legend.style.display = '';
    })
    .catch(function (err) {
      if (err === 'session') return;
      console.warn('[Routes] couche échouée :', err);
      toast('Routes indisponibles', 'err');
    });
}


/* ══════ Fond GEE (imagerie Sentinel-2 servie par le backend) ═

   L'URL des tuiles vient de /api/gee/basemap/ (composite vraie
   couleur des 12 derniers mois). Un seul fetch par session. */

let _geeBasemapPromise = null;

function _fetchGeeBasemap() {
  if (!_geeBasemapPromise) {
    _geeBasemapPromise = _fetchJSON('/api/gee/basemap/').then(function (d) {
      if (!d || d.no_data || !d.imagery_tiles_url) {
        throw new Error('gee-off');
      }
      return d;
    }).catch(function (err) {
      _geeBasemapPromise = null;   // réessayable
      throw err;
    });
  }
  return _geeBasemapPromise;
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

"""
_contours.py — extraction d'isolignes (courbes de niveau) depuis une grille
d'altitudes, par « marching squares » avec interpolation linéaire.

Logique PURE (aucune dépendance GEE/Django/numpy) : prend une grille 2D
(listes de listes, ligne 0 = nord) et une emprise géographique, renvoie des
polylignes GeoJSON étiquetées de leur cote altimétrique exacte.

Pourquoi pas les tuiles raster GEE d'avant ? Impossible d'y écrire les cotes,
et le bruit du MNT 30 m donnait des « vers » illisibles. Ici : lignes
vectorielles propres (MNT lissé 3×3), chaque courbe SAIT son altitude →
le frontend peut l'afficher, conformément aux standards topographiques.
"""


def smooth_grid(grid):
    """Lissage moyenne 3×3 — gomme le bruit du MNT sans déplacer les formes."""
    rows, cols = len(grid), len(grid[0])
    out = [[0.0] * cols for _ in range(rows)]
    for r in range(rows):
        r0, r1 = max(0, r - 1), min(rows, r + 2)
        for c in range(cols):
            c0, c1 = max(0, c - 1), min(cols, c + 2)
            acc = n = 0
            for rr in range(r0, r1):
                row = grid[rr]
                for cc in range(c0, c1):
                    acc += row[cc]
                    n += 1
            out[r][c] = acc / n
    return out


def _levels_for(grid, interval, max_levels=40):
    """Niveaux à tracer. Si trop nombreux (relief marqué), l'intervalle est
    doublé jusqu'à tenir dans max_levels — la carte reste lisible."""
    lo = min(min(row) for row in grid)
    hi = max(max(row) for row in grid)
    iv = float(interval)
    while (hi - lo) / iv > max_levels:
        iv *= 2
    first = int(lo // iv) * iv + iv
    levels = []
    lv = first
    while lv < hi:
        levels.append(round(lv, 6))
        lv += iv
    return levels, iv


# Cas du marching squares : pour chaque configuration des 4 coins
# (TL,TR,BR,BL au-dessus/au-dessous du niveau), les paires d'arêtes que la
# courbe traverse. Arêtes : 0=haut (TL-TR), 1=droite (TR-BR),
# 2=bas (BL-BR), 3=gauche (TL-BL). Cas 5/10 ambigus → résolus via le centre.
_CASES = {
    1:  [(3, 2)], 2:  [(2, 1)], 3:  [(3, 1)], 4:  [(0, 1)],
    6:  [(0, 2)], 7:  [(3, 0)], 8:  [(3, 0)], 9:  [(0, 2)],
    11: [(0, 1)], 12: [(3, 1)], 13: [(2, 1)], 14: [(3, 2)],
}


def _edge_point(edge, r, c, v_tl, v_tr, v_br, v_bl, level):
    """Point (x, y) en coordonnées de grille où le niveau coupe l'arête."""
    if edge == 0:    # haut : TL → TR
        t = (level - v_tl) / (v_tr - v_tl)
        return (c + t, float(r))
    if edge == 1:    # droite : TR → BR
        t = (level - v_tr) / (v_br - v_tr)
        return (c + 1.0, r + t)
    if edge == 2:    # bas : BL → BR
        t = (level - v_bl) / (v_br - v_bl)
        return (c + t, r + 1.0)
    # gauche : TL → BL
    t = (level - v_tl) / (v_bl - v_tl)
    return (float(c), r + t)


def _cell_segments(r, c, v_tl, v_tr, v_br, v_bl, level):
    """Segments (0, 1 ou 2) traversant la cellule pour ce niveau."""
    idx = ((v_tl > level) << 3) | ((v_tr > level) << 2) \
        | ((v_br > level) << 1) | (v_bl > level)
    if idx in (0, 15):
        return []
    if idx in (5, 10):
        center_above = (v_tl + v_tr + v_br + v_bl) / 4.0 > level
        if idx == 5:
            pairs = [(3, 0), (2, 1)] if center_above else [(3, 2), (0, 1)]
        else:
            pairs = [(0, 1), (3, 2)] if center_above else [(3, 0), (2, 1)]
    else:
        pairs = _CASES[idx]
    return [
        (_edge_point(a, r, c, v_tl, v_tr, v_br, v_bl, level),
         _edge_point(b, r, c, v_tl, v_tr, v_br, v_bl, level))
        for a, b in pairs
    ]


def _chain(segments):
    """Assemble des segments épars en polylignes continues."""
    def key(pt):
        return (round(pt[0] * 1e4), round(pt[1] * 1e4))

    adj = {}
    for i, (a, b) in enumerate(segments):
        adj.setdefault(key(a), []).append((i, 0))
        adj.setdefault(key(b), []).append((i, 1))

    used = [False] * len(segments)
    lines = []
    for start in range(len(segments)):
        if used[start]:
            continue
        used[start] = True
        a, b = segments[start]
        line = [a, b]
        # Étend chaque extrémité tant qu'un segment libre s'y raccorde.
        for endpos in (True, False):          # True = queue, False = tête
            while True:
                tip = line[-1] if endpos else line[0]
                nxt = next(((i, e) for i, e in adj.get(key(tip), [])
                            if not used[i]), None)
                if nxt is None:
                    break
                i, e = nxt
                used[i] = True
                pt = segments[i][1 - e]       # l'autre extrémité du segment
                if endpos:
                    line.append(pt)
                else:
                    line.insert(0, pt)
        lines.append(line)
    return lines


def contour_lines(grid, bbox, interval, major_every=5):
    """
    Extrait les courbes de niveau d'une grille d'altitudes.

    grid   : liste de listes (ligne 0 = bord NORD de l'emprise)
    bbox   : {"west", "south", "east", "north"} en degrés
    interval : équidistance souhaitée en mètres (doublée si relief trop dense)
    major_every : une courbe maîtresse toutes les N courbes

    Renvoie (features, interval_effectif) — features = liste de Features
    GeoJSON LineString avec propriétés {"elev": m, "major": bool}.
    """
    grid = smooth_grid(grid)
    rows, cols = len(grid), len(grid[0])
    if rows < 2 or cols < 2:
        return [], interval

    levels, iv = _levels_for(grid, interval)
    major_iv = iv * major_every
    dx = (bbox["east"] - bbox["west"]) / (cols - 1)
    dy = (bbox["north"] - bbox["south"]) / (rows - 1)

    def to_lonlat(pt):
        return (round(bbox["west"] + pt[0] * dx, 6),
                round(bbox["north"] - pt[1] * dy, 6))

    features = []
    for level in levels:
        segments = []
        for r in range(rows - 1):
            row0, row1 = grid[r], grid[r + 1]
            for c in range(cols - 1):
                v_tl, v_tr = row0[c], row0[c + 1]
                v_bl, v_br = row1[c], row1[c + 1]
                lo = min(v_tl, v_tr, v_bl, v_br)
                hi = max(v_tl, v_tr, v_bl, v_br)
                if lo < level < hi:
                    segments.extend(
                        _cell_segments(r, c, v_tl, v_tr, v_br, v_bl, level))
        is_major = round(level % major_iv, 6) in (0.0, major_iv)
        for line in _chain(segments):
            # Les brins minuscules sont du bruit résiduel — on ne garde que
            # les courbes assez longues pour être lues (maîtresses : plus
            # permissif, elles portent la cote).
            if len(line) < (3 if is_major else 5):
                continue
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [to_lonlat(p) for p in line],
                },
                "properties": {"elev": int(round(level)), "major": is_major},
            })
    return features, iv

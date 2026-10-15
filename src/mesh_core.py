# ----------------------------------------------------------------------
# Mesh section core: pure numpy, no FreeCAD (tests/test_mesh_core.py runs it alone)
# ----------------------------------------------------------------------
# BEGIN MESH CORE
MESH_MAX_QUAD_POINTS = 8000     # finished view: convex-quad fill up to this many loop points
MESH_MAX_HOLES = 400            # more holes per outer loop: no keyhole bridging (fill skipped)
def mesh_weld(V, T, sid, tol):
    """Merge coincident vertices of a tessellation.

    OpenCascade duplicates the vertices along face borders; welding them makes the mesh
    edges shared, so the section contour can be chained by edge id. Points are clustered
    by gaps along x, then y, then z (no grid, so nothing is split at a cell border).
    sid: solid id per vertex, so touching solids stay separate.
    Returns (V, T) with degenerate triangles removed.
    """
    n = len(V)
    if n == 0:
        return V, T
    gid = np.asarray(sid, dtype=np.int64)
    for ax in range(3):
        order = np.lexsort((V[:, ax], gid))
        g, x = gid[order], V[order, ax]
        brk = np.ones(n, dtype=bool)
        brk[1:] = (g[1:] != g[:-1]) | (x[1:] - x[:-1] > tol)
        lab = np.empty(n, dtype=np.int64)
        lab[order] = np.cumsum(brk) - 1
        gid = lab
    k = int(gid.max()) + 1
    cnt = np.bincount(gid, minlength=k).astype(float)
    Vw = np.stack([np.bincount(gid, weights=V[:, a], minlength=k) / cnt for a in range(3)], axis=1)
    Tw = gid[T]
    ok = (Tw[:, 0] != Tw[:, 1]) & (Tw[:, 1] != Tw[:, 2]) & (Tw[:, 0] != Tw[:, 2])
    return Vw, Tw[ok]


def _join_chains(chains, gap):
    """Join open polylines whose ends meet within `gap` (cracks in the mesh)."""
    pool = [np.asarray(c, dtype=float) for c in chains]
    closed, rest = [], []
    if len(pool) > 400:
        return closed, pool
    while pool:
        cur = pool.pop()
        while True:
            if len(cur) >= 3 and np.linalg.norm(cur[0] - cur[-1]) <= gap:
                closed.append(cur)
                cur = None
                break
            joined = False
            for i, c in enumerate(pool):
                if np.linalg.norm(cur[-1] - c[0]) <= gap:
                    cur = np.concatenate((cur, c))
                elif np.linalg.norm(cur[-1] - c[-1]) <= gap:
                    cur = np.concatenate((cur, c[::-1]))
                elif np.linalg.norm(cur[0] - c[-1]) <= gap:
                    cur = np.concatenate((c, cur))
                elif np.linalg.norm(cur[0] - c[0]) <= gap:
                    cur = np.concatenate((c[::-1], cur))
                else:
                    continue
                pool.pop(i)
                joined = True
                break
            if not joined:
                break
        if cur is not None:
            rest.append(cur)
    return closed, rest


def mesh_section_loops(V, T, n, d, gap=0.0):
    """Section of a welded mesh with the plane n.x = d.

    Every triangle the plane crosses gives one segment; its two cut points are keyed by the
    mesh edge they lie on, so the segments chain into loops without any distance search.
    Vertices exactly on the plane count as lying on the positive side.
    Returns (closed, open): lists of (m, 3) arrays (closed loops do not repeat the first point).
    """
    if len(T) == 0:
        return [], []
    n = np.asarray(n, dtype=float)
    sv = V @ n - d
    pos = sv[T] >= 0.0
    cnt = pos.sum(axis=1)
    cut = (cnt == 1) | (cnt == 2)
    if not cut.any():
        return [], []
    P, sp, one = T[cut], pos[cut], cnt[cut] == 1
    lone = np.where(one, sp.argmax(axis=1), (~sp).argmax(axis=1))
    r = np.arange(len(P))
    ia, ib, ic = P[r, lone], P[r, (lone + 1) % 3], P[r, (lone + 2) % 3]
    sa = sv[ia]

    def cross(io):
        f = (sa / (sa - sv[io]))[:, None]
        return V[ia] + f * (V[io] - V[ia])

    nv = len(V)

    def ekey(p, q):
        return np.minimum(p, q).astype(np.int64) * nv + np.maximum(p, q)

    keys = np.concatenate((ekey(ia, ib), ekey(ia, ic)))
    pts_all = np.concatenate((cross(ib), cross(ic)))
    uk, first, inv = np.unique(keys, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    pts = pts_all[first]
    k = len(P)
    a, b = inv[:k], inv[k:]

    m = len(uk)
    src = np.concatenate((a, b))
    dst = np.concatenate((b, a))
    dst_s = dst[np.argsort(src, kind="stable")].tolist()
    deg = np.bincount(src, minlength=m)
    start = (np.cumsum(deg) - deg).tolist()
    deg_l = deg.tolist()
    seen = [False] * m

    def walk(s):
        chain = [s]
        seen[s] = True
        cur = s
        while True:
            nxt = -1
            for j in range(start[cur], start[cur] + deg_l[cur]):
                c = dst_s[j]
                if not seen[c]:
                    nxt = c
                    break
            if nxt < 0:
                return chain
            seen[nxt] = True
            chain.append(nxt)
            cur = nxt

    closed, opened = [], []
    for s in np.flatnonzero(deg == 1).tolist():       # open ends first
        if not seen[s]:
            opened.append(walk(s))
    for s in range(m):
        if not seen[s]:
            chain = walk(s)
            last = chain[-1]
            if len(chain) >= 3 and s in dst_s[start[last]:start[last] + deg_l[last]]:
                closed.append(chain)
            else:
                opened.append(chain)

    closed = [pts[c] for c in closed]
    opened = [pts[c] for c in opened]
    if opened:
        more, opened = _join_chains(opened, gap)
        closed += more
    return closed, opened


def _signed_area(P):
    x, y = P[:, 0], P[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _orient(P, ccw):
    return P if (_signed_area(P) > 0) == ccw else P[::-1]


def _point_in_loop(p, L):
    """Even-odd test of the 2D point p against the closed loop L."""
    x0, y0 = L[:, 0], L[:, 1]
    x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
    c = (y0 > p[1]) != (y1 > p[1])
    if not c.any():
        return False
    xi = x0[c] + (p[1] - y0[c]) * (x1[c] - x0[c]) / (y1[c] - y0[c])
    return bool(int((xi > p[0]).sum()) & 1)


def loop_nesting(loops):
    """Nesting depth and immediate parent of every 2D loop (-1 = none).

    Even depth = outer boundary, odd depth = hole (islands inside holes are outer again).
    """
    n = len(loops)
    depth, parent = [0] * n, [-1] * n
    if n < 2:
        return depth, parent
    lo = np.array([L.min(axis=0) for L in loops])
    hi = np.array([L.max(axis=0) for L in loops])
    area = np.array([abs(_signed_area(L)) for L in loops])
    for i, L in enumerate(loops):
        p = 0.5 * (L[0] + L[1])
        near = np.flatnonzero((lo[:, 0] <= p[0]) & (hi[:, 0] >= p[0]) &
                              (lo[:, 1] <= p[1]) & (hi[:, 1] >= p[1]) & (area > area[i]))
        best = -1
        for j in near.tolist():
            if _point_in_loop(p, loops[j]):
                depth[i] += 1
                if best < 0 or area[j] < area[best]:
                    best = j
        parent[i] = best
    return depth, parent


def _crosses(M, Q, E0, E1):
    """True if the segment M-Q properly crosses any of the segments E0[i]-E1[i]."""
    r = Q - M
    d0 = r[0] * (E0[:, 1] - M[1]) - r[1] * (E0[:, 0] - M[0])
    d1 = r[0] * (E1[:, 1] - M[1]) - r[1] * (E1[:, 0] - M[0])
    e = E1 - E0
    d2 = e[:, 0] * (M[1] - E0[:, 1]) - e[:, 1] * (M[0] - E0[:, 0])
    d3 = e[:, 0] * (Q[1] - E0[:, 1]) - e[:, 1] * (Q[0] - E0[:, 0])
    return bool(np.any((d0 * d1 < 0) & (d2 * d3 < 0)))


def _bridge_holes(outer, holes):
    """One weakly simple polygon from a counter-clockwise outer loop and clockwise holes.

    Keyhole bridging: each hole (right-most first) is joined to the nearest polygon vertex
    its right-most point can see; the bridge is walked twice, so the area is unchanged.
    """
    poly = outer
    todo = sorted(holes, key=lambda h: -h[:, 0].max())
    for k, h in enumerate(todo):
        mi = int(np.argmax(h[:, 0]))
        M = h[mi]
        segs = [poly, h] + todo[k + 1:]
        E0 = np.concatenate(segs)
        E1 = np.concatenate([np.roll(p, -1, axis=0) for p in segs])
        order = np.argsort(((poly - M) ** 2).sum(axis=1))
        qi = int(order[0])
        for cand in order[:64].tolist():
            if not _crosses(M, poly[cand], E0, E1):
                qi = cand
                break
        poly = np.concatenate((poly[:qi + 1], h[mi:], h[:mi + 1], poly[qi:]))
    return poly


def fill_polygons(loops):
    """Fillable polygons (outer boundary with its holes bridged in) from 2D closed loops."""
    if not loops:
        return []
    depth, parent = loop_nesting(loops)
    holes_of = {}
    for i, dp in enumerate(depth):
        if dp & 1:
            holes_of.setdefault(parent[i], []).append(i)
    out = []
    for i, L in enumerate(loops):
        if depth[i] & 1:
            continue
        outer = _orient(L, True)
        hs = [_orient(loops[h], False) for h in holes_of.get(i, [])]
        if len(hs) > MESH_MAX_HOLES:
            continue                                  # too many holes to bridge quickly
        out.append(_bridge_holes(outer, hs) if hs else outer)
    return out


def fill_trapezoids(loops):
    """Even-odd fill of 2D closed loops as convex quads, (m, 4, 2).

    Horizontal slabs between consecutive vertex heights; inside a slab the edges cut it into
    trapezoids (pairs of neighbouring edges). Convex pieces need no polygon tessellator, so
    sections with many holes cannot tear.
    """
    P = [np.asarray(L, dtype=float) for L in loops if len(L) >= 3]
    if not P:
        return np.zeros((0, 4, 2))
    A = np.concatenate(P)
    B = np.concatenate([np.roll(L, -1, axis=0) for L in P])
    flip = A[:, 1] > B[:, 1]
    lo = np.where(flip[:, None], B, A)
    hi = np.where(flip[:, None], A, B)
    keep = hi[:, 1] > lo[:, 1]
    lo, hi = lo[keep], hi[keep]
    if not len(lo):
        return np.zeros((0, 4, 2))
    x0, y0, x1, y1 = lo[:, 0], lo[:, 1], hi[:, 0], hi[:, 1]
    slope = (x1 - x0) / (y1 - y0)
    ys = np.unique(np.concatenate((y0, y1)))
    sa, sb = ys[:-1], ys[1:]
    out = []
    block = max(1, int(2e7 // len(x0)))                     # slabs per block (bounds the memory)
    for k0 in range(0, len(sa), block):
        a, b = sa[k0:k0 + block], sb[k0:k0 + block]
        si, ei = np.nonzero((y0[None, :] <= a[:, None]) & (y1[None, :] >= b[:, None]))
        if not len(si):
            continue
        xa = x0[ei] + (a[si] - y0[ei]) * slope[ei]
        xb = x0[ei] + (b[si] - y0[ei]) * slope[ei]
        o = np.lexsort((xa + xb, si))                       # left to right inside every slab
        si, xa, xb = si[o], xa[o], xb[o]
        cnt = np.bincount(si, minlength=len(a))
        rank = np.arange(len(si)) - (np.cumsum(cnt) - cnt)[si]
        li = np.flatnonzero((rank % 2 == 0) & (rank + 1 < cnt[si]))
        if not len(li):
            continue
        ri = li + 1
        q = np.empty((len(li), 4, 2))
        q[:, 0, 0], q[:, 1, 0], q[:, 2, 0], q[:, 3, 0] = xa[li], xa[ri], xb[ri], xb[li]
        q[:, 0, 1] = q[:, 1, 1] = a[si[li]]
        q[:, 2, 1] = q[:, 3, 1] = b[si[li]]
        out.append(q)
    return np.concatenate(out) if out else np.zeros((0, 4, 2))


def hatch_segments(loops, angle_deg, spacing, max_lines=1500):
    """Scanline hatch of closed 2D loops with the even-odd rule: (k, 2, 2) segments.

    Lines are n.q = j * spacing, anchored at the origin (so the pattern does not move
    when the plane or the object does). Holes need no special handling.
    """
    empty = np.zeros((0, 2, 2))
    loops = [L for L in loops if len(L) >= 3]
    if not loops or spacing <= 0:
        return empty
    a = math.radians(angle_deg)
    nv = np.array((math.cos(a), math.sin(a)))
    wv = np.array((-math.sin(a), math.cos(a)))
    P0 = np.concatenate(loops)
    P1 = np.concatenate([np.roll(L, -1, axis=0) for L in loops])
    sl = [L @ nv for L in loops]
    s0 = np.concatenate(sl)
    s1 = np.concatenate([np.roll(s, -1) for s in sl])
    lo, hi = np.minimum(s0, s1), np.maximum(s0, s1)
    span = float(hi.max() - lo.min())
    if span / spacing > max_lines:
        spacing = span / max_lines
    j0 = np.ceil(lo / spacing).astype(np.int64)
    j1 = np.ceil(hi / spacing).astype(np.int64) - 1      # a segment covers [lo, hi)
    cnt = np.maximum(j1 - j0 + 1, 0)
    total = int(cnt.sum())
    if total == 0:
        return empty
    seg = np.repeat(np.arange(len(P0)), cnt)
    first = np.cumsum(cnt) - cnt
    j = j0[seg] + (np.arange(total) - first[seg])
    s = j * spacing
    t = (s - s0[seg]) / (s1[seg] - s0[seg])
    q = P0[seg] + t[:, None] * (P1[seg] - P0[seg])
    x = q @ wv
    order = np.lexsort((x, j))
    js, xs = j[order], x[order]
    new = np.ones(total, dtype=bool)
    new[1:] = js[1:] != js[:-1]
    gstart = np.flatnonzero(new)
    gid = np.cumsum(new) - 1
    gcnt = np.diff(np.concatenate((gstart, [total])))
    rank = np.arange(total) - gstart[gid]
    i0 = np.flatnonzero((rank % 2 == 0) & (rank + 1 < gcnt[gid]))
    xa, xb, sj = xs[i0], xs[i0 + 1], js[i0] * spacing
    keep = xb - xa > 1e-12
    xa, xb, sj = xa[keep], xb[keep], sj[keep]
    A = sj[:, None] * nv + xa[:, None] * wv
    B = sj[:, None] * nv + xb[:, None] * wv
    return np.stack((A, B), axis=1)
# END MESH CORE

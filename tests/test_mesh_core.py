"""Tests for the pure-numpy core of the mesh-based section (v0.5-experimental).

Run:  python3 tests/test_mesh_core.py

The macro cannot be imported outside FreeCAD, so the block between the
"BEGIN MESH CORE" / "END MESH CORE" markers is cut out of the macro and executed here.
"""
import math
import os
import re
import time
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MACRO = os.path.join(HERE, "..", "macro", "LiveSectionAnalyzer.FCMacro")

with open(MACRO, encoding="utf-8") as fh:
    SRC = fh.read()
CORE = re.search(r"# BEGIN MESH CORE\n(.*?)# END MESH CORE", SRC, re.S).group(1)
NS = {"np": np, "math": math}
exec(compile(CORE, "mesh_core", "exec"), NS)

mesh_weld = NS["mesh_weld"]
mesh_section_loops = NS["mesh_section_loops"]
mesh_plane_index = NS["mesh_plane_index"]
loop_nesting = NS["loop_nesting"]
fill_polygons = NS["fill_polygons"]
hatch_segments = NS["hatch_segments"]
fill_trapezoids = NS["fill_trapezoids"]
_signed_area = NS["_signed_area"]
_point_in_loop = NS["_point_in_loop"]
_join_chains = NS["_join_chains"]


# ----------------------------------------------------------------------
# Synthetic meshes
# ----------------------------------------------------------------------
def box_mesh(dx, dy, dz):
    """Box with duplicated vertices per face, like OpenCascade's tessellation (24 vertices)."""
    c = np.array([(x, y, z) for x in (0, dx) for y in (0, dy) for z in (0, dz)], dtype=float)
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    V, T = [], []
    for f in faces:
        o = len(V)
        V += [c[i] for i in f]
        T += [(o, o + 1, o + 2), (o, o + 2, o + 3)]
    return np.array(V), np.array(T, dtype=np.int64)


def ring(rad, z, n):
    a = np.arange(n) * 2 * math.pi / n
    return np.stack((rad * np.cos(a), rad * np.sin(a), np.full(n, z)), axis=1)


def tube_mesh(R, r, h, n):
    """Annulus extruded along z, shared vertices."""
    V = np.concatenate((ring(R, 0, n), ring(R, h, n), ring(r, 0, n), ring(r, h, n)))
    ob, ot, ib, it = (np.arange(n) + k * n for k in range(4))
    nx = (np.arange(n) + 1) % n
    T = []
    for a, b in ((ob, ot), (ib, it)):                              # outer and inner wall
        T += list(zip(a, a[nx], b[nx])) + list(zip(a, b[nx], b))
    for o, i in ((ob, ib), (ot, it)):                              # bottom and top ring
        T += list(zip(o, o[nx], i[nx])) + list(zip(o, i[nx], i))
    return V, np.array(T, dtype=np.int64)


def star_mesh(teeth, R, r, h):
    """Extruded star-shaped 'gear' polygon; returns (V, T, outline polygon)."""
    k = teeth * 4
    ang = np.arange(k) * 2 * math.pi / k
    rad = np.where((np.arange(k) % 4) < 2, R, r)
    P = np.stack((rad * np.cos(ang), rad * np.sin(ang)), axis=1)
    V = np.concatenate((np.c_[P, np.zeros(k)], np.c_[P, np.full(k, h)], [[0, 0, 0], [0, 0, h]]))
    cb, ct = 2 * k, 2 * k + 1
    nx = (np.arange(k) + 1) % k
    T = []
    for i in range(k):
        T += [(i, nx[i], k + nx[i]), (i, k + nx[i], k + i)]       # wall
        T += [(cb, i, nx[i]), (ct, k + i, k + nx[i])]             # caps
    return V, np.array(T, dtype=np.int64), P


def torus_mesh(R, r, n, m):
    u = np.arange(n) * 2 * math.pi / n
    v = np.arange(m) * 2 * math.pi / m
    U, Vv = np.meshgrid(u, v, indexing="ij")
    X = (R + r * np.cos(Vv)) * np.cos(U)
    Y = (R + r * np.cos(Vv)) * np.sin(U)
    Z = r * np.sin(Vv)
    V = np.stack((X, Y, Z), axis=-1).reshape(-1, 3)
    idx = np.arange(n * m).reshape(n, m)
    a, b = idx, np.roll(idx, -1, axis=0)
    c, d = np.roll(idx, -1, axis=1), np.roll(np.roll(idx, -1, axis=0), -1, axis=1)
    T = np.concatenate((np.stack((a, b, d), -1).reshape(-1, 3), np.stack((a, d, c), -1).reshape(-1, 3)))
    return V, T.astype(np.int64)


def welded(V, T, diag=None):
    if diag is None:
        diag = float(np.linalg.norm(V.max(axis=0) - V.min(axis=0)))
    return mesh_weld(V, T, np.zeros(len(V), dtype=np.int64), diag * 1e-6)


def uv(loops, drop):
    """Project 3D loops to 2D by dropping one axis."""
    keep = [a for a in range(3) if a != drop]
    return [L[:, keep] for L in loops]


def proper_self_crossings(P):
    """Number of proper crossings between non-adjacent edges of a polygon (brute force)."""
    n = len(P)
    cnt = 0

    def orient(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    for i in range(n):
        a, b = P[i], P[(i + 1) % n]
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            c, d = P[j], P[(j + 1) % n]
            if orient(a, b, c) * orient(a, b, d) < 0 and orient(c, d, a) * orient(c, d, b) < 0:
                cnt += 1
    return cnt


def circle(cx, cy, rad, n=16):
    a = np.arange(n) * 2 * math.pi / n
    return np.stack((cx + rad * np.cos(a), cy + rad * np.sin(a)), axis=1)


# ----------------------------------------------------------------------
class WeldTests(unittest.TestCase):
    def test_box_vertices_merge(self):
        V, T = box_mesh(10, 20, 30)
        self.assertEqual(len(V), 24)
        Vw, Tw = welded(V, T)
        self.assertEqual(len(Vw), 8)
        self.assertEqual(len(Tw), 12)

    def test_noise_does_not_split_vertices(self):
        V, T = box_mesh(10, 20, 30)
        rng = np.random.default_rng(1)
        Vn = V + rng.uniform(-2e-8, 2e-8, V.shape)
        Vw, _ = mesh_weld(Vn, T, np.zeros(len(Vn), dtype=np.int64), 1e-6)
        self.assertEqual(len(Vw), 8)

    def test_noise_on_a_cell_border_is_still_one_vertex(self):
        # a grid weld would split these two (they straddle a rounding border)
        tol = 1e-3
        a = np.array([[0.5 * tol - 1e-9, 0.0, 0.0], [0.5 * tol + 1e-9, 0.0, 0.0]])
        T = np.array([[0, 1, 1]])
        Vw, _ = mesh_weld(a, T, np.zeros(2, dtype=np.int64), tol)
        self.assertEqual(len(Vw), 1)

    def test_touching_solids_stay_separate(self):
        V = np.array([[0, 0, 0], [0, 0, 0.0]])
        T = np.array([[0, 1, 1]])
        Vw, _ = mesh_weld(V, T, np.array([0, 1]), 1e-6)
        self.assertEqual(len(Vw), 2)


class LoopTests(unittest.TestCase):
    def test_box_section_is_one_loop_with_exact_area(self):
        V, T = welded(*box_mesh(10, 20, 30))
        closed, opened = mesh_section_loops(V, T, (0, 0, 1), 12.5)
        self.assertEqual((len(closed), len(opened)), (1, 0))
        self.assertAlmostEqual(abs(_signed_area(closed[0][:, :2])), 200.0, places=9)
        self.assertTrue(np.allclose(closed[0][:, 2], 12.5))

    def test_plane_through_vertices(self):
        # plane exactly at the top face: the loop is the top rim
        V, T = welded(*box_mesh(10, 20, 30))
        closed, opened = mesh_section_loops(V, T, (0, 0, 1), 30.0)
        self.assertEqual((len(closed), len(opened)), (1, 0))
        self.assertAlmostEqual(abs(_signed_area(closed[0][:, :2])), 200.0, places=9)

    def test_plane_misses(self):
        V, T = welded(*box_mesh(10, 20, 30))
        self.assertEqual(mesh_section_loops(V, T, (0, 0, 1), 31.0), ([], []))

    def test_unwelded_mesh_has_no_shared_edges(self):
        # why welding matters: with duplicated vertices every segment is its own chain; only
        # the end-joining step (gap) puts the loop back together
        V, T = box_mesh(10, 20, 30)
        closed, opened = mesh_section_loops(V, T, (0, 0, 1), 12.5, gap=-1.0)   # joining off
        self.assertEqual(closed, [])
        self.assertEqual(len(opened), 4)               # one chain per side face
        closed, opened = mesh_section_loops(V, T, (0, 0, 1), 12.5)             # coincident ends join
        self.assertEqual((len(closed), len(opened)), (1, 0))

    def test_crack_is_closed_by_gap(self):
        V, T = box_mesh(10, 20, 30)
        V = V.copy()
        V[0] += (1e-5, 0, 0)              # one face copy of a corner drifts by 1e-5
        Vw, Tw = mesh_weld(V, T, np.zeros(len(V), dtype=np.int64), 1e-7)
        closed, opened = mesh_section_loops(Vw, Tw, (0, 0, 1), 12.5, gap=1e-3)
        self.assertEqual((len(closed), len(opened)), (1, 0))

    def test_tube_z_section_is_two_nested_loops(self):
        V, T = welded(*tube_mesh(20, 12, 10, 48))
        closed, opened = mesh_section_loops(V, T, (0, 0, 1), 5.0)
        self.assertEqual((len(closed), len(opened)), (2, 0))
        loops = uv(closed, 2)
        depth, parent = loop_nesting(loops)
        self.assertEqual(sorted(depth), [0, 1])
        outer = loops[depth.index(0)]
        self.assertEqual(parent[depth.index(1)], depth.index(0))
        a_out = 0.5 * 48 * 20 ** 2 * math.sin(2 * math.pi / 48)
        a_in = 0.5 * 48 * 12 ** 2 * math.sin(2 * math.pi / 48)
        self.assertAlmostEqual(abs(_signed_area(outer)), a_out, places=6)
        polys = fill_polygons(loops)
        self.assertEqual(len(polys), 1)
        self.assertAlmostEqual(abs(_signed_area(polys[0])), a_out - a_in, places=6)
        self.assertEqual(proper_self_crossings(polys[0]), 0)

    def test_tube_x_section_is_two_rectangles(self):
        V, T = welded(*tube_mesh(20, 12, 10, 48))
        closed, opened = mesh_section_loops(V, T, (1, 0, 0), 0.0)
        self.assertEqual((len(closed), len(opened)), (2, 0))
        depth, _ = loop_nesting(uv(closed, 0))
        self.assertEqual(depth, [0, 0])
        self.assertEqual(len(fill_polygons(uv(closed, 0))), 2)

    def test_torus_midplane_has_a_hole_and_an_offset_cut_has_two_circles(self):
        V, T = welded(*torus_mesh(30, 8, 64, 32))
        closed, opened = mesh_section_loops(V, T, (0, 0, 1), 0.0)     # exactly through vertices
        self.assertEqual((len(closed), len(opened)), (2, 0))
        depth, _ = loop_nesting(uv(closed, 2))
        self.assertEqual(sorted(depth), [0, 1])
        closed, opened = mesh_section_loops(V, T, (1, 0, 0), 0.0)
        self.assertEqual((len(closed), len(opened)), (2, 0))
        self.assertEqual(loop_nesting(uv(closed, 0))[0], [0, 0])

    def test_gear_section_area_matches_polygon(self):
        V, T, P = star_mesh(40, 30.0, 26.0, 10.0)
        V, T = welded(V, T)
        closed, opened = mesh_section_loops(V, T, (0, 0, 1), 4.0)
        self.assertEqual((len(closed), len(opened)), (1, 0))
        self.assertAlmostEqual(abs(_signed_area(closed[0][:, :2])), abs(_signed_area(P)), places=6)


class HatchTests(unittest.TestCase):
    def test_square_with_hole_exact_length(self):
        outer = np.array([[0, 0], [10, 0], [10, 10], [0, 10.0]])
        hole = np.array([[3, 3], [7, 3], [7, 7], [3, 7.0]])
        segs = hatch_segments([outer, hole], 0.0, 1.0)
        length = np.linalg.norm(segs[:, 1] - segs[:, 0], axis=1).sum()
        self.assertAlmostEqual(length, 84.0, places=9)          # area 84, spacing 1

    def test_length_is_area_over_spacing_at_any_angle(self):
        V, T, P = star_mesh(40, 30.0, 26.0, 10.0)
        for ang in (45.0, 135.0, 0.0, 90.0, 17.0):
            segs = hatch_segments([P], ang, 0.4)
            length = np.linalg.norm(segs[:, 1] - segs[:, 0], axis=1).sum()
            self.assertAlmostEqual(length / (abs(_signed_area(P)) / 0.4), 1.0, delta=0.01, msg=ang)

    def test_segments_stay_inside_the_section(self):
        outer = circle(0, 0, 20, 64)
        hole = circle(3, 2, 8, 32)
        segs = hatch_segments([outer, hole], 45.0, 1.5)
        mid = segs.mean(axis=1)
        for p in mid[::7]:
            self.assertTrue(_point_in_loop(p, outer))
            self.assertFalse(_point_in_loop(p, hole))

    def test_pattern_is_anchored_at_the_origin(self):
        sq = np.array([[0, 0], [10, 0], [10, 10], [0, 10.0]])
        a = hatch_segments([sq], 45.0, 2.0)
        b = hatch_segments([sq + (4.0, 0.0)], 45.0, 2.0)
        # shifting the shape by 4 (not a multiple of the spacing along the normal) must not
        # shift the lines: they stay on n.q = j * spacing
        nv = np.array((math.cos(math.pi / 4), math.sin(math.pi / 4)))
        for segs in (a, b):
            s = segs[:, 0] @ nv / 2.0
            self.assertTrue(np.allclose(s, np.round(s)))

    def test_max_lines_limits_the_count(self):
        sq = np.array([[0, 0], [100, 0], [100, 100], [0, 100.0]])
        self.assertLessEqual(len(hatch_segments([sq], 0.0, 0.01, max_lines=50)), 51)

    def test_empty(self):
        self.assertEqual(len(hatch_segments([], 45.0, 1.0)), 0)


class FillTests(unittest.TestCase):
    def test_plate_with_many_holes_and_an_island(self):
        outer = np.array([[0, 0], [100, 0], [100, 60], [0, 60.0]])
        holes = [circle(15 + 14 * i, 15, 4) for i in range(6)] + [circle(15 + 14 * i, 45, 4) for i in range(6)]
        big = circle(50, 30, 9, 32)
        island = circle(50, 30, 3, 16)
        loops = [outer] + holes + [big, island]
        depth, parent = loop_nesting(loops)
        self.assertEqual(depth[0], 0)
        self.assertTrue(all(d == 1 for d in depth[1:13]))
        self.assertEqual((depth[13], depth[14]), (1, 2))
        self.assertEqual(parent[14], 13)
        polys = fill_polygons(loops)
        self.assertEqual(len(polys), 2)                           # plate and island
        want = abs(_signed_area(outer)) - sum(abs(_signed_area(h)) for h in holes + [big])
        got = sum(_signed_area(p) for p in polys[:1])
        self.assertAlmostEqual(got, want, places=6)
        self.assertAlmostEqual(_signed_area(polys[1]), abs(_signed_area(island)), places=6)
        for p in polys:
            self.assertEqual(proper_self_crossings(p), 0)

    def test_hole_is_bridged_to_a_visible_vertex(self):
        # concave outer (U shape) with a hole behind the notch: a naive bridge would cut the notch
        outer = np.array([[0, 0], [30, 0], [30, 30], [20, 30], [20, 10], [10, 10], [10, 30], [0, 30.0]])
        hole = circle(25, 20, 3, 12)
        polys = fill_polygons([outer, hole])
        self.assertEqual(len(polys), 1)
        self.assertAlmostEqual(_signed_area(polys[0]), abs(_signed_area(outer)) - abs(_signed_area(hole)), places=6)
        self.assertEqual(proper_self_crossings(polys[0]), 0)

    def test_no_holes_returns_the_loop(self):
        sq = np.array([[0, 0], [1, 0], [1, 1], [0, 1.0]])
        polys = fill_polygons([sq[::-1]])
        self.assertEqual(len(polys), 1)
        self.assertGreater(_signed_area(polys[0]), 0)             # counter-clockwise


class SpeedTests(unittest.TestCase):
    def test_big_gear_timings(self):
        V, T, P = star_mesh(80, 60.0, 54.0, 30.0)
        # refine the walls so the mesh has a realistic triangle count
        reps = 300
        z = np.linspace(0, 30.0, reps + 1)
        k = len(P)
        V2 = np.concatenate([np.c_[P, np.full(k, zz)] for zz in z])
        nx = (np.arange(k) + 1) % k
        T2 = []
        for s in range(reps):
            a, b = np.arange(k) + s * k, np.arange(k) + (s + 1) * k
            T2 += list(zip(a, a[nx], b[nx])) + list(zip(a, b[nx], b))
        # caps (fans around two centre vertices)
        c0, c1 = len(V2), len(V2) + 1
        V2 = np.concatenate((V2, [[0, 0, 0.0], [0, 0, 30.0]]))
        for i in range(k):
            T2 += [(c0, i, nx[i]), (c1, reps * k + i, reps * k + nx[i])]
        V2, T2 = np.asarray(V2), np.array(T2, dtype=np.int64)
        t0 = time.perf_counter()
        Vw, Tw = welded(V2, T2)
        t1 = time.perf_counter()
        closed, opened = mesh_section_loops(Vw, Tw, (1, 0, 0), 3.3)
        t2 = time.perf_counter()
        loops = uv(closed, 0)
        polys = fill_polygons(loops)
        t3 = time.perf_counter()
        segs = hatch_segments(loops, 45.0, 1.0)
        t4 = time.perf_counter()
        print("\n  %d triangles: weld %.0f ms | loops %.0f ms (%d) | fill %.0f ms | hatch %.0f ms (%d segs)" % (
            len(T2), (t1 - t0) * 1e3, (t2 - t1) * 1e3, len(closed), (t3 - t2) * 1e3, (t4 - t3) * 1e3, len(segs)))
        self.assertGreater(len(closed), 0)
        self.assertLess(t2 - t1, 1.0)



class TrapezoidFillTests(unittest.TestCase):
    @staticmethod
    def quad_area(q):
        return sum(abs(_signed_area(x)) for x in q)

    def test_plate_with_holes_and_island_area(self):
        outer = np.array([[0, 0], [100, 0], [100, 60], [0, 60.0]])
        holes = [circle(15 + 14 * i, 15, 4) for i in range(6)] + [circle(15 + 14 * i, 45, 4) for i in range(6)]
        big, island = circle(50, 30, 9, 32), circle(50, 30, 3, 16)
        loops = [outer] + holes + [big, island]
        want = (abs(_signed_area(outer)) - sum(abs(_signed_area(h)) for h in holes + [big])
                + abs(_signed_area(island)))
        q = fill_trapezoids(loops)
        self.assertAlmostEqual(self.quad_area(q), want, places=6)

    def test_concave_u_shape_and_rotation(self):
        outer = np.array([[0, 0], [30, 0], [30, 30], [20, 30], [20, 10], [10, 10], [10, 30], [0, 30.0]])
        hole = circle(25, 20, 3, 12)[::-1]
        want = abs(_signed_area(outer)) - abs(_signed_area(hole))
        for ang in (0.0, 0.3, 1.1, 2.5):
            c, s_ = np.cos(ang), np.sin(ang)
            R = np.array([[c, -s_], [s_, c]])
            q = fill_trapezoids([outer @ R, hole @ R])
            self.assertAlmostEqual(self.quad_area(q), want, places=6)

    def test_star_gear_with_bore(self):
        n = 120
        th = np.linspace(0, 2 * np.pi, 2 * n, endpoint=False)
        r = np.where(np.arange(2 * n) % 2 == 0, 20.0, 17.0)
        gear = np.stack((r * np.cos(th), r * np.sin(th)), axis=1)
        bore = circle(0, 0, 6, 24)
        want = abs(_signed_area(gear)) - abs(_signed_area(bore))
        q = fill_trapezoids([gear, bore])
        self.assertAlmostEqual(self.quad_area(q), want, places=5)
        self.assertTrue(np.all(q[:, 2, 1] >= q[:, 0, 1]))

    def test_empty(self):
        self.assertEqual(fill_trapezoids([]).shape, (0, 4, 2))

    def test_speed_on_a_big_section(self):
        import time
        loops = [circle(0, 0, 100, 1500)] + [circle(40 * np.cos(a), 40 * np.sin(a), 5, 200)
                                              for a in np.linspace(0, 6.28, 6, endpoint=False)]
        t0 = time.perf_counter()
        fill_trapezoids(loops)
        self.assertLess(time.perf_counter() - t0, 1.5)


class PlaneIndexTests(unittest.TestCase):
    NORMALS = ((1, 0, 0), (0, 1, 0), (0, 0, 1), (0.6, 0.0, 0.8), (0.48, 0.6, 0.64))

    def check(self, V, T):
        for n in self.NORMALS:
            n = np.array(n, dtype=float)
            s = V @ n
            index = mesh_plane_index(V, T, n)
            # random heights, exact vertex heights and planes outside the mesh
            ds = np.concatenate((np.linspace(s.min() - 1, s.max() + 1, 37), s[:: max(1, len(s) // 25)]))
            for d in ds:
                closed_a, open_a = mesh_section_loops(V, T, n, d, 1e-6)
                closed_b, open_b = mesh_section_loops(V, T, n, d, 1e-6, index)
                self.assertEqual(len(closed_a), len(closed_b))
                self.assertEqual(len(open_a), len(open_b))
                for a, b in zip(closed_a + open_a, closed_b + open_b):
                    self.assertEqual(sorted(map(tuple, a.round(9))), sorted(map(tuple, b.round(9))))

    def test_torus_matches_the_full_scan(self):
        self.check(*welded(*torus_mesh(10.0, 3.0, 48, 24)))

    def test_long_triangles_match_the_full_scan(self):
        self.check(*welded(*star_mesh(24, 20.0, 15.0, 40.0)[:2]))

    def test_box_matches_the_full_scan(self):
        self.check(*welded(*box_mesh(3.0, 5.0, 7.0)))

    def test_candidates_are_few_on_a_fine_mesh(self):
        V, T = welded(*torus_mesh(10.0, 3.0, 300, 200))
        s, lo, step, starts, ids = mesh_plane_index(V, T, np.array((0.0, 0.0, 1.0)))
        k = int((0.5 - lo) / step)
        self.assertLess(starts[k + 1] - starts[k], len(T) / 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)

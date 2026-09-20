"""
核心数学引擎 - 移植自 React 版本
DLT 摄像机矩阵解算 & 透视变换 & 高度线性解算
"""
import numpy as np
from typing import Optional


DEFAULT_SCENE_CONFIG = {
    "width": 180.0,
    "depth": 310.0,
    "height": 150.0,
}


def normalize_scene_config(scene_config: Optional[dict] = None) -> dict:
    config = dict(DEFAULT_SCENE_CONFIG)
    if scene_config:
        config.update(scene_config)
    config["width"] = max(1.0, float(config.get("width", DEFAULT_SCENE_CONFIG["width"])))
    config["depth"] = max(1.0, float(config.get("depth", DEFAULT_SCENE_CONFIG["depth"])))
    config["height"] = max(1.0, float(config.get("height", DEFAULT_SCENE_CONFIG["height"])))
    return config


def solve_linear_system(A: list, b: list) -> Optional[list]:
    """高斯消元法求解线性方程组"""
    A = [row[:] for row in A]
    b = b[:]
    n = len(A)
    for i in range(n):
        max_el, max_row = abs(A[i][i]), i
        for k in range(i + 1, n):
            if abs(A[k][i]) > max_el:
                max_el, max_row = abs(A[k][i]), k
        A[i], A[max_row] = A[max_row], A[i]
        b[i], b[max_row] = b[max_row], b[i]
        if abs(A[i][i]) < 1e-10:
            return None
        for k in range(i + 1, n):
            c = -A[k][i] / A[i][i]
            for j in range(i, n):
                A[k][j] = 0 if i == j else A[k][j] + c * A[i][j]
            b[k] += c * b[i]
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        x[i] = b[i] / A[i][i]
        for k in range(i - 1, -1, -1):
            b[k] -= A[k][i] * x[i]
    return x


def solve_camera_matrix(pts: list) -> Optional[list]:
    """
    DLT 解算摄像机矩阵 P (3x4)
    pts: [{'x': u, 'y': v, 'x3': X, 'y3': Y, 'z3': Z}, ...]  至少6个点
    """
    if len(pts) < 6:
        return None
    N = len(pts)
    cu = sum(p['x'] for p in pts) / N
    cv = sum(p['y'] for p in pts) / N
    cX = sum(p.get('x3', 0) for p in pts) / N
    cY = sum(p.get('y3', 0) for p in pts) / N
    cZ = sum(p.get('z3', 0) for p in pts) / N

    s2d = sum(((p['x'] - cu) ** 2 + (p['y'] - cv) ** 2) ** 0.5 for p in pts)
    s3d = sum(((p.get('x3', 0) - cX) ** 2 + (p.get('y3', 0) - cY) ** 2 + (p.get('z3', 0) - cZ) ** 2) ** 0.5 for p in pts)
    s2d = (2 ** 0.5) * N / (s2d or 1)
    s3d = (3 ** 0.5) * N / (s3d or 1)

    A, b = [], []
    for pt in pts:
        u = (pt['x'] - cu) * s2d
        v = (pt['y'] - cv) * s2d
        X = (pt.get('x3', 0) - cX) * s3d
        Y = (pt.get('y3', 0) - cY) * s3d
        Z = (pt.get('z3', 0) - cZ) * s3d
        A.append([X, Y, Z, 1, 0, 0, 0, 0, -u * X, -u * Y, -u * Z]); b.append(u)
        A.append([0, 0, 0, 0, X, Y, Z, 1, -v * X, -v * Y, -v * Z]); b.append(v)

    ATA = [[0.0] * 11 for _ in range(11)]
    ATb = [0.0] * 11
    for k in range(len(A)):
        for i in range(11):
            ATb[i] += A[k][i] * b[k]
            for j in range(11):
                ATA[i][j] += A[k][i] * A[k][j]

    pn = solve_linear_system(ATA, ATb)
    if not pn:
        return None

    P_norm = [[pn[0], pn[1], pn[2], pn[3]],
              [pn[4], pn[5], pn[6], pn[7]],
              [pn[8], pn[9], pn[10], 1.0]]

    P_tmp = [[0.0] * 4 for _ in range(3)]
    for i in range(3):
        P_tmp[i][0] = P_norm[i][0] * s3d
        P_tmp[i][1] = P_norm[i][1] * s3d
        P_tmp[i][2] = P_norm[i][2] * s3d
        P_tmp[i][3] = (P_norm[i][0] * (-s3d * cX) + P_norm[i][1] * (-s3d * cY) +
                       P_norm[i][2] * (-s3d * cZ) + P_norm[i][3])

    P = [[0.0] * 4 for _ in range(3)]
    for j in range(4):
        P[0][j] = (1 / s2d) * P_tmp[0][j] + cu * P_tmp[2][j]
        P[1][j] = (1 / s2d) * P_tmp[1][j] + cv * P_tmp[2][j]
        P[2][j] = P_tmp[2][j]
    return P


def project_point(P: list, X: float, Y: float, Z: float) -> dict:
    """将3D点投影到2D图像坐标"""
    w = P[2][0] * X + P[2][1] * Y + P[2][2] * Z + P[2][3]
    if abs(w) < 1e-8:
        w = 1e-8
    return {
        'x': (P[0][0] * X + P[0][1] * Y + P[0][2] * Z + P[0][3]) / w,
        'y': (P[1][0] * X + P[1][1] * Y + P[1][2] * Z + P[1][3]) / w
    }


def solve_height_with_xyz(P: list, X: float, Y: float, Z_base: float, v_2d: float) -> float:
    """根据2D v坐标反解鼠标高度"""
    if not P:
        return 0.0
    num_fixed = P[1][0] * X + P[1][1] * Y + P[1][3]
    den_fixed = P[2][0] * X + P[2][1] * Y + P[2][3]
    a = v_2d * P[2][2] - P[1][2]
    b = num_fixed - v_2d * den_fixed
    if abs(a) < 1e-8:
        return 0.0
    return max(0.0, (b / a) - Z_base)


def solve_x_with_yz(P: list, u: float, Y: float, Z: float) -> Optional[float]:
    """已知Y,Z和u坐标，反解X"""
    if not P:
        return None
    num = P[0][1] * Y + P[0][2] * Z + P[0][3] - u * (P[2][1] * Y + P[2][2] * Z + P[2][3])
    den = u * P[2][0] - P[0][0]
    if abs(den) < 1e-8:
        return None
    return num / den


def solve_xy_with_z(P: list, u: float, v: float, Z: float) -> Optional[dict]:
    """已知Z和(u,v)坐标，反解X,Y"""
    if not P:
        return None
    A = u * P[2][0] - P[0][0]
    B = u * P[2][1] - P[0][1]
    C = (P[0][2] - u * P[2][2]) * Z + (P[0][3] - u * P[2][3])
    D = v * P[2][0] - P[1][0]
    E = v * P[2][1] - P[1][1]
    F = (P[1][2] - v * P[2][2]) * Z + (P[1][3] - v * P[2][3])
    det = A * E - B * D
    if abs(det) < 1e-8:
        return None
    return {'x': (C * E - B * F) / det, 'y': (A * F - C * D) / det}


def apply_homography(H: list, pt: dict) -> Optional[dict]:
    """应用单应矩阵变换"""
    w = H[2][0] * pt['x'] + H[2][1] * pt['y'] + H[2][2]
    if abs(w) < 1e-8:
        w = -1e-8 if w < 0 else 1e-8
    return {
        'x': (H[0][0] * pt['x'] + H[0][1] * pt['y'] + H[0][2]) / w,
        'y': (H[1][0] * pt['x'] + H[1][1] * pt['y'] + H[1][2]) / w
    }


def get_perspective_transform(src: list, dst: list) -> Optional[list]:
    """计算4点透视变换矩阵"""
    if len(src) != 4 or len(dst) != 4:
        return None
    A = []
    for i in range(4):
        x, y = src[i]['x'], src[i]['y']
        u, v = dst[i]['x'], dst[i]['y']
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y])
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y])
    B = []
    for i in range(4):
        B.append(dst[i]['x'])
        B.append(dst[i]['y'])
    h = solve_linear_system(A, B)
    if not h:
        return None
    return [[h[0], h[1], h[2]], [h[3], h[4], h[5]], [h[6], h[7], 1.0]]


def is_point_in_polygon(point: dict, vs: list) -> bool:
    """射线法判断点是否在多边形内"""
    if not vs or len(vs) < 3:
        return False
    x, y, inside = point['x'], point['y'], False
    j = len(vs) - 1
    for i in range(len(vs)):
        xi, yi = vs[i]['x'], vs[i]['y']
        xj, yj = vs[j]['x'], vs[j]['y']
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def compute_grid_3d_coords(points: list, grid_data: list, scene_config: Optional[dict] = None) -> list:
    """
    计算所有点的3D坐标（与React版本 p3d useMemo 逻辑一致）
    label '1'=地面, '2'=近端垂直面, '3'=中间垂直面, '4'=远端垂直面
    """
    scene = normalize_scene_config(scene_config)
    completed_labels = set(p['label'] for p in grid_data)
    all_pts = [p for p in points if p.get('label', '1') not in completed_labels] + grid_data

    maxes = {}
    for lbl in ['1', '2', '3', '4']:
        l_pts = [p for p in all_pts if p.get('label') == lbl and p.get('c') is not None]
        if l_pts:
            maxes[lbl] = {
                'maxC': max(1, max(p['c'] for p in l_pts)),
                'maxR': max(1, max(p['r'] for p in l_pts))
            }

    result = []
    for p in all_pts:
        if p.get('c') is None or p.get('r') is None:
            continue
        lbl = p.get('label', '1')
        m = maxes.get(lbl)
        if not m:
            continue
        x3, y3, z3 = 0.0, 0.0, 0.0
        if lbl == '1':
            x3 = ((p['c'] / m['maxC']) - 0.5) * scene['width']
            y3 = (1.0 - (p['r'] / m['maxR'])) * scene['depth']
            z3 = 0.0
        elif lbl in ('2', '3', '4'):
            x3 = ((p['c'] / m['maxC']) - 0.5) * scene['width']
            y3 = 0.0 if lbl == '2' else (scene['depth'] / 2.0 if lbl == '3' else scene['depth'])
            z3 = (1.0 - (p['r'] / m['maxR'])) * scene['height']
        result.append({**p, 'x3': x3, 'y3': y3, 'z3': z3})
    return result


def interpolate_grid(active_points: list, n_cols: int, n_rows: int, label: str) -> list:
    """
    透视插值补全网格（与React handleAutoComplete逻辑一致）
    active_points: [{'id', 'x', 'y', 'label'}, ...]
    返回补全后的网格点列表
    """
    if len(active_points) < 4:
        return []

    # 找凸包四角
    sorted_pts = sorted(active_points, key=lambda p: (p['x'], p['y']))

    def cross(o, a, b):
        return (a['x'] - o['x']) * (b['y'] - o['y']) - (a['y'] - o['y']) * (b['x'] - o['x'])

    lower = []
    for p in sorted_pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(sorted_pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    upper.pop(); lower.pop()
    hull = lower + upper

    if len(hull) < 4:
        min_x = min(p['x'] for p in active_points)
        max_x = max(p['x'] for p in active_points)
        min_y = min(p['y'] for p in active_points)
        max_y = max(p['y'] for p in active_points)
        pTL = {'x': min_x, 'y': min_y}; pTR = {'x': max_x, 'y': min_y}
        pBL = {'x': min_x, 'y': max_y}; pBR = {'x': max_x, 'y': max_y}
    else:
        # 找最大面积四边形
        best_quad, max_area = hull[:4], 0
        for i in range(len(hull)):
            for j in range(i + 1, len(hull)):
                for k in range(j + 1, len(hull)):
                    for l in range(k + 1, len(hull)):
                        q = [hull[i], hull[j], hull[k], hull[l]]
                        area = 0.5 * abs(q[0]['x'] * q[1]['y'] + q[1]['x'] * q[2]['y'] +
                                         q[2]['x'] * q[3]['y'] + q[3]['x'] * q[0]['y'] -
                                         (q[0]['y'] * q[1]['x'] + q[1]['y'] * q[2]['x'] +
                                          q[2]['y'] * q[3]['x'] + q[3]['y'] * q[0]['x']))
                        if area > max_area:
                            max_area, best_quad = area, q
        best_quad.sort(key=lambda p: p['x'])
        lefts = sorted(best_quad[:2], key=lambda p: p['y'])
        rights = sorted(best_quad[2:], key=lambda p: p['y'])
        pTL, pBL = lefts[0], lefts[1]
        pTR, pBR = rights[0], rights[1]

    src_corners = [{'x': 0, 'y': 0}, {'x': 1000, 'y': 0}, {'x': 0, 'y': 1000}, {'x': 1000, 'y': 1000}]
    dst_corners = [pTL, pTR, pBL, pBR]
    H_inv = get_perspective_transform(dst_corners, src_corners)
    H_fwd = get_perspective_transform(src_corners, dst_corners)
    if not H_inv or not H_fwd:
        return []

    # 建立手动点映射
    green_map = set()
    for pt in active_points:
        sp = apply_homography(H_inv, pt)
        c = round((sp['x'] / 1000) * (n_cols - 1))
        r = round((sp['y'] / 1000) * (n_rows - 1))
        c = max(0, min(n_cols - 1, c))
        r = max(0, min(n_rows - 1, r))
        green_map.add(f"{c},{r}")

    full_grid = []
    for r in range(n_rows):
        for c in range(n_cols):
            sx = c * (1000 / (n_cols - 1 or 1))
            sy = r * (1000 / (n_rows - 1 or 1))
            ideal = apply_homography(H_fwd, {'x': sx, 'y': sy})
            full_grid.append({
                'c': c, 'r': r,
                'x': ideal['x'], 'y': ideal['y'],
                'label': label,
                'is_manual_covered': f"{c},{r}" in green_map
            })
    return full_grid

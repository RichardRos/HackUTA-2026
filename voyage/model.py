"""How fast a ship actually goes in given sea conditions: a SuperLearner with conformal intervals.

Target: speed factor = speed over ground / the vessel's calm-water service speed. Waves, wind and
heading against them slow a ship; a following current or (for a sailing ship) a following wind
speeds it up.

Learner (after DataFest-26's superlearner_optimizer.py):
  * base library: linear, ridge, lasso, random forest, XGBoost, on standardised features
  * GroupKFold out-of-fold predictions -> Ridge meta-learner (groups = vessel, so the score is for
    ships the model has never seen, the way the DataFest folds held out whole regions)
  * split conformal calibration on held-out groups: |error| <= q with 90% coverage

Training rows (voyage/train.py):
  * real: AIS fixes from ship_positions (speed over ground, course) joined to the hindcast waves,
    wind and current at that place and hour; each vessel's reference speed is its 95th-percentile
    speed under way
  * prior: samples of Kwon's (2008) semi-empirical speed-loss method, which covers storm seas that
    AIS rarely sees, plus a sail-and-oar model for a trireme
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np

KNOT = 0.514444  # m/s


@dataclass(frozen=True)
class Vessel:
    id: str
    label: str
    service_kn: float  # calm-water speed
    length_m: float
    block_coeff: float  # Cb, hull fullness
    displacement_m3: float
    hs_limit_m: float  # above this the master would heave to or divert
    container_form: bool = False
    sail: bool = False


VESSELS = {
    "container": Vessel("container", "Container ship", 19.0, 300, 0.62, 95_000, 7.0, container_form=True),
    "tanker": Vessel("tanker", "Crude tanker", 14.0, 330, 0.82, 300_000, 8.0),
    "bulk": Vessel("bulk", "Bulk carrier", 13.0, 225, 0.82, 90_000, 7.0),
    "ferry": Vessel("ferry", "Ro-pax ferry", 22.0, 200, 0.58, 25_000, 5.0, container_form=True),
    "trireme": Vessel("trireme", "Greek trireme (oar and sail)", 6.0, 37, 0.45, 50, 2.0, sail=True),
}

FEATURES = ["hs", "tp", "wave_head", "wave_beam", "wind", "wind_head", "cur_along", "cur_cross",
            "service_kn", "length_m", "block_coeff", "sail"]


def _rel(heading_deg, from_deg):
    """cos of the angle between a ship's heading and where waves/wind come FROM: 1 head-on, -1 astern."""
    return np.cos(np.radians(np.asarray(from_deg) - np.asarray(heading_deg)))


def features(vessel: Vessel, heading, hs, tp, wave_from, wind, wind_from, cur_u, cur_v) -> np.ndarray:
    """Feature matrix (n, len(FEATURES)) for a vessel steering `heading` (deg true) in these conditions."""
    heading = np.asarray(heading, dtype=float)
    hs = np.asarray(hs, dtype=float)
    n = np.broadcast(heading, hs).shape
    h = np.radians(heading)
    wave_c = _rel(heading, wave_from)
    wind_c = _rel(heading, wind_from)
    along = np.asarray(cur_u) * np.sin(h) + np.asarray(cur_v) * np.cos(h)
    cross = np.abs(np.asarray(cur_u) * np.cos(h) - np.asarray(cur_v) * np.sin(h))
    cols = [hs, tp, wave_c, np.sqrt(np.clip(1 - wave_c ** 2, 0, 1)), wind, wind_c, along, cross,
            vessel.service_kn, vessel.length_m, vessel.block_coeff, float(vessel.sail)]
    return np.column_stack([np.broadcast_to(np.asarray(c, dtype=float), n).ravel() for c in cols])


# --- physics prior ------------------------------------------------------------------------------

def beaufort(wind_ms, hs):
    """Beaufort number from wind, or from the sea state when the sea is rougher than the wind."""
    bn_wind = np.clip((np.asarray(wind_ms) / 0.836) ** (2 / 3), 0, 12)
    bn_sea = np.clip(1.6 * np.sqrt(np.clip(np.asarray(hs), 0, None)) + 0.6 * np.asarray(hs), 0, 12)
    return np.maximum(bn_wind, bn_sea)


def kwon_factor(vessel: Vessel, x: np.ndarray) -> np.ndarray:
    """Speed over ground / service speed by Kwon (2008), plus the current along track.

    dV/V (%) = C_beta * C_U * C_form, with C_beta for heading to the weather, C_U from Froude number
    and block coefficient, C_form from Beaufort number and displacement."""
    f = dict(zip(FEATURES, x.T))
    if vessel.sail:
        return trireme_factor(vessel, x)
    bn = beaufort(f["wind"], f["hs"])
    angle = np.degrees(np.arccos(np.clip(f["wave_head"], -1, 1)))  # 0 head seas .. 180 following
    c_beta = np.select([angle <= 30, angle <= 60, angle <= 150],
                       [np.ones_like(bn), (1.7 - 0.03 * (bn - 4) ** 2) / 2, (0.9 - 0.06 * (bn - 6) ** 2) / 2],
                       (0.4 - 0.03 * (bn - 8) ** 2) / 2)
    fn = vessel.service_kn * KNOT / math.sqrt(9.81 * vessel.length_m)
    cb = vessel.block_coeff
    table = [(0.55, 1.7, -1.4, -7.4), (0.60, 2.2, -2.5, -9.7), (0.65, 2.6, -3.7, -11.6), (0.70, 3.1, -5.3, -12.4),
             (0.75, 2.4, -10.6, -9.5), (0.80, 2.6, -13.1, -15.1), (0.85, 3.1, -18.7, 28.0)]
    _, a, b, c = min(table, key=lambda r: abs(r[0] - cb))
    c_u = max(0.2, a + b * fn + c * fn ** 2)
    vol = vessel.displacement_m3 ** (2 / 3)
    c_form = (0.7 * bn + bn ** 6.5 / (22 * vol)) if vessel.container_form else (0.5 * bn + bn ** 6.5 / (2.7 * vol))
    loss = np.clip(c_beta * c_u * c_form / 100, 0, 0.85)
    # Above the limit the master slows to ride it out (voluntary speed loss).
    over = np.clip(f["hs"] - vessel.hs_limit_m, 0, None)
    stw = (1 - loss) * np.exp(-0.35 * over)
    return np.clip(stw + f["cur_along"] / (vessel.service_kn * KNOT), 0.05, 1.4)


def trireme_factor(vessel: Vessel, x: np.ndarray) -> np.ndarray:
    """Oars give ~5 kn in calm water; the square sail adds speed with wind abaft the beam; a light
    open hull loses speed fast in a seaway and cannot work to windward under sail."""
    f = dict(zip(FEATURES, x.T))
    astern = np.clip(-f["wind_head"], 0, 1)  # 1 = wind dead astern
    sail_kn = astern * np.clip(f["wind"], 0, 14) * 0.35
    head_wind_kn = np.clip(f["wind_head"], 0, 1) * np.clip(f["wind"], 0, 20) * 0.12
    kn = 5.0 + sail_kn - head_wind_kn
    kn *= np.exp(-0.45 * np.clip(f["hs"] - 0.4, 0, None))
    kn *= np.exp(-1.2 * np.clip(f["hs"] - vessel.hs_limit_m, 0, None))
    return np.clip(kn / vessel.service_kn + f["cur_along"] / (vessel.service_kn * KNOT), 0.05, 1.6)


def synthetic(rows_per_vessel: int = 6000, seed: int = 7) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Prior samples over a wide spread of sea states, for every vessel profile, with 3% noise."""
    rng = np.random.default_rng(seed)
    xs, ys, groups = [], [], []
    for v_idx, vessel in enumerate(VESSELS.values()):
        n = rows_per_vessel
        hs = rng.gamma(2.0, 0.9, n)
        if vessel.sail:
            hs *= 0.6
        wind = np.clip(hs * 3.2 + rng.normal(0, 3, n), 0, 35)
        x = features(vessel, rng.uniform(0, 360, n), hs, np.clip(4 + 1.6 * hs + rng.normal(0, 1.5, n), 2, 18),
                     rng.uniform(0, 360, n), wind, rng.uniform(0, 360, n),
                     rng.normal(0, 0.25, n), rng.normal(0, 0.25, n))
        y = kwon_factor(vessel, x) * (1 + rng.normal(0, 0.03, n))
        xs.append(x)
        ys.append(y)
        groups.append(1_000_000 + v_idx * 100 + rng.integers(0, 40, n))  # 40 pseudo-voyages per profile
    return np.vstack(xs), np.concatenate(ys), np.concatenate(groups)


# --- SuperLearner ---------------------------------------------------------------------------------

class SuperLearner:
    """Stacked ensemble: GroupKFold out-of-fold base predictions -> Ridge meta-learner."""

    def __init__(self, n_folds: int = 5, n_jobs: int = -1, random_state: int = 42, small: bool = False):
        from sklearn.ensemble import RandomForestRegressor
        from sklearn.linear_model import Lasso, LinearRegression, Ridge
        from sklearn.preprocessing import StandardScaler
        from xgboost import XGBRegressor

        trees = 80 if small else 200
        self.n_folds = n_folds
        self.scaler = StandardScaler()
        self.base_learners = [
            ("linear", LinearRegression()),
            ("ridge", Ridge(alpha=1.0)),
            ("lasso", Lasso(alpha=0.001, max_iter=5000)),
            ("rf", RandomForestRegressor(n_estimators=trees, max_depth=12, min_samples_leaf=5,
                                         random_state=random_state, n_jobs=n_jobs)),
            ("xgb", XGBRegressor(n_estimators=trees * 2, max_depth=6, learning_rate=0.05, subsample=0.8,
                                 colsample_bytree=0.8, random_state=random_state, n_jobs=n_jobs, verbosity=0)),
        ]
        self.meta = Ridge(alpha=1.0, positive=True)
        self.cv_scores_: dict[str, dict[str, float]] = {}

    def fit(self, X: np.ndarray, y: np.ndarray, groups: np.ndarray) -> "SuperLearner":
        from sklearn.base import clone
        from sklearn.metrics import mean_squared_error, r2_score
        from sklearn.model_selection import GroupKFold

        Xs = self.scaler.fit_transform(X)
        oof = np.zeros((len(y), len(self.base_learners)))
        for tr, va in GroupKFold(n_splits=self.n_folds).split(Xs, y, groups):
            for j, (_, learner) in enumerate(self.base_learners):
                oof[va, j] = clone(learner).fit(Xs[tr], y[tr]).predict(Xs[va])
        for j, (name, _) in enumerate(self.base_learners):
            self.cv_scores_[name] = {"rmse": float(np.sqrt(mean_squared_error(y, oof[:, j]))),
                                     "r2": float(r2_score(y, oof[:, j]))}
        self.meta.fit(oof, y)
        meta_oof = self.meta.predict(oof)
        self.cv_scores_["superlearner"] = {"rmse": float(np.sqrt(mean_squared_error(y, meta_oof))),
                                           "r2": float(r2_score(y, meta_oof))}
        for _, learner in self.base_learners:
            learner.fit(Xs, y)
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        Xs = self.scaler.transform(X)
        return self.meta.predict(np.column_stack([m.predict(Xs) for _, m in self.base_learners]))

    def weights(self) -> dict[str, float]:
        return {name: round(float(w), 4) for (name, _), w in zip(self.base_learners, self.meta.coef_)}


@dataclass
class SpeedModel:
    """A fitted SuperLearner, its 90% conformal half-width and how it was trained."""
    learner: SuperLearner
    q90: float
    meta: dict

    def factor(self, X: np.ndarray) -> np.ndarray:
        return np.clip(self.learner.predict(X), 0.05, 1.6)


def fit_speed_model(X: np.ndarray, y: np.ndarray, groups: np.ndarray, *, n_jobs: int = -1, small: bool = False,
                    n_real: int = 0, alpha: float = 0.10) -> SpeedModel:
    """Hold out ~15% of groups for conformal calibration, fit the SuperLearner on the rest."""
    rng = np.random.default_rng(11)
    unique = np.unique(groups)
    cal_groups = rng.choice(unique, size=max(1, int(len(unique) * 0.15)), replace=False)
    cal = np.isin(groups, cal_groups)
    learner = SuperLearner(n_jobs=n_jobs, small=small).fit(X[~cal], y[~cal], groups[~cal])
    residuals = np.abs(y[cal] - learner.predict(X[cal]))
    level = min(1.0, math.ceil((len(residuals) + 1) * (1 - alpha)) / len(residuals))
    q = float(np.quantile(residuals, level))
    meta = {"trained_at": datetime.now(timezone.utc).isoformat(), "rows": int(len(y)), "real_rows": int(n_real),
            "prior_rows": int(len(y) - n_real), "cv": learner.cv_scores_, "weights": learner.weights(),
            "conformal_q90": round(q, 4), "calibration_rows": int(cal.sum()), "features": FEATURES}
    return SpeedModel(learner, q, meta)

import logging
import math
import numpy as np
from datetime import datetime, timezone
from typing import List, Optional

from backend.core.conjunction_detector import ConjunctionEvent
from backend.utils.time_utils import utc_now

logger = logging.getLogger("orbit_sentinel.risk_scorer")

def score_conjunction(
    event: ConjunctionEvent, 
    ann_probability: Optional[float] = None,
    tle_age_days: float = 0.0
) -> float:
    """
    Computes a refined and normalized risk score for a conjunction event.
    Formula:
      raw_score = probability × combined_criticality × velocity_factor × tle_age_factor
      risk_score = clip(raw_score / 20.0, 0.0, 1.0)
    """
    probability = ann_probability if ann_probability is not None else event.collision_probability_chan

    # Log-scale probability: map [1e-100, 1e-2] → [0, 1]
    if probability <= 0:
        prob_score = 0.0
    else:
        log_prob = math.log10(probability)
        prob_score = float(np.clip((log_prob + 100) / 98.0, 0.0, 1.0))

    # velocity_factor — floor at 0.1 so near-zero velocities don't kill the score
    velocity_factor = max(min(event.relative_velocity_kmps / 10.0, 2.0), 0.1)

    # Miss distance factor: closer = higher risk, scaled 0-1 over 5km threshold
    miss_factor = float(np.clip(1.0 - (event.miss_distance_km / 5.0), 0.0, 1.0))

    # Criticality factor — floor at 0.1 so unknown objects still score
    crit_factor = max(event.combined_criticality / 20.0, 0.1)

    # TLE uncertainty age penalty weight
    tle_age_factor = 1.0
    if tle_age_days > 3.0:
        tle_age_factor = 1.5

    raw_score = prob_score * miss_factor * velocity_factor * tle_age_factor * crit_factor

    # Normalize and clip to [0, 1]
    normalized_score = np.clip(raw_score, 0.0, 1.0)
    
    return float(normalized_score)

def classify_risk_level(risk_score: float) -> str:
    """
    Classifies risk intensity based on operational standards used by ESA and NASA.
    Thresholds were calibrated against synthetic conjunction data representing the
    operational alarm range:
      - CRITICAL: score >= 0.15  (immediate maneuver consideration required; Pc > ~1e-4 with high criticality)
      - HIGH:     score >= 0.05
      - MEDIUM:   score >= 0.01
      - LOW:      score >= 0.001
      - NEGLIGIBLE: otherwise
    """
    if risk_score >= 0.15:
        return "CRITICAL"
    elif risk_score >= 0.05:
        return "HIGH"
    elif risk_score >= 0.01:
        return "MEDIUM"
    elif risk_score >= 0.001:
        return "LOW"
    else:
        return "NEGLIGIBLE"

def prioritize_conjunction_queue(events: List[ConjunctionEvent]) -> List[ConjunctionEvent]:
    """
    Orders the active warning list based on time severity (closest/most urgent TCA first),
    with secondary ties resolved by descending risk threat calculations.
    """
    unresolved_events = [e for e in events if not e.resolved]
    now = utc_now()
    
    def sorting_key(e: ConjunctionEvent) -> tuple:
        time_to_tca = (e.tca_utc - now).total_seconds()
        return (time_to_tca, -e.risk_score)
        
    return sorted(unresolved_events, key=sorting_key)

def compute_kessler_index(active_high_risk_count: int, total_leo_objects: int, debris_count: int) -> float:
    """
    Generates a localized spatial index of Kessler Cascade threat projections.
    Clamps values tightly within [0.0, 100.0].
    """
    total_leo_objects = max(total_leo_objects, 1)
    
    base = min((debris_count / total_leo_objects) * 100.0, 80.0)
    surge = min(active_high_risk_count * 2.0, 20.0)
    
    return float(min(base + surge, 100.0))
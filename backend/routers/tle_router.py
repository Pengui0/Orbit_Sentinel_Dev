import os
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, Query, HTTPException, BackgroundTasks
from backend.db.mongo_client import get_db
from backend.db import satellite_repo
from backend.core.tle_ingestion import run_tle_ingestion_job, get_cached_tles
from backend.core.sgp4_propagator import propagate_single, propagate_current_positions
from backend.core.scheduler import sentinel_scheduler

router = APIRouter()

def serialize_mongo_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return doc
    res = {}
    for k, v in doc.items():
        if k == "_id":
            res["_id"] = str(v)
        elif isinstance(v, datetime):
            res[k] = v.isoformat()
        else:
            res[k] = v
    return res

@router.get("/status")
async def get_tle_status(db = Depends(get_db)):
    """
    Retrieve current TLE cache status, count, scheduled pull times, and offline indicators.
    """
    # 1. Last pull time
    last_pull_time = None
    try:
        snapshots = await db["tle_snapshots"].find({}).sort("fetched_at", -1).to_list(length=1)
        latest_snapshot = snapshots[0] if snapshots else None
        if latest_snapshot:
            fetched = latest_snapshot.get("fetched_at")
            if isinstance(fetched, datetime):
                last_pull_time = fetched.isoformat()
            elif isinstance(fetched, str):
                last_pull_time = fetched
    except Exception:
        pass

    # 2. Object count
    try:
        object_count = await db["satellites"].count_documents({})
    except Exception:
        object_count = 0

    # 3. Next scheduled pull (in X minutes)
    next_scheduled_pull = 10
    try:
        if sentinel_scheduler and sentinel_scheduler.scheduler:
            job = sentinel_scheduler.scheduler.get_job("job_ingest_tles")
            if job and job.next_run_time:
                now_utc = datetime.now(timezone.utc)
                diff = job.next_run_time - now_utc
                next_scheduled_pull = max(0, int(diff.total_seconds() / 60))
    except Exception:
        pass

    # 4. Cache file indicator
    cache_file_exists = os.path.exists("tle_cache.json")

    return {
        "last_pull_time": last_pull_time,
        "object_count": object_count,
        "next_scheduled_pull": next_scheduled_pull,
        "cache_file_exists": cache_file_exists
    }

@router.get("/objects")
async def get_tracked_objects(
    type: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db = Depends(get_db)
):
    """
    Retrieve cached TLE elements using limit, offset pagination, and target classifications.
    """
    query = {}
    if type:
        query["object_type"] = type

    try:
        cursor = db["satellites"].find(query).skip(offset).limit(limit)
        objects = await cursor.to_list(length=limit)
        return [serialize_mongo_doc(obj) for obj in objects]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database retrieval failed: {e}")

@router.get("/object/{norad_id}")
async def get_tracked_object_detail(norad_id: str, db = Depends(get_db)):
    """
    Fetch a single satellite tracking profile mapped with real time propagated position coordinates.
    """
    sat = await db["satellites"].find_one({"norad_id": norad_id})
    if not sat:
        raise HTTPException(status_code=404, detail=f"Satellite with NORAD ID {norad_id} not found.")

    tle1 = sat.get("tle1")
    tle2 = sat.get("tle2")
    position_data = None
    if tle1 and tle2:
        try:
            now_utc = datetime.now(timezone.utc)
            position_data = propagate_single(tle1, tle2, now_utc)
        except Exception:
            pass

    serialized_sat = serialize_mongo_doc(sat)
    serialized_sat["position"] = position_data
    return serialized_sat

@router.post("/refresh")
async def trigger_refresh_job(background_tasks: BackgroundTasks, db = Depends(get_db)):
    """
    Trigger standard CelesTrak and Supplemental satellite synchronization in the background.
    """
    background_tasks.add_task(run_tle_ingestion_job, db)
    return {
        "status": "refresh_triggered",
        "message": "Background TLE refresh job has been scheduled."
    }

@router.get("/positions/current")
async def get_current_world_positions(db = Depends(get_db)):
    """
    Propagates top 5000 tracked targets to current system clock time to paint map visuals.
    """
    try:
        satellites_list = await db["satellites"].find({}).sort("criticality_score", -1).limit(5000).to_list(length=5000)
        if not satellites_list:
            return []

        # Strip non-serializable MongoDB _id before passing to thread executor
        clean_sats = [{k: v for k, v in s.items() if k != "_id"} for s in satellites_list]

        # Propagate batch — catch propagation errors separately for clearer logs
        try:
            positions = await propagate_current_positions(clean_sats)
        except Exception as prop_err:
            raise HTTPException(status_code=500, detail=f"SGP4 propagation engine error: {prop_err}")

        if not positions:
            return []

        # Build index maps to enrich missing values from database copies
        sats_map = {s["norad_id"]: s for s in clean_sats if "norad_id" in s}

        enriched = []
        for pos in positions:
            n_id = pos.get("norad_id")
            lat = pos.get("lat")
            lon = pos.get("lon")
            alt = pos.get("alt")
            # Skip satellites with NaN or None positions (degenerate TLE)
            if lat is None or lon is None or alt is None:
                continue
            try:
                if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and isinstance(alt, (int, float))):
                    continue
                import math
                if math.isnan(lat) or math.isnan(lon) or math.isnan(alt):
                    continue
            except Exception:
                continue
            orig_sat = sats_map.get(n_id, {})
            enriched.append({
                "norad_id": n_id,
                "name": pos.get("name", orig_sat.get("name", "SAT")),
                "object_type": orig_sat.get("object_type", "PAYLOAD"),
                "criticality_score": pos.get("criticality_score", orig_sat.get("criticality_score", 5.0)),
                "lat": lat,
                "lon": lon,
                "alt": alt,
                "speed_kmps": pos.get("speed_kmps"),
                "x": pos.get("x"),
                "y": pos.get("y"),
                "z": pos.get("z"),
                "vx": pos.get("vx"),
                "vy": pos.get("vy"),
                "vz": pos.get("vz"),
                "t": pos.get("t")
            })
        return enriched
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to propagate current positions: {e}")

@router.get("/object/{norad_id}/orbit")
async def get_full_orbit_path(norad_id: str, db = Depends(get_db)):
    """
    Calculates detailed geographic trajectory nodes tracing a complete 90 minutes low-orbit cycle.
    """
    sat = await db["satellites"].find_one({"norad_id": norad_id})
    if not sat:
        raise HTTPException(status_code=404, detail=f"Satellite with NORAD ID {norad_id} not found.")

    tle1 = sat.get("tle1")
    tle2 = sat.get("tle2")
    if not tle1 or not tle2:
        raise HTTPException(status_code=400, detail="Missing baseline TLE parameters to compute propagation paths.")

    try:
        now_utc = datetime.now(timezone.utc)
        orbit_nodes = []
        for i in range(91):  # 90 minutes full orbit cycle, +1 to close the loop
            ts = now_utc + timedelta(minutes=i)
            pos = propagate_single(tle1, tle2, ts)
            if pos:
                orbit_nodes.append({
                    "lat": pos.get("lat"),
                    "lon": pos.get("lon"),
                    "alt": pos.get("alt")
                })
        return orbit_nodes
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to propagate orbit coordinates: {e}")

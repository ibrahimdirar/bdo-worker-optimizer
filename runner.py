import json
import os
from copy import deepcopy
from pathlib import Path
from urllib.request import Request, urlopen

from bdo_empire.generate_graph_data import generate_graph_data
from bdo_empire.generate_reference_data import generate_reference_data
from bdo_empire.generate_workerman_data import generate_workerman_data
from bdo_empire.main import lodging_specifications, optimize_config, solver_config
from bdo_empire.optimize_highspy import optimize
from bdo_empire.solver_highspy import SolverController

BASE_URL = os.environ["DASHBOARD_URL"].rstrip("/")
SECRET = os.environ["DASHBOARD_RUNNER_SECRET"]
JOB_ID = os.environ["JOB_ID"]

def api(path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    req = Request(f"{BASE_URL}{path}", data=data, headers={"Authorization": f"Bearer {SECRET}", "Content-Type": "application/json"})
    with urlopen(req, timeout=120) as response:
        return json.load(response)

def main():
    job = api(f"/api/optimizer/jobs/{JOB_ID}/input")
    config = deepcopy(optimize_config)
    config["budget"] = int(job.get("cpBudget", 300))
    config["solver"] = deepcopy(solver_config)
    config["solver"]["time_limit"] = int(job.get("timeLimitSeconds", 3600))
    config["solver"]["mip_improvement_timeout"] = int(job.get("improvementTimeoutSeconds", 900))
    lodging = deepcopy(lodging_specifications)
    for town, values in job.get("lodging", {}).items():
        if town in lodging:
            lodging[town].update({k: int(v) for k, v in values.items() if k in {"bonus", "reserved", "prepaid", "bonus_ub"}})
    prices = {int(k): int(v) for k, v in job["effectivePrices"].items()}
    data = generate_reference_data(config, prices, job.get("modifiers", {}), lodging, job.get("forcedNodeIds", []))
    data = generate_graph_data(data)
    data["base_empire"] = job.get("baseEmpire") if job.get("lockCurrentEmpire") else None
    result = generate_workerman_data(optimize(data, SolverController()), lodging, data)
    Path("optimized_empire.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    api(f"/api/optimizer/jobs/{JOB_ID}/result", {"status": "completed", "empire": result})

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        try: api(f"/api/optimizer/jobs/{JOB_ID}/result", {"status": "failed", "error": str(exc)[:1000]})
        finally: raise

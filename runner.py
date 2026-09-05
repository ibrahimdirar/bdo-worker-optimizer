import json, os
from copy import deepcopy
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from bdo_empire.generate_graph_data import generate_graph_data
from bdo_empire.generate_reference_data import generate_reference_data
from bdo_empire.generate_workerman_data import generate_workerman_data
from bdo_empire.main import lodging_specifications, optimize_config, solver_config
from bdo_empire.optimize_highspy import optimize
from bdo_empire.solver_highspy import SolverController

BASE=os.environ["DASHBOARD_URL"].rstrip("/"); JOB=os.environ["JOB_ID"]

def oidc():
 req=Request(os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]+"&audience="+quote(BASE),headers={"Authorization":"Bearer "+os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]})
 with urlopen(req) as r:return json.load(r)["value"]

def api(path,payload=None):
 req=Request(BASE+path,data=None if payload is None else json.dumps(payload).encode(),headers={"X-GitHub-OIDC":oidc(),"Content-Type":"application/json"})
 with urlopen(req,timeout=120) as r:return json.load(r)

def live_prices(job):
 region=job.get("region","EU").upper()
 url="https://bdolytics.com/api/trpc/market.getMarket?"+urlencode({"input":json.dumps({"language":"en","region":region})})
 req=Request(url,headers={"User-Agent":"bdo-worker-optimizer/1.0","Accept":"application/json"})
 with urlopen(req,timeout=60) as r: rows=json.load(r).get("result",{}).get("data",[])
 tax=float(job.get("marketTax",0.845))
 prices={int(row["itemId"]):int(float(row["price"])*tax) for row in rows if row.get("itemId") is not None and row.get("price") is not None}
 if not prices: raise RuntimeError("EU market source returned no usable prices")
 return prices

def main():
 job=api(f"/api/optimizer/jobs/{JOB}/input")
 config=deepcopy(optimize_config); config["budget"]=int(job.get("cpBudget",300)); config["solver"]=deepcopy(solver_config); config["solver"]["time_limit"]=int(job.get("timeLimitSeconds",7200)); config["solver"]["mip_improvement_timeout"]=int(job.get("improvementTimeoutSeconds",900))
 lodging=deepcopy(lodging_specifications)
 for town,values in job.get("lodging",{}).items():
  if town in lodging: lodging[town].update(values)
 prices=live_prices(job)
 data=generate_reference_data(config,prices,job.get("modifiers",{}),lodging,job.get("forcedNodeIds",[])); data=generate_graph_data(data); data["base_empire"]=job.get("baseEmpire") if job.get("lockCurrentEmpire") else None
 result=generate_workerman_data(optimize(data,SolverController()),lodging,data)
 output={"empire":result,"pricing":{"source":"bdolytics","region":job.get("region","EU"),"pricedItems":len(prices),"tax":job.get("marketTax",0.845)}}
 Path("optimized_empire.json").write_text(json.dumps(output,indent=2))
 api(f"/api/optimizer/jobs/{JOB}/result",{"status":"completed",**output})

if __name__=="__main__":
 try:main()
 except Exception as exc:
  try:api(f"/api/optimizer/jobs/{JOB}/result",{"status":"failed","error":str(exc)[:1000]})
  finally:raise

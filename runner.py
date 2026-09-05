import json, os
from datetime import datetime, timezone
from math import isfinite
from copy import deepcopy
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from bdo_empire.generate_graph_data import generate_graph_data
from bdo_empire.generate_reference_data import generate_reference_data, update_workerman_data
import bdo_empire.data_store as ds
from bdo_empire.generate_workerman_data import generate_workerman_data
from bdo_empire.main import lodging_specifications, optimize_config, solver_config
from bdo_empire.optimize_highspy import optimize
from bdo_empire.solver_highspy import SolverController

BASE=os.environ["DASHBOARD_URL"].rstrip("/"); JOB=os.environ["JOB_ID"]

def oidc():
 req=Request(os.environ["ACTIONS_ID_TOKEN_REQUEST_URL"]+"&audience="+quote(BASE),headers={"Authorization":"Bearer "+os.environ["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]})
 with urlopen(req) as r:return json.load(r)["value"]

def api(path,payload=None):
 body=dict(payload or {})
 body["_runnerToken"]=oidc()
 req=Request(BASE+path,data=json.dumps(body).encode(),headers={"Content-Type":"application/json","OAI-Sites-Authorization":"Bearer "+os.environ["BDO_SITES_API_TOKEN"]},method="POST")
 with urlopen(req,timeout=120) as r:return json.load(r)

# Reviewed Workerman snapshots, not executable remote pricing rules.
# https://github.com/shrddr/workermanjs/blob/main/data/manual/calculated_prices.json
# Blob e62b2211e329c396df7466a15ac6c1a2a282841a; reviewed 2026-09-05.
CALCULATED_PRICES = {
 9071: {9069: 0.2},
 1024: {5205: 1},
 1025: {4204: 1/6, 4401: 1/6, 4402: 1/6, 4403: 1/6, 4404: 1/6, 4405: 1/6},
 1026: {5960: 1},
 1027: {4476: 0.2, 4477: 0.2, 6504: 0.2, 6505: 0.2, 6506: 0.2},
}
# https://github.com/shrddr/workermanjs/blob/main/data/manual/vendor_prices.json
# Blob de534731f42d11e6155f18b055a173758e32b80e; reviewed 2026-09-05.
VENDOR_PRICES = {8012:0,8013:0,8014:0,8015:0,8022:1000,8027:0,8028:0,8029:0,8030:0,8933:20000,42418:3000000,44035:16,44065:125,44118:100,44119:19,44121:25,44141:65,44179:840,44230:1440,44250:150,44253:300,44254:150,44255:262,44256:300,44257:412,44258:150,44259:382,44260:900,44287:100,44288:100,44356:620,44357:760,44406:64,752023:51000,9071:0,1024:0,1025:0,1026:0,1027:0,65267:1000000,820035:50000,820036:50000,820037:50000,820038:50000,820039:50000}

def resolve_prices(rows, drops, tax):
 """Resolve unit sale values; node yields/luck remain the upstream model's job."""
 tax=float(tax)
 if not isfinite(tax) or not 0 <= tax <= 1:
  raise ValueError("marketTax must be a finite retention multiplier between 0 and 1")
 market={}
 for row in rows:
  try:
   item=int(row["itemId"]); value=float(row["price"])
  except (KeyError, TypeError, ValueError, OverflowError):
   continue
  # A zero market quote is unknown, not a verified zero-value item.
  if item > 0 and isfinite(value) and value > 0: market[item]=value
 if not market: raise RuntimeError("Market source returned no usable positive prices")
 prices={}; methods={}; unresolved={}
 def resolve(item, visiting=()):
  if item in prices: return prices[item]
  if item in visiting: raise ValueError("Cyclic calculated-price rule")
  if item in CALCULATED_PRICES:
   components={k:resolve(k, visiting+(item,)) for k in CALCULATED_PRICES[item]}
   missing=[k for k,v in components.items() if v is None]
   if missing:
    unresolved[item]=missing
    return None  # Never disguise incomplete contents as a vendor zero.
   value=sum(components[k]*q for k,q in CALCULATED_PRICES[item].items())
   method="calculated_contents"
  elif item in market:
   value=market[item] if item in VENDOR_PRICES else market[item]*tax
   method="market_untaxed" if item in VENDOR_PRICES else "market_after_tax"
  elif item in VENDOR_PRICES:
   value=VENDOR_PRICES[item]; method="vendor"
  else:
   return None
  prices[item]=value; methods[item]=method
  return value
 groups=("lucky","unlucky","unlucky_gi")
 by_node={str(node):{int(k) for g in groups for k in drop.get(g,{})} for node,drop in drops.items()}
 required=set().union(*by_node.values()) if by_node else set()
 missing=[item for item in sorted(required) if resolve(item) is None]
 # Keep the numerical solver operational, but preserve the semantic distinction.
 for item in missing: prices[item]=0; methods[item]="unresolved_zero_fallback"
 affected={node:sorted(items.intersection(missing)) for node,items in by_node.items() if items.intersection(missing)}
 diagnostics={
  "logicVersion":2,"source":"bdolytics","fetchedAt":datetime.now(timezone.utc).isoformat(),
  "pricedItems":len(market),"requiredDropItems":len(required),"resolvedDropItems":len(required)-len(missing),
  "missingItemIds":missing,"coverageComplete":not missing,"affectedNodeIds":sorted(affected),
  "affectedNodes":affected,"missingDependencies":unresolved,"tax":tax,
  "calculatedRulesBlob":"e62b2211e329c396df7466a15ac6c1a2a282841a",
  "vendorRulesBlob":"de534731f42d11e6155f18b055a173758e32b80e",
  "items":{str(k):{"value":prices[k],"method":methods[k],**({"contents":CALCULATED_PRICES[k]} if k in CALCULATED_PRICES else {})} for k in sorted(required)},
  "warning":"Unresolved items valued at zero; affected nodes may be undervalued." if missing else None,
 }
 return prices,diagnostics

def live_prices(job, drops):
 region=job.get("region","EU").upper()
 url="https://bdolytics.com/api/trpc/market.getMarket?"+urlencode({"input":json.dumps({"language":"en","region":region})})
 req=Request(url,headers={"User-Agent":"bdo-worker-optimizer/1.0","Accept":"application/json"})
 with urlopen(req,timeout=60) as r: rows=json.load(r).get("result",{}).get("data",[])
 if not isinstance(rows,list): raise RuntimeError("Market source returned an unexpected format")
 prices,diagnostics=resolve_prices(rows,drops,job.get("marketTax",0.845))
 diagnostics["region"]=region
 return prices,diagnostics

def main():
 if not os.environ.get("BDO_SITES_API_TOKEN"):
  raise RuntimeError("Missing Actions secret BDO_SITES_API_TOKEN. Complete dashboard /runner-setup before running.")
 job=api(f"/api/optimizer/jobs/{JOB}/input",{})
 config=deepcopy(optimize_config); config["budget"]=int(job.get("cpBudget",300)); config["solver"]=deepcopy(solver_config); config["solver"]["time_limit"]=int(job.get("timeLimitSeconds",7200)); config["mip_improvement_timeout"]=int(job.get("improvementTimeoutSeconds",900))
 lodging=deepcopy(lodging_specifications)
 for town,values in job.get("lodging",{}).items():
  if town in lodging: lodging[town].update(values)
 update_workerman_data()
 prices,pricing=live_prices(job,ds.read_json("plantzone_drops.json"))
 print(f"Pricing coverage: {pricing['resolvedDropItems']}/{pricing['requiredDropItems']}; unresolved zero fallbacks: {pricing['missingItemIds']}",flush=True)
 data=generate_reference_data(config,prices,job.get("modifiers",{}),lodging,job.get("forcedNodeIds",[])); data=generate_graph_data(data); data["base_empire"]=job.get("baseEmpire") if job.get("lockCurrentEmpire") else None
 result=generate_workerman_data(optimize(data,SolverController()),lodging,data)
 output={"empire":result,"pricing":pricing}
 Path("optimized_empire.json").write_text(json.dumps(output,indent=2))
 api(f"/api/optimizer/jobs/{JOB}/result",{"status":"completed",**output})

if __name__=="__main__":
 try:main()
 except Exception as exc:
  try:
   if os.environ.get("BDO_SITES_API_TOKEN"):
    api(f"/api/optimizer/jobs/{JOB}/result",{"status":"failed","error":str(exc)[:1000]})
  except Exception:
   print("Could not report failure to dashboard; the original exception follows.")
  raise

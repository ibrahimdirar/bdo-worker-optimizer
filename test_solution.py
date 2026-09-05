"""Exercise export against real pinned bdo-empire, Rustworkx and HiGHS APIs."""
import ast,json,unittest
from math import isfinite
from pathlib import Path
from unittest.mock import patch
import rustworkx as rx
from highspy import Highs,HighsVarType

source=Path(__file__).with_name('runner-next.py')
if not source.exists():source=source.with_name('runner.py')
tree=ast.parse(source.read_text())
scope={'isfinite':isfinite}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='export_solution'],type_ignores=[]),'export','exec'),scope)
export=scope['export_solution']

class SolutionTests(unittest.TestCase):
 def test_shared_connections_lodging_routes_workers_and_quality(self):
  g=rx.PyDiGraph()
  root=g.add_node({'waypoint_key':301,'region_key':7,'capacity_cost':[0,0,2],'need_exploration_point':0})
  mid=g.add_node({'waypoint_key':400,'need_exploration_point':2})
  a=g.add_node({'waypoint_key':451,'need_exploration_point':1})
  b=g.add_node({'waypoint_key':452,'need_exploration_point':1})
  for t in (a,b):g.add_edge(t,mid,None)
  g.add_edge(mid,root,None)
  h=Highs();h.setOptionValue('output_flag',False)
  xs={i:h.addVariable(lb=1,ub=1,type=HighsVarType.kInteger) for i in g.node_indices()}
  assignments={(t,root):h.addVariable(lb=1,ub=1,type=HighsVarType.kInteger,obj=100) for t in (a,b)}
  h.setMaximize();h.run()
  v={'x':xs,'x_t_r':assignments,'farm_r':{}}
  data={'solver_graph':g,'config':{'budget':6,'top_n':5,'nearest_n':5},'base_empire':None,
    'force_active_node_ids':[],'affiliated_town_region':{7:301},'exploration':{301:{'is_worker_npc_town':True}},
    'region_strings':{7:'Heidel'},'exploration_strings':{301:'Heidel',400:'Quarry',451:'Iron',452:'Copper'},
    'plant_values':{k:{7:{'value':100,'worker_data':{'charkey':7572,'wspd':150,'mspd':7,'luck':20,'skills':[1001]}}} for k in (451,452)}}
  with patch('bdo_empire.generate_workerman_data.ds.read_strings_csv',return_value={7:'Heidel'}),patch('bdo_empire.generate_workerman_data.print_summary'):
   empire,s=export((h,v),{'Heidel':{'bonus':0,'reserved':0,'prepaid':0}},data)
  self.assertEqual(len(empire['userWorkers']),2)
  self.assertEqual(s['cp'],{'lodging':2,'production':2,'connections':2,'total':6})
  self.assertEqual(s['routes'][0]['nodeIds'],[301,400,451])
  self.assertEqual(s['routes'][1]['nodeIds'],[301,400,452])
  self.assertEqual(s['quality']['status'],'Optimal')
  self.assertEqual(s['estimatedDailySilver'],200)
  self.assertEqual(s['quality']['relativeGap'],0)
  self.assertEqual(s['towns'][0]['workers'],2)
  self.assertEqual(empire['userWorkers'][0]['skills'],[1001])
  json.dumps(s,allow_nan=False)

if __name__=='__main__':unittest.main()

"""Pure pricing tests: no solver installation, credentials, or network required."""
import ast
import unittest
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path

# Load only the pure pricing unit; runner's imports/env are intentionally excluded.
tree = ast.parse(Path(__file__).with_name('runner.py').read_text())
nodes = [n for n in tree.body if
         isinstance(n, ast.FunctionDef) and n.name == 'resolve_prices' or
         isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in
         ('CALCULATED_PRICES', 'VENDOR_PRICES') for t in n.targets)]
scope = dict(datetime=datetime, timezone=timezone, isfinite=isfinite)
exec(compile(ast.Module(body=nodes, type_ignores=[]), 'pricing', 'exec'), scope)
resolve = scope['resolve_prices']

def run_prices(values, items, tax=0.845):
    return resolve([dict(itemId=k, price=v) for k,v in values.items()],
                   {'node': {'lucky': {str(k): 1 for k in items}}}, tax)

class PricingTests(unittest.TestCase):
    def test_farmer_tax_once(self):
        p,d = run_prices({5205:100000}, [1024])
        self.assertEqual(p[1024], 84500)
        self.assertTrue(d['coverageComplete'])
        self.assertEqual(d['items']['1024']['method'], 'calculated_contents')

    def test_all_calculated_rules(self):
        for item,parts in scope['CALCULATED_PRICES'].items():
            with self.subTest(item=item):
                values={k:(i+1)*100 for i,k in enumerate(parts)}
                p,_=run_prices(values,[item])
                self.assertAlmostEqual(p[item],sum(values[k]*q*.845 for k,q in parts.items()))

    def test_incomplete_container_is_unknown_not_vendor_zero(self):
        p,d=run_prices({4401:100}, [1025])
        self.assertEqual(p[1025],0)
        self.assertEqual(d['missingItemIds'],[1025])
        self.assertIn(4204,d['missingDependencies'][1025])
        self.assertEqual(d['affectedNodes'],{'node':[1025]})

    def test_vendor_untaxed_and_known_zero(self):
        p,d=run_prices({5205:100},[8022,8012])
        self.assertEqual(p[8022],1000)
        self.assertEqual(p[8012],0)
        self.assertTrue(d['coverageComplete'])

    def test_invalid_quotes(self):
        p,d=run_prices({1:0,2:-2,3:float('nan'),4:float('inf'),5:'bad',6:100},[1,2,3,4,5,6])
        self.assertEqual(d['missingItemIds'],[1,2,3,4,5])
        self.assertEqual(p[6],84.5)

    def test_invalid_tax(self):
        for tax in [-1,1.1,float('nan'),float('inf')]:
            with self.assertRaises(ValueError): run_prices({5205:100},[1024],tax)

    def test_empty_feed_fails(self):
        with self.assertRaises(RuntimeError): run_prices({},[1024])

    def test_drop_groups_and_no_input_mutation(self):
        drops={'a':{'lucky':{'1024':1},'unlucky':{'999':2},'unlucky_gi':{'998':3}}}
        _,d=resolve([{'itemId':5205,'price':100}],drops,1)
        self.assertEqual(d['requiredDropItems'],3)
        self.assertEqual(d['missingItemIds'],[998,999])
        self.assertEqual(drops['a']['lucky'],{'1024':1})

if __name__ == '__main__': unittest.main()

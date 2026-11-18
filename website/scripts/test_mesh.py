"""Check the published diagrams and the three synthetic traffic states."""
import json
import re
import unittest
from xml.etree import ElementTree as ET

from diagrams import NETWORK, SCENES, ROOT, mesh, road, traffic_state


class SharingDiagram(unittest.TestCase):
    def test_both_pictures_show_the_supplied_graph(self):
        edges={tuple(pair) for pair in NETWORK['edges']}
        self.assertEqual((len(NETWORK['nodes']),len(edges)),(6,9))
        for layout in SCENES:
            svg=ET.fromstring(mesh(layout))
            self.assertEqual({n.get('data-hut') for n in svg.iter() if 'data-hut' in n.attrib},set(NETWORK['nodes']))
            rendered={tuple(p.get('data-edge').split('-')):p.get('d') for p in svg.iter() if 'data-edge' in p.attrib}
            self.assertEqual(set(rendered),edges)
            for (a,b),path in rendered.items():
                numbers=list(map(float,re.findall(r'-?[\d.]+',path)))
                self.assertEqual(numbers[:2],SCENES[layout]['anchors'][a])
                self.assertEqual(numbers[-2:],SCENES[layout]['anchors'][b])

    def test_alternative_routes_and_each_single_hut_loss(self):
        graph={node:set() for node in NETWORK['nodes']}
        for a,b in NETWORK['edges']:
            graph[a].add(b)
            graph[b].add(a)
        for route in NETWORK['example_routes']:
            self.assertEqual((route[0],route[-1]),('A','F'))
            for a,b in zip(route,route[1:]):self.assertIn(b,graph[a])
        first,second=NETWORK['example_routes']
        self.assertFalse(set(first[1:-1]) & set(second[1:-1]))
        for missing in graph:
            remaining=set(graph)-{missing}
            stack=[next(iter(remaining))]
            reached=set(stack)
            while stack:
                neighbours=(graph[stack.pop()] & remaining)-reached
                reached.update(neighbours)
                stack.extend(neighbours)
            self.assertEqual(reached,remaining)


class RoadIllustration(unittest.TestCase):
    def test_direction_version_age_and_agreement(self):
        sample=json.loads((ROOT/'src/traffic-example.json').read_text())
        fresh=sample['segments'][0]['report']
        self.assertEqual(traffic_state(fresh,sample),'fresh')
        for change in ({'map':'Other edition'},{'direction':'uphill'},{'observations':1},{'conflicting':True}):
            self.assertEqual(traffic_state(dict(fresh,**change),sample),'unreported')
        self.assertEqual(traffic_state(fresh,dict(sample,at=fresh['expires'])),'expired')
        self.assertEqual(traffic_state(None,sample),'unreported')
        self.assertEqual([p.get('data-state') for p in ET.fromstring(road()).iter() if p.get('data-state')],['fresh','expired','unreported'])


if __name__=='__main__': unittest.main()

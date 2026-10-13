import unittest
from pflow_store_inflow_audit import audit_pair,point_key


def rows(destination_point,selected_city='23104',origin_purpose=1):
    return [{'person_id':1,'start':0,'purpose':origin_purpose,'gcode':'23104','lon':0,'lat':0},
            {'person_id':1,'start':3600,'purpose':100,'gcode':selected_city,'lon':destination_point,'lat':0}]


class StoreInflowTest(unittest.TestCase):
    locations={point_key(1,0):{'23104'},point_key(2,0):{'23105'},point_key(3,0):{'23104','23105'}}
    def test_selected_city_does_not_define_store_city(self):
        result=audit_pair(rows(1),rows(2),self.locations)
        self.assertEqual(result['home_outside_net_lower'],1)
        self.assertEqual(result['home_outside_net_upper'],1)
        self.assertEqual(result['after_counts']['known_home_outside_target_arrivals'],1)
        self.assertEqual(result['after_counts'].get('selected_city_target_arrivals',0),0)
        self.assertEqual(result['previous_store_cities_of_confirmed_entries'],{'23104':1})

    def test_identical_unknown_points_cancel(self):
        result=audit_pair(rows(4),rows(4),self.locations)
        self.assertEqual(result['home_outside_net_lower'],0)
        self.assertEqual(result['home_outside_net_upper'],0)
        self.assertEqual(result['before_counts']['unmapped_or_ambiguous'],1)

    def test_collocated_conflicting_addresses_remain_uncertain(self):
        result=audit_pair(rows(3),rows(3),self.locations)
        self.assertEqual(result['home_outside_net_lower'],-1)
        self.assertEqual(result['home_outside_net_upper'],1)

    def test_changed_unknown_points_are_bounded(self):
        result=audit_pair(rows(3),rows(2),self.locations)
        self.assertEqual(result['home_outside_net_lower'],0)
        self.assertEqual(result['home_outside_net_upper'],1)
        self.assertEqual(result['other_changes']['uncertain_changed_events'],1)

    def test_nonhome_origin_not_claimed_as_known_outside(self):
        result=audit_pair(rows(1,origin_purpose=200),rows(2,origin_purpose=200),self.locations)
        self.assertEqual(result['home_outside_net_lower'],0)
        self.assertEqual(result['after_counts']['retail_address_target_arrivals'],1)
        self.assertNotIn('known_home_outside_target_arrivals',result['after_counts'])

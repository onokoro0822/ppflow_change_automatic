import unittest
from pflow_city_inflow_audit import audit_pair,shopping_events


def rows(pid,origin,destination,start=3600):
    return [{'person_id':pid,'start':0,'purpose':0,'gcode':origin},
            {'person_id':pid,'start':start,'purpose':100,'gcode':destination}]


class InflowAuditTest(unittest.TestCase):
    def test_entries_exits_and_origin_change_reconcile(self):
        before=rows(1,'23104','23106')+rows(2,'23104','23105')+rows(3,'23105','23105')
        after=rows(1,'23104','23105')+rows(2,'23104','23106')+rows(3,'23104','23105')
        result=audit_pair(before,after)
        self.assertEqual(result['counts']['incoming_before'],1)
        self.assertEqual(result['counts']['incoming_after'],2)
        self.assertEqual(result['counts']['destination_entries'],1)
        self.assertEqual(result['counts']['destination_exits'],1)
        self.assertEqual(result['counts']['origin_classification_delta'],1)
        self.assertEqual(result['previous_destinations_of_entries'],{'23106':1})

    def test_unmatched_and_unknown_previous_are_not_discarded(self):
        before=rows(1,'23104','23105')
        after=rows(2,'23104','23105')+[{'person_id':3,'start':0,'purpose':100,'gcode':'23105'}]
        result=audit_pair(before,after)
        self.assertEqual(result['counts']['unmatched_before_shopping'],1)
        self.assertEqual(result['counts']['unmatched_after_shopping'],2)
        self.assertEqual(result['counts']['incoming_after'],1)

    def test_duplicate_activity_key_fails(self):
        with self.assertRaises(ValueError):shopping_events(rows(1,'23104','23105')*2)

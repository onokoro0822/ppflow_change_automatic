import unittest
from pflow_existing_facility_audit import audit_rows, model_point, summarize_rows


class ExistingFacilityAuditTest(unittest.TestCase):
    def test_shared_coordinate_is_reported_and_other_ward_excluded(self):
        config = {'name_aliases':['JR名古屋高島屋'], 'gcode':'23105'}
        rows = [{'DN':n,'ADDCD':g,'LON':'136.9','LAT':'35.1'} for n,g in
                [('ＪＲ名古屋高島屋','23105'),('別店舗','23105'),('JR名古屋高島屋','23104')]]
        result = audit_rows(rows, config)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['name_matched_rows'],1)
        self.assertEqual(result[0]['other_name_rows'],1)
        self.assertAlmostEqual(result[0]['longitude'],136.9-0.000293)
        self.assertAlmostEqual(result[0]['latitude'],35.1+0.000106950)

    def test_previous_activity_is_person_specific_and_unknown_preserved(self):
        point = {'longitude':136.9,'latitude':35.1}
        def row(pid,purpose,gcode):
            return {'person_id':pid,'purpose':purpose,'gcode':gcode,'lon':136.9,'lat':35.1}
        rows = [row(1,0,'23104'),row(2,0,'23105'),row(1,100,'23105'),row(2,100,'23105'),row(3,100,'23105')]
        result = summarize_rows(rows,[point],'23105')
        self.assertEqual(result['shopping_arrivals'],3)
        self.assertEqual(result['outside_ward'],1)
        self.assertEqual(result['same_ward'],1)
        self.assertEqual(result['unknown_previous'],1)
        self.assertEqual(result['outside_ward_share_known'],0.5)
        self.assertEqual(result['destination_gcodes'],{'23105':3})

    def test_missing_candidate_and_nonfinite_coordinates_fail(self):
        with self.assertRaises(ValueError):
            audit_rows([],{'name_aliases':['store'],'gcode':'23105'})
        with self.assertRaises(ValueError):
            model_point(float('nan'),35)

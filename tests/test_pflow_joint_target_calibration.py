import unittest
from pflow_joint_target_calibration import next_capacity, choose_best

class JointCalibrationTests(unittest.TestCase):
    def test_share_inverse_recovers_known_capacity(self):
        # Synthetic ceiling 300, competitor weight 1000, facility weight 500 -> 100 visits.
        f=next_capacity([{'capacity':500,'arrivals_sample':100}],150,1000)
        self.assertEqual(f,1000)
    def test_bracket_stays_between_actual_trials(self):
        history=[{'capacity':10000,'arrivals_sample':20},{'capacity':100000,'arrivals_sample':100}]
        f=next_capacity(history,60,10000)
        self.assertGreater(f,10000);self.assertLess(f,100000)
    def test_pt_fit_never_displaces_count_feasible_candidate(self):
        rows=[{'arrivals_full_equivalent':3000,'tvd_aichi':.4},
              {'arrivals_full_equivalent':6000,'tvd_aichi':.01}]
        self.assertEqual(choose_best(rows,3000,.1),rows[0])
    def test_zero_arrivals_does_not_divide_by_zero(self):
        self.assertEqual(next_capacity([{'capacity':10000,'arrivals_sample':0}],60,10000),40000)

if __name__=='__main__':unittest.main()

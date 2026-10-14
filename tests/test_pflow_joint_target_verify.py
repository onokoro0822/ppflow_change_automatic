import unittest
from pflow_joint_target_verify import refine_target

class FullRefinementTests(unittest.TestCase):
    def test_full_count_feedback_converges_for_capacity_share_model(self):
        ceiling,competing=21000,3220000
        initial={'target':3000,'capacity':505000,'arrivals_sample':round(ceiling*505000/(competing+505000))}
        def evaluate(capacity,iteration):
            return {'target':3000,'capacity':capacity,'arrivals_sample':round(ceiling*capacity/(competing+capacity))}
        history=refine_target(initial,evaluate,competing_capacity=competing)
        self.assertLessEqual(abs(history[-1]['arrivals_sample']-3000)/3000,.02)
        self.assertEqual(len(history),2)
    def test_completed_count_never_launches_extra_generation(self):
        def unexpected(*args):self.fail('No new generation should be launched')
        result=refine_target({'target':6000,'capacity':1610000,'arrivals_sample':5980},unexpected)
        self.assertEqual(len(result),1)
    def test_budget_stops_unreachable_target(self):
        def plateau(capacity,iteration):return {'target':3000,'capacity':capacity,'arrivals_sample':1000}
        result=refine_target({'target':3000,'capacity':1000,'arrivals_sample':1000},plateau,max_rounds=2)
        self.assertEqual(len(result),3)
        self.assertEqual(result[-1]['arrivals_sample'],1000)

if __name__=='__main__':unittest.main()

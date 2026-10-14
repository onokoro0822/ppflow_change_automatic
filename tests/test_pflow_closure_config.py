import unittest
from pflow_closure_config import excluded, ADDRESSES
class ClosureScopeTest(unittest.TestCase):
    def test_exact_scope_excludes_only_requested_industry_city_addresses(self):
        row=['']*22; row[5]=ADDRESSES[0];row[7]='23105';row[10]='3402001'
        self.assertTrue(excluded(row))
        for index,value in [(5,ADDRESSES[0]+'−６Ｆ'),(7,'23106'),(10,'3401')]:
            other=list(row);other[index]=value;self.assertFalse(excluded(other))
if __name__=='__main__': unittest.main()

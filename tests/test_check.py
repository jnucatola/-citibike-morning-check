import datetime as dt
import unittest
from unittest.mock import patch
import check

NOW = dt.datetime(2026, 11, 12, 12, 45, tzinfo=dt.timezone.utc)
TS = NOW.timestamp() - 45

class CitiBikeTests(unittest.TestCase):
    def test_distinguish_e_bikes(self):
        self.assertEqual(check.ebike_count({'num_ebikes_available':0,'num_bikes_available':12}, set()), 0)
        self.assertIsNone(check.ebike_count({'num_bikes_available':12}, set()))
        self.assertEqual(check.ebike_count({'vehicle_types_available':[{'vehicle_type_id':'e','count':4}, {'vehicle_type_id':'c','count':7}]}, {'e'}), 4)

    def test_nearest_station_only_when_primary_empty(self):
        info = [
            {'station_id':check.STATION_ID, 'name':check.STATION_NAME,'lat':40.683046,'lon':-74.003486},
            {'station_id':'near','name':'Near station','lat':40.684,'lon':-74.003},
            {'station_id':'far','name':'Far station','lat':40.690,'lon':-74.003},
        ]
        status = [
            {'station_id':check.STATION_ID, 'num_ebikes_available':0,'num_bikes_available':8,'is_renting':True,'last_reported':TS},
            {'station_id':'near', 'num_ebikes_available':1,'is_renting':True,'last_reported':TS},
            {'station_id':'far', 'num_ebikes_available':8,'is_renting':True,'last_reported':TS}
        ]
        result = check.select_stations(info,status,[],NOW)
        self.assertEqual(result['alternative']['name'],'Near station')
        self.assertEqual(result['total_bikes'],8)
        status[0]['num_ebikes_available']=2
        self.assertIsNone(check.select_stations(info,status,[],NOW)['alternative'])

    def test_reject_stale_inventory(self):
        record={'last_reported':NOW.timestamp()-3600,'is_renting':True}
        self.assertFalse(check.healthy(record,NOW))

if __name__ == '__main__':
    unittest.main()

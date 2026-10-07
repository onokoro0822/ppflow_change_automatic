import unittest

import pflow_combined_validation as validation


def activity(lon, lat, start, duration, purpose=100, age=35, gender=2):
    return {"person_id": 1, "age": age, "gender": gender, "labor": 1, "start": start,
            "duration": duration, "purpose": purpose, "lon": lon, "lat": lat, "gcode": "23105"}


class FeasibilityTest(unittest.TestCase):
    def test_travel_time_must_fit_previous_activity_and_stay(self):
        # About 11.1 km north: 33 minutes by car (20 km/h), 21 minutes by train (32 km/h).
        previous = activity(136.9, 35.1, 0, 30 * 60, purpose=1)
        shop = activity(136.9, 35.2, 30 * 60, 60 * 60)
        following = activity(136.9, 35.2, 90 * 60, 60, purpose=1)
        result = validation.feasibility([{"row": shop, "previous": previous, "next": following}])
        self.assertEqual(result["infeasible_share_car"], 1.0)
        self.assertEqual(result["infeasible_share_train"], 0.0)

    def test_outbound_trip_uses_shopping_duration(self):
        previous = activity(136.9, 35.2, 0, 3 * 3600, purpose=1)
        shop = activity(136.9, 35.2, 3 * 3600, 10 * 60)
        following = activity(136.9, 35.1, 3 * 3600 + 600, 60, purpose=1)
        result = validation.feasibility([{"row": shop, "previous": previous, "next": following}])
        self.assertEqual(result["infeasible_share_train"], 1.0)


class CompositionTest(unittest.TestCase):
    def test_age_bands_and_hours(self):
        self.assertEqual(validation.age_band(7), "10歳未満")
        self.assertEqual(validation.age_band(45), "40～49歳")
        self.assertEqual(validation.age_band(93), "80歳以上")
        arrivals = [
            {"row": activity(0, 0, 11 * 3600 + 5, 60, age=45, gender=1)},
            {"row": activity(0, 0, 11 * 3600 + 50, 60, age=72, gender=2)},
        ]
        shares = validation.composition(arrivals)
        self.assertEqual(shares["hour"], {"11": 1.0})
        self.assertEqual(shares["age_gender"], {"40～49歳|male": 0.5, "70～79歳|female": 0.5})
        self.assertEqual(
            validation.summarize_marginals(shares["age_gender"]),
            {"40～49歳": 0.5, "male": 0.5, "70～79歳": 0.5, "female": 0.5},
        )


if __name__ == "__main__":
    unittest.main()

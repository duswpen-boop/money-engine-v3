import os
import tempfile
import unittest

from backend.db import init_db
from backend.store import create_content, get_content, list_contents


class Phase1PersistenceTest(unittest.TestCase):
    def test_create_list_reopen_and_independent_status(self):
        with tempfile.TemporaryDirectory() as directory:
            previous = os.environ.get("MONEY_ENGINE_DB")
            os.environ["MONEY_ENGINE_DB"] = directory + "/test.sqlite3"
            try:
                init_db()
                created = create_content("[A급 신규]\n보은군 전기차 구매보조금 28대 추가")
                self.assertEqual(created["title"], "보은군 전기차 구매보조금 28대 추가")
                self.assertEqual(created["steps"][0]["status"], "COMPLETED")
                self.assertTrue(all(s["status"] == "PENDING" for s in created["steps"][1:]))
                self.assertEqual([i["role"] for i in created["images"]], ["COVER", "ACTION", "CONTEXT"])
                init_db()  # simulates another application startup
                self.assertEqual(list_contents("보은")[0]["id"], created["id"])
                self.assertEqual(get_content(created["id"])["input_source"], created["input_source"])
            finally:
                if previous is None:
                    del os.environ["MONEY_ENGINE_DB"]
                else:
                    os.environ["MONEY_ENGINE_DB"] = previous


if __name__ == "__main__":
    unittest.main()

from types import SimpleNamespace

from app import task_runner


def test_weekly_report_failure_is_reported_and_does_not_block_next_user(monkeypatch):
    users = [SimpleNamespace(id=1, username="first"), SimpleNamespace(id=2, username="second")]

    class FakeDB:
        def execute(self, _query):
            return self

        def scalars(self):
            return self

        def all(self):
            return users

        def rollback(self):
            pass

    def report(_db, user_id):
        if user_id == 1:
            raise RuntimeError("report failed")
        return SimpleNamespace(id=42)

    monkeypatch.setattr(task_runner, "weekly_report", report)
    result = task_runner.task_weekly_reports(FakeDB())
    assert result["reports"] == [{"user": "second", "report_id": 42}]
    assert result["errors"]
    assert task_runner._is_degraded(result)

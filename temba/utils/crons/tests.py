from unittest.mock import patch

from celery.app.task import Task

from temba.tests import TembaTest

from . import cron_task


class CronsTest(TembaTest):
    @patch("redis.client.StrictRedis.lock")
    @patch("redis.client.StrictRedis.get")
    def test_cron_task(self, mock_redis_get, mock_redis_lock):
        mock_redis_get.return_value = None
        task_calls = []

        @cron_task()
        def test_task1(foo, bar):
            task_calls.append("1-%d-%d" % (foo, bar))
            return {"foo": 1}

        @cron_task(name="task2", time_limit=100)
        def test_task2(foo, bar):
            task_calls.append("2-%d-%d" % (foo, bar))
            return 1234

        @cron_task(name="task3", time_limit=100, lock_timeout=55)
        def test_task3(foo, bar):
            task_calls.append("3-%d-%d" % (foo, bar))

        self.assertIsInstance(test_task1, Task)
        self.assertIsInstance(test_task2, Task)
        self.assertEqual(test_task2.name, "task2")
        self.assertEqual(test_task2.time_limit, 100)
        self.assertIsInstance(test_task3, Task)
        self.assertEqual(test_task3.name, "task3")
        self.assertEqual(test_task3.time_limit, 100)

        test_task1(11, 12)
        test_task2(21, bar=22)
        test_task3(foo=31, bar=32)

        mock_redis_get.assert_any_call("celery-task-lock:test_task1")
        mock_redis_get.assert_any_call("celery-task-lock:task2")
        mock_redis_get.assert_any_call("celery-task-lock:task3")
        mock_redis_lock.assert_any_call("celery-task-lock:test_task1", timeout=900)
        mock_redis_lock.assert_any_call("celery-task-lock:task2", timeout=100)
        mock_redis_lock.assert_any_call("celery-task-lock:task3", timeout=55)

        self.assertEqual(task_calls, ["1-11-12", "2-21-22", "3-31-32"])

        # simulate task being already running
        mock_redis_get.reset_mock()
        mock_redis_get.return_value = "xyz"
        mock_redis_lock.reset_mock()

        # try to run again
        test_task1(13, 14)

        # check that task is skipped
        mock_redis_get.assert_called_once_with("celery-task-lock:test_task1")
        self.assertEqual(mock_redis_lock.call_count, 0)
        self.assertEqual(task_calls, ["1-11-12", "2-21-22", "3-31-32"])

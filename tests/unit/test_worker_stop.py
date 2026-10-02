"""Stopping a background task: one still waiting is cancelled; a running one is "stopping",
and counts as at work, until its run() returns."""
import pytest

from cps.services.worker import (CalibreTask, WorkerThread, STAT_CANCELLED, STAT_ENDED, STAT_FAIL,
                                 STAT_FINISH_SUCCESS, STAT_STARTED, STAT_STOPPING, STAT_WAITING)

pytestmark = pytest.mark.unit


class _Task(CalibreTask):
    """Runs `work(task)`; finishes with success unless told to stop meanwhile."""

    def __init__(self, work=lambda task: None, cancellable=True):
        super().__init__("Working")
        self.work = work
        self.cancellable = cancellable
        self.seen = []

    def run(self, worker_thread):
        self.work(self)
        self.seen.append((self.stat, self.stop_requested, self.dead))
        if not self.stop_requested:
            self._handleSuccess()

    @property
    def name(self):
        return "Test task"

    @property
    def is_cancellable(self):
        return self.cancellable


def _worker(tasks):
    worker = WorkerThread.__new__(WorkerThread)  # no thread: only its task list is used
    worker.tasks_for_test = [(i, "admin", None, task, False) for i, task in enumerate(tasks)]
    return worker


@pytest.fixture
def end_task(monkeypatch):
    def end(*tasks, target):
        worker = _worker(tasks)
        monkeypatch.setattr(WorkerThread, "get_instance", classmethod(lambda cls: worker))
        monkeypatch.setattr(WorkerThread, "tasks", property(lambda self: self.tasks_for_test))
        worker.end_task(target.id)
        return worker
    return end


def test_a_waiting_task_is_cancelled_and_never_runs(end_task):
    task = _Task()
    end_task(task, target=task)
    assert task.stat == STAT_CANCELLED and task.stop_requested and task.dead
    # A task on a thread of its own is started regardless
    task.start(None)
    assert task.seen == [] and task.stat == STAT_CANCELLED and task.start_time is None


def test_a_running_task_is_stopping_until_its_run_returns(end_task):
    task = _Task(work=lambda task: end_task(task, target=task))
    assert task.stat == STAT_WAITING
    task.start(None)
    # While it finished its work: told to stop, but still counted as at work
    assert task.seen == [(STAT_STOPPING, True, False)]
    assert task.stat == STAT_ENDED and task.dead and task.end_time is not None


def test_a_stopping_task_is_still_an_active_task(end_task):
    task = _Task()
    task.stat = STAT_STARTED
    worker = end_task(task, target=task)
    assert task.stat == STAT_STOPPING
    assert worker.has_active_task_of_type("_Task")
    task.stat = STAT_ENDED
    assert not worker.has_active_task_of_type("_Task")


@pytest.mark.parametrize("finished_as", [STAT_FINISH_SUCCESS, STAT_FAIL, STAT_ENDED])
def test_a_task_already_over_keeps_how_it_finished(end_task, finished_as):
    # Ending the scheduled tasks used to relabel finished ones "Ended"
    task = _Task()
    task.stat = finished_as
    end_task(task, target=task)
    assert task.stat == finished_as


def test_only_the_task_named_and_only_a_cancellable_one_is_stopped(end_task):
    target, other, fixed = _Task(), _Task(), _Task(cancellable=False)
    end_task(target, other, fixed, target=target)
    end_task(target, other, fixed, target=fixed)
    assert (target.stat, other.stat, fixed.stat) == (STAT_CANCELLED, STAT_WAITING, STAT_WAITING)


def test_a_task_that_succeeds_despite_a_late_stop_is_a_success(end_task):
    def work(task):
        end_task(task, target=task)
        task._handleSuccess()  # its last piece of work was already done
    task = _Task(work=work)
    task.start(None)
    assert task.stat == STAT_FINISH_SUCCESS

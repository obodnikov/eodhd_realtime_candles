"""
Tests for TickDispatcher: which worker gets a tick, and the shared limit.

The pipeline-level proof that ticks stay in order is
TestTickOrdering in test_websocket_worker.py.
"""

import asyncio

import pytest

from src.websocket_worker import TickDispatcher


class TestWorkerChoice:

    def test_a_ticker_always_goes_to_the_same_worker(self):
        dispatcher = TickDispatcher(worker_count=100, maxsize=1000)

        assert len({dispatcher.worker_for('NVDA') for _ in range(50)}) == 1

    def test_the_choice_does_not_depend_on_case(self):
        """The engine treats nvda and NVDA as one ticker, so they must not split."""
        dispatcher = TickDispatcher(worker_count=100, maxsize=1000)

        assert dispatcher.worker_for('nvda') == dispatcher.worker_for('NVDA')

    def test_the_choice_is_the_same_on_every_run(self):
        """Python's hash() of a string changes per process; this must not."""
        first = TickDispatcher(worker_count=100, maxsize=1000).worker_for('NVDA')
        second = TickDispatcher(worker_count=100, maxsize=1000).worker_for('NVDA')

        assert first == second
        assert 0 <= first < 100

    def test_tickers_spread_across_the_workers(self):
        """Parallelism between tickers is the point of having several workers."""
        dispatcher = TickDispatcher(worker_count=100, maxsize=1000)
        tickers = [f'T{n}' for n in range(50)]

        assert len({dispatcher.worker_for(t) for t in tickers}) > 25

    def test_a_single_worker_takes_everything(self):
        dispatcher = TickDispatcher(worker_count=1, maxsize=1000)

        assert {dispatcher.worker_for(t) for t in ('NVDA', 'AMD', 'MU')} == {0}

    def test_at_least_one_worker_is_required(self):
        with pytest.raises(ValueError, match='worker_count'):
            TickDispatcher(worker_count=0, maxsize=1000)


class TestOrder:

    @pytest.mark.asyncio
    async def test_one_tickers_ticks_come_out_in_arrival_order(self):
        dispatcher = TickDispatcher(worker_count=8, maxsize=1000)
        worker = dispatcher.worker_for('NVDA')

        for stamp in range(100):
            dispatcher.put_nowait('NVDA', 200.0, 1, stamp)

        taken = [await dispatcher.get(worker) for _ in range(100)]
        assert [tick[3] for tick in taken] == list(range(100))
        assert taken[0] == ('NVDA', 200.0, 1, 0)

    @pytest.mark.asyncio
    async def test_a_worker_waits_for_its_own_tickers(self):
        dispatcher = TickDispatcher(worker_count=2, maxsize=1000)
        worker = dispatcher.worker_for('NVDA')
        waiting = asyncio.create_task(dispatcher.get(worker))
        await asyncio.sleep(0)
        assert not waiting.done()

        dispatcher.put_nowait('NVDA', 200.0, 1, 5)

        assert (await asyncio.wait_for(waiting, timeout=1))[3] == 5


class TestSharedLimit:

    @pytest.mark.asyncio
    async def test_the_limit_covers_all_workers_together(self):
        dispatcher = TickDispatcher(worker_count=8, maxsize=10)
        for n in range(10):
            dispatcher.put_nowait(f'T{n}', 1.0, 1, n)

        with pytest.raises(asyncio.QueueFull):
            dispatcher.put_nowait('T99', 1.0, 1, 99)

        assert dispatcher.qsize() == 10

    @pytest.mark.asyncio
    async def test_one_busy_ticker_may_use_the_whole_limit(self):
        """A burst in one stock at the open must not be cut to a share of the limit."""
        dispatcher = TickDispatcher(worker_count=100, maxsize=1000)

        for stamp in range(1000):
            dispatcher.put_nowait('NVDA', 200.0, 1, stamp)

        assert dispatcher.qsize() == 1000

    @pytest.mark.asyncio
    async def test_taking_a_tick_frees_room(self):
        dispatcher = TickDispatcher(worker_count=1, maxsize=1)
        dispatcher.put_nowait('NVDA', 200.0, 1, 1)
        with pytest.raises(asyncio.QueueFull):
            dispatcher.put_nowait('NVDA', 200.0, 1, 2)

        await dispatcher.get(0)
        dispatcher.put_nowait('NVDA', 200.0, 1, 3)

        assert dispatcher.qsize() == 1

    @pytest.mark.asyncio
    async def test_a_cancelled_wait_loses_nothing_and_keeps_the_count(self):
        """Worker shutdown cancels get(); the count must stay true."""
        dispatcher = TickDispatcher(worker_count=1, maxsize=10)
        waiting = asyncio.create_task(dispatcher.get(0))
        await asyncio.sleep(0)
        waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)

        assert dispatcher.qsize() == 0
        dispatcher.put_nowait('NVDA', 200.0, 1, 1)
        assert dispatcher.qsize() == 1
        assert (await dispatcher.get(0))[3] == 1

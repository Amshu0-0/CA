# The streaming windower must give exactly the windows that the NLP project evaluated on (logad.data.windows.sliding_windows).
# Random logs with ties, gaps, bursts and several window sizes are cut both ways and compared.

import random

import numpy as np

from engine import Item, Windower
from fakes import random_times
from logad.data.windows import sliding_windows


def stream(times, size, step):
    windower = Windower(size, step)
    found = []

    for offset, ts in enumerate(times):
        status, closed = windower.add(Item(offset, ts, 1, 0, 0, None))
        assert status == "ok"
        found.extend(closed)

    found.extend(windower.flush())
    return [(w.start, w.items[0].offset, w.items[-1].offset + 1) for w in found], windower


def batch(times, size, step):
    pairs, starts = sliding_windows(np.asarray(times, dtype=np.int64), size, step)
    return [(int(s), int(lo), int(hi)) for s, (lo, hi) in zip(starts, pairs)]


def test_same_windows_as_the_batch_function_on_random_logs():
    rng = random.Random(7)

    for case in range(600):
        size, step = rng.choice([(300, 60), (300, 60), (10, 3), (60, 60), (100, 7), (5, 1)])
        times = random_times(rng, rng.randrange(1, 160))
        assert stream(times, size, step)[0] == batch(times, size, step), (case, size, step, times)


def test_one_message_makes_one_window():
    assert stream([1000], 300, 60)[0] == [(1000, 0, 1)]


def test_windows_are_complete_only_when_a_later_line_arrives():
    windower = Windower(300, 60)
    assert windower.add(Item(0, 1000, 1, 0, 0, None)) == ("ok", [])
    assert windower.add(Item(1, 1299, 1, 0, 0, None)) == ("ok", [])
    status, closed = windower.add(Item(2, 1300, 1, 0, 0, None))
    assert [w.start for w in closed] == [1000]
    assert [i.offset for i in closed[0].items] == [0, 1]


def brute_force(times, order, size, step):
    """The same rules, written the slow and obvious way. Lines arrive in `order` (offsets into `times`). A window is handed out when a line
    with a time at or after its end has arrived. It holds every line that has arrived by then and lies inside it, in time order (equal
    times in arrival order). A line older than the oldest open window's start is not added."""
    origin = times[order[0]]
    arrived, arrival, newest, next_k, last, found = [], {}, None, 0, None, []

    def take(k):
        nonlocal last
        start = origin + k * step
        inside = sorted((o for o in arrived if start <= times[o] < start + size), key=lambda o: (times[o], arrival[o]))

        if inside and inside != last:
            found.append((start, inside))
            last = inside

    for position, offset in enumerate(order):
        if times[offset] < origin + next_k * step:
            continue

        arrival[offset] = position
        arrived.append(offset)
        newest = times[offset] if newest is None else max(newest, times[offset])

        while origin + next_k * step + size <= newest:
            take(next_k)
            next_k += 1

    # the end of the log
    while origin + next_k * step <= newest:
        take(next_k)
        next_k += 1

        if newest < origin + next_k * step - step + size:
            break

    return found


def test_lines_that_arrive_out_of_order_are_put_where_they_belong():
    rng = random.Random(11)
    reordered = 0

    for case in range(600):
        size, step = rng.choice([(300, 60), (10, 3), (60, 60), (100, 7), (5, 1)])
        times = random_times(rng, rng.randrange(2, 120))
        order = list(range(len(times)))

        # The first line of a replay decides where the window grid starts, so it stays first (the export script sorts the replay by time).
        for i in range(1, len(order) - 1):
            if rng.random() < 0.3 and times[order[i + 1]] - times[order[i]] <= 2 * size:
                order[i], order[i + 1] = order[i + 1], order[i]

        windower = Windower(size, step)
        found = []

        for offset in order:
            status, closed = windower.add(Item(offset, times[offset], 1, 0, 0, None))
            reordered += status == "reordered"
            found.extend(closed)

        found.extend(windower.flush())
        got = [(w.start, [i.offset for i in w.items]) for w in found]
        assert got == brute_force(times, order, size, step), (case, size, step, times, order)

    assert reordered > 500


def test_a_line_older_than_the_oldest_open_window_is_late_and_not_added():
    windower = Windower(300, 60)
    windower.add(Item(0, 1000, 1, 0, 0, None))
    windower.add(Item(1, 1700, 1, 0, 0, None))
    status, closed = windower.add(Item(2, 1100, 1, 0, 0, None))
    assert (status, closed) == ("late", [])


def test_after_the_end_nothing_more_is_accepted():
    windower = Windower(300, 60)
    windower.add(Item(0, 1000, 1, 0, 0, None))
    assert len(windower.flush()) == 1
    assert windower.add(Item(1, 1010, 1, 0, 0, None)) == ("late", [])
    assert windower.flush() == []


def test_bad_sizes_are_refused():
    for size, step in ((0, 60), (300, 0), (-1, 5)):
        try:
            Windower(size, step)
        except ValueError:
            continue

        raise AssertionError("a window size or step of zero or less must be refused")


def test_the_first_line_decides_where_the_grid_starts():
    # A replay must begin with its oldest line. If an older line arrives second, it is late: it cannot start a window grid of its own.
    windower = Windower(300, 60)
    windower.add(Item(0, 1000, 1, 0, 0, None))
    assert windower.add(Item(1, 990, 1, 0, 0, None)) == ("late", [])

import datetime

from wcs.qommon.misc import strftime


def test():
    # Make sure that the day names are in order
    # from 1/1/1800 until 1/1/2100
    s = strftime('%Y has the same days as 1980 and 2008', datetime.date(1800, 9, 23))
    if s != '1800 has the same days as 1980 and 2008':
        raise AssertionError(s)

    days = []
    for i in range(1, 10):
        days.append(datetime.date(2000, 1, i).strftime('%A'))
    nextday = {}
    for i in range(8):
        nextday[days[i]] = days[i + 1]

    startdate = datetime.date(1800, 1, 1)
    enddate = datetime.date(2100, 1, 1)
    prevday = strftime('%A', startdate)
    one_day = datetime.timedelta(1)

    testdate = startdate + one_day
    while testdate < enddate:
        day = strftime('%A', testdate)
        if nextday[prevday] != day:
            raise AssertionError(str(testdate))
        prevday = day
        testdate = testdate + one_day


def test_types():
    assert (
        strftime('%Y-%m-%d %H:%M:%S', datetime.datetime(2017, 11, 19, 13, 8, 0).timetuple())
        == '2017-11-19 13:08:00'
    )
    assert strftime('%Y-%m-%d %H:%M:%S', datetime.datetime(2017, 11, 19, 13, 8, 0)) == '2017-11-19 13:08:00'

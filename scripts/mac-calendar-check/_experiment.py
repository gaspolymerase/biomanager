"""TEMPORARY: does a timed event across the night the clocks change come back
from EventKit as it went in? With the event's time zone left alone, set to
the Mac's, or set to UTC."""
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.mac_calendar import Entry, EventKitStore  # noqa: E402

st = EventKitStore()
fd, ek = st.fd, st.ek
print("TZ env:", os.environ.get("TZ"), "system:", fd.NSTimeZone.systemTimeZone().name(),
      "default:", fd.NSTimeZone.defaultTimeZone().name(), "python:", datetime.now().astimezone().tzname())
src = next(s for s in st.store.sources() if int(s.sourceType()) == 0)
cal = st.create(str(src.sourceIdentifier()), "BioManager experiment")
# Oct 31 22:00 to Nov 1 06:00 in Los Angeles, as moments.
start, end = 1793509200, 1793541600
variants = {"left alone": None, "the Mac's": fd.NSTimeZone.localTimeZone(), "UTC": fd.NSTimeZone.timeZoneWithName_("UTC")}
for name, zone in variants.items():
    ev = ek.EKEvent.eventWithEventStore_(st.store)
    ev.setCalendar_(st.store.calendarWithIdentifier_(cal))
    ev.setTitle_(name)
    if zone is not None:
        ev.setTimeZone_(zone)
    ev.setStartDate_(fd.NSDate.dateWithTimeIntervalSince1970_(start))
    ev.setEndDate_(fd.NSDate.dateWithTimeIntervalSince1970_(end))
    ok, err = st.store.saveEvent_span_commit_error_(ev, ek.EKSpanThisEvent, True, None)
    print(name, "saved:", ok, err)
fresh = EventKitStore()
for handle, e in fresh.events(cal, date(2026, 10, 25), date(2026, 11, 10)):
    print(f"{e.title:12} start {e.start - start:+6d}  end {e.end - end:+6d}  zone {handle.timeZone()}")
st.remove_calendar(cal)

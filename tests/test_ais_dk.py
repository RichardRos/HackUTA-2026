import io
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))
import ais_dk  # noqa: E402

HEADER = ("# Timestamp,Type of mobile,MMSI,Latitude,Longitude,Navigational status,ROT,SOG,COG,Heading,IMO,Callsign,"
          "Name,Ship type,Cargo type,Width,Length,Type of position fixing device,Draught,Destination,ETA,"
          "Data source type,A,B,C,D")


def row(ts, mmsi="219000001", status="Under way using engine", sog="12.5", typ="Cargo", mob="Class A"):
    return (f"{ts},{mob},{mmsi},55.5,11.0,{status},,{sog},90.0,91,Unknown,OXAB,EXAMPLE,{typ},,32,229,GPS,"
            "9.1,HAMBURG,,AIS,,,,")


class ParseTest(unittest.TestCase):
    def test_keeps_one_moving_merchant_fix_per_vessel_hour(self):
        lines = [HEADER,
                 row("12/11/2026 00:00:05"),
                 row("12/11/2026 00:30:00"),                      # same vessel and hour: dropped
                 row("12/11/2026 01:00:00"),                      # next hour: kept
                 row("12/11/2026 00:00:00", status="Moored"),     # not under way
                 row("12/11/2026 00:00:00", mmsi="219000002", typ="Fishing"),
                 row("12/11/2026 00:00:00", mmsi="219000003", sog="0.4"),
                 row("12/11/2026 00:00:00", mmsi="219000004", mob="Class B")]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("aisdk-2026-11-12.csv", "\n".join(lines) + "\n")
        buf.seek(0)
        vessels, positions = ais_dk.parse(buf)
        self.assertEqual([(p[0].hour, p[1], p[4]) for p in positions], [(0, 219000001, 12.5), (1, 219000001, 12.5)])
        self.assertEqual(len(vessels), 1)
        mmsi, name, imo, callsign, _flag, _code, category, _hz, dest, _eta, length, beam, draught, _ = vessels[0]
        self.assertEqual((mmsi, name, imo, callsign, category, dest, length, beam, draught),
                         (219000001, "EXAMPLE", None, "OXAB", "cargo", "HAMBURG", 229, 32, 9.1))


if __name__ == "__main__":
    unittest.main()

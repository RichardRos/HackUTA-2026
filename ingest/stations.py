"""Balancing authorities and the weather stations that represent their load.

BA_TIMEZONES: IANA zone of each EIA-930 respondent's operating day (used by
eia_backfill_available_at). Respondents not listed default to America/New_York.

BA_STATIONS: ICAO airport stations per BA with rough load weights (sum to 1
per BA). Every station is in the NOAA NBM text bulletins and the NCEI ISD
archive, so it has forecasts (NBM + NWS) and observations (ISD + NWS).
Generation-only BAs (AVRN, DEAA, GRID, GRIF, GWA, HGMA, SEPA, WWA, YAD, GLHB)
and the EIA regions / US48 have no stations; weight them up from BAs if needed.
Weights are rough metro-population shares; tune freely.

ERCO keeps the point_ids from db/01_schema.sql (dfw, hou, sat, aus, maf).
Other points are named <ba>_<icao>, e.g. pjm_kphl.
"""

BA_TIMEZONES = {
    # Regions and national total
    "CAL": "America/Los_Angeles", "CAR": "America/New_York", "CENT": "America/Chicago",
    "FLA": "America/New_York", "MIDA": "America/New_York", "MIDW": "America/Chicago",
    "NE": "America/New_York", "NW": "America/Los_Angeles", "NY": "America/New_York",
    "SE": "America/Chicago", "SW": "America/Phoenix", "TEN": "America/Chicago",
    "TEX": "America/Chicago", "US48": "America/New_York",
    # Balancing authorities
    "AEC": "America/Chicago", "AECI": "America/Chicago", "AVA": "America/Los_Angeles",
    "AVRN": "America/Los_Angeles", "AZPS": "America/Phoenix", "BANC": "America/Los_Angeles",
    "BHBA": "America/Denver", "BPAT": "America/Los_Angeles", "CHPD": "America/Los_Angeles",
    "CISO": "America/Los_Angeles", "CPLE": "America/New_York", "CPLW": "America/New_York",
    "DEAA": "America/Phoenix", "DOPD": "America/Los_Angeles", "DUK": "America/New_York",
    "EEI": "America/Chicago", "EPE": "America/Denver", "ERCO": "America/Chicago",
    "FMPP": "America/New_York", "FPC": "America/New_York", "FPL": "America/New_York",
    "GCPD": "America/Los_Angeles", "GLHB": "America/Chicago", "GRID": "America/Los_Angeles",
    "GRIF": "America/Phoenix", "GVL": "America/New_York", "GWA": "America/Denver",
    "HGMA": "America/Phoenix", "HST": "America/New_York", "IID": "America/Los_Angeles",
    "IPCO": "America/Boise", "ISNE": "America/New_York", "JEA": "America/New_York",
    "LDWP": "America/Los_Angeles", "LGEE": "America/New_York",
    "MISO": "Etc/GMT+5",            # MISO runs on Eastern Standard Time all year
    "NEVP": "America/Los_Angeles", "NSB": "America/New_York", "NWMT": "America/Denver",
    "NYIS": "America/New_York", "PACE": "America/Denver", "PACW": "America/Los_Angeles",
    "PGE": "America/Los_Angeles", "PJM": "America/New_York", "PNM": "America/Denver",
    "PSCO": "America/Denver", "PSEI": "America/Los_Angeles", "SC": "America/New_York",
    "SCEG": "America/New_York", "SCL": "America/Los_Angeles", "SEC": "America/New_York",
    "SEPA": "America/New_York", "SIKE": "America/Chicago", "SOCO": "America/Chicago",
    "SPA": "America/Chicago", "SRP": "America/Phoenix", "SWPP": "America/Chicago",
    "SWPW": "America/Denver", "TAL": "America/New_York", "TEC": "America/New_York",
    "TEPC": "America/Phoenix", "TIDC": "America/Los_Angeles", "TPWR": "America/Los_Angeles",
    "TVA": "America/Chicago", "WACM": "America/Denver", "WALC": "America/Phoenix",
    "WAUW": "America/Denver", "WWA": "America/Denver", "YAD": "America/New_York",
}
DEFAULT_TZ = "America/New_York"

# ba -> [(icao, weight)]
BA_STATIONS = {
    "ERCO": [("KDFW", .40), ("KIAH", .35), ("KSAT", .13), ("KAUS", .12)],
    "PJM":  [("KPHL", .16), ("KEWR", .14), ("KBWI", .12), ("KIAD", .14), ("KORD", .16),
             ("KPIT", .07), ("KCMH", .07), ("KRIC", .08), ("KCVG", .06)],
    "MISO": [("KMSP", .18), ("KDTW", .20), ("KSTL", .12), ("KIND", .10), ("KMSY", .10),
             ("KLIT", .07), ("KMKE", .10), ("KDSM", .06), ("KJAN", .07)],
    "CISO": [("KLAX", .30), ("KSFO", .12), ("KSJC", .10), ("KSAN", .14), ("KFAT", .10),
             ("KONT", .14), ("KOAK", .10)],
    "NYIS": [("KJFK", .30), ("KLGA", .25), ("KISP", .12), ("KHPN", .08), ("KALB", .08),
             ("KBUF", .10), ("KSYR", .07)],
    "ISNE": [("KBOS", .45), ("KBDL", .20), ("KPVD", .13), ("KPWM", .08), ("KMHT", .08),
             ("KBTV", .06)],
    "SWPP": [("KOKC", .20), ("KTUL", .15), ("KMCI", .25), ("KICT", .12), ("KOMA", .15),
             ("KLBB", .07), ("KAMA", .06)],
    "SOCO": [("KATL", .55), ("KBHM", .15), ("KMOB", .08), ("KMGM", .07), ("KSAV", .08),
             ("KCSG", .07)],
    "TVA":  [("KBNA", .35), ("KMEM", .25), ("KTYS", .15), ("KCHA", .12), ("KHSV", .13)],
    "DUK":  [("KCLT", .55), ("KGSO", .25), ("KGSP", .20)],
    "CPLE": [("KRDU", .70), ("KILM", .15), ("KFAY", .15)],
    "CPLW": [("KAVL", 1.0)],
    "FPL":  [("KMIA", .35), ("KFLL", .25), ("KPBI", .20), ("KRSW", .10), ("KDAB", .10)],
    "FPC":  [("KPIE", .35), ("KMCO", .40), ("KOCF", .25)],
    "TEC":  [("KTPA", 1.0)],
    "FMPP": [("KMCO", .80), ("KLAL", .20)],
    "JEA":  [("KJAX", 1.0)],
    "TAL":  [("KTLH", 1.0)],
    "GVL":  [("KGNV", 1.0)],
    "SEC":  [("KLAL", .50), ("KOCF", .50)],
    "HST":  [("KHST", 1.0)],
    "NSB":  [("KEVB", 1.0)],
    "LGEE": [("KSDF", .65), ("KLEX", .35)],
    "AECI": [("KSGF", .50), ("KCOU", .30), ("KJLN", .20)],
    "AEC":  [("KDHN", 1.0)],
    "EEI":  [("KPAH", 1.0)],
    "SIKE": [("KCGI", 1.0)],
    "SPA":  [("KFSM", 1.0)],
    "SC":   [("KMYR", .50), ("KCHS", .50)],
    "SCEG": [("KCAE", .60), ("KCHS", .40)],
    "BPAT": [("KPDX", .30), ("KPSC", .25), ("KEUG", .25), ("KYKM", .20)],
    "PACW": [("KPDX", .35), ("KMFR", .25), ("KSLE", .20), ("KYKM", .20)],
    "PGE":  [("KPDX", .70), ("KSLE", .30)],
    "PSEI": [("KSEA", .50), ("KBFI", .20), ("KPAE", .30)],
    "SCL":  [("KBFI", 1.0)],
    "TPWR": [("KTCM", 1.0)],
    "AVA":  [("KGEG", .80), ("KLWS", .20)],
    "CHPD": [("KEAT", 1.0)],
    "DOPD": [("KEAT", 1.0)],
    "GCPD": [("KMWH", 1.0)],
    "IPCO": [("KBOI", .70), ("KTWF", .30)],
    "NWMT": [("KBIL", .35), ("KMSO", .25), ("KBZN", .20), ("KGTF", .20)],
    "PACE": [("KSLC", .65), ("KPVU", .15), ("KCPR", .10), ("KIDA", .10)],
    "PSCO": [("KDEN", .70), ("KCOS", .20), ("KGJT", .10)],
    "WACM": [("KCYS", .40), ("KGJT", .30), ("KPUB", .30)],
    "BHBA": [("KRAP", .60), ("KCYS", .40)],
    "SWPW": [("KBFF", 1.0)],
    "WAUW": [("KGGW", 1.0)],
    "AZPS": [("KPHX", .70), ("KFLG", .10), ("KNYL", .10), ("KPRC", .10)],
    "SRP":  [("KPHX", .50), ("KIWA", .30), ("KCHD", .20)],
    "TEPC": [("KTUS", 1.0)],
    "WALC": [("KIGM", 1.0)],
    "EPE":  [("KELP", .85), ("KLRU", .15)],
    "PNM":  [("KABQ", .80), ("KSAF", .20)],
    "NEVP": [("KLAS", 1.0)],
    "IID":  [("KIPL", .50), ("KTRM", .50)],
    "LDWP": [("KLAX", .40), ("KBUR", .40), ("KVNY", .20)],
    "BANC": [("KSAC", .60), ("KMCC", .20), ("KRDD", .20)],
    "TIDC": [("KMOD", 1.0)],
}

# Extra non-load points (weight 0) for wind/solar features
RENEWABLE_STATIONS = {
    "ERCO": ["KMAF"],
}

# ERCO points that already exist in db/01_schema.sql
LEGACY_POINT_IDS = {
    ("ERCO", "KDFW"): "dfw", ("ERCO", "KIAH"): "hou", ("ERCO", "KSAT"): "sat",
    ("ERCO", "KAUS"): "aus", ("ERCO", "KMAF"): "maf",
}

# Stations NCEI's GHCNh list files under a different ICAO
GHCN_ID_OVERRIDES = {
    "KCYS": "USW00024018",      # Cheyenne airport; listed as KFEW
}


def point_id(ba: str, icao: str) -> str:
    return LEGACY_POINT_IDS.get((ba, icao), f"{ba}_{icao}".lower())


def all_points():
    """(point_id, ba, icao, weight, purpose) for every configured point."""
    out = []
    for ba, sts in BA_STATIONS.items():
        for icao, w in sts:
            out.append((point_id(ba, icao), ba, icao, w, "load"))
    for ba, sts in RENEWABLE_STATIONS.items():
        for icao in sts:
            out.append((point_id(ba, icao), ba, icao, 0.0, "renewables"))
    return out

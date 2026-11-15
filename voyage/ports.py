"""Selectable ports and the voyage of Odysseus.

Port coordinates are the harbour itself; the router snaps each to the nearest navigable grid cell.
"""

# River harbours whose nearest grid water is the wrong sea: route from their real sea approach instead.
# Hamburg is 85 km up the Elbe; snapping the harbour picks Lübeck Bay on the Baltic, which drew a land crossing.
APPROACH = {(53.54, 9.97): (53.9, 8.7)}  # Hamburg -> Elbe mouth off Cuxhaven

# (id, name, country, lat, lon)
PORTS = [
    # East Asia
    ("cnsha", "Shanghai", "China", 31.23, 121.49), ("cnnbg", "Ningbo-Zhoushan", "China", 29.93, 121.85),
    ("cnszx", "Shenzhen", "China", 22.48, 113.88), ("cngzh", "Guangzhou (Nansha)", "China", 22.75, 113.65),
    ("cntao", "Qingdao", "China", 36.07, 120.32), ("cntxg", "Tianjin", "China", 38.98, 117.75),
    ("hkhkg", "Hong Kong", "China", 22.30, 114.17), ("twkhh", "Kaohsiung", "Taiwan", 22.61, 120.28),
    ("krpus", "Busan", "South Korea", 35.10, 129.04), ("jptyo", "Tokyo", "Japan", 35.62, 139.78),
    ("jpykk", "Yokohama", "Japan", 35.45, 139.65), ("jpukb", "Kobe", "Japan", 34.68, 135.20),
    # Southeast and South Asia
    ("sgsin", "Singapore", "Singapore", 1.26, 103.84), ("mypkg", "Port Klang", "Malaysia", 3.00, 101.39),
    ("mytpp", "Tanjung Pelepas", "Malaysia", 1.36, 103.55), ("idjkt", "Jakarta (Tanjung Priok)", "Indonesia", -6.10, 106.88),
    ("thlch", "Laem Chabang", "Thailand", 13.08, 100.88), ("vnsgn", "Ho Chi Minh City", "Vietnam", 10.77, 106.71),
    ("phmnl", "Manila", "Philippines", 14.58, 120.96), ("lkcmb", "Colombo", "Sri Lanka", 6.95, 79.84),
    ("innsa", "Nhava Sheva (Mumbai)", "India", 18.95, 72.95), ("inmaa", "Chennai", "India", 13.10, 80.30),
    ("pkkhi", "Karachi", "Pakistan", 24.84, 66.98), ("bdcgp", "Chittagong", "Bangladesh", 22.31, 91.80),
    # Middle East and Africa
    ("aejea", "Jebel Ali (Dubai)", "United Arab Emirates", 25.01, 55.06), ("omsll", "Salalah", "Oman", 16.94, 54.00),
    ("sajed", "Jeddah", "Saudi Arabia", 21.47, 39.17), ("egpsd", "Port Said", "Egypt", 31.26, 32.30),
    ("djjib", "Djibouti", "Djibouti", 11.60, 43.13), ("kemba", "Mombasa", "Kenya", -4.06, 39.67),
    ("tzdar", "Dar es Salaam", "Tanzania", -6.83, 39.29), ("zadur", "Durban", "South Africa", -29.87, 31.03),
    ("zacpt", "Cape Town", "South Africa", -33.91, 18.43), ("nglos", "Lagos (Apapa)", "Nigeria", 6.44, 3.37),
    ("citab", "Abidjan", "Côte d'Ivoire", 5.27, -4.00), ("sndkr", "Dakar", "Senegal", 14.68, -17.43),
    ("macas", "Casablanca", "Morocco", 33.61, -7.60), ("matng", "Tanger Med", "Morocco", 35.89, -5.50),
    # Europe
    ("nlrtm", "Rotterdam", "Netherlands", 51.95, 4.05), ("beanr", "Antwerp", "Belgium", 51.27, 4.33),
    ("deham", "Hamburg", "Germany", 53.54, 9.97), ("debrv", "Bremerhaven", "Germany", 53.56, 8.55),
    ("gbfxt", "Felixstowe", "United Kingdom", 51.95, 1.32), ("gbsou", "Southampton", "United Kingdom", 50.90, -1.40),
    ("frleh", "Le Havre", "France", 49.48, 0.11), ("frmrs", "Marseille", "France", 43.33, 5.33),
    ("esalg", "Algeciras", "Spain", 36.13, -5.44), ("esvlc", "Valencia", "Spain", 39.44, -0.31),
    ("esbcn", "Barcelona", "Spain", 41.35, 2.16), ("ptsie", "Sines", "Portugal", 37.95, -8.87),
    ("itgoa", "Genoa", "Italy", 44.40, 8.92), ("itgit", "Gioia Tauro", "Italy", 38.45, 15.90),
    ("ittrs", "Trieste", "Italy", 45.65, 13.76), ("mtmar", "Marsaxlokk", "Malta", 35.82, 14.55),
    ("grpir", "Piraeus", "Greece", 37.94, 23.62), ("trist", "Istanbul (Ambarlı)", "Turkey", 40.97, 28.69),
    ("dkcph", "Copenhagen", "Denmark", 55.70, 12.61), ("segot", "Gothenburg", "Sweden", 57.69, 11.85),
    ("nobgo", "Bergen", "Norway", 60.40, 5.31), ("plgdn", "Gdańsk", "Poland", 54.40, 18.67),
    ("ruled", "St Petersburg", "Russia", 59.88, 30.20),
    # Americas
    ("uslax", "Los Angeles", "United States", 33.73, -118.26), ("uslgb", "Long Beach", "United States", 33.75, -118.21),
    ("usoak", "Oakland", "United States", 37.80, -122.32), ("ussea", "Seattle", "United States", 47.60, -122.35),
    ("usnyc", "New York / New Jersey", "United States", 40.67, -74.05), ("ussav", "Savannah", "United States", 32.08, -81.09),
    ("ushou", "Houston", "United States", 29.73, -95.27), ("usmsy", "New Orleans", "United States", 29.93, -90.06),
    ("usmia", "Miami", "United States", 25.77, -80.17), ("usbal", "Baltimore", "United States", 39.26, -76.58),
    ("cavan", "Vancouver", "Canada", 49.29, -123.11),
    ("cahal", "Halifax", "Canada", 44.64, -63.56), ("mxzlo", "Manzanillo", "Mexico", 19.06, -104.31),
    ("mxver", "Veracruz", "Mexico", 19.21, -96.13), ("pabal", "Balboa (Panama)", "Panama", 8.95, -79.57),
    ("paonx", "Colón (Panama)", "Panama", 9.36, -79.90), ("cokto", "Cartagena", "Colombia", 10.40, -75.53),
    ("jmkin", "Kingston", "Jamaica", 17.98, -76.80), ("brssz", "Santos", "Brazil", -23.96, -46.30),
    ("brrio", "Rio de Janeiro", "Brazil", -22.89, -43.18), ("arbue", "Buenos Aires", "Argentina", -34.58, -58.37),
    ("uymvd", "Montevideo", "Uruguay", -34.90, -56.21), ("clvap", "Valparaíso", "Chile", -33.03, -71.63),
    ("pecll", "Callao", "Peru", -12.05, -77.15),
    # Oceania
    ("ausyd", "Sydney (Botany)", "Australia", -33.97, 151.22), ("aumel", "Melbourne", "Australia", -37.84, 144.92),
    ("aubne", "Brisbane", "Australia", -27.38, 153.17), ("aufre", "Fremantle", "Australia", -32.05, 115.74),
    ("nzakl", "Auckland", "New Zealand", -36.84, 174.77), ("fjsuv", "Suva", "Fiji", -18.13, 178.42),
    ("ushnl", "Honolulu", "United States", 21.31, -157.87),
]
PORTS_BY_ID = {p[0]: {"id": p[0], "name": p[1], "country": p[2], "lat": p[3], "lon": p[4]} for p in PORTS}

# Odysseus's homeward voyage, using the commonly proposed real-world sites for each episode.
# (id, place, episode, book of the Odyssey, lat, lon)
ODYSSEY = [
    ("troy", "Troy (Hisarlik)", "Departure from the fallen city", "Book 9", 39.96, 26.12),
    ("ismaros", "Ismaros, Thrace", "The raid on the Cicones", "Book 9", 40.86, 25.55),
    ("malea", "Cape Malea", "Storm drives the fleet off course", "Book 9", 36.43, 23.20),
    ("djerba", "Djerba", "The Lotus-Eaters", "Book 9", 33.80, 10.85),
    ("cyclops", "Eastern Sicily (Aci Trezza)", "Polyphemus the Cyclops", "Book 9", 37.56, 15.17),
    ("aeolia", "Aeolian Islands (Lipari)", "Aeolus and the bag of winds", "Book 10", 38.47, 14.96),
    ("laestrygon", "Bonifacio, Corsica", "The Laestrygonians", "Book 10", 41.38, 9.16),
    ("aiaia", "Monte Circeo", "Circe's island, Aiaia", "Book 10", 41.23, 13.06),
    ("sirens", "Li Galli islands", "The Sirens", "Book 12", 40.58, 14.43),
    ("scylla", "Strait of Messina", "Scylla and Charybdis", "Book 12", 38.25, 15.63),
    ("thrinacia", "Southern Sicily", "The cattle of Helios, Thrinacia", "Book 12", 36.70, 15.10),
    ("ogygia", "Gozo", "Seven years with Calypso on Ogygia", "Book 5", 36.04, 14.24),
    ("scheria", "Corfu", "The Phaeacians of Scheria", "Books 6-13", 39.62, 19.92),
    ("ithaca", "Ithaca", "Home", "Book 13", 38.37, 20.72),
]
ODYSSEY_STOPS = [{"id": s[0], "name": s[1], "episode": s[2], "book": s[3], "lat": s[4], "lon": s[5]} for s in ODYSSEY]

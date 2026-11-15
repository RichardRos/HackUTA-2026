import type { MonsterId } from './art'

/** A creature of Greek myth, pinned where the ancient sources put it. */
export type Monster = {
  kind: 'monster'
  id: MonsterId
  name: string
  greek: string
  translit: string
  epithet: string
  place: string
  lat: number
  lng: number
  foe: string
  source: string
  lore: string
  rank: number // 1 shows first when markers crowd each other
}

/** One stop on Odysseus' voyage home. */
export type Station = {
  kind: 'station'
  id: string
  n: number
  name: string
  greek: string
  book: string
  lat: number
  lng: number
  lore: string
}

export type GreekMarker = Monster | Station
export type LoreEntry = GreekMarker

export const isGreekMarker = (d: object): d is GreekMarker =>
  (d as GreekMarker).kind === 'monster' || (d as GreekMarker).kind === 'station'

export const MONSTERS: Monster[] = [
  {
    kind: 'monster', id: 'medusa', name: 'Medusa', greek: 'Μέδουσα', translit: 'Médousa', epithet: 'The Gorgon',
    place: 'Beyond the stream of Ocean, at the edge of Night', lat: 30.5, lng: -15.5, rank: 1,
    foe: 'Perseus, with Athena’s polished shield',
    source: 'Hesiod, Theogony 274–283 · Apollodorus 2.4.2',
    lore: 'The one mortal sister among the three Gorgons, with serpents for hair and a gaze that turned anyone who met it to stone. Perseus took her head by watching her reflection in a polished shield, and the winged horse Pegasus sprang from her neck.',
  },
  {
    kind: 'monster', id: 'cetus', name: 'Cetus', greek: 'Κῆτος Αἰθιοπικός', translit: 'Kêtos Aithiopikós', epithet: 'The sea monster of Aethiopia',
    place: 'Off the rocks of Joppa (Jaffa)', lat: 32.3, lng: 34.35, rank: 1,
    foe: 'Perseus',
    source: 'Apollodorus 2.4.3 · Ovid, Metamorphoses 4.663–739 · Strabo 16.2.28',
    lore: 'Poseidon sent the ketos to ravage the coast after Queen Cassiopeia boasted she was lovelier than the sea-nymphs. Her daughter Andromeda was chained to a rock at Joppa as its meal until Perseus killed it. Hollywood calls it the Kraken. The Greeks called it a ketos, and the constellation Cetus still swims beside Andromeda.',
  },
  {
    kind: 'monster', id: 'sirens', name: 'The Sirens', greek: 'Σειρῆνες', translit: 'Seirênes', epithet: 'Singers of the flowering meadow',
    place: 'The Sirenusai islets, off the Amalfi coast', lat: 40.58, lng: 14.43, rank: 1,
    foe: 'Odysseus, bound to his own mast',
    source: 'Homer, Odyssey 12.39–54, 12.158–200',
    lore: 'Painted on vases as birds with women’s heads, they sang from a meadow heaped with the bones of men who stopped to listen, promising to tell a sailor everything that happens on the earth. Odysseus stopped his crew’s ears with beeswax and had himself lashed to the mast so he could hear the song and live.',
  },
  {
    kind: 'monster', id: 'scylla', name: 'Scylla & Charybdis', greek: 'Σκύλλα καὶ Χάρυβδις', translit: 'Skúlla kai Khárubdis', epithet: 'The strait of two evils',
    place: 'The Strait of Messina', lat: 38.25, lng: 15.64, rank: 1,
    foe: 'None. Odysseus only got past them.',
    source: 'Homer, Odyssey 12.73–126, 12.234–259',
    lore: 'Scylla had six heads on long necks, each with three rows of teeth, and snatched six of Odysseus’ men from the deck. Across the strait, Charybdis sucked down the sea three times a day and spewed it out again. Odysseus chose to lose six men to Scylla rather than his whole ship to the whirlpool.',
  },
  {
    kind: 'monster', id: 'polyphemus', name: 'Polyphemus', greek: 'Πολύφημος', translit: 'Polúphēmos', epithet: 'The Cyclops',
    place: 'Island of the Cyclopes, by tradition Sicily below Etna', lat: 37.56, lng: 15.17, rank: 2,
    foe: 'Odysseus, calling himself “Nobody”',
    source: 'Homer, Odyssey 9.105–566',
    lore: 'A one-eyed shepherd, son of Poseidon, who shut Odysseus’ men in his cave and ate them two at a time. Odysseus gave his name as “Nobody”, made him drunk on unmixed wine and blinded him with a sharpened olive stake, then slipped out clinging under his rams. The Cyclops prayed to his father for revenge, which is why the voyage home took ten years.',
  },
  {
    kind: 'monster', id: 'minotaur', name: 'Minotaur', greek: 'Μινώταυρος', translit: 'Minṓtauros', epithet: 'The bull of Minos',
    place: 'The Labyrinth at Knossos, Crete', lat: 35.3, lng: 25.16, rank: 1,
    foe: 'Theseus, with Ariadne’s thread',
    source: 'Apollodorus 3.1.4, Epitome 1.7–9 · Plutarch, Theseus 15–19',
    lore: 'Son of Queen Pasiphaë and a bull from the sea, shut in the Labyrinth that Daedalus built for King Minos. Athens sent seven youths and seven maidens to be eaten until Theseus killed him and found his way out by the thread Ariadne gave him.',
  },
  {
    kind: 'monster', id: 'hydra', name: 'Lernaean Hydra', greek: 'Λερναία Ὕδρα', translit: 'Lernaía Húdra', epithet: 'Water serpent of the swamp',
    place: 'The springs of Lerna, in the Argolid', lat: 37.55, lng: 22.72, rank: 2,
    foe: 'Heracles and Iolaus (the Second Labour)',
    source: 'Hesiod, Theogony 313–318 · Apollodorus 2.5.2',
    lore: 'A nine-headed serpent whose middle head could not die. Each time Heracles cut off a head, two grew back, so his nephew Iolaus seared every stump with a torch. Heracles buried the immortal head under a rock and dipped his arrows in the Hydra’s poisonous blood.',
  },
  {
    kind: 'monster', id: 'cerberus', name: 'Cerberus', greek: 'Κέρβερος', translit: 'Kérberos', epithet: 'The hound of Hades',
    place: 'Cape Tainaron, a gateway to the underworld', lat: 36.39, lng: 22.48, rank: 2,
    foe: 'Heracles (the Twelfth Labour)',
    source: 'Hesiod, Theogony 310–312, 769–774 · Apollodorus 2.5.12',
    lore: 'Watchdog at the gates of Hades, who fawns on the dead as they enter and eats anyone who tries to leave. Hesiod gives him fifty heads; later poets settled on three, with a serpent’s tail. For his last Labour, Heracles carried him up into daylight through the cave at Tainaron.',
  },
  {
    kind: 'monster', id: 'keres', name: 'The Keres', greek: 'Κῆρες', translit: 'Kêres', epithet: 'Spirits of violent death',
    place: 'The plain of Troy', lat: 39.9, lng: 26.32, rank: 2,
    foe: 'Unbeaten. Each warrior’s own Ker came for him in the end.',
    source: 'Homer, Iliad 18.535–538 · Hesiod, Shield of Heracles 248–257',
    lore: 'Daughters of Night who haunt battlefields. Homer puts one on the shield of Achilles, dragging a dead man through the fighting by his feet, her cloak red with men’s blood. In Hesiod they gnash white teeth and claw at one another over every man who falls. We set them on the plain of Troy, the greatest battle of the heroic age.',
  },
  {
    kind: 'monster', id: 'chimera', name: 'Chimera', greek: 'Χίμαιρα', translit: 'Khímaira', epithet: 'The fire-breather of Lycia',
    place: 'Mount Chimaera in Lycia, where the rocks still burn', lat: 36.43, lng: 30.46, rank: 2,
    foe: 'Bellerophon, riding Pegasus',
    source: 'Homer, Iliad 6.179–183 · Hesiod, Theogony 319–325',
    lore: 'A lion in front, a serpent behind and a she-goat in the middle, breathing fire. Bellerophon shot her down from the back of Pegasus. Natural gas still burns from the rocks at Yanartaş, the place ancient writers linked to her.',
  },
  {
    kind: 'monster', id: 'ladon', name: 'Ladon', greek: 'Λάδων', translit: 'Ládōn', epithet: 'Guardian of the golden apples',
    place: 'Garden of the Hesperides, beneath Atlas', lat: 31.2, lng: -7.2, rank: 2,
    foe: 'Heracles (the Eleventh Labour)',
    source: 'Hesiod, Theogony 333–335 · Apollonius, Argonautica 4.1396–1407',
    lore: 'An unsleeping serpent coiled round the tree of golden apples that Earth gave Hera as a wedding gift, in the garden of the nymphs of evening at the western edge of the world. Heracles took the apples; the Argonauts arrived the next day to find the serpent dead beneath the tree.',
  },
  {
    kind: 'monster', id: 'dragon', name: 'Colchian Dragon', greek: 'Δράκων Κόλχος', translit: 'Drákōn Kólkhos', epithet: 'Warden of the Golden Fleece',
    place: 'The grove of Ares, Colchis', lat: 42.2, lng: 41.9, rank: 1,
    foe: 'Jason, with Medea’s spells',
    source: 'Apollonius, Argonautica 4.123–166 · Pindar, Pythian 4.244–246',
    lore: 'A serpent that never slept, coiled round the oak where the Golden Fleece hung. Pindar says it was longer than a fifty-oared ship. Medea sang it to sleep and sprinkled its eyes with a potion while Jason lifted the Fleece from the branch.',
  },
  {
    kind: 'monster', id: 'griffins', name: 'Griffins', greek: 'Γρῦπες', translit: 'Grûpes', epithet: 'Gold-guards of the far north-east',
    place: 'Beyond Scythia, by the Altai gold fields', lat: 49.5, lng: 87, rank: 1,
    foe: 'The one-eyed Arimaspians, forever',
    source: 'Herodotus, Histories 3.116, 4.13, 4.27 · Aeschylus, Prometheus Bound 803–806',
    lore: 'Eagle-headed lions that guard the gold of the far north-east against the one-eyed Arimaspians, who keep trying to steal it. Herodotus heard the story secondhand and doubted it. Some modern scholars link the legend to Scythian gold miners finding beaked dinosaur skulls in the deserts of Central Asia.',
  },
  {
    kind: 'monster', id: 'phoenix', name: 'Phoenix', greek: 'Φοῖνιξ', translit: 'Phoînix', epithet: 'Bird of the Sun',
    place: 'Heliopolis, the City of the Sun, Egypt', lat: 30.13, lng: 31.3, rank: 2,
    foe: 'None. It outlives everyone.',
    source: 'Herodotus, Histories 2.73',
    lore: 'The priests of Heliopolis told Herodotus of a sacred bird, red and gold and shaped like an eagle, that came from Arabia once every five hundred years, carrying its dead father sealed in an egg of myrrh to the temple of the Sun. He admits he only ever saw it in a painting. Rebirth from fire is a later addition.',
  },
  {
    kind: 'monster', id: 'sphinx', name: 'Sphinx', greek: 'Σφίγξ', translit: 'Sphínx', epithet: 'The strangler of Thebes',
    place: 'Mount Phikion, above Thebes', lat: 38.42, lng: 23.3, rank: 3,
    foe: 'Oedipus, who answered her riddle',
    source: 'Hesiod, Theogony 326 · Sophocles, Oedipus Tyrannus · Apollodorus 3.5.8',
    lore: 'A winged lion with a woman’s head who sat on a rock outside Thebes and devoured everyone who failed her riddle: what walks on four feet, then two, then three? Oedipus answered “man”, and she threw herself from the rock. The Naxians set up her statue on an Ionic column at Delphi.',
  },
]

const NUMERALS = ['Α', 'Β', 'Γ', 'Δ', 'Ε', 'Ϛ', 'Ζ', 'Η', 'Θ', 'Ι', 'ΙΑ', 'ΙΒ', 'ΙΓ', 'ΙΔ', 'ΙΕ']
/** Greek alphabetic numeral with its keraia mark, as a scribe would number a list: Αʹ, Βʹ, Γʹ… */
export const greekNumeral = (n: number) => `${NUMERALS[n - 1] ?? n}ʹ`

const stop = (n: number, id: string, name: string, greek: string, book: string, lat: number, lng: number, lore: string): Station =>
  ({ kind: 'station', id, n, name, greek, book, lat, lng, lore })

export const STATIONS: Station[] = [
  stop(1, 'troy', 'Troy', 'Ἴλιος', 'Odyssey 9.39', 39.957, 26.239,
    'After ten years of war, Odysseus sails for home with twelve ships. The trip should take a few weeks. It will take him ten years.'),
  stop(2, 'ismaros', 'Ismaros, city of the Cicones', 'Ἴσμαρος', 'Odyssey 9.39–61', 40.87, 25.52,
    'The crew sacks the city, then lingers to feast on the beach. The Cicones counter-attack at dawn and kill six men from every ship.'),
  stop(3, 'malea', 'Cape Malea', 'Μαλέα', 'Odyssey 9.80–81', 36.44, 23.2,
    'Rounding the cape, a north wind and the current drive the fleet off course for nine days, past the edge of the known world.'),
  stop(4, 'lotus', 'Land of the Lotus-eaters', 'Λωτοφάγοι', 'Odyssey 9.82–104', 33.8, 10.85,
    'Men who eat the honey-sweet lotus forget all about going home. Odysseus drags the weeping scouts back to the ships and ties them under the benches.'),
  stop(5, 'cyclopes', 'Island of the Cyclopes', 'Κύκλωπες', 'Odyssey 9.105–566', 37.56, 15.17,
    'Polyphemus traps them in his cave. “Nobody” blinds him, and from here on Poseidon’s anger follows Odysseus across every sea.'),
  stop(6, 'aeolia', 'Aeolia, the floating island', 'Αἰολίη', 'Odyssey 10.1–76', 38.79, 15.21,
    'Aeolus, keeper of the winds, gives Odysseus every storm wind tied up in an ox-hide bag. Within sight of Ithaca the crew opens it looking for treasure, and the gale blows them all the way back.'),
  stop(7, 'laestrygonians', 'Telepylos of the Laestrygonians', 'Λαιστρυγόνες', 'Odyssey 10.80–132', 41.39, 9.16,
    'Giant cannibals hurl boulders into the harbour and spear the sailors like fish. Eleven of the twelve ships are lost; only Odysseus’ own escapes.'),
  stop(8, 'aiaia', 'Aiaia, island of Circe', 'Αἰαίη', 'Odyssey 10.133–574', 41.23, 13.05,
    'The witch Circe turns half the crew into pigs. Protected by moly, a herb from Hermes, Odysseus resists her spell, frees his men and stays a year.'),
  stop(9, 'nekyia', 'The edge of the underworld', 'Νέκυια', 'Odyssey 11', 40.84, 14.07,
    'Odysseus digs a pit, pours out blood for the dead and hears the prophet Teiresias foretell the way home. Homer sets it at the edge of Ocean; later tradition put the entrance at Lake Avernus.'),
  stop(10, 'sirens', 'The Sirens', 'Σειρῆνες', 'Odyssey 12.158–200', 40.58, 14.43,
    'Bound to the mast while his crew row with their ears full of beeswax, Odysseus hears the Sirens’ song and lives.'),
  stop(11, 'strait', 'Scylla and Charybdis', 'Σκύλλα · Χάρυβδις', 'Odyssey 12.201–259', 38.25, 15.64,
    'Steering clear of the whirlpool, the ship passes under Scylla’s cliff. She takes six men, one for each mouth.'),
  stop(12, 'thrinacia', 'Thrinacia, the cattle of the Sun', 'Θρινακίη', 'Odyssey 12.260–419', 36.75, 15.05,
    'Becalmed and starving, the crew kill the sacred cattle of Helios. Zeus splits the ship with a thunderbolt and every man drowns except Odysseus.'),
  stop(13, 'ogygia', 'Ogygia, island of Calypso', 'Ὠγυγίη', 'Odyssey 5.55–268', 36.04, 14.24,
    'The nymph Calypso keeps him seven years and offers to make him immortal. He sits on the shore weeping for home until the gods order her to let him build a raft.'),
  stop(14, 'scheria', 'Scheria, land of the Phaeacians', 'Σχερίη', 'Odyssey 6–13', 39.62, 19.92,
    'Shipwrecked again, he is found on the beach by the princess Nausicaa. He tells the king his whole story, and the Phaeacians sail him home in a single night.'),
  stop(15, 'ithaca', 'Ithaca, home', 'Ἰθάκη', 'Odyssey 13–24', 38.43, 20.67,
    'Twenty years after he left (ten at war, ten at sea), Odysseus lands in disguise, strings his great bow, and wins back his house and Penelope. This is the nostos, the homecoming.'),
]

/** The voyage as sailed: the stations plus open-water turning points, so the line stays at sea. */
export const VOYAGE: { lat: number; lng: number }[] = [
  [39.957, 26.239], [40.3, 25.95], [40.87, 25.52], [39.6, 24.9], [38.5, 24.55], [37.2, 23.75], [36.3, 23.25],
  [35.6, 19.5], [34.6, 14.5], [33.8, 10.85], [35.3, 13.4], [36.45, 15.5], [37.1, 15.38], [37.56, 15.2],
  [37.95, 15.42], [38.22, 15.62], [38.42, 15.55], [38.79, 15.21], [39.6, 12.8], [40.6, 10.2], [41.39, 9.16],
  [41.05, 10.9], [41.23, 13.05], [40.95, 13.7], [40.84, 14.07], [40.55, 14.18], [40.58, 14.43], [39.4, 15.1],
  [38.25, 15.64], [37.5, 15.33], [36.75, 15.15], [36.3, 14.6], [36.04, 14.24], [36.8, 16.0], [37.9, 17.6],
  [39.2, 19.3], [39.62, 19.92], [39.2, 20.15], [38.72, 20.52], [38.43, 20.67],
].map(([lat, lng]) => ({ lat, lng }))

/** Ancient names for the lands under the cursor (the modern name always comes first). */
export const ANCIENT_LANDS: Record<string, string> = {
  '300': 'Hellas · Ἑλλάς',
  '380': 'Italia · Ἰταλία',
  '818': 'Aigyptos · Αἴγυπτος',
  '792': 'Anatolia · Ἀνατολή',
  '434': 'Libya · Λιβύη',
  '364': 'Persis · Περσίς',
  '368': 'Mesopotamia · Μεσοποταμία',
  '724': 'Iberia · Ἰβηρία',
  '250': 'Keltike · Κελτική',
  '804': 'Scythia · Σκυθία',
  '268': 'Colchis · Κολχίς',
  '788': 'Karchedon · Καρχηδών',
  '100': 'Thrace · Θρᾴκη',
  '356': 'Indike · Ἰνδική',
  '231': 'Aethiopia · Αἰθιοπία',
  '196': 'Kypros · Κύπρος',
  '422': 'Phoenicia · Φοινίκη',
  '826': 'Prettanike · Πρεττανική',
  '504': 'Maurousia · Μαυρουσία',
  '008': 'Illyria · Ἰλλυρία',
  '682': 'Arabia · Ἀραβία',
  '887': 'Arabia Eudaimon · Ἀραβία Εὐδαίμων',
  '004': 'Baktriane · Βακτριανή',
  '276': 'Germania · Γερμανία',
  '642': 'Dakia · Δακία',
  '860': 'Sogdiane · Σογδιανή',
}

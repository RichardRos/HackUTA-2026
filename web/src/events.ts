export type Trend = 'rising' | 'easing'

export type SignalEvent = {
  id: string
  kicker: string
  title: string
  value: string
  valueLabel: string
  body: string
  sources: string
  trend: Trend
  lat: number
  lng: number
  country: string // ISO 3166 numeric, matches world-atlas feature ids; empty when unknown
  query: string
  commodity?: Commodity // set on commodity futures markers
}

/** A row from GET /api/signals (mcp_server.get_region_signals). */
export type RegionSignal = {
  ba_code: string
  name: string | null
  hours: number
  surprise_pct: number
  lat: number
  lng: number
  as_of: string
}

const US = '840' // every EIA balancing authority is in the US

export function toEvent(r: RegionSignal): SignalEvent {
  const above = r.surprise_pct > 0
  const name = r.name ?? r.ba_code
  return {
    id: r.ba_code,
    kicker: `Grid / ${r.ba_code}`,
    title: name,
    value: `${above ? '+' : ''}${r.surprise_pct}%`,
    valueLabel: 'Demand vs EIA forecast',
    body: `Actual demand ran ${Math.abs(r.surprise_pct)}% ${above ? 'above' : 'below'} EIA's day-ahead forecast over the ${r.hours} hours to ${new Date(r.as_of).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}.`,
    sources: 'EIA-930 hourly demand / day-ahead forecast',
    trend: above ? 'rising' : 'easing',
    lat: r.lat,
    lng: r.lng,
    country: US,
    query: name,
  }
}

/** A row from GET /api/weather/global (next-hour NOAA GFS city forecast). */
export type GlobalWeather = {
  point_id: string
  name: string
  country: string
  latitude: number | null
  longitude: number | null
  valid_time: string | null
  issued_at: string | null
  temp_f: number | null
  apparent_temp_f: number | null
  wind_10m_mph: number | null
}

/** Natural Earth country names -> ISO code of the global-city weather feed (every country has a point). */
export const GLOBAL_WEATHER_COUNTRY_CODES: Record<string, string> = {
  'united arab emirates': 'AE', 'afghanistan': 'AF', 'albania': 'AL', 'armenia': 'AM', 'angola': 'AO',
  'antarctica': 'AQ', 'argentina': 'AR', 'austria': 'AT', 'australia': 'AU', 'azerbaijan': 'AZ',
  'bosnia and herz.': 'BA', 'bangladesh': 'BD', 'belgium': 'BE', 'burkina faso': 'BF', 'bulgaria': 'BG',
  'burundi': 'BI', 'benin': 'BJ', 'brunei': 'BN', 'bolivia': 'BO', 'brazil': 'BR', 'bahamas': 'BS',
  'bhutan': 'BT', 'botswana': 'BW', 'belarus': 'BY', 'belize': 'BZ', 'canada': 'CA', 'dem. rep. congo': 'CD',
  'democratic republic of the congo': 'CD', 'central african rep.': 'CF', 'congo': 'CG', 'switzerland': 'CH',
  "côte d'ivoire": 'CI', 'chile': 'CL', 'cameroon': 'CM', 'china': 'CN', 'colombia': 'CO', 'costa rica': 'CR',
  'cuba': 'CU', 'cyprus': 'CY', 'czechia': 'CZ', 'germany': 'DE', 'djibouti': 'DJ', 'denmark': 'DK',
  'dominican rep.': 'DO', 'algeria': 'DZ', 'ecuador': 'EC', 'estonia': 'EE', 'egypt': 'EG', 'w. sahara': 'EH',
  'eritrea': 'ER', 'spain': 'ES', 'ethiopia': 'ET', 'finland': 'FI', 'fiji': 'FJ', 'falkland is.': 'FK',
  'france': 'FR', 'gabon': 'GA', 'united kingdom': 'GB', 'georgia': 'GE', 'ghana': 'GH', 'greenland': 'GL',
  'gambia': 'GM', 'guinea': 'GN', 'eq. guinea': 'GQ', 'greece': 'GR', 'guatemala': 'GT', 'guinea-bissau': 'GW',
  'guyana': 'GY', 'hong kong': 'HK', 'honduras': 'HN', 'croatia': 'HR', 'haiti': 'HT', 'hungary': 'HU',
  'indonesia': 'ID', 'ireland': 'IE', 'israel': 'IL', 'india': 'IN', 'iraq': 'IQ', 'iran': 'IR',
  'iceland': 'IS', 'italy': 'IT', 'jamaica': 'JM', 'jordan': 'JO', 'japan': 'JP', 'kenya': 'KE',
  'kyrgyzstan': 'KG', 'cambodia': 'KH', 'north korea': 'KP', 'south korea': 'KR', 'kuwait': 'KW',
  'kazakhstan': 'KZ', 'laos': 'LA', 'lebanon': 'LB', 'sri lanka': 'LK', 'liberia': 'LR', 'lesotho': 'LS',
  'lithuania': 'LT', 'luxembourg': 'LU', 'latvia': 'LV', 'libya': 'LY', 'morocco': 'MA', 'moldova': 'MD',
  'montenegro': 'ME', 'madagascar': 'MG', 'macedonia': 'MK', 'mali': 'ML', 'myanmar': 'MM', 'mongolia': 'MN',
  'mauritania': 'MR', 'malawi': 'MW', 'mexico': 'MX', 'malaysia': 'MY', 'mozambique': 'MZ', 'namibia': 'NA',
  'new caledonia': 'NC', 'niger': 'NE', 'nigeria': 'NG', 'nicaragua': 'NI', 'netherlands': 'NL',
  'norway': 'NO', 'nepal': 'NP', 'new zealand': 'NZ', 'oman': 'OM', 'panama': 'PA', 'peru': 'PE',
  'papua new guinea': 'PG', 'philippines': 'PH', 'pakistan': 'PK', 'poland': 'PL', 'puerto rico': 'PR',
  'palestine': 'PS', 'portugal': 'PT', 'paraguay': 'PY', 'qatar': 'QA', 'romania': 'RO', 'serbia': 'RS',
  'russia': 'RU', 'rwanda': 'RW', 'saudi arabia': 'SA', 'solomon is.': 'SB', 'sudan': 'SD', 'sweden': 'SE',
  'singapore': 'SG', 'slovenia': 'SI', 'slovakia': 'SK', 'sierra leone': 'SL', 'senegal': 'SN',
  'somalia': 'SO', 'suriname': 'SR', 's. sudan': 'SS', 'el salvador': 'SV', 'syria': 'SY', 'eswatini': 'SZ',
  'chad': 'TD', 'fr. s. antarctic lands': 'TF', 'togo': 'TG', 'thailand': 'TH', 'tajikistan': 'TJ',
  'timor-leste': 'TL', 'turkmenistan': 'TM', 'tunisia': 'TN', 'turkey': 'TR', 'trinidad and tobago': 'TT',
  'taiwan': 'TW', 'tanzania': 'TZ', 'ukraine': 'UA', 'uganda': 'UG', 'united states of america': 'US',
  'united states': 'US', 'uruguay': 'UY', 'uzbekistan': 'UZ', 'venezuela': 'VE', 'vietnam': 'VN',
  'vanuatu': 'VU', 'yemen': 'YE', 'south africa': 'ZA', 'zambia': 'ZM', 'zimbabwe': 'ZW',
}

export function globalWeatherCountryCode(country: string): string | null {
  const name = country.trim().toLowerCase()
  return Object.prototype.hasOwnProperty.call(GLOBAL_WEATHER_COUNTRY_CODES, name) ? GLOBAL_WEATHER_COUNTRY_CODES[name] : null
}

const COUNTRY_IDS: Record<string, string> = {
  AE: '784', AO: '024', AR: '032', AT: '040', AU: '036', BD: '050', BE: '056', BR: '076', CA: '124', CD: '180', CH: '756',
  CL: '152', CN: '156', CO: '170', CU: '192', CZ: '203', DE: '276', DK: '208', DZ: '012', EG: '818', ES: '724',
  ET: '231', FI: '246', FR: '250', GB: '826', GH: '288', GR: '300', HK: '344', HU: '348', ID: '360', IE: '372',
  IL: '376', IN: '356', IQ: '368', IR: '364', IT: '380', JP: '392', KE: '404', KR: '410', KW: '414', KZ: '398',
  MA: '504', MX: '484', MY: '458', NG: '566', NL: '528', NO: '578', NZ: '554', PE: '604', PH: '608', PK: '586',
  PL: '616', PT: '620', QA: '634', RO: '642', RU: '643', SA: '682', SE: '752', SG: '702', TH: '764', TR: '792',
  TW: '158', UA: '804', US: '840', UZ: '860', VE: '862', VN: '704', ZA: '710', TZ: '834',
  FJ: '242', EH: '732', PG: '598', SO: '706', SD: '729', TD: '148', HT: '332', DO: '214', BS: '044', FK: '238', GL: '304', TF: '260', TL: '626', LS: '426', UY: '858', BO: '068', PA: '591', CR: '188', NI: '558', HN: '340', SV: '222', GT: '320', BZ: '084', GY: '328', SR: '740', EC: '218', PR: '630', JM: '388', ZW: '716', BW: '072', NA: '516', SN: '686', ML: '466', MR: '478', BJ: '204', NE: '562', CM: '120', TG: '768', CI: '384', GN: '324', GW: '624', LR: '430', SL: '694', BF: '854', CF: '140', CG: '178', GA: '266', GQ: '226', ZM: '894', MW: '454', MZ: '508', SZ: '748', BI: '108', LB: '422', MG: '450', PS: '275', GM: '270', TN: '788', JO: '400', OM: '512', VU: '548', KH: '116', LA: '418', MM: '104', KP: '408', MN: '496', BT: '064', NP: '524', AF: '004', TJ: '762', KG: '417', TM: '795', SY: '760', AM: '051', BY: '112', MD: '498', LT: '440', LV: '428', EE: '233', BG: '100', AL: '008', HR: '191', LU: '442', NC: '540', SB: '090', LK: '144', IS: '352', AZ: '031', GE: '268', BN: '096', SI: '705', SK: '703', ER: '232', PY: '600', YE: '887', AQ: '010', CY: '196', LY: '434', DJ: '262', UG: '800', RW: '646', BA: '070', MK: '807', RS: '688', ME: '499', TT: '780', SS: '728'
}

const weatherTime = (value: string | null) => {
  if (!value) return null
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })
}

export function toWeatherEvent(r: GlobalWeather): SignalEvent | null {
  const temp = r.temp_f
  if (
    typeof temp !== 'number' || !Number.isFinite(temp) ||
    typeof r.latitude !== 'number' || !Number.isFinite(r.latitude) || r.latitude < -90 || r.latitude > 90 ||
    typeof r.longitude !== 'number' || !Number.isFinite(r.longitude) || r.longitude < -180 || r.longitude > 180
  ) return null

  const apparent = typeof r.apparent_temp_f === 'number' && Number.isFinite(r.apparent_temp_f) ? r.apparent_temp_f : null
  const warmer = apparent !== null && apparent > temp
  const valid = weatherTime(r.valid_time)
  const issued = weatherTime(r.issued_at)
  const wind = typeof r.wind_10m_mph === 'number' && Number.isFinite(r.wind_10m_mph) ? r.wind_10m_mph : null
  const context = [
    valid && `Forecast for ${valid}`,
    issued && `issued ${issued}`,
    apparent !== null && `feels like ${Math.round(apparent)}°F`,
    wind !== null && `wind ${Math.round(wind)} mph`,
  ].filter(Boolean).join(' · ')
  return {
    id: r.point_id,
    kicker: `Weather / ${r.country}`,
    title: r.name,
    value: `${Math.round(temp)}°F`,
    valueLabel: 'NOAA GFS forecast temperature',
    body: context || 'Next-hour NOAA GFS city forecast.',
    sources: 'NOAA Global Forecast System (GFS)',
    trend: warmer ? 'rising' : 'easing',
    lat: r.latitude,
    lng: r.longitude,
    country: COUNTRY_IDS[r.country.toUpperCase()] ?? '',
    query: `${r.name} weather`,
  }
}

/** A contract from GET /api/commodities/{commodity} (world_data.commodity_futures). */
export type Contract = {
  symbol: string
  name: string
  region: string
  settlement: 'cash' | 'physical'
  unit: string
  price: number | null
  closes: { date: string; close: number }[]
  change_pct: number | null
}

export const COMMODITIES = {
  oil: 'Oil',
  'natural-gas': 'Natural gas',
  metals: 'Metals',
  agriculture: 'Agriculture',
  livestock: 'Livestock',
} as const
export type Commodity = keyof typeof COMMODITIES

// Where each contract prices: its delivery or assessment point (exchange vaults/warehouses for metals).
// shortcut: contracts sharing a point get small hand offsets so markers don't stack; move to real clustering if more are added.
const SITES: Record<string, { place: string; lat: number; lng: number; country: string }> = {
  'BZ=F': { place: 'North Sea', lat: 57.5, lng: 1.5, country: '826' },
  'CL=F': { place: 'Cushing, Oklahoma', lat: 35.98, lng: -96.77, country: '840' },
  'DBL=F': { place: 'Arabian Gulf', lat: 25.6, lng: 55.0, country: '784' },
  'HO=F': { place: 'New York Harbor', lat: 40.5, lng: -73.6, country: '840' },
  'RB=F': { place: 'New York Harbor', lat: 40.5, lng: -73.6, country: '840' },
  'NG=F': { place: 'Henry Hub, Louisiana', lat: 29.96, lng: -92.03, country: '840' },
  'TTF=F': { place: 'Netherlands (TTF)', lat: 52.37, lng: 4.9, country: '528' },
  'JKM=F': { place: 'Japan / Korea (JKM)', lat: 35.4, lng: 139.8, country: '392' },
  'GC=F': { place: 'COMEX vaults, New York', lat: 41.2, lng: -74.6, country: '840' },
  'SI=F': { place: 'COMEX vaults, New York', lat: 41.2, lng: -74.6, country: '840' },
  'PL=F': { place: 'NYMEX vaults, New York', lat: 41.8, lng: -75.6, country: '840' },
  'PA=F': { place: 'NYMEX vaults, New York', lat: 41.8, lng: -75.6, country: '840' },
  'HG=F': { place: 'COMEX warehouses, US', lat: 39.3, lng: -76.6, country: '840' },
  'ALI=F': { place: 'COMEX aluminum warehouses, US', lat: 42.33, lng: -83.05, country: '840' },
  'HRC=F': { place: 'US Midwest steel (Gary, Indiana)', lat: 41.6, lng: -87.34, country: '840' },
  'TIO=F': { place: 'Qingdao, China', lat: 36.07, lng: 120.38, country: '156' },
  'ZW=F': { place: 'Toledo / Chicago (SRW wheat)', lat: 41.66, lng: -83.56, country: '840' },
  'KE=F': { place: 'Kansas City (HRW wheat)', lat: 39.1, lng: -94.58, country: '840' },
  'ZC=F': { place: 'Illinois River (corn, soybeans)', lat: 41.3, lng: -89.1, country: '840' },
  'ZS=F': { place: 'Illinois River (corn, soybeans)', lat: 41.3, lng: -89.1, country: '840' },
  'ZM=F': { place: 'Central Illinois processors', lat: 39.84, lng: -88.95, country: '840' },
  'ZL=F': { place: 'Central Illinois processors', lat: 39.84, lng: -88.95, country: '840' },
  'ZO=F': { place: 'Minneapolis (oats)', lat: 44.98, lng: -93.27, country: '840' },
  'ZR=F': { place: 'Arkansas (rough rice)', lat: 34.75, lng: -92.29, country: '840' },
  'KC=F': { place: 'ICE coffee warehouses, New York', lat: 40.0, lng: -72.5, country: '840' },
  'CC=F': { place: 'Delaware River ports (cocoa)', lat: 39.95, lng: -75.16, country: '840' },
  'SB=F': { place: 'Santos, Brazil (FOB raw sugar)', lat: -23.96, lng: -46.33, country: '076' },
  'CT=F': { place: 'Memphis (cotton)', lat: 35.15, lng: -90.05, country: '840' },
  'OJ=F': { place: 'Florida (orange juice)', lat: 28.0, lng: -81.7, country: '840' },
  'LBR=F': { place: 'Pacific Northwest mills (lumber)', lat: 45.5, lng: -122.7, country: '840' },
  'HE=F': { place: 'Iowa (lean hogs)', lat: 41.9, lng: -93.1, country: '840' },
  'LE=F': { place: 'Nebraska / Kansas feedlots', lat: 40.6, lng: -99.5, country: '840' },
  'GF=F': { place: 'Oklahoma City (feeder cattle)', lat: 35.47, lng: -97.5, country: '840' },
  'DC=F': { place: 'Wisconsin dairy', lat: 44.5, lng: -89.5, country: '840' },
  'CSC=F': { place: 'Wisconsin dairy', lat: 44.5, lng: -89.5, country: '840' },
}

type Region = 'americas' | 'europe-africa' | 'gulf-asia'
/** Trading region of a point: the Americas, Europe/Africa/western Russia, or the Gulf, Asia and Oceania. */
export function tradingRegion(lat: number, lng: number): Region {
  if (lng < -25) return 'americas'
  if (lng >= 45 || (lat > 12 && lat < 37 && lng >= 34)) return 'gulf-asia' // Arabian Peninsula, Levant and Iraq price crude off Dubai
  return 'europe-africa'
}
// Single-location contracts that only price one region (substitutes above are picked by distance instead).
// Everything else (metals, grains, softs) is a global exchange reference.
const PRICES_REGION: Record<string, Region> = {
  'CL=F': 'americas', 'HO=F': 'americas', 'RB=F': 'americas', 'BZ=F': 'europe-africa', 'DBL=F': 'gulf-asia',
  'NG=F': 'americas', 'TTF=F': 'europe-africa', 'JKM=F': 'gulf-asia',
  'HRC=F': 'americas', 'ZR=F': 'americas', 'OJ=F': 'americas', 'LBR=F': 'americas',
  'HE=F': 'americas', 'LE=F': 'americas', 'GF=F': 'americas', 'DC=F': 'americas', 'CSC=F': 'americas',
}

// Benchmarks that price the same thing in different places; a point uses only the nearest of each.
const SUBSTITUTES: string[][] = [
  ['BZ=F', 'CL=F', 'DBL=F'], // crude: Brent, WTI, Dubai
  ['TTF=F', 'NG=F', 'JKM=F'], // gas: TTF, Henry Hub, JKM
]

/** Great-circle distance in km. */
export function distanceKm(a: { lat: number; lng: number }, b: { lat: number; lng: number }): number {
  const rad = Math.PI / 180
  const dLat = (b.lat - a.lat) * rad
  const dLng = (b.lng - a.lng) * rad
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.sin(dLng / 2) ** 2
  return 2 * 6371 * Math.asin(Math.min(1, Math.sqrt(h)))
}

/** Benchmarks for the clicked point: of each set of substitutes (crude, gas) only the one whose pricing
 *  point is nearest, plus the single-location contracts that price the point's region and the global
 *  exchange references. Every mapped contract without a location; all of them when none fits. */
export function regionalCommodityBenchmarks(contracts: Contract[], location: { lat: number; lng: number } | null): Contract[] {
  const mapped = contracts.filter((contract) => SITES[contract.symbol])
  if (!location || !Number.isFinite(location.lat) || !Number.isFinite(location.lng)) return mapped
  const nearest = new Set<string>()
  for (const group of SUBSTITUTES) {
    const ranked = mapped.filter((c) => group.includes(c.symbol))
      .sort((a, b) => distanceKm(location, SITES[a.symbol]) - distanceKm(location, SITES[b.symbol]))
    if (ranked.length) nearest.add(ranked[0].symbol)
  }
  const region = tradingRegion(location.lat, location.lng)
  const fits = mapped.filter((c) => SUBSTITUTES.some((g) => g.includes(c.symbol))
    ? nearest.has(c.symbol)
    : (PRICES_REGION[c.symbol] ?? region) === region)
  return fits.length ? fits : mapped
}

/** One marker per commodity per pricing location, valued at the average 30-day change there. */
export function toCommodityEvents(commodity: Commodity, contracts: Contract[]): SignalEvent[] {
  const groups = new Map<string, Contract[]>()
  for (const c of contracts) {
    if (!SITES[c.symbol] || c.change_pct === null) continue
    const place = SITES[c.symbol].place
    groups.set(place, [...(groups.get(place) ?? []), c])
  }
  return [...groups.values()].map((group) => {
    const site = SITES[group[0].symbol]
    const avg = group.reduce((sum, c) => sum + (c.change_pct ?? 0), 0) / group.length
    const names = group.map((c) => c.name).join(' / ')
    return {
      id: `commodity:${commodity}:${site.place}`,
      kicker: names,
      title: `${COMMODITIES[commodity]} / ${site.place}`,
      value: `${avg >= 0 ? '+' : ''}${avg.toFixed(1)}%`,
      valueLabel: '30-day futures change',
      body: `${names} front-month futures, ${group.map((c) => (c.settlement === 'cash' ? 'cash-settled' : 'physical delivery')).join(' / ')}.`,
      sources: 'Yahoo Finance front-month futures',
      trend: avg >= 0 ? 'rising' : 'easing',
      lat: site.lat,
      lng: site.lng,
      country: site.country,
      query: `${COMMODITIES[commodity]} futures`,
      commodity,
    }
  })
}

/**
 * Greek-mode artwork, drawn as Attic red-figure pottery: figures left in the orange clay, the ground
 * painted in black glaze, and anatomy picked out in fine black lines, with touches of the added
 * white, red and gilding real painters used. Everything is static markup, so it is safe for innerHTML.
 */

export const CLAY = '#e07c44'
export const CLAY_DK = '#a9552a' // dilute glaze: the brown wash painters used for hair and shading
export const GLAZE = '#17100c'
export const IVORY = '#f3e4c6'
export const BLOOD = '#a8382a'
export const GOLD = '#e8c97a'

export type MonsterId =
  | 'medusa' | 'cetus' | 'sirens' | 'scylla' | 'polyphemus' | 'minotaur' | 'hydra' | 'cerberus'
  | 'keres' | 'chimera' | 'ladon' | 'dragon' | 'griffins' | 'phoenix' | 'sphinx'

const r1 = (n: number) => Math.round(n * 10) / 10
const fill = (d: string, color = CLAY) => `<path d="${d}" fill="${color}"/>`
const line = (d: string, w = 1.5, color = GLAZE) =>
  `<path d="${d}" fill="none" stroke="${color}" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round"/>`
const dot = (x: number, y: number, r: number, color = GLAZE) => `<circle cx="${x}" cy="${y}" r="${r}" fill="${color}"/>`

/** A thick clay body along a path, with a dotted spine of scales. */
function serpent(d: string, w: number, color = CLAY) {
  return `<path d="${d}" fill="none" stroke="${color}" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round"/>`
    + `<path d="${d}" fill="none" stroke="${GLAZE}" stroke-width="${r1(w * 0.18)}" stroke-dasharray="0.1 ${r1(w * 0.55)}" stroke-linecap="round" opacity=".75"/>`
}

/** Snake head facing +x in local space, with a forked red tongue. */
function snakeHead(x: number, y: number, deg: number, s = 1, eye = GLAZE) {
  return `<g transform="translate(${r1(x)} ${r1(y)}) rotate(${r1(deg)}) scale(${s})">`
    + `<path d="M5 0 l4.5 -1.4 M5 0 l4.5 1.4" stroke="${BLOOD}" stroke-width="1" fill="none" stroke-linecap="round"/>`
    + `<path d="M-3 -2.6 C0 -3.6 4.5 -2.4 5.6 0 C4.5 2.4 0 3.6 -3 2.6 Z" fill="${CLAY}"/>`
    + `<circle cx="1.6" cy="-1" r="0.9" fill="${eye}"/></g>`
}

/** Hound's head in profile facing +x: open jaws, pricked ear, gold eye. */
function houndHead(x: number, y: number, deg: number, s = 1, flip = false) {
  return `<g transform="translate(${x} ${y}) rotate(${deg}) scale(${flip ? -s : s} ${s})">`
    + fill('M-6 -9 L-4.5 -17.5 L0.5 -10 Z')
    + fill('M-9 -4 C-7 -11 2 -11.5 5.5 -7.5 L14.5 -5.5 C16.5 -4.6 16.6 -1.6 14.6 -0.8 L6.5 0.6 L14 4 C13.2 6.4 9.5 7.2 6 5.6 C3 9.2 -5.5 9.4 -9 3.5 Z')
    + line('M6.5 0.6 L14 4', 1.1)
    + line('M8 1.1 l0.8 1.6 l0.9 -1.3 l0.9 1.6 l0.9 -1.3', 0.8, IVORY)
    + dot(2, -6, 1.5, GOLD) + dot(2, -6, 0.7)
    + line('M-4 -3 C-2 1 -2 4 -5 7', 1)
    + '</g>'
}

/* ------------------------------------------------------------------ figures (100 x 100) */

function medusa() {
  const cx = 50, cy = 50
  const snakes: string[] = []
  for (let i = 0; i < 13; i++) {
    const a = ((150 + i * 20) * Math.PI) / 180
    const c = Math.cos(a), s = Math.sin(a)
    const p = (r: number, off: number) => `${r1(cx + r * c - off * s)} ${r1(cy + r * s + off * c)}`
    const flip = i % 2 ? 1 : -1
    snakes.push(serpent(`M${p(17, 0)} C${p(23, 6 * flip)} ${p(27, -6 * flip)} ${p(31, 0)} S${p(35, 4 * flip)} ${p(37.5, 1 * flip)}`, 3.4))
    snakes.push(snakeHead(cx + 38.5 * c - flip * s, cy + 38.5 * s + flip * c, (a * 180) / Math.PI, 0.85))
  }
  return snakes.join('')
    // two snakes knotted under the chin, as on gorgoneia
    + serpent('M30 62 C36 80 46 74 50 70 C54 74 64 80 70 62', 3.2)
    + `<ellipse cx="50" cy="51" rx="20.5" ry="19.5" fill="${CLAY}"/>`
    + line('M35 42 Q41 36.5 47 41', 1.8) + line('M53 41 Q59 36.5 65 42', 1.8)
    + `<circle cx="42" cy="46" r="4.4" fill="${IVORY}" stroke="${GLAZE}" stroke-width="1.3"/>`
    + `<circle cx="58" cy="46" r="4.4" fill="${IVORY}" stroke="${GLAZE}" stroke-width="1.3"/>`
    + dot(42, 46.3, 2.1) + dot(58, 46.3, 2.1)
    + line('M50 44 L48.2 52.5 Q50 54.5 51.8 52.5', 1.2)
    + line('M33.5 52 Q36 56 39 57 M66.5 52 Q64 56 61 57', 1)
    + fill('M36.5 56.5 Q50 70 63.5 56.5 Q50 61.5 36.5 56.5 Z', GLAZE)
    + fill('M40.5 60 L42.4 54.6 L44.4 60.6 Z', IVORY) + fill('M55.6 60.6 L57.6 54.6 L59.5 60 Z', IVORY)
    + `<path d="M46 61.6 Q50 73 54 61.6 Z" fill="${BLOOD}" stroke="${GLAZE}" stroke-width=".8"/>`
}

function sirens() {
  return fill('M40 52 C36 38 27 26 13 20 C25 22 38 28 46 38 C49 42 51 46 51 50 Z', CLAY_DK)
    + line('M42 47 C38 39 31 31 21 25 M46.5 46 C43 38 37 32 29 27.5', 1.1)
    + fill('M31 58 L11 49 L14 56 L9 61 L14 65 L11 72 L33 64 Z')
    + line('M30 59.5 L15 55.5 M30 61.5 L12.5 61.2 M31 63 L15 67.5', 1.1)
    + fill('M27 58 C29 47 44 42 57 46 C65 49 67 58 61 66 C53 74 36 72 27 62 Z')
    + line('M35 61 C43 66 52 64 58.5 57', 1.2)
    + line('M53 51 q2.4 1.6 0.4 4 M49 54 q2.4 1.6 0.4 4 M57 55 q2 1.6 0 4', 0.9)
    + line('M43 70 L41.5 80 M52 70 L53 80', 2.2, CLAY)
    + line('M38 81.5 L41.5 80 L44.5 81.5 M50 81.5 L53 80 L56 81.5', 1.4, CLAY)
    + fill('M20 82 Q50 77 80 82 L80 87 Q50 83.5 20 87 Z', CLAY_DK)
    + fill('M55 48 C57 41 59 38 62 35 L70 37 C68 41 66 46 63 51 Z')
    + `<circle cx="67" cy="30" r="8" fill="${CLAY}"/>`
    + fill('M74 27.5 L78.2 32 L74.4 33.6 Z')
    + fill('M59 31 C58 22 67 19 74 23 C70 24 67 25.5 65 28 C63 31 63 35 62 38.5 C60 36.5 59 34 59 31 Z', CLAY_DK)
    + dot(58.6, 25, 3.8, CLAY_DK)
    + line('M61 24.2 C66 21 71 21 74.5 24', 1.3, GOLD)
    + line('M69.6 29.2 q1.7 -1.1 3.2 0', 1.1) + dot(71.2, 29.6, 0.7)
    + line('M74.2 35 l1.8 0.2', 1)
    // the lyre she plays
    + line('M82 40 C80 48 82 56 86 60 M90 40 C92 48 90 56 86 60 M82.5 46 L89.5 46', 1.4, GOLD)
}

function scylla() {
  // Charybdis below: a whirlpool spiral, squashed into perspective; its dashes flow in CSS
  const pts: string[] = []
  for (let t = 0; t <= 1; t += 0.02) {
    const a = t * Math.PI * 2 * 3.2
    const r = 2 + t * 22
    pts.push(`${r1(50 + r * Math.cos(a))} ${r1(77 + r * 0.38 * Math.sin(a))}`)
  }
  const spiral = `<g class="art-swirl">`
    + `<path d="M${pts.join(' L')}" fill="none" stroke="${CLAY}" stroke-width="2.2" stroke-linecap="round"/>`
    + `<path d="M${pts.join(' L')}" fill="none" stroke="${IVORY}" stroke-width=".7" stroke-linecap="round" stroke-dasharray="2 5" opacity=".7"/></g>`
  return spiral
    + serpent('M50 72 C40 62 26 56 30.5 36', 6)
    + serpent('M50 72 C53 56 46 42 51 25', 6)
    + serpent('M50 72 C60 62 76 56 70.5 38', 6)
    + houndHead(30, 33, -35, 0.95, true)
    + houndHead(52, 23, -55, 1)
    + houndHead(71, 35, -20, 0.95)
    + fill('M38 74 C44 66 56 66 62 74 C56 70 44 70 38 74 Z', CLAY_DK)
}

function polyphemus() {
  return fill('M24 50 L21 38 L28 39.5 L27 27 L35 31 L37 18.5 L44 26 L50 13.5 L56 26 L63 18.5 L65 31 L73 27 L72 39.5 L79 38 L76 50 Z', CLAY_DK)
    + `<ellipse cx="27" cy="51" rx="3.5" ry="5.5" fill="${CLAY}"/><ellipse cx="73" cy="51" rx="3.5" ry="5.5" fill="${CLAY}"/>`
    + `<ellipse cx="50" cy="50" rx="22" ry="25" fill="${CLAY}"/>`
    + fill('M27.5 54 C29 73 38 87 50 91 C62 87 71 73 72.5 54 C66 63 60 66 50 66 C40 66 34 63 27.5 54 Z', CLAY_DK)
    + line('M34 70 q3 3 0 6 M41 74 q3 3 0 6 M50 76 q3 3 0 6 M59 74 q3 3 0 6 M66 70 q3 3 0 6', 1)
    + line('M33 31 Q50 23.5 67 31', 2.8)
    + `<path d="M37 40.5 Q50 29.5 63 40.5 Q50 51.5 37 40.5 Z" fill="${IVORY}" stroke="${GLAZE}" stroke-width="1.5"/>`
    + dot(50, 40.5, 5.6, GOLD) + dot(50, 40.5, 2.6) + dot(48.6, 39.1, 0.9, IVORY)
    + line('M47 46 Q43.5 54 46.5 56 Q50 58.5 53.5 56 Q56.5 54 53 46', 1.3)
    + line('M41.5 62.5 Q50 58.5 58.5 62.5', 1.9)
    + line('M44 63 l1 -1.6 M56 63 l-1 -1.6', 1, IVORY)
}

function minotaur() {
  return fill('M14 98 C16 81 28 73 40 71 L60 71 C72 73 84 81 86 98 Z')
    + line('M50 75 L50 93 M35.5 81 Q43.5 87.5 50 85 M64.5 81 Q56.5 87.5 50 85 M38 74.5 Q44 77 50 76 Q56 77 62 74.5', 1.2)
    + fill('M40 56 L38 73 L62 73 L60 56 Z')
    + `<path d="M38.5 30 C28 30 18 24 15.5 10 C22 18 30 21.5 41 24 Z" fill="${IVORY}" stroke="${GLAZE}" stroke-width=".9"/>`
    + `<path d="M61.5 30 C72 30 82 24 84.5 10 C78 18 70 21.5 59 24 Z" fill="${IVORY}" stroke="${GLAZE}" stroke-width=".9"/>`
    + fill('M37.5 32 L25.5 36.5 L36.5 39 Z') + fill('M62.5 32 L74.5 36.5 L63.5 39 Z')
    + fill('M36 28 C40 21.5 60 21.5 64 28 L62 44 C62 52 58 62 50 64 C42 62 38 52 38 44 Z')
    + `<ellipse cx="50" cy="57.5" rx="9.5" ry="6.5" fill="${CLAY}" stroke="${GLAZE}" stroke-width="1.2"/>`
    + `<ellipse cx="46" cy="57.5" rx="1.6" ry="2.2" fill="${GLAZE}"/><ellipse cx="54" cy="57.5" rx="1.6" ry="2.2" fill="${GLAZE}"/>`
    + `<circle cx="50" cy="63.6" r="3.2" fill="none" stroke="${GOLD}" stroke-width="1.5"/>`
    + line('M40 37.5 L46.5 40.5 M60 37.5 L53.5 40.5', 2)
    + dot(44.2, 41.6, 1.2, GOLD) + dot(55.8, 41.6, 1.2, GOLD)
    + line('M43 25 Q47 20 50 25 Q53 20 57 25', 1.2)
    + line('M41.5 48 Q44 51 43.5 54 M58.5 48 Q56 51 56.5 54', 1)
}

function hydra() {
  const angles = [-162, -136, -112, -90, -68, -44, -18]
  const necks: string[] = []
  const heads: string[] = []
  angles.forEach((deg, i) => {
    const a = (deg * Math.PI) / 180
    const hx = 50 + 31 * Math.cos(a), hy = 70 + 44 * Math.sin(a) * (i === 3 ? 1.02 : 0.9)
    const wig = i % 2 ? 7 : -7
    const mx = 50 + 16 * Math.cos(a) + wig * Math.sin(a), my = 70 + 22 * Math.sin(a) - wig * Math.cos(a)
    necks.push(serpent(`M50 72 Q${r1(mx)} ${r1(my)} ${r1(hx)} ${r1(hy)}`, 4.2))
    heads.push(snakeHead(hx, hy, (Math.atan2(hy - my, hx - mx) * 180) / Math.PI, 1.25, i === 3 ? GOLD : GLAZE))
  })
  return serpent('M75 80 C88 80 91 69 84 65.5', 4.5)
    + fill('M23 80 C29 69 71 69 77 80 C71 89 29 89 23 80 Z')
    + line('M30 79 q4 -3 8 0 q4 -3 8 0 q4 -3 8 0 q4 -3 8 0 q4 -3 8 0', 1)
    + line('M33 84 q3 -2 6 0 q3 -2 6 0 q3 -2 6 0 q3 -2 6 0 q3 -2 6 0', 0.9)
    + necks.join('') + heads.join('')
    // the reeds of the Lernaean swamp
    + line('M14 88 Q16 76 13 66 M18 89 Q19 80 22 72 M86 88 Q84 76 88 67', 1.3, CLAY_DK)
}

function cerberus() {
  return fill('M18 98 C20 78 33 67 50 65 C67 67 80 78 82 98 Z')
    + line('M50 70 L50 96 M36 76 Q42 82 44 92 M64 76 Q58 82 56 92', 1.1)
    // necks with a mane of snakes
    + fill('M28 58 L40 68 L36 75 L23 63 Z') + fill('M72 58 L60 68 L64 75 L77 63 Z') + fill('M44 50 L56 50 L58 67 L42 67 Z')
    + line('M27 60 q-3 -3 -1 -6 M31 63 q-3 -3 -1 -6 M73 60 q3 -3 1 -6 M69 63 q3 -3 1 -6', 1.6, CLAY_DK)
    + houndHead(27, 54, 8, 1.05, true)
    + houndHead(73, 54, -8, 1.05)
    // middle head, facing out
    + fill('M41 34 L35.5 19 L46.5 30 Z') + fill('M59 34 L64.5 19 L53.5 30 Z')
    + fill('M39 40 C39 29 61 29 61 40 L58.5 52 C56 59 44 59 41.5 52 Z')
    + `<ellipse cx="50" cy="52" rx="6.6" ry="5.4" fill="${CLAY}" stroke="${GLAZE}" stroke-width="1.1"/>`
    + `<ellipse cx="50" cy="49" rx="2.6" ry="1.8" fill="${GLAZE}"/>`
    + dot(44.5, 41, 1.9, GOLD) + dot(55.5, 41, 1.9, GOLD) + dot(44.5, 41, 0.8) + dot(55.5, 41, 0.8)
    + line('M45 55.5 Q50 58.5 55 55.5', 1.1)
    + line('M46.5 56.4 l0.6 1.8 M53.5 56.4 l-0.6 1.8', 1, IVORY)
    // spiked bronze collar
    + `<path d="M41.5 62 Q50 65.5 58.5 62" fill="none" stroke="${GOLD}" stroke-width="2.4"/>`
    + line('M44 63.5 l-0.8 2.6 M50 64.6 l0 2.8 M56 63.5 l0.8 2.6', 1.2, GOLD)
}

function keres() {
  return fill('M47 40 C35 27 19 25 7 31 C13 35 15 39 13 45 C19 45 21 49 19 55 C27 53 33 53 40 58 Z', CLAY_DK)
    + fill('M53 40 C65 27 81 25 93 31 C87 35 85 39 87 45 C81 45 79 49 81 55 C73 53 67 53 60 58 Z', CLAY_DK)
    + line('M44 41 C34 33 24 31 14 33 M42 46 C34 41 25 41 17 44 M41 51 C34 49 28 49 22 51', 1)
    + line('M56 41 C66 33 76 31 86 33 M58 46 C66 41 75 41 83 44 M59 51 C66 49 72 49 78 51', 1)
    // blood-red peplos
    + fill('M43.5 40 L56.5 40 L64 86 C57 90 43 90 36 86 Z', BLOOD)
    + line('M46 48 L42 86 M50 47 L50 88 M54 48 L58 86', 0.9)
    + fill('M43.5 40 L56.5 40 L58 52 C53 54.5 47 54.5 42 52 Z')
    + line('M44 45 C38 53 33 59 28.5 66 M56 45 C62 53 67 59 71.5 66', 3, CLAY)
    + line('M28.5 66 l-3 2.5 M28.5 66 l-1.4 3.6 M28.5 66 l0.6 3.8 M71.5 66 l3 2.5 M71.5 66 l1.4 3.6 M71.5 66 l-0.6 3.8', 1.2, CLAY)
    // wild hair streaming
    + fill('M41.5 31 C40 23 45 18 50 18 C55 18 60 23 58.5 31 L63 27 L60 34 L65 35 L58 38 L42 38 L35 35 L40 34 L37 27 Z', CLAY_DK)
    + `<circle cx="50" cy="31" r="7" fill="${CLAY}"/>`
    + dot(47.4, 30, 1.1) + dot(52.6, 30, 1.1)
    + fill('M46.5 34.2 L53.5 34.2 L52.5 36.6 L47.5 36.6 Z', GLAZE)
    + line('M47.6 34.4 L47.6 35.8 M49.2 34.4 L49.2 36 M50.8 34.4 L50.8 36 M52.4 34.4 L52.4 35.8', 0.6, IVORY)
}

function chimera() {
  return line('M22 60 C11 57 8 45 15.5 38.5 C19.5 35 24.5 36.5 24 40.5', 3.6, CLAY)
    + snakeHead(24, 40.5, 80, 1.15)
    + fill('M19 64 C18 55 25 50 35 50 L56 49 C60 49 63 52 64 57 L65 64 C66 70 64 74 60 74 L60.5 84 L55.5 84 L55 74 C50 75 44 75 39 74 L37.5 84 L32.5 84 L32.5 72 L30 74 L28.5 84 L23.5 84 L24.5 70 C20.5 70 19 67 19 64 Z')
    + line('M23.5 84 L28.5 84 M32.5 84 L37.5 84 M55.5 84 L60.5 84', 1.6, CLAY)
    + line('M33 60 Q40 64 48 62 M27 68 Q24 74 26 80 M58 70 Q60 76 58 82', 1)
    // goat rising from the back
    + fill('M40 51 L38 39 C35 37 32 36 29 37 L27 41 C30 43 34 43 37 42 L42 50 Z')
    + line('M36.5 38.5 C37 31 42 26.5 48 27.5 M38.5 39 C40 33 44 30.5 49 31', 1.8, CLAY_DK)
    + line('M28.5 41 L27 46', 1.2, CLAY) + dot(32.6, 38.8, 0.9)
    // lion head and mane
    + `<circle cx="69" cy="48" r="14" fill="${CLAY_DK}"/>`
    + line('M58 39 l-4 -4 M61 35.5 l-2 -5 M66 34 l0 -5 M72 34 l2 -5 M77 36 l4 -4 M80.5 41 l5 -2 M57 47 l-5 0 M59 55 l-4 4', 1.6, CLAY_DK)
    + fill('M62 41 C64 37 72 36 76 40 L84 44.5 C86 46 85.5 48.5 83.5 49 L77 50.5 L84 54 C83 56.5 78.5 57.5 75 55.5 C70 58 63 56 62 50 Z')
    + line('M77 50.5 L84 54', 1) + dot(73, 43.5, 1.4, GOLD) + dot(73, 43.5, 0.6)
    + line('M78 51 l0.8 1.6 l0.8 -1.3 l0.8 1.6', 0.8, IVORY)
    // fire
    + fill('M84.5 50 C89 46 93 47 96 44 C94 48 97 49 99 49.5 C95 51 95 54 97.5 57 C93 55 90 56 85.5 53 Z', GOLD)
    + fill('M85 51 C88 50 90 50.5 92 49.5 C91 51.5 92 52.5 93.5 53.5 C90.5 53.5 88 53.5 85 52.5 Z', BLOOD)
}

function ladon() {
  return fill('M45.5 92 C47 76 45 64 44 52 L56 52 C55 64 53 76 54.5 92 Z', CLAY_DK)
    + line('M48 60 Q49 70 48 82 M52.5 58 Q51.5 70 52.5 86', 1)
    + `<circle cx="50" cy="35" r="17" fill="${CLAY_DK}"/><circle cx="33" cy="42" r="12" fill="${CLAY_DK}"/>`
    + `<circle cx="67" cy="42" r="12" fill="${CLAY_DK}"/><circle cx="39" cy="25" r="10" fill="${CLAY_DK}"/><circle cx="61" cy="25" r="10" fill="${CLAY_DK}"/>`
    + line('M30 38 q3 -3 6 0 M40 30 q3 -3 6 0 M54 30 q3 -3 6 0 M64 38 q3 -3 6 0 M47 44 q3 -3 6 0 M36 48 q3 -3 6 0 M60 48 q3 -3 6 0', 0.9)
    + [[34, 32], [45, 21], [58, 19], [68, 33], [41, 41], [56, 38], [27, 45], [73, 47], [50, 31]]
      .map(([x, y]) => `<circle cx="${x}" cy="${y}" r="3.3" fill="${GOLD}" stroke="${GLAZE}" stroke-width=".8"/>`).join('')
    + serpent('M22 90 C38 86 64 84 60 75 C56 66 38 71 41 62 C44 54 63 59 62 50 C61 44 52 47 50 42', 5)
    + snakeHead(50, 42, -110, 1.3)
    + fill('M14 92 Q50 88 86 92 L86 96 L14 96 Z', CLAY_DK)
}

function dragon() {
  return fill('M23 92 L25.5 38 L32.5 38 L33 92 Z', CLAY_DK)
    + line('M27 48 Q28 64 27 84 M30.5 46 Q29.5 62 30.5 88', 0.9)
    + line('M30 40 C40 33 52 31 66 29 M27 40 C22 34 18 30 13 28', 3.2, CLAY_DK)
    + `<circle cx="29" cy="25" r="11" fill="${CLAY_DK}"/><circle cx="18" cy="31" r="7" fill="${CLAY_DK}"/><circle cx="41" cy="30" r="7" fill="${CLAY_DK}"/>`
    + line('M22 22 q3 -3 6 0 M30 18 q3 -3 6 0 M33 28 q3 -3 6 0 M15 30 q3 -3 6 0', 0.9)
    // the Golden Fleece: a whole ram's hide hung by its horns, legs splayed
    + `<path d="M46 33.5 C50 31.5 56 31.5 60 33.5 L61 38 L67.5 41 L62 43.5 L61.5 50 C60 52 58 53 56 53.5 L57 60 L52.5 55 L48 60 L48.5 53.5 C46.5 53 44.5 52 43 50 L42.5 43.5 L37 41 L43.5 38 Z" fill="${GOLD}" stroke="#9c7a35" stroke-width=".9" stroke-linejoin="round"/>`
    + line('M46.5 32.5 C44 30 44.5 27 47.5 27.5 M57.5 32.5 C60 30 59.5 27 56.5 27.5', 1.4, '#9c7a35')
    + line('M47 38 q1.6 -1.8 3.2 0 q1.6 -1.8 3.2 0 q1.6 -1.8 3.2 0 M45.5 43 q1.6 -1.8 3.2 0 q1.6 -1.8 3.2 0 q1.6 -1.8 3.2 0 q1.6 -1.8 3.2 0 M47 48 q1.6 -1.8 3.2 0 q1.6 -1.8 3.2 0 q1.6 -1.8 3.2 0', 0.75, '#9c7a35')
    // the sleepless serpent
    + serpent('M12 86 C30 96 66 96 78 86 C88 77 70 68 58 74 C46 80 34 76 38 66 C42 57 60 62 70 58', 6.2)
    + fill('M66 56 C68 50 75 47.5 80.5 49.5 L91 52 C93 53 93 55.5 91 56 L83 57 L91.5 60 C90.5 62.5 85.5 63.5 81.5 61.5 C77 64.5 68 63 66 56 Z')
    + fill('M70 50.5 L67 43 L73.5 49 Z M75 48.6 L74.5 41 L78.5 48.6 Z')
    + line('M83 57 L91.5 60', 1) + dot(77, 53, 1.8, GOLD) + dot(77, 53, 0.8)
    + line('M84.5 57.6 l0.8 1.6 l0.8 -1.3 l0.8 1.6', 0.8, IVORY)
}

function griffins() {
  return line('M22 62 C12 60 10 50 15 44 C17 42 20 43 19.5 46', 2.4, CLAY)
    + fill('M15.5 41 C13.5 43 14 47.5 17.5 47.5 C21 47 20.5 42 15.5 41 Z')
    + fill('M19 66 C18 57 25 52 35 52 L56 51 C60 51 63 54 64 59 L65 66 C66 72 64 76 60 76 L60.5 86 L55.5 86 L55 76 C50 77 44 77 39 76 L37.5 86 L32.5 86 L32.5 74 L30 76 L28.5 86 L23.5 86 L24.5 72 C20.5 72 19 69 19 66 Z')
    + line('M23.5 86 L28.5 86 M32.5 86 L37.5 86 M55.5 86 L60.5 86', 1.6, CLAY)
    + line('M27 70 Q24 76 26 82 M58 72 Q60 78 58 84', 1)
    // sickle wing
    + fill('M38 54 C32 38 37 22 52 12 C50 22 52 32 58 40 C60 44 61 48 60 52 Z', CLAY_DK)
    + line('M42 50 C38 40 40 30 47 21 M47 50 C44 41 45 33 50 26 M52 50 C50 43 51 37 54 32', 1)
    // eagle head with a hooked beak, long ears and the forehead knob of Archaic griffins
    + fill('M60 54 L62 42 C63 33 71 28.5 78 30.5 C84.5 31.5 89.5 35 92 40.5 C93 44 91.5 47.5 88.5 48.5 C89 45.5 87.5 44 85 44 L80 44.5 C77 49.5 70 51 66 56 Z')
    + fill('M67.5 32.5 L64 20 L71.5 30.5 Z M71 31 L70.5 21.5 L74.5 30 Z')
    + dot(76.5, 30.6, 1.9)
    + line('M80.5 41.6 C84 41.4 87 42 89.5 43.4', 1) + dot(76.5, 37, 1.8, GOLD) + dot(76.5, 37, 0.8)
    + line('M66 40 q2 4 0 8 M70 44 q2 3 0 6', 0.9)
    // gold of the Arimaspians
    + [[68, 86], [74, 87], [71, 82.5], [78, 86.5], [65, 88]]
      .map(([x, y]) => `<circle cx="${x}" cy="${y}" r="2.7" fill="${GOLD}" stroke="${GLAZE}" stroke-width=".7"/>`).join('')
}

function phoenix() {
  const rays = Array.from({ length: 18 }, (_, i) => {
    const a = (i / 18) * Math.PI * 2
    return `M${r1(50 + 19 * Math.cos(a))} ${r1(40 + 19 * Math.sin(a))} L${r1(50 + 40 * Math.cos(a))} ${r1(40 + 40 * Math.sin(a))}`
  }).join(' ')
  return `<path d="${rays}" stroke="${GOLD}" stroke-width="1.1" opacity=".45" stroke-linecap="round"/>`
    + fill('M47 46 C37 38 24 36 11 40 L16 44 L11 48.5 L18 50 L13.5 55 L22 55 L19 60.5 C29 58.5 39 56.5 47.5 57 Z')
    + fill('M53 46 C63 38 76 36 89 40 L84 44 L89 48.5 L82 50 L86.5 55 L78 55 L81 60.5 C71 58.5 61 56.5 52.5 57 Z')
    + line('M45 49 C37 44 28 42.5 18 44 M45 53 C38 50 30 49.5 21 50.5 M55 49 C63 44 72 42.5 82 44 M55 53 C62 50 70 49.5 79 50.5', 1)
    + line('M48 66 C42 76 37 84 33 91 M50 68 L50 94 M52 66 C58 76 63 84 67 91', 2.4, CLAY)
    + dot(33, 91, 2.2, GOLD) + dot(50, 94, 2.2, GOLD) + dot(67, 91, 2.2, GOLD)
    + fill('M50 35 C56 39.5 58 52 54 62 L50 69 L46 62 C42 52 44 39.5 50 35 Z')
    + line('M47 47 q3 2 6 0 M46.5 52 q3.5 2 7 0 M47 57 q3 2 6 0', 0.9)
    + `<circle cx="50" cy="31.5" r="5.2" fill="${CLAY}"/>`
    + fill('M54.6 30.5 L58.8 32.4 L54.6 33.5 Z')
    + dot(51.6, 30.6, 0.9)
    + line('M48 27 L44.5 19.5 M50 26.5 L50 18 M52 27 L55.5 19.5', 1.1, CLAY)
    + dot(44.5, 19, 1.5, GOLD) + dot(50, 17.5, 1.5, GOLD) + dot(55.5, 19, 1.5, GOLD)
    // flames of rebirth
    + fill('M30 96 C33 89 36 89 37 84 C39 89 42 88 44 82 C46 88 49 87 50 80 C51 87 54 88 56 82 C58 88 61 89 63 84 C64 89 67 89 70 96 Z', GOLD)
    + fill('M37 96 C39 92 41 91 42.5 88 C44 92 47 91 50 86 C53 91 56 92 57.5 88 C59 91 61 92 63 96 Z', BLOOD)
}

function sphinx() {
  return fill('M24 79 C22 69 27 62 37 60 L53 58 C59 58 61 62 61 68 L62 79 Z')
    + fill('M55 60 L57 81 L62 81 L61 60 Z') + fill('M49.5 63 L50.5 81 L55 81 L54 63 Z')
    + line('M28 70 Q34 74 42 72 M50 81 L55 81 M57 81 L62 81', 1)
    + line('M24 74 C14 72 12 62 18 56 C20 54 23 55 22 58', 2.2, CLAY)
    // recurving wing of the Archaic sphinx
    + fill('M39 61 C34 43 38 27 51 15 C54 12.5 57 14.5 55 18.5 C47 28 46.5 41 50.5 58 Z', CLAY_DK)
    + line('M42 57 C39 45 41 33 49 23 M46 57 C44 47 45 38 50 30', 1)
    + fill('M52 59 C54 49 58 43 62 41 L68 43 C70 51 66 58 61.5 66 Z')
    + line('M57 50 q2.6 1.4 1.2 4 M55.5 56 q2.6 1.4 1.2 4', 0.9)
    + fill('M60 42 C57 37 58 30 61 27 L57 41 C57 46 58 52 60 55 C56 51 54 46 55 40 Z', CLAY_DK)
    + `<circle cx="66" cy="31" r="7" fill="${CLAY}"/>`
    + fill('M72 29 L75.5 33 L72.4 34 Z')
    + fill('M59.5 31 C59 25 64 22.5 69 23.5 C66 25 63.5 27 62.5 30.5 C62 34 62.5 38 63 41 C60.5 39 59.5 35 59.5 31 Z', CLAY_DK)
    + `<rect x="60.5" y="17.5" width="11" height="6" fill="${GOLD}" stroke="${GLAZE}" stroke-width=".8"/>`
    + dot(68.2, 30.2, 0.8) + line('M67 29 q1.4 -0.8 2.8 0', 1)
    + line('M72.2 35.6 l1.6 0.2', 1)
    // Ionic capital she sits on, as the Naxians set her up at Delphi
    + `<rect x="20" y="81" width="46" height="4" fill="${IVORY}"/>`
    + `<path d="M20 85 L66 85 L64 88 L22 88 Z" fill="${IVORY}"/>`
    + `<circle cx="21.5" cy="89" r="5" fill="${IVORY}"/><circle cx="64.5" cy="89" r="5" fill="${IVORY}"/>`
    + line('M21.5 89 m-2.5 0 a2.5 2.5 0 1 0 2.5 -2.5 a1.2 1.2 0 1 0 1.2 1.2 M64.5 89 m2.5 0 a2.5 2.5 0 1 1 -2.5 -2.5 a1.2 1.2 0 1 1 -1.2 1.2', 0.9)
    + `<rect x="30" y="88" width="26" height="10" fill="${IVORY}"/>`
    + line('M34 88 L34 98 M39 88 L39 98 M43 88 L43 98 M47 88 L47 98 M52 88 L52 98', 0.8, '#b9a888')
}

function cetus() {
  // waves: the running scroll border painters put under sea scenes
  let waves = 'M6 84'
  for (let x = 6; x < 94; x += 12) waves += ` C${x + 3} ${78} ${x + 9} ${78} ${x + 9} ${83} C${x + 9} ${86} ${x + 5} ${86} ${x + 5} ${83.5} C${x + 5} ${82} ${x + 7} ${81.5} ${x + 7.5} ${83} S${x + 10} ${88} ${x + 12} ${84}`
  return serpent('M62 36 C52 41 38 44 40 56 C42 68 66 61 68 73 C70 85 48 89 34 83', 10)
    + fill('M33 83 L18 72 L22 83 L15 94 Z')
    + line('M32 82.5 L22 78.5 M32 83.5 L20 88', 0.9)
    + fill('M46 44 L44 33 L51 41 Z M39.5 53 L31 47 L40.5 48.5 Z M48 61.5 L46 52.5 L53.5 59 Z M63 65 L65.5 56 L68 64 Z M67 75.5 L76 71 L70 78.5 Z')
    + fill('M56 41 C48 45 45 50 47 56 C49 51 53 47 59 45 Z', CLAY_DK)
    + `<g transform="translate(68 30) scale(1.3) translate(-68 -30)">`
    + fill('M60 33 C60 25 68 21 74 23 L87 25 C89 26.5 87.5 29 85 29 L77 30 L87 34.5 C85.5 37.5 79 37.5 74.5 35 C70 39 62 39 60 33 Z')
    + fill('M62 25 L57 15.5 L66.5 22.5 Z M66 23 L64 13 L71 21.5 Z')
    + line('M77 30 L87 34.5', 1)
    + line('M78.5 30.5 l0.8 1.7 l0.9 -1.4 l0.9 1.7 l0.9 -1.4 l0.9 1.7', 0.8, IVORY)
    + line('M78 27 l0.8 -1.6 l0.9 1.3 l0.9 -1.6 l0.9 1.3', 0.8, IVORY)
    + dot(70, 26.5, 1.9, GOLD) + dot(70, 26.5, 0.8)
    + line('M63 30 q2 3 0 6', 1)
    + '</g>'
    + `<path d="${waves}" fill="none" stroke="${CLAY}" stroke-width="2" stroke-linecap="round"/>`
}

const FIGURES: Record<MonsterId, () => string> = {
  medusa, cetus, sirens, scylla, polyphemus, minotaur, hydra, cerberus, keres, chimera, ladon, dragon, griffins, phoenix, sphinx,
}

/* ------------------------------------------------------------------ medallions */

let uid = 0

/** Meander band wrapped round a circle: each key unit is bent into the ring. */
function ringMeander(cx: number, cy: number, r0: number, r1v: number, units: number, color: string, w: number) {
  const unit: [number, number][] = [[0.08, 0], [0.08, 0.84], [0.84, 0.84], [0.84, 0.3], [0.38, 0.3], [0.38, 0.6], [0.6, 0.6]]
  const toXY = (u: number, v: number) => {
    const a = (u / units) * Math.PI * 2 - Math.PI / 2
    const r = r0 + v * (r1v - r0)
    return `${r1(cx + r * Math.cos(a))} ${r1(cy + r * Math.sin(a))}`
  }
  let d = ''
  for (let k = 0; k < units; k++) {
    const pts: string[] = []
    for (let i = 0; i < unit.length; i++) {
      const [u, v] = unit[i]
      if (i > 0 && unit[i - 1][1] === v) {
        // bend horizontal runs along the arc
        const u0 = unit[i - 1][0]
        for (let s = 1; s <= 4; s++) pts.push(toXY(k + u0 + ((u - u0) * s) / 4, v))
      } else pts.push(toXY(k + u, v))
    }
    d += `M${pts.join(' L')} `
  }
  return `<circle cx="${cx}" cy="${cy}" r="${r0}" fill="none" stroke="${color}" stroke-width="${w}"/>`
    + `<circle cx="${cx}" cy="${cy}" r="${r1v}" fill="none" stroke="${color}" stroke-width="${w}"/>`
    + `<path d="${d}" fill="none" stroke="${color}" stroke-width="${w}" stroke-linejoin="miter"/>`
}

/**
 * A kylix tondo: the painted roundel inside a drinking cup. `marker` is the compact globe version
 * (clay band with a dot border); `card` adds a full meander ring for the lore card.
 */
export function medallionSvg(id: MonsterId, size: 'marker' | 'card' = 'marker') {
  const n = ++uid
  const card = size === 'card'
  const R = card ? 60 : 50 // half the viewBox
  const field = card ? 45 : 43.5
  const band0 = field + 0.8
  const band1 = card ? 58 : 49
  const k = field / 44.5
  const dots = card ? '' : Array.from({ length: 22 }, (_, i) => {
    const a = (i / 22) * Math.PI * 2
    return `<circle cx="${r1(R + 46.4 * Math.cos(a))}" cy="${r1(R + 46.4 * Math.sin(a))}" r="1.15" fill="${GLAZE}"/>`
  }).join('')
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${R * 2} ${R * 2}" aria-hidden="true" focusable="false">`
    + `<defs><radialGradient id="gz${n}" cx="42%" cy="36%" r="70%"><stop offset="0" stop-color="#3a271c"/><stop offset=".55" stop-color="${GLAZE}"/><stop offset="1" stop-color="#0b0705"/></radialGradient>`
    + `<radialGradient id="cl${n}" cx="40%" cy="30%" r="80%"><stop offset="0" stop-color="#eb9560"/><stop offset="1" stop-color="#b85d2c"/></radialGradient>`
    + `<clipPath id="cp${n}"><circle cx="${R}" cy="${R}" r="${field}"/></clipPath></defs>`
    + `<circle cx="${R}" cy="${R}" r="${band1 + 0.6}" fill="url(#cl${n})"/>`
    + (card ? ringMeander(R, R, band0 + 1.6, band1 - 1.8, 22, GLAZE, 1.5) : dots)
    + `<circle cx="${R}" cy="${R}" r="${band1 + 0.6}" fill="none" stroke="${GOLD}" stroke-width="${card ? 1.4 : 1.2}"/>`
    + `<circle cx="${R}" cy="${R}" r="${band0}" fill="${GLAZE}"/>`
    + `<circle cx="${R}" cy="${R}" r="${field}" fill="url(#gz${n})"/>`
    + `<g clip-path="url(#cp${n})"><g transform="translate(${R} ${R}) scale(${r1(k * 100) / 100}) translate(-50 -50)">${FIGURES[id]()}</g></g>`
    // glaze gloss
    + `<path d="M${R - field * 0.72} ${R - field * 0.28} A${field * 0.8} ${field * 0.8} 0 0 1 ${R - field * 0.12} ${R - field * 0.78}" fill="none" stroke="#fff" stroke-opacity=".16" stroke-width="${card ? 3.2 : 3}" stroke-linecap="round"/>`
    + '</svg>'
}

/* ------------------------------------------------------------------ roaming sea creatures */

/** Side-view ketos swimming right, outlined so it reads against the sea. */
export function ketosSvg() {
  const body = 'M14 34 C24 22 38 44 54 34 C66 26 72 22 80 26'
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 64">`
    + `<g stroke="${GLAZE}" stroke-width="3.2" stroke-linejoin="round" stroke-linecap="round" fill="none">`
    + `<path d="${body}" stroke-width="13"/></g>`
    + `<path d="M4 22 L16 33 L5 44 L12 33 Z" fill="${CLAY}" stroke="${GLAZE}" stroke-width="2"/>`
    + `<path d="${body}" fill="none" stroke="${CLAY}" stroke-width="9" stroke-linecap="round"/>`
    + `<path d="${body}" fill="none" stroke="${GLAZE}" stroke-width="1.4" stroke-dasharray="0.1 5" stroke-linecap="round"/>`
    + `<path d="M30 30 L27 21 L35 28 Z M46 36 L44 27 L51 33 Z M62 29 L62 20 L68 27 Z" fill="${CLAY}" stroke="${GLAZE}" stroke-width="1.6" stroke-linejoin="round"/>`
    + `<path d="M74 28 C74 19 84 15 92 17 L110 19.5 C113 21 111 24.5 108 24.5 L98 25.5 L110 30 C108 34 100 34 94 31.5 C89 36 77 35 74 28 Z" fill="${CLAY}" stroke="${GLAZE}" stroke-width="2.2" stroke-linejoin="round"/>`
    + `<path d="M77 19 L72 9 L83 16.5 Z" fill="${CLAY}" stroke="${GLAZE}" stroke-width="1.6" stroke-linejoin="round"/>`
    + `<path d="M98 25.5 L110 30" stroke="${GLAZE}" stroke-width="1.4"/>`
    + `<path d="M99.5 26 l1 2 l1 -1.6 l1 2 l1 -1.6 l1 2" stroke="${IVORY}" stroke-width="1" fill="none"/>`
    + `<circle cx="89" cy="22" r="2.4" fill="${GOLD}"/><circle cx="89" cy="22" r="1" fill="${GLAZE}"/>`
    + `<path d="M64 34 C60 40 62 44 66 46" fill="none" stroke="${GLAZE}" stroke-width="5" stroke-linecap="round"/>`
    + `<path d="M64 34 C60 40 62 44 66 46" fill="none" stroke="${CLAY}" stroke-width="2.6" stroke-linecap="round"/>`
    + '</svg>'
}

/** Side-view hippocampus, Poseidon's sea-horse: horse in front, coiling fish tail behind. */
export function hippocampusSvg() {
  const tail = 'M50 36 C36 40 26 46 26 54 C26 60 34 62 38 57 C41 53 37 49 33 51'
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 64">`
    + `<path d="${tail}" fill="none" stroke="${GLAZE}" stroke-width="12" stroke-linecap="round"/>`
    + `<path d="${tail}" fill="none" stroke="${IVORY}" stroke-width="8" stroke-linecap="round"/>`
    + `<path d="${tail}" fill="none" stroke="${GLAZE}" stroke-width="1.2" stroke-dasharray="0.1 4" stroke-linecap="round"/>`
    + `<path d="M33 51 L22 46 L25 53 L20 60 Z" fill="${IVORY}" stroke="${GLAZE}" stroke-width="1.8" stroke-linejoin="round"/>`
    + `<path d="M51 31 L44 25 L53 26.5 L50 18 L58 22.5 L58 13 L64 19 L66 10 L69 15" fill="${GOLD}" stroke="${GLAZE}" stroke-width="1.6" stroke-linejoin="round"/>`
    + `<path d="M46 40 C46 30 52 20 62 14 C66 11 70 9.5 74 10 C78.5 12.5 83 17 89.5 22 C93 24.8 92.5 29 89 29.2 C85.5 29.4 82 28 79 26.5 C74 30 70 36 68 43 C62 47 52 47 46 40 Z" fill="${IVORY}" stroke="${GLAZE}" stroke-width="2.2" stroke-linejoin="round"/>`
    + `<path d="M71.5 11 L74 3.5 L76.5 11.5" fill="${IVORY}" stroke="${GLAZE}" stroke-width="1.4" stroke-linejoin="round"/>`
    + `<circle cx="79" cy="16.5" r="1.5" fill="${GLAZE}"/><circle cx="88.6" cy="25" r="1" fill="${GLAZE}"/>`
    + `<path d="M84 27.8 C86 27 88 27.4 90 28.2" fill="none" stroke="${GLAZE}" stroke-width="1"/>`
    + `<path d="M60 44 C64 50 70 52 74 49 M54 45 C56 52 61 56 66 55" fill="none" stroke="${GLAZE}" stroke-width="5" stroke-linecap="round"/>`
    + `<path d="M60 44 C64 50 70 52 74 49 M54 45 C56 52 61 56 66 55" fill="none" stroke="${IVORY}" stroke-width="2.6" stroke-linecap="round"/>`
    + `<path d="M74 49 l5 -4 l-1 6 Z M66 55 l5 -3 l-1 6 Z" fill="${GOLD}" stroke="${GLAZE}" stroke-width="1.2" stroke-linejoin="round"/>`
    + `<path d="M72 26 C68 30 66 34 65 38" fill="none" stroke="${GLAZE}" stroke-width="1" opacity=".6"/>`
    + '</svg>'
}

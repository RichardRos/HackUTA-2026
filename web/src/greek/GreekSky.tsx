import { memo } from 'react'

/**
 * The night sky behind the globe in Greek mode: a scatter of stars, a faint Milky Way, and the
 * constellations of the Perseus myth (with Cetus, the sea monster), Orion, the Pleiades and the Bear,
 * which Odysseus steered by (Odyssey 5.272–277). Star positions are real (RA hours, Dec degrees).
 */

type Star = [number, number]
type Figure = {
  name: string
  stars: Record<string, Star>
  lines: string[][]
  label: { at: string; dx: number; dy: number }
  cluster?: boolean
}
type Group = { ra0: number; dec0: number; x: number; y: number; k: number; figures: Figure[] }

const GROUPS: Group[] = [
  {
    // Perseus, Andromeda, her mother Cassiopeia, Pegasus, and Cetus the monster sent to devour her
    ra0: 1, dec0: 25, x: 330, y: 300, k: 5.4,
    figures: [
      {
        name: 'ΚΑΣΣΙΕΠΕΙΑ',
        stars: { caph: [0.153, 59.15], schedar: [0.675, 56.54], navi: [0.945, 60.72], ruchbah: [1.43, 60.24], segin: [1.907, 63.67] },
        lines: [['caph', 'schedar', 'navi', 'ruchbah', 'segin']],
        label: { at: 'navi', dx: 0, dy: -22 },
      },
      {
        name: 'ΑΝΔΡΟΜΕΔΑ',
        stars: { alpheratz: [0.14, 29.09], delta: [0.655, 30.86], mirach: [1.162, 35.62], almach: [2.065, 42.33], mu: [0.946, 38.5], nu: [0.83, 41.08] },
        lines: [['alpheratz', 'delta', 'mirach', 'almach'], ['mirach', 'mu', 'nu']],
        label: { at: 'mirach', dx: 22, dy: 16 },
      },
      {
        name: 'ΠΗΓΑΣΟΣ',
        stars: { markab: [23.079, 15.21], scheat: [23.063, 28.08], algenib: [0.221, 15.18], alpheratz: [0.14, 29.09], homam: [22.691, 10.83], enif: [21.736, 9.88] },
        lines: [['markab', 'scheat', 'alpheratz', 'algenib', 'markab'], ['markab', 'homam', 'enif']],
        label: { at: 'markab', dx: 4, dy: 26 },
      },
      {
        name: 'ΠΕΡΣΕΥΣ',
        stars: { eta: [2.845, 55.9], gamma: [3.08, 53.51], mirfak: [3.405, 49.86], delta: [3.715, 47.79], epsilon: [3.964, 40.01], zeta: [3.902, 31.88], kappa: [3.158, 44.86], algol: [3.136, 40.96] },
        lines: [['eta', 'gamma', 'mirfak', 'delta', 'epsilon', 'zeta'], ['mirfak', 'kappa', 'algol']],
        label: { at: 'gamma', dx: 6, dy: -18 },
      },
      {
        name: 'ΚΗΤΟΣ',
        stars: {
          menkar: [3.038, 4.09], lambda: [2.995, 8.91], mu: [2.749, 10.11], xi: [2.469, 8.46], gamma: [2.722, 3.24], delta: [2.658, 0.33],
          mira: [2.322, -2.98], zeta: [1.858, -10.33], tau: [1.734, -15.94], diphda: [0.726, -17.99], iota: [0.324, -8.82], theta: [1.4, -8.18], eta: [1.143, -10.18],
        },
        lines: [['menkar', 'lambda', 'mu', 'xi', 'gamma', 'menkar'], ['gamma', 'delta', 'mira', 'zeta', 'theta', 'eta', 'diphda', 'iota'], ['zeta', 'tau', 'diphda']],
        label: { at: 'theta', dx: 0, dy: 30 },
      },
      {
        name: 'ΠΛΕΙΑΔΕΣ',
        stars: { alcyone: [3.791, 24.105], atlas: [3.82, 24.053], electra: [3.747, 24.113], maia: [3.763, 24.368], merope: [3.772, 23.948], taygeta: [3.754, 24.467], pleione: [3.82, 24.137] },
        lines: [],
        label: { at: 'alcyone', dx: 0, dy: -14 },
        cluster: true,
      },
    ],
  },
  {
    ra0: 5.58, dec0: 0, x: 1385, y: 250, k: 12,
    figures: [{
      name: 'ΩΡΙΩΝ',
      stars: {
        betelgeuse: [5.919, 7.41], bellatrix: [5.419, 6.35], meissa: [5.585, 9.93], mintaka: [5.533, -0.3], alnilam: [5.604, -1.2],
        alnitak: [5.679, -1.94], saiph: [5.796, -9.67], rigel: [5.242, -8.2],
      },
      lines: [['meissa', 'betelgeuse', 'alnitak', 'saiph'], ['meissa', 'bellatrix', 'mintaka', 'rigel'], ['mintaka', 'alnilam', 'alnitak']],
      label: { at: 'saiph', dx: -30, dy: 30 },
    }],
  },
  {
    ra0: 12.4, dec0: 56, x: 1330, y: 700, k: 8.5,
    figures: [{
      name: 'ΑΡΚΤΟΣ',
      stars: { dubhe: [11.062, 61.75], merak: [11.031, 56.38], phecda: [11.897, 53.69], megrez: [12.257, 57.03], alioth: [12.9, 55.96], mizar: [13.399, 54.93], alkaid: [13.792, 49.31] },
      lines: [['dubhe', 'merak', 'phecda', 'megrez', 'dubhe'], ['megrez', 'alioth', 'mizar', 'alkaid']],
      label: { at: 'phecda', dx: 6, dy: 34 },
    }],
  },
]

function project(g: Group, [ra, dec]: Star): [number, number] {
  let dra = ra - g.ra0
  if (dra > 12) dra -= 24
  if (dra < -12) dra += 24
  // sky charts put east on the left: RA increases to the left
  return [g.x - dra * 15 * Math.cos((g.dec0 * Math.PI) / 180) * g.k, g.y - (dec - g.dec0) * g.k]
}

let seed = 11
const rnd = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646
const FIELD = Array.from({ length: 190 }, (_, i) => ({
  x: rnd() * 1600, y: rnd() * 900, r: 0.35 + rnd() ** 3 * 1.4, o: 0.25 + rnd() * 0.6, twinkle: i % 7 === 0, delay: rnd() * 6,
}))

function GreekSky() {
  return (
    <svg className="greek-sky" viewBox="0 0 1600 900" preserveAspectRatio="xMidYMid slice" aria-hidden="true" focusable="false">
      <defs>
        <radialGradient id="sky-milk" cx="50%" cy="50%" r="50%">
          <stop offset="0" stopColor="#c9d8e6" stopOpacity=".09" />
          <stop offset=".6" stopColor="#8fa7c0" stopOpacity=".04" />
          <stop offset="1" stopColor="#8fa7c0" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="sky-star" cx="50%" cy="50%" r="50%">
          <stop offset="0" stopColor="#fff6dc" />
          <stop offset=".35" stopColor="#f6dc8e" stopOpacity=".55" />
          <stop offset="1" stopColor="#f6dc8e" stopOpacity="0" />
        </radialGradient>
      </defs>
      <ellipse cx="800" cy="420" rx="980" ry="150" fill="url(#sky-milk)" transform="rotate(-24 800 420)" />
      {FIELD.map((s, i) => (
        <circle key={i} cx={s.x.toFixed(1)} cy={s.y.toFixed(1)} r={s.r.toFixed(2)} fill="#f3ead6" opacity={s.o.toFixed(2)}
          className={s.twinkle ? 'twinkle' : undefined} style={s.twinkle ? { animationDelay: `${s.delay.toFixed(2)}s` } : undefined} />
      ))}
      {GROUPS.map((g, gi) => g.figures.map((f) => {
        const p = (id: string) => project(g, f.stars[id])
        const [lx, ly] = p(f.label.at)
        return (
          <g key={`${gi}-${f.name}`} className="constellation">
            {f.lines.map((chain, j) => (
              <polyline key={j} points={chain.map((id) => p(id).map((n) => n.toFixed(1)).join(',')).join(' ')} />
            ))}
            {Object.keys(f.stars).map((id) => {
              const [x, y] = p(id)
              const r = f.cluster ? 1.1 : 2
              return (
                <g key={id}>
                  <circle cx={x.toFixed(1)} cy={y.toFixed(1)} r={(r * 3.2).toFixed(1)} fill="url(#sky-star)" />
                  <circle cx={x.toFixed(1)} cy={y.toFixed(1)} r={r} className="cstar" />
                </g>
              )
            })}
            <text x={(lx + f.label.dx).toFixed(1)} y={(ly + f.label.dy).toFixed(1)} lang="grc">{f.name}</text>
          </g>
        )
      }))}
      <text className="sky-quote" x="1268" y="792" textAnchor="end">
        “the Bear, which alone has no share in the baths of Ocean” · Od. 5.275
      </text>
    </svg>
  )
}

export default memo(GreekSky)

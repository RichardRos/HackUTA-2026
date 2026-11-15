/** A marble Ionic column: carved capital with volutes and egg-and-dart, fluted shaft, Attic base. */

function volute(cx: number, cy: number, dir: 1 | -1) {
  const pts: string[] = []
  for (let t = 0; t <= 1; t += 0.02) {
    const a = t * Math.PI * 2 * 2.6
    const r = 15 * (1 - t) + 1.6
    pts.push(`${(cx + dir * r * Math.cos(a)).toFixed(1)} ${(cy + r * Math.sin(a)).toFixed(1)}`)
  }
  return `M${pts.join(' L')}`
}

export default function IonicColumn({ side }: { side: 'left' | 'right' }) {
  return (
    <div className={`ionic ionic-${side}`} aria-hidden="true">
      <svg className="ionic-capital" viewBox="0 0 160 66" focusable="false">
        <defs>
          <linearGradient id={`marble-${side}`} x1="0" x2="1">
            <stop offset="0" stopColor="#b9ad9a" />
            <stop offset=".38" stopColor="#f6f1e7" />
            <stop offset=".72" stopColor="#ddd2c1" />
            <stop offset="1" stopColor="#a39581" />
          </linearGradient>
        </defs>
        <rect x="6" y="2" width="148" height="9" rx="1" fill={`url(#marble-${side})`} />
        <path d="M10 11 H150 C150 26 140 30 132 30 H28 C20 30 10 26 10 11 Z" fill={`url(#marble-${side})`} />
        <circle cx="26" cy="36" r="19" fill={`url(#marble-${side})`} />
        <circle cx="134" cy="36" r="19" fill={`url(#marble-${side})`} />
        <path d={volute(26, 36, -1)} fill="none" stroke="#8c7f6b" strokeWidth="1.6" />
        <path d={volute(134, 36, 1)} fill="none" stroke="#8c7f6b" strokeWidth="1.6" />
        <circle cx="26" cy="36" r="2.4" fill="#c9a24a" />
        <circle cx="134" cy="36" r="2.4" fill="#c9a24a" />
        {/* egg-and-dart moulding */}
        <path d="M44 34 H116 V50 C108 58 52 58 44 50 Z" fill={`url(#marble-${side})`} />
        {[52, 64, 76, 88, 100, 112].map((x) => (
          <g key={x}>
            <ellipse cx={x - 4} cy="42" rx="4.2" ry="6" fill="none" stroke="#8c7f6b" strokeWidth="1.2" />
            {x < 112 && <path d={`M${x + 2} 36 L${x + 2} 49 L${x} 45 M${x + 2} 49 L${x + 4} 45`} fill="none" stroke="#8c7f6b" strokeWidth="1" />}
          </g>
        ))}
        <path d="M10 11 H150" stroke="#c9a24a" strokeWidth="1.2" />
      </svg>
      <div className="ionic-shaft" />
      <svg className="ionic-base" viewBox="0 0 160 40" focusable="false">
        <defs>
          <linearGradient id={`marble-b-${side}`} x1="0" x2="1">
            <stop offset="0" stopColor="#b9ad9a" />
            <stop offset=".38" stopColor="#f6f1e7" />
            <stop offset=".72" stopColor="#ddd2c1" />
            <stop offset="1" stopColor="#a39581" />
          </linearGradient>
        </defs>
        <rect x="34" y="0" width="92" height="5" fill={`url(#marble-b-${side})`} />
        <rect x="28" y="5" width="104" height="9" rx="4.5" fill={`url(#marble-b-${side})`} />
        <path d="M34 14 H126 L120 20 H40 Z" fill="#c8bca9" />
        <rect x="22" y="20" width="116" height="10" rx="5" fill={`url(#marble-b-${side})`} />
        <rect x="14" y="30" width="132" height="10" fill={`url(#marble-b-${side})`} />
      </svg>
    </div>
  )
}

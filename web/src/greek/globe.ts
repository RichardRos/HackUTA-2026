import type { GlobeMethods } from 'react-globe.gl'
import { BufferAttribute, BufferGeometry, CanvasTexture, Points, ShaderMaterial, SRGBColorSpace, Vector3 } from 'three'
import { BLOOD, GOLD, IVORY, hippocampusSvg, ketosSvg } from './art'
import { VOYAGE } from './myth'

const rad = Math.PI / 180

/* ------------------------------------------------------------------ the wine-dark sea */

/** Draws a Greek key along a horizontal band: y is the band's top, h its height. */
function meanderRow(g: CanvasRenderingContext2D, y: number, h: number, unit: number, width: number) {
  g.beginPath()
  g.moveTo(0, y + 0.5)
  g.lineTo(width, y + 0.5)
  g.moveTo(0, y + h - 0.5)
  g.lineTo(width, y + h - 0.5)
  for (let x = 0; x < width; x += unit) {
    const u = (f: number) => x + f * unit
    const v = (f: number) => y + h - f * h
    g.moveTo(u(0.1), v(0))
    g.lineTo(u(0.1), v(0.84))
    g.lineTo(u(0.84), v(0.84))
    g.lineTo(u(0.84), v(0.3))
    g.lineTo(u(0.4), v(0.3))
    g.lineTo(u(0.4), v(0.6))
    g.lineTo(u(0.62), v(0.6))
  }
  g.stroke()
}

/**
 * Homer's οἶνοψ πόντος: a deep Aegean sea painted like an old chart, with scalloped waves,
 * a gilt graticule, a meander band for the equator and the ancient names of the seas.
 */
export function greekOceanTexture() {
  const W = 2048, H = 1024
  const c = document.createElement('canvas')
  c.width = W
  c.height = H
  const g = c.getContext('2d')!
  const tex = new CanvasTexture(c)
  tex.colorSpace = SRGBColorSpace
  tex.anisotropy = 4
  const X = (lng: number) => ((lng + 180) / 360) * W
  const Y = (lat: number) => ((90 - lat) / 180) * H

  const paint = () => {
    const sea = g.createLinearGradient(0, 0, 0, H)
    sea.addColorStop(0, '#07131f')
    sea.addColorStop(0.22, '#0c2537')
    sea.addColorStop(0.5, '#123a55')
    sea.addColorStop(0.78, '#0c2537')
    sea.addColorStop(1, '#07131f')
    g.fillStyle = sea
    g.fillRect(0, 0, W, H)

    // wine-dark and turquoise mottling, seeded so it paints the same every time
    let seed = 7
    const rnd = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646
    for (let i = 0; i < 70; i++) {
      const x = rnd() * W, y = H * 0.12 + rnd() * H * 0.76, r = 40 + rnd() * 140
      const blob = g.createRadialGradient(x, y, 0, x, y, r)
      const wine = i % 3 === 0
      blob.addColorStop(0, wine ? 'rgba(74,30,62,0.22)' : 'rgba(40,110,140,0.12)')
      blob.addColorStop(1, 'rgba(0,0,0,0)')
      g.fillStyle = blob
      g.fillRect(x - r, y - r, r * 2, r * 2)
    }

    // scalloped waves, widened toward the poles so they look even on the sphere
    g.lineCap = 'round'
    g.strokeStyle = 'rgba(150,200,215,0.17)'
    g.lineWidth = 1.2
    for (let lat = 72; lat >= -72; lat -= 3.2) {
      const stretch = 1 / Math.max(Math.cos(lat * rad), 0.3)
      const step = 22 * stretch
      const y = Y(lat)
      const offset = (Math.abs(Math.round(lat / 3.2)) % 2) * step * 0.5
      for (let x = offset + rnd() * 4; x < W + step; x += step) {
        const w = 4.2 * stretch
        g.beginPath()
        for (let k = 0; k < 3; k++) g.arc(x + k * w * 2, y, w, Math.PI, 0, true)
        g.stroke()
      }
    }

    // gilt graticule every 15 degrees
    g.strokeStyle = 'rgba(232,201,122,0.13)'
    g.lineWidth = 1
    for (let lng = -180; lng <= 180; lng += 15) {
      g.beginPath()
      g.moveTo(X(lng), 0)
      g.lineTo(X(lng), H)
      g.stroke()
    }
    for (let lat = -75; lat <= 75; lat += 15) {
      if (lat === 0) continue
      g.beginPath()
      g.moveTo(0, Y(lat))
      g.lineTo(W, Y(lat))
      g.stroke()
    }
    // tropics and polar circles, dotted like a chart
    g.setLineDash([2, 7])
    g.strokeStyle = 'rgba(232,201,122,0.32)'
    for (const lat of [23.44, -23.44, 66.56, -66.56]) {
      g.beginPath()
      g.moveTo(0, Y(lat))
      g.lineTo(W, Y(lat))
      g.stroke()
    }
    g.setLineDash([])

    // the equator as a gold meander band
    g.strokeStyle = 'rgba(232,201,122,0.55)'
    g.lineWidth = 1.4
    meanderRow(g, Y(0) - 6, 12, 14, W)

    // the seas by their Greek names, letter-spaced like a carved inscription
    const label = (text: string, lat: number, lng: number, px: number, alpha: number, spacing = 0.42) => {
      g.save()
      g.translate(X(lng), Y(lat))
      g.scale(1 / Math.cos(lat * rad), 1)
      g.font = `600 ${px}px 'GFS Didot', 'Palatino Linotype', Georgia, serif`
      g.fillStyle = `rgba(232,201,122,${alpha})`
      g.textAlign = 'center'
      g.textBaseline = 'middle'
      const chars = [...text]
      const widths = chars.map((ch) => g.measureText(ch).width + px * spacing)
      let x = -widths.reduce((a, b) => a + b, 0) / 2
      chars.forEach((ch, i) => {
        g.fillText(ch, x + widths[i] / 2, 0)
        x += widths[i]
      })
      g.restore()
    }
    label('ΩΚΕΑΝΟΣ', 33, -48, 30, 0.42)
    label('ΩΚΕΑΝΟΣ', 4, -140, 40, 0.38)
    label('ΩΚΕΑΝΟΣ', -42, -20, 26, 0.32)
    label('ΕΡΥΘΡΑ ΘΑΛΑΣΣΑ', -12, 76, 22, 0.38, 0.32)
    label('ΘΑΛΑΣΣΑ', 33.4, 18.2, 9, 0.5, 0.5)
    label('ΠΟΝΤΟΣ ΕΥΞΕΙΝΟΣ', 43.3, 34.4, 5.5, 0.55, 0.3)
    tex.needsUpdate = true
  }
  paint()
  // Repaint once the inscription face arrives; until then the serif fallback stands in.
  document.fonts?.load("600 30px 'GFS Didot'").then(paint, () => {})
  return tex
}

/* ------------------------------------------------------------------ textures */

/** An SVG drawn into a canvas texture; it fills in as soon as the image decodes. */
export function svgTexture(svg: string, w: number, h: number) {
  const c = document.createElement('canvas')
  c.width = w
  c.height = h
  const tex = new CanvasTexture(c)
  tex.flipY = false
  const img = new Image()
  img.onload = () => {
    c.getContext('2d')!.drawImage(img, 0, 0, w, h)
    tex.needsUpdate = true
  }
  img.src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
  return tex
}

/** Odysseus' ship from above: a Homeric “black ship” with red-painted cheeks, gold trim and a white sail. */
export function blackShipTexture() {
  const c = document.createElement('canvas')
  c.width = c.height = 64
  const g = c.getContext('2d')!
  g.translate(32, 32)
  g.lineCap = 'round'
  g.strokeStyle = 'rgba(243,228,198,0.9)'
  g.lineWidth = 1.6
  for (let y = -18; y <= 20; y += 4.2) {
    g.beginPath()
    g.moveTo(-8, y)
    g.lineTo(-27, y + 4)
    g.moveTo(8, y)
    g.lineTo(27, y + 4)
    g.stroke()
  }
  g.beginPath()
  g.moveTo(0, -31)
  g.quadraticCurveTo(9.5, -21, 9.5, -8)
  g.lineTo(8.5, 22)
  g.quadraticCurveTo(7, 29, 0, 30)
  g.quadraticCurveTo(-7, 29, -8.5, 22)
  g.lineTo(-9.5, -8)
  g.quadraticCurveTo(-9.5, -21, 0, -31)
  g.closePath()
  g.fillStyle = '#1b1310'
  g.fill()
  g.lineWidth = 2.4
  g.strokeStyle = GOLD
  g.stroke()
  g.fillStyle = BLOOD // μιλτοπάρῃοι: “vermilion-cheeked”
  for (const side of [-1, 1]) {
    g.beginPath()
    g.moveTo(1.5 * side, -27)
    g.quadraticCurveTo(7.5 * side, -20, 8 * side, -12)
    g.lineTo(3 * side, -14)
    g.closePath()
    g.fill()
  }
  g.fillStyle = IVORY
  g.strokeStyle = '#0b1622'
  g.lineWidth = 1.4
  g.beginPath()
  g.rect(-17, -5, 34, 6)
  g.fill()
  g.stroke()
  g.fillStyle = BLOOD
  g.fillRect(-17, -2.6, 34, 1.4)
  g.beginPath() // aphlaston, the curled stern ornament
  g.arc(0, 26, 3.2, 0, Math.PI * 2)
  g.strokeStyle = GOLD
  g.lineWidth = 1.6
  g.stroke()
  const tex = new CanvasTexture(c)
  tex.flipY = false
  return tex
}

/* ------------------------------------------------------------------ roaming sea creatures */

type Kind = 'ketos' | 'hippocampus'
type Swimmer = { kind: Kind; lat: number; lng: number; rLat: number; rLng: number; period: number; phase: number; dir: 1 | -1 }

const swim = (kind: Kind, lat: number, lng: number, rLat: number, rLng: number, period: number, phase: number, dir: 1 | -1): Swimmer =>
  ({ kind, lat, lng, rLat, rLng, period, phase, dir })

/** Loops chosen to stay in open water. */
const SWIMMERS: Swimmer[] = [
  swim('ketos', 34, -42, 7, 10, 120, 0.3, 1),
  swim('hippocampus', 45, -25, 4, 6, 90, 2.1, -1),
  swim('ketos', -28, -18, 8, 10, 130, 4.0, -1),
  swim('hippocampus', -5, -20, 5, 7, 100, 1.2, 1),
  swim('ketos', 32, -150, 8, 12, 140, 5.1, 1),
  swim('hippocampus', 30, 160, 6, 9, 110, 0.7, -1),
  swim('ketos', -32, -125, 9, 12, 150, 2.8, -1),
  swim('hippocampus', 2, -125, 5, 8, 95, 3.6, 1),
  swim('ketos', -22, 78, 8, 11, 135, 1.9, 1),
  swim('hippocampus', 13, 63, 4, 5, 80, 4.4, -1),
  swim('ketos', -52, 30, 4, 8, 125, 0.1, 1),
  swim('hippocampus', -38, 162, 4, 4, 85, 5.5, 1),
  swim('ketos', 25.5, -90, 2, 3, 90, 2.4, -1),
  swim('hippocampus', 34.5, 20, 1.2, 2, 70, 1.0, 1),
]

const creatureVertex = /* glsl */ `
  attribute float flip;
  attribute float tilt;
  attribute float phase;
  uniform float size;
  varying float vFlip;
  varying float vTilt;
  varying float vPhase;
  void main() {
    vFlip = flip;
    vTilt = tilt;
    vPhase = phase;
    gl_PointSize = size;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }`

const creatureFragment = /* glsl */ `
  uniform sampler2D map;
  uniform float time;
  uniform float wiggle;
  varying float vFlip;
  varying float vTilt;
  varying float vPhase;
  void main() {
    vec2 p = gl_PointCoord - 0.5;
    if (vFlip > 0.5) p.x = -p.x; // face the way it swims
    float c = cos(vTilt), s = sin(vTilt);
    p = vec2(c * p.x - s * p.y, s * p.x + c * p.y); // nose up or down with its course
    p.y += sin(p.x * 11.0 - time * 3.2 + vPhase) * wiggle * (0.55 - p.x); // the body ripples, the head stays steady
    vec2 uv = vec2(p.x + 0.5, p.y * 2.0 + 0.5); // the art is twice as wide as it is tall
    if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) discard;
    vec4 color = texture2D(map, uv);
    if (color.a < 0.35) discard;
    gl_FragColor = color;
  }`

/** Ketea and hippocampi circling the open oceans, drawn side-on and flipped to face their heading. */
export class SeaCreatureLayer {
  private globe: GlobeMethods
  private groups: { swimmers: Swimmer[]; points: Points; geometry: BufferGeometry; material: ShaderMaterial }[] = []
  private frame = 0
  private start = performance.now()

  constructor(globe: GlobeMethods) {
    this.globe = globe
    const px = Math.min(devicePixelRatio, 1.5)
    const spec: Record<Kind, { svg: string; size: number; wiggle: number }> = {
      ketos: { svg: ketosSvg(), size: 66, wiggle: 0.05 },
      hippocampus: { svg: hippocampusSvg(), size: 58, wiggle: 0.025 },
    }
    for (const kind of ['ketos', 'hippocampus'] as Kind[]) {
      const swimmers = SWIMMERS.filter((s) => s.kind === kind)
      const geometry = new BufferGeometry()
      geometry.setAttribute('position', new BufferAttribute(new Float32Array(swimmers.length * 3), 3))
      geometry.setAttribute('flip', new BufferAttribute(new Float32Array(swimmers.length), 1))
      geometry.setAttribute('tilt', new BufferAttribute(new Float32Array(swimmers.length), 1))
      geometry.setAttribute('phase', new BufferAttribute(Float32Array.from(swimmers, (s) => s.phase * 3), 1))
      const material = new ShaderMaterial({
        uniforms: {
          map: { value: svgTexture(spec[kind].svg, 256, 128) },
          size: { value: spec[kind].size * px },
          time: { value: 0 },
          wiggle: { value: spec[kind].wiggle },
        },
        vertexShader: creatureVertex,
        fragmentShader: creatureFragment,
        transparent: true,
        depthTest: true,
      })
      const points = new Points(geometry, material)
      points.frustumCulled = false
      points.renderOrder = 1
      globe.scene().add(points)
      this.groups.push({ swimmers, points, geometry, material })
    }
    const tick = () => {
      this.update()
      this.frame = requestAnimationFrame(tick)
    }
    this.frame = requestAnimationFrame(tick)
  }

  private at(s: Swimmer, t: number): [number, number] {
    const a = s.phase + (s.dir * 2 * Math.PI * t) / s.period
    const lat = s.lat + s.rLat * Math.sin(a)
    return [lat, s.lng + (s.rLng * Math.cos(a)) / Math.cos(s.lat * rad)]
  }

  private update() {
    const t = (performance.now() - this.start) / 1000
    const camera = this.globe.camera()
    const canvas = this.globe.renderer().domElement
    const w = canvas.clientWidth, h = canvas.clientHeight
    const a = new Vector3(), b = new Vector3()
    for (const { swimmers, geometry, material } of this.groups) {
      material.uniforms.time.value = t
      const pos = geometry.getAttribute('position') as BufferAttribute
      const flip = geometry.getAttribute('flip') as BufferAttribute
      const tilt = geometry.getAttribute('tilt') as BufferAttribute
      swimmers.forEach((s, i) => {
        const [lat, lng] = this.at(s, t)
        const [lat2, lng2] = this.at(s, t + 0.8)
        const p = this.globe.getCoords(lat, lng, 0.004)
        const q = this.globe.getCoords(lat2, lng2, 0.004)
        pos.setXYZ(i, p.x, p.y, p.z)
        a.set(p.x, p.y, p.z).project(camera)
        b.set(q.x, q.y, q.z).project(camera)
        const dx = (b.x - a.x) * w, dy = (b.y - a.y) * h
        flip.setX(i, dx < 0 ? 1 : 0)
        tilt.setX(i, Math.max(-0.6, Math.min(0.6, Math.atan2(dy, Math.abs(dx) + 1e-6))))
      })
      pos.needsUpdate = true
      flip.needsUpdate = true
      tilt.needsUpdate = true
    }
  }

  dispose() {
    cancelAnimationFrame(this.frame)
    for (const { points, geometry, material } of this.groups) {
      this.globe.scene().remove(points)
      geometry.dispose()
      ;(material.uniforms.map.value as CanvasTexture).dispose()
      material.dispose()
    }
  }
}

/* ------------------------------------------------------------------ Odysseus under sail */

const SAIL_S = 150 // Troy to Ithaca
const HOME_S = 6 // a pause on Ithaca before the next telling

function bearing(a: { lat: number; lng: number }, b: { lat: number; lng: number }) {
  const y = Math.sin((b.lng - a.lng) * rad) * Math.cos(b.lat * rad)
  const x = Math.cos(a.lat * rad) * Math.sin(b.lat * rad) - Math.sin(a.lat * rad) * Math.cos(b.lat * rad) * Math.cos((b.lng - a.lng) * rad)
  return (Math.atan2(y, x) / rad + 360) % 360
}
const nm = (a: { lat: number; lng: number }, b: { lat: number; lng: number }) => {
  const dLat = (b.lat - a.lat) * 60
  const dLng = (b.lng - a.lng) * 60 * Math.cos(((a.lat + b.lat) / 2) * rad)
  return Math.hypot(dLat, dLng)
}
const legs = VOYAGE.slice(1).map((p, i) => nm(VOYAGE[i], p))
const total = legs.reduce((s, d) => s + d, 0)

/** Where Odysseus' ship is `seconds` into the loop, as a fix the ship layer can dead-reckon from. */
export function odysseusFix(seconds: number) {
  const t = seconds % (SAIL_S + HOME_S)
  const knots = (total / SAIL_S) * 3600
  let along = (Math.min(t, SAIL_S) / SAIL_S) * total
  for (let i = 0; i < legs.length; i++) {
    if (along <= legs[i] || i === legs.length - 1) {
      const f = Math.min(along / legs[i], 1)
      const a = VOYAGE[i], b = VOYAGE[i + 1]
      const home = t >= SAIL_S
      return {
        mmsi: -1, name: 'Odysseus', category: 'Homeric black ship', age_s: 0,
        lat: a.lat + (b.lat - a.lat) * f, lon: a.lng + (b.lng - a.lng) * f,
        heading: bearing(a, b), course: bearing(a, b), speed_kn: home ? 0 : knots,
      }
    }
    along -= legs[i]
  }
  return null
}

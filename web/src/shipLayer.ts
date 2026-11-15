import type { GlobeMethods } from 'react-globe.gl'
import {
  BufferAttribute,
  BufferGeometry,
  CanvasTexture,
  Points,
  ShaderMaterial,
  Vector3,
} from 'three'

export type MovingShip = {
  mmsi: number
  name: string | null
  lat: number
  lon: number
  category: string
  heading: number | null
  course: number | null
  speed_kn: number | null
  age_s: number
}

const MAX_LIVE_S = 15 * 60 // stop extrapolating a fix older than this (live)
const MAX_PROJECTED_H = 6 // cap a time-lapse projection

export type LayerStyle = { texture: () => CanvasTexture; iconPx: number; alt: number; colorByCategory?: boolean }

/** Top-down airliner, nose up: light blue for passenger traffic, amber when `cargo`. */
export function planeTexture(cargo = false): CanvasTexture {
  const c = document.createElement('canvas')
  c.width = c.height = 64
  const g = c.getContext('2d')!
  g.translate(32, 32)
  g.beginPath()
  g.moveTo(0, -30) // nose
  g.quadraticCurveTo(4, -26, 4, -16)
  g.lineTo(4, -6)
  g.lineTo(28, 6) // right wing
  g.lineTo(28, 11)
  g.lineTo(4, 5)
  g.lineTo(3, 20)
  g.lineTo(11, 26) // right tailplane
  g.lineTo(11, 30)
  g.lineTo(0, 27)
  g.lineTo(-11, 30)
  g.lineTo(-11, 26)
  g.lineTo(-3, 20)
  g.lineTo(-4, 5)
  g.lineTo(-28, 11)
  g.lineTo(-28, 6)
  g.lineTo(-4, -6)
  g.lineTo(-4, -16)
  g.quadraticCurveTo(-4, -26, 0, -30)
  g.closePath()
  g.fillStyle = cargo ? '#ffc77a' : '#8fd3ff'
  g.fill()
  g.lineWidth = 2.5
  g.strokeStyle = '#0b1114'
  g.stroke()
  const tex = new CanvasTexture(c)
  tex.flipY = false
  return tex
}

/** Top-down cargo ship in the Figma's amber: pointed bow up, container rows, bridge at the stern. */
function shipTexture(): CanvasTexture {
  const c = document.createElement('canvas')
  c.width = c.height = 64
  const g = c.getContext('2d')!
  g.translate(32, 32)
  g.beginPath() // hull
  g.moveTo(0, -30)
  g.bezierCurveTo(9, -20, 10, -8, 10, 4)
  g.lineTo(10, 26)
  g.quadraticCurveTo(10, 30, 6, 30)
  g.lineTo(-6, 30)
  g.quadraticCurveTo(-10, 30, -10, 26)
  g.lineTo(-10, 4)
  g.bezierCurveTo(-10, -8, -9, -20, 0, -30)
  g.closePath()
  g.fillStyle = '#e8b56a'
  g.fill()
  g.lineWidth = 2
  g.strokeStyle = '#0b1114' // dark rim so the icon reads on land and sea
  g.stroke()
  const stacks = ['#c98a3e', '#a86f2c', '#d9a04f']
  for (let row = 0; row < 4; row++)
    for (let col = 0; col < 2; col++) {
      g.fillStyle = stacks[(row + col) % 3]
      g.fillRect(col ? 1 : -7, -14 + row * 7, 6, 5.5)
    }
  g.fillStyle = '#f2dcb1' // bridge
  g.fillRect(-7, 16, 14, 8)
  const tex = new CanvasTexture(c)
  tex.flipY = false // sample in the same top-down orientation as gl_PointCoord
  return tex
}

/** Trireme silhouette with oars and a central sail, bow pointing up. */
function triremeTexture(): CanvasTexture {
  const c = document.createElement('canvas')
  c.width = c.height = 64
  const g = c.getContext('2d')!
  g.translate(32, 32)

  g.lineCap = 'round'
  g.lineWidth = 2
  g.strokeStyle = '#102f46'
  for (let y = -20; y <= 20; y += 7) {
    g.beginPath()
    g.moveTo(-7, y)
    g.lineTo(-27, y + 3)
    g.moveTo(7, y)
    g.lineTo(27, y + 3)
    g.stroke()
  }

  g.beginPath()
  g.moveTo(0, -31)
  g.quadraticCurveTo(9, -21, 9, -8)
  g.lineTo(8, 23)
  g.quadraticCurveTo(7, 29, 0, 30)
  g.quadraticCurveTo(-7, 29, -8, 23)
  g.lineTo(-9, -8)
  g.quadraticCurveTo(-9, -21, 0, -31)
  g.closePath()
  g.fillStyle = '#c45c3e'
  g.fill()
  g.lineWidth = 2.5
  g.strokeStyle = '#102f46'
  g.stroke()

  g.fillStyle = '#eee3d2'
  g.fillRect(-1.5, -18, 3, 35)
  g.beginPath()
  g.moveTo(1, -17)
  g.lineTo(8, -4)
  g.lineTo(1, 1)
  g.closePath()
  g.fillStyle = '#e8c97a'
  g.fill()
  g.strokeStyle = '#102f46'
  g.lineWidth = 1
  g.stroke()

  for (let y = -11; y <= 17; y += 7) {
    for (const x of [-6, 6]) {
      g.beginPath()
      g.arc(x, y, 2, 0, Math.PI * 2)
      g.fillStyle = '#eee3d2'
      g.fill()
      g.lineWidth = 1
      g.strokeStyle = '#102f46'
      g.stroke()
    }
  }

  const tex = new CanvasTexture(c)
  tex.flipY = false
  return tex
}

/** Longship for tankers in Greek mode: narrow double-ended gold hull, shields along both rails, striped square sail. */
function longshipTexture(): CanvasTexture {
  const c = document.createElement('canvas')
  c.width = c.height = 64
  const g = c.getContext('2d')!
  g.translate(32, 32)

  g.beginPath() // hull, pointed at both ends
  g.moveTo(0, -31)
  g.quadraticCurveTo(9, -19, 9, -4)
  g.lineTo(9, 10)
  g.quadraticCurveTo(9, 23, 0, 31)
  g.quadraticCurveTo(-9, 23, -9, 10)
  g.lineTo(-9, -4)
  g.quadraticCurveTo(-9, -19, 0, -31)
  g.closePath()
  g.fillStyle = '#e8c97a'
  g.fill()
  g.lineWidth = 2.5
  g.strokeStyle = '#102f46'
  g.stroke()

  for (let i = 0, y = -15; y <= 19; y += 5.5, i++) // shields on both rails
    for (const x of [-8, 8]) {
      g.beginPath()
      g.arc(x, y, 2.6, 0, Math.PI * 2)
      g.fillStyle = (i + (x > 0 ? 1 : 0)) % 2 ? '#c45c3e' : '#eee3d2'
      g.fill()
      g.lineWidth = 1
      g.strokeStyle = '#102f46'
      g.stroke()
    }

  for (let i = 0; i < 7; i++) { // square sail on its yard, seen from above
    g.fillStyle = i % 2 ? '#eee3d2' : '#c45c3e'
    g.fillRect(-14 + i * 4, -7, 4, 6)
  }
  g.lineWidth = 1.5
  g.strokeStyle = '#102f46'
  g.strokeRect(-14, -7, 28, 6)

  const tex = new CanvasTexture(c)
  tex.flipY = false
  return tex
}

const vertex = /* glsl */ `
  attribute float angle;
  attribute float category;
  uniform float size;
  varying float vAngle;
  varying float vCategory;
  void main() {
    vAngle = angle;
    vCategory = category;
    gl_PointSize = size;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }`

const fragment = /* glsl */ `
  uniform sampler2D map;
  uniform float colorByCategory;
  varying float vAngle;
  varying float vCategory;
  void main() {
    // Turn the icon clockwise by vAngle: sample the texture at the inversely rotated point.
    vec2 p = gl_PointCoord - 0.5;
    float c = cos(vAngle), s = sin(vAngle);
    vec2 uv = vec2(c * p.x + s * p.y, -s * p.x + c * p.y) + 0.5;
    if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) discard;
    vec4 color = texture2D(map, uv);
    if (color.a < 0.4) discard;
    if (colorByCategory > 0.5) {
      float shade = min(dot(color.rgb, vec3(0.299, 0.587, 0.114)) / 0.73, 1.0);
      vec3 tint = vCategory < 0.5 ? vec3(1.0, 0.78, 0.48)
        : vCategory < 1.5 ? vec3(0.38, 0.78, 0.91) : vec3(0.68, 0.73, 0.76);
      color.rgb = tint * shade;
    }
    gl_FragColor = color;
  }`

export const SHIP_STYLE: LayerStyle = { texture: shipTexture, iconPx: 18, alt: 0.006, colorByCategory: true } // just above the country caps
export const TRIREME_STYLE: LayerStyle = { texture: triremeTexture, iconPx: 24, alt: 0.006 } // Greek mode: every live ship except tankers
export const LONGSHIP_STYLE: LayerStyle = { texture: longshipTexture, iconPx: 26, alt: 0.006 } // Greek mode: tankers
export const PLANE_STYLE: LayerStyle = { texture: () => planeTexture(), iconPx: 14, alt: 0.012 } // above the ships
export const CARGO_PLANE_STYLE: LayerStyle = { ...PLANE_STYLE, texture: () => planeTexture(true) }

/**
 * Every ship (or plane, with PLANE_STYLE) as one THREE.Points draw call: positions dead-reckoned from each ship's last fix,
 * speed and course every frame, and each icon turned to its course as seen on screen.
 */
export class ShipLayer {
  private ships: MovingShip[] = []
  private fixedAt = 0 // performance.now() when the current ship list arrived
  private motion = 1
  private points: Points
  private geometry = new BufferGeometry()
  private material: ShaderMaterial
  private frame = 0
  private screen: Float32Array = new Float32Array(0) // last projected x,y per ship (CSS px), for picking
  private globe: GlobeMethods
  private style: LayerStyle

  constructor(globe: GlobeMethods, style: LayerStyle = SHIP_STYLE) {
    this.globe = globe
    this.style = style
    this.material = new ShaderMaterial({
      uniforms: {
        map: { value: style.texture() },
        size: { value: style.iconPx * Math.min(devicePixelRatio, 1.5) },
        colorByCategory: { value: style.colorByCategory ? 1 : 0 },
      },
      vertexShader: vertex,
      fragmentShader: fragment,
      transparent: true,
      depthTest: true, // the globe hides ships on its far side
    })
    this.points = new Points(this.geometry, this.material)
    this.points.frustumCulled = false
    this.points.renderOrder = 2
    globe.scene().add(this.points)
    const tick = () => {
      this.update()
      this.frame = requestAnimationFrame(tick)
    }
    this.frame = requestAnimationFrame(tick)
  }

  setShips(ships: MovingShip[]) {
    this.ships = ships
    this.fixedAt = performance.now()
    this.geometry.setAttribute('position', new BufferAttribute(new Float32Array(ships.length * 3), 3))
    this.geometry.setAttribute('angle', new BufferAttribute(new Float32Array(ships.length), 1))
    this.geometry.setAttribute('category', new BufferAttribute(Float32Array.from(ships, (ship) =>
      ship.category === 'cargo' ? 0 : ship.category === 'tanker' ? 1 : 2), 1))
    this.screen = new Float32Array(ships.length * 2)
  }

  setMotion(multiplier: number) {
    this.motion = multiplier
  }

  /** Where ship i is now: its fix moved along its course at its speed for the elapsed time. */
  private position(s: MovingShip, nowMs: number): [number, number] {
    const course = s.course ?? s.heading
    if (!s.speed_kn || s.speed_kn < 0.5 || course == null) return [s.lat, s.lon]
    const realS = s.age_s + (nowMs - this.fixedAt) / 1000
    const seconds = this.motion === 1 ? Math.min(realS, MAX_LIVE_S) : Math.min(realS * this.motion, MAX_PROJECTED_H * 3600)
    const nm = (s.speed_kn * seconds) / 3600
    const rad = (course * Math.PI) / 180
    const lat = s.lat + (nm * Math.cos(rad)) / 60
    const lon = s.lon + (nm * Math.sin(rad)) / (60 * Math.max(Math.cos((s.lat * Math.PI) / 180), 0.05))
    return [Math.max(-89.9, Math.min(89.9, lat)), ((lon + 540) % 360) - 180]
  }

  private update() {
    const pos = this.geometry.getAttribute('position') as BufferAttribute | undefined
    const ang = this.geometry.getAttribute('angle') as BufferAttribute | undefined
    if (!pos || !ang || !this.ships.length) return
    const camera = this.globe.camera()
    const canvas = this.globe.renderer().domElement
    const w = canvas.clientWidth
    const h = canvas.clientHeight
    const now = performance.now()
    const a = new Vector3()
    const b = new Vector3()
    this.ships.forEach((s, i) => {
      const [lat, lon] = this.position(s, now)
      const p = this.globe.getCoords(lat, lon, this.style.alt)
      pos.setXYZ(i, p.x, p.y, p.z)
      // Screen angle of the course: project the ship and a point a little ahead of it.
      const course = ((s.heading ?? s.course ?? 0) * Math.PI) / 180
      const q = this.globe.getCoords(lat + 0.05 * Math.cos(course), lon + 0.05 * Math.sin(course), this.style.alt)
      a.set(p.x, p.y, p.z).project(camera)
      b.set(q.x, q.y, q.z).project(camera)
      ang.setX(i, Math.atan2(b.x - a.x, b.y - a.y)) // clockwise from screen-up
      this.screen[i * 2] = ((a.x + 1) / 2) * w
      this.screen[i * 2 + 1] = ((1 - a.y) / 2) * h
    })
    pos.needsUpdate = true
    ang.needsUpdate = true
  }

  /** The ship under a CSS-pixel point on the canvas, if any (front side of the globe only). */
  pick(x: number, y: number): MovingShip | null {
    const pos = this.geometry.getAttribute('position') as BufferAttribute | undefined
    if (!pos) return null
    const cam = this.globe.camera().position
    const p = new Vector3()
    let best: MovingShip | null = null
    let bestD = (this.style.iconPx / 2 + 6) ** 2 // a little larger than the icon, so it is easy to hit
    this.ships.forEach((s, i) => {
      const dx = this.screen[i * 2] - x
      const dy = this.screen[i * 2 + 1] - y
      const d = dx * dx + dy * dy
      if (d >= bestD) return
      // Front side only: the surface normal (globe centred at the origin) must face the camera.
      p.set(pos.getX(i), pos.getY(i), pos.getZ(i))
      if (p.dot(cam.clone().sub(p)) <= 0) return
      best = s
      bestD = d
    })
    return best
  }

  dispose() {
    cancelAnimationFrame(this.frame)
    this.globe.scene().remove(this.points)
    this.geometry.dispose()
    ;(this.material.uniforms.map.value as CanvasTexture).dispose()
    this.material.dispose()
  }
}

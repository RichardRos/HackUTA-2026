import { BufferAttribute, BufferGeometry, LineSegments, ShaderMaterial } from 'three'
import type { GlobeMethods } from 'react-globe.gl'

/** The u/v/speed grid this needs (a subset of weatherLayer's WeatherGrid). */
export type WindField = {
  lats: number[]
  lons: number[]
  wind_u: (number | null)[]
  wind_v: (number | null)[]
  wind_ms: (number | null)[]
}

const PARTICLES = 5000
const TRAIL = 18 // points per trail, so 17 segments
const SEGMENTS = TRAIL - 1
const DEG_PER_MS = 0.022 // degrees moved per frame for each m/s of wind (60 fps)
const ALT = 0.012
const MAX_LAT = 78

/** Bilinear wind at any point of a regular lat/lon grid, wrapping in longitude. null off-grid or on gaps. */
export function sampleWind(field: WindField, lat: number, lon: number): [number, number, number] | null {
  const { lats, lons } = field
  if (lats.length < 2 || lons.length < 2) return null
  const dLat = lats[1] - lats[0]
  const dLon = lons[1] - lons[0]
  const y = (lat - lats[0]) / dLat
  if (y < 0 || y > lats.length - 1) return null
  const x = ((((lon - lons[0]) / dLon) % lons.length) + lons.length) % lons.length
  const r0 = Math.min(Math.floor(y), lats.length - 2)
  const c0 = Math.floor(x)
  const c1 = (c0 + 1) % lons.length
  const fy = y - r0
  const fx = x - c0
  const at = (r: number, c: number) => r * lons.length + c
  let u = 0
  let v = 0
  let s = 0
  for (const [r, c, w] of [[r0, c0, (1 - fx) * (1 - fy)], [r0, c1, fx * (1 - fy)], [r0 + 1, c0, (1 - fx) * fy], [r0 + 1, c1, fx * fy]]) {
    const uu = field.wind_u[at(r, c)]
    const vv = field.wind_v[at(r, c)]
    if (uu == null || vv == null || !Number.isFinite(uu) || !Number.isFinite(vv)) return null
    const ss = field.wind_ms[at(r, c)]
    u += uu * w
    v += vv * w
    s += (ss != null && Number.isFinite(ss) ? ss : Math.hypot(uu, vv)) * w
  }
  return [u, v, s]
}

// Each vertex carries its trail fade and the local wind speed. Faster air -> particles cover more
// ground per frame (so trails stretch), and the colour runs teal -> white -> storm amber.
const vertex = /* glsl */ `
  attribute float alpha;
  attribute float speed;
  varying float vAlpha;
  varying float vSpeed;
  void main() {
    vAlpha = alpha;
    vSpeed = speed;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }`
const fragment = /* glsl */ `
  varying float vAlpha;
  varying float vSpeed;
  void main() {
    vec3 calm = vec3(0.36, 0.66, 0.78);
    vec3 fresh = vec3(0.86, 0.96, 1.0);
    vec3 storm = vec3(1.0, 0.64, 0.36);
    vec3 color = mix(calm, fresh, smoothstep(2.0, 12.0, vSpeed));
    color = mix(color, storm, smoothstep(15.0, 25.0, vSpeed));
    float strength = 0.5 + 0.5 * smoothstep(1.0, 14.0, vSpeed);
    gl_FragColor = vec4(color, vAlpha * strength);
  }`

/**
 * Wind as a swirling particle flow: particles drift along the interpolated wind field, each leaving a
 * fading trail, and respawn at random after a short life, so the field reads as moving streamlines
 * that curl around highs and lows.
 */
export class WindFlow {
  readonly object: LineSegments
  private readonly geometry = new BufferGeometry()
  private readonly material: ShaderMaterial
  private readonly field: WindField
  private readonly globe: GlobeMethods
  private readonly lat = new Float32Array(PARTICLES)
  private readonly lon = new Float32Array(PARTICLES)
  private readonly age = new Float32Array(PARTICLES)
  private readonly life = new Float32Array(PARTICLES)
  private readonly trail = new Float32Array(PARTICLES * TRAIL * 3) // ring of xyz per particle
  private readonly trailSpeed = new Float32Array(PARTICLES * TRAIL)
  private head = 0
  private last = 0

  constructor(globe: GlobeMethods, field: WindField) {
    this.globe = globe
    this.field = field
    this.geometry.setAttribute('position', new BufferAttribute(new Float32Array(PARTICLES * SEGMENTS * 2 * 3), 3))
    this.geometry.setAttribute('alpha', new BufferAttribute(new Float32Array(PARTICLES * SEGMENTS * 2), 1))
    this.geometry.setAttribute('speed', new BufferAttribute(new Float32Array(PARTICLES * SEGMENTS * 2), 1))
    this.material = new ShaderMaterial({
      vertexShader: vertex,
      fragmentShader: fragment,
      transparent: true,
      depthTest: true,
      depthWrite: false,
    })
    this.object = new LineSegments(this.geometry, this.material)
    this.object.frustumCulled = false
    this.object.renderOrder = 3
    for (let p = 0; p < PARTICLES; p++) {
      this.spawn(p)
      this.age[p] = Math.random() * this.life[p] // stagger lives so respawns don't pulse
    }
  }

  private spawn(p: number) {
    // Uniform over the sphere's area between the poles' caps, which the grid does not cover.
    const limit = Math.sin((MAX_LAT * Math.PI) / 180)
    this.lat[p] = (Math.asin((Math.random() * 2 - 1) * limit) * 180) / Math.PI
    this.lon[p] = Math.random() * 360 - 180
    this.age[p] = 0
    this.life[p] = 70 + Math.random() * 140
    const c = this.globe.getCoords(this.lat[p], this.lon[p], ALT)
    const s = sampleWind(this.field, this.lat[p], this.lon[p])?.[2] ?? 0
    for (let k = 0; k < TRAIL; k++) {
      this.trail.set([c.x, c.y, c.z], (p * TRAIL + k) * 3) // zero-length trail until it moves
      this.trailSpeed[p * TRAIL + k] = s
    }
  }

  /** Advance every particle by `frames` 60-fps frames and rebuild the trail segments. */
  step(frames = 1) {
    this.head = (this.head + 1) % TRAIL
    for (let p = 0; p < PARTICLES; p++) {
      const wind = sampleWind(this.field, this.lat[p], this.lon[p])
      this.age[p] += frames
      if (!wind || this.age[p] > this.life[p] || Math.hypot(wind[0], wind[1]) < 0.3) {
        this.spawn(p)
        continue
      }
      const [u, v, s] = wind
      const lat = this.lat[p] + v * DEG_PER_MS * frames
      if (Math.abs(lat) > MAX_LAT) {
        this.spawn(p)
        continue
      }
      const cos = Math.max(0.2, Math.cos((lat * Math.PI) / 180))
      this.lat[p] = lat
      this.lon[p] = ((this.lon[p] + (u * DEG_PER_MS * frames) / cos + 540) % 360) - 180
      const c = this.globe.getCoords(lat, this.lon[p], ALT)
      this.trail.set([c.x, c.y, c.z], (p * TRAIL + this.head) * 3)
      this.trailSpeed[p * TRAIL + this.head] = s
    }
    this.rebuild()
  }

  /** Time-based step for requestAnimationFrame; caps the catch-up after a hidden tab. */
  tick(time: number) {
    const frames = this.last ? Math.min(3, (time - this.last) / (1000 / 60)) : 1
    this.last = time
    this.step(frames)
  }

  private rebuild() {
    const pos = this.geometry.getAttribute('position') as BufferAttribute
    const alpha = this.geometry.getAttribute('alpha') as BufferAttribute
    const speed = this.geometry.getAttribute('speed') as BufferAttribute
    const P = pos.array as Float32Array
    const A = alpha.array as Float32Array
    const S = speed.array as Float32Array
    for (let p = 0; p < PARTICLES; p++) {
      // Fade in over the first frames of a life and out over the last, so particles never pop.
      const lifeFade = Math.min(1, this.age[p] / 12, (this.life[p] - this.age[p]) / 20)
      for (let k = 0; k < SEGMENTS; k++) {
        const a = (this.head - k + TRAIL) % TRAIL
        const b = (a - 1 + TRAIL) % TRAIL
        const out = (p * SEGMENTS + k) * 2
        const ia = (p * TRAIL + a) * 3
        const ib = (p * TRAIL + b) * 3
        P[out * 3] = this.trail[ia]
        P[out * 3 + 1] = this.trail[ia + 1]
        P[out * 3 + 2] = this.trail[ia + 2]
        P[out * 3 + 3] = this.trail[ib]
        P[out * 3 + 4] = this.trail[ib + 1]
        P[out * 3 + 5] = this.trail[ib + 2]
        const fade = Math.max(0, lifeFade) * (1 - k / SEGMENTS)
        A[out] = fade
        A[out + 1] = fade * (1 - 1 / SEGMENTS)
        S[out] = this.trailSpeed[p * TRAIL + a]
        S[out + 1] = this.trailSpeed[p * TRAIL + b]
      }
    }
    pos.needsUpdate = alpha.needsUpdate = speed.needsUpdate = true
  }

  dispose() {
    this.geometry.dispose()
    this.material.dispose()
  }
}

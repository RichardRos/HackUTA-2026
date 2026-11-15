import {
  BufferAttribute,
  BufferGeometry,
  CanvasTexture,
  Color,
  LineSegments,
  Points,
  ShaderMaterial,
  SRGBColorSpace,
  Vector3,
} from 'three'
import type { GlobeMethods } from 'react-globe.gl'

export type WeatherGrid = {
  lats: number[]
  lons: number[]
  temp_c: (number | null)[]
  precip_mm: (number | null)[]
  cloud_pct?: (number | null)[]
  wind_u: (number | null)[]
  wind_v: (number | null)[]
  wind_ms: (number | null)[]
  stale?: boolean
}

export type MarineGrid = {
  lats: number[]
  lons: number[]
  wave_m: (number | null)[]
  wave_period_s?: (number | null)[]
  wave_u: (number | null)[]
  wave_v: (number | null)[]
  stale?: boolean
}

const finite = (value: number | null | undefined): value is number =>
  typeof value === 'number' && Number.isFinite(value)

function nearestIndex(values: number[], value: number, longitude = false) {
  let best = -1
  let distance = Infinity
  values.forEach((candidate, index) => {
    if (!finite(candidate)) return
    let delta = Math.abs(candidate - value)
    if (longitude) delta = Math.min(delta, 360 - delta)
    if (delta < distance) {
      best = index
      distance = delta
    }
  })
  return best
}

export function nearestWeatherCell(grid: WeatherGrid, lat: number, lon: number) {
  const row = nearestIndex(grid.lats, lat)
  const col = nearestIndex(grid.lons, lon, true)
  if (row < 0 || col < 0) return null
  const i = row * grid.lons.length + col
  return {
    temp: grid.temp_c[i],
    rain: grid.precip_mm[i],
    wind: grid.wind_ms[i],
    stale: !!grid.stale,
  }
}

const mix = (a: Color, b: Color, t: number) => a.clone().lerp(b, Math.max(0, Math.min(1, t)))

function tempColor(temp: number, rain: number) {
  const cold = new Color('#397fc1')
  const mild = new Color('#46b9a3')
  const hot = new Color('#e8784f')
  const value = temp <= 15 ? mix(cold, mild, (temp + 20) / 35) : mix(mild, hot, (temp - 15) / 25)
  return mix(value, new Color('#62a9e7'), Math.min(0.72, Math.max(0, rain) / 12) * 0.72).convertLinearToSRGB()
}

/** Create an equirectangular, row-major texture without changing the shared ocean material. */
export function weatherTexture(grid: WeatherGrid) {
  const width = 720
  const height = 360
  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const context = canvas.getContext('2d')
  if (!context || !grid.lats.length || !grid.lons.length) return new CanvasTexture(canvas)
  const step = grid.lats.length > 1
    ? Math.abs(grid.lats[1] - grid.lats[0])
    : 180
  const sourceWidth = Math.max(1, Math.round(360 / (grid.lons.length > 1
    ? Math.abs(grid.lons[1] - grid.lons[0])
    : 360)))
  const sourceHeight = Math.max(1, Math.round(180 / step))
  const source = document.createElement('canvas')
  source.width = sourceWidth
  source.height = sourceHeight
  const sourceContext = source.getContext('2d')
  if (!sourceContext) return new CanvasTexture(canvas)
  const image = sourceContext.createImageData(sourceWidth, sourceHeight)
  for (let y = 0; y < sourceHeight; y++) {
    const row = nearestIndex(grid.lats, 90 - ((y + 0.5) / sourceHeight) * 180)
    if (row < 0) continue
    for (let x = 0; x < sourceWidth; x++) {
      const col = nearestIndex(grid.lons, -180 + ((x + 0.5) / sourceWidth) * 360, true)
      if (col < 0) continue
      const i = row * grid.lons.length + col
      const temp = grid.temp_c[i]
      if (!finite(temp)) continue
      const color = tempColor(temp, grid.precip_mm[i] ?? 0)
      const at = (y * sourceWidth + x) * 4
      image.data[at] = color.r * 255
      image.data[at + 1] = color.g * 255
      image.data[at + 2] = color.b * 255
      image.data[at + 3] = 220
    }
  }
  sourceContext.putImageData(image, 0, 0)
  context.imageSmoothingEnabled = true
  context.imageSmoothingQuality = 'high'
  context.drawImage(source, 0, 0, width, height)
  const texture = new CanvasTexture(canvas)
  texture.colorSpace = SRGBColorSpace
  texture.needsUpdate = true
  return texture
}

// Wind: every streak carries its speed (m/s). Faster wind -> longer streaks that travel farther and
// cycle faster, brighter colour (teal -> white -> storm amber) and a lateral gust wobble above ~8 m/s.
const windVertex = /* glsl */ `
  attribute float phase;
  attribute float flow;
  attribute float speed;
  attribute vec3 advect;
  uniform float uTime;
  uniform float uAnimate;
  varying float vPulse;
  varying float vFade;
  varying float vSpeed;
  void main() {
    float cycle = fract(uTime * flow * 0.12 + phase);
    vec3 moved = position + advect * ((cycle - 0.5) * uAnimate);
    float gust = smoothstep(8.0, 25.0, speed);
    vec3 side = normalize(cross(advect, position));
    moved += side * sin(uTime * (2.0 + flow * 2.5) + phase * 40.0) * gust * 0.35 * uAnimate;
    vFade = mix(1.0, smoothstep(0.0, 0.14, cycle) * (1.0 - smoothstep(0.86, 1.0, cycle)), uAnimate);
    vPulse = 0.72 + 0.28 * (0.5 + 0.5 * sin(uTime * flow * (1.0 + gust * 2.0) + phase * 6.28318));
    vSpeed = speed;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(moved, 1.0);
  }`
const windFragment = /* glsl */ `
  varying float vPulse;
  varying float vFade;
  varying float vSpeed;
  void main() {
    vec3 calm = vec3(0.36, 0.66, 0.78);
    vec3 fresh = vec3(0.86, 0.96, 1.0);
    vec3 storm = vec3(1.0, 0.64, 0.36);
    vec3 color = mix(calm, fresh, smoothstep(2.0, 12.0, vSpeed));
    color = mix(color, storm, smoothstep(15.0, 25.0, vSpeed));
    float strength = 0.35 + 0.6 * smoothstep(1.0, 15.0, vSpeed);
    gl_FragColor = vec4(color, vPulse * vFade * strength);
  }`

// Waves: each marine point bobs along its travel direction on a cycle set by the wave period, with
// amplitude, dot size and ripple rings scaled by significant wave height. High seas (>~3 m) add a
// second ring and shift the colour from teal toward orange.
const waveVertex = /* glsl */ `
  attribute float height;
  attribute float period;
  attribute float phase;
  attribute vec3 travel;
  uniform float pixelRatio;
  uniform float uTime;
  uniform float uAnimate;
  varying float vHeight;
  varying float vCycle;
  void main() {
    float h = clamp(height, 0.0, 8.0);
    float rate = 1.0 / (period * 0.35);
    vCycle = fract(uTime * rate + phase);
    float swell = sin(vCycle * 6.28318);
    float amp = (0.08 + h * 0.09) * uAnimate;
    vec3 up = normalize(position);
    vec3 moved = position + travel * swell * amp + up * max(swell, 0.0) * h * 0.02 * uAnimate;
    vHeight = height;
    gl_PointSize = (6.0 + h * 2.4) * (1.0 + 0.12 * swell * uAnimate * smoothstep(0.5, 4.0, h)) * pixelRatio;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(moved, 1.0);
  }`
const waveFragment = /* glsl */ `
  uniform float uAnimate;
  varying float vHeight;
  varying float vCycle;
  float ring(float r, float t) {
    float radius = 0.2 + t * 0.3;
    return (1.0 - smoothstep(0.0, 0.045, abs(r - radius))) * (1.0 - t);
  }
  void main() {
    float r = length(gl_PointCoord - 0.5);
    float intensity = clamp(vHeight / 4.0, 0.0, 1.0);
    float core = 1.0 - smoothstep(0.17, 0.23, r);
    float rings = ring(r, vCycle) * (0.35 + 0.65 * intensity);
    rings += ring(r, fract(vCycle + 0.5)) * smoothstep(2.5, 5.0, vHeight);
    rings *= uAnimate;
    vec3 color = mix(vec3(0.24, 0.74, 0.79), vec3(1.0, 0.61, 0.35), clamp(vHeight / 5.0, 0.0, 1.0));
    float alpha = max(core * 0.88, rings * 0.7);
    if (alpha < 0.01) discard;
    gl_FragColor = vec4(mix(color, vec3(0.9, 0.98, 1.0), rings * (1.0 - core) * 0.3), alpha);
  }`

/** Wind streaks advect along local vectors scaled by speed; wave points swell and ripple with height and period. */
export class WeatherOverlay {
  private readonly wind: LineSegments
  private readonly waves: Points
  private readonly windGeometry: BufferGeometry
  private readonly waveGeometry: BufferGeometry
  private readonly windMaterial: ShaderMaterial
  private readonly waveMaterial: ShaderMaterial
  private readonly globe: GlobeMethods
  private frame = 0
  private started = false
  private readonly motionQuery = matchMedia('(prefers-reduced-motion: reduce)')
  private reducedMotion = this.motionQuery.matches
  private disposed = false
  private readonly motionChange = (event: MediaQueryListEvent) => {
    this.reducedMotion = event.matches
    this.windMaterial.uniforms.uAnimate.value = this.reducedMotion ? 0 : 1
    this.waveMaterial.uniforms.uAnimate.value = this.reducedMotion ? 0 : 1
    if (this.reducedMotion) {
      cancelAnimationFrame(this.frame)
      this.started = false
    }
    else this.animate()
  }

  constructor(globe: GlobeMethods, weather: WeatherGrid, marine: MarineGrid) {
    this.globe = globe
    this.windGeometry = new BufferGeometry()
    const positions: number[] = []
    const phases: number[] = []
    const flows: number[] = []
    const advections: number[] = []
    const speeds: number[] = []
    weather.lats.forEach((lat, row) => weather.lons.forEach((lon, col) => {
      const i = row * weather.lons.length + col
      const u = weather.wind_u[i]
      const v = weather.wind_v[i]
      if (!finite(lat) || !finite(lon) || !finite(u) || !finite(v) || Math.hypot(u, v) < 0.2) return
      const magnitude = Math.hypot(u, v)
      const speed = finite(weather.wind_ms[i]) ? weather.wind_ms[i]! : magnitude
      // Streak length (degrees) grows with speed: ~0.6 deg in light air, ~3 deg in a gale.
      const length = 0.5 + Math.min(2.5, speed * 0.12)
      const dlat = (v / magnitude) * length
      const dlon = (u / magnitude) * length / Math.max(0.25, Math.cos(lat * Math.PI / 180))
      const a = globe.getCoords(lat - dlat / 2, lon - dlon / 2, 0.018)
      const b = globe.getCoords(lat + dlat / 2, lon + dlon / 2, 0.018)
      positions.push(a.x, a.y, a.z, b.x, b.y, b.z)
      const phase = ((i * 37) % 101) / 101
      const flow = 0.4 + Math.min(3, speed / 8)
      // Distance a streak travels per cycle also grows with speed.
      const travel = 2 + Math.min(6, speed * 0.3)
      const direction = new Vector3(b.x - a.x, b.y - a.y, b.z - a.z).normalize().multiplyScalar(travel)
      phases.push(phase, phase)
      flows.push(flow, flow)
      speeds.push(speed, speed)
      advections.push(direction.x, direction.y, direction.z, direction.x, direction.y, direction.z)
    }))
    this.windGeometry.setAttribute('position', new BufferAttribute(new Float32Array(positions), 3))
    this.windGeometry.setAttribute('phase', new BufferAttribute(new Float32Array(phases), 1))
    this.windGeometry.setAttribute('flow', new BufferAttribute(new Float32Array(flows), 1))
    this.windGeometry.setAttribute('speed', new BufferAttribute(new Float32Array(speeds), 1))
    this.windGeometry.setAttribute('advect', new BufferAttribute(new Float32Array(advections), 3))
    this.windMaterial = new ShaderMaterial({
      uniforms: { uTime: { value: 0 }, uAnimate: { value: this.reducedMotion ? 0 : 1 } },
      vertexShader: windVertex,
      fragmentShader: windFragment,
      transparent: true,
      depthTest: true,
      depthWrite: false,
    })
    this.wind = new LineSegments(this.windGeometry, this.windMaterial)
    this.wind.renderOrder = 3

    this.waveGeometry = new BufferGeometry()
    const wavePositions: number[] = []
    const heights: number[] = []
    const periods: number[] = []
    const wavePhases: number[] = []
    const travels: number[] = []
    marine.lats.forEach((lat, row) => marine.lons.forEach((lon, col) => {
      const i = row * marine.lons.length + col
      const height = marine.wave_m[i]
      if (!finite(lat) || !finite(lon) || !finite(height) || height < 0) return
      const p = globe.getCoords(lat, lon, 0.016)
      wavePositions.push(p.x, p.y, p.z)
      heights.push(height)
      const period = marine.wave_period_s?.[i]
      periods.push(finite(period) ? Math.max(3, Math.min(20, period)) : 8)
      wavePhases.push(((i * 53) % 97) / 97)
      // Unit tangent on the globe pointing the way the waves travel (zero if direction unknown).
      const u = marine.wave_u[i]
      const v = marine.wave_v[i]
      const direction = new Vector3()
      if (finite(u) && finite(v) && Math.hypot(u, v) > 1e-6) {
        const step = 0.5 / Math.hypot(u, v)
        const q = globe.getCoords(lat + v * step, lon + (u * step) / Math.max(0.25, Math.cos(lat * Math.PI / 180)), 0.016)
        direction.set(q.x - p.x, q.y - p.y, q.z - p.z).normalize()
      }
      travels.push(direction.x, direction.y, direction.z)
    }))
    this.waveGeometry.setAttribute('position', new BufferAttribute(new Float32Array(wavePositions), 3))
    this.waveGeometry.setAttribute('height', new BufferAttribute(new Float32Array(heights), 1))
    this.waveGeometry.setAttribute('period', new BufferAttribute(new Float32Array(periods), 1))
    this.waveGeometry.setAttribute('phase', new BufferAttribute(new Float32Array(wavePhases), 1))
    this.waveGeometry.setAttribute('travel', new BufferAttribute(new Float32Array(travels), 3))
    this.waveMaterial = new ShaderMaterial({
      uniforms: {
        pixelRatio: { value: Math.min(devicePixelRatio, 1.5) },
        uTime: { value: 0 },
        uAnimate: { value: this.reducedMotion ? 0 : 1 },
      },
      vertexShader: waveVertex,
      fragmentShader: waveFragment,
      transparent: true,
      depthTest: true,
      depthWrite: false,
    })
    this.waves = new Points(this.waveGeometry, this.waveMaterial)
    this.waves.renderOrder = 4
    globe.scene().add(this.wind, this.waves)
    this.motionQuery.addEventListener('change', this.motionChange)
    this.animate()
  }

  private animate = () => {
    if (this.disposed || this.reducedMotion || this.started) return
    this.started = true
    const tick = (time: number) => {
      this.windMaterial.uniforms.uTime.value = time * 0.001
      this.waveMaterial.uniforms.uTime.value = time * 0.001
      this.started = false
      if (!this.disposed && !this.reducedMotion) {
        this.started = true
        this.frame = requestAnimationFrame(tick)
      }
    }
    this.frame = requestAnimationFrame(tick)
  }

  dispose() {
    this.disposed = true
    cancelAnimationFrame(this.frame)
    this.motionQuery.removeEventListener('change', this.motionChange)
    this.globe.scene().remove(this.wind, this.waves)
    this.windGeometry.dispose()
    this.waveGeometry.dispose()
    this.windMaterial.dispose()
    this.waveMaterial.dispose()
  }
}

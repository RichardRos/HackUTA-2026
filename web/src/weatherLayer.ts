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

const windVertex = /* glsl */ `
  attribute float phase;
  attribute float flow;
  attribute vec3 advect;
  uniform float uTime;
  uniform float uAnimate;
  varying float vPulse;
  varying float vFade;
  void main() {
    float cycle = fract(uTime * flow * 0.12 + phase);
    vec3 moved = position + advect * ((cycle - 0.5) * 5.0 * uAnimate);
    vFade = mix(1.0, smoothstep(0.0, 0.14, cycle) * (1.0 - smoothstep(0.86, 1.0, cycle)), uAnimate);
    vPulse = 0.72 + 0.28 * (0.5 + 0.5 * sin(uTime * flow + phase * 6.28318));
    gl_Position = projectionMatrix * modelViewMatrix * vec4(moved, 1.0);
  }`
const windFragment = /* glsl */ `
  varying float vPulse;
  varying float vFade;
  void main() { gl_FragColor = vec4(0.48, 0.84, 0.91, vPulse * vFade * 0.82); }`
const waveVertex = /* glsl */ `
  attribute float height;
  uniform float pixelRatio;
  varying float vHeight;
  void main() {
    vHeight = height;
    gl_PointSize = (3.0 + clamp(height, 0.0, 8.0) * 1.2) * pixelRatio;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }`
const waveFragment = /* glsl */ `
  varying float vHeight;
  void main() {
    vec2 p = gl_PointCoord - 0.5;
    float edge = 1.0 - smoothstep(0.38, 0.5, length(p));
    vec3 color = mix(vec3(0.24, 0.74, 0.79), vec3(1.0, 0.61, 0.35), clamp(vHeight / 5.0, 0.0, 1.0));
    gl_FragColor = vec4(color, edge * 0.88);
  }`

/** Wind streaks advect along local vectors; wave height controls each marine point. */
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
    weather.lats.forEach((lat, row) => weather.lons.forEach((lon, col) => {
      const i = row * weather.lons.length + col
      const u = weather.wind_u[i]
      const v = weather.wind_v[i]
      if (!finite(lat) || !finite(lon) || !finite(u) || !finite(v) || Math.hypot(u, v) < 0.2) return
      const magnitude = Math.hypot(u, v)
      const length = 1.25
      const dlat = (v / magnitude) * length
      const dlon = (u / magnitude) * length / Math.max(0.25, Math.cos(lat * Math.PI / 180))
      const a = globe.getCoords(lat - dlat / 2, lon - dlon / 2, 0.018)
      const b = globe.getCoords(lat + dlat / 2, lon + dlon / 2, 0.018)
      positions.push(a.x, a.y, a.z, b.x, b.y, b.z)
      const phase = ((i * 37) % 101) / 101
      const flow = 0.6 + Math.min(2, finite(weather.wind_ms[i]) ? weather.wind_ms[i]! / 12 : magnitude / 12)
      const direction = new Vector3(b.x - a.x, b.y - a.y, b.z - a.z).normalize()
      phases.push(phase, phase)
      flows.push(flow, flow)
      advections.push(direction.x, direction.y, direction.z, direction.x, direction.y, direction.z)
    }))
    this.windGeometry.setAttribute('position', new BufferAttribute(new Float32Array(positions), 3))
    this.windGeometry.setAttribute('phase', new BufferAttribute(new Float32Array(phases), 1))
    this.windGeometry.setAttribute('flow', new BufferAttribute(new Float32Array(flows), 1))
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
    marine.lats.forEach((lat, row) => marine.lons.forEach((lon, col) => {
      const height = marine.wave_m[row * marine.lons.length + col]
      if (!finite(lat) || !finite(lon) || !finite(height) || height < 0) return
      const p = globe.getCoords(lat, lon, 0.016)
      wavePositions.push(p.x, p.y, p.z)
      heights.push(height)
    }))
    this.waveGeometry.setAttribute('position', new BufferAttribute(new Float32Array(wavePositions), 3))
    this.waveGeometry.setAttribute('height', new BufferAttribute(new Float32Array(heights), 1))
    this.waveMaterial = new ShaderMaterial({
      uniforms: { pixelRatio: { value: Math.min(devicePixelRatio, 1.5) } },
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

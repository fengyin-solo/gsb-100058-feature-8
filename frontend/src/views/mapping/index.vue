<template>
  <section class="page" data-module="mapping">
    <header class="page-head">
      <div>
        <h2>成果图版本轨道</h2>
        <p class="page-desc">
          拖动图幅边界只改草稿，「提交确认」后才落一个新版本；刷新或重新进入工作区读到的都是同一份确认版本，
          填图详情、图幅名称目录、报告引用汇总共用这份投影。同图幅编号不同比例尺是独立轨道，互不覆盖。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="loadAll">刷新读取确认版本</button>
        <button class="btn" type="button" @click="replay">事件重放重建投影</button>
        <button class="btn primary" type="button" @click="showRegister = !showRegister">登记新图幅</button>
      </div>
    </header>

    <nav class="tabs">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        class="tab"
        :class="{ active: activeTab === tab.key }"
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
      </button>
    </nav>

    <p v-if="message" class="error-text" :class="{ ok: messageOk }">{{ message }}</p>

    <!-- ============ 成果图工作区 ============ -->
    <div v-if="activeTab === 'workspace'">
      <form v-if="showRegister" class="register-bar" @submit.prevent="registerUnit">
        <input v-model="registerForm['图幅编号']" placeholder="图幅编号（如 MAPP-0010）" />
        <input v-model="registerForm['图幅名称']" placeholder="图幅名称" />
        <input v-model="registerForm['比例尺']" placeholder="比例尺（如 1:50000）" />
        <button class="btn primary" type="submit">建立 R1 轨道</button>
      </form>

      <div class="workspace">
        <svg
          ref="canvasRef"
          class="map-canvas"
          :viewBox="`0 0 ${canvas.width} ${canvas.height}`"
          @pointermove="onPointerMove"
          @pointerup="endDrag"
          @pointerleave="endDrag"
        >
          <rect :width="canvas.width" :height="canvas.height" fill="#f1f5f9" />
          <g v-for="unit in units" :key="unit.unit_key">
            <rect
              :x="draftRect(unit).x"
              :y="draftRect(unit).y"
              :width="draftRect(unit).w"
              :height="draftRect(unit).h"
              :class="['map-unit', { selected: selectedKey === unit.unit_key, pending: !!unit.draft }]"
              @pointerdown="startDrag($event, unit, 'move')"
            />
            <text
              :x="draftRect(unit).x + 8"
              :y="draftRect(unit).y + 22"
              class="map-label"
              @pointerdown="startDrag($event, unit, 'move')"
            >
              {{ unit['图幅编号'] }}（{{ unit['比例尺'] }}）{{ unit['图幅名称'] }} · {{ unit.版本号 }}
            </text>
            <text
              :x="draftRect(unit).x + 8"
              :y="draftRect(unit).y + 42"
              class="map-sublabel"
              @pointerdown="startDrag($event, unit, 'move')"
            >
              结论：{{ unit.确认结论 }}<tspan v-if="unit.draft">｜有未确认草稿</tspan>
            </text>
            <template v-if="selectedKey === unit.unit_key">
              <rect
                :x="draftRect(unit).x + draftRect(unit).w - 8"
                :y="draftRect(unit).y + draftRect(unit).h - 8"
                width="16" height="16"
                class="map-handle"
                @pointerdown="startDrag($event, unit, 'se')"
              />
              <rect
                :x="draftRect(unit).x - 8"
                :y="draftRect(unit).y + draftRect(unit).h - 8"
                width="16" height="16"
                class="map-handle"
                @pointerdown="startDrag($event, unit, 'sw')"
              />
              <rect
                :x="draftRect(unit).x + draftRect(unit).w - 8"
                :y="draftRect(unit).y - 8"
                width="16" height="16"
                class="map-handle"
                @pointerdown="startDrag($event, unit, 'ne')"
              />
              <rect
                :x="draftRect(unit).x - 8"
                :y="draftRect(unit).y - 8"
                width="16" height="16"
                class="map-handle"
                @pointerdown="startDrag($event, unit, 'nw')"
              />
            </template>
          </g>
        </svg>

        <aside class="unit-panel">
          <template v-if="selectedUnit">
            <h3>{{ selectedUnit['图幅名称'] }}</h3>
            <dl class="kv">
              <dt>单元标识</dt><dd>{{ selectedUnit.unit_key }}</dd>
              <dt>图幅编号</dt><dd>{{ selectedUnit['图幅编号'] }}</dd>
              <dt>比例尺</dt><dd>{{ selectedUnit['比例尺'] }}</dd>
              <dt>确认版本</dt><dd>{{ selectedUnit.版本号 }}（{{ selectedUnit.status }}）</dd>
              <dt>确认结论</dt><dd>{{ selectedUnit.确认结论 }}</dd>
              <dt>更新时间</dt><dd>{{ selectedUnit.更新时间 }}</dd>
              <dt v-if="selectedUnit.draft">草稿基准</dt>
              <dd v-if="selectedUnit.draft">R{{ selectedUnit.draft.base_revision }}（{{ selectedUnit.draft.saved_at }}）</dd>
            </dl>
            <label class="block-label">
              本次确认结论（同步到填图详情、图幅名称目录、报告引用汇总）
              <textarea v-model="conclusion" rows="3" :placeholder="`上一版：${selectedUnit.确认结论}`"></textarea>
            </label>
            <div class="panel-actions">
              <button class="btn" type="button" :disabled="dragDirty" @click="saveDraft()">暂存草稿</button>
              <button class="btn primary" type="button" @click="submitRevision">提交确认新版本</button>
            </div>
            <div v-if="history.length" class="history">
              <h4>版本事件流</h4>
              <ul>
                <li v-for="ev in history" :key="ev.seq">
                  #{{ ev.seq }} {{ eventTypeLabel(ev.type) }} · R{{ ev.revision }} · {{ ev.actor }} · {{ ev.ts }}
                </li>
              </ul>
            </div>
          </template>
          <p v-else class="page-desc">点击画布中的图幅单元选择后拖动边界；同编号不同比例尺的图幅是独立版本轨道。</p>
        </aside>
      </div>
    </div>

    <!-- ============ 填图详情 ============ -->
    <table v-else-if="activeTab === 'details'" class="data-table">
      <thead>
        <tr>
          <th v-for="column in detailColumns" :key="column">{{ column }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in detailRows" :key="String(row.id)">
          <td v-for="column in detailColumns" :key="column">{{ row[column] ?? '—' }}</td>
        </tr>
      </tbody>
    </table>

    <!-- ============ 图幅名称目录 ============ -->
    <table v-else-if="activeTab === 'catalog'" class="data-table">
      <thead>
        <tr>
          <th v-for="column in catalogColumns" :key="column">{{ column }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in catalogRows" :key="row.unit_key">
          <td v-for="column in catalogColumns" :key="column">{{ row[column] ?? '—' }}</td>
        </tr>
      </tbody>
    </table>

    <!-- ============ 报告引用汇总清单 ============ -->
    <div v-else>
      <form class="register-bar" @submit.prevent="registerReference">
        <input v-model="referenceForm['报告编号']" placeholder="报告编号（如 GEOL-0001）" />
        <input v-model="referenceForm['图幅编号']" placeholder="图幅编号" />
        <input v-model="referenceForm['比例尺']" placeholder="比例尺" />
        <input v-model.number="referenceForm['钉住修订号']" placeholder="钉住修订号（留空=当前最新）" />
        <button class="btn primary" type="submit">登记引用</button>
      </form>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in referenceColumns" :key="column">{{ column }}</th>
            <th>版本跟进</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in referenceRows" :key="row.id">
            <td v-for="column in referenceColumns" :key="column">
              <template v-if="column === '引用是否最新'">
                <span :class="row[column] ? 'badge ok' : 'badge stale'">
                  {{ row[column] ? '最新版' : `滞后 R${row['最新修订号']}` }}
                </span>
              </template>
              <template v-else>{{ row[column] ?? '—' }}</template>
            </td>
            <td>
              <button
                v-if="!row.引用是否最新"
                class="link"
                type="button"
                @click="followLatest(row.id)"
              >
                跟进到 R{{ row.最新修订号 }}
              </button>
              <span v-else class="page-desc">—</span>
            </td>
          </tr>
          <tr v-if="!referenceRows.length">
            <td :colspan="referenceColumns.length + 1" class="empty-state">暂无报告引用</td>
          </tr>
        </tbody>
      </table>
    </div>

    <footer class="page-foot">
      <span>共 {{ units.length }} 条成果图版本轨道（同编号不同比例尺分列，不互相覆盖）</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Geometry = { x: number; y: number; w: number; h: number }
type WorkspaceUnit = {
  unit_key: string
  id: number
  图幅编号: string
  图幅名称: string
  比例尺: string
  status: string
  修订号: number
  版本号: string
  确认结论: string
  geometry: Geometry
  draft: { geometry: Geometry; base_revision: number; saved_at: string; note?: string } | null
  更新时间: string
}
type EventRow = {
  seq: number
  type: string
  revision: number
  actor: string
  ts: string
  note: string
}

const ENDPOINT = '/api/mapping'
const tabs = [
  { key: 'workspace', label: '成果图工作区' },
  { key: 'details', label: '填图详情' },
  { key: 'catalog', label: '图幅名称目录' },
  { key: 'references', label: '报告引用汇总' },
] as const

const canvas = { width: 1000, height: 1000 }
const activeTab = ref<(typeof tabs)[number]['key']>('workspace')
const units = ref<WorkspaceUnit[]>([])
// 表格投影数据字段来自后端确认版本，列动态渲染，这里按宽松字典处理。
const detailRows = ref<Record<string, any>[]>([])
const catalogRows = ref<Record<string, any>[]>([])
const referenceRows = ref<Record<string, any>[]>([])
const selectedKey = ref<string | null>(null)
const conclusion = ref('')
const history = ref<EventRow[]>([])
const message = ref('')
const messageOk = ref(false)
const showRegister = ref(false)
const canvasRef = ref<SVGSVGElement | null>(null)
const dragDirty = ref(false)

const registerForm = ref<Record<string, string>>({ 图幅编号: '', 图幅名称: '', 比例尺: '' })
const referenceForm = ref<Record<string, string | number>>({
  报告编号: '', 图幅编号: '', 比例尺: '', 钉住修订号: '',
})

const detailColumns = ['id', '图幅编号', '图幅名称', '比例尺', 'status', '版本号', '确认结论', '填图人员', '更新时间']
const catalogColumns = ['图幅编号', '图幅名称', '比例尺', '当前版本', '确认结论', 'status', '报告引用数', '更新时间']
const referenceColumns = ['报告编号', '图幅编号', '图幅名称', '比例尺', '引用版本', '引用是否最新', '确认结论', '地图标注', '登记时间']

// 本地编辑中的几何：key -> Geometry。有草稿先显示草稿，拖动时实时显示本地值。
const editing = ref<Record<string, Geometry>>({})

const selectedUnit = computed(() => units.value.find((unit) => unit.unit_key === selectedKey.value) ?? null)

function draftRect(unit: WorkspaceUnit): Geometry {
  return editing.value[unit.unit_key] ?? unit.draft?.geometry ?? unit.geometry
}

function flash(text: string, ok = false) {
  message.value = text
  messageOk.value = ok
}

async function postJson(path: string, values: Record<string, unknown>) {
  const response = await request(path, { method: 'POST', body: JSON.stringify({ values }) })
  const payload = await response.json()
  return { status: response.status, payload }
}

async function loadWorkspace(selectKey: string | null = null) {
  const response = await request(`${ENDPOINT}/workspace`)
  if (!response.ok) throw new Error('成果图工作区读取失败')
  const data = await response.json()
  units.value = data.units
  editing.value = {}
  dragDirty.value = false
  if (selectKey && units.value.some((unit) => unit.unit_key === selectKey)) {
    selectedKey.value = selectKey
  } else if (selectedKey.value && !units.value.some((unit) => unit.unit_key === selectedKey.value)) {
    selectedKey.value = null
  }
  if (selectedKey.value) await loadHistory(selectedKey.value)
}

async function loadDetails() {
  const response = await request(`${ENDPOINT}?size=200`)
  detailRows.value = response.ok ? (await response.json()).items : []
}

async function loadCatalog() {
  const response = await request(`${ENDPOINT}/catalog`)
  catalogRows.value = response.ok ? (await response.json()).items : []
}

async function loadReferences() {
  const response = await request(`${ENDPOINT}/references`)
  referenceRows.value = response.ok ? (await response.json()).items : []
}

async function loadHistory(key: string) {
  const { status, payload } = await postJson(`${ENDPOINT}/events`, { unit_key: key })
  history.value = status === 200 ? payload.events : []
}

async function loadAll() {
  try {
    await Promise.all([loadWorkspace(), loadDetails(), loadCatalog(), loadReferences()])
    flash('已读取成果图确认版本（刷新页面/重进工作区同一份数据）', true)
  } catch (error) {
    flash(error instanceof Error ? error.message : '加载失败')
  }
}

// ---------------- 画布拖动边界 ----------------
const drag = ref<{
  key: string
  mode: 'move' | 'nw' | 'ne' | 'sw' | 'se'
  pointerId: number
  startX: number
  startY: number
  orig: Geometry
} | null>(null)

function toCanvasPoint(event: PointerEvent): { x: number; y: number } {
  const svg = canvasRef.value
  if (!svg) return { x: 0, y: 0 }
  const pt = svg.createSVGPoint()
  pt.x = event.clientX
  pt.y = event.clientY
  const transformed = pt.matrixTransform(svg.getScreenCTM()?.inverse())
  return { x: transformed.x, y: transformed.y }
}

function clampRect(rect: Geometry): Geometry {
  const size = canvas.width
  const w = Math.max(20, Math.min(rect.w, size))
  const h = Math.max(20, Math.min(rect.h, size))
  const x = Math.min(Math.max(rect.x, 0), size - w)
  const y = Math.min(Math.max(rect.y, 0), size - h)
  return { x: Math.round(x), y: Math.round(y), w: Math.round(w), h: Math.round(h) }
}

function startDrag(event: PointerEvent, unit: WorkspaceUnit, mode: 'move' | 'nw' | 'ne' | 'sw' | 'se') {
  event.preventDefault()
  if (selectedKey.value !== unit.unit_key) {
    selectedKey.value = unit.unit_key
    conclusion.value = ''
    void loadHistory(unit.unit_key)
  }
  const point = toCanvasPoint(event)
  drag.value = {
    key: unit.unit_key,
    mode,
    pointerId: event.pointerId,
    startX: point.x,
    startY: point.y,
    orig: { ...draftRect(unit) },
  }
  ;(event.target as Element).setPointerCapture?.(event.pointerId)
}

function onPointerMove(event: PointerEvent) {
  const d = drag.value
  if (!d) return
  const point = toCanvasPoint(event)
  const dx = point.x - d.startX
  const dy = point.y - d.startY
  const r = { ...d.orig }
  if (d.mode === 'move') {
    r.x += dx
    r.y += dy
  } else {
    if (d.mode.includes('w')) { r.x += dx; r.w -= dx }
    if (d.mode.includes('e')) r.w += dx
    if (d.mode.includes('n')) { r.y += dy; r.h -= dy }
    if (d.mode.includes('s')) r.h += dy
  }
  editing.value[d.key] = clampRect(r)
  dragDirty.value = true
}

function endDrag(event: PointerEvent) {
  if (!drag.value) return
  try {
    ;(event.target as Element).releasePointerCapture?.(drag.value.pointerId)
  } catch {
    /* 指针已离开画布，捕获自动释放 */
  }
  const key = drag.value.key
  drag.value = null
  dragDirty.value = false
  // 拖动结束自动暂存草稿：刷新/重进工作区仍能接着改，确认版本不变
  void saveDraft(key)
}

// ---------------- 草稿 / 提交确认 ----------------
async function saveDraft(key?: string) {
  const targetKey = key ?? selectedKey.value
  const unit = units.value.find((item) => item.unit_key === targetKey)
  if (!targetKey || !unit) return
  const geometry = editing.value[targetKey] ?? unit.draft?.geometry ?? unit.geometry
  const { status, payload } = await postJson(`${ENDPOINT}/draft`, {
    unit_key: targetKey,
    geometry,
    base_revision: unit.修订号,
  })
  if (status !== 200) {
    flash(typeof payload.detail === 'string' ? payload.detail : '草稿暂存失败')
    return
  }
  flash(`草图已暂存（基于 R${unit.修订号}），确认版本未变`, true)
}

async function submitRevision() {
  const unit = selectedUnit.value
  if (!unit) return
  const geometry = editing.value[unit.unit_key] ?? unit.draft?.geometry ?? unit.geometry
  const { status, payload } = await postJson(`${ENDPOINT}/revisions`, {
    unit_key: unit.unit_key,
    expected_revision: unit.修订号,
    geometry,
    确认结论: conclusion.value,
  })
  if (status === 409) {
    // 乐观锁冲突：并发修订只落一个版本，必须以服务端确认版本为准刷新后再改
    const server = payload.detail.server_state
    flash(`版本冲突：${payload.detail.message}；已为您刷新到 R${server.修订号}，请重新调整边界`)
    editing.value = {}
    await loadWorkspace(unit.unit_key)
    return
  }
  if (status !== 200) {
    flash(typeof payload.detail === 'string' ? payload.detail : '提交失败，目录与标注已一起回滚')
    return
  }
  conclusion.value = ''
  flash(payload.message, true)
  await loadWorkspace(unit.unit_key)
  await Promise.all([loadDetails(), loadCatalog(), loadReferences()])
}

async function replay() {
  const { status, payload } = await postJson(`${ENDPOINT}/replay`, {})
  if (status === 200) {
    flash(`事件重放完成：${payload.entry.streams} 条轨道、${payload.entry.events} 个事件，各入口投影一致`, true)
    await loadAll()
  } else {
    flash('事件重放失败')
  }
}

async function registerUnit() {
  const { status, payload } = await postJson(`${ENDPOINT}`, registerForm.value)
  if (status === 200 && payload.ok) {
    showRegister.value = false
    registerForm.value = { 图幅编号: '', 图幅名称: '', 比例尺: '' }
    flash(payload.message, true)
    await loadAll()
  } else {
    flash(payload.message ?? '登记失败')
  }
}

async function registerReference() {
  const values: Record<string, unknown> = { ...referenceForm.value }
  if (values['钉住修订号'] === '' || values['钉住修订号'] == null) delete values['钉住修订号']
  const { status, payload } = await postJson(`${ENDPOINT}/references`, values)
  if (status === 200 && payload.ok) {
    referenceForm.value = { 报告编号: '', 图幅编号: '', 比例尺: '', 钉住修订号: '' }
    flash(payload.message, true)
    await loadReferences()
    await loadCatalog()
  } else {
    flash(typeof payload.detail === 'string' ? payload.detail : '引用登记失败')
  }
}

async function followLatest(id: number) {
  const { status, payload } = await postJson(`${ENDPOINT}/references/repin`, { id })
  if (status === 200 && payload.ok) {
    flash(payload.message, true)
    await Promise.all([loadReferences(), loadCatalog()])
  } else {
    flash(typeof payload.detail === 'string' ? payload.detail : '版本跟进失败')
  }
}

function eventTypeLabel(type: string) {
  return { initial_import: '导入/建轨', revision_confirmed: '确认新版本', status_transition: '状态流转' }[type] ?? type
}

onMounted(loadAll)
</script>

<style scoped>
.tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); margin-bottom: 12px; }
.tab { border: none; background: none; padding: 8px 14px; cursor: pointer; font-size: 13px; color: var(--muted); border-bottom: 2px solid transparent; }
.tab.active { color: var(--brand); border-bottom-color: var(--brand); font-weight: 600; }
.ok { color: #067647; }
.workspace { display: flex; gap: 12px; align-items: flex-start; }
.map-canvas { flex: 1; background: #fff; border: 1px solid var(--border); border-radius: 8px; min-height: 520px; touch-action: none; }
.map-unit { fill: rgba(31, 111, 235, 0.14); stroke: #1f6feb; stroke-width: 2; cursor: move; }
.map-unit.pending { stroke-dasharray: 8 4; stroke: #b54708; fill: rgba(245, 158, 11, 0.14); }
.map-unit.selected { stroke-width: 3; }
.map-label { font-size: 16px; fill: #0f172a; pointer-events: none; font-weight: 600; }
.map-sublabel { font-size: 12px; fill: #475569; pointer-events: none; }
.map-handle { fill: #fff; stroke: #1f6feb; stroke-width: 2; cursor: nwse-resize; }
.unit-panel { width: 320px; background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; }
.kv { display: grid; grid-template-columns: 90px 1fr; gap: 4px 8px; font-size: 13px; margin: 8px 0; }
.kv dt { color: var(--muted); }
.kv dd { margin: 0; }
.block-label { display: block; font-size: 12px; color: var(--muted); margin: 8px 0; }
.block-label textarea { width: 100%; margin-top: 4px; font: inherit; font-size: 13px; padding: 6px; border: 1px solid var(--border); border-radius: 6px; color: #0f172a; }
.panel-actions { display: flex; gap: 8px; }
.panel-actions .btn { flex: 1; }
.register-bar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; }
.register-bar input { padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
.history { margin-top: 12px; border-top: 1px solid var(--border); padding-top: 8px; }
.history h4 { margin: 0 0 6px; font-size: 13px; }
.history ul { margin: 0; padding-left: 16px; font-size: 12px; color: var(--muted); display: flex; flex-direction: column; gap: 2px; }
.badge { padding: 1px 8px; border-radius: 10px; font-size: 12px; }
.badge.ok { background: #e7f8ef; color: #067647; }
.badge.stale { background: #fef3c7; color: #b54708; }
</style>

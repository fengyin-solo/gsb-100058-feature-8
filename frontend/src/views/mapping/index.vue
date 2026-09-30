<template>
  <section class="page" data-module="mapping">
    <header class="page-head">
      <div>
        <h2>成果图版本轨道 · 地质填图</h2>
        <p class="page-desc">
          拖动单元边界后修订落入版本轨道，确认后刷新页面或重进工作区读到的是同一份成果图；
          审定结论同步到填图详情、图幅名称目录与报告引用，比例尺不同、图幅编号相同的单元各走各的轨道。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记填图单元</button>
        <button class="btn" type="button" @click="exportRows">导出地质填图清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>版本轨道</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="version-cell">
            <span :class="['version-badge', Number(row['确认版本号']) > 0 ? 'confirmed' : 'draft']">
              v{{ row['版本号'] ?? '—' }}
            </span>
            <span class="conclusion-text">{{ row['审定结论'] ?? '待确认' }}</span>
            <button class="link" type="button" @click="openWorkspace(row)">成果图工作区</button>
          </td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 2" class="empty-state">暂无地质填图数据，可先登记填图单元</td>
        </tr>
      </tbody>
    </table>

    <section class="catalog-panel">
      <header class="catalog-head">
        <div>
          <h3>图幅名称目录（事件重放投影，与地图标注、报告引用同一份结论）</h3>
          <p class="page-desc">目录只保存事件重放出的投影，不允许任何入口各存一份；刷新后与地图标注版本一致。</p>
        </div>
        <button class="btn ghost" type="button" @click="loadCatalog">重放目录</button>
      </header>
      <table class="data-table">
        <thead>
          <tr>
            <th>图幅编号</th><th>图幅名称</th><th>比例尺</th>
            <th>当前版本号</th><th>版本状态</th><th>审定结论</th><th>确认时间</th><th>确认人</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in catalog" :key="`${item.track_id}`">
            <td>{{ item['图幅编号'] }}</td>
            <td>{{ item['图幅名称'] }}</td>
            <td>{{ item['比例尺'] }}</td>
            <td>v{{ item['当前版本号'] }}</td>
            <td>
              <span :class="['version-badge', item['版本状态'] === '已确认' ? 'confirmed' : 'draft']">
                {{ item['版本状态'] }}
              </span>
            </td>
            <td>{{ item['审定结论'] }}</td>
            <td>{{ item['确认时间'] ?? '—' }}</td>
            <td>{{ item['确认人'] ?? '—' }}</td>
          </tr>
          <tr v-if="!catalog.length">
            <td colspan="8" class="empty-state">目录为空</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条地质填图记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <!-- 成果图工作区 -->
    <div v-if="workspace" class="modal-mask" @click.self="closeWorkspace">
      <div class="modal">
        <header class="modal-head">
          <div>
            <h3>成果图工作区 · {{ workspace['图幅名称'] }}（{{ workspace['图幅编号'] }} · {{ workspace['比例尺'] }}）</h3>
            <p class="page-desc">
              已确认版本：
              <strong>v{{ workspace.confirmed_revision || '—' }}</strong>
              <template v-if="workspace.confirmed_revision">（{{ workspace.confirmed_conclusion }}，{{ workspace.confirmed_by }}，{{ workspace.confirmed_at }}）</template>
              <template v-else>尚无确认成果图，v1 为旧版迁移基线</template>
              ；工作区状态：{{ workspace['工作区状态'] }}
            </p>
          </div>
          <button class="btn ghost" type="button" @click="closeWorkspace">关闭</button>
        </header>

        <div v-if="workspace.pending_revision" class="draft-banner">
          存在待确认草稿 r{{ workspace.pending_revision.revision }}
          （基于 v{{ workspace.pending_revision.base_revision }}，{{ workspace.pending_revision.editor || '未署名' }}
          于 {{ workspace.pending_revision.created_at }} 提交）：{{ workspace.pending_revision.note || '无说明' }}
        </div>

        <div class="workspace-grid">
          <div class="editor-wrap">
            <div class="editor-toolbar">
              <span class="hint">拖动顶点调整单元边界（仅改本地，点“保存边界修订”才落版本轨道）</span>
              <button class="btn" type="button" @click="resetGeometry">重置为当前版本</button>
            </div>
            <svg ref="svgRef" class="boundary-svg" viewBox="0 0 480 340" @pointermove="onPointerMove" @pointerup="onPointerUp">
              <polygon
                :points="polygonPoints(confirmedGeometry)"
                class="poly-confirmed"
              />
              <polygon
                :points="polygonPoints(editingGeometry)"
                class="poly-editing"
                :class="{ changed: geometryChanged }"
              />
              <circle
                v-for="(pt, idx) in editingGeometry"
                :key="idx"
                :cx="pt[0]"
                :cy="pt[1]"
                r="6"
                class="vertex-handle"
                @pointerdown.prevent="startDrag(idx, $event)"
              />
            </svg>
            <div class="editor-actions">
              <input v-model="revisionNote" class="note-input" placeholder="修订说明，如：与银沟幅接边调整" />
              <input v-model="editorName" class="note-input narrow" placeholder="修订人" />
              <button
                class="btn primary"
                type="button"
                :disabled="saving || !geometryChanged"
                @click="saveBoundary"
              >
                {{ saving ? '保存中…' : '保存边界修订（乐观锁）' }}
              </button>
            </div>
            <div class="confirm-row">
              <select v-model="confirmConclusion">
                <option value="合格">合格</option>
                <option value="需补测">需补测</option>
                <option value="退回修编">退回修编</option>
              </select>
              <button
                class="btn primary"
                type="button"
                :disabled="saving || !workspace.pending_revision"
                @click="confirmRevision"
              >
                确认草稿 r{{ workspace.pending_revision?.revision ?? '—' }} 为成果图版本
              </button>
              <span v-if="!workspace.pending_revision" class="hint">当前没有待确认草稿，先拖边界并保存修订</span>
            </div>
          </div>

          <aside class="history-pane">
            <h4>版本轨道（事件重放）</h4>
            <ul class="history-list">
              <li v-for="item in workspace.history" :key="`${item.revision}-${item.kind}-${item.created_at}`" class="history-item">
                <div class="history-line">
                  <span class="history-rev">r{{ item.revision }}</span>
                  <span>{{ item.kind }}</span>
                </div>
                <div class="history-meta">
                  <span :class="['history-state', historyStateClass(item.state)]">{{ item.state }}</span>
                  <span v-if="item.conclusion">结论：{{ item.conclusion }}</span>
                  <span v-if="item.note">{{ item.note }}</span>
                  <span class="hint">{{ item.created_at }}</span>
                </div>
                <button
                  v-if="item.geometry"
                  class="link"
                  type="button"
                  @click="replayRevision(item.revision)"
                >
                  重放 r{{ item.revision }} 成果图
                </button>
              </li>
            </ul>
          </aside>
        </div>
      </div>
    </div>

    <!-- 登记弹窗 -->
    <div v-if="creating" class="modal-mask" @click.self="creating = false">
      <div class="modal small">
        <header class="modal-head">
          <h3>登记填图单元（建立 v1 成果图轨道）</h3>
        </header>
        <div class="form-stack">
          <label v-for="field in createFields" :key="field">
            <span>{{ field }}</span>
            <input v-model="createForm[field]" :placeholder="field" />
          </label>
          <p class="hint">图幅编号 + 比例尺是轨道身份：编号相同、比例尺不同不会互相覆盖。</p>
        </div>
        <footer class="modal-foot">
          <button class="btn" type="button" @click="creating = false">取消</button>
          <button class="btn primary" type="button" :disabled="saving" @click="submitCreate">登记</button>
        </footer>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type Point = [number, number]
type HistoryItem = {
  revision: number
  kind: string
  state: string
  conclusion?: string | null
  note?: string | null
  created_at?: string | null
  geometry?: Point[] | null
}
type Workspace = {
  track_id: number
  图幅编号: string
  图幅名称: string
  比例尺: string
  工作区状态: string
  confirmed_revision: number
  confirmed_conclusion: string | null
  confirmed_at: string | null
  confirmed_by: string | null
  confirmed_geometry: Point[] | null
  pending_revision: {
    revision: number
    geometry: Point[]
    base_revision: number
    note: string
    editor: string
    created_at: string
  } | null
  history: HistoryItem[]
}

const ENDPOINT = '/api/mapping'
const columns = ['图幅编号', '图幅名称', '比例尺', '填图面积', '填图人员', '野外日期', '室内整理', '填图状态']
const actions = ['开始野外', '完成整理', '申请验收']
const stats = [{ label: '填图中图幅', value: 0 }, { label: '已验收图幅', value: 0 }, { label: '版本轨道', value: 0 }]
const createFields = ['图幅编号', '图幅名称', '比例尺']

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const catalog = ref<Record<string, unknown>[]>([])

const workspace = ref<Workspace | null>(null)
const editingGeometry = ref<Point[]>([])
const confirmedGeometry = ref<Point[]>([])
const geometryChanged = ref(false)
const revisionNote = ref('')
const editorName = ref('')
const confirmConclusion = ref('合格')
const saving = ref(false)
const svgRef = ref<SVGSVGElement | null>(null)
const dragIndex = ref<number | null>(null)

const creating = ref(false)
const createForm = ref<Record<string, string>>({})

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  createForm.value = { 图幅编号: '', 图幅名称: '', 比例尺: '' }
  creating.value = true
}

async function submitCreate() {
  errorMessage.value = ''
  saving.value = true
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: createForm.value }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '填图单元登记失败')
    }
    creating.value = false
    await Promise.all([reload(), loadCatalog()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '填图单元登记失败'
  } finally {
    saving.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '地质填图动作未生效，请稍后重试')
    }
    await Promise.all([reload(), loadCatalog()])
    if (workspace.value && workspace.value.track_id === row.id) {
      await openWorkspace(row)
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '地质填图操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('填图单元列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    stats[2].value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '地质填图列表读取失败'
  }
}

async function loadCatalog() {
  try {
    const response = await request(`${ENDPOINT}/catalog`)
    if (!response.ok) {
      throw new Error('图幅名称目录读取失败')
    }
    const payload = await response.json()
    catalog.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '图幅名称目录读取失败'
  }
}

async function openWorkspace(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/workspace`)
    if (!response.ok) {
      throw new Error('工作区读取失败')
    }
    workspace.value = (await response.json()) as Workspace
    revisionNote.value = ''
    resetGeometry()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '工作区读取失败'
  }
}

function closeWorkspace() {
  workspace.value = null
  dragIndex.value = null
}

function resetGeometry() {
  if (!workspace.value) {
    return
  }
  // 有待确认草稿时，编辑器载入草稿；否则载入最新确认版本
  const source = workspace.value.pending_revision?.geometry ?? workspace.value.confirmed_geometry
  confirmedGeometry.value = (workspace.value.confirmed_geometry ?? source ?? []) as Point[]
  editingGeometry.value = (source ?? []).map((pt) => [pt[0], pt[1]] as Point)
  geometryChanged.value = false
}

function polygonPoints(points: Point[]): string {
  return points.map((pt) => `${pt[0]},${pt[1]}`).join(' ')
}

function svgPoint(event: PointerEvent): Point | null {
  const svg = svgRef.value
  if (!svg) {
    return null
  }
  const ctm = svg.getScreenCTM()
  if (!ctm) {
    return null
  }
  const pt = svg.createSVGPoint()
  pt.x = event.clientX
  pt.y = event.clientY
  const local = pt.matrixTransform(ctm.inverse())
  return [Math.round(local.x * 10) / 10, Math.round(local.y * 10) / 10]
}

function startDrag(index: number, event: PointerEvent) {
  dragIndex.value = index
  ;(event.target as Element).setPointerCapture?.(event.pointerId)
}

function onPointerMove(event: PointerEvent) {
  if (dragIndex.value === null) {
    return
  }
  const point = svgPoint(event)
  if (!point) {
    return
  }
  const next = editingGeometry.value.slice()
  next[dragIndex.value] = [
    Math.min(480, Math.max(0, point[0])),
    Math.min(340, Math.max(0, point[1])),
  ]
  editingGeometry.value = next
  geometryChanged.value = true
}

function onPointerUp() {
  dragIndex.value = null
}

function handleConflict(message: string) {
  errorMessage.value = `${message} 已为你重放最新工作区，请核对后重做修订`
  if (workspace.value) {
    void openWorkspace({ id: workspace.value.track_id })
  }
}

async function saveBoundary() {
  if (!workspace.value) {
    return
  }
  errorMessage.value = ''
  saving.value = true
  const track = workspace.value
  // 乐观锁：提交本地看到的最新修订号
  const expected = track.pending_revision?.revision ?? track.confirmed_revision
  try {
    const response = await request(`${ENDPOINT}/${track.track_id}/boundary`, {
      method: 'POST',
      body: JSON.stringify({
        geometry: editingGeometry.value,
        expected_revision: expected,
        note: revisionNote.value,
        editor: editorName.value,
      }),
    })
    const payload = await response.json()
    if (response.status === 409) {
      handleConflict(payload.detail || '成果图已有更新的修订')
      return
    }
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '边界修订未生效')
    }
    await openWorkspace({ id: track.track_id })
    await Promise.all([reload(), loadCatalog()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '边界修订失败'
  } finally {
    saving.value = false
  }
}

async function confirmRevision() {
  if (!workspace.value?.pending_revision) {
    return
  }
  errorMessage.value = ''
  saving.value = true
  const track = workspace.value
  const pending = track.pending_revision
  if (!pending) {
    saving.value = false
    return
  }
  const pendingRevision = pending.revision
  try {
    const response = await request(`${ENDPOINT}/${track.track_id}/confirm`, {
      method: 'POST',
      body: JSON.stringify({
        revision: pendingRevision,
        expected_revision: track.confirmed_revision,
        conclusion: confirmConclusion.value,
        editor: editorName.value,
      }),
    })
    const payload = await response.json()
    if (response.status === 409) {
      handleConflict(payload.detail || '成果图已有更新的确认版本')
      return
    }
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '成果图确认未生效')
    }
    await openWorkspace({ id: track.track_id })
    await Promise.all([reload(), loadCatalog()])
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '成果图确认失败'
  } finally {
    saving.value = false
  }
}

async function replayRevision(revision: number) {
  if (!workspace.value) {
    return
  }
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${workspace.value.track_id}/replay/${revision}`)
    if (!response.ok) {
      throw new Error('历史版本重放失败')
    }
    const payload = await response.json()
    confirmedGeometry.value = payload.geometry
    editingGeometry.value = payload.geometry.map((pt: Point) => [pt[0], pt[1]] as Point)
    geometryChanged.value = false
    errorMessage.value = `已重放 r${revision}（${payload.confirmed ? payload.conclusion : '待确认草稿'}）；历史版本只读，保存修订仍基于当前最新版本`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '历史版本重放失败'
  }
}

function historyStateClass(state: string): string {
  if (state === '已确认') {
    return 'state-confirmed'
  }
  if (state.startsWith('历史')) {
    return 'state-history'
  }
  return 'state-draft'
}

onMounted(() => {
  void reload()
  void loadCatalog()
})
</script>

<style scoped>
.version-cell {
  display: flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}
.version-badge {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 12px;
  border: 1px solid var(--border);
}
.version-badge.confirmed,
.version-badge.state-confirmed {
  background: #e8f5e9;
  color: #1b5e20;
  border-color: #a5d6a7;
}
.version-badge.draft {
  background: #fff8e1;
  color: #8d6e00;
  border-color: #ffe082;
}
.conclusion-text {
  color: var(--muted);
  font-size: 12px;
}
.catalog-panel {
  margin-top: 18px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
}
.catalog-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.catalog-head h3 {
  margin: 0 0 2px;
  font-size: 15px;
}
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
  padding: 24px;
}
.modal {
  background: #fff;
  border-radius: 10px;
  width: min(1040px, 100%);
  max-height: 92vh;
  overflow: auto;
  padding: 16px 18px;
}
.modal.small {
  width: min(480px, 100%);
}
.modal-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.modal-head h3 {
  margin: 0 0 4px;
  font-size: 16px;
}
.draft-banner {
  margin: 10px 0;
  padding: 8px 12px;
  background: #fff8e1;
  border: 1px solid #ffe082;
  border-radius: 6px;
  font-size: 13px;
}
.workspace-grid {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 300px;
  gap: 14px;
  margin-top: 10px;
}
.editor-toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}
.hint {
  color: var(--muted);
  font-size: 12px;
}
.boundary-svg {
  width: 100%;
  height: 360px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #f8fafc;
  touch-action: none;
}
.poly-confirmed {
  fill: rgba(31, 111, 235, 0.08);
  stroke: #1f6feb;
  stroke-width: 2;
  stroke-dasharray: 6 4;
}
.poly-editing {
  fill: rgba(255, 152, 0, 0.12);
  stroke: #f57c00;
  stroke-width: 2;
}
.poly-editing.changed {
  fill: rgba(244, 67, 54, 0.12);
  stroke: #d32f2f;
}
.vertex-handle {
  fill: #fff;
  stroke: #d32f2f;
  stroke-width: 2;
  cursor: grab;
}
.vertex-handle:active {
  cursor: grabbing;
}
.editor-actions {
  display: flex;
  gap: 8px;
  margin-top: 8px;
  flex-wrap: wrap;
}
.note-input {
  flex: 1;
  min-width: 160px;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.note-input.narrow {
  flex: 0 0 120px;
}
.confirm-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 10px;
  flex-wrap: wrap;
}
.confirm-row select {
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.history-pane {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px;
  max-height: 520px;
  overflow: auto;
}
.history-pane h4 {
  margin: 0 0 8px;
  font-size: 14px;
}
.history-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.history-item {
  border-left: 3px solid var(--border);
  padding: 4px 8px;
  font-size: 12px;
}
.history-line {
  display: flex;
  gap: 8px;
  font-weight: 600;
}
.history-rev {
  color: var(--brand);
}
.history-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  color: var(--muted);
  margin: 2px 0;
}
.history-state {
  align-self: flex-start;
  padding: 0 6px;
  border-radius: 8px;
}
.state-confirmed {
  background: #e8f5e9;
  color: #1b5e20;
}
.state-draft {
  background: #fff8e1;
  color: #8d6e00;
}
.state-history {
  background: #eceff1;
  color: #546e7a;
}
.form-stack {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin: 12px 0;
}
.form-stack label span {
  display: block;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 2px;
}
.form-stack input {
  width: 100%;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.modal-foot {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
</style>

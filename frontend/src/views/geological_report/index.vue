<template>
  <section class="page" data-module="geological_report">
    <header class="page-head">
      <div>
        <h2>地质报告管理</h2>
        <p class="page-desc">维护勘探报告，并按成果图版本号登记引用；引用结论由填图版本轨道重放，与成果图始终对得上。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记勘探报告</button>
        <button class="btn" type="button" @click="exportRows">导出地质报告清单</button>
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
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
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
          <td :colspan="columns.length + 1" class="empty-state">暂无地质报告数据，可先登记勘探报告</td>
        </tr>
      </tbody>
    </table>

    <section class="cite-panel">
      <header class="cite-head">
        <div>
          <h3>成果图引用汇总清单（引用结论由版本轨道重放，不另存副本）</h3>
          <p class="page-desc">
            引用只钉「图幅编号 + 比例尺 + 版本号」指针；成果图确认新版后，未更新的引用会被标出差异，
            避免报告与成果图各说一套。对得上 {{ summary.aligned ?? 0 }} 条 / 待更新 {{ summary.stale ?? 0 }} 条。
          </p>
        </div>
        <button class="btn ghost" type="button" @click="loadCitations">重放清单</button>
      </header>

      <form class="cite-form" @submit.prevent="registerCitation">
        <input v-model="citeForm.report_no" placeholder="报告编号，如 GEOL-0003" required />
        <input v-model="citeForm.sheet_no" placeholder="图幅编号，如 I49D001001" required />
        <input v-model="citeForm.scale" placeholder="比例尺，如 1:50000" required />
        <input v-model="citeForm.pinned_revision" placeholder="引用版本号（留空钉最新确认版）" inputmode="numeric" />
        <input v-model="citeForm.remark" placeholder="备注" />
        <button class="btn primary" type="submit" :disabled="saving">登记引用</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th>报告编号</th><th>图幅编号</th><th>图幅名称</th><th>比例尺</th>
            <th>引用版本</th><th>引用结论</th><th>最新确认版本</th><th>最新确认结论</th><th>核对</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in citations" :key="String(item.id)">
            <td>{{ item['报告编号'] }}</td>
            <td>{{ item['图幅编号'] }}</td>
            <td>{{ item['图幅名称'] }}</td>
            <td>{{ item['比例尺'] }}</td>
            <td>v{{ item['引用版本号'] }}</td>
            <td>{{ item['引用结论'] }}</td>
            <td>v{{ item['最新确认版本号'] }}</td>
            <td>{{ item['最新确认结论'] }}</td>
            <td>
              <span :class="['cite-flag', item['引用是否对得上'] ? 'aligned' : 'stale']">
                {{ item['引用是否对得上'] ? '对得上' : '版本落后' }}
              </span>
            </td>
          </tr>
          <tr v-if="!citations.length">
            <td colspan="9" class="empty-state">暂无报告引用，登记后将从事宜版本轨道重放结论</td>
          </tr>
        </tbody>
      </table>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条地质报告记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>
type Citation = Record<string, string | number | boolean | null>

const ENDPOINT = '/api/geological_report'
const columns = ['报告编号', '勘探区', '报告类型', '编制人', '审核人', '提交日期', '审定结论', '报告状态']
const actions = ['提交内审', '提交外审', '确认定稿']
const stats = [{ label: '编制中报告', value: 0 }, { label: '待审报告', value: 0 }, { label: '已定稿报告', value: 0 }]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

const citations = ref<Citation[]>([])
const summary = ref<{ aligned: number; stale: number }>({ aligned: 0, stale: 0 })
const saving = ref(false)
const citeForm = ref<Record<string, string>>({
  report_no: '',
  sheet_no: '',
  scale: '',
  pinned_revision: '',
  remark: '',
})

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '勘探报告登记入口尚未接入审批流'
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
      throw new Error(payload.message || '地质报告动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '地质报告操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('勘探报告列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '地质报告列表读取失败'
  }
}

async function loadCitations() {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/citations/summary`)
    if (!response.ok) {
      throw new Error('报告引用汇总读取失败')
    }
    const payload = await response.json()
    citations.value = payload.items ?? []
    summary.value = { aligned: payload.aligned ?? 0, stale: payload.stale ?? 0 }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '报告引用汇总读取失败'
  }
}

async function registerCitation() {
  errorMessage.value = ''
  saving.value = true
  const form = citeForm.value
  const body: Record<string, string | number> = {
    report_no: form.report_no,
    sheet_no: form.sheet_no,
    scale: form.scale,
    remark: form.remark,
  }
  if (form.pinned_revision.trim()) {
    body.pinned_revision = Number(form.pinned_revision)
  }
  try {
    const response = await request(`${ENDPOINT}/citations`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      throw new Error(payload.message || '报告引用登记失败')
    }
    citeForm.value = { report_no: form.report_no, sheet_no: '', scale: '', pinned_revision: '', remark: '' }
    await loadCitations()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '报告引用登记失败'
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  void reload()
  void loadCitations()
})
</script>

<style scoped>
.cite-panel {
  margin-top: 18px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
}
.cite-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.cite-head h3 {
  margin: 0 0 2px;
  font-size: 15px;
}
.cite-form {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin: 10px 0;
}
.cite-form input {
  flex: 1 1 140px;
  min-width: 120px;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.cite-flag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 12px;
}
.cite-flag.aligned {
  background: #e8f5e9;
  color: #1b5e20;
  border: 1px solid #a5d6a7;
}
.cite-flag.stale {
  background: #fdecea;
  color: #b42318;
  border: 1px solid #f5b5ad;
}
</style>

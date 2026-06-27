const DAYS = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
const CMD_LABEL = { 1: 'Disable device', 2: 'Boost' }
const CMD_COLOR = { 1: 'bg-blue-900/40 text-blue-300', 2: 'bg-red-900/40 text-red-300' }

const CIRCUIT_LABEL = { command: 'PD', offpeak: 'Off-Peak', heater2: 'Heater 2' }
const CIRCUIT_POINT = { command: 'active_rule_id', offpeak: 'active_rule_offpeak_id', heater2: 'active_rule_heater2_id' }

function rulesTab() {
  return {
    rules: [],
    loading: false,
    showJSON: false,
    filter: 'all',           // 'all' | 'active' | 'inactive'
    allExpanded: false,
    // Create modal
    createModal: false,
    newRuleName: '',
    newRuleType: 'command',   // command=PD, offpeak, heater2
    creating: false,
    createMsg: '',
    activating: null,         // rule id being set as active
    // Edit modal
    editModal: false,
    saving: false,
    deleting: false,
    saveResult: '',
    saveMsg: '',
    saveWarnBypass: false,   // true = user acknowledged gap warning, proceed on next click
    draft: null,
    // Delete confirm modal
    deleteConfirm: null,
    // Span modal
    spanModal: false,
    spanDayIdx: null,

    async init() { await this.refresh() },

    // ── expand / collapse (per-card x-data handles local state; parent dispatches global events) ──

    expandAll()   { this.allExpanded = true;  window.dispatchEvent(new CustomEvent('rules-expand-all')) },
    collapseAll() { this.allExpanded = false; window.dispatchEvent(new CustomEvent('rules-collapse-all')) },

    // ── filter / sort ─────────────────────────────────────────────────────────

    activeRuleId() { return Alpine.store('app').points?.active_rule_id || null },

    // Return the active rule ID for a given circuit type
    activeRuleIdForType(type) {
      const pt = CIRCUIT_POINT[type] || 'active_rule_id'
      return Alpine.store('app').points?.[pt] || null
    },

    isActive(rule) { return rule.id === this.activeRuleIdForType(rule.type) },

    circuitLabel(type) { return CIRCUIT_LABEL[type] || type },

    filteredRules() {
      // Sort: active rules (any circuit) first
      const activeIds = new Set(Object.values(CIRCUIT_POINT).map(pt => Alpine.store('app').points?.[pt]).filter(Boolean))
      let list = [...this.rules]
      if (this.filter === 'active')   list = list.filter(r => activeIds.has(r.id))
      if (this.filter === 'inactive') list = list.filter(r => !activeIds.has(r.id))
      return list.sort((a, b) => (activeIds.has(b.id) ? 1 : 0) - (activeIds.has(a.id) ? 1 : 0))
    },

    // ── rule helpers ──────────────────────────────────────────────────────────

    ruleHasDay(rule, di) {
      return !!(rule.data && `d${di+1}` in rule.data)
    },

    // True if all 7 days present AND all have identical slots (semantic comparison)
    isEverydayIdentical(rule) {
      const d = rule.data || {}
      if (![1,2,3,4,5,6,7].every(i => d[`d${i}`]?.length)) return false
      const normSlot = s => `${s.timeFrom}|${s.timeTo}|${s.command}|${s.boost_power??''}|${s.temperature_min??''}`
      const normDay = slots => (slots || []).map(normSlot).sort().join(';')
      const ref = normDay(d.d1)
      return [2,3,4,5,6,7].every(i => normDay(d[`d${i}`]) === ref)
    },

    // Short one-line summary of slots for the collapsed card
    slotSummary(rule) {
      const d = rule.data || {}
      const allSlots = Object.values(d).filter(v => Array.isArray(v)).flat()
      if (!allSlots.length) return 'No schedule'
      const first = allSlots[0]
      const cmd = first.command == 2 ? 'Boost' : 'Disable'
      const time = `${first.timeFrom || '--'}–${first.timeTo || '--'}`
      const more = allSlots.length > 1 ? ` +${allSlots.length - 1}` : ''
      return `${cmd} ${time}${more}`
    },

    boostPowerLabel(bp) {
      if (!bp) return ''
      const map = { 1: '25%', 2: '50%', 3: '75%', 4: '100%' }
      return map[bp] || `${bp}`
    },

    // ── create ────────────────────────────────────────────────────────────────

    openCreate() {
      this.newRuleName = ''
      this.newRuleType = 'command'
      this.createMsg = ''
      this.createModal = true
    },

    async disableRule(rule) {
      if (this.activating || !rule?.type) return
      this.activating = '__disable__' + rule.type
      try {
        const r = await fetch('/api/device/set', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ rule_id: '0', rule_type: rule.type }),
        })
        if (r.ok) {
          const pt = CIRCUIT_POINT[rule.type] || 'active_rule_id'
          Alpine.store('app').points[pt] = ''
          Alpine.store('app').addToast(`${this.circuitLabel(rule.type)} rule cleared`, 'warn')
        } else {
          const d = await r.json()
          Alpine.store('app').addToast(d.detail || 'Disable failed', 'error')
        }
      } catch (e) {
        Alpine.store('app').addToast('Network error', 'error')
      } finally {
        this.activating = null
      }
    },

    async setActiveRule(rule) {
      if (this.activating || !rule?.id) return
      this.activating = rule.id
      try {
        const r = await fetch('/api/device/set', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ rule_id: rule.id, rule_type: rule.type || 'command' }),
        })
        if (r.ok) {
          const pt = CIRCUIT_POINT[rule.type] || 'active_rule_id'
          Alpine.store('app').points[pt] = rule.id
          Alpine.store('app').addToast(`${this.circuitLabel(rule.type)} rule → "${rule.name}"`, 'info')
        } else {
          const d = await r.json()
          Alpine.store('app').addToast(d.detail || 'Activate failed', 'error')
        }
      } catch (e) {
        Alpine.store('app').addToast('Network error', 'error')
      } finally {
        this.activating = null
      }
    },

    async createRule() {
      if (!this.newRuleName.trim() || this.creating) return
      this.creating = true
      this.createMsg = ''
      try {
        const r = await fetch('/api/rules', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: this.newRuleName.trim(), type: this.newRuleType }),
        })
        const d = await r.json()
        if (r.ok) {
          // API may return the new rule directly or wrapped
          const newRule = d.data || d
          this.createModal = false
          await this.refresh()
          // Open edit modal on the newly created rule so user can add schedule
          const created = this.rules.find(x => x.id === newRule.id) || newRule
          if (created?.id) this.openEdit(created)
          Alpine.store('app').addToast(`Rule "${this.newRuleName.trim()}" created`, 'info')
        } else {
          this.createMsg = d.detail || d.message || 'Create failed'
        }
      } catch (e) {
        this.createMsg = 'Network error'
      } finally {
        this.creating = false
      }
    },

    // ── delete ────────────────────────────────────────────────────────────────

    confirmDelete(rule) {
      this.deleteConfirm = rule
    },

    cancelDelete() {
      this.deleteConfirm = null
    },

    async deleteRule() {
      const rule = this.deleteConfirm
      if (!rule) return
      this.deleting = true
      try {
        const r = await fetch(`/api/rules/${rule.id}`, { method: 'DELETE' })
        if (r.ok) {
          this.rules = this.rules.filter(x => x.id !== rule.id)
          this.deleteConfirm = null
          this.editModal = false
          Alpine.store('app').addToast(`Rule "${rule.name}" deleted`, 'warn')
        } else {
          const d = await r.json()
          Alpine.store('app').addToast(d.detail || 'Delete failed', 'error')
        }
      } catch (e) {
        Alpine.store('app').addToast('Network error', 'error')
      } finally {
        this.deleting = false
      }
    },

    // ── clone ─────────────────────────────────────────────────────────────────

    async cloneRule(rule) {
      const name = (rule.name || 'Rule') + ' (copy)'
      try {
        const r = await fetch('/api/rules', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, type: rule.type }),
        })
        const d = await r.json()
        if (!r.ok) { Alpine.store('app').addToast(d.detail || 'Clone failed', 'error'); return }

        // Resolve real ID: try response fields first, then reload from server
        const raw = d.data || d
        let newId = raw.id || raw.rule_id || raw._id
        if (!newId) {
          // Reload rules from server and find the newly created one by name
          await this.refresh()
          const found = this.rules.find(x => x.name === name && x.id !== rule.id)
          newId = found?.id
        }
        if (!newId) {
          Alpine.store('app').addToast(`Clone created — open to add schedule`, 'warn')
          await this.refresh()
          return
        }

        // Copy schedule from source rule
        const cloned = JSON.parse(JSON.stringify(rule))
        cloned.id = newId
        cloned.name = name
        const r2 = await fetch(`/api/rules/${newId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ rule: cloned }),
        })
        if (!r2.ok) {
          const e2 = await r2.json().catch(() => ({}))
          Alpine.store('app').addToast(e2.detail || 'Clone schedule copy failed', 'error')
        } else {
          Alpine.store('app').addToast(`Cloned as "${name}"`, 'success')
        }
        await this.refresh()
      } catch (e) {
        Alpine.store('app').addToast('Network error', 'error')
      }
    },

    // ── span / fill gaps ──────────────────────────────────────────────────────

    openSpan(dayIdx) {
      this.spanDayIdx = dayIdx
      this.spanModal = true
    },

    _timeToMin(t) {
      const [h, m] = (t || '00:00').split(':').map(Number)
      return h * 60 + m
    },

    _minToTime(m) {
      return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
    },

    _getCovered(dayIdx) {
      const key = `d${dayIdx + 1}`
      const slots = (this.draft?.data?.[key] || [])
        .filter(s => s.timeFrom && s.timeTo)
        .map(s => ({ from: this._timeToMin(s.timeFrom), to: this._timeToMin(s.timeTo) }))
        .sort((a, b) => a.from - b.from)
      // Merge overlapping
      const merged = []
      for (const s of slots) {
        if (merged.length && s.from <= merged[merged.length - 1].to)
          merged[merged.length - 1].to = Math.max(merged[merged.length - 1].to, s.to)
        else merged.push({ ...s })
      }
      return merged
    },

    applySpan(mode) {
      const di = this.spanDayIdx
      const key = `d${di + 1}`
      if (!this.draft.data[key]) this.draft.data[key] = []
      const covered = this._getCovered(di)
      const cmd = mode === 'boost_remainder' ? 2 : 1
      let gaps = []

      if (mode === 'between') {
        // Gaps between consecutive covered slots (internal only)
        for (let i = 0; i < covered.length - 1; i++) {
          if (covered[i].to < covered[i + 1].from)
            gaps.push({ from: covered[i].to, to: covered[i + 1].from })
        }
        // Fill internal gaps with Disable
        for (const g of gaps)
          this.draft.data[key].push({ timeFrom: this._minToTime(g.from), timeTo: this._minToTime(g.to), command: 1, boost_power: null, temperature_min: null })
      } else {
        // All uncovered time [0, 1439]
        let prev = 0
        for (const c of covered) {
          if (prev < c.from) gaps.push({ from: prev, to: c.from })
          prev = c.to
        }
        if (prev < 1440) gaps.push({ from: prev, to: 1440 })
        for (const g of gaps)
          this.draft.data[key].push({
            timeFrom: this._minToTime(g.from),
            timeTo: g.to >= 1440 ? '23:59' : this._minToTime(g.to),
            command: cmd,
            boost_power: cmd === 2 ? null : undefined,
            temperature_min: cmd === 2 ? 60 : undefined,
          })
      }

      // Sort slots by timeFrom
      this.draft.data[key].sort((a, b) => a.timeFrom.localeCompare(b.timeFrom))
      this.spanModal = false
    },

    async refresh() {
      this.loading = true
      try {
        const r = await fetch('/api/rules')
        if (!r.ok) return
        const d = await r.json()
        this.rules = Array.isArray(d) ? d : (d.data || [])
        // Signal cards to expand the active rule after load
        const aid = this.activeRuleId()
        if (aid) window.dispatchEvent(new CustomEvent('rules-expand-active', { detail: aid }))
      } catch (_) {
        Alpine.store('app').addToast('Failed to load rules', 'error')
      } finally {
        this.loading = false
      }
    },

    // ── helpers ────────────────────────────────────────────────────────────────

    activeDays(rule) {
      return Object.keys(rule.data || {})
        .filter(k => /^d\d$/.test(k))
        .map(k => parseInt(k.slice(1)) - 1)  // 0-indexed
    },

    slotsFor(rule, dayIdx) {
      const key = `d${dayIdx + 1}`
      return (rule.data || {})[key] || []
    },

    allDaySlots(rule) {
      // Returns [{day, dayLabel, slots}] for days that have slots
      const entries = []
      for (let i = 0; i < 7; i++) {
        const slots = this.slotsFor(rule, i)
        if (slots.length) entries.push({ day: i, dayLabel: DAYS[i], slots })
      }
      return entries
    },

    isEveryday(rule) {
      const active = this.activeDays(rule)
      return active.length === 7
    },

    // timeline bar: percent width and position for a time slot
    slotStyle(slot, color) {
      const pct = t => {
        const [h, m] = (t || '00:00').split(':').map(Number)
        return ((h * 60 + m) / 1440) * 100
      }
      const left = pct(slot.timeFrom)
      const width = Math.max(0.5, pct(slot.timeTo) - left)
      return `left:${left}%;width:${width}%;background:${color}`
    },

    // ── day toggles ───────────────────────────────────────────────────────────

    isDayActive(di) {
      return !!(this.draft?.data && `d${di+1}` in this.draft.data)
    },

    isEverydayActive() {
      if (!this.draft?.data) return false
      return [1,2,3,4,5,6,7].every(i => `d${i}` in this.draft.data)
    },

    _defaultSlot() {
      return { timeFrom: '08:00', timeTo: '10:00', command: 2, boost_power: null,
               temperature_min: 60, price_max: null, price_min: null }
    },

    toggleDay(di) {
      if (!this.draft) return
      const key = `d${di+1}`
      if (this.isDayActive(di)) {
        delete this.draft.data[key]
      } else {
        // Copy first existing day's slots as template, or create default
        const existing = Object.values(this.draft.data)[0]
        this.draft.data[key] = existing
          ? JSON.parse(JSON.stringify(existing))
          : [this._defaultSlot()]
      }
    },

    toggleEveryday() {
      if (!this.draft) return
      if (this.isEverydayActive()) {
        for (let i = 1; i <= 7; i++) delete this.draft.data[`d${i}`]
      } else {
        // Spread first existing day's slots to all missing days
        const existing = Object.values(this.draft.data)[0]
        const template = existing
          ? JSON.parse(JSON.stringify(existing))
          : [this._defaultSlot()]
        for (let i = 1; i <= 7; i++) {
          if (!(`d${i}` in this.draft.data))
            this.draft.data[`d${i}`] = JSON.parse(JSON.stringify(template))
        }
      }
    },

    // ── edit modal ────────────────────────────────────────────────────────────

    openEdit(rule) {
      this.draft = JSON.parse(JSON.stringify(rule))  // deep clone
      this.saveResult = ''
      this.saveMsg = ''
      this.saveWarnBypass = false
      this.editModal = true
    },

    closeEdit() {
      this.editModal = false
      this.draft = null
    },

    addSlot(dayIdx) {
      const key = `d${dayIdx + 1}`
      if (!this.draft.data[key]) this.draft.data[key] = []
      this.draft.data[key].push({
        timeFrom: '08:00', timeTo: '10:00',
        command: 2, boost_power: null,
        temperature_min: 60, price_max: null, price_min: null,
      })
    },

    removeSlot(dayIdx, slotIdx) {
      const key = `d${dayIdx + 1}`
      this.draft.data[key].splice(slotIdx, 1)
      if (!this.draft.data[key].length) delete this.draft.data[key]
    },

    snapTimes(dayIdx, slotIdx, field) {
      const key = `d${dayIdx + 1}`
      const slots = this.draft?.data?.[key]
      if (!slots) return
      if (field === 'timeTo' && slotIdx < slots.length - 1) {
        slots[slotIdx + 1].timeFrom = slots[slotIdx].timeTo
      } else if (field === 'timeFrom' && slotIdx > 0) {
        slots[slotIdx - 1].timeTo = slots[slotIdx].timeFrom
      }
    },

    _checkGaps() {
      // Returns list of human-readable gap descriptions, empty if no gaps
      const dayLabels = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
      const gaps = []
      // In everyday mode all days are identical; only check d1
      const keys = this.isEverydayActive()
        ? ['d1']
        : Object.keys(this.draft.data || {}).filter(k => /^d\d$/.test(k))
      for (const key of keys) {
        const di = parseInt(key.slice(1)) - 1
        const slots = (this.draft.data[key] || []).filter(s => s.timeFrom && s.timeTo)
        if (!slots.length) continue
        const sorted = [...slots].sort((a, b) => a.timeFrom.localeCompare(b.timeFrom))
        let prev = '00:00'
        for (const s of sorted) {
          if (s.timeFrom > prev) gaps.push(`${dayLabels[di]} ${prev}–${s.timeFrom}`)
          prev = s.timeTo > prev ? s.timeTo : prev
        }
        const end = sorted[sorted.length - 1].timeTo
        if (end < '23:59') gaps.push(`${dayLabels[di]} ${end}–23:59`)
      }
      return gaps
    },

    async saveRule() {
      if (!this.draft || this.saving) return
      // Sort slots by timeFrom for all active days
      for (const key of Object.keys(this.draft.data || {})) {
        if (/^d\d$/.test(key) && Array.isArray(this.draft.data[key]))
          this.draft.data[key].sort((a, b) => (a.timeFrom || '').localeCompare(b.timeFrom || ''))
      }
      // In "every day" mode, sync d1 slots to all other days before saving
      if (this.isEverydayActive() && this.draft.data['d1']) {
        const template = JSON.parse(JSON.stringify(this.draft.data['d1']))
        for (let i = 2; i <= 7; i++) this.draft.data[`d${i}`] = JSON.parse(JSON.stringify(template))
      }
      // Gap check — warn once, bypass on second click
      if (!this.saveWarnBypass) {
        const gaps = this._checkGaps()
        if (gaps.length) {
          this.saveWarnBypass = true
          this.saveResult = 'warn'
          this.saveMsg = `Unallocated time gaps: ${gaps.slice(0, 3).join(', ')}${gaps.length > 3 ? ` +${gaps.length - 3} more` : ''}`
          return
        }
      }
      this.saveWarnBypass = false
      this.saving = true
      this.saveResult = ''
      try {
        const r = await fetch(`/api/rules/${this.draft.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ rule: this.draft }),
        })
        const d = await r.json()
        if (r.ok) {
          this.saveResult = 'ok'
          this.saveMsg = `Rule "${this.draft.name}" saved successfully.`
          Alpine.store('app').addToast('Rule saved', 'success')
          setTimeout(async () => {
            this.closeEdit()
            await this.refresh()   // reload from server so UI reflects what cloud actually saved
          }, 1200)
        } else {
          this.saveResult = 'error'
          this.saveMsg = d.detail || d.error || 'Save failed — check logs for details'
        }
      } catch (e) {
        this.saveResult = 'error'
        this.saveMsg = String(e)
      } finally {
        this.saving = false
      }
    },
  }
}

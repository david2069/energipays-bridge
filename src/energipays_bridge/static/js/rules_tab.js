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
    debugMode: false,
    debugBefore: null,
    debugDraft: null,
    debugServerReturn: null,
    debugSnapshotLabel: '',
    debugTab: 'sent',   // 'before' | 'sent' | 'returned'
    filter: 'all',           // 'all' | 'active' | 'inactive'
    allExpanded: false,
    // Create modal
    createModal: false,
    newRuleName: '',
    newRuleType: 'command',   // command=PD, offpeak, heater2
    creating: false,
    createMsg: '',
    createConfirmActive: false,  // true = user saw active-rule warning and confirmed
    createStep: 'form',          // 'warn' | 'form'
    activating: null,         // rule id being set as active
    // Edit modal
    editModal: false,
    saving: false,
    deleting: false,
    saveResult: '',
    saveMsg: '',
    saveWarnBypass: false,   // true = user acknowledged gap warning, proceed on next click
    draft: null,
    // Gap picker modal (shown when Add Slot finds multiple gaps)
    gapPicker: { open: false, dayIdx: 0, gaps: [] },
    // Running slot ticker (updates every 30s for countdown)
    _rulesTick: 0,
    // Rename modal
    renameModal: false,
    renameTarget: null,
    renameDraft: '',
    renaming: false,
    renameMsg: '',
    // Delete confirm modal
    deleteConfirm: null,
    // Span modal
    spanModal: false,
    spanDayIdx: null,

    async init() {
      await this.refresh()
      setInterval(() => { this._rulesTick++ }, 30000)
    },

    fmtAge(iso) {
      if (!iso) return ''
      const sec = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
      if (sec < 60) return `${sec}s ago`
      if (sec < 3600) return `${Math.floor(sec/60)}m ago`
      if (sec < 86400) return `${Math.floor(sec/3600)}h ago`
      return `${Math.floor(sec/86400)}d ago`
    },

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

    ruleIsEveryday(rule) {
      // energipays.com shows "Everyday" when active_day is set (any non-null value)
      if (rule.active_day) return true
      // also true if all 7 data keys present (our expanded format)
      return [1,2,3,4,5,6,7].every(i => `d${i}` in (rule.data || {}))
    },

    ruleHasDay(rule, di) {
      if (this.ruleIsEveryday(rule)) return true   // all pills lit for everyday rules
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
      this.createConfirmActive = false
      // If a rule is already active, show the warning screen first
      this.createStep = this.activeRuleForNewType() ? 'warn' : 'form'
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

    activeRuleForNewType() {
      // Returns the active rule for the currently selected new rule type, or null
      const activeId = this.activeRuleIdForType(this.newRuleType)
      return activeId ? (this.rules.find(r => r.id === activeId) || null) : null
    },

    async createRule() {
      if (!this.newRuleName.trim() || this.creating) return
      this.createConfirmActive = false
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
      // "join" mode: make slot times continuous in-place (no gap fills)
      if (mode === 'join') {
        const jKey = di === 0 && this.isEverydayActive() ? 'd1' : `d${di + 1}`
        const jSlots = this.draft?.data?.[jKey]
        if (jSlots && jSlots.length >= 2) {
          jSlots.sort((a, b) => (a.timeFrom || '').localeCompare(b.timeFrom || ''))
          for (let i = 1; i < jSlots.length; i++) jSlots[i].timeFrom = jSlots[i - 1].timeTo
        }
        this.spanModal = false
        this._clearWarn()
        return
      }
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
      this._clearWarn()
    },

    // joinGaps: make slot times continuous — end of slot N becomes start of slot N+1
    joinGaps(dayIdx) {
      const key = dayIdx === 0 && this.isEverydayActive() ? 'd1' : `d${dayIdx + 1}`
      const slots = this.draft?.data?.[key]
      if (!slots || slots.length < 2) return
      slots.sort((a, b) => (a.timeFrom || '').localeCompare(b.timeFrom || ''))
      for (let i = 1; i < slots.length; i++) {
        slots[i].timeFrom = slots[i - 1].timeTo
      }
      this._clearWarn()
    },

    async refresh() {
      this.loading = true
      try {
        const r = await fetch('/api/rules')
        if (!r.ok) return
        const d = await r.json()
        const raw = Array.isArray(d) ? d : (d.data || [])
        // Server stores everyday rules as a single active_day template.
        // Expand to d1-d7 so the UI correctly shows "every day" mode.
        this.rules = raw.map(rule => {
          // Server returns data:[] for new empty rules — must be {} for day keys to survive JSON.stringify
          if (!rule.data || Array.isArray(rule.data)) rule = { ...rule, data: {} }
          const keys = Object.keys(rule.data).filter(k => /^d\d$/.test(k))
          // active_day === "d1" is energipays's specific marker for everyday rules.
          // Any other value (null, "d3", "d6", etc.) means single-day — do NOT expand.
          if (keys.length === 1 && rule.active_day === 'd1') {
            const serverKey = keys[0]
            const template = rule.data[serverKey]
            rule = { ...rule, data: {}, _serverKey: serverKey }
            for (let i = 1; i <= 7; i++)
              rule.data[`d${i}`] = JSON.parse(JSON.stringify(template))
          }
          return rule
        })
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
      const everyday = this.ruleIsEveryday(rule)
      const entries = []
      for (let i = 0; i < 7; i++) {
        const slots = this.slotsFor(rule, i)
        if (slots.length) entries.push({ day: i, dayLabel: everyday ? 'Every day' : DAYS[i], slots })
      }
      return entries
    },

    isEveryday(rule) {
      const active = this.activeDays(rule)
      return active.length === 7
    },

    // timeline bar: percent width and position for a time slot
    slotStyle(slot, color) {
      const toMin = t => {
        const [h, m] = (t || '00:00').split(':').map(Number)
        return h * 60 + m
      }
      const left = toMin(slot.timeFrom)
      // '00:00' as timeTo means midnight end-of-day (1440), not start-of-day (0)
      let end = toMin(slot.timeTo)
      if (end === 0) end = 1440
      const width = Math.max(2, end - left)
      return `left:${(left/1440*100).toFixed(2)}%;width:${(width/1440*100).toFixed(2)}%;background:${color}`
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
      return { timeFrom: '00:00', timeTo: '23:59', command: 1, boost_power: null,
               temperature_min: null, price_max: null, price_min: null }
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

    runningSlot(rule) {
      void this._rulesTick  // reactive dependency
      const now = new Date()
      const hhmm = `${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}`
      const jsDay = now.getDay()
      const todayKey = `d${jsDay === 0 ? 7 : jsDay}`
      const data = rule.data || {}
      const keys = this.ruleIsEveryday(rule)
        ? [todayKey]
        : Object.keys(data).filter(k => /^d\d$/.test(k))
      for (const key of keys) {
        for (const slot of (data[key] || [])) {
          if (slot.timeFrom <= hhmm && hhmm < slot.timeTo) return slot
        }
      }
      return null
    },

    slotCountdown(slot) {
      const now = new Date()
      const [endH, endM] = slot.timeTo.split(':').map(Number)
      const rem = (endH * 60 + endM) - (now.getHours() * 60 + now.getMinutes())
      if (rem <= 0) return '0:00'
      return `${Math.floor(rem/60)}:${String(rem%60).padStart(2,'0')}`
    },

    slotProgress(slot) {
      const now = new Date()
      const [sh, sm] = slot.timeFrom.split(':').map(Number)
      const [eh, em] = slot.timeTo.split(':').map(Number)
      const nowMin = now.getHours() * 60 + now.getMinutes()
      const startMin = sh * 60 + sm
      const endMin = eh * 60 + em
      if (endMin <= startMin) return 100
      return Math.min(100, Math.max(0, ((nowMin - startMin) / (endMin - startMin)) * 100))
    },

    openEdit(rule) {
      this.draft = JSON.parse(JSON.stringify(rule))  // deep clone
      // For new empty rules, pre-select today's day — matches energipays.com client-side default.
      // Not persisted until user adds slots and saves.
      const dataKeys = Object.keys(this.draft.data || {}).filter(k => /^d\d$/.test(k))
      if (dataKeys.length === 0) {
        const jsDay = new Date().getDay() // 0=Sun, 1=Mon…6=Sat
        const ourKey = `d${jsDay === 0 ? 7 : jsDay}` // d1=Mon…d7=Sun
        this.draft.data = { [ourKey]: [] }
      }
      this.draftOriginalName = rule.name             // track for rename detection
      this.debugBefore = JSON.parse(JSON.stringify(rule))  // frozen copy for debug diff
      this.debugDraft = null
      this.debugServerReturn = null
      this.debugTab = 'before'
      this.saveResult = ''
      this.saveMsg = ''
      this.saveWarnBypass = false
      this.editModal = true
    },

    closeEdit() {
      if (this.debugMode && this.draft) {
        this.debugDraft = JSON.parse(JSON.stringify(this.draft))
        this.debugSnapshotLabel = 'dismissed'
      }
      this.editModal = false
      this.draft = null
    },

    openRename(rule) {
      this.renameTarget = rule
      this.renameDraft = rule.name
      this.renameMsg = ''
      this.renaming = false
      this.renameModal = true
      this.$nextTick(() => this.$el.querySelector('[x-model="renameDraft"]')?.focus())
    },

    closeRename() {
      this.renameModal = false
      this.renameTarget = null
    },

    async saveRename() {
      if (!this.renameDraft.trim() || !this.renameTarget) return
      this.renaming = true
      this.renameMsg = ''
      try {
        const res = await fetch(`/api/rules/${this.renameTarget.id}/name`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: this.renameDraft.trim() }),
        })
        if (!res.ok) throw new Error(await res.text())
        this.closeRename()
        await this.refresh()
      } catch (e) {
        this.renameMsg = e.message || 'Rename failed'
      } finally {
        this.renaming = false
      }
    },

    _clearWarn() {
      if (this.saveResult === 'warn') { this.saveResult = ''; this.saveMsg = '' }
      this.saveWarnBypass = false
    },

    _computeGapsForDay(dayIdx) {
      const key = `d${dayIdx + 1}`
      const slots = (this.draft.data[key] || []).filter(s => s.timeFrom && s.timeTo)
      if (!slots.length) return [{ from: '00:00', to: '23:59' }]
      const sorted = [...slots].sort((a, b) => a.timeFrom.localeCompare(b.timeFrom))
      const gaps = []
      if (sorted[0].timeFrom > '00:00') gaps.push({ from: '00:00', to: sorted[0].timeFrom })
      for (let i = 0; i < sorted.length - 1; i++) {
        if (sorted[i].timeTo < sorted[i + 1].timeFrom)
          gaps.push({ from: sorted[i].timeTo, to: sorted[i + 1].timeFrom })
      }
      const last = sorted[sorted.length - 1].timeTo
      if (last < '23:59') gaps.push({ from: last, to: '23:59' })
      return gaps
    },

    _applyGap(dayIdx, gap) {
      const key = `d${dayIdx + 1}`
      if (!this.draft.data[key]) this.draft.data[key] = []
      this.draft.data[key].push({
        timeFrom: gap.from, timeTo: gap.to,
        command: 2, boost_power: null,
        temperature_min: 60, price_max: null, price_min: null,
      })
      this._clearWarn()
    },

    addSlot(dayIdx) {
      const gaps = this._computeGapsForDay(dayIdx)
      if (gaps.length === 0) {
        // No gaps — fall back to appending a blank slot at the end
        const key = `d${dayIdx + 1}`
        const slots = this.draft.data[key] || []
        const lastEnd = slots.length ? slots[slots.length - 1].timeTo : '00:00'
        this._applyGap(dayIdx, { from: lastEnd, to: '23:59' })
      } else if (gaps.length === 1) {
        this._applyGap(dayIdx, gaps[0])
      } else {
        this.gapPicker = { open: true, dayIdx, gaps }
      }
    },

    pickGap(gap) {
      this._applyGap(this.gapPicker.dayIdx, gap)
      this.gapPicker.open = false
    },

    removeSlot(dayIdx, slotIdx) {
      const key = `d${dayIdx + 1}`
      this.draft.data[key].splice(slotIdx, 1)
      if (!this.draft.data[key].length) delete this.draft.data[key]
      this._clearWarn()
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
      this._clearWarn()
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
      // Collapse everyday rules back to the ORIGINAL server key before saving.
      // Never change active_day — preserve exactly what the server gave us.
      if (this.isEverydayActive() && this.draft.data['d1']) {
        const template = JSON.parse(JSON.stringify(this.draft.data['d1']))
        const serverKey = this.draft._serverKey || 'd1'
        this.draft.data = { [serverKey]: template }
        delete this.draft._serverKey
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
      if (this.debugMode) {
        this.debugDraft = JSON.parse(JSON.stringify(this.draft))
        this.debugSnapshotLabel = 'saved at ' + new Date().toLocaleTimeString()
      }
      this.saving = true
      this.saveResult = ''
      try {
        // Step 1: rename if name changed (separate PUT — server treats name and data independently)
        const nameChanged = this.draft.name && this.draft.name !== this.draftOriginalName
        if (nameChanged) {
          const rn = await fetch(`/api/rules/${this.draft.id}/name`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: this.draft.name }),
          })
          if (!rn.ok) {
            const dn = await rn.json().catch(() => ({}))
            this.saveResult = 'error'
            this.saveMsg = dn.detail || dn.error || 'Rename failed — check logs'
            return
          }
        }
        // Step 2: save schedule data
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
          const savedId = this.draft.id
          setTimeout(async () => {
            this.closeEdit()
            await this.refresh()
            if (this.debugMode) {
              const returned = this.rules.find(r => r.id === savedId)
              if (returned) {
                this.debugServerReturn = JSON.parse(JSON.stringify(returned))
                const beforeTs = this.debugBefore?.updated_at
                const afterTs = returned.updated_at
                const changed = beforeTs !== afterTs
                this.debugSnapshotLabel = changed
                  ? `✓ saved ${new Date(afterTs).toLocaleTimeString()} (was ${new Date(beforeTs).toLocaleTimeString()})`
                  : `⚠ saved but updated_at unchanged (${afterTs})`
                this.debugTab = 'returned'
              }
            }
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

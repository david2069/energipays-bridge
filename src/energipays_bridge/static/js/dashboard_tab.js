// ── Temperature ring helpers (module-level, same pattern as Modbus Bridge socRingOffset) ──

function tempRingOffset(avg, target) {
  const C = 251.3  // 2*pi*40
  if (avg == null || !target) return C
  const pct = Math.min(1, Math.max(0, avg / target))
  return C * (1 - pct)
}

function tempRingColor(avg, target) {
  if (avg == null || !target) return '#475569'
  const pct = avg / target
  if (pct >= 0.95) return '#22c55e'  // green  — at/near target
  if (pct >= 0.75) return '#f59e0b'  // amber  — close
  if (pct >= 0.55) return '#f97316'  // orange — warming
  return '#60a5fa'                   // blue   — cold
}

// ── Dashboard component ─────────────────────────────────────────────────────

// Map boost_power index (1-4) to display %
const BOOST_PCT = {1: '25%', 2: '50%', 3: '75%', 4: '100%'}

function dashboardTab() {
  return {
    weatherNem: { weather: null, nem: null },
    boosting: false,
    cancelling: false,
    settingPower: false,
    boostPeriod: 2,     // 2=1h, 3=2h, 4=3h — default 1h (period=1 is cancel)
    boostConflictOpen: false,
    boostConflictRule: null,
    boostConflictSlots: [],
    _boostConflictPeriod: 2,
    _boostSvgEl: null,
    _boostDragging: false,
    toggling: null,
    _boostMsAtPoll: 0,     // boostAntibacterialTime value from last poll
    _boostPollWallMs: 0,   // wall-clock ms when that poll arrived
    _tick: 0,              // incremented every second to drive countdown re-render
    _rulesCache: {},

    // ── Solar forecast ──────────────────────────────────────────────────────
    solarData: null,
    solarLoading: false,
    solarError: null,
    _solarRules: [],
    solarShowActual: localStorage.getItem('solarShowActual') === '1',
    solarShowTemp:   localStorage.getItem('solarShowTemp')   === '1',
    _solarActualData: [],  // [{ts, value}] from metrics history

    async fetchSolar() {
      if (this.solarLoading) return
      this.solarLoading = true
      this.solarError = null
      try {
        const fetches = [fetch('/api/solar/forecast'), fetch('/api/rules')]
        if (this.solarShowActual) fetches.push(this.fetchSolarActual())
        const [sr, rr] = await Promise.all(fetches)
        if (sr.ok) {
          const d = await sr.json()
          if (d.error) { this.solarError = d.error } else { this.solarData = d }
        }
        if (rr.ok) {
          const d = await rr.json()
          this._solarRules = Array.isArray(d) ? d : (d.data || d.rules || [])
        }
      } catch (e) { this.solarError = String(e) }
      finally { this.solarLoading = false }
    },

    async fetchSolarActual() {
      try {
        const r = await fetch('/api/metrics/history?point=ext.solar_power_w&device_id=ext&range=24h&bucket=1h')
        if (r.ok) { const d = await r.json(); this._solarActualData = d.data || [] }
      } catch {}
    },

    // Actual solar PV: Y pixel values for today's 24 hours (bars 0-23), null if no data
    get solarActualPoints() {
      if (!this._solarActualData.length || !this.solar48Hours.today.length) return []
      const chartH = 76
      const todayHours = this.solar48Hours.today
      const nowTs = Date.now() / 1000
      // Build a map: hour-of-day → watts
      const byHour = {}
      this._solarActualData.forEach(({ts, value}) => {
        const d = new Date(ts * 1000)
        if (d.toDateString() === new Date().toDateString()) byHour[d.getHours()] = value
      })
      // Normalize against max actual power, NOT irradiance (different units)
      const vals = Object.values(byHour).filter(v => v > 0)
      if (!vals.length) return []
      const maxActual = Math.max(...vals)
      const pts = []
      for (let i = 0; i < todayHours.length; i++) {
        const hh = parseInt(todayHours[i].hour.slice(11, 13))
        const slotTs = new Date(todayHours[i].hour).getTime() / 1000 + 1800
        if (slotTs > nowTs) break  // only up to NOW
        const w = byHour[hh] ?? null
        if (w === null) { pts.push(null); continue }
        const scaled = Math.min(Math.max(w, 0), maxActual)
        pts.push((chartH - (scaled / maxActual) * chartH + 6).toFixed(1))
      }
      return pts.filter(p => p !== null)
    },

    // Max actual power seen today (kW, for Y-axis label)
    get solarActualMaxKw() {
      if (!this._solarActualData.length) return null
      const vals = this._solarActualData.map(d => d.value).filter(v => v > 0)
      return vals.length ? (Math.max(...vals) / 1000).toFixed(2) : null
    },

    // Temperature: Y pixel values across all 48h (today+tomorrow), one per hour
    get solarTempPoints() {
      if (!this.solarData?.hourly?.length) return []
      const { today, tomorrow } = this.solar48Hours
      const all = [...today, ...tomorrow]
      const temps = all.map(h => h.temp_c).filter(t => t !== null)
      if (!temps.length) return []
      const minT = Math.min(...temps), maxT = Math.max(...temps)
      const range = maxT - minT || 1
      const chartH = 76
      return all.map(h => {
        if (h.temp_c === null) return null
        return (chartH - ((h.temp_c - minT) / range) * chartH * 0.8 + 6 + chartH * 0.1).toFixed(1)
      }).filter(p => p !== null)
    },

    get solarTempMin() {
      const { today, tomorrow } = this.solar48Hours
      const temps = [...today, ...tomorrow].map(h => h.temp_c).filter(t => t !== null)
      return temps.length ? Math.round(Math.min(...temps)) : null
    },
    get solarTempMax() {
      const { today, tomorrow } = this.solar48Hours
      const temps = [...today, ...tomorrow].map(h => h.temp_c).filter(t => t !== null)
      return temps.length ? Math.round(Math.max(...temps)) : null
    },

    _localDateStr(offset) {
      const d = new Date()
      d.setDate(d.getDate() + offset)
      return [d.getFullYear(), String(d.getMonth()+1).padStart(2,'0'), String(d.getDate()).padStart(2,'0')].join('-')
    },

    // Today + tomorrow hourly arrays
    get solar48Hours() {
      if (!this.solarData?.hourly?.length) return { today: [], tomorrow: [] }
      return {
        today:    this.solarData.hourly.filter(h => h.hour.startsWith(this._localDateStr(0))),
        tomorrow: this.solarData.hourly.filter(h => h.hour.startsWith(this._localDateStr(1))),
      }
    },

    // False until /api/solar/forecast reports a configured system size — the
    // API returns estimated_kw: null for every hour when kwp is unset, since
    // there is no honest number to compute (see BACKLOG.md Package 2a).
    get solarKwpConfigured() {
      return this.solarData?.kwp != null
    },

    get solarMax48() {
      const { today, tomorrow } = this.solar48Hours
      return Math.max(...[...today, ...tomorrow].map(h => h.estimated_kw || 0), 0.1)
    },

    solarBarH(v, containerH) {
      return Math.max(1, Math.round((v / this.solarMax48) * (containerH || 72)))
    },

    _solarSummarize(hours) {
      if (!hours.length || !this.solarKwpConfigured) return null
      const total = hours.reduce((s, h) => s + (h.estimated_kw || 0), 0)
      const peak  = Math.max(...hours.map(h => h.estimated_kw || 0))
      const ph    = hours.find(h => h.estimated_kw === peak)
      const t     = ph?.hour.slice(11,16) || '—'
      return {
        total:    total.toFixed(1),
        peak:     peak.toFixed(2),
        peakTime: t,
        slots:    hours.filter(h => (h.estimated_kw || 0) >= 0.1).length,
      }
    },

    get solarSummary() {
      const { today, tomorrow } = this.solar48Hours
      return { today: this._solarSummarize(today), tomorrow: this._solarSummarize(tomorrow) }
    },

    solarDayHeading(offset) {
      const d = new Date()
      d.setDate(d.getDate() + offset)
      const day  = d.toLocaleDateString('en', { weekday: 'short' }).toUpperCase()
      const date = d.toLocaleDateString('en', { day: 'numeric', month: 'long' }).toUpperCase()
      return `${day}, ${date}`
    },

    // NOW position within the 48h chart (today = left 50%, tomorrow = right 50%)
    get solarNowPct48() {
      const n = new Date()
      const minOfDay = n.getHours() * 60 + n.getMinutes()
      return (minOfDay / 1440 * 50).toFixed(2)  // 0–50% for today's position
    },

    // Rule slots for today (for the strip below the chart)
    get solarActiveRuleSlots() {
      const pts = Alpine.store('app').points
      const activeId = pts.active_rule_id || pts['sd.active_rule_id'] || null
      let rule = activeId ? this._solarRules.find(r => r.id === activeId) : null
      if (!rule) rule = this._solarRules.find(r => r.enabled !== false) || null
      if (!rule?.data) return []
      const jsDay = new Date().getDay()  // 0=Sun
      const dKey = jsDay === 0 ? 'd7' : `d${jsDay}`
      const keys = Object.keys(rule.data).filter(k => /^d\d$/.test(k))
      const useKey = keys.includes(dKey) ? dKey : (keys.includes('d1') ? 'd1' : keys[0])
      return rule.data[useKey] || []
    },

    solarSlotStyle(slot, color) {
      const toMin = t => { const [h, m] = (t || '00:00').split(':').map(Number); return h * 60 + m }
      const left = toMin(slot.timeFrom)
      let end = toMin(slot.timeTo)
      if (end === 0) end = 1440
      const width = Math.max(2, end - left)
      // Rule strip spans only the TODAY half (50% of the 48h chart)
      return `left:${(left/1440*50).toFixed(2)}%;width:${(width/1440*50).toFixed(2)}%;background:${color}`
    },

    // ── Card customization ──────────────────────────────────────────────────
    cardConfigOpen: false,
    _cardConfig: null,

    _loadCardConfig() {
      const defaults = [
        { id: 'hotwater',  label: 'Hot Water' },
        { id: 'powerflow', label: 'Energy Power Flow' },
        { id: 'solar',     label: 'Solar Forecast' },
      ]
      try {
        const stored = JSON.parse(localStorage.getItem('dashCardConfig') || 'null')
        if (stored && Array.isArray(stored)) {
          // Merge: keep stored order/enabled; add any new defaults at end
          const merged = stored
            .map(s => ({ ...(defaults.find(d => d.id === s.id) || {}), ...s }))
            .filter(s => defaults.some(d => d.id === s.id))
          defaults.forEach(d => { if (!merged.find(m => m.id === d.id)) merged.push(d) })
          this._cardConfig = merged
          return
        }
      } catch {}
      this._cardConfig = defaults.map(d => ({ ...d, enabled: true }))
    },
    _saveCardConfig() {
      localStorage.setItem('dashCardConfig', JSON.stringify(
        this._cardConfig.map(c => ({ id: c.id, enabled: c.enabled }))
      ))
    },
    get cardCfg() {
      if (!this._cardConfig) this._loadCardConfig()
      return this._cardConfig
    },
    cardOrder(id) {
      const idx = this.cardCfg.findIndex(c => c.id === id)
      return idx === -1 ? 99 : idx
    },
    cardEnabled(id) {
      const c = this.cardCfg.find(c => c.id === id)
      return c ? (c.enabled !== false) : true
    },
    toggleCard(id) {
      const c = this.cardCfg.find(c => c.id === id)
      if (!c) return
      const enabledCount = this.cardCfg.filter(x => x.enabled !== false).length
      if (c.enabled !== false && enabledCount <= 1) return  // must keep ≥1
      c.enabled = c.enabled === false ? true : false
      this._saveCardConfig()
    },
    moveCard(id, dir) {
      const idx = this.cardCfg.findIndex(c => c.id === id)
      if (idx === -1) return
      const newIdx = idx + dir
      if (newIdx < 0 || newIdx >= this.cardCfg.length) return
      const tmp = this.cardCfg[idx]
      this.cardCfg[idx] = this.cardCfg[newIdx]
      this.cardCfg[newIdx] = tmp
      this._saveCardConfig()
    },


    // ── Hot Water mini chart modal ───────────────────────────────────────────
    hotWaterModal: false,
    _hwChart: null,

    async openHotWaterChart() {
      this.hotWaterModal = true
      await this.$nextTick()
      const canvas = document.getElementById('hw-mini-chart')
      if (!canvas) return
      if (this._hwChart) { this._hwChart.destroy(); this._hwChart = null }

      const [r1, r2, r3, r4] = await Promise.all([
        fetch('/api/metrics/history?point=waterTemperature3&range=24h&bucket=15m').then(r => r.json()),
        fetch('/api/metrics/history?point=waterTemperature2&range=24h&bucket=15m').then(r => r.json()),
        fetch('/api/metrics/history?point=waterTemperature1&range=24h&bucket=15m').then(r => r.json()),
        fetch('/api/metrics/history?point=offPeakStreamHeaterPower&range=24h&bucket=15m').then(r => r.json()),
      ])

      const fmtT = ts => {
        const d = new Date(ts * 1000)
        return d.getHours().toString().padStart(2,'0') + ':' + d.getMinutes().toString().padStart(2,'0')
      }
      const labels = (r1.data || []).map(p => fmtT(p.ts))

      this._hwChart = new Chart(canvas.getContext('2d'), {
        data: {
          labels,
          datasets: [
            { type:'line', label:'T3 Top', data:(r1.data||[]).map(p=>p.value), borderColor:'#f43f5e', backgroundColor:'transparent', borderWidth:2, pointRadius:0, tension:0.3, yAxisID:'y' },
            { type:'line', label:'T2 Mid', data:(r2.data||[]).map(p=>p.value), borderColor:'#fb923c', backgroundColor:'transparent', borderWidth:2, pointRadius:0, tension:0.3, yAxisID:'y' },
            { type:'line', label:'T1 Bot', data:(r3.data||[]).map(p=>p.value), borderColor:'#60a5fa', backgroundColor:'transparent', borderWidth:2, pointRadius:0, tension:0.3, yAxisID:'y' },
            { type:'bar',  label:'Heater kW', data:(r4.data||[]).map(p=>p.value != null ? Math.abs(p.value) : null), backgroundColor:'#f97316aa', borderWidth:0, barPercentage:1.0, categoryPercentage:0.95, yAxisID:'y2' },
          ],
        },
        options: {
          responsive: true, maintainAspectRatio: false, animation: false,
          interaction: { mode:'index', intersect:false },
          plugins: {
            legend: { labels:{ color:'#94a3b8', boxWidth:12, font:{size:11} } },
            tooltip: { backgroundColor:'#1e293b', borderColor:'#334155', borderWidth:1, titleColor:'#e2e8f0', bodyColor:'#94a3b8' },
          },
          scales: {
            x: { ticks:{ color:'#64748b', maxTicksLimit:8, font:{size:10} }, grid:{ color:'#1e293b' } },
            y: { position:'left',  ticks:{ color:'#94a3b8', font:{size:10}, callback: v => v+'°' }, grid:{ color:'#1e293b' }, title:{ display:true, text:'°C', color:'#64748b', font:{size:10} } },
            y2:{ position:'right', ticks:{ color:'#f97316', font:{size:10}, callback: v => v+'kW' }, grid:{ drawOnChartArea:false }, title:{ display:true, text:'kW', color:'#f97316', font:{size:10} } },
          },
        },
      })
    },

    closeHotWaterChart() {
      this.hotWaterModal = false
      if (this._hwChart) { this._hwChart.destroy(); this._hwChart = null }
    },

    checkBoostConflict(period) {
      this._boostConflictPeriod = period
      const pts = Alpine.store('app').points
      const activeId = pts.active_rule_id
      if (!activeId) { this.sendBoost(period); return }

      const rule = (this._rulePickerRules || []).find(r => r.id === activeId)
      if (!rule) { this.sendBoost(period); return }

      const data = rule.data || {}
      const now = new Date()
      const hhmm = String(now.getHours()).padStart(2,'0') + ':' + String(now.getMinutes()).padStart(2,'0')
      const jsDay = now.getDay()
      const todayKey = `d${jsDay === 0 ? 7 : jsDay}`
      const everyday = rule.everyday || Object.keys(data).filter(k => /^d\d$/.test(k)).length === 7
      const keys = everyday ? [todayKey] : Object.keys(data).filter(k => /^d\d$/.test(k))

      const CMD_LABELS = { 1: 'Disable', 2: 'Boost' }
      const conflicting = []
      for (const key of keys) {
        for (const slot of (data[key] || [])) {
          const cmd = parseInt(slot.command ?? 0)
          if (cmd !== 1 && cmd !== 2) continue
          // Slot overlaps with now or is upcoming today
          if (slot.timeTo > hhmm) {
            conflicting.push({
              time: slot.timeFrom + ' – ' + slot.timeTo,
              label: CMD_LABELS[cmd] || 'Command',
            })
          }
        }
      }

      this.boostConflictRule = rule
      this.boostConflictSlots = conflicting
      if (conflicting.length > 0) {
        this.boostConflictOpen = true
      } else {
        this.sendBoost(period)
      }
    },

    async sendBoost(period, clearRule = false) {
      if (this.boosting) return
      this.boosting = true
      try {
        const r = await fetch('/api/boost', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ period, clear_rule: clearRule }),
        })
        const d = await r.json()
        const pct = this.boostPowerLabel() || '—'
        const hrs = {2:'1h', 3:'2h', 4:'3h'}[period] || `${period}h`
        if (!r.ok) {
          // d.detail already reads "Boost failed: ..." (set server-side) — don't re-prefix it
          Alpine.store('app').addToast(d.detail || 'Boost failed: unknown error', 'error')
        } else {
          // Optimistically flip — guard in _fetchPoints prevents poll from overwriting
          Alpine.store('app')._boostPendingTs = Date.now()
          Alpine.store('app').points.boostStatus = true
          const clearedNote = clearRule ? ' (active rule cleared — re-enable it from Rules when done)' : ''
          Alpine.store('app').addToast(`Boost ${hrs} @ ${pct} started${clearedNote}`, 'success')
        }
      } catch (e) {
        Alpine.store('app').addToast('Boost: network error', 'error')
      } finally {
        this.boosting = false
      }
    },

    async cancelBoost() {
      if (this.cancelling) return
      this.cancelling = true
      try {
        const r = await fetch('/api/boost/cancel', { method: 'POST' })
        const d = await r.json()
        if (!r.ok) {
          Alpine.store('app').addToast(d.detail || 'Cancel failed', 'error')
        } else {
          Alpine.store('app').addToast('Boost cancelled', 'info')
        }
      } catch (e) {
        Alpine.store('app').addToast('Network error', 'error')
      } finally {
        this.cancelling = false
      }
    },

    // Remaining boost time — counts down client-side between 60s polls
    boostRemaining() {
      void this._tick  // Alpine dependency — re-evaluates every second
      const pollMs = Alpine.store('app').points.boostAntibacterialTime
      if (!pollMs || pollMs <= 0) return '—'
      // Adjust for elapsed time since the poll arrived
      const elapsed = this._boostPollWallMs ? (Date.now() - this._boostPollWallMs) : 0
      const ms = Math.max(0, pollMs - elapsed)
      if (ms <= 0) return '0min'
      const totalSec = Math.round(ms / 1000)
      const h = Math.floor(totalSec / 3600)
      const m = Math.floor((totalSec % 3600) / 60)
      const s = totalSec % 60
      return h > 0 ? `${h}h ${m}min` : m > 0 ? `${m}min ${s}s` : `${s}s`
    },

    // Called from app.js whenever fresh points arrive — capture wall-clock for countdown
    onPointsUpdate(pts) {
      const newMs = pts.boostAntibacterialTime
      if (newMs && newMs !== this._boostMsAtPoll) {
        this._boostMsAtPoll = newMs
        this._boostPollWallMs = Date.now()
      }
    },

    // Set boost power level on device (index 1-4 = 25/50/75/100%)
    async setBoostPower(idx) {
      if (this.settingPower) return
      this.settingPower = true
      try {
        const r = await fetch('/api/device/set', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ fields: { boost_power: idx } }),
        })
        if (r.ok) {
          // Update both keys so boostPowerPct() reflects the change immediately
          // (boostPowerPct reads 'boost_power' first; 'dev.boost_power' is a fallback)
          Alpine.store('app').points['boost_power'] = idx
          Alpine.store('app').points['dev.boost_power'] = idx
        } else {
          const d = await r.json()
          Alpine.store('app').addToast(d.detail || 'Power set failed', 'error')
        }
      } catch (e) {
        Alpine.store('app').addToast('Network error', 'error')
      } finally {
        this.settingPower = false
      }
    },

    // Boost power as percentage (boost_power index 1–4, device max = 4)
    boostPowerPct() {
      const idx = Alpine.store('app').points['boost_power']
             ?? Alpine.store('app').points['dev.boost_power']
      if (!idx) return 0
      return Math.round((idx / 4) * 100)
    },

    boostPowerLabel() {
      const idx = Alpine.store('app').points['boost_power']
             ?? Alpine.store('app').points['dev.boost_power']
      return BOOST_PCT[idx] || '—'
    },

    async init() {
      try {
        const r = await fetch('/api/rules')
        if (r.ok) {
          const d = await r.json()
          const rules = Array.isArray(d) ? d : (d.data || d.rules || [])
          rules.forEach(rule => { if (rule.id) this._rulesCache[rule.id] = rule.name || rule.id })
          this._rulePickerRules = rules
        }
      } catch (_) {}
      this._fetchWeatherNem()
      setInterval(() => this._fetchWeatherNem(), 300000)
      this.fetchSolar()
      setInterval(() => this.fetchSolar(), 3600000)
    },

    async _fetchWeatherNem() {
      try {
        const r = await fetch('/api/weather-nem')
        if (r.ok) {
          const d = await r.json()
          this.weatherNem = d
          // Also push to Alpine.store('dashboard') so nested SVG x-data can read it
          if (Alpine.store('dashboard')) Alpine.store('dashboard').weatherNem = d
        }
      } catch(e) {}
    },

    activeRuleName() {
      const id = Alpine.store('app').points.active_rule_id
      if (!id) return null
      return this._rulesCache[id] || id.slice(0, 8) + '…'
    },

    activeRuleNameForType(type) {
      const ptMap = { command: 'active_rule_id', offpeak: 'active_rule_offpeak_id', heater2: 'active_rule_heater2_id' }
      const id = Alpine.store('app').points[ptMap[type] || 'active_rule_id']
      if (!id) return null
      return this._rulesCache[id] || id.slice(0, 8) + '…'
    },

    _rulePickerType: 'command',  // circuit being activated via confirm modal

    // Returns a CSS colour based on how close val is to target (for T-stack dots)
    tempColor(val, target) {
      if (!val || !target) return '#475569'
      const diff = target - val
      if (diff <= 2)  return '#22c55e'
      if (diff <= 15) return '#f59e0b'
      if (diff <= 30) return '#f97316'
      return '#60a5fa'
    },

    async toggleDevice(field, currentVal) {
      if (this.toggling) return
      const newVal = currentVal == 1 ? 0 : 1
      const label = field === 'customer' ? 'Power Diverter' : field === 'heaterStatus' ? 'Heater' : field
      this.toggling = field
      try {
        const r = await fetch('/api/device/set', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ fields: { [field]: newVal } }),
        })
        const d = await r.json()
        if (!r.ok) {
          Alpine.store('app').addToast((d.detail || `${label} toggle failed`), 'error')
        } else {
          Alpine.store('app').addToast(`${label} turned ${newVal ? 'ON' : 'OFF'}`, 'info')
          Alpine.store('app').points[`sd.${field}`] = newVal
        }
      } catch (e) {
        Alpine.store('app').addToast('Network error', 'error')
      } finally {
        this.toggling = null
      }
    },

    async applyWeatherBoost() {
      this.wbSaving = true
      try {
        const r = await fetch('/api/device/set', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ fields: { weatherSwitcherStatus: this.wbPending } }),
        })
        const d = await r.json()
        if (!r.ok) {
          Alpine.store('app').addToast(d.detail || 'Weather boost failed', 'error')
        } else {
          Alpine.store('app').points['sd.weatherSwitcherStatus'] = this.wbPending
          Alpine.store('app').addToast(`Weather boost ${this.wbPending ? 'enabled' : 'disabled'}`, 'info')
          this.weatherBoostOpen = false
        }
      } catch (e) {
        Alpine.store('app').addToast('Network error', 'error')
      } finally {
        this.wbSaving = false
      }
    },

    // ── Circular boost power slider ───────────────────────────────────────────
    // Arc: counter-clockwise from 225° to 315° (270° sweep), radius 38, centre 60,60
    // idx 1=25%, 2=50%, 3=75%, 4=100% mapped proportionally along the arc

    _boostPoint(deg) {
      const rad = deg * Math.PI / 180
      return [60 + 38 * Math.cos(rad), 60 + 38 * Math.sin(rad)]
    },
    _boostFillAngle(idx) {
      // counter-clockwise from 225°: 25% → 157.5°, 50% → 90°, 75% → 22.5°, 100% → -45°=315°
      return 225 - (idx / 4) * 270
    },
    boostTrackPath() {
      const [x1, y1] = this._boostPoint(225)
      const [x2, y2] = this._boostPoint(315)
      // counter-clockwise (sweep=0), large-arc=1
      return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A 38 38 0 1 0 ${x2.toFixed(2)} ${y2.toFixed(2)}`
    },
    boostFillPath() {
      const idx = Alpine.store('app').points['boost_power'] ?? Alpine.store('app').points['dev.boost_power']
      if (!idx || idx < 1) return ''
      const span = (idx / 4) * 270
      if (span < 1) return ''
      const endDeg = this._boostFillAngle(idx)
      const [x1, y1] = this._boostPoint(225)
      const [x2, y2] = this._boostPoint(endDeg)
      const large = span > 180 ? 1 : 0
      return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A 38 38 0 ${large} 0 ${x2.toFixed(2)} ${y2.toFixed(2)}`
    },
    boostHandleX() {
      const idx = Alpine.store('app').points['boost_power'] ?? Alpine.store('app').points['dev.boost_power'] ?? 1
      return this._boostPoint(this._boostFillAngle(Math.max(1, Math.min(4, idx))))[0].toFixed(2)
    },
    boostHandleY() {
      const idx = Alpine.store('app').points['boost_power'] ?? Alpine.store('app').points['dev.boost_power'] ?? 1
      return this._boostPoint(this._boostFillAngle(Math.max(1, Math.min(4, idx))))[1].toFixed(2)
    },
    boostSliderStart(e) {
      this._boostSvgEl = e.currentTarget
      this._boostDragging = true
      this._boostUpdateFromMouse(e.clientX, e.clientY)
    },
    boostSliderMove(e) {
      if (!this._boostDragging) return
      this._boostUpdateFromMouse(e.clientX, e.clientY)
    },
    boostSliderEnd() { this._boostDragging = false },
    boostSliderStartTouch(e) {
      if (!e.touches[0]) return
      this._boostSvgEl = e.currentTarget
      this._boostDragging = true
      this._boostUpdateFromMouse(e.touches[0].clientX, e.touches[0].clientY)
    },
    boostSliderMoveTouch(e) {
      if (!this._boostDragging || !e.touches[0]) return
      this._boostUpdateFromMouse(e.touches[0].clientX, e.touches[0].clientY)
    },
    _boostUpdateFromMouse(clientX, clientY) {
      if (!this._boostSvgEl) return
      const rect = this._boostSvgEl.getBoundingClientRect()
      const mx = (clientX - rect.left) * (120 / rect.width)
      const my = (clientY - rect.top) * (120 / rect.height)
      const dx = mx - 60, dy = my - 60
      // angle in SVG coords (clockwise from east)
      let angle = Math.atan2(dy, dx) * 180 / Math.PI
      if (angle < 0) angle += 360
      // convert to arc-relative position: 0 = start (225°), increasing = counter-clockwise
      let arcPos = ((225 - angle) % 360 + 360) % 360
      // clamp to arc range [0, 270]
      if (arcPos > 270) arcPos = arcPos > 315 ? 0 : 270
      // snap to nearest of 4 steps (each 90° apart)
      const idx = Math.max(1, Math.min(4, Math.round(arcPos / 90) + 1))
      this.setBoostPower(idx)
    },

    // ── Device Rules modal ────────────────────────────────────────────────────
    deviceRulesOpen: false,
    weatherBoostOpen: false,
    wbPending: 0,
    wbSaving: false,

    // ── Rule confirm modal (triggered by Device Rules modal dropdowns) ────────
    _rulePickerRules: [],

    get rulePickerOptions() {
      const TYPE_LABEL = { command: 'PD', offpeak: 'Off-Peak', heater2: 'Heater 2' }
      const types = [...new Set(this._rulePickerRules.map(r => r.type))]
      const multiType = types.length > 1
      const opts = [{ value: '', label: 'No Rule Set' }]
      for (const type of ['command', 'offpeak', 'heater2']) {
        const group = this._rulePickerRules.filter(r => r.type === type)
        for (const r of group) {
          opts.push({ value: r.id, label: (multiType ? TYPE_LABEL[type] + ': ' : '') + (r.name || r.id) })
        }
      }
      return opts
    },

    get rulePickerValue() {
      const pts = Alpine.store('app').points
      return pts.active_rule_id || pts.active_rule_offpeak_id || pts.active_rule_heater2_id || ''
    },
    _rulePickerConfirmOpen: false,
    _rulePickerPendingId: '',    // rule ID selected, awaiting confirm
    _rulePickerPendingName: '',  // display name for confirm modal
    _rulePickerBusy: false,
    _rulePickerError: '',

    openRuleConfirm(ruleId, ruleType) {
      const ptMap = { command: 'active_rule_id', offpeak: 'active_rule_offpeak_id', heater2: 'active_rule_heater2_id' }
      const current = Alpine.store('app').points?.[ptMap[ruleType] || 'active_rule_id'] || ''
      if (ruleId == null || ruleId === current) return
      const clearing = ruleId === '0' || ruleId === ''
      const rule = this._rulePickerRules.find(r => r.id === ruleId)
      this._rulePickerPendingId = clearing ? '0' : ruleId
      this._rulePickerPendingName = clearing ? '(none — clear rule)' : (rule?.name || ruleId)
      this._rulePickerType = ruleType || rule?.type || 'command'
      this._rulePickerError = ''
      this._rulePickerConfirmOpen = true
    },

    cancelRuleConfirm() {
      this._rulePickerConfirmOpen = false
      this._rulePickerPendingId = ''
      this._rulePickerPendingName = ''
      this._rulePickerError = ''
    },

    async confirmRulePicker() {
      if (this._rulePickerBusy) return
      const ruleId = this._rulePickerPendingId
      if (!ruleId) { this._rulePickerError = 'No rule selected'; return }
      this._rulePickerBusy = true
      this._rulePickerError = ''
      try {
        const ruleType = this._rulePickerType || 'command'
        const clearing = ruleId === '0'
        const r = await fetch('/api/device/set', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ rule_id: ruleId, rule_type: ruleType }),
        })
        const d = await r.json()
        if (r.ok) {
          const ptMap = { command: 'active_rule_id', offpeak: 'active_rule_offpeak_id', heater2: 'active_rule_heater2_id' }
          Alpine.store('app').points[ptMap[ruleType] || 'active_rule_id'] = clearing ? null : ruleId
          Alpine.store('app').addToast(clearing ? 'Rule cleared' : `Rule "${this._rulePickerPendingName}" enabled`, 'success')
          this.cancelRuleConfirm()
        } else {
          this._rulePickerError = d.detail || `Error ${r.status}`
        }
      } catch (e) {
        this._rulePickerError = 'Network error — check connection'
      } finally {
        this._rulePickerBusy = false
      }
    },

    fmt,
    tempRingOffset,
    tempRingColor,
  }
}

// ── Dashboard running-slot helpers (mirror rules_tab equivalents) ──────────
function dashRunningSlot(rule) {
  if (!rule) return null
  const now = new Date()
  const hhmm = String(now.getHours()).padStart(2,'0') + ':' + String(now.getMinutes()).padStart(2,'0')
  const jsDay = now.getDay()
  const todayKey = `d${jsDay === 0 ? 7 : jsDay}`
  const data = rule.data || {}
  const everyday = rule.everyday || Object.keys(data).filter(k => /^d\d$/.test(k)).length === 7
  const keys = everyday ? [todayKey] : Object.keys(data).filter(k => /^d\d$/.test(k))
  for (const key of keys) {
    for (const slot of (data[key] || [])) {
      if (slot.timeFrom <= hhmm && hhmm < slot.timeTo) return slot
    }
  }
  return null
}

function dashSlotCountdown(slot) {
  if (!slot) return ''
  const now = new Date()
  const [endH, endM] = slot.timeTo.split(':').map(Number)
  const rem = (endH * 60 + endM) - (now.getHours() * 60 + now.getMinutes())
  if (rem <= 0) return '0:00'
  return `${Math.floor(rem/60)}:${String(rem%60).padStart(2,'0')}`
}

function dashSlotProgress(slot) {
  if (!slot) return 0
  const now = new Date()
  const cur = now.getHours() * 60 + now.getMinutes()
  const [sh, sm] = slot.timeFrom.split(':').map(Number)
  const [eh, em] = slot.timeTo.split(':').map(Number)
  const start = sh * 60 + sm, end = eh * 60 + em
  if (end <= start) return 0
  return Math.min(100, Math.max(0, Math.round((cur - start) / (end - start) * 100)))
}

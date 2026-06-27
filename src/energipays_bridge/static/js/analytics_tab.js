// Chart.js stored in closure — NOT Alpine reactive (avoids Proxy recursion)
let _analyticsChart = null
let _chartData = []   // latest fetched data for export

// Cloud stats field definitions — mirrors energipays.com chart legend
// Keys match raw analytics API field names confirmed from HAR capture 2026-06-25
const CLOUD_SERIES = [
  { key: 'EIP',  label: 'Home import',        color: '#22d3ee', type: 'bar', abs: true },
  { key: 'SP',   label: 'Solar gen.',         color: '#4ade80', type: 'bar', abs: true },
  { key: 'GLPH', label: 'Diverted heater',    color: '#0891b2', type: 'bar', abs: true },
  { key: 'GLPE', label: 'Diverted extra',     color: '#059669', type: 'bar', abs: true },
  { key: 'OLPH', label: 'Heater',             color: '#ec4899', type: 'bar', abs: true },
  { key: 'OLPE', label: 'Extra plug',         color: '#8b5cf6', type: 'bar', abs: true },
  { key: 'WHD',  label: 'Off-Peak heater',    color: '#f97316', type: 'bar', abs: true },
  { key: 'EPD',  label: 'Off-Peak extra plug',color: '#a78bfa', type: 'bar', abs: true },
  { key: 'SoC',  label: 'SOC (%)',            color: '#84cc16', type: 'line', yAxis: 'right' },
  { key: 't3',   label: 'T3 Top (°C)',        color: '#f43f5e', type: 'line', yAxis: 'right' },
  { key: 't2',   label: 'T2 Mid (°C)',        color: '#fb923c', type: 'line', yAxis: 'right' },
  { key: 't1',   label: 'T1 Bot (°C)',        color: '#60a5fa', type: 'line', yAxis: 'right' },
  // computed client-side as (t1+t2+t3)/3 — no direct API field
  { key: '_tAvg', label: 'Average T (°C)',    color: '#06b6d4', type: 'line', yAxis: 'right', computed: true },
]

function analyticsTab() {
  return {
    // ── mode ──────────────────────────────────────────────────────────────
    mode: localStorage.getItem('analyticsSource') || 'cloud',   // 'local' | 'cloud'

    // ── local mode ────────────────────────────────────────────────────────
    ranges: ['5m','15m','30m','1h','2h','4h','6h','8h','12h','18h','24h','5d','7d','14d','30d'],
    buckets: ['1m','5m','15m','1h'],
    rangeOrCustom: '24h',
    localSeries: [
      { key: 'phasePower',          label: 'Grid (kW)',       color: '#f59e0b', type: 'line' },
      { key: 'today.IEct',          label: 'Import (kWh)',    color: '#ef4444', type: 'bar' },
      { key: 'today.EEct',          label: 'Export (kWh)',    color: '#22c55e', type: 'bar' },
      { key: 'today.DE_h',          label: 'Diverted (kWh)',  color: '#3b82f6', type: 'bar' },
      { key: 'waterTemperatureAvg', label: 'Avg Temp (°C)',   color: '#f43f5e', type: 'line', yAxis: 'right' },
      { key: 'waterTemperature3',   label: 'T3 Top (°C)',     color: '#a78bfa', type: 'line', yAxis: 'right' },
      { key: 'waterTemperature2',   label: 'T2 Mid (°C)',     color: '#fb923c', type: 'line', yAxis: 'right' },
      { key: 'waterTemperature1',   label: 'T1 Bot (°C)',     color: '#60a5fa', type: 'line', yAxis: 'right' },
    ],
    get series() { return this.mode === 'cloud' ? CLOUD_SERIES : this.localSeries },
    range: '24h',
    localDayMode: 'rolling',   // 'today' | 'yesterday' | 'date' | 'rolling'
    localPickDate: '',         // YYYY-MM-DD for 'date' mode
    bucket: '15m',
    activeSeries: JSON.parse(localStorage.getItem('analyticsLocalSeries') || 'null') || ['phasePower', 'today.IEct', 'today.EEct', 'today.DE_h'],
    activeCloudSeries: (() => { const s = JSON.parse(localStorage.getItem('analyticsCloudSeries') || 'null'); return s ? s.filter(k => k !== 't3' && k !== 't' && k !== 'EEP') : ['EIP', 'SP', 'GLPH', 'OLPH', '_tAvg']; })(),
    loading: false,
    signedMode: localStorage.getItem('analyticsSignedMode') === '1',  // false = energipays flat, true = signed
    chartStyle: localStorage.getItem('analyticsChartStyle') || 'area',   // 'bars' | 'area'
    showCustomRange: false,
    useCustomRange: false,
    customFrom: '',
    customTo: '',

    // ── cloud mode ────────────────────────────────────────────────────────
    cloudDate: '',          // YYYY-MM-DD — end date for cloud view
    cloudSpan: '1d',        // '1d' | '7d' | '30d'
    cloudPhase: 'sum',      // 'sum' | 'l1' | 'l2' | 'l3'
    cloudError: '',

    async init() {
      const now = new Date()
      const yesterday = new Date(now - 86400000)
      this.customTo = _toDatetimeLocal(now)
      this.customFrom = _toDatetimeLocal(yesterday)
      // Default cloud date = today
      this.cloudDate = _toDateStr(now)
      // On mobile: default local range to 1h, cloud span to 6h (less dense)
      if (window.innerWidth < 768) {
        this.rangeOrCustom = '1h'
        this.cloudSpan = '6h'
      }
      await this.$nextTick()
      this._buildChart()
      await this.load()
      this.$watch('$store.app.activeTab', val => {
        if (val === 'analytics') {
          this.$nextTick(() => {
            if (_analyticsChart) _analyticsChart.resize()
            else { this._buildChart(); this.load() }
          })
        }
      })
      this.$watch('$store.app.isDark', () => this._applyThemeToChart())
    },

    _chartColors() {
      const dark = Alpine.store('app').isDark
      return {
        legend:   dark ? '#94a3b8' : '#334155',
        ticks:    dark ? '#64748b' : '#475569',
        grid:     dark ? '#1e293b' : '#e2e8f0',
      }
    },

    _applyThemeToChart() {
      if (!_analyticsChart) return
      const c = this._chartColors()
      _analyticsChart.options.plugins.legend.labels.color = c.legend
      _analyticsChart.options.scales.x.ticks.color = c.ticks
      _analyticsChart.options.scales.x.grid.color  = c.grid
      _analyticsChart.options.scales.y.ticks.color = c.ticks
      _analyticsChart.options.scales.y.grid.color  = c.grid
      _analyticsChart.update('none')
    },

    _resizeChart() {
      if (_analyticsChart) {
        _analyticsChart.resize()
        // Belt-and-suspenders: mobile browsers sometimes need a second pass
        // after layout settles (scroll/flex recalc)
        setTimeout(() => { if (_analyticsChart) _analyticsChart.resize() }, 150)
      }
    },

    _buildChart() {
      const canvas = document.getElementById('analyticsChart')
      if (!canvas || _analyticsChart) return
      // Defer if container is hidden (x-show=display:none → 0 dimensions).
      // The activeTab watcher will retry once the tab is visible.
      if (canvas.offsetWidth === 0) return
      canvas.style.width = '100%'
      canvas.style.height = '100%'
      const c = this._chartColors()
      const nowLinePlugin = {
        id: 'nowLine',
        afterDraw(chart) {
          const nowLabel = chart._nowLabel
          if (!nowLabel) return
          const labels = chart.data.labels
          if (!labels || !labels.length) return
          // find the label index closest to nowLabel
          let idx = labels.indexOf(nowLabel)
          if (idx < 0) {
            // pick nearest by string comparison (HH:MM)
            idx = labels.reduce((best, lbl, i) => lbl <= nowLabel ? i : best, -1)
          }
          if (idx < 0) return
          const meta = chart.getDatasetMeta(chart.data.datasets.findIndex(d => d.data.length > 0))
          if (!meta || !meta.data[idx]) return
          const x = meta.data[idx].x
          const { top, bottom } = chart.chartArea
          const ctx = chart.ctx
          ctx.save()
          ctx.beginPath()
          ctx.setLineDash([4, 4])
          ctx.strokeStyle = 'rgba(99,102,241,0.7)'
          ctx.lineWidth = 1.5
          ctx.moveTo(x, top)
          ctx.lineTo(x, bottom)
          ctx.stroke()
          ctx.fillStyle = 'rgba(99,102,241,0.9)'
          ctx.font = '10px sans-serif'
          ctx.fillText('Now', x + 3, top + 12)
          ctx.restore()
        }
      }
      _analyticsChart = new Chart(canvas.getContext('2d'), {
        type: 'bar',
        data: { labels: [], datasets: [] },
        plugins: [nowLinePlugin],
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: { duration: 200 },
          plugins: {
            legend: { position: 'bottom', labels: { color: c.legend, boxWidth: 12, padding: 8, font: { size: 11 } } },
            tooltip: { mode: 'index', intersect: false },
          },
          scales: {
            x: {
              stacked: true,
              ticks: { color: c.ticks, maxTicksLimit: 10, font: { size: 10 } },
              grid:  { color: c.grid },
            },
            y: {
              type: 'linear', position: 'left', stacked: true,
              ticks: { color: c.ticks, font: { size: 10 } },
              grid:  { color: c.grid },
            },
            yRight: {
              type: 'linear', position: 'right',
              display: false,
              ticks: { color: '#f43f5e', font: { size: 10 }, callback: v => v + '°' },
              grid:   { drawOnChartArea: false },
              title:  { display: true, text: '°C', color: '#f43f5e', font: { size: 10 } },
            },
          },
        },
      })
    },

    // ── toggle mode ────────────────────────────────────────────────────────
    setMode(m) {
      this.mode = m
      localStorage.setItem('analyticsSource', m)
      // default chart style per source if not yet overridden
      if (!localStorage.getItem('analyticsChartStyle')) {
        this.chartStyle = m === 'local' ? 'area' : 'bars'
      }
      this._resetChart()
      this.load()
    },

    _resetChart() {
      if (_analyticsChart) {
        _analyticsChart.data.labels = []
        _analyticsChart.data.datasets = []
        _analyticsChart.update()
      }
    },

    // ── load dispatcher ────────────────────────────────────────────────────
    async load() {
      if (this.mode === 'cloud') await this._loadCloud()
      else await this._loadLocal()
    },

    // ── local SQLite load ─────────────────────────────────────────────────
    _rangeParams() {
      if (this.useCustomRange && this.customFrom && this.customTo) {
        const from = new Date(this.customFrom).getTime() / 1000
        const to   = new Date(this.customTo).getTime()   / 1000
        return `from=${from}&to=${to}&bucket=${this.bucket}`
      }
      if (this.localDayMode === 'today') {
        const d = new Date(); d.setHours(0,0,0,0)
        return `from=${d.getTime()/1000}&to=${Date.now()/1000}&bucket=${this.bucket}`
      }
      if (this.localDayMode === 'yesterday') {
        const d = new Date(); d.setHours(0,0,0,0); d.setDate(d.getDate()-1)
        const e = new Date(d); e.setDate(e.getDate()+1)
        return `from=${d.getTime()/1000}&to=${e.getTime()/1000}&bucket=${this.bucket}`
      }
      if (this.localDayMode === 'date' && this.localPickDate) {
        const d = new Date(this.localPickDate + 'T00:00:00')
        const e = new Date(this.localPickDate + 'T00:00:00'); e.setDate(e.getDate()+1)
        return `from=${d.getTime()/1000}&to=${e.getTime()/1000}&bucket=${this.bucket}`
      }
      return `range=${this.range}&bucket=${this.bucket}`
    },

    async _loadLocal() {
      if (!_analyticsChart) return
      this.loading = true
      this.cloudError = ''
      _chartData = []
      try {
        const allDatasets = []
        let labels = []
        for (const key of this.activeSeries) {
          const s = this.localSeries.find(x => x.key === key)
          if (!s) continue
          const r = await fetch(`/api/metrics/history?point=${encodeURIComponent(key)}&${this._rangeParams()}`)
          if (!r.ok) continue
          const d = await r.json()
          const isArea = this.chartStyle === 'area'
          const isBar = !isArea && s.type === 'bar'
          const rawVals = d.data.map(p => p.value ?? null)
          const dispVals = rawVals.map(v => (s.type === 'bar' && !this.signedMode && v !== null) ? Math.abs(v) : v)
          allDatasets.push({
            label: s.label,
            data: dispVals,
            borderColor: s.color,
            backgroundColor: isArea ? s.color + '33' : isBar ? s.color + 'cc' : 'transparent',
            borderWidth: isArea ? 2 : isBar ? 0 : 2,
            pointRadius: 0,
            fill: isArea && s.yAxis !== 'right',
            tension: 0.4,
            type: isArea ? 'line' : (s.type || 'line'),
            yAxisID: s.yAxis === 'right' ? 'yRight' : 'y',
            barPercentage: 1.0,
            categoryPercentage: 0.95,
            spanGaps: false,
          })
          if (!labels.length) {
            labels = d.data.map(p =>
              new Date(p.ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
            )
          }
        }
        const hasRight = allDatasets.some(ds => ds.yAxisID === 'yRight')
        _analyticsChart.options.scales.yRight.display = hasRight
        _analyticsChart.data.labels = labels
        _analyticsChart.data.datasets = allDatasets
        // Stacking: flat mode = stacked bars, signed = grouped
        const localStacked = !this.signedMode
        _analyticsChart.options.scales.x.stacked = localStacked
        _analyticsChart.options.scales.y.stacked = localStacked
        // Show "Now" line for local mode (always current time) — HH:MM to match label format
        const _ln = new Date()
        _analyticsChart._nowLabel = String(_ln.getHours()).padStart(2,'0') + ':' + String(_ln.getMinutes()).padStart(2,'0')
        _analyticsChart.update()
      } finally {
        this.loading = false
        await this.$nextTick()
        this._resizeChart()
      }
    },

    // ── cloud stats load ──────────────────────────────────────────────────
    _cloudDateFrom() {
      const days = this.cloudSpan === '30d' ? 29 : this.cloudSpan === '7d' ? 6 : 0
      const d = new Date(this.cloudDate + 'T12:00:00')
      d.setDate(d.getDate() - days)
      return _toDateStr(d)
    },

    async _loadCloud() {
      if (!_analyticsChart || !this.cloudDate) return
      this.loading = true
      this.cloudError = ''
      _chartData = []
      try {
        const url = `/api/cloud/stats?date_from=${this._cloudDateFrom()}&date_to=${this.cloudDate}&data_type=power&phase=${this.cloudPhase}`
        const r = await fetch(url)
        if (!r.ok) {
          const err = await r.json().catch(() => ({}))
          this.cloudError = err.detail || `HTTP ${r.status}`
          return
        }
        const json = await r.json()
        this._renderCloudData(json)
      } catch (e) {
        this.cloudError = e.message
      } finally {
        this.loading = false
        await this.$nextTick()
        this._resizeChart()
      }
    },

    _renderCloudData(json) {
      // The raw field from cloud API — try several known response shapes
      const raw = json.raw || json
      // Energipays stats returns: { data: [ {time, IEct, EEct, DE_h, ...}, ... ] }
      // or { IEct: [...], EEct: [...], ... } — handle both
      let rows = []
      let labels = []

      const multiDay = this.cloudSpan !== '1d' && this.cloudSpan !== '6h'
      const _fmtTime = t => {
        if (!t) return ''
        if (typeof t === 'string' && t.length >= 16) {
          const time = t.slice(11, 16)            // HH:MM
          const day  = t.slice(8, 10) + '/' + t.slice(5, 7)  // DD/MM
          if (!multiDay) return time
          // Multi-day: show "DD/MM" at midnight, otherwise "HH:MM"
          return time === '00:00' ? day : time
        }
        if (typeof t === 'number') {
          const d = new Date(t * 1000)
          if (!multiDay) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
          return d.getHours() === 0 && d.getMinutes() === 0
            ? d.toLocaleDateString([], { day: '2-digit', month: '2-digit' })
            : d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
        }
        return String(t)
      }

      // Raw ISO timestamps for 6h slicing (kept alongside labels/rows)
      let rawTs = []

      // Primary format: { analytics: { "YYYY-MM-DD HH:MM:SS": { EIP, SP, ... } }, interval: ... }
      if (raw?.analytics && typeof raw.analytics === 'object' && !Array.isArray(raw.analytics)) {
        const entries = Object.entries(raw.analytics).sort((a, b) => a[0] < b[0] ? -1 : 1)
        rawTs  = entries.map(([ts]) => ts)
        labels = entries.map(([ts]) => _fmtTime(ts))
        rows   = entries.map(([, v]) => v)
      } else if (Array.isArray(raw)) {
        rows   = raw
        labels = rows.map(r => _fmtTime(r.time || r.ts || r.date || ''))
      } else if (Array.isArray(raw?.data)) {
        rows   = raw.data
        labels = rows.map(r => _fmtTime(r.time || r.ts || r.date || ''))
      } else if (raw && typeof raw === 'object') {
        // Column-oriented: { key: [{time,value},...], ... }
        const firstKey = Object.keys(raw).find(k => Array.isArray(raw[k]))
        if (firstKey) {
          labels = raw[firstKey].map(r => _fmtTime(r.time || r.ts || ''))
          for (const k of Object.keys(raw)) {
            if (Array.isArray(raw[k])) rows.push({ _key: k, _values: raw[k].map(r => r.value ?? r) })
          }
        }
      }

      // 6h view: find the last entry at or before now, slice 12 entries back from there
      if (this.cloudSpan === '6h' && rows.length > 12 && rawTs.length === rows.length) {
        const nowStr = new Date().toISOString().slice(0, 16).replace('T', ' ') // "YYYY-MM-DD HH:MM"
        let nowIdx = rawTs.reduce((best, ts, i) => ts.slice(0,16) <= nowStr ? i : best, 11)
        const end   = nowIdx + 1
        const start = Math.max(0, end - 12)
        rows   = rows.slice(start, end)
        labels = labels.slice(start, end)
      }

      const active = this.activeCloudSeries
      const allDatasets = []

      for (const cs of CLOUD_SERIES) {
        if (!active.includes(cs.key)) continue
        let values = []

        if (cs.computed) {
          // _tAvg: compute as mean of t1, t2, t3
          values = rows.map(r => {
            const t1 = parseFloat(r.t1), t2 = parseFloat(r.t2), t3 = parseFloat(r.t3)
            const vals = [t1, t2, t3].filter(v => !isNaN(v) && v !== 0)
            return vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null
          })
        } else if (rows.length && rows[0]?._key !== undefined) {
          // column-oriented rows
          const col = rows.find(r => r._key === cs.key)
          values = col ? col._values : []
        } else {
          // row-oriented — always store raw signed values for export
          values = rows.map(r => {
            const v = r[cs.key]
            if (v == null) return null
            return parseFloat(v)
          })
        }

        // Export always uses true signed values
        values.forEach((v, i) => {
          if (v != null) _chartData.push({ point: cs.key, label: cs.label, ts: labels[i], value: v })
        })

        // Display: apply abs in flat mode for bar series
        const displayValues = values.map(v => {
          if (v == null) return null
          return (cs.abs && !this.signedMode) ? Math.abs(v) : v
        })

        // Trim trailing null/zero values from line series (API pads future slots with 0)
        let trimmed = displayValues
        if (cs.type === 'line') {
          let last = trimmed.length - 1
          while (last > 0 && (trimmed[last] == null || trimmed[last] === 0)) last--
          trimmed = trimmed.slice(0, last + 1)
        }
        const isArea = this.chartStyle === 'area'
        const isLine = cs.type === 'line'
        allDatasets.push({
          label: cs.label,
          data: trimmed.map(v => v ?? null),
          borderColor: cs.color,
          backgroundColor: isArea ? cs.color + '33' : isLine ? 'transparent' : cs.color + 'cc',
          borderWidth: isArea ? 2 : isLine ? 2 : 0,
          pointRadius: 0,
          fill: isArea && cs.yAxis !== 'right',
          tension: 0.4,
          type: isArea ? 'line' : (cs.type || 'bar'),
          yAxisID: cs.yAxis === 'right' ? 'yRight' : 'y',
          barPercentage: 1.0,
          categoryPercentage: 0.95,
          spanGaps: false,
        })
      }

      const hasRight = allDatasets.some(ds => ds.yAxisID === 'yRight')
      _analyticsChart.options.scales.yRight.display = hasRight
      // Stacking: flat mode = stacked, signed mode = grouped
      const stacked = !this.signedMode
      _analyticsChart.options.scales.x.stacked = stacked
      _analyticsChart.options.scales.y.stacked = stacked
      // For multi-day, show more x-axis ticks so date labels are visible
      const multiDayView = this.cloudSpan !== '1d'
      _analyticsChart.options.scales.x.ticks.maxTicksLimit = multiDayView ? 60 : 10
      // For today's 1d view: null out all values after "Now" index so future slots don't render
      const isToday = !multiDayView && this.cloudDate === _toDateStr(new Date())
      if (isToday) {
        const _n = new Date()
        const nowLabel = String(_n.getHours()).padStart(2,'0') + ':' + String(_n.getMinutes()).padStart(2,'0')
        const nowIdx = labels.reduce((best, lbl, i) => lbl <= nowLabel ? i : best, -1)
        if (nowIdx >= 0) {
          allDatasets.forEach(ds => {
            for (let i = nowIdx + 1; i < ds.data.length; i++) ds.data[i] = null
          })
        }
        _analyticsChart._nowLabel = nowLabel
      } else {
        _analyticsChart._nowLabel = null
      }
      _analyticsChart.data.labels = labels
      _analyticsChart.data.datasets = allDatasets
      _analyticsChart.update()

      // If labels are empty we got an unexpected format — expose structure for debugging
      if (!labels.length) {
        const top = Object.keys(raw || {}).slice(0, 8).join(', ')
        const firstVal = raw && Object.values(raw)[0]
        const shape = Array.isArray(firstVal)
          ? `array[${firstVal.length}] keys: ` + Object.keys(firstVal[0] || {}).slice(0,6).join(', ')
          : typeof firstVal === 'object' ? 'object: ' + JSON.stringify(firstVal).slice(0,80)
          : String(firstVal).slice(0,40)
        this.cloudError = `Unexpected format — top keys: [${top}] | first value: ${shape}`
        console.warn('[cloud stats] raw response:', json)
      }
    },

    // ── controls ──────────────────────────────────────────────────────────
    setChartStyle(s) {
      this.chartStyle = s
      localStorage.setItem('analyticsChartStyle', s)
      this._resetChart()
      this.load()
    },

    toggleSignedMode() {
      this.signedMode = !this.signedMode
      localStorage.setItem('analyticsSignedMode', this.signedMode ? '1' : '0')
      this._resetChart()
      this.load()
    },

    onRangeChange(val) {
      if (val === 'custom') {
        this.showCustomRange = true
      } else if (val === 'today' || val === 'yesterday') {
        this.localDayMode = val
        this.useCustomRange = false
        this.showCustomRange = false
        this.rangeOrCustom = val
        // auto-switch to bars for day views
        this.chartStyle = 'bars'
        localStorage.setItem('analyticsChartStyle', 'bars')
        this._resetChart()
        this.load()
      } else if (val === 'datepick') {
        this.localDayMode = 'date'
        this.useCustomRange = false
        this.showCustomRange = false
        this.rangeOrCustom = val
        this.chartStyle = 'bars'
        localStorage.setItem('analyticsChartStyle', 'bars')
        this._resetChart()
        this.load()
      } else {
        this.localDayMode = 'rolling'
        this.setRange(val)
      }
    },

    setRange(r) {
      this.range = r
      this.rangeOrCustom = r
      this.useCustomRange = false
      this.showCustomRange = false
      this._resetChart()
      this.load()
    },
    setBucket(b) {
      this.bucket = b
      this._resetChart()
      this.load()
    },
    applyCustomRange() {
      if (!this.customFrom || !this.customTo) return
      this.useCustomRange = true
      this._resetChart()
      this.load()
    },
    toggleSeries(key) {
      if (this.mode === 'cloud') {
        if (this.activeCloudSeries.includes(key))
          this.activeCloudSeries = this.activeCloudSeries.filter(k => k !== key)
        else
          this.activeCloudSeries.push(key)
        localStorage.setItem('analyticsCloudSeries', JSON.stringify(this.activeCloudSeries))
      } else {
        if (this.activeSeries.includes(key))
          this.activeSeries = this.activeSeries.filter(k => k !== key)
        else
          this.activeSeries.push(key)
        localStorage.setItem('analyticsLocalSeries', JSON.stringify(this.activeSeries))
      }
      this._resetChart()
      this.load()
    },
    isSeriesActive(key) {
      return this.mode === 'cloud'
        ? this.activeCloudSeries.includes(key)
        : this.activeSeries.includes(key)
    },

    // ── export ────────────────────────────────────────────────────────────
    exportJSON() {
      const blob = new Blob([JSON.stringify(_chartData, null, 2)], { type: 'application/json' })
      _download(blob, 'energipays-metrics.json')
    },
    exportCSV() {
      const header = 'timestamp,point,label,value\n'
      const rows = _chartData.map(r =>
        `${r.ts},${r.point},${r.label},${r.value}`
      ).join('\n')
      _download(new Blob([header + rows], { type: 'text/csv' }), 'energipays-metrics.csv')
    },

    fmt,
    _toDateStr,
    _prevDay,
    _nextDay,
  }
}

function _prevDay(dateStr) {
  const d = new Date(dateStr + 'T12:00:00')
  d.setDate(d.getDate() - 1)
  return _toDateStr(d)
}

function _nextDay(dateStr) {
  const d = new Date(dateStr + 'T12:00:00')
  d.setDate(d.getDate() + 1)
  return _toDateStr(d)
}

function _toDatetimeLocal(date) {
  const pad = n => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function _toDateStr(date) {
  const pad = n => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}`
}

function _download(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

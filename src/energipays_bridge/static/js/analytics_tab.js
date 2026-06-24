// Chart.js stored in closure — NOT Alpine reactive (avoids Proxy recursion)
let _analyticsChart = null

function analyticsTab() {
  return {
    ranges: ['1h','2h','4h','6h','12h','24h','7d','30d'],
    buckets: ['1m','5m','15m','1h'],
    series: [
      { key: 'phasePower',  label: 'Grid (kW)',      color: '#f59e0b' },
      { key: 'today.IEct',  label: 'Import (kWh)',   color: '#ef4444' },
      { key: 'today.EEct',  label: 'Export (kWh)',   color: '#22c55e' },
      { key: 'today.DE_h',  label: 'Diverted (kWh)', color: '#3b82f6' },
    ],
    range: '24h',
    bucket: '5m',
    activeSeries: ['phasePower'],
    loading: false,

    async init() {
      await this.$nextTick()
      this._buildChart()
      await this.load()
    },

    _buildChart() {
      const canvas = document.getElementById('analyticsChart')
      if (!canvas || _analyticsChart) return
      _analyticsChart = new Chart(canvas.getContext('2d'), {
        type: 'line',
        data: { labels: [], datasets: [] },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: { duration: 200 },
          plugins: { legend: { labels: { color: '#94a3b8', boxWidth: 12 } } },
          scales: {
            x: {
              ticks: { color: '#64748b', maxTicksLimit: 8, font: { size: 10 } },
              grid: { color: '#1e293b' },
            },
            y: {
              ticks: { color: '#64748b', font: { size: 10 } },
              grid: { color: '#1e293b' },
            },
          },
        },
      })
    },

    async load() {
      if (!_analyticsChart) return
      this.loading = true
      try {
        const datasets = []
        for (const key of this.activeSeries) {
          const s = this.series.find(x => x.key === key)
          if (!s) continue
          const r = await fetch(`/api/metrics/history?point=${key}&range=${this.range}&bucket=${this.bucket}`)
          if (!r.ok) continue
          const d = await r.json()
          datasets.push({
            label: s.label,
            data: d.data.map(p => p.value),
            borderColor: s.color,
            backgroundColor: s.color + '22',
            borderWidth: 1.5,
            pointRadius: 0,
            fill: false,
            tension: 0.3,
          })
          // Use timestamps from first dataset for labels
          if (!_analyticsChart.data.labels.length) {
            _analyticsChart.data.labels = d.data.map(p =>
              new Date(p.ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
            )
          }
        }
        _analyticsChart.data.datasets = datasets
        _analyticsChart.update()
      } finally {
        this.loading = false
      }
    },

    setRange(r) { this.range = r; _analyticsChart && (_analyticsChart.data.labels = []); this.load() },
    setBucket(b) { this.bucket = b; _analyticsChart && (_analyticsChart.data.labels = []); this.load() },
    toggleSeries(key) {
      if (this.activeSeries.includes(key)) {
        this.activeSeries = this.activeSeries.filter(k => k !== key)
      } else {
        this.activeSeries.push(key)
      }
      _analyticsChart && (_analyticsChart.data.labels = [])
      this.load()
    },

    fmt,
  }
}

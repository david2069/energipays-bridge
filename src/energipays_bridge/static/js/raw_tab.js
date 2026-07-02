function rawTab() {
  return {
    mode: 'tree',   // 'tree' | 'terminal'

    // ── Command definitions ────────────────────────────────────────────────
    commands: [
      // ── Read ──
      { id: 'points',         label: 'Latest points (live)',  desc: 'All polled data points from the last fetch',
        endpoint: '/api/points/latest',   method: 'GET' },
      { id: 'health',         label: 'health',               desc: 'Poller status, uptime, last poll time',
        endpoint: '/api/health',          method: 'GET' },
      { id: 'me',             label: 'me',                   desc: 'Authenticated user profile from cloud',
        endpoint: '/api/device/list',     method: 'GET' },
      { id: 'devices',        label: 'devices',              desc: 'All registered devices on this account',
        endpoint: '/api/devices',         method: 'GET' },
      { id: 'device-status',  label: 'device-status',        desc: 'Live device status from cloud API',
        endpoint: '/api/device/status',   method: 'GET' },
      { id: 'device-profile', label: 'device (full object)', desc: 'Full device object including config and state',
        endpoint: '/api/device/profile',  method: 'GET' },
      { id: 'rules',          label: 'rules',                desc: 'All automation rules for this device',
        endpoint: '/api/rules',           method: 'GET' },
      { id: 'logs',           label: 'logs',                 desc: 'Recent bridge log entries (ring buffer)',
        endpoint: '/api/logs',            method: 'GET' },
      { id: 'cloud-stats',    label: 'stats (cloud)',        desc: 'Cloud energy chart data for a date range',
        endpoint: '/api/cloud/stats',     method: 'GET',
        params: [
          { key: 'date_from', label: 'Date from', type: 'date', required: false },
          { key: 'date_to',   label: 'Date to',   type: 'date', required: false },
          { key: 'data_type', label: 'Data type', type: 'select', options: ['power','energy'], required: false },
          { key: 'phase',     label: 'Phase',     type: 'select', options: ['sum','l1','l2','l3'], required: false },
        ]},
      { id: 'metrics-history', label: 'metrics history (local)', desc: 'SQLite metrics for a point over a time range',
        endpoint: '/api/metrics/history', method: 'GET',
        params: [
          { key: 'point',  label: 'Point key', type: 'text',   placeholder: 'e.g. phasePower', required: false },
          { key: 'range',  label: 'Range',     type: 'select', options: ['1h','6h','24h','7d','30d'], required: false },
          { key: 'bucket', label: 'Bucket',    type: 'select', options: ['1m','5m','15m','1h'], required: false },
        ]},
      { id: 'db-stats',       label: 'db-stats',             desc: 'SQLite database size and row counts',
        endpoint: '/api/admin/db-stats',  method: 'GET' },
      // ── Write ──
      { id: 'boost',          label: 'boost',                desc: 'Trigger a timed boost on the immersion heater',
        endpoint: '/api/boost',           method: 'POST', danger: true,
        params: [
          { key: 'period', label: 'Duration', type: 'select', options: ['1','2','3'],
            optionLabels: ['1 hour','2 hours','3 hours'], required: true },
        ]},
      { id: 'boost-cancel',   label: 'cancel',               desc: 'Cancel a running boost',
        endpoint: '/api/boost/cancel',    method: 'POST', danger: true },
      { id: 'device-switch',  label: 'device-on / device-off', desc: 'Enable or disable the PowerDiverter (PD)',
        endpoint: '/api/device/switch',   method: 'POST', danger: true,
        params: [
          { key: 'status', label: 'State', type: 'select', options: ['1','0'],
            optionLabels: ['On (enable)','Off (disable)'], required: true },
        ]},
      { id: 'heater-switch',  label: 'heater-on / heater-off', desc: 'Turn the immersion heater on or off',
        endpoint: '/api/device/set',      method: 'POST', danger: true,
        params: [
          { key: 'field', label: 'Field',  type: 'hidden', value: 'heaterStatus' },
          { key: 'value', label: 'State',  type: 'select', options: ['1','0'],
            optionLabels: ['On','Off'], required: true },
        ]},
    ],

    selectedCmd: 'points',
    paramValues: {},   // { paramKey: value }
    cmdOutput: null,
    cmdLoading: false,
    cmdError: '',

    get currentCmd() {
      return this.commands.find(c => c.id === this.selectedCmd) || this.commands[0]
    },

    get selectedEndpoint() {
      return this.currentCmd.endpoint || ''
    },

    onCmdChange() {
      this.paramValues = {}
      // Pre-fill hidden params
      for (const p of (this.currentCmd.params || [])) {
        if (p.type === 'hidden') this.paramValues[p.key] = p.value
        else if (p.options) this.paramValues[p.key] = p.options[0]
      }
      this.cmdOutput = null
      this.cmdError = ''
    },

    async runCommand() {
      const cmd = this.currentCmd
      if (!cmd.endpoint) return
      this.cmdLoading = true
      this.cmdError = ''
      try {
        let url = cmd.endpoint
        let opts = { method: cmd.method || 'GET' }

        if (cmd.method === 'GET') {
          const qs = Object.entries(this.paramValues)
            .filter(([, v]) => v !== '' && v != null)
            .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
            .join('&')
          if (qs) url += '?' + qs
        } else {
          opts.headers = { 'Content-Type': 'application/json' }
          opts.body = JSON.stringify(
            Object.fromEntries(
              Object.entries(this.paramValues).filter(([, v]) => v !== '' && v != null)
            )
          )
        }

        const r = await fetch(url, opts)
        this.cmdOutput = await r.json()
      } catch (e) {
        this.cmdError = String(e)
        this.cmdOutput = null
      } finally {
        this.cmdLoading = false
      }
    },

    get cmdOutputStr() {
      if (this.cmdOutput === null) return ''
      return JSON.stringify(this.cmdOutput, null, 2)
    },

    exportTreeJSON() {
      _downloadJson(Alpine.store('app').points, 'energipays-raw.json')
    },

    exportCmdJSON() {
      if (this.cmdOutput === null) return
      _downloadJson(this.cmdOutput, `energipays-${this.selectedCmd}.json`)
    },
  }
}

function _downloadJson(obj, filename) {
  const blob = new Blob([JSON.stringify(obj, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url; a.download = filename; a.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

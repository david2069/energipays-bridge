function rawTab() {
  return {
    mode: 'tree',   // 'tree' | 'terminal'

    // Terminal / command runner state
    commands: [
      { id: 'points',        label: 'Latest points (live)',   endpoint: '/api/points/latest' },
      { id: 'health',        label: 'Health / poller status', endpoint: '/api/health' },
      { id: 'devices',       label: 'devices',                endpoint: '/api/devices' },
      { id: 'device-status', label: 'device-status',          endpoint: '/api/device/status' },
      { id: 'rules',         label: 'rules',                  endpoint: '/api/rules' },
      { id: 'device-profile', label: 'device (full object)',  endpoint: '/api/device/profile' },
    ],
    selectedCmd: 'points',
    cmdOutput: null,
    cmdLoading: false,
    cmdError: '',

    get selectedEndpoint() {
      return (this.commands.find(c => c.id === this.selectedCmd) || {}).endpoint || ''
    },

    async runCommand() {
      if (!this.selectedEndpoint) return
      this.cmdLoading = true
      this.cmdError = ''
      try {
        const r = await fetch(this.selectedEndpoint)
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
      _downloadJson($store.app.points, 'energipays-raw.json')
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

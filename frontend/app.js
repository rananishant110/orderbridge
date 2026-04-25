/* OrderBridge — reconciliation desk, Alpine controller.
   Single-file state + methods, driven from x-data="orderBridge()". */

// Global 401 interceptor — redirect to login on any unauthenticated response.
(function () {
  const _fetch = window.fetch.bind(window);
  window.fetch = async function (...args) {
    const r = await _fetch(...args);
    if (r.status === 401) {
      const url = typeof args[0] === 'string' ? args[0] : args[0]?.url ?? '';
      if (!url.includes('/api/login') && !url.includes('/api/logout')) {
        window.location.href = '/login.html';
      }
    }
    return r;
  };
})();

function orderBridge() {
  return {
    // ────────── UI state ──────────
    theme: 'dark',
    cmdkOpen: false,
    refreshModalOpen: false,
    historyOpen: false,
    dragActive: false,
    uploadStatus: '',
    applying: false,
    refreshing: false,
    refreshStatus: '',
    applyResult: null,
    toasts: [],
    _toastSeq: 0,

    // catalog
    catalogStatus: null,
    onestopFile: null,
    gmFile: null,
    diff: null,

    // run
    runId: null,
    filename: null,
    lines: [],          // enriched OneStop rows (with UI state)
    gmSheets: [],       // [{ sheet, items: [] }]
    activeSheet: null,

    // interaction
    selectedRowIndex: null,
    selectedGmKey: null,
    filter: 'all',      // all | auto | review | unmatched
    query: '',
    gmQuery: '',

    // manual GM search / command palette
    manualSearchRow: null,
    cmdQuery: '',
    cmdResults: [],
    cmdSearching: false,

    // history
    runHistory: [],

    // ────────── lifecycle ──────────
    async init() {
      this.theme = localStorage.getItem('ob.theme') || 'dark';
      this.applyTheme();
      const auth = await fetch('/api/me');
      if (!auth.ok) return; // 401 interceptor handles redirect to /login.html
      await Promise.all([this.loadCatalogStatus(), this.loadHistory()]);
      this.bindShortcuts();
    },

    applyTheme() {
      document.documentElement.classList.toggle('dark', this.theme === 'dark');
    },
    toggleTheme() {
      this.theme = this.theme === 'dark' ? 'light' : 'dark';
      localStorage.setItem('ob.theme', this.theme);
      this.applyTheme();
    },

    bindShortcuts() {
      window.addEventListener('keydown', (e) => {
        const inField = e.target.matches('input, textarea, select');
        // ⌘K / Ctrl+K — command palette
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
          e.preventDefault();
          this.openCmdk();
          return;
        }
        // ⌘⏎ — apply
        if ((e.metaKey || e.ctrlKey) && e.key === 'Enter' && this.runId) {
          e.preventDefault(); this.applyRun(); return;
        }
        if (inField) return;
        // j/k navigation through filtered lines
        if (e.key === 'j' || e.key === 'k') { e.preventDefault(); this.moveSelection(e.key === 'j' ? 1 : -1); }
        if (e.key === 'r' && !this.refreshModalOpen) { this.refreshModalOpen = true; }
        if (e.key === 'h') { this.historyOpen = !this.historyOpen; }
        if (e.key === 't') { this.toggleTheme(); }
        if (e.key === '/') { e.preventDefault(); document.querySelector('input[placeholder*="Filter lines"]')?.focus(); }
      });
    },

    openCmdk() {
      this.cmdkOpen = true; this.cmdQuery = ''; this.cmdResults = [];
      this.$nextTick(() => this.$refs.cmdInput?.focus());
    },

    // ────────── Catalog status / history ──────────
    async loadCatalogStatus() {
      try {
        const r = await fetch('/api/catalogs/status');
        if (r.ok) this.catalogStatus = await r.json();
      } catch {}
    },
    async loadHistory() {
      try {
        const r = await fetch('/api/orders/history');
        if (r.ok) this.runHistory = await r.json();
      } catch {}
    },

    // ────────── Upload / new run ──────────
    handleFileInput(e) { const f = e.target.files[0]; if (f) this.startUpload(f); },
    handleDrop(e) {
      this.dragActive = false;
      const f = e.dataTransfer?.files?.[0];
      if (f) this.startUpload(f);
    },
    async startUpload(file) {
      if (!file.name.endsWith('.xlsx')) {
        this.toast('err', 'Only .xlsx files are accepted.');
        return;
      }
      this.uploadStatus = `Uploading ${file.name} …`;
      try {
        const form = new FormData(); form.append('file', file);
        const res = await fetch('/api/orders/upload', { method: 'POST', body: form });
        if (!res.ok) { this.uploadStatus = `Upload failed (${res.status}): ${await res.text()}`; return; }
        const run = await res.json();
        this.filename = file.name;
        this.runId = run.run_id;
        this.lines = this.enrichLines(run);
        this.uploadStatus = '';
        await this.loadGm();
        this.toast('ok', `Matched ${run.auto.length} · review ${run.review.length} · unmatched ${run.unmatched.length}`);
      } catch (err) {
        this.uploadStatus = 'Network error: ' + err.message;
      }
    },

    enrichLines(run) {
      const all = [
        ...run.auto.map(l => ({ ...l, choice: 'accept', remember: true })),
        ...run.review.map(l => ({
          ...l,
          choice: 'accept',
          remember: true,
          pickedKey: l.picked ? `${l.picked.item_no}::${l.picked.sheet}` : null,
        })),
        ...run.unmatched.map(l => ({ ...l, choice: 'onestop_only', remember: true, picked: null })),
      ];
      all.sort((a, b) => a.row_index - b.row_index);
      return all;
    },

    async loadGm() {
      try {
        const r = await fetch('/api/catalogs/gm');
        if (!r.ok) { this.toast('err', 'Could not load GM catalog.'); return; }
        this.gmSheets = await r.json();
        if (!this.activeSheet && this.gmSheets.length) {
          // Prefer the sheet with the most filled items as the landing tab.
          const filledBySheet = {};
          for (const l of this.lines) {
            if (l.picked) filledBySheet[l.picked.sheet] = (filledBySheet[l.picked.sheet] || 0) + 1;
          }
          const ranked = Object.entries(filledBySheet).sort((a, b) => b[1] - a[1]);
          this.activeSheet = ranked[0]?.[0] || this.gmSheets[0].sheet;
        }
      } catch {}
    },

    resetRun() {
      if (!confirm('Discard this run and start over?')) return;
      this.runId = null;
      this.filename = null;
      this.lines = [];
      this.applyResult = null;
      this.selectedRowIndex = null;
      this.selectedGmKey = null;
      this.filter = 'all';
      this.query = '';
      this.gmQuery = '';
    },

    // ────────── Selection linking ──────────
    selectLine(rowIndex) {
      this.selectedRowIndex = rowIndex;
      const line = this.lines.find(l => l.row_index === rowIndex);
      if (line?.picked) {
        this.activeSheet = line.picked.sheet;
        this.selectedGmKey = this.gmKey(line.picked);
        this.$nextTick(() => {
          const el = document.getElementById(`gm-${line.picked.item_no}-${line.picked.sheet}`);
          el?.scrollIntoView({ block: 'center', behavior: 'smooth' });
        });
      }
    },

    focusGmItem(item) {
      this.selectedGmKey = this.gmKey(item);
      // Reverse-link: find the OneStop line feeding this GM item.
      const line = this.lines.find(
        l => l.picked && l.picked.item_no === item.item_no && l.picked.sheet === item.sheet
      );
      if (line) {
        this.selectedRowIndex = line.row_index;
        this.$nextTick(() => {
          document.getElementById(`os-${line.row_index}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
        });
      }
    },

    moveSelection(dir) {
      const visible = this.filteredLines;
      if (!visible.length) return;
      const currIdx = visible.findIndex(l => l.row_index === this.selectedRowIndex);
      const nextIdx = currIdx === -1 ? 0 : Math.max(0, Math.min(visible.length - 1, currIdx + dir));
      const next = visible[nextIdx];
      this.selectLine(next.row_index);
      this.$nextTick(() => {
        document.getElementById(`os-${next.row_index}`)?.scrollIntoView({ block: 'center', behavior: 'smooth' });
      });
    },

    // ────────── Edits to a line ──────────
    updateQty(rowIndex, val) {
      const n = Math.max(0, Math.floor(Number(val) || 0));
      const line = this.lines.find(l => l.row_index === rowIndex);
      if (line) line.qty = n;
    },

    pickCandidate(rowIndex, cand) {
      const line = this.lines.find(l => l.row_index === rowIndex);
      if (!line) return;
      line.picked = { ...cand };
      line.pickedKey = `${cand.item_no}::${cand.sheet}`;
      line.choice = 'accept';
      this.activeSheet = cand.sheet;
      this.selectedGmKey = this.gmKey(cand);
    },

    clearManualPick(rowIndex) {
      const line = this.lines.find(l => l.row_index === rowIndex);
      if (!line) return;
      line.picked = null;
      line.pickedKey = null;
      line.choice = 'onestop_only';
    },

    bulkAcceptAbove(threshold) {
      let changed = 0;
      for (const line of this.lines) {
        if (line.bucket !== 'review') continue;
        if (line.picked && line.picked.score >= threshold) {
          line.choice = 'accept';
          changed++;
        }
      }
      this.toast('info', `${changed} review line(s) marked accept.`);
    },

    // ────────── Manual search (from cmdk or unmatched line) ──────────
    openManualSearch(rowIndex) {
      this.manualSearchRow = rowIndex;
      this.cmdQuery = '';
      this.cmdResults = [];
      const line = this.lines.find(l => l.row_index === rowIndex);
      if (line) this.cmdQuery = line.onestop_desc.split(' ').slice(0, 3).join(' ');
      this.$nextTick(() => { this.$refs.cmdInput?.focus(); this.runCmdSearch(); });
    },

    async runCmdSearch() {
      const q = this.cmdQuery.trim();
      if (!q) { this.cmdResults = []; return; }
      this.cmdSearching = true;
      try {
        const r = await fetch(`/api/catalogs/search?q=${encodeURIComponent(q)}&limit=12`);
        if (r.ok) this.cmdResults = await r.json();
      } catch {}
      this.cmdSearching = false;
    },

    applyCmdResult(r) {
      if (this.manualSearchRow !== null) {
        const line = this.lines.find(l => l.row_index === this.manualSearchRow);
        if (line) {
          line.picked = { item_no: r.item_no, sheet: r.sheet, description: r.description, price: r.price, score: 1.0 };
          line.pickedKey = `${r.item_no}::${r.sheet}`;
          line.choice = 'accept';
          // If it was unmatched, promote to review-resolved so apply() writes it.
          if (line.bucket === 'unmatched') line.bucket = 'review';
          this.activeSheet = r.sheet;
          this.selectedGmKey = this.gmKey(r);
          this.toast('ok', `Assigned #${r.item_no} to line #${String(line.row_index).padStart(3, '0')}.`);
        }
        this.manualSearchRow = null;
      }
      this.cmdkOpen = false;
      this.cmdQuery = '';
      this.cmdResults = [];
    },

    // ────────── Apply the run ──────────
    async applyRun() {
      if (!this.runId || this.applying) return;
      this.applying = true; this.applyResult = null;
      const resolutions = [];
      for (const line of this.lines) {
        if (line.bucket === 'auto') continue; // server handles auto
        if (line.bucket === 'review' || line.bucket === 'unmatched') {
          if (line.choice === 'accept' && line.picked) {
            resolutions.push({
              row_index: line.row_index,
              choice: 'pick',
              gm_item_no: line.picked.item_no,
              gm_sheet: line.picked.sheet,
              remember: !!line.remember,
            });
          } else {
            resolutions.push({
              row_index: line.row_index,
              choice: 'onestop_only',
              remember: !!line.remember,
            });
          }
        }
      }
      try {
        const res = await fetch('/api/orders/apply', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ run_id: this.runId, resolutions }),
        });
        if (!res.ok) {
          this.toast('err', `Apply failed: ${res.status} ${await res.text()}`);
        } else {
          this.applyResult = await res.json();
          this.toast('ok', `Wrote ${this.applyResult.lines_written} line(s) to GM.`);
          this.loadHistory();
        }
      } catch (err) {
        this.toast('err', 'Network error: ' + err.message);
      } finally {
        this.applying = false;
      }
    },

    // ────────── Catalog refresh ──────────
    async submitRefresh() {
      if (!this.onestopFile || !this.gmFile) return;
      this.refreshing = true; this.refreshStatus = 'Uploading and indexing…';
      try {
        const form = new FormData();
        form.append('onestop', this.onestopFile);
        form.append('gm', this.gmFile);
        const res = await fetch('/api/catalogs/refresh', { method: 'POST', body: form });
        if (!res.ok) {
          this.refreshStatus = `Failed: ${res.status} ${await res.text()}`;
        } else {
          this.diff = await res.json();
          this.refreshStatus = 'Done.';
          await this.loadCatalogStatus();
          this.toast('ok', 'Catalogs refreshed.');
        }
      } catch (err) {
        this.refreshStatus = 'Network error: ' + err.message;
      } finally {
        this.refreshing = false;
      }
    },

    // ────────── Derived getters ──────────
    get counts() {
      const c = { total: this.lines.length, auto: 0, review: 0, unmatched: 0 };
      for (const l of this.lines) {
        if (l.bucket === 'auto') c.auto++;
        else if (l.bucket === 'review') c.review++;
        else c.unmatched++;
      }
      return c;
    },

    get coverage() {
      const c = this.counts;
      if (!c.total) return 0;
      let covered = c.auto;
      for (const l of this.lines) {
        if (l.bucket === 'review' && l.choice === 'accept' && l.picked) covered++;
      }
      return covered / c.total;
    },

    get gmWrites() {
      // Map of gmKey() -> { qty, onestop_desc, row_index } for quick right-pane lookup.
      const map = {};
      for (const l of this.lines) {
        if (!l.picked) continue;
        if (l.bucket === 'auto' ||
            (l.bucket === 'review' && l.choice === 'accept') ||
            (l.bucket === 'unmatched' && l.choice === 'accept')) {
          map[this.gmKey(l.picked)] = {
            qty: l.qty,
            onestop_desc: l.onestop_desc,
            row_index: l.row_index,
          };
        }
      }
      return map;
    },

    get totalGmItems() {
      return this.gmSheets.reduce((n, s) => n + s.items.length, 0);
    },

    get filledGmCount() { return Object.keys(this.gmWrites).length; },

    get filteredLines() {
      const q = this.query.trim().toLowerCase();
      return this.lines.filter(l => {
        if (this.filter !== 'all' && l.bucket !== this.filter) return false;
        if (q && !l.onestop_desc.toLowerCase().includes(q)) return false;
        return true;
      });
    },

    get activeSheetData() {
      return this.gmSheets.find(s => s.sheet === this.activeSheet) || null;
    },

    get filteredGmItems() {
      if (!this.activeSheetData) return [];
      const q = this.gmQuery.trim().toLowerCase();
      if (!q) return this.activeSheetData.items;
      return this.activeSheetData.items.filter(
        i => i.description.toLowerCase().includes(q) || String(i.item_no).includes(q)
      );
    },

    sheetFilledCount(sheetName) {
      let n = 0;
      for (const k of Object.keys(this.gmWrites)) {
        if (k.endsWith(`::${sheetName}`)) n++;
      }
      return n;
    },

    // ────────── Helpers ──────────
    gmKey(itemOrPicked) {
      const sheet = itemOrPicked.sheet || this.activeSheet;
      return `${itemOrPicked.item_no}::${sheet}`;
    },

    toast(kind, msg, ms = 3800) {
      const id = ++this._toastSeq;
      this.toasts.push({ id, kind, msg });
      setTimeout(() => { this.toasts = this.toasts.filter(t => t.id !== id); }, ms);
    },

    fmtAge(iso) {
      if (!iso) return '—';
      const t = new Date(iso + (iso.endsWith('Z') ? '' : 'Z'));
      if (isNaN(t)) return '—';
      const diff = Date.now() - t.getTime();
      const mins = Math.floor(diff / 60000);
      if (mins < 1) return 'just now';
      if (mins < 60) return `${mins}m ago`;
      const hrs = Math.floor(mins / 60);
      if (hrs < 24) return `${hrs}h ago`;
      const days = Math.floor(hrs / 24);
      if (days < 30) return `${days}d ago`;
      return t.toLocaleDateString();
    },

    fmtWhen(iso) {
      if (!iso) return '—';
      const t = new Date(iso + (iso.endsWith('Z') ? '' : 'Z'));
      if (isNaN(t)) return iso;
      return t.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    },

    todayStamp() {
      const d = new Date();
      return d.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' }).toUpperCase();
    },

    async logout() {
      await fetch('/api/logout', { method: 'POST' });
      window.location.href = '/login';
    },
  };
}

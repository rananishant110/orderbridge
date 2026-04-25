# OrderBridge — Features

## Domain: Order Processing (Hot Path)

### Upload a Filled OneStop Order
**Code:** `routes/orders.py` → `POST /api/orders/upload`; `services/matching.py`; `services/excel_reader.py`

User drops or selects a filled OneStop `.xlsx` file. The server reads every row with qty > 0 from the `Report` sheet, matches each against the active GM catalog, and buckets lines into `auto`, `review`, or `unmatched`. The resulting run is staged in memory (`_RUN_STAGING`) and returned as JSON.

Frontend entry point: `app.js` `startUpload()`.

---

### Three-Bucket Matching
**Code:** `services/matching.py` `match_row()` / `match_all()`; `services/normalize.py`

Every OneStop line with qty > 0 is classified:

| Bucket | Condition | UI color |
|--------|-----------|----------|
| `auto` | Learned mapping, exact match, or fuzzy score ≥ 0.92 with compatible pack size | jade |
| `review` | Fuzzy score 0.75–0.92, or pack-size mismatch | amber/saffron |
| `unmatched` | Fuzzy score < 0.75, or learned as OneStop-exclusive | rose |

---

### Line Review — Accept / Pick / OneStop-Only
**Code:** `routes/orders.py` `apply()` via `POST /api/orders/apply`; `app.js` `applyRun()`

For each `review` line, the user can:
- **Accept** the top fuzzy candidate.
- **Pick** a different candidate from the chip strip.
- **Search catalog** to open the command palette and assign any GM item.
- Mark as **OneStop-only** (item not on GM; will not be written to the GM output).

For `unmatched` lines, the user can use catalog search to manually assign a GM item before applying.

---

### Quantity Edit
**Code:** `app.js` `updateQty()`; inline `<input type="number">` in `index.html`

Each line card has an editable quantity field. Changes are reflected immediately in the in-memory `lines` array and are sent in the apply payload.

---

### Apply and Download
**Code:** `routes/orders.py` `apply()`; `services/excel_writer.py` `write_quantities()`

After review, the user clicks "Apply & download" (or presses ⌘↵). The server:
1. Collects all auto writes and user resolutions.
2. Copies the active GM template.
3. Writes ORDER quantities to the correct cells (column C or H depending on left/right block), verified against item# before writing.
4. Saves the output file as `gm_order_<timestamp>_<run_id>.xlsx` in `storage/runs/`.
5. Persists confirmed mappings to the `mapping` table (if `remember` was checked).
6. Records the run in `order_run`.
7. Returns a download URL.

---

### Bulk Accept Above Threshold
**Code:** `app.js` `bulkAcceptAbove(threshold)`

Toolbar button "Accept ≥85%" sets `choice = 'accept'` on all `review` lines whose top candidate score is ≥ the given threshold. Reduces manual effort for near-confident matches. Also accessible via the ⌘K command palette.

---

### Coverage Bar
**Code:** `app.js` `get coverage()`

Real-time progress indicator: `(auto + review lines marked accept with a pick) / total lines`. Updates as the user resolves review items.

---

## Domain: Catalog Management (Weekly)

### Refresh Catalogs
**Code:** `routes/catalogs.py` `refresh()` via `POST /api/catalogs/refresh`; `app.js` `submitRefresh()`

Ops lead uploads both the blank OneStop master template and the blank GM master template. The server:
1. Saves both as `storage/templates/onestop_active.xlsx` and `gm_active.xlsx`.
2. Re-indexes `onestop_template` and `gm_catalog` tables.
3. Returns a `CatalogDiff`:
   - `new_onestop` — descriptions added to the OneStop master.
   - `removed_onestop` — descriptions removed from the OneStop master.
   - `changed_gm_match` — descriptions with a learned mapping whose GM item# is no longer in the new GM catalog.
   - `price_changed` — (not yet populated in v1).

Triggered via the Refresh Catalogs modal (toolbar button or `r` shortcut).

---

### Catalog Status Pills
**Code:** `routes/catalogs.py` `status()` via `GET /api/catalogs/status`; `app.js` `loadCatalogStatus()`

The header bar shows green/red dots for each template, plus their age (e.g. "3d ago"). Loaded on app init and after a catalog refresh.

---

### GM Catalog Right-Hand Pane
**Code:** `routes/catalogs.py` `gm_listing()` via `GET /api/catalogs/gm`; `app.js` `loadGm()`

The right half of the run workspace shows the full GM catalog grouped by sheet, with sheet tabs. Filled items (those with a queued write) are highlighted amber. Clicking a GM row reverse-links to the corresponding OneStop line on the left.

---

### Catalog Search (Command Palette)
**Code:** `routes/catalogs.py` `search()` via `GET /api/catalogs/search?q=`; `app.js` `runCmdSearch()`

Real-time fuzzy search over the GM catalog. Used in two contexts:
1. **⌘K command palette** — general purpose.
2. **Manual assign from a review or unmatched line** — pre-fills the query with the first three words of the OneStop description.

Search uses `rapidfuzz.fuzz.WRatio` against normalized GM descriptions.

---

## Domain: History & Navigation

### Run History
**Code:** `routes/orders.py` `history()` via `GET /api/orders/history`; `app.js` `loadHistory()`

Shows the last 25 runs from `order_run` with stats (auto/reviewed/unmatched line counts) and a download link if the output file still exists. Displayed in:
- The "Recent runs" sidebar on the upload screen.
- The "Run history" drawer (keyboard shortcut `h`).

---

### Bidirectional Line ↔ GM Linking
**Code:** `app.js` `selectLine()` / `focusGmItem()`

Clicking a OneStop line card navigates the GM pane to the sheet and highlights the matched GM item. Clicking a GM item reverse-navigates to the OneStop line. Both sides scroll to center the selection.

---

### Keyboard Navigation
**Code:** `app.js` `bindShortcuts()` / `moveSelection()`

`j` / `k` moves the selection through the filtered OneStop line list and scrolls both panes to keep the selected item visible.

---

### Filter + Search Lines
**Code:** `app.js` `get filteredLines()`; filter pill buttons and search input in `index.html`

Lines can be filtered by bucket (All / Review / Unmatched / Auto) and by a free-text substring match on the OneStop description. Filters are combined.

---

### Dark / Light Theme
**Code:** `app.js` `toggleTheme()` / `applyTheme()`; `styles.css` `html:not(.dark)` block

Preference persisted in `localStorage` (`ob.theme`). Light mode inverts the ink palette to a warm paper variant.

---

## Domain: System / Infrastructure

### Learned Mappings
**Code:** `db.py` `mapping` table; `routes/orders.py` `apply()` (writes); `routes/orders.py` `_load_learned()` (reads)

When a user resolves a review or unmatched line and leaves "remember" checked, the confirmed pairing (`onestop_desc_normalized → gm_item_no + gm_sheet`) is upserted into the `mapping` table with confidence 1.0. On the next upload, learned mappings always win over fuzzy matching. If the user marks a line as OneStop-only with remember, the mapping is stored with `gm_item_no = NULL`, permanently suppressing future GM match attempts for that description.

---

### Row-Drift Guard
**Code:** `services/excel_writer.py` `write_quantities()`

Before writing a quantity, the writer verifies that the cell in the item# column (A for left block, F for right block) at the target row actually contains the expected item#. If the catalog has drifted out of sync, the write raises `ValueError` rather than silently filling the wrong row.

---

### Path-Traversal Guard
**Code:** `routes/orders.py` `download()`

The download endpoint rejects any `filename` parameter containing `/`, `\\`, or `..` before constructing the file path.

---

## Adding a New Feature — Template

To document a new feature here, add a section under the appropriate domain (or create a new domain) with:

```markdown
### Feature Name
**Code:** `path/to/module.py` `function_or_class` via `HTTP METHOD /api/route` (if applicable)

What the feature does, when it's triggered, and what it produces. One short paragraph.
```

Include:
- Backend: the route handler + service function.
- Frontend: the Alpine method or computed property, if applicable.
- Database: any table reads/writes.
- Any edge cases or constraints the next developer should know.

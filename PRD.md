# OrderBridge — Product Requirements Document

**Status:** Draft v1
**Owner:** OneStop Distribution (internal tool)
**Last updated:** 2026-04-23

---

## 1. Problem

OneStop Distribution receives customer orders as filled-in copies of its own Excel catalog (the *OneStop order form*). OneStop then re-keys a large portion of those orders into GrainMarket's (GM) Excel order form, because GM is OneStop's primary upstream supplier. The two forms have completely different structures:

- **OneStop form:** one flat sheet, product identified **only by description text** (no SKUs), customer writes case counts in column A.
- **GM form:** nine sheets, products identified by **numeric item#**, two-column side-by-side grid per sheet, ORDER column is where quantities go.

Re-keying is manual, error-prone, and slow. Staff eyeball each OneStop line, locate the equivalent GM item on the correct sheet, and type in the quantity — 200+ line orders take the better part of an hour, with typos, wrong-SKU picks, and missed items baked in.

The catalogs also **change every week** (new items, discontinued items, new pack sizes, price updates), so any hand-maintained crosswalk spreadsheet goes stale almost immediately.

## 2. Solution in one sentence

A small web app that reads a filled OneStop order form, matches each line to the correct GM item# using a learned description-to-SKU mapping, writes the quantities into GM's `ORDER` column on the correct sheet, and returns the completed GM Excel file with all original formatting intact.

## 3. Users

- **Primary:** OneStop back-office staff who process customer orders (single-tenant, ~1–5 users total).
- **Secondary:** OneStop operations lead, who refreshes the master catalogs each week.

Authentication is a single shared username / password (HTTP Basic). No per-user accounts.

## 4. Goals

1. Turn a 30–60 minute manual re-key into a ~30 second upload → review → download flow.
2. Eliminate SKU typos and wrong-sheet entries.
3. Make the weekly catalog refresh a ~10 minute review task, not a re-mapping marathon.
4. Preserve GM's Excel template **byte-for-byte** — formatting, colors, merged cells, layout. GM must not receive a visually altered file.
5. Never silently drop a line: every OneStop row with qty > 0 either lands on GM or is explicitly flagged as "fulfilled by OneStop."

## 5. Non-goals (v1)

- Placing orders electronically with GM (submitting via API, email automation, etc.) — staff still download the file and send it the existing way.
- Multi-tenant / multi-customer isolation — single organization, single login.
- Auto-ingestion from Gmail or WhatsApp — staff upload files manually. (Hook for later.)
- Inventory / pricing analytics, order history dashboards, margin calculations.
- Mobile UI — desktop browser only.
- Handling GM availability (`NA` items get quantity written anyway; GM decides availability on their side).

## 6. Key user flows

### 6.1 Process an order (daily — the hot path)

1. Staff receive a filled OneStop order form by email / WhatsApp and save it as `.xlsx`.
2. Open OrderBridge in a browser → "Process Order".
3. Upload the filled OneStop file.
4. App returns three buckets:
   - **Auto-matched** — confident matches, nothing to do.
   - **Needs review** — fuzzy matches in the 0.75–0.92 confidence band; staff accept, pick a different GM item, or mark as "fulfilled by OneStop."
   - **No GM match** — items OneStop will fulfill itself (expected for brands GM doesn't carry).
5. Staff resolve the review bucket (typically a handful of rows).
6. Click "Apply & Download" → app writes quantities into the active GM template and returns the completed file.
7. Staff forward the completed GM form to GM by their existing channel.

**Target time:** <60 seconds end-to-end for a typical order, assuming <10 rows in the review bucket.

### 6.2 Refresh catalogs (weekly — ~once per week)

1. Ops lead receives the week's new OneStop master template and the week's new GM master template.
2. "Refresh Catalogs" screen → upload both files.
3. App runs exact + fuzzy match against the new GM catalog and shows a diff:
   - **New OneStop items** (never seen before) → need mapping.
   - **Removed OneStop items** → mappings retired.
   - **Changed GM match** — item# retired or pack size changed → re-review.
   - **Price changes on GM side** — informational only (flag for awareness).
4. Ops lead resolves each flagged row; confirmed mappings write to SQLite.
5. Both new templates become "active" for the coming week.

**Target time:** ~10 minutes, assuming 10–30 changed items per week.

## 7. Data model

### 7.1 `onestop_template`
Canonical representation of the current-week OneStop order form.
- `row_index` INT — source row in the original sheet
- `description` TEXT — original description text
- `description_normalized` TEXT — uppercase, punctuation-stripped, whitespace-collapsed
- `price` REAL
- `is_header` BOOL — `true` for category bands like `CADBURY`, `BAKERY ITEMS`

### 7.2 `gm_catalog`
Flat index of every GM item across all sheets.
- `item_no` INT
- `sheet` TEXT — `BRANDED PRODUCTS`, `NON-FOOD PRODUCTS`, `FROZEN PRODUCTS`, `ORGANIC PRODUCTS`, `REST LIST`, `GRAIN MARKET PRODUCTS`, `BULK PRODUCTS`, `SUPPLIES`
- `side` TEXT — `left` (cols A–D) or `right` (cols F–I)
- `row_index` INT — row in the sheet
- `description` TEXT
- `description_normalized` TEXT
- `price` REAL
- `available` BOOL — derived from ORDER cell being `NA` vs. empty

### 7.3 `mapping` (the learning table — the durable artifact)
- `onestop_desc_normalized` TEXT PRIMARY KEY
- `gm_item_no` INT NULL — `NULL` means "OneStop-exclusive, don't try to match"
- `gm_sheet` TEXT NULL
- `confidence` REAL — 1.0 for user-confirmed, else match score
- `confirmed_by` TEXT — `auto` or username
- `confirmed_at` DATETIME
- `notes` TEXT NULL

### 7.4 `order_run` (operational log)
- `id` INT PK
- `uploaded_at` DATETIME
- `uploaded_by` TEXT
- `filename` TEXT
- `lines_auto` INT
- `lines_reviewed` INT
- `lines_unmatched` INT
- `output_path` TEXT — archived completed GM file

## 8. Matching rules (the product promise)

Matching happens in this priority order:

1. **Learned mapping** — if `onestop_desc_normalized` is in the `mapping` table, use it. Confidence always 1.0.
2. **Exact normalized match** against `gm_catalog.description_normalized`. Confidence 1.0.
3. **Fuzzy match** via `rapidfuzz.token_set_ratio`:
   - ≥92: auto-accept, confidence = score/100.
   - 75–91: send to **review bucket**, do not auto-write.
   - <75: treat as "no GM match."
4. **Pack-size guard** — the last pack-size token (regex roughly `\d+[Xx]\d+[A-Za-z]+`) in the OneStop description must appear in the GM description. If not, a fuzzy match is demoted to review regardless of score.

**Primary GM sheet rule (locked):** when an item appears on both a specialized sheet and `REST LIST`, write to the specialized sheet and ignore `REST LIST`. `REST LIST` is informational only.

## 9. Success criteria

- ≥90% of OneStop lines auto-resolve (learned + exact) once the mapping table is seeded.
- ≤5% of lines land in the review bucket in steady-state.
- Zero silent drops (measured by reconciling input line count vs. (auto + review + no-match) count).
- GM opens the returned file and sees no formatting difference vs. their blank template. (Manual verification once.)

## 10. Open questions (for ops)

1. Does GM dedupe item#s across sheets if we ever write the same item# to two sheets? (Confirms whether "specialized sheet only" is strictly correct or just preferred.)
2. What's the volume — orders per day, lines per order? Affects whether archiving every `order_run` output is worth the disk.
3. Which brands are expected OneStop-exclusive long-term? (Worth bulk-flagging to suppress them from the review bucket forever.)

## 11. Out of scope for v1, candidates for v2

- Gmail / WhatsApp auto-ingestion.
- Slack / email notification on completed runs.
- Quantity sanity alerts ("this is 10× the usual").
- PDF summary of what went to GM vs. OneStop-fulfilled.
- Multi-supplier (if OneStop adds a second upstream like GM).

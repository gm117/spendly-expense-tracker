# Spec: Quarterly Expense Tracker

## Overview
Step 10 turns the existing `/analytics` placeholder ("Coming Soon") into a real
quarterly spending report. A logged-in user can pick a year and see their
expenses broken down into four calendar quarters (Q1 Jan–Mar, Q2 Apr–Jun,
Q3 Jul–Sep, Q4 Oct–Dec), with a per-quarter total, transaction count, and
category breakdown. This is the first "reporting" feature beyond the
`/profile` date-range filter, and reuses the same parameterised-date-filter
pattern established in Step 6 rather than introducing new query mechanics.

## Depends on
- Step 1: Database setup (`expenses` table with a `date` column)
- Step 3: Login / Logout (`session["user_id"]` is set and checked)
- Step 5: Backend routes for profile page (`database/queries.py` helper
  pattern — `_build_date_filter`, `get_db()` usage, dict-shaping conventions)
- Step 6: Date filter for profile (establishes the `date_from`/`date_to`
  query-param convention this step follows for year/quarter selection)

## Routes
- `GET /analytics` — modify the existing stub route — logged-in only
  - Reads optional query params `year` (int) and `quarter` (`1`–`4`)
  - If `year` is absent or invalid, defaults to the current year
  - If `quarter` is absent or invalid, shows all four quarters for the
    selected year (no quarter-level filtering)
  - If `quarter` is present and valid, shows only that quarter's detail

No other new routes.

## Database changes
No database changes. The `expenses.date` column (`TEXT`, `YYYY-MM-DD`)
already supports the `strftime('%m', date)` / `BETWEEN` comparisons needed
to bucket rows into quarters.

## Templates
- **Modify:** `templates/analytics.html`
  - Remove the "Coming Soon" placeholder markup (`cs-*` classes)
  - Add a year selector (dropdown or prev/next links) driven by the years
    that actually have expense data for the logged-in user
  - Add four quarter cards (Q1–Q4), each showing: total spent, transaction
    count, and top category for that quarter
  - Clicking a quarter card links to `/analytics?year=<y>&quarter=<q>` to
    show that quarter's category breakdown and transaction list in more
    detail on the same page
  - No structural changes outside this page; `base.html` is untouched

## Files to change
- `app.py`
  - Replace the stub `analytics()` view body: require login (existing
    check already there), parse `year`/`quarter` from `request.args`,
    validate them, fetch data via new query helpers, pass to the template
- `database/queries.py`
  - `get_available_years(user_id)` — distinct years (as ints) present in
    the user's expenses, most recent first
  - `get_quarterly_summary(user_id, year)` — returns a list of 4 dicts
    (one per quarter) each with `quarter`, `total`, `count`, `top_category`,
    computed via parameterised `strftime('%Y', date) = ?` and
    `strftime('%m', date) BETWEEN ? AND ?` clauses
  - `get_quarter_detail(user_id, year, quarter)` — category breakdown and
    transaction list for a single quarter, reusing the existing
    `get_category_breakdown` / `get_recent_transactions` shape where
    practical
- `templates/analytics.html` — rebuild page content (see Templates section)
- `static/css/analytics.css` — replace "coming soon" styles with styles for
  the year selector and quarter cards, using CSS variables only

## Files to create
No new files.

## New dependencies
No new dependencies.

## Rules for implementation
- No SQLAlchemy or ORMs — raw `sqlite3` only via `get_db()`
- Parameterised queries only — never string-format the year/quarter into
  SQL; use `?` placeholders, including inside `strftime` comparisons
- Passwords hashed with werkzeug (no changes to auth in this step)
- Use CSS variables — never hardcode hex values
- All templates extend `base.html`
- No inline styles
- `year`/`quarter` validation happens in `app.py`, not in templates or
  query helpers: invalid/missing `year` → current year; invalid `quarter`
  → treated as absent (show all four quarters)
- A user with no expenses in a given quarter sees ₹0.00 total, 0
  transactions, and "—" top category for that quarter — no errors
- A user with no expenses at all sees an empty-state message instead of a
  crash (`get_available_years` returning `[]` must be handled gracefully)
- Quarter month ranges are fixed: Q1 = 01–03, Q2 = 04–06, Q3 = 07–09,
  Q4 = 10–12

## Definition of done
- [ ] Visiting `/analytics` while logged out redirects to `/login`
- [ ] Visiting `/analytics` with no query params shows the current year's
  four quarters with correct totals/counts/top categories
- [ ] Selecting a past year (one with seeded/added data) via the year
  selector updates all four quarter cards correctly
- [ ] Selecting a year with no expenses shows all four quarters at
  ₹0.00 / 0 transactions / "—" with no errors
- [ ] Clicking into a specific quarter shows that quarter's category
  breakdown and transaction list, matching what `/profile` would show if
  filtered to the same date range
- [ ] Passing a malformed `year` (e.g. `year=abc`) does not crash the app —
  falls back to the current year
- [ ] Passing an out-of-range `quarter` (e.g. `quarter=5`) does not crash
  the app — falls back to showing all four quarters
- [ ] All amounts display the ₹ symbol consistently with `/profile`
- [ ] The four quarters' totals for a year sum to the same grand total
  shown on `/profile` for that same year's date range

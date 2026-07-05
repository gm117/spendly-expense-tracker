"""
Tests for Step 10: Quarterly Expense Tracker (/analytics)
Spec: .claude/specs/10-quarterly-expense-tracker.md

These tests are written from the FEATURE SPEC only — they define what the
`/analytics` route should do, not what its current implementation happens to
do. Coverage areas:

  1. Auth guard: unauthenticated GET /analytics redirects to /login
  2. Default view (no query params): current year, all four quarters shown,
     no drill-down detail
  3. Year selection: a valid past year updates all four quarter cards;
     a year with no expenses shows the zero-state for every quarter
  4. Malformed `year` (missing/non-numeric/injection/very long) never crashes
     and silently falls back to the current year
  5. Quarter drill-down: a valid `quarter` (1-4) additionally shows that
     quarter's category breakdown + transaction list, matching what
     /profile shows when filtered to the same date range
  6. Invalid `quarter` (out-of-range, non-numeric, injection) never crashes
     and falls back to "no quarter selected" (all four quarters, no detail)
  7. Empty states: a quarter with no expenses shows Rs0.00 / 0 / "-" with no
     errors; a user with zero expenses overall sees an empty-state message
  8. Rs symbol is always present in rendered amounts, never "$"
  9. DB-side correctness: quarter buckets are non-overlapping and
     non-missing — the four quarters' totals for a year sum to the same
     grand total /profile shows for that year's full date range, including
     at calendar-quarter boundary dates
 10. Query helper contracts named in the spec (get_available_years,
     get_quarterly_summary, get_quarter_detail) return the shapes the spec
     describes
"""

import calendar
from datetime import date

import pytest

import database.db as db_module
from app import app as flask_app
from database.db import get_db, init_db
from database.queries import (
    get_available_years,
    get_quarter_detail,
    get_quarterly_summary,
)

# ---------------------------------------------------------------------------
# Constants / helpers
# ---------------------------------------------------------------------------

TEST_EMAIL = "quarterly@example.com"
TEST_PASSWORD = "testpass123"
TEST_NAME = "Quarterly Tester"

CURRENT_YEAR = date.today().year
PAST_YEAR = CURRENT_YEAR - 1
EMPTY_YEAR = CURRENT_YEAR - 5  # guaranteed to have no seeded data in any test


def _quarter_range(year, quarter):
    """
    Compute the [start, end] ISO date bounds for a calendar quarter, per the
    spec's fixed month ranges: Q1=01-03, Q2=04-06, Q3=07-09, Q4=10-12.
    """
    start_month, end_month = {1: (1, 3), 2: (4, 6), 3: (7, 9), 4: (10, 12)}[quarter]
    start = date(year, start_month, 1).isoformat()
    end_day = calendar.monthrange(year, end_month)[1]
    end = date(year, end_month, end_day).isoformat()
    return start, end


def _to_float(amount_str):
    """Convert a formatted amount string like '1,234.50' to a float."""
    return float(amount_str.replace(",", ""))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    """Path to a fresh, isolated SQLite database file for this test."""
    return str(tmp_path / "test_spendly.db")


@pytest.fixture
def app(db_path, monkeypatch):
    """
    Flask app configured for testing with an isolated SQLite DB.

    monkeypatch replaces DB_PATH in database.db so every call to get_db() —
    whether from route handlers or query helpers — uses the temp database.
    """
    monkeypatch.setattr(db_module, "DB_PATH", db_path)

    flask_app.config.update(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "WTF_CSRF_ENABLED": False,
        }
    )

    with flask_app.app_context():
        init_db()
        yield flask_app


@pytest.fixture
def client(app):
    return app.test_client()


def _register_and_login(client, email=TEST_EMAIL, password=TEST_PASSWORD, name=TEST_NAME):
    client.post(
        "/register",
        data={
            "name": name,
            "email": email,
            "password": password,
            "confirm_password": password,
        },
    )
    client.post("/login", data={"email": email, "password": password})


@pytest.fixture
def user_id(app, client):
    """Register + log in a fresh user via the real routes; return their id."""
    _register_and_login(client)
    with app.app_context():
        conn = get_db()
        row = conn.execute(
            "SELECT id FROM users WHERE email = ?", (TEST_EMAIL,)
        ).fetchone()
        conn.close()
    assert row is not None, "Setup failed: registered user not found in DB"
    return row["id"]


@pytest.fixture
def auth_client(client, user_id):
    """Test client already logged in as the fixture user."""
    return client


def _insert_expense(app, user_id, amount, category, expense_date, description=""):
    with app.app_context():
        conn = get_db()
        conn.execute(
            "INSERT INTO expenses (user_id, amount, category, date, description) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, amount, category, expense_date, description),
        )
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# 1. Auth guard
# ---------------------------------------------------------------------------

class TestAuthGuard:
    def test_unauthenticated_get_analytics_redirects_to_login(self, client):
        response = client.get("/analytics", follow_redirects=False)
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]

    def test_unauthenticated_get_analytics_with_query_params_redirects_to_login(self, client):
        response = client.get(
            f"/analytics?year={CURRENT_YEAR}&quarter=1", follow_redirects=False
        )
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]


# ---------------------------------------------------------------------------
# 2. Default view — no query params
# ---------------------------------------------------------------------------

class TestDefaultView:
    def test_no_params_returns_200(self, app, auth_client, user_id):
        response = auth_client.get("/analytics")
        assert response.status_code == 200

    def test_no_params_shows_all_four_quarter_labels(self, app, auth_client, user_id):
        _insert_expense(app, user_id, 100.00, "Food", f"{CURRENT_YEAR}-02-10", "Q1 item")
        body = auth_client.get("/analytics").data.decode()
        assert "Q1" in body
        assert "Q2" in body
        assert "Q3" in body
        assert "Q4" in body

    def test_no_params_defaults_to_current_year_data(self, app, auth_client, user_id):
        _insert_expense(app, user_id, 321.00, "Food", f"{CURRENT_YEAR}-02-10", "Current year groceries")
        body = auth_client.get("/analytics").data.decode()
        assert "321.00" in body

    def test_no_quarter_selected_shows_no_drill_down_detail(self, app, auth_client, user_id):
        """Without a quarter selection, the per-transaction detail must not render."""
        _insert_expense(app, user_id, 50.00, "Food", f"{CURRENT_YEAR}-02-10", "Unique Snack Purchase XYZ")
        body = auth_client.get("/analytics").data.decode()
        assert "Unique Snack Purchase XYZ" not in body


# ---------------------------------------------------------------------------
# 3. Year selection
# ---------------------------------------------------------------------------

class TestYearSelection:
    def test_valid_past_year_updates_all_four_quarter_cards(self, app, auth_client, user_id):
        _insert_expense(app, user_id, 999.00, "Bills", f"{CURRENT_YEAR}-02-10", "Current year bill")
        _insert_expense(app, user_id, 111.00, "Food", f"{PAST_YEAR}-02-10", "Past year groceries")

        body = auth_client.get(f"/analytics?year={PAST_YEAR}").data.decode()
        assert "111.00" in body
        assert "999.00" not in body

    def test_year_with_no_expenses_shows_zero_state_for_all_quarters(self, app, auth_client, user_id):
        """Spec: a quarter/year with no expenses shows Rs0.00, 0 transactions, '-' — no errors."""
        _insert_expense(app, user_id, 500.00, "Food", f"{CURRENT_YEAR}-02-10", "Some expense")

        response = auth_client.get(f"/analytics?year={EMPTY_YEAR}")
        assert response.status_code == 200
        body = response.data.decode()
        assert body.count("₹0.00") >= 4, "Expected all four quarters to show ₹0.00"
        assert body.count("—") >= 4, "Expected all four quarters to show '—' top category"


# ---------------------------------------------------------------------------
# 4. Malformed `year` never crashes; falls back to current year
# ---------------------------------------------------------------------------

class TestMalformedYear:
    def test_malformed_year_does_not_crash(self, app, auth_client, user_id):
        response = auth_client.get("/analytics?year=abc")
        assert response.status_code == 200

    def test_malformed_year_falls_back_to_current_year_data(self, app, auth_client, user_id):
        _insert_expense(app, user_id, 654.00, "Food", f"{CURRENT_YEAR}-05-05", "Current year item")
        body = auth_client.get("/analytics?year=abc").data.decode()
        assert "654.00" in body

    @pytest.mark.parametrize(
        "bad_year",
        [
            "abc",
            "",
            "12.5",
            "year",
            "null",
            "'; DROP TABLE expenses; --",
            "a" * 300,  # very long malformed input
        ],
    )
    def test_various_malformed_years_do_not_crash(self, app, auth_client, user_id, bad_year):
        response = auth_client.get(f"/analytics?year={bad_year}")
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 5. Quarter drill-down
# ---------------------------------------------------------------------------

class TestQuarterSelectionAndDrillDown:
    def test_valid_quarter_returns_200(self, app, auth_client, user_id):
        response = auth_client.get(f"/analytics?year={CURRENT_YEAR}&quarter=1")
        assert response.status_code == 200

    @pytest.mark.parametrize("quarter", [1, 2, 3, 4])
    def test_selecting_each_valid_quarter_shows_its_own_transaction(self, app, auth_client, user_id, quarter):
        date_from, _ = _quarter_range(CURRENT_YEAR, quarter)
        description = f"Unique marker for quarter {quarter}"
        _insert_expense(app, user_id, 42.00, "Food", date_from, description)

        body = auth_client.get(f"/analytics?year={CURRENT_YEAR}&quarter={quarter}").data.decode()
        assert description in body, f"Q{quarter} transaction marker missing from drill-down"

    def test_selecting_quarter_does_not_show_other_quarters_transactions(self, app, auth_client, user_id):
        _insert_expense(app, user_id, 10.00, "Food", date(CURRENT_YEAR, 2, 10).isoformat(), "Q1 only marker")
        _insert_expense(app, user_id, 20.00, "Food", date(CURRENT_YEAR, 8, 10).isoformat(), "Q3 only marker")

        body = auth_client.get(f"/analytics?year={CURRENT_YEAR}&quarter=1").data.decode()
        assert "Q1 only marker" in body
        assert "Q3 only marker" not in body

    def test_no_quarter_param_hides_transaction_detail(self, app, auth_client, user_id):
        _insert_expense(app, user_id, 15.00, "Food", date(CURRENT_YEAR, 2, 10).isoformat(), "Hidden without drilldown")
        body = auth_client.get(f"/analytics?year={CURRENT_YEAR}").data.decode()
        assert "Hidden without drilldown" not in body

    def test_quarter_detail_matches_profile_filtered_view_for_same_range(self, app, auth_client, user_id):
        """
        Spec DoD: 'Clicking into a specific quarter shows that quarter's category
        breakdown and transaction list, matching what /profile would show if
        filtered to the same date range.'
        """
        date_from, date_to = _quarter_range(CURRENT_YEAR, 2)
        _insert_expense(app, user_id, 275.50, "Bills", date(CURRENT_YEAR, 4, 20).isoformat(), "Q2 electricity")
        _insert_expense(app, user_id, 89.00, "Food", date(CURRENT_YEAR, 5, 5).isoformat(), "Q2 lunch")
        # Outside Q2 — must not leak into either view
        _insert_expense(app, user_id, 999.00, "Shopping", date(CURRENT_YEAR, 8, 1).isoformat(), "Q3 gadget")

        analytics_body = auth_client.get(f"/analytics?year={CURRENT_YEAR}&quarter=2").data.decode()
        profile_body = auth_client.get(f"/profile?date_from={date_from}&date_to={date_to}").data.decode()

        for marker in ("Q2 electricity", "Q2 lunch"):
            assert marker in analytics_body
            assert marker in profile_body

        assert "Q3 gadget" not in analytics_body
        assert "Q3 gadget" not in profile_body

        # Same combined total (275.50 + 89.00 = 364.50) must appear on both pages
        assert "364.50" in analytics_body
        assert "364.50" in profile_body


# ---------------------------------------------------------------------------
# 6. Invalid `quarter` never crashes; falls back to "no quarter selected"
# ---------------------------------------------------------------------------

class TestInvalidQuarterFallback:
    def test_out_of_range_quarter_does_not_crash(self, app, auth_client, user_id):
        response = auth_client.get(f"/analytics?year={CURRENT_YEAR}&quarter=5")
        assert response.status_code == 200

    def test_out_of_range_quarter_falls_back_to_no_drill_down(self, app, auth_client, user_id):
        _insert_expense(app, user_id, 33.00, "Food", date(CURRENT_YEAR, 2, 10).isoformat(), "Should stay hidden")
        body = auth_client.get(f"/analytics?year={CURRENT_YEAR}&quarter=5").data.decode()
        assert "Should stay hidden" not in body

    @pytest.mark.parametrize(
        "bad_quarter",
        ["0", "5", "-1", "abc", "", "3.5", "10", "'; DROP TABLE expenses; --"],
    )
    def test_various_invalid_quarters_do_not_crash_and_hide_detail(self, app, auth_client, user_id, bad_quarter):
        _insert_expense(app, user_id, 22.00, "Food", date(CURRENT_YEAR, 2, 10).isoformat(), "Marker for invalid quarter test")
        response = auth_client.get(f"/analytics?year={CURRENT_YEAR}&quarter={bad_quarter}")
        assert response.status_code == 200
        body = response.data.decode()
        assert "Marker for invalid quarter test" not in body


# ---------------------------------------------------------------------------
# 7. Empty states
# ---------------------------------------------------------------------------

class TestEmptyStates:
    def test_quarter_with_no_expenses_shows_zero_state_no_error(self, app, auth_client, user_id):
        # Seed only Q1 data; Q3 has nothing for this year
        _insert_expense(app, user_id, 100.00, "Food", date(CURRENT_YEAR, 2, 10).isoformat(), "Q1 only")

        response = auth_client.get(f"/analytics?year={CURRENT_YEAR}&quarter=3")
        assert response.status_code == 200
        body = response.data.decode()
        assert "₹0.00" in body
        assert "—" in body

    def test_user_with_zero_expenses_overall_shows_empty_state(self, app, auth_client, user_id):
        """
        Spec: 'A user with no expenses at all sees an empty-state message
        instead of a crash (get_available_years returning [] must be handled
        gracefully).'
        """
        response = auth_client.get("/analytics")
        assert response.status_code == 200
        body = response.data.decode()

        # No server error markers
        assert "Traceback" not in body
        assert "Internal Server Error" not in body

        # Some empty-state cue must be present rather than a normal quarter grid
        body_lower = body.lower()
        assert (
            "no expense" in body_lower
            or "add" in body_lower
            or "start" in body_lower
        ), "Expected an empty-state message for a user with zero expenses"


# ---------------------------------------------------------------------------
# 8. Rs symbol always present, never $
# ---------------------------------------------------------------------------

class TestRupeeSymbol:
    def test_rupee_symbol_present_default_view(self, app, auth_client, user_id):
        _insert_expense(app, user_id, 10.00, "Food", date(CURRENT_YEAR, 2, 10).isoformat())
        body = auth_client.get("/analytics").data.decode()
        assert "₹" in body

    def test_rupee_symbol_present_with_quarter_selected(self, app, auth_client, user_id):
        _insert_expense(app, user_id, 10.00, "Food", date(CURRENT_YEAR, 2, 10).isoformat())
        body = auth_client.get(f"/analytics?year={CURRENT_YEAR}&quarter=1").data.decode()
        assert "₹" in body

    def test_rupee_symbol_present_on_zero_state_quarter(self, app, auth_client, user_id):
        _insert_expense(app, user_id, 10.00, "Food", date(CURRENT_YEAR, 2, 10).isoformat())
        body = auth_client.get(f"/analytics?year={CURRENT_YEAR}&quarter=3").data.decode()
        assert "₹" in body

    def test_no_dollar_sign_in_analytics_page(self, app, auth_client, user_id):
        _insert_expense(app, user_id, 10.00, "Food", date(CURRENT_YEAR, 2, 10).isoformat())
        body = auth_client.get("/analytics").data.decode()
        assert "$" not in body


# ---------------------------------------------------------------------------
# 9. DB-side correctness: quarter bucketing is exhaustive and non-overlapping
# ---------------------------------------------------------------------------

class TestQuarterBucketingCorrectness:
    def test_quarter_totals_sum_to_profile_grand_total_http(self, app, auth_client, user_id):
        """
        Spec DoD: 'The four quarters' totals for a year sum to the same grand
        total shown on /profile for that same year's date range.'
        """
        expenses = [
            (150.00, "Food", date(CURRENT_YEAR, 1, 10).isoformat(), "Jan item"),
            (250.50, "Transport", date(CURRENT_YEAR, 3, 31).isoformat(), "End of Q1 item"),
            (300.00, "Bills", date(CURRENT_YEAR, 4, 1).isoformat(), "Start of Q2 item"),
            (75.25, "Health", date(CURRENT_YEAR, 6, 30).isoformat(), "End of Q2 item"),
            (500.00, "Shopping", date(CURRENT_YEAR, 7, 1).isoformat(), "Start of Q3 item"),
            (999.99, "Entertainment", date(CURRENT_YEAR, 9, 30).isoformat(), "End of Q3 item"),
            (120.00, "Other", date(CURRENT_YEAR, 10, 1).isoformat(), "Start of Q4 item"),
            (60.00, "Food", date(CURRENT_YEAR, 12, 31).isoformat(), "End of Q4 item"),
        ]
        for amount, category, expense_date, description in expenses:
            _insert_expense(app, user_id, amount, category, expense_date, description)

        expected_grand_total = sum(e[0] for e in expenses)
        expected_total_str = "{:,.2f}".format(expected_grand_total)

        # Grand total, unfiltered for the full year, as /profile shows it
        profile_body = auth_client.get(
            f"/profile?date_from={CURRENT_YEAR}-01-01&date_to={CURRENT_YEAR}-12-31"
        ).data.decode()
        assert expected_total_str in profile_body, (
            f"Expected grand total {expected_total_str} not found on /profile"
        )

        # Sum of the four quarter totals via the query helper must match exactly
        with app.app_context():
            quarters = get_quarterly_summary(user_id, CURRENT_YEAR)

        assert len(quarters) == 4
        quarter_sum = sum(_to_float(q["total"]) for q in quarters)
        assert quarter_sum == pytest.approx(expected_grand_total, abs=0.001)

        quarter_count_sum = sum(q["count"] for q in quarters)
        assert quarter_count_sum == len(expenses)

        # Boundary dates (first/last day of each quarter) must land in exactly
        # the right bucket — 2 expenses per quarter, no overlap/no gaps.
        assert quarters[0]["quarter"] == 1 and quarters[0]["count"] == 2
        assert quarters[1]["quarter"] == 2 and quarters[1]["count"] == 2
        assert quarters[2]["quarter"] == 3 and quarters[2]["count"] == 2
        assert quarters[3]["quarter"] == 4 and quarters[3]["count"] == 2

    def test_each_quarter_total_matches_expected_bucketed_sum_via_http(self, app, auth_client, user_id):
        """Same boundary-date scenario, verified purely through the rendered page."""
        expenses = [
            (150.00, date(CURRENT_YEAR, 1, 10).isoformat()),   # Q1
            (250.50, date(CURRENT_YEAR, 3, 31).isoformat()),   # Q1 (last day)
            (300.00, date(CURRENT_YEAR, 4, 1).isoformat()),    # Q2 (first day)
            (75.25, date(CURRENT_YEAR, 6, 30).isoformat()),    # Q2 (last day)
            (500.00, date(CURRENT_YEAR, 7, 1).isoformat()),    # Q3 (first day)
            (999.99, date(CURRENT_YEAR, 9, 30).isoformat()),   # Q3 (last day)
            (120.00, date(CURRENT_YEAR, 10, 1).isoformat()),   # Q4 (first day)
            (60.00, date(CURRENT_YEAR, 12, 31).isoformat()),   # Q4 (last day)
        ]
        for amount, expense_date in expenses:
            _insert_expense(app, user_id, amount, "Food", expense_date, f"item-{expense_date}")

        body = auth_client.get(f"/analytics?year={CURRENT_YEAR}").data.decode()

        assert "400.50" in body     # Q1: 150.00 + 250.50
        assert "375.25" in body     # Q2: 300.00 + 75.25
        assert "1,499.99" in body   # Q3: 500.00 + 999.99
        assert "180.00" in body     # Q4: 120.00 + 60.00


# ---------------------------------------------------------------------------
# 10. Query helper contracts named explicitly in the spec
# ---------------------------------------------------------------------------

class TestQueryHelperContracts:
    """
    Direct checks of the helper contracts described in the spec's
    'Files to change' section, independent of the HTTP layer:
      - get_available_years(user_id)
      - get_quarterly_summary(user_id, year)
      - get_quarter_detail(user_id, year, quarter)
    """

    def test_get_available_years_returns_years_most_recent_first(self, app, user_id):
        _insert_expense(app, user_id, 10.00, "Food", f"{CURRENT_YEAR}-01-05")
        _insert_expense(app, user_id, 20.00, "Food", f"{PAST_YEAR}-01-05")
        with app.app_context():
            years = get_available_years(user_id)
        assert years == sorted(years, reverse=True)
        assert CURRENT_YEAR in years
        assert PAST_YEAR in years

    def test_get_available_years_empty_for_user_with_no_expenses(self, app, user_id):
        with app.app_context():
            years = get_available_years(user_id)
        assert years == []

    def test_get_quarterly_summary_returns_four_dicts_with_required_keys(self, app, user_id):
        with app.app_context():
            quarters = get_quarterly_summary(user_id, CURRENT_YEAR)
        assert len(quarters) == 4
        for q in quarters:
            for key in ("quarter", "total", "count", "top_category"):
                assert key in q

    def test_get_quarterly_summary_zero_state_for_year_with_no_data(self, app, user_id):
        with app.app_context():
            quarters = get_quarterly_summary(user_id, EMPTY_YEAR)
        for q in quarters:
            assert q["total"] == "0.00"
            assert q["count"] == 0
            assert q["top_category"] == "—"

    def test_get_quarter_detail_returns_categories_and_transactions(self, app, user_id):
        _insert_expense(app, user_id, 50.00, "Food", date(CURRENT_YEAR, 2, 1).isoformat(), "Detail check")
        with app.app_context():
            detail = get_quarter_detail(user_id, CURRENT_YEAR, 1)

        assert isinstance(detail, dict)
        assert "categories" in detail
        assert "transactions" in detail

        descriptions = [tx.get("description") for tx in detail["transactions"]]
        assert "Detail check" in descriptions

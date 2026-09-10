import pytest
import os
import subprocess
import sys


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://localhost/gancuju_education_center_prod",
        "postgresql://localhost/lfa_production",
        "postgresql://localhost/lfa_staging",
        "postgresql://db.internal/lfa_live",
    ],
)
def test_testing_rejects_production_like_or_unmarked_database_urls(url):
    from app.core.test_database_safety import UnsafeTestDatabaseError, assert_safe_test_database_url

    with pytest.raises(UnsafeTestDatabaseError):
        assert_safe_test_database_url(url, explicitly_configured=True, disposable_confirmed=False)


def test_testing_rejects_implicit_default_even_if_name_looks_safe():
    from app.core.test_database_safety import UnsafeTestDatabaseError, assert_safe_test_database_url

    with pytest.raises(UnsafeTestDatabaseError, match="explicit"):
        assert_safe_test_database_url(
            "postgresql://localhost/lfa_test",
            explicitly_configured=False,
            disposable_confirmed=False,
        )


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://localhost/lfa_pc1_test",
        "postgresql://127.0.0.1/lfa_disposable_123",
        "postgresql://localhost/ci_check",
    ],
)
def test_testing_accepts_explicit_local_test_database(url):
    from app.core.test_database_safety import assert_safe_test_database_url

    assert_safe_test_database_url(url, explicitly_configured=True, disposable_confirmed=False)


def test_nonlocal_database_requires_explicit_disposable_confirmation():
    from app.core.test_database_safety import UnsafeTestDatabaseError, assert_safe_test_database_url

    url = "postgresql://ephemeral-ci.internal/lfa_test"
    with pytest.raises(UnsafeTestDatabaseError, match="disposable confirmation"):
        assert_safe_test_database_url(url, explicitly_configured=True, disposable_confirmed=False)
    assert_safe_test_database_url(url, explicitly_configured=True, disposable_confirmed=True)


def test_application_database_import_stops_before_prod_like_test_engine_creation():
    env = os.environ.copy()
    env.update(
        TESTING="1",
        DATABASE_URL="postgresql://127.0.0.1:1/gancuju_education_center_prod",
    )
    completed = subprocess.run(
        [sys.executable, "-c", "import app.database"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode != 0
    assert "Production-like database name is forbidden" in completed.stderr


def test_pytest_process_stops_before_prod_like_engine_even_without_testing_flag():
    env = os.environ.copy()
    env.pop("TESTING", None)
    env.update(DATABASE_URL="postgresql://127.0.0.1:1/gancuju_education_center_prod")
    completed = subprocess.run(
        [sys.executable, "-c", "import pytest; import app.database"],
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode != 0
    assert "Production-like database name is forbidden" in completed.stderr

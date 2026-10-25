from app.config import Settings, default_state_dir


def test_state_dir_is_outside_current_working_directory():
    import sys
    expected_name = "Synfull" if sys.platform == "win32" else "synfull"
    assert default_state_dir().name == expected_name


def test_explicit_database_and_log_paths_are_respected(tmp_path):
    database = tmp_path / "custom.sqlite3"
    logs = tmp_path / "logs"
    settings = Settings(state_dir=tmp_path / "state", database_path=database, log_dir=logs)
    assert settings.resolved_database_path == database
    assert settings.resolved_log_dir == logs
    settings.prepare_directories()
    assert database.parent.exists()
    assert logs.is_dir()

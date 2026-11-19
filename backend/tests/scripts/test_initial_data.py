"""Smoke for the prestart entry point: bootstraps the superuser, idempotent."""

from app.initial_data import init, main


def test_initial_data_is_idempotent() -> None:
    # the conftest db fixture already created the superuser; running the
    # bootstrap again (what `prestart.sh` does on every deploy) must not fail
    init()
    init()


def test_initial_data_main_runs() -> None:
    main()

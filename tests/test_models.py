from __future__ import annotations

from pathlib import Path

from find_stuff.models import Repository, create_engine_for_path, init_db


def test_models_create_schema(tmp_path: Path) -> None:
    db = tmp_path / "model.sqlite3"
    engine = create_engine_for_path(db)
    init_db(engine)

    # Basic insert roundtrip and existence check
    with engine.begin() as conn:
        conn.execute(
            Repository.__table__.insert().values(  # type: ignore
                root=str(tmp_path / "repo")
            )
        )

    assert db.exists()

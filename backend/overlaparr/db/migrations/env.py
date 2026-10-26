from alembic import context
from sqlalchemy import create_engine

from overlaparr.db.models import Base

target_metadata = Base.metadata


def _run(connection) -> None:  # noqa: ANN001
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations() -> None:
    connection = context.config.attributes.get("connection")
    if connection is None:  # developer CLI: `alembic -c alembic.ini ...`
        engine = create_engine(context.config.get_main_option("sqlalchemy.url"))
        with engine.connect() as connection:
            _run(connection)
            connection.commit()
    else:
        _run(connection)


run_migrations()

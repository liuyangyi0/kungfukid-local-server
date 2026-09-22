"""Connection-scoped transaction ownership; repositories never commit."""
from contextlib import contextmanager


def require_transaction(db):
    if not db.in_transaction:raise ValueError('repository write requires an owned transaction')


@contextmanager
def transaction(db,message='nested transaction',*,immediate=True):
    if db.in_transaction:raise ValueError(message)
    db.execute('BEGIN IMMEDIATE' if immediate else 'BEGIN')
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise

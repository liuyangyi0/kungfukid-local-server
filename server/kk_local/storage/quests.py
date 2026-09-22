"""Quest definitions and progress persistence; no eligibility or reward policy."""
from dataclasses import dataclass
from .transactions import require_transaction


@dataclass(frozen=True)
class QuestProgress:
    definition: str
    state: int
    baseline: bytes
    execution: str | None
    counts: bytes
    claimed_instance: int | None


class QuestRepository:
    def __init__(self, db):
        self._db = db

    def availability(self):
        return self._db.execute('SELECT family,task_key,definition FROM quest_availability ORDER BY family,task_key').fetchall()

    def available_definition(self, family, key):
        return self._db.execute('SELECT definition FROM quest_availability WHERE family=? AND task_key=?', (family, key)).fetchone()

    def conflicting_progress(self, family, key, definition):
        return self._db.execute('SELECT 1 FROM quest_progress WHERE family=? AND task_key=? AND definition<>?',
                                (family, key, definition)).fetchone() is not None

    def replace_availability(self, rows):
        require_transaction(self._db)
        self._db.execute('DELETE FROM quest_availability')
        self._db.executemany('INSERT INTO quest_availability VALUES(?,?,?)', rows)

    def progress(self, uid, family, key):
        row = self._db.execute('SELECT definition,state,baseline,execution,counts,claimed_instance '
                               'FROM quest_progress WHERE uid=? AND family=? AND task_key=?', (uid, family, key)).fetchone()
        return QuestProgress(*row) if row is not None else None

    def saved_states(self, uid):
        return self._db.execute('SELECT family,task_key,definition,state FROM quest_progress WHERE uid=?', (uid,)).fetchall()

    def transition(self, uid, family, key, definition, state, baseline, execution):
        require_transaction(self._db)
        self._db.execute('INSERT INTO quest_progress(uid,family,task_key,definition,state,baseline,execution) '
                         'VALUES(?,?,?,?,?,?,?) ON CONFLICT(uid,family,task_key) DO UPDATE SET '
                         'state=excluded.state,baseline=excluded.baseline,execution=excluded.execution,counts=zeroblob(12)',
                         (uid, family, key, definition, state, baseline, execution))

    def rule(self, family, key):
        return self._db.execute('SELECT definition,rule FROM quest_reward_rules WHERE family=? AND task_key=?', (family, key)).fetchone()

    def replace_rules(self, rows):
        require_transaction(self._db)
        self._db.execute('DELETE FROM quest_reward_rules')
        self._db.executemany('INSERT INTO quest_reward_rules VALUES(?,?,?,?)', rows)

    def ordinary_rules(self):
        return self._db.execute('''SELECT r.task_key,r.definition,r.rule FROM quest_reward_rules r
            JOIN quest_availability a ON a.family=r.family AND a.task_key=r.task_key AND a.definition=r.definition
            WHERE r.family='ordinary' ''').fetchall()

    def has_rules(self):
        return self._db.execute('SELECT 1 FROM quest_reward_rules LIMIT 1').fetchone() is not None

    def active_extended(self, uid):
        return self._db.execute('''SELECT p.family,p.task_key,p.definition,p.execution,p.counts
            FROM quest_progress p JOIN quest_availability a ON p.family=a.family AND p.task_key=a.task_key AND p.definition=a.definition
            JOIN quest_reward_rules r ON r.family=p.family AND r.task_key=p.task_key AND r.definition=p.definition
            WHERE p.uid=? AND p.state=2 AND p.family IN ('daily','newbie') AND p.execution IS NOT NULL''', (uid,)).fetchall()

    def active_ordinary(self, uid):
        return self._db.execute('''SELECT p.task_key,p.definition,p.baseline,p.execution
            FROM quest_progress p JOIN quest_reward_rules r ON r.family=p.family AND r.task_key=p.task_key AND r.definition=p.definition
            WHERE p.uid=? AND p.family='ordinary' AND p.state=2 AND p.execution IS NOT NULL ORDER BY p.task_key''', (uid,)).fetchall()

    def claimed_ordinary(self, uid):
        return self._db.execute("SELECT task_key,definition,execution,claimed_instance FROM quest_progress "
                                "WHERE uid=? AND family='ordinary' AND state=3 AND execution IS NOT NULL ORDER BY task_key", (uid,)).fetchall()

    def update_counts(self, uid, family, key, counts, state):
        require_transaction(self._db)
        self._db.execute('UPDATE quest_progress SET counts=?,state=? WHERE uid=? AND family=? AND task_key=?',
                         (counts, state, uid, family, key))

    def mark_claimed(self, uid, family, key, instance):
        require_transaction(self._db)
        self._db.execute('UPDATE quest_progress SET state=3,claimed_instance=? WHERE uid=? AND family=? AND task_key=?',
                         (instance, uid, family, key))

    def completed(self, uid):
        return self._db.execute('SELECT family,task_key,state FROM quest_progress WHERE uid=? AND state=4', (uid,)).fetchall()

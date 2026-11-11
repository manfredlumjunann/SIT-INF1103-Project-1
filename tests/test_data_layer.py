"""Data layer: DataManager, repositories, entities and the database schema (needs PostgreSQL)."""

import unittest

from sqlalchemy import inspect as sql_inspect

from support import TEST_DATABASE_URL, ReportedCase, _engine, reset_database, run_sql

from data_manager import DataManager
from database import Database
from entities import AnalysisStatus, Base, RiskLevel, Role, Verification
from repositories import AnalysisRepository, ReferenceRepository, UserRepository


def ai_clause(kind='Liability', risk='HIGH', text='Text', refs=()):
    return {'clause_type': kind, 'risk_level': risk, 'clause_text': text, 'issue_description': 'Issue',
            'workaround': 'Fix', 'line_number': 'Page 1',
            'legal_references': [{'title': title, 'url': url, 'summary': f'About {title}'} for title, url in refs]}


class DataCase(ReportedCase):
    @classmethod
    def setUpClass(cls):
        cls.data = DataManager(Database(TEST_DATABASE_URL))
        cls.addClassCleanup(cls.data.close)

    def setUp(self):
        reset_database()


class DataManagerTests(DataCase):
    def test_composition_of_repositories(self):
        self.verify('DataManager creates its own repositories (composition)',
                    'type of data.users / data.references / data.analyses',
                    ('UserRepository', 'ReferenceRepository', 'AnalysisRepository'),
                    (type(self.data.users).__name__, type(self.data.references).__name__,
                     type(self.data.analyses).__name__))
        self.verify('Every repository is-a Repository', 'isinstance(repository, Repository)',
                    True, all(isinstance(r, (UserRepository, ReferenceRepository, AnalysisRepository))
                              for r in (self.data.users, self.data.references, self.data.analyses)))

    def test_init_storage_and_counts(self):
        self.data.users.create('alice', 'hash')
        self.verify('init_storage() reports record counts',
                    'one user created, then init_storage()',
                    {'users': 1, 'analyses': 0, 'references': 0}, self.data.init_storage())

    def test_database_helpers(self):
        self.verify('postgres:// URLs are given the psycopg 3 driver', "Database.normalise_url('postgres://u:p@h/db')",
                    'postgresql+psycopg://u:p@h/db', Database.normalise_url('postgres://u:p@h/db'))
        database = Database('postgresql+psycopg://user:secret@localhost/db')
        self.verify('The password never appears in the printed URL', 'repr(Database(...secret...))',
                    "Database('postgresql+psycopg://user:***@localhost/db')", repr(database))


class UserRepositoryTests(DataCase):
    def test_create_find_list(self):
        alice = self.data.users.create('Alice', 'hash-1')
        self.data.users.create('bob', 'hash-2', Role.ADMIN)
        self.verify('create() lower-cases the username and numbers the id', "create('Alice', 'hash-1')",
                    ('us_001', 'alice', Role.CUSTOMER), (alice.id, alice.username, alice.role))
        self.verify('find_by_username() ignores letter case', "find_by_username('ALICE')",
                    'us_001', self.data.users.find_by_username('ALICE').id)
        self.verify('list() is ordered by username and get() finds by id',
                    'users alice (customer), bob (admin)',
                    (['alice', 'bob'], True, None),
                    ([u.username for u in self.data.users.list()], self.data.users.get('us_002').is_admin(),
                     self.data.users.get('us_999')))

    def test_duplicates_and_invalid_input(self):
        self.data.users.create('alice', 'hash')
        results = []
        for username, password_hash in (('ALICE', 'hash'), ('ab', 'hash'), ('carol', '')):
            try:
                self.data.users.create(username, password_hash)
                results.append('created')
            except ValueError as error:
                results.append(str(error))
        self.verify('Duplicate (case-insensitive), too-short and blank-hash users are refused',
                    "create('ALICE', ...), create('ab', ...), create('carol', '')",
                    ['Username already taken: alice',
                     "Username must be 3-30 characters: letters, numbers, '.', '_' or '-'",
                     'password_hash must not be empty'], results)
        self.verify('A refused duplicate does not use up an id number', "next user after the refusals",
                    'us_002', self.data.users.create('bob', 'hash').id)


class ReferenceRepositoryTests(DataCase):
    def test_add_many_is_idempotent_and_verification(self):
        from entities import LegalReference
        first = LegalReference.from_url('https://www.example.org/a', 'A')
        second = LegalReference.from_url('https://example.org/b', 'B')
        added_first = self.data.references.add_many([first, second])
        added_again = self.data.references.add_many([first])
        self.verify('add_many() stores each source once', 'add_many([A, B]) then add_many([A])',
                    ([first.id, second.id], []), ([r.id for r in added_first], [r.id for r in added_again]))
        self.data.references.set_verification(first.id, Verification.VERIFIED_SG)
        self.verify('set_verification() updates the stored state', f'set_verification({first.id}, VERIFIED_SG)',
                    (Verification.VERIFIED_SG, Verification.UNVERIFIED),
                    (self.data.references.get(first.id).verification, self.data.references.get(second.id).verification))
        try:
            self.data.references.set_verification('ref_missing', Verification.REJECTED)
            outcome = 'updated'
        except LookupError as error:
            outcome = str(error)
        self.verify('Verifying an unknown reference is an error', "set_verification('ref_missing', REJECTED)",
                    'references record not found: ref_missing', outcome)
        try:
            self.data.references.set_verification(first.id, 'maybe')
            outcome = 'updated'
        except ValueError as error:
            outcome = str(error)
        self.verify('An invalid verification state is refused', "set_verification(id, 'maybe')",
                    "Invalid verification 'maybe'. Expected one of: unverified, verified_sg, rejected", outcome)
        self.verify('list() returns every reference', 'list()', 2, len(self.data.references.list()))


class AnalysisRepositoryTests(DataCase):
    def setUp(self):
        super().setUp()
        self.owner = self.data.users.create('alice', 'hash')

    def test_lifecycle(self):
        analysis = self.data.analyses.create(self.owner.id, 'contract.txt', 'The contract', 'ctx')
        self.verify('A new analysis starts as processing with a numbered id',
                    "create(owner, 'contract.txt', 'The contract', 'ctx')",
                    ('an_001', AnalysisStatus.PROCESSING, []), (analysis.id, analysis.status, analysis.clauses))
        self.verify('Processing analyses are not served from the cache', 'find_by_hash(owner, hash)',
                    None, self.data.analyses.find_by_hash(
                        self.owner.id, AnalysisRepository.compute_contract_hash('The contract', 'ctx')))
        done = self.data.analyses.complete(analysis.id, [
            ai_clause('Liability', 'high', 'No cap', refs=[('Case', 'https://www.example.org/1')]),
            ai_clause('Renewal', 'LOW', 'Renews')])
        self.verify('complete() saves clauses in order and marks the analysis done',
                    'complete(id, [Liability high (1 reference), Renewal LOW])',
                    (AnalysisStatus.DONE, ['Liability', 'Renewal'], [RiskLevel.HIGH, RiskLevel.LOW],
                     {RiskLevel.HIGH: 1, RiskLevel.MEDIUM: 0, RiskLevel.LOW: 1}),
                    (done.status, [c.clause_type for c in done.clauses], [c.risk_level for c in done.clauses],
                     done.counts()))
        link = done.clauses[0].references[0]
        self.verify('The cited legal reference is stored and reachable from the clause (association)',
                    'done.clauses[0].references[0].reference',
                    ('example.org', 'About Case'), (link.reference.domain, link.snippet))
        cached = self.data.analyses.find_by_hash(
            self.owner.id, AnalysisRepository.compute_contract_hash('The contract', 'ctx'))
        self.verify('A finished analysis is found by contract + context hash', 'find_by_hash(owner, hash)',
                    'an_001', cached.id)
        replaced = self.data.analyses.complete(analysis.id, [ai_clause('Only one')])
        self.verify('Completing again replaces the earlier clauses', 'complete(id, [Only one])',
                    ['Only one'], [c.clause_type for c in replaced.clauses])

    def test_invalid_clause_writes_nothing(self):
        analysis = self.data.analyses.create(self.owner.id, 'c.txt', 'text')
        try:
            self.data.analyses.complete(analysis.id, [ai_clause(), ai_clause(risk='EXTREME')])
            outcome = 'saved'
        except ValueError as error:
            outcome = str(error)
        stored = self.data.analyses.get(analysis.id)
        self.verify('One invalid clause rejects the whole batch (nothing half-saved)',
                    "complete(id, [valid clause, clause with risk 'EXTREME'])",
                    ("Invalid risk_level 'EXTREME'. Expected one of: HIGH, MEDIUM, LOW", AnalysisStatus.PROCESSING, 0),
                    (outcome, stored.status, len(stored.clauses)))

    def test_fail_list_and_filters(self):
        first = self.data.analyses.create(self.owner.id, 'a.txt', 'one')
        second = self.data.analyses.create(self.owner.id, 'b.txt', 'two')
        self.data.analyses.complete(first.id, [ai_clause(risk='HIGH')])
        self.data.analyses.fail(second.id)
        self.verify('list() is newest first and filters by status and risk',
                    'one done (HIGH clause), one failed',
                    (['an_002', 'an_001'], ['an_001'], ['an_002'], ['an_001'], []),
                    ([a.id for a in self.data.analyses.list()],
                     [a.id for a in self.data.analyses.list(status=AnalysisStatus.DONE)],
                     [a.id for a in self.data.analyses.list(status=AnalysisStatus.FAILED)],
                     [a.id for a in self.data.analyses.list(risk_level=RiskLevel.HIGH)],
                     [a.id for a in self.data.analyses.list(risk_level=RiskLevel.LOW)]))
        self.verify("list(owner_id=...) only returns that owner's analyses", "list(owner_id='us_999')",
                    [], self.data.analyses.list(owner_id='us_999'))

    def test_unknown_owner_and_analysis(self):
        outcomes = []
        for action in (lambda: self.data.analyses.create('us_999', 'a.txt', 'text'),
                       lambda: self.data.analyses.fail('an_999')):
            try:
                action()
                outcomes.append('ok')
            except LookupError as error:
                outcomes.append(str(error))
        self.verify('Unknown owner / analysis ids are errors', "create(owner 'us_999'), fail('an_999')",
                    ['users record not found: us_999', 'analyses record not found: an_999'], outcomes)


class CompositionTests(DataCase):
    def test_deleting_an_analysis_deletes_its_parts_but_not_shared_references(self):
        owner = self.data.users.create('alice', 'hash')
        analysis = self.data.analyses.create(owner.id, 'c.txt', 'text')
        self.data.analyses.complete(analysis.id, [
            ai_clause('One', refs=[('Case', 'https://www.example.org/1')]),
            ai_clause('Two', refs=[('Case', 'https://www.example.org/1')])])

        def counts():
            return tuple(run_sql(f'SELECT count(*) FROM {table}').scalar()
                         for table in ('analyses', 'clauses', 'clause_references', 'legal_references'))

        before = counts()
        deleted = self.data.analyses.delete(analysis.id)
        self.verify('Analysis -> Clause -> ClauseReference are deleted together; the shared LegalReference stays',
                    'Rows (analyses, clauses, clause_references, legal_references) before and after delete()',
                    ((1, 2, 2, 1), (0, 0, 0, 1), 'an_001'), (before, counts(), deleted.id))


class TableCreationTests(DataCase):
    def test_init_storage_creates_missing_tables(self):
        Base.metadata.drop_all(_engine)
        before = sql_inspect(_engine).get_table_names()
        counts = self.data.init_storage()
        inspector = sql_inspect(_engine)
        self.verify('init_storage() creates every table that is missing',
                    'Drop all tables, then init_storage()',
                    ([], ['analyses', 'clause_references', 'clauses', 'legal_references', 'users'],
                     {'users': 0, 'analyses': 0, 'references': 0}),
                    (before, sorted(inspector.get_table_names()), counts))
        self.verify('clause_references is keyed by (clause_id, reference_id) and clauses has no category column',
                    'Primary key and columns after the tables are created',
                    (['clause_id', 'reference_id'], False),
                    (inspector.get_pk_constraint('clause_references')['constrained_columns'],
                     'category' in {c['name'] for c in inspector.get_columns('clauses')}))

    def test_existing_data_is_kept(self):
        self.data.users.create('alice', 'hash')
        self.data.init_storage()
        self.verify('Running init_storage() again leaves existing tables and rows alone',
                    'Create one user, then init_storage()', ['alice'], [u.username for u in self.data.users.list()])


if __name__ == '__main__':
    unittest.main()

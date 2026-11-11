"""Evidence that the required OOP concepts are really used by the running code.

Each test case prints its input, the expected result and the actual result, so a person can
check it. Concept -> classes:

  abstract class .... Repository, DocumentExtractor, AlertRule
  inheritance ....... UserRepository / ReferenceRepository / AnalysisRepository, Pdf/Docx/TxtExtractor,
                      ClauseCountAlertRule
  overriding ........ extract(), suffix, get(), list(), matches()
  interface ......... Notifier, ApprovalChannel (realised by TelegramNotifier)
  polymorphism ...... extractors, alert rules and notifiers are used through their base type
  encapsulation ..... private state (_status, _role, ...) changed only through methods
  aggregation ....... AlertPolicy o-- AlertRule, IOManager o-- DocumentExtractor
  composition ....... DataManager creates its repositories (see test_data_layer for Analysis -> Clause)
  association ....... repositories -> Database, ClauseCountAlertRule -> Notifier
"""

import os
import tempfile
import unittest
from pathlib import Path

from docx import Document

from support import ReportedCase  # noqa: F401  (also puts every layer on sys.path)

from alert_policy import AlertPolicy, AlertRule, ClauseCountAlertRule
from analysis_comparer import AnalysisComparer
from document_extractors import DocumentExtractor, DocxExtractor, PdfExtractor, TxtExtractor
from entities import (Analysis, AnalysisStatus, Clause, ClauseReference, LegalReference, RiskLevel, Role,
                      User, Verification)
from notifications import ApprovalChannel, Notifier, TelegramNotifier
from repositories import AnalysisRepository, ReferenceRepository, Repository, UserRepository


def make_pdf(text: str) -> bytes:
    """A minimal one-page PDF containing the text."""
    stream = f'BT /F1 18 Tf 20 100 Td ({text}) Tj ET'.encode()
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 144] /Contents 4 0 R '
        b'/Resources << /Font << /F1 5 0 R >> >> >>',
        b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    output, offsets = b'%PDF-1.4\n', []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += b'%d 0 obj\n' % number + body + b'\nendobj\n'
    xref = len(output)
    output += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)
    output += b''.join(b'%010d 00000 n \n' % offset for offset in offsets)
    output += b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF' % (len(objects) + 1, xref)
    return output


class RecordingNotifier(Notifier):
    """A different Notifier: proves callers only depend on the interface."""

    def __init__(self):
        self.sent = []

    def send(self, message: str, recipient: str) -> None:
        self.sent.append((recipient, message.splitlines()[0]))


def analysis_with(clause_count: int, risk=RiskLevel.HIGH) -> Analysis:
    analysis = Analysis('an_001', 'us_001', 'contract.txt', 'text', '', 'hash')
    analysis.clauses.extend(Clause(position=i, risk_level=risk, clause_type=f'Type {i}',
                                   clause_text=f'Text {i}') for i in range(1, clause_count + 1))
    return analysis


class AbstractAndInterfaceTests(ReportedCase):
    def test_abstract_classes_cannot_be_instantiated(self):
        outcomes = []
        for cls, args in ((Repository, (None,)), (DocumentExtractor, ()), (AlertRule, (RecordingNotifier(), 'x'))):
            try:
                cls(*args)
                outcomes.append((cls.__name__, 'created'))
            except TypeError as error:
                outcomes.append((cls.__name__, 'TypeError'))
        self.verify('Abstract classes refuse to be instantiated',
                    'Repository(), DocumentExtractor(), AlertRule()',
                    [('Repository', 'TypeError'), ('DocumentExtractor', 'TypeError'), ('AlertRule', 'TypeError')],
                    outcomes)

    def test_abstract_methods_are_declared(self):
        self.verify('Abstract methods each class forces its children to implement',
                    'Repository / DocumentExtractor / AlertRule / Notifier / ApprovalChannel .__abstractmethods__',
                    {'Repository': ['get', 'list'], 'DocumentExtractor': ['extract', 'suffix'],
                     'AlertRule': ['matches'], 'Notifier': ['send'],
                     'ApprovalChannel': ['announce', 'request_decision']},
                    {cls.__name__: sorted(cls.__abstractmethods__)
                     for cls in (Repository, DocumentExtractor, AlertRule, Notifier, ApprovalChannel)})

    def test_inheritance(self):
        pairs = [(UserRepository, Repository), (ReferenceRepository, Repository), (AnalysisRepository, Repository),
                 (PdfExtractor, DocumentExtractor), (DocxExtractor, DocumentExtractor),
                 (TxtExtractor, DocumentExtractor), (ClauseCountAlertRule, AlertRule)]
        self.verify('Each concrete class is-a its abstract parent', 'issubclass(child, parent) for 7 pairs',
                    [True] * 7, [issubclass(child, parent) for child, parent in pairs])

    def test_overriding(self):
        self.verify('Each extractor overrides extract() and suffix with its own version',
                    'DocumentExtractor subclasses: own extract / suffix defined in the class body',
                    {'PdfExtractor': ('.pdf', True), 'DocxExtractor': ('.docx', True), 'TxtExtractor': ('.txt', True)},
                    {cls.__name__: (cls().suffix, 'extract' in vars(cls)) for cls in
                     (PdfExtractor, DocxExtractor, TxtExtractor)})

    def test_interface_realisation_and_substitution(self):
        notifier = TelegramNotifier('token')
        self.verify('TelegramNotifier signs both interface contracts (a class with no parent class)',
                    'isinstance(TelegramNotifier(), Notifier / ApprovalChannel)',
                    (True, True), (isinstance(notifier, Notifier), isinstance(notifier, ApprovalChannel)))
        recorder = RecordingNotifier()
        rule = ClauseCountAlertRule(recorder, 'chat-1', 7, 8)
        AlertPolicy([rule]).evaluate(analysis_with(7), User('us_001', 'alice', 'hash'))
        self.verify('Any Notifier can replace Telegram (Liskov substitution)',
                    'AlertPolicy with RecordingNotifier, analysis with 7 flagged clauses',
                    [('chat-1', 'Contract clause analysis completed')], recorder.sent)


class PolymorphismTests(ReportedCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        (root / 'contract.txt').write_text('Plain text contract', encoding='utf-8')
        document = Document()
        document.add_paragraph('Word contract')
        document.save(root / 'contract.docx')
        (root / 'contract.pdf').write_bytes(make_pdf('PDF contract'))
        self.root = root
        self.extractors = [PdfExtractor(), DocxExtractor(), TxtExtractor()]

    def read(self, name):
        path = str(self.root / name)
        for extractor in self.extractors:  # no isinstance, no if-suffix chain
            if extractor.can_handle(path):
                return extractor.extract(path)
        return None

    def test_extractors_are_used_through_the_base_type(self):
        for name, expected in (('contract.txt', 'Plain text contract'), ('contract.docx', 'Word contract'),
                               ('contract.pdf', 'PDF contract')):
            self.verify(f'{name} is read by the matching extractor, chosen without type checks',
                        f'for extractor in extractors: extractor.can_handle({name!r}) -> extract()',
                        expected, self.read(name).strip())
        self.verify('A file type nobody handles is left unread', "can_handle('contract.xyz') for every extractor",
                    None, self.read('contract.xyz'))
        self.verify('File type matching ignores letter case', "PdfExtractor().can_handle('A.PDF')",
                    True, PdfExtractor().can_handle('A.PDF'))

    def test_alert_rules_are_used_through_the_base_type(self):
        class EveryAnalysis(AlertRule):  # a brand-new rule: AlertPolicy needs no change
            def matches(self, total_flagged: int) -> bool:
                return True

        recorder = RecordingNotifier()
        policy = AlertPolicy([ClauseCountAlertRule(recorder, 'medium', 7, 8),
                              ClauseCountAlertRule(recorder, 'high', 9),
                              EveryAnalysis(recorder, 'everyone')])
        owner = User('us_001', 'alice', 'hash')
        results = []
        for count in (3, 7, 8, 9, 12):
            recorder.sent.clear()
            policy.evaluate(analysis_with(count), owner)
            results.append((count, [recipient for recipient, _ in recorder.sent]))
        self.verify('AlertPolicy runs the first matching rule, whatever kind it is (OCP: new rule, no policy change)',
                    'Rules: medium 7-8, high 9+, catch-all. Analyses with 3, 7, 8, 9, 12 flagged clauses',
                    [(3, ['everyone']), (7, ['medium']), (8, ['medium']), (9, ['high']), (12, ['high'])], results)


class AggregationTests(ReportedCase):
    def test_rules_outlive_the_policy(self):
        recorder = RecordingNotifier()
        shared_rule = ClauseCountAlertRule(recorder, 'chat', 7)
        policy_a, policy_b = AlertPolicy([shared_rule]), AlertPolicy([shared_rule])
        del policy_a, policy_b
        self.verify('An AlertRule exists without any AlertPolicy and can be shared (aggregation)',
                    'Two policies share one rule, both policies are deleted, then rule.matches(7)',
                    True, shared_rule.matches(7))

    def test_range_rule(self):
        rule = ClauseCountAlertRule(RecordingNotifier(), 'chat', 7, 8)
        self.verify('ClauseCountAlertRule(7, 8).matches() for 6, 7, 8, 9 flagged clauses',
                    '[6, 7, 8, 9]', [False, True, True, False], [rule.matches(n) for n in (6, 7, 8, 9)])


class EncapsulationTests(ReportedCase):
    def test_status_changes_only_through_methods(self):
        analysis = analysis_with(1)
        try:
            analysis.status = AnalysisStatus.DONE
            outcome = 'assignment allowed'
        except AttributeError:
            outcome = 'AttributeError'
        self.verify('Analysis.status is read-only', 'analysis.status = AnalysisStatus.DONE',
                    ('AttributeError', AnalysisStatus.PROCESSING), (outcome, analysis.status))
        analysis.mark_done([])
        self.verify('mark_done() is the way to change it', 'analysis.mark_done([])',
                    AnalysisStatus.DONE, analysis.status)
        analysis.mark_failed()
        self.verify('mark_failed() likewise', 'analysis.mark_failed()', AnalysisStatus.FAILED, analysis.status)

    def test_private_attributes_use_the_underscore_convention(self):
        self.verify('State with rules is stored in underscore-prefixed attributes',
                    'vars of fresh Analysis / User / Clause / LegalReference objects',
                    {'Analysis._status': 'processing', 'User._role': 'customer',
                     'Clause._risk_level': 'LOW', 'LegalReference._verification': 'unverified'},
                    {'Analysis._status': analysis_with(0)._status, 'User._role': User('us_1', 'alice', 'h')._role,
                     'Clause._risk_level': Clause(1, RiskLevel.LOW)._risk_level,
                     'LegalReference._verification': LegalReference.from_url('https://a.org')._verification})

    def test_entities_enforce_their_own_rules(self):
        cases = []
        for title, action in (
            ('risk level', lambda: RiskLevel.parse('critical')),
            ('status', lambda: AnalysisStatus.parse('bogus')),
            ('username', lambda: User.validated_fields('ab', 'hash', Role.CUSTOMER)),
            ('empty password hash', lambda: User.validated_fields('alice', '  ', Role.CUSTOMER)),
        ):
            try:
                action()
                cases.append((title, 'accepted'))
            except ValueError as error:
                cases.append((title, str(error)))
        self.verify('Invalid values are refused by the entity or enumeration itself',
                    "RiskLevel.parse('critical'), AnalysisStatus.parse('bogus'), username 'ab', blank hash",
                    [('risk level', "Invalid risk_level 'CRITICAL'. Expected one of: HIGH, MEDIUM, LOW"),
                     ('status', "Invalid status 'bogus'. Expected one of: processing, done, failed"),
                     ('username', "Username must be 3-30 characters: letters, numbers, '.', '_' or '-'"),
                     ('empty password hash', 'password_hash must not be empty')], cases)

    def test_reference_verification(self):
        reference = LegalReference.from_url('https://www.example.org/case', 'A case')
        reference.verify(Verification.VERIFIED_SG)
        self.verify('LegalReference builds itself from a URL and changes state through verify()',
                    "LegalReference.from_url('https://www.example.org/case', 'A case').verify(VERIFIED_SG)",
                    ('example.org', 'A case', Verification.VERIFIED_SG),
                    (reference.domain, reference.title, reference.verification))


class ClauseConstructionTests(ReportedCase):
    def test_from_ai_builds_clause_and_collapses_repeated_references(self):
        drafts = {}
        raw = {'clause_type': 'Liability', 'risk_level': ' high ', 'clause_text': 'Text',
               'line_number': 'Page 3, Clause 2(a)',
               'legal_references': [
                   {'title': 'Case', 'url': 'https://www.example.org/1', 'summary': 'First'},
                   {'title': 'Case again', 'url': 'https://www.example.org/1', 'summary': 'Duplicate'},
                   {'title': 'No url'}, 'junk']}
        clause = Clause.from_ai(1, raw, drafts)
        self.verify('Clause.from_ai parses risk, location and references (one clause cites a URL once)',
                    str(raw),
                    (RiskLevel.HIGH, 3, '2(a)', ['First'], ['example.org']),
                    (clause.risk_level, clause.location_page, clause.location_clause_ref,
                     [link.snippet for link in clause.references], [r.domain for r in drafts.values()]))
        try:
            Clause.from_ai(2, 'not an object', {})
            outcome = 'accepted'
        except ValueError as error:
            outcome = str(error)
        self.verify('A clause that is not an object is rejected', "Clause.from_ai(2, 'not an object', {})",
                    'Clause 2 is not an object', outcome)


class ComparerTests(ReportedCase):
    def test_compare_in_memory(self):
        def analysis(identifier, clauses):
            result = Analysis(identifier, 'us_001', 'contract.txt', 'same text', '', 'hash')
            for position, (kind, level, text) in enumerate(clauses, start=1):
                result.clauses.append(Clause(position, level, clause_type=kind, clause_text=text))
            return result

        first = analysis('an_001', [('Liability', RiskLevel.HIGH, 'All damages'),
                                    ('Renewal', RiskLevel.LOW, 'Renews yearly')])
        second = analysis('an_002', [('Liability', RiskLevel.LOW, 'all  damages'),
                                      ('Renewal', RiskLevel.HIGH, 'Auto renew'),
                                      ('Termination', RiskLevel.MEDIUM, 'Either side may end')])
        result = AnalysisComparer().compare(first, second)
        self.verify('Compare pairs clauses by text first, then by type, and reports risk changes',
                    'an_001 [Liability HIGH, Renewal LOW] vs an_002 [Liability LOW (same text, extra spaces), '
                    'Renewal HIGH (different text), Termination MEDIUM]',
                    [('Liability', 'text', 'lower'), ('Renewal', 'type', 'higher')],
                    [(m.first.clause_type, m.matched_on, m.risk_change) for m in result.in_both])
        self.verify('Clauses found on one side only', 'only_first / only_second',
                    ([], ['Termination']),
                    ([c.clause_type for c in result.only_first], [c.clause_type for c in result.only_second]))
        same, others = AnalysisComparer().split_by_file(first, [first, second, analysis('an_003', [])])
        self.verify('split_by_file ignores the analysis itself and groups by file name',
                    'candidates an_001 (itself), an_002, an_003, all named contract.txt',
                    (['an_002', 'an_003'], []), ([a.id for a in same], [a.id for a in others]))


if __name__ == '__main__':
    unittest.main()

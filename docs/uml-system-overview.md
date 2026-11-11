# System design: IO, AI, Logic, Notifications (matches the code)

Same notation as `uml-data-layer.md`. `<<interface>>` = ABC with abstract methods only; it is realised with a dashed
line and hollow triangle (`<|..`). The 13 route handlers are listed in the table at the end.

## D. Overview: the four managers and how they connect

```mermaid
classDiagram
    direction TB

    namespace IO_Manager {
        class IOManager
        class DocumentExtractor {
            <<abstract>>
        }
        class PdfExtractor
        class DocxExtractor
        class TxtExtractor
    }
    namespace Logic_Manager {
        class Application
        class AuthService
        class AnalysisService
        class AnalysisComparer
        class AlertPolicy
        class AlertRule {
            <<abstract>>
        }
        class ClauseCountAlertRule
    }
    namespace AI_Manager {
        class AIManager
        class RecoverySession
        class FirecrawlResearcher
    }
    namespace Notifications {
        class Notifier {
            <<interface>>
        }
        class ApprovalChannel {
            <<interface>>
        }
        class TelegramNotifier
    }
    namespace Data_Manager {
        class DataManager
    }

    Application ..> IOManager : builds
    IOManager --> AuthService
    IOManager --> AnalysisService
    IOManager --> AnalysisComparer
    IOManager --> DataManager
    IOManager "1" o-- "1..*" DocumentExtractor : extractors
    DocumentExtractor <|-- PdfExtractor
    DocumentExtractor <|-- DocxExtractor
    DocumentExtractor <|-- TxtExtractor

    AuthService --> DataManager
    AnalysisService --> DataManager
    AnalysisService --> AIManager
    AnalysisService --> FirecrawlResearcher
    AnalysisService --> AlertPolicy
    AnalysisService ..> RecoverySession : creates
    AlertPolicy "1" o-- "0..*" AlertRule : rules
    AlertRule <|-- ClauseCountAlertRule
    AlertRule --> Notifier

    AIManager --> ApprovalChannel
    AIManager ..> RecoverySession
    Notifier <|.. TelegramNotifier
    ApprovalChannel <|.. TelegramNotifier
```

## E. IO Manager

```mermaid
classDiagram
    direction TB

    class IOManager {
        -_flask: Flask
        -_auth: AuthService
        -_analyses: AnalysisService
        -_comparer: AnalysisComparer
        -_data: DataManager
        -_extractors: list~DocumentExtractor~
        +flask_app: Flask
        +register() Response
        +login() Response
        +analyze() Response
        +list_analyses() Response
        +compare() Response
        +health() Response
        -_extract_text(path: str) str
        -_analysis_view(analysis: Analysis, cached: bool) dict
        -_clause_view(clause: Clause) dict
        -_session_user() User | None
        -_login_required(view: Callable) Callable
        -_admin_required(view: Callable) Callable
        -_analysis_error_response(error: Exception) Response
    }

    class DocumentExtractor {
        <<abstract>>
        +suffix: str*
        +can_handle(path: str) bool
        +extract(path: str)* str
    }

    class PdfExtractor {
        +suffix: str
        +extract(path: str) str
    }
    class DocxExtractor {
        +suffix: str
        +extract(path: str) str
    }
    class TxtExtractor {
        +suffix: str
        +extract(path: str) str
    }

    IOManager "1" o-- "1..*" DocumentExtractor : extractors
    DocumentExtractor <|-- PdfExtractor
    DocumentExtractor <|-- DocxExtractor
    DocumentExtractor <|-- TxtExtractor
```

## F. Logic Manager

```mermaid
classDiagram
    direction TB

    class Application {
        +build()$ Flask
    }

    class AuthError {
        +reason: str
    }

    class AuthService {
        -_data: DataManager
        +register(username: str, password: str) User
        +authenticate(username: str, password: str) User
        +seed_admin_from_env() None
    }

    class AnalysisService {
        -_data: DataManager
        -_ai: AIManager
        -_researcher: FirecrawlResearcher
        -_alerts: AlertPolicy
        +analyze(owner: User, filename: str, contract_text: str, context: str) tuple~Analysis, bool~
        +recover_interrupted() int
        +health_report() dict
        -_run_pipeline(contract_text: str, context: str) list~dict~
    }

    class AnalysisComparer {
        +compare(first: Analysis, second: Analysis) Comparison
        +split_by_file(analysis: Analysis, candidates: list~Analysis~) tuple
        +is_older(first: Analysis, second: Analysis)$ bool
    }

    class Comparison {
        +first: Analysis
        +second: Analysis
        +in_both: list~ClauseMatch~
        +only_first: list~Clause~
        +only_second: list~Clause~
        +same_contract: bool
    }

    class ClauseMatch {
        +first: Clause
        +second: Clause
        +matched_on: str
        +risk_change: str
    }

    class AlertPolicy {
        -_rules: list~AlertRule~
        +evaluate(analysis: Analysis, owner: User) None
    }

    class AlertRule {
        <<abstract>>
        -_notifier: Notifier
        -_recipient: str
        +matches(total_flagged: int)* bool
        +notify(analysis: Analysis, owner: User) None
    }

    class ClauseCountAlertRule {
        -_min_flagged: int
        -_max_flagged: int | None
        +matches(total_flagged: int) bool
    }

    class Notifier { <<interface>> }
    class DataManager
    class AIManager
    class FirecrawlResearcher

    AuthService --> DataManager
    AuthService ..> AuthError : raises
    AnalysisService --> DataManager
    AnalysisService --> AIManager
    AnalysisService --> FirecrawlResearcher
    AnalysisService --> "1" AlertPolicy
    AnalysisComparer ..> Comparison : returns
    Comparison "1" *-- "0..*" ClauseMatch : in_both
    AlertPolicy "1" o-- "0..*" AlertRule : rules
    AlertRule <|-- ClauseCountAlertRule
    AlertRule "0..*" --> "1" Notifier
```

## G. AI Manager and notifications

```mermaid
classDiagram
    direction TB

    class AIManager {
        -_api_key: str | None
        -_models: list~str~
        -_max_retries: int
        -_approval: ApprovalChannel
        +primary_model: str
        +is_configured: bool
        +detect_clauses(contract_text: str, context: str, recovery: RecoverySession) list~dict~
        +improve_workarounds(clauses: list~dict~, recovery: RecoverySession) list~dict~
        +probe_models(limit: int) tuple~list~str~, list~dict~~
        -_call_with_failover(messages: list~dict~, recovery: RecoverySession) str
        -_classify_error(error: Exception)$ str
        -_parse_json_array(response: str)$ list | None
        -_validated_clauses(items: list | None)$ list~dict~
    }

    class RecoverySession {
        -_notified: bool
        -_manual_model: str | None
        +manual_model: str | None
        +mark_notified() None
        +was_notified() bool
        +approve(model: str) None
    }

    class FirecrawlResearcher {
        -_api_key: str | None
        +is_configured: bool
        +research(clause: dict) list~dict~
    }

    class Notifier {
        <<interface>>
        +send(message: str, recipient: str)* None
    }

    class ApprovalChannel {
        <<interface>>
        +request_decision(prompt: str)* str
        +announce(message: str)* None
    }

    class TelegramNotifier {
        -_token: str | None
        -_group_chat_id: str
        +from_env()$ TelegramNotifier
        +send(message: str, recipient: str) None
        +request_decision(prompt: str) str
        +announce(message: str) None
        -_latest_update_id() int
        -_send_error_alert(error_message: str) None
        -_wait_for_reply(last_update_id: int) tuple~str, int~
    }

    AIManager --> "1" ApprovalChannel
    AIManager ..> RecoverySession
    Notifier <|.. TelegramNotifier
    ApprovalChannel <|.. TelegramNotifier
```

## Design decisions (and why)

| Decision | Reason (rubric / lab) |
|---|---|
| `DocumentExtractor` abstract with `Pdf/Docx/Txt` subclasses | Replaces the `if suffix == ...` chain: abstract class + inheritance + overriding + polymorphism + OCP (a new format = a new subclass) |
| `IOManager o-- DocumentExtractor` | Extractors are created outside and injected: parts live independently of the whole (aggregation) |
| `AlertPolicy o-- AlertRule` | Rules exist without the policy and can be shared or swapped: aggregation. `AlertPolicy` only calls `rule.matches()` / `rule.notify()`: polymorphism, no type branching |
| `Notifier` and `ApprovalChannel` interfaces; `TelegramNotifier` realises both | "Can-do" capabilities, not "is-a": a class with no parent plus two interfaces (Lab 7). Telegram's Fix / Stop flow is unchanged, inside `request_decision()` |
| `RecoverySession` replaces the `recovery_state` dict | An object with private state and methods instead of a loose dict |
| Managers receive collaborators in the constructor | Association + dependency injection: loose coupling; tests pass fakes (no live AI) |
| `Application.build()` wires the object graph | One place knows the concrete classes; modules have no side effects when imported |
| `AuthService`, `AnalysisService`, `AnalysisComparer` split | SRP: login rules, the analysis pipeline and the compare feature change for different reasons |
| `Comparison` / `ClauseMatch` value objects | The compare feature returns typed results; `IOManager` only turns them into JSON |
| `FlaggedClause` dataclass dropped | Unused; `Clause.from_ai()` and `AIManager._validated_clauses()` validate AI output |

## Route handlers in `IOManager` (URLs and JSON unchanged)

| Method + path | Handler |
|---|---|
| POST `/api/auth/register`, POST `/api/auth/login`, GET `/api/auth/me`, POST `/api/auth/logout` | `register`, `login`, `current_user`, `logout` |
| POST `/api/analyze` | `analyze` |
| GET `/api/analyses`, GET / DELETE `/api/analyses/<id>` | `list_analyses`, `get_analysis`, `delete_analysis` |
| GET `/api/compare`, GET `/api/analyses/<id>/compare-options` | `compare`, `compare_options` |
| GET `/api/admin/users`, GET `/api/admin/analyses` | `admin_users`, `admin_analyses` |
| GET `/api/health` | `health` |

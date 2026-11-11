# Data layer: class diagrams (draft for review)

Notation: `-` private, `+` public, `#` protected, italics / `<<abstract>>` abstract, `*--` composition,
`o--` aggregation, `-->` association, `..>` dependency, `<|--` inheritance.
Entities are the domain objects. FK columns are persistence details, shown only in the ER view.
Diagram A shows the services (managers and repositories); diagram B shows the entities.

## A. Data Manager and repositories

```mermaid
classDiagram
    direction TB

    class DataManager {
        -_database: Database
        -_users: UserRepository
        -_references: ReferenceRepository
        -_analyses: AnalysisRepository
        +users: UserRepository
        +references: ReferenceRepository
        +analyses: AnalysisRepository
        +from_env()$ DataManager
        +init_storage() dict~str, int~
        +counts() dict~str, int~
        +close() None
    }

    class Database {
        -_url: str
        -_engine: Engine
        +session() Session
        +ping() bool
        +schema_revision() str | None
        +dispose() None
    }

    class Repository~T~ {
        <<abstract>>
        #_database: Database
        +get(entity_id: str)* T | None
        +list()* list~T~
    }

    class UserRepository {
        +create(username: str, password_hash: str, role: Role) User
        +find_by_username(username: str) User | None
        +get(entity_id: str) User | None
        +list() list~User~
    }

    class ReferenceRepository {
        +add_many(references: list~LegalReference~) list~LegalReference~
        +set_verification(ref_id: str, verification: Verification) LegalReference
        +get(entity_id: str) LegalReference | None
        +list() list~LegalReference~
    }

    class AnalysisRepository {
        -_references: ReferenceRepository
        +create(owner_id: str, filename: str, contract_text: str, context: str) Analysis
        +complete(analysis_id: str, ai_clauses: list~dict~) Analysis
        +fail(analysis_id: str) Analysis
        +delete(analysis_id: str) Analysis
        +find_by_hash(owner_id: str, contract_hash: str) Analysis | None
        +compute_contract_hash(contract_text: str, context: str)$ str
        +get(entity_id: str) Analysis | None
        +list(owner_id: str | None, status: AnalysisStatus | None, risk_level: RiskLevel | None) list~Analysis~
    }

    class User
    class LegalReference
    class Analysis

    DataManager "1" *-- "1" UserRepository : creates
    DataManager "1" *-- "1" ReferenceRepository : creates
    DataManager "1" *-- "1" AnalysisRepository : creates
    DataManager --> "1" Database
    Repository <|-- UserRepository
    Repository <|-- ReferenceRepository
    Repository <|-- AnalysisRepository
    Repository --> "1" Database
    AnalysisRepository --> "1" ReferenceRepository
    UserRepository ..> User
    ReferenceRepository ..> LegalReference
    AnalysisRepository ..> Analysis
```

## B. Entities

```mermaid
classDiagram
    direction TB

    class User {
        +id: str
        +username: str
        +password_hash: str
        +created_at: datetime
        -_role: Role
        +role: Role
        +created_at_iso: str
        +is_admin() bool
        +normalise_username(username: str)$ str
        +validated_fields(username: str, password_hash: str, role: Role)$ dict
    }

    class Analysis {
        +id: str
        +owner_id: str
        +filename: str
        +context: str
        +contract_text: str
        +contract_hash: str
        +created_at: datetime
        -_status: AnalysisStatus
        +status: AnalysisStatus
        +created_at_iso: str
        +mark_done(clauses: list~Clause~) None
        +mark_failed() None
        +discard_clauses() None
        +counts() dict~RiskLevel, int~
        +is_owned_by(user_id: str) bool
        +has_status(status: AnalysisStatus)$ Criterion
    }

    class Clause {
        +position: int
        +clause_type: str
        +clause_text: str
        +location_page: int | None
        +location_clause_ref: str | None
        +location_raw: str | None
        +issue_description: str
        +workaround: str
        -_risk_level: RiskLevel
        +risk_level: RiskLevel
        +from_ai(position: int, raw: dict, references: dict~str, LegalReference~)$ Clause
        +has_risk_level(level: RiskLevel)$ Criterion
    }

    class ClauseReference {
        +position: int
        +snippet: str
    }

    class LegalReference {
        +id: str
        +url: str
        +title: str
        +domain: str
        -_verification: Verification
        +verification: Verification
        +verify(state: Verification) None
        +from_url(url: str, title: str)$ LegalReference
    }

    class Role {
        <<enumeration>>
        CUSTOMER
        ADMIN
    }
    class AnalysisStatus {
        <<enumeration>>
        PROCESSING
        DONE
        FAILED
    }
    class RiskLevel {
        <<enumeration>>
        HIGH
        MEDIUM
        LOW
        +label: str
        +rank: int
    }
    class Verification {
        <<enumeration>>
        UNVERIFIED
        VERIFIED_SG
        REJECTED
    }

    User "1" -- "0..*" Analysis : owns
    Analysis "1" *-- "0..*" Clause : has
    Clause "1" *-- "0..*" ClauseReference : cites
    ClauseReference "0..*" --> "1" LegalReference : reference

    User ..> Role
    Analysis ..> AnalysisStatus
    Clause ..> RiskLevel
    LegalReference ..> Verification
```

## Design decisions (and why)

| Decision | Reason (rubric / lab) |
|---|---|
| `Repository<T>` abstract with abstract `get` / `list`, subclasses override | Abstract class + inheritance + overriding; the three repositories share one shape |
| `DataManager` composes the three repositories | It creates them itself (composition, whole end `1`) |
| `Analysis ◆ Clause ◆ ClauseReference` | Cascade delete in the schema: parts die with the whole |
| `ClauseReference --> LegalReference` is an association | Shared references survive when an analysis is deleted |
| `User -- Analysis` is an association | A stored FK is not ownership of the lifecycle (Lab 8 trap) |
| `status`, `role`, `verification`, `risk_level` private + read-only property | Encapsulation: changed only through `mark_done()`, `verify()` ... which enforce the rules |
| `LegalReference.from_url`, `Clause.from_ai` | Removes the `ClauseParser -> ReferenceRepository` coupling; each class builds itself (SRP) |
| Enumerations instead of tuples of strings | Type hints show the allowed values; the UML shows them |
| Repositories return entities, not `dict` | Diagram and code correlate both ways |
| `Clause.category` removed | Never populated by the AI prompt |
| `ClauseReference` key `(clause_id, reference_id)` | Chosen; `position` stays an ordinary column; one clause citing the same URL twice is collapsed in `Clause.from_ai` |

## C. ER view (report "data storage design" section)

```mermaid
erDiagram
    USERS ||--o{ ANALYSES : owner_id
    ANALYSES ||--o{ CLAUSES : analysis_id
    CLAUSES ||--o{ CLAUSE_REFERENCES : clause_id
    LEGAL_REFERENCES ||--o{ CLAUSE_REFERENCES : reference_id
    USERS {
        text id PK
        text username UK
        text password_hash
        text role
        timestamptz created_at
    }
    ANALYSES {
        text id PK
        text owner_id FK
        text status
        text filename
        text context
        text contract_text
        text contract_hash
        timestamptz created_at
    }
    CLAUSES {
        int id PK
        text analysis_id FK
        int position
        text clause_type
        text risk_level
        text clause_text
        int location_page
        text location_clause_ref
        text location_raw
        text issue_description
        text workaround
    }
    CLAUSE_REFERENCES {
        int clause_id PK
        text reference_id PK
        int position
        text snippet
    }
    LEGAL_REFERENCES {
        text id PK
        text url UK
        text title
        text domain
        text verification
    }
```

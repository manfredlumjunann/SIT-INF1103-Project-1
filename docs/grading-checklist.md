# Grading checklist (derived from Labs 6-8, the Phase 2 brief and the rubric)

Sources: Lab 6 (classes, encapsulation, SRP), Lab 7 (inheritance, overriding, abstract, interfaces, OCP),
Lab 8 (association/aggregation/composition, type hints, LSP, polymorphism), Phase 2 brief and rubric.
Lab 9 (error handling + a design pattern) and later labs were not provided, so are not covered.

## 1. Rubric weights

| Criterion | Weight | "Competent" bar | "Proficient" bar |
|---|---|---|---|
| OOP design and required concepts | 25% | Every required concept works in the running system | Concepts fit naturally and give a coherent, maintainable design |
| Refactoring and modular architecture | 15% | IO / AI / Logic / Data managers identifiable, separated, with functional interfaces | Decoupled, focused responsibilities, clear contracts, convincing Phase 1 -> Phase 2 refactor |
| New feature and SOLID | 20% | One useful new feature; >= 3 SOLID principles explained **with code evidence** incl. SRP, OCP, LSP | Feature integrated and tested; principles measurably improve extensibility |
| Testing and robustness | 10% | Automated tests for core logic + new feature, **no live AI**, failures handled | Edge cases, robust error handling, reproducible |
| DevOps | 15% | Meaningful Git history, working Docker, CI that runs the tests | Incremental history, reliable container, green CI, reproducible docs |
| Documentation / TeamAId | 10% | UML, data flow, exception handling, OOP mapping, SOLID justification; steady TeamAId progress | Concise, design choices tied to code and domain |
| Advanced feature | 5% | Implemented and integrated (this project: Database integration) | Well integrated, tested, adds real value |

Peer evaluation is a 20% modifier. Report: max 6 pages. "No procedural style code left" is stated in the brief.

## 2. Mandatory OOP concepts and what they look for

| Concept | What the marker checks | Check in this project |
|---|---|---|
| Classes / objects | Several purposeful classes, instantiated by the running app. Not functions wrapped in classes | `logic-layer/app.py` is ~1,160 lines of module-level functions: fails |
| Encapsulation | Private attrs `__x` / `_x` (UML `-`), changed only through public methods, `+` only for the real public API | Repositories use `_db`; entity columns are public by ORM design |
| Abstract class | Shared behaviour **and** at least one `@abstractmethod`; cannot be instantiated; has `<<abstract>>` in UML | None yet |
| Inheritance | Only when "X is-a Y" reads correctly; child calls `super().__init__`; child reuses parent code via `super()` instead of copying | Only `Base(DeclarativeBase)` (framework, not meaningful) |
| Method overriding | Child gives its own version of a parent method; extends with `super().method()` rather than duplicating | None yet |
| Interface (ABC) | "can-do" capability (CanBeShipped style): ABC with only abstract methods, no attributes; class may implement several; keep **single** class inheritance | None yet |
| Association | Objects know / use each other, no ownership, can exist independently; usually passed in via the constructor or a parameter | Repository -> Database / ClauseParser / ReferenceRepository |
| Aggregation | Whole holds references to parts that can exist alone, be removed, moved or shared. Whole end is `0..1` / `0..*` | Needs a real example |
| Composition | Whole **creates** and owns the part, lifecycle tied, whole end is exactly `1` (filled diamond); parts never transferred | Analysis -> Clause -> ClauseReference; DataManager -> repositories |
| Polymorphism | Client uses the base type and calls the method; **no `isinstance` / type branching** | None yet |

## 3. "When do I use X" decision guide

| Use this | When | Do not use when |
|---|---|---|
| Inheritance | Several kinds share data and behaviour and the sentence "Child **is-a** Parent" is true (`PhysicalProduct` is-a `Product`) | You only want to reuse code (use composition); the child must reject something the parent accepts (LSP) |
| Abstract class | Parent is incomplete on its own, has shared state / concrete methods, and every child **must** supply some behaviour (`Product.get_fulfilment_info()`) | Nothing is shared; no required method; you only need a contract (use an interface) |
| `@abstractmethod` | Each child must implement it differently and the parent cannot sensibly implement it | The parent has a sensible default (use a normal method children may override) |
| Interface (ABC, no state) | A capability that unrelated classes may offer: "**must-be-able-to**" (`CanBeShipped`) | The relationship is "is-a"; the contract needs attributes |
| Method overriding + `super()` | Child extends common behaviour (`display_information`): parent does the shared part, child adds its own | Copying the parent's body into the child |
| Association | Objects collaborate but neither owns the other (Supplier - Product, OrderLine -> Product) | One object is part of another |
| Aggregation | "Has-a", parts live on if the whole is removed (Inventory - Product) | The part is meaningless without the whole |
| Composition | The whole creates the part and the part dies with it (Order - OrderLine) | The part is created elsewhere and passed in; it is shared or moved |
| Polymorphism | Loop over a base type and call a method (`for p in products: p.display_information()`) | You are tempted to write `if isinstance(...)` |

Common traps the labs warn about:
- Any stored reference is **not** automatically aggregation or composition. `supplier: Supplier` is an association.
- Composition: the whole must create the part (`add_order_line(product, qty)` builds the `OrderLine`), not receive a ready-made one.
- Do not use inheritance just to reuse code, and avoid multiple inheritance (one parent plus several interfaces is fine).
- Do not add `isinstance` checks to the manager classes to work around a badly designed subclass.
- A subclass that raises `NotImplementedError` for an inherited method violates LSP: change the hierarchy, not the caller.
- Do not use `Any` to avoid choosing a type.

## 4. SOLID: required evidence (SRP, OCP, LSP at minimum)

| Principle | Evidence the marker wants | Typical failure |
|---|---|---|
| SRP | One stated responsibility per class (Product manages a product, Inventory manages the collection, main does menu/IO; no file I/O, input or menu in domain classes) | A class or module that does routes + auth + AI + Telegram + persistence |
| OCP | New type added **without editing** existing classes (new `SubscriptionProduct`; new AI provider; new notifier) | `if type == ...` chains that grow with each feature |
| LSP | Subclasses usable wherever the parent is expected; same meaning of inherited methods; no stricter inputs | Subclass raises `NotImplementedError`; callers check the subclass |

Optional extras that earn "Proficient": ISP (small interfaces) and DIP (managers depend on abstractions, injected).

## 5. Design rules from the labs

1. Identify classes from the **nouns** of the scenario; give each **one** responsibility; state it.
2. Attributes private by convention (`__name` / `_name`), changed through public methods that validate.
3. Constructors use `super().__init__()` and take **objects** (e.g. a `Supplier`), not ids or names, for associations.
4. Type hints everywhere: object references, `list[Product]` (base type), parameters, returns (`-> None`, `Product | None`). The UML must show the same types.
5. Manager classes talk to the base type only (`list[Product]`, never separate lists per subclass).
6. File handling (load/save) stays out of domain classes.
7. Return `None` when something is not found rather than crashing.

## 6. UML expectations (Lab 7 + Lab 8)

- `<<abstract>>` (italic name) for abstract classes and abstract methods; `<<interface>>` for ABC contracts.
- Generalisation: solid line, hollow triangle at the parent. Realisation: dashed line, hollow triangle at the interface.
- Association: plain line (optional arrow). Aggregation: hollow diamond at the whole. Composition: filled diamond at the whole.
- Visibility `+` / `-`, attribute types, parameter types, return types, multiplicities (`1`, `0..1`, `0..*`, `1..*`).
- Multiplicity rules: association unrestricted; aggregation whole end `0..1` or `0..*`; composition whole end exactly `1`.
- Diagram and code must correlate **both ways** (report guideline). The UML must describe design, not just mirror ORM columns.
- Include: all four managers, class diagram with relationships, data flow diagram, exception handling matrix.

## 7. Testing, DevOps and report checklist

- Automated tests: core business rules, scoring / routing, **AI response validation**, new feature, failure cases (malformed AI output, missing / corrupt data, DB down).
- Tests run **without** a live AI API (mocks / stubs) and pass inside Docker; documented run command.
- Git: incremental, descriptive commits showing Phase 1 -> OOP refactor -> feature -> tests -> pipeline; no secrets in the repo.
- Docker: root Dockerfile; documented env vars; app and tests run in the container.
- CI: GitHub Actions workflow that runs the tests, green on the submitted commit.
- Report (<= 6 pages): UML, data flow diagram, data storage design, exception matrix, OOP concept mapping, SOLID evidence, new + advanced feature notes (problem, access, classes, tests).
- Presentation (<= 15 min): every member speaks; run from Docker; show new + advanced features and key tests.
- TeamAId: tasks added and updated chronologically (not all on the last day); CSV exported separately.

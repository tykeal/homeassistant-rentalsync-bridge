<!--
Sync Impact Report - Constitution v1.0.0
========================================
Version Change: INITIAL → 1.0.0
Created: 2026-01-10
Ratification: 2026-01-10

Principles Defined:
  I. Code Quality & Maintainability
  II. Testing Standards
  III. User Experience Consistency
  IV. Performance Requirements
  V. Commit Discipline & Pre-Commit Integrity

Sections Added:
  - Core Principles (5 principles)
  - Development Workflow
  - Governance

Templates Status:
  ✅ plan-template.md - Constitution Check section updated with specific gates
  ✅ spec-template.md - Already aligned (Success Criteria section present)
  ✅ tasks-template.md - Notes section updated with commit discipline requirements
  ✅ checklist-template.md - Compatible with commit discipline (no changes needed)
  ✅ agent-file-template.md - Generic guidance compatible (no changes needed)

Follow-up Items:
  - None - all placeholders resolved and templates synchronized

Notes:
  This is the initial constitution ratification. All principles are
  declarative, testable, and enforce strict discipline around commits,
  pre-commit hooks, licensing, and agent co-authorship. The constitution
  directly references pre-commit configuration tools already in place
  (reuse, ruff, mypy, interrogate, yamllint, actionlint, gitlint).
-->

# RentalSync Bridge Constitution

## Core Principles

### I. Code Quality & Maintainability

All code MUST meet the following non-negotiable quality standards:

- **SPDX License Headers**: Every new or modified source file MUST include correct
  SPDX license identifier and copyright headers as defined in REUSE.toml
- **Code Style Enforcement**: All code MUST pass configured linters and formatters
  (ruff, mypy, interrogate, yamllint, actionlint) with zero violations
- **Documentation Requirements**: All public APIs, functions, and classes MUST
  have complete docstrings (100% interrogate coverage required)
- **Type Safety**: Python code MUST include type hints and pass mypy strict checks
- **Clean Code**: Code MUST be readable, self-documenting, and follow DRY
  (Don't Repeat Yourself) principles

**Rationale**: Maintainability is achieved through consistency. Automated
enforcement via pre-commit hooks eliminates ambiguity and reduces cognitive load
during code review. SPDX headers ensure license compliance and clear ownership.

### II. Testing Standards

Testing MUST follow a disciplined, comprehensive approach:

- **Test Coverage**: All new features MUST include appropriate test coverage at
  contract, integration, or unit test levels as determined by feature complexity
- **Test-First Discipline**: When tests are included in a feature specification,
  tests MUST be written first, verified to fail, then implementation proceeds
- **Red-Green-Refactor**: Follow strict TDD cycle where applicable - failing
  test → minimal implementation → passing test → refactor
- **Integration Tests**: Features involving external APIs, inter-service
  communication, or critical user journeys MUST include integration tests
- **Contract Tests**: New or modified API contracts MUST include contract tests

**Rationale**: Quality is not negotiable. Test-first practices catch design
flaws early and ensure testability. Comprehensive testing at appropriate levels
provides confidence during refactoring and prevents regressions.

### III. User Experience Consistency

All user-facing interfaces MUST provide consistent, predictable experiences:

- **Error Messages**: User-facing errors MUST be clear, actionable, and
  consistent in format across the application
- **API Consistency**: All API endpoints MUST follow consistent naming
  conventions, response formats, and error structures
- **Input Validation**: All user inputs MUST be validated with clear feedback
  on validation failures
- **Response Times**: User-facing operations MUST provide feedback within
  expected performance requirements (see Principle IV)
- **Documentation**: All user-facing features MUST be documented with clear
  examples and edge case handling

**Rationale**: Consistent UX reduces user frustration and support burden.
Predictable error messages and API behavior enable faster debugging and
integration. Clear validation feedback prevents user errors.

### IV. Performance Requirements

Performance characteristics MUST be defined and measured:

- **Performance Goals**: Every feature specification MUST include measurable
  performance goals appropriate to the domain (e.g., response time, throughput,
  memory usage)
- **Performance Testing**: Features with explicit performance requirements MUST
  include performance validation tests
- **Regression Prevention**: Performance-critical paths MUST be monitored to
  detect regressions before production deployment
- **Resource Constraints**: Features MUST respect defined resource constraints
  (memory, CPU, network) and fail gracefully when limits are reached
- **Scalability Considerations**: Features MUST document scaling behavior and
  known limits

**Rationale**: Performance problems discovered in production are expensive to
fix. Defining and testing performance requirements early ensures features meet
user expectations and prevents architectural dead-ends.

### V. Commit Discipline & Pre-Commit Integrity (NON-NEGOTIABLE)

Commit discipline MUST be strictly enforced without exception:

- **Atomic Commits**: Every commit MUST represent exactly one logical change.
  Multiple unrelated changes MUST be split into separate commits
- **Pre-Commit Hooks**: All pre-commit hooks MUST pass locally before any push.
  Bypassing hooks (--no-verify) is PROHIBITED under all circumstances
- **Hook Failure Protocol**: If pre-commit hooks fail, the commit MUST be
  redone (not amended) as if the original commit never existed
- **Commit Subject Format**: Commit subject lines MUST NOT exceed 50 characters.
  Semantic commit types MUST be capitalized (Feat, Docs, CI, Fix, Chore, Style,
  Refactor, Perf, Test, Revert, Build)
- **Agent Co-Authorship**: All commits authored by AI agents MUST include a
  'Co-Authored-By' trailer line identifying the agent and an appropriate email
  address (e.g., "Co-Authored-By: Claude <claude@anthropic.com>")
- **DCO Sign-Off**: All commits MUST include Developer Certificate of Origin
  sign-off via `git commit -s` (Signed-off-by trailer)
- **Checklist Item Commits**: Each completed checklist item MUST be committed
  in its own separate commit, followed by a separate "Docs:" commit that updates
  the checklist itself

**Rationale**: Commit discipline enables precise history navigation, clean
reverts, and clear attribution. Pre-commit integrity ensures that broken or
non-compliant code never enters version control. Atomic commits make code review
effective and enable confident rollbacks. Agent attribution provides transparency
in AI-assisted development. DCO sign-off establishes legal clarity.

## Development Workflow

The development workflow enforces constitution compliance at every stage:

### Code Change Process

1. **Branch Creation**: Feature branches MUST follow naming convention
   `###-feature-name` where ### is a unique identifier
2. **Implementation**: Follow task checklist from tasks.md, completing items in
   dependency order
3. **Commit Protocol**:
   - Write change in working directory
   - Stage changes: `git add <files>`
   - Commit with sign-off: `git commit -s`
   - Pre-commit hooks run automatically
   - If hooks fail: unstage, fix issues, stage, commit again (never amend)
   - If hooks pass: commit is accepted
4. **Checklist Updates**: After completing a checklist item:
   - Commit the implementation changes
   - Update the checklist file (mark item complete)
   - Commit checklist update separately with "Docs:" prefix
5. **Push**: `git push` only after all pre-commit validations pass

### Quality Gates

Before any code review or merge:

- ✅ All pre-commit hooks pass (reuse, ruff, mypy, interrogate, yamllint, etc.)
- ✅ All commits include proper SPDX headers on modified source files
- ✅ All commits include DCO sign-off
- ✅ Agent commits include Co-Authored-By trailer
- ✅ Commit messages follow 50-character subject limit and semantic format
- ✅ Tests pass (if feature includes tests)
- ✅ Documentation updated (if user-facing changes)

### Code Review Requirements

Reviewers MUST verify:

- Constitution compliance (all principles followed)
- Commit discipline (atomic commits, proper formatting)
- License headers present on all modified source files
- Test coverage appropriate for feature complexity
- Performance requirements defined and validated (if applicable)
- UX consistency maintained

## Governance

### Amendment Process

Constitution amendments require:

1. **Proposal**: Document proposed change with rationale and impact analysis
2. **Version Bump Decision**:
   - MAJOR: Backward incompatible changes, principle removal/redefinition
   - MINOR: New principles, material guidance expansion
   - PATCH: Clarifications, wording improvements, typo fixes
3. **Sync Impact Analysis**: Identify all dependent templates and documentation
   requiring updates
4. **Approval**: Maintainer approval required before ratification
5. **Propagation**: Update all dependent templates and agent command files
6. **Ratification**: Update version, amendment date, and publish sync report

### Compliance Review

- Constitution compliance is verified during code review for every pull request
- Pre-commit automation enforces technical requirements automatically
- Manual review verifies architectural and design principle adherence
- Violations MUST be resolved before merge - no exceptions

### Versioning Policy

- **Version Format**: MAJOR.MINOR.PATCH (semantic versioning)
- **Ratification Date**: Original constitution adoption date (immutable)
- **Last Amended Date**: Updated with each constitutional change
- **Sync Report**: Prepended as HTML comment documenting changes and impact

### Enforcement

This constitution supersedes all other practices and conventions. When conflicts
arise between existing code patterns and constitutional principles, the
constitution takes precedence. Exceptions require documented justification and
maintainer approval.

**Version**: 1.0.0 | **Ratified**: 2026-01-10 | **Last Amended**: 2026-01-10

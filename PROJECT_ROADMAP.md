# ROTIC HRM Project Roadmap

## Product Vision

ROTIC HRM is a unified human resources platform for managing employees, attendance, payroll, leave, performance, training, assets, recruitment, and reporting in one coherent system. The product should support day-to-day HR operations reliably, scale with organizational growth, and remain maintainable as new modules are added.

## Architectural Principles

- Keep business logic in services, not views or UI components.
- Prefer small, focused modules with clear ownership and stable interfaces.
- Design APIs to be predictable, versionable, and easy to integrate.
- Preserve data integrity with validation, auditing, and explicit relationships.
- Build reusable frontend components and shared design patterns early.
- Favor simple, proven approaches before introducing complexity.
- Treat integrations as first-class features with error handling and observability.

## Coding Standards

- Use consistent naming, formatting, and file structure across backend and frontend code.
- Separate models, serializers, services, views, and URLs in the backend.
- Keep frontend pages thin and move shared UI into reusable components.
- Write tests for core business rules, edge cases, and integration points.
- Document important domain decisions and schema changes as the system evolves.
- Avoid breaking changes unless there is a clear migration path.

## Roadmap Phases

### Phase 1: Core HR & Employee Management

**Objectives**
- Establish the employee master data foundation.
- Standardize core HR entities and workflows.
- Ensure the system can reliably store and manage employee records.

**Major Features**
- Employee profiles and lifecycle management
- Departments, positions, and reporting structure
- Employee documents and profile metadata
- Import and bulk data validation
- Duplicate detection and record hygiene
- Basic admin and maintenance workflows

**Dependencies**
- Stable employee data model
- Authentication and role-based access
- Core database migrations and validation rules

**Completion Criteria**
- Employee records can be created, updated, reviewed, and imported reliably.
- Core organizational structure is in place.
- Basic data quality checks are enforced.
- The module is stable enough to support downstream features.

### Phase 2: Attendance & Biometric Integration

**Objectives**
- Capture attendance accurately and consistently.
- Connect biometric devices and attendance sources.
- Establish trustworthy timekeeping data for payroll and reporting.

**Major Features**
- Daily attendance tracking
- Check-in and check-out processing
- Biometric identity mapping
- Device import and synchronization
- Manual corrections and approvals
- Attendance summaries and exceptions

**Dependencies**
- Employee master data from Phase 1
- Biometric identity model
- Reliable attendance storage and processing services

**Completion Criteria**
- Attendance data can be captured from devices and reviewed in the system.
- Identity matching is dependable.
- Exceptions can be corrected with an audit trail.
- Attendance outputs are ready for payroll consumption.

### Phase 3: Payroll

**Objectives**
- Automate salary calculation and payroll preparation.
- Support consistent pay runs with configurable rules.
- Provide payroll outputs that are traceable and auditable.

**Major Features**
- Salary structure and pay components
- Allowances, deductions, and overtime rules
- Payroll processing runs
- Payslip generation
- Payroll approval workflow
- Payroll history and adjustments

**Dependencies**
- Attendance data from Phase 2
- Employee and compensation records from Phase 1
- Company payroll policies and statutory rules

**Completion Criteria**
- Payroll can be computed accurately for a standard pay cycle.
- Payslips and payroll summaries are generated successfully.
- Adjustments and approvals are tracked.
- Payroll outputs can be exported or reviewed without manual rework.

### Phase 4: Leave & Shift Management

**Objectives**
- Manage employee leave requests and shift schedules.
- Align attendance, leave, and working patterns.
- Reduce manual scheduling and leave reconciliation.

**Major Features**
- Leave types and balances
- Leave application and approval workflow
- Shift creation and assignment
- Roster planning and calendar views
- Public holidays and work calendars
- Leave and shift conflict handling

**Dependencies**
- Employee records from Phase 1
- Attendance foundation from Phase 2
- Policy rules for leave entitlements and scheduling

**Completion Criteria**
- Leave and shift records can be created, approved, and tracked.
- Balances and calendar conflicts are handled correctly.
- Scheduling data integrates cleanly with attendance and payroll.

### Phase 5: Performance & Training

**Objectives**
- Support employee development and performance management.
- Track goals, reviews, and learning activity.
- Give managers structured tools for workforce growth.

**Major Features**
- Performance review cycles
- Goals and appraisal forms
- Manager feedback and ratings
- Training programs and course tracking
- Certifications and learning history
- Development plans and reminders

**Dependencies**
- Employee and reporting structure from Phase 1
- Role permissions for managers and reviewers
- Notification and workflow support

**Completion Criteria**
- Reviews and training records can be created and tracked.
- Managers can evaluate employees using structured workflows.
- Development history is stored centrally and is reportable.

### Phase 6: Assets & Inventory

**Objectives**
- Track company assets issued to employees.
- Manage inventory items used by HR and operations.
- Improve accountability for company property.

**Major Features**
- Asset registry and assignment tracking
- Inventory categories and stock records
- Issue, return, and transfer workflows
- Asset condition and lifecycle status
- Stock movement and audit history

**Dependencies**
- Employee records from Phase 1
- Basic audit logging and workflow support
- Clear ownership and assignment rules

**Completion Criteria**
- Assets can be assigned, returned, and audited.
- Inventory records remain accurate over time.
- Asset and stock history is traceable.

### Phase 7: Recruitment & Onboarding

**Objectives**
- Support hiring workflows from candidate intake to employee creation.
- Streamline onboarding for new hires.
- Reduce friction between recruitment and HR operations.

**Major Features**
- Job openings and requisitions
- Candidate profiles and application tracking
- Interview scheduling and evaluation
- Offer management
- New hire onboarding checklist
- Automatic employee record creation after hiring

**Dependencies**
- Employee schema and access control from Phase 1
- Workflow and notification support
- Document handling for onboarding materials

**Completion Criteria**
- Candidates can move through a defined hiring pipeline.
- Onboarding tasks are tracked to completion.
- Successful hires flow cleanly into employee records.

### Phase 8: Reports & Analytics

**Objectives**
- Turn operational data into actionable insights.
- Provide management visibility across HR functions.
- Support routine and ad hoc reporting needs.

**Major Features**
- HR dashboards
- Attendance, payroll, and leave reports
- Headcount and turnover metrics
- Department and location summaries
- Exportable report formats
- Filtered analytics by period, team, and status

**Dependencies**
- Mature data from prior phases
- Consistent reporting fields and validation
- Performance-conscious queries and aggregations

**Completion Criteria**
- Core operational reports are available and trusted.
- Users can filter, export, and review key metrics easily.
- Reporting outputs match underlying source data.

### Phase 9: Mobile App

**Objectives**
- Extend essential HR workflows to mobile devices.
- Enable faster self-service and field use.
- Improve accessibility for employees and managers on the move.

**Major Features**
- Mobile-friendly employee self-service
- Attendance actions and approvals
- Leave requests and status checks
- Notifications and reminders
- Compact dashboards for quick access
- Offline-tolerant interactions where practical

**Dependencies**
- Stable backend APIs from earlier phases
- Clear authentication and session handling
- Responsive data models and permissions

**Completion Criteria**
- Core workflows are usable on mobile.
- The experience is reliable and responsive on smaller screens.
- Mobile access supports the highest-value employee tasks.

### Phase 10: Commercial SaaS Readiness

**Objectives**
- Prepare ROTIC HRM for broader commercial deployment.
- Strengthen security, scalability, and tenant-aware operations.
- Make the product supportable as a long-term SaaS platform.

**Major Features**
- Multi-tenant architecture or tenant isolation strategy
- Subscription and licensing support
- Role-based administration for customer organizations
- Security hardening and audit controls
- Observability, monitoring, and backup strategy
- Deployment, onboarding, and support tooling

**Dependencies**
- Mature core product across earlier phases
- Stable infrastructure and release processes
- Security, compliance, and operational standards

**Completion Criteria**
- The platform can support multiple customer organizations safely.
- Operational monitoring and recovery processes are defined.
- The product is ready for controlled commercial rollout.

## Roadmap Usage

This document is the master roadmap for ROTIC HRM. `PROJECT_STATUS.md` should reference this file when summarizing current progress, upcoming work, and long-term priorities. Any future scope changes should be reflected here first so the roadmap remains the source of truth.



One strategic addition I'd put on the roadmap
Because you're operating a manufacturing business, I recommend adding a Compliance Engine after Audit and Notifications.
It would monitor rules such as:
- Mandatory PPE training completed before operating certain equipment.
- Required medical examinations are current.
- Forklift or crane certifications have not expired.
- Employees are eligible for assigned roles.
- Required onboarding documents have been submitted.
Instead of just storing data, the system would continuously evaluate compliance and surface risks to HR and Operations. That would make the platform valuable not only to HR but also to your production and safety teams
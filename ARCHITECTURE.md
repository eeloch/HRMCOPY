# HRM Architecture

## 1. Project Structure

The HRM is a Django backend with a Next.js frontend. Django exposes authenticated REST endpoints under `/api/`; the frontend consumes them through the shared `apiFetch` helper.

### Django Apps

- `employees`: Employee master data, departments, positions, employment details, biometric identities, import workflows, and employee profile data.
- `attendance`: Shifts, daily attendance, attendance events, exceptions, and workforce dashboard data.
- `documents`: Employee document uploads, retrieval, and deletion.
- `leave`: Leave domain foundation, including leave types, balances, requests, and future workflow endpoints.
- `payroll`: Payroll domain and future salary-processing functionality.

### Frontend

- `frontend/src/app`: Route-based Next.js pages. Domain pages are grouped by feature, such as `employees` and `attendance`.
- `frontend/src/components`: Reusable feature components, including the sidebar, employee forms, and document components.
- `frontend/src/components/ui`: Shared presentation primitives: `AppCard`, `MetricCard`, `PageHeader`, `Section`, and `StatusBadge`.
- `frontend/src/app/layout.tsx`: Application-level layout and global styles. Feature pages currently render the shared `Sidebar`; a future `AppShell` will centralize that page chrome.

## 2. Backend Standards

Every Django module should follow this structure:

```text
module/
  models.py
  serializers.py
  urls.py
  admin.py
  views/
  services/
```

- `models.py` defines persistent domain data and database constraints.
- `serializers.py` defines API input and output contracts.
- `urls.py` owns module endpoint registration.
- `views/` contains thin request handlers grouped by responsibility.
- `services/` contains business rules, workflows, calculations, and reusable domain operations.
- `admin.py` provides useful operational administration for each model.

Business logic belongs in services. Views should authenticate, validate input, call a service or serializer, and return a response. They should not contain complex calculations, cross-domain workflows, or duplicated query logic.

## 3. Frontend Standards

Pages should use the shared UI foundation whenever it fits the page:

- `AppShell` (future): Shared sidebar, navigation, page spacing, and responsive application chrome.
- `PageHeader`: Page title, description, and right-aligned actions.
- `AppCard`: General content container.
- `MetricCard`: Dashboard and operational summary values.
- `Section`: Tables and grouped page content.
- `StatusBadge`: Consistent status presentation.

Avoid duplicating Tailwind layouts that these components already provide. Keep pages responsible for route state and data loading; move reusable presentation or domain-specific controls into `components/`.

## 4. Shared Services

Future modules should build on shared services rather than independently recreating cross-cutting behavior.

- `Audit`: Record important user and system actions, including approvals, imports, and employee updates.
- `Notifications`: Deliver in-app, email, or future device notifications for approvals, deadlines, and exceptions.
- `Timeline`: Present ordered employee and operational history from audit and workflow events.
- `Workflow`: Provide reusable approval states, assignments, decisions, and escalation rules.
- `Search`: Provide consistent filtering and search patterns across employee, attendance, leave, and future modules.

## 5. Coding Standards

- Use Django `TextChoices` for controlled model values and API-safe enumerations.
- Use `select_related()` for foreign-key relationships and `prefetch_related()` for collections to avoid avoidable query growth.
- Put business rules in a service layer rather than views, serializers, or frontend pages.
- Use reusable UI components before introducing another copy of an existing Tailwind layout.
- Keep TypeScript in strict mode and type API responses, component props, and form state explicitly.
- Preserve existing API contracts unless a deliberate backend change is approved.

## 6. Future Modules

Planned domains should follow the same module and UI standards:

- Training
- Medical
- Meals
- Assets
- Recruitment
- Reports
- Analytics

Each module should begin with a scoped domain model, service layer, thin API views, admin support, and frontend routes built from the UI foundation.

## 7. Design Principles

- **Single Responsibility**: Each model, service, view, page, and component should have one clear purpose.
- **Reusable Components**: Shared UI and shared business behavior should be implemented once and reused.
- **Thin Views**: HTTP views coordinate requests; they do not own domain workflows.
- **Business Logic in Services**: Calculations, approvals, eligibility, and multi-model updates belong in services.
- **Configuration over Hardcoding**: Prefer managed data, model choices, and settings over feature-specific constants embedded in pages or views.

## 8. Roadmap

### Current Implementation

- Employee master data, employment classifications, profiles, documents, import workflows, and editing are implemented.
- Attendance records, exception review, dashboard operations, department readiness, hostel absences, and the daily register are implemented.
- The Leave app foundation, models, administration, and migrations are implemented; leave calculations, balances, requests, and approval APIs remain future work.
- Shared frontend UI components are available and the Employees page uses the UI foundation.

### Next Steps

- Introduce `AppShell` and progressively adopt the shared UI primitives across remaining pages.
- Implement leave request, balance, and approval services with audit and workflow support.
- Add shared audit, notifications, timeline, workflow, and search capabilities before expanding additional HR domains.
- Build future domain modules using the backend and frontend standards in this document.

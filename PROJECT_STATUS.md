# Project Status

# ROTIC HRM

Version: 0.2.1
Status: Active Development
Current Milestone: Employee Management Complete / Attendance Development
Last Updated: 31 Aug 2026


## 1) Project Overview
Rotic HRM is a Django + Next.js human resource management system focused on employee administration, attendance, and a growing set of operational HR workflows. The project already has a structured backend and frontend, and the current state shows a solid foundation for a commercial HR platform.

## 2) Current Architecture
### Django Backend
- `config/` handles project configuration and routing.
- `employees/` is the most mature backend module and currently anchors the HR data model.
- `attendance/` already has models, serializers, services, views, and URLs in place.
- `payroll/` exists as a scaffold and is not yet fully implemented.
- `core/` appears to be reserved for shared app-level functionality.

### Next.js Frontend
- `frontend/` is built with Next.js App Router.
- Current routes include `dashboard`, `attendance`, `employees`, and `login`.
- Shared UI begins with `components/Sidebar.tsx` and `lib/api.ts`.

## 3) Completed Features
- Employee CRUD is in place.
- Department management is implemented.
- Position management is implemented.
- Employee import workflow is present.
- Import preview is available.
- Excel validation is implemented.
- Duplicate detection is implemented.
- Organization auto-creation is in place during import.
- Import button is enabled.
- Hostel room support has been added.
- Biometric identity support has been added.
- Attendance module foundation is established.
- Backend code is organized with models, serializers, views, URLs, and services where needed.

## 4) Modules in Progress
- Attendance: foundation exists, but the feature set still needs expansion.
- Employees: mature core module, with room for refinement in import, admin, and workflow polish.
- Frontend: key pages exist, but the UI layer still needs broader feature coverage and consistency.
- Core: shared system functionality is still being defined.

## 5) Planned Modules
- Payroll
- Leave
- Recruitment
- Performance
- Training
- Assets
- Reports
- Mobile

## 6) Current Project Tree Summary
- `config/` project settings and URLs
- `core/` shared app foundation
- `employees/` primary HR master data module
- `attendance/` attendance tracking foundation
- `payroll/` payroll scaffold
- `frontend/` Next.js application with app routes and shared UI
- `db.sqlite3` local development database
- `manage.py` Django entry point
- `requirements.txt` backend dependencies

## 7) Development Conventions
- Keep Django app logic separated into models, serializers, views, URLs, and services.
- Prefer modular app design over large monolithic files.
- Keep frontend work organized inside the Next.js App Router structure.
- Use shared components for repeated UI patterns.
- Preserve import validation and duplicate-checking behavior when extending employee workflows.
- Treat the backend as the source of truth for HR records.

## 8) Next Priorities
1. Strengthen attendance workflows beyond the initial foundation.
2. Expand employee module polish around import, validation, and admin usability.
3. Build out the payroll module structure.
4. Establish the shared `core/` layer for cross-cutting HR features.
5. Improve frontend coverage for the existing backend capabilities.
6. Add the next business modules in a controlled sequence: leave, recruitment, performance, training, assets, and reports.


## Current Sprint

Goal:
Complete Attendance Module

Tasks

- Attendance Dashboard
- Device Synchronization
- Exception Approval
- Attendance Reports
- Shift Assignment
- Overtime Engine

## Session Log
- Confirmed the current project structure from the referenced conversation.
- Identified the Django backend apps and the Next.js frontend routes.
- Captured the completed employee and attendance foundation work.
- Documented the next module roadmap and development conventions.



## Milestone 1
✔ Authentication

## Milestone 2
✔ Employee Management

- Employee CRUD
- Departments
- Positions
- Hostel
- Import

## Milestone 3
✔ Import Engine

- Excel Import
- Validation
- Duplicate Detection
- Auto Department Creation
- Auto Position Creation



| Module | Backend | Frontend | Testing | Production |
|---------|----------|----------|----------|------------|
| Login | ✅ | ✅ | ✅ | ✅ |
| Employees | ✅ | ✅ | 🟡 | 🟡 |
| Attendance | 🟡 | 🟡 | ❌ | ❌ |
| Payroll | ❌ | ❌ | ❌ | ❌ |
| Leave | ❌ | ❌ | ❌ | ❌ |
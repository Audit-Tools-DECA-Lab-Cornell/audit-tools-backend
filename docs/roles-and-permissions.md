# Roles And Permissions

## Overview

The YEE platform has three main roles:

- `ADMIN`
- `MANAGER`
- `AUDITOR`

The frontend uses route guards and role-aware layouts, but backend access checks are the real enforcement layer.

## Role Matrix

| Capability | ADMIN | MANAGER | AUDITOR |
| --- | --- | --- | --- |
| View all users | Yes | No | No |
| Approve users | Yes | No | No |
| Create projects | Not from current admin route | Yes | No |
| Create places | Not from current admin route | Yes | No |
| Invite auditors | Not from current admin route | Yes | No |
| Assign auditors to places | Not from current admin route | Yes | No |
| View scoped dashboard | Yes | Yes | Yes |
| View all projects/places/audits | Yes | Scoped only | No |
| View assigned places | No | No | Yes |
| Submit YEE audit | No | No | Yes |
| View raw data export | Yes | Scoped only | No |
| View comparison reports | Yes | Scoped only | No |

## Route Expectations

### Public / Account

- `/login`
- `/signup`
- `/verify-email`
- `/invite/[token]`
- `/waiting-approval`
- `/complete-profile`

### Admin

- `/admin`
- `/admin/users`
- `/admin/projects`
- `/admin/places`
- `/admin/audits`
- `/admin/raw-data`
- `/admin/settings`

### Manager

- `/dashboard`
- `/dashboard/projects`
- `/dashboard/projects/new`
- `/dashboard/projects/[projectId]`
- `/dashboard/places`
- `/dashboard/places/new`
- `/dashboard/places/[placeId]`
- `/dashboard/auditors`
- `/dashboard/auditors/invite`
- `/dashboard/audits`
- `/dashboard/raw-data`
- `/dashboard/reports`
- `/dashboard/settings`

### Auditor

- `/my-dashboard`
- `/my-dashboard/places`
- `/my-dashboard/audits`
- `/my-dashboard/settings`
- `/yee/introduction`
- `/yee/audit/[placeId]/page/1` through `/page/8`
- `/yee/audit/[placeId]/review`
- `/yee/audit/[placeId]/submitted`

## Auth And Onboarding State

The backend returns onboarding state that drives the frontend route decision.

Important session fields:

- `role`
- `email_verified`
- `approved`
- `profile_completed`
- `next_step`
- `dashboard_path`

Expected routing behavior:

- unverified -> `/verify-email`
- not approved -> `/waiting-approval`
- approved but incomplete profile -> `/complete-profile`
- fully ready -> role dashboard

Role dashboard mapping:

- `ADMIN -> /admin`
- `MANAGER -> /dashboard`
- `AUDITOR -> /my-dashboard`

## Account Scope Rules

### Admin

Admin is system-wide. Admin can see:

- all users
- all accounts
- all projects
- all places
- all auditors
- all audits
- all raw data

### Manager

Manager is scoped by `account_id`. Manager can only see:

- projects in their account
- places under those projects
- auditors tied to that account
- assignments in that account
- audits and reports from that account

### Auditor

Auditor access is scoped through assignments.

Auditors can only:

- see places assigned to them
- see their own audit history
- submit YEE audits for assigned places only

## Submission Rule

Important enforced rule:

- one auditor can submit only one final YEE audit per place

This is enforced in the backend when the auditor submits the final audit.

## Invite And Approval Behavior

### Public signup

- managers can sign up and are auto-approved
- auditors can sign up but remain pending approval
- public users cannot self-create admin accounts

### Manager invite flow

- manager creates an auditor invite
- invited auditor accepts using `/invite/[token]`
- invite acceptance creates or links the account
- approval and profile state still apply

### Admin approval

Admins can approve pending users.

For auditors:

- admin approval can attach the user to an account/workspace
- if needed, an `Auditor` profile and generated auditor code are created automatically

## Auditor Privacy Rules

Generated auditor IDs are the default reporting identity.

Rules:

- use generated IDs such as `AUD-XXXXXX`
- do not expose DOB or full name in reporting identifiers
- comparisons and exports should default to generated IDs
- use full names only in restricted internal contexts where policy allows

## Current Gaps

- admin deny/revoke/deactivate actions are not implemented
- settings/profile permissions are not fully built out
- some admin mutation flows still intentionally reject writes from manager-specific endpoints

Future engineers should treat those as workflow gaps, not as missing role concepts.

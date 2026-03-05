# Audit System Development Structure
This document outlines the architecture, roles, and functional requirements for the Audit System. The system is designed to manage hierarchical audit processes across multiple projects and physical locations.

## System Hierarchy
The system follows a strict top-down hierarchy, where a single Account houses multiple Projects, which in turn house multiple Places. Auditors are assigned to specific Places to complete Audits.

```
Account (Managed by Primary/Secondary Managers)
│
├── Project A
│   ├── Place A
│   │   ├── Auditor A ──► Completes Audit(s)
│   │   ├── Auditor B ──► Completes Audit(s)
│   │   └── Auditor C ──► Completes Audit(s)
│   ├── Place B
│   └── Place C
│
└── Project B
    ├── Place A
    ├── Place B
    └── Place C
```

## Accounts & Roles
The system is accessed via an Audit Account, which supports two distinct user types: Managers and Auditors.

### Manager Profile
- Limit: Up to 5 managers per account (the first profile is the Primary Manager).

- Permissions: Managers have access to all Projects and Auditor data/settings. Only Managers can edit/delete system configurations. Managers can access a full database of all Auditors and their assigned Projects/Places. (Note: Managers can also be Auditors, but must set up a separate Auditor Profile).

- Required Fields:

    - Full Name

    - Position/Role

    - Organization/Institution

    - Email address

    - Phone contact (Required for Primary; optional for others)

- Automatically record the start date

### Auditor Profile
- Limit: As many as needed (suggested cap at ~25).

- Permissions: Auditors only have the ability to complete audits, view/download reports, and change their own settings.

- Onboarding: Invited by a Manager via email or a designated login link specific to their Project (potentially via a QR code for field setup).

- Required Fields:

    - Full Name (Privacy note: Only visible within Auditor account settings and Manager databases; public/user-facing reports must only use the Auditor Code).

    - Auditor Code (Pre-assigned by Manager or designated by Auditor; initials or alphanumeric code, no real names).

    - Email address

    - Age range (Dropdown list)

    - Gender (Open text)

    - Country of origin or residence (Open text)

    - Role (Dropdown with 'other' open field: student / teacher / facilitator / etc.)

### Structural Profiles
1. #### Project Profile
    Managers can create unlimited Projects. Each Project profile dictates the overall scope of a specific auditing initiative.

    - Project overview / aims

    - Types of Places to be Audited (Dropdown list with 'other' field)

    - Anticipated Start & End Dates

    - Estimated number of Places to be audited

    - Estimated number of Auditors per Project

    - Description of Auditors (Population type, age range, inclusions/exclusions)

2. #### Place Profile
    Set up under a selected Project. Every physical location to be audited requires a Place Profile.

    - Place Name

    - Place Location (City, Province/State, Country via dropdowns; potential Google Maps integration with map pin and GPS coordinates)

    - Type of place (Dropdown list with 'other' field)

    - Anticipated Start & End Dates

    - Estimated number of Auditors

    - Description of Auditors

    - **Assignment**: Managers can assign or provide access to Auditors at either the Project level or the Place level.

### The Audit Process
An audit can be completed by any Auditor assigned to a specific Place.

1. #### Audit Execution
    - Master Code: Automatically generated for each audit: [Place Name] - [Auditor Code] - [Date].

    - Tracking: Automatically records Start Date/Time.

    - Navigation & Saving: Auditors can move back and forth between sections to edit responses. The system should auto-save periodically.

    - Submission: Cannot be submitted unless all mandatory sections are completed.

2. #### Scoring & Dashboards
    - Calculations: Scores and sub-scores are automatically calculated upon completion.

    - Place Dashboard: Each Place features a dashboard displaying results.

        - Allows comparison of total and sub-scores across other audits completed for the same Place.

        - Compare ALL or toggle specific audits (identified by Place-Auditor Name-Date).

        - Comparisons can be side-by-side, mean scores, or level of agreement (e.g., Kappa score).

        - Flexibility to toggle graphics/results on and off.

        - Export/print capabilities (PDF).

    - Project Dashboard: Each Project features a dashboard with summary stats (e.g., total number of audits completed).

### Specific Audit Tools
1. #### Youth Enabling Environments Audit Tool
    - Pre-Audit Survey: Starts with a short survey where the Auditor assigns personal importance/weights to different sub-sections.

    - Weighted Results: These personal weights are used to calculate total and sub-section scores.

        - Design consideration: Ensure weighted scores reflect the maximum possible score if highest audit responses and highest weights are applied.

    - Display/Export: Users must have the ability to toggle between or view side-by-side Weighted Results and Non-Weighted Results.

2. #### Playspace Play Value and Usability Audit Tool
    - Two-Part Tool: Consists of the audit tool itself and an owner/manager survey.

    - Survey Integration: The system should automatically generate a survey link for the owner/manager.

    - Scoring: Calculates scores from the audit alone, but also generates a combined score integrating the survey responses (if possible).
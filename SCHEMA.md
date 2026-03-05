# Audit System Database Schema

This document outlines the relational database schema required to support the Audit System's hierarchical structure, user roles, and data collection tools.

---

## 1. Core Account & User Management

### `Accounts`
Stores the top-level organization/audit account.
* `account_id` (PK) - UUID
* `account_name` - VARCHAR(255)
* `data_sharing_agreed` - BOOLEAN (Required to be TRUE)
* `created_at` - TIMESTAMP

### `Managers`
Stores both Primary and secondary managers. Limited to 5 per account via application logic.
* `manager_id` (PK) - UUID
* `account_id` (FK) - UUID (References `Accounts`)
* `is_primary` - BOOLEAN 
* `full_name` - VARCHAR(255)
* `position_role` - VARCHAR(255)
* `organization` - VARCHAR(255)
* `email` - VARCHAR(255) UNIQUE
* `phone` - VARCHAR(50) (Nullable, but required if `is_primary` = TRUE)
* `start_date` - TIMESTAMP (Auto-recorded)

### `Auditors`
Stores auditor profiles. Designed to protect privacy by heavily relying on `auditor_code`.
* `auditor_id` (PK) - UUID
* `auditor_code` - VARCHAR(50) UNIQUE (Initials/mixed code; no real names)
* `full_name` - VARCHAR(255) (Encrypted/Restricted access)
* `email` - VARCHAR(255) UNIQUE
* `age_range` - VARCHAR(50) (e.g., "18-24", "25-34")
* `gender` - VARCHAR(50)
* `country` - VARCHAR(100)
* `role` - VARCHAR(100) (e.g., Student, Teacher, Facilitator, Other)
* `created_at` - TIMESTAMP

---

## 2. Project & Place Hierarchy

### `Projects`
Projects fall under an Account and define the scope of the audits.
* `project_id` (PK) - UUID
* `account_id` (FK) - UUID (References `Accounts`)
* `project_name` - VARCHAR(255)
* `overview` - TEXT
* `anticipated_start_date` - DATE
* `anticipated_end_date` - DATE
* `est_places_count` - INT
* `est_auditors_count` - INT
* `auditor_demographic_reqs` - TEXT (JSON or text defining inclusions/exclusions)

### `Places`
Physical locations assigned to a specific Project.
* `place_id` (PK) - UUID
* `project_id` (FK) - UUID (References `Projects`)
* `place_name` - VARCHAR(255)
* `type_of_place` - VARCHAR(100)
* `city` - VARCHAR(100)
* `state_province` - VARCHAR(100)
* `country` - VARCHAR(100)
* `latitude` - DECIMAL(9,6) (For map pin integration)
* `longitude` - DECIMAL(9,6) (For map pin integration)
* `anticipated_start_date` - DATE
* `anticipated_end_date` - DATE
* `est_auditors_count` - INT

---

## 3. Assignments (Many-to-Many Relationships)

### `Auditor_Assignments`
Maps which Auditors have access to which Projects and Places.
* `assignment_id` (PK) - UUID
* `auditor_id` (FK) - UUID (References `Auditors`)
* `project_id` (FK) - UUID (References `Projects`) (Nullable if assigned specifically to a Place)
* `place_id` (FK) - UUID (References `Places`) (Nullable if assigned broadly to a Project)

---

## 4. Audit Execution & Scoring

### `Audits`
The core record of an auditor completing an assessment at a place.
* `audit_id` (PK) - UUID
* `place_id` (FK) - UUID (References `Places`)
* `auditor_id` (FK) - UUID (References `Auditors`)
* `master_code` - VARCHAR(255) (Generated: `[Place Name]-[Auditor Code]-[Date]`)
* `status` - VARCHAR(50) (e.g., 'Draft', 'Completed')
* `started_at` - TIMESTAMP
* `completed_at` - TIMESTAMP (Nullable until submission)
* `base_total_score` - DECIMAL(5,2)
* `weighted_total_score` - DECIMAL(5,2) (Calculated via pre-audit survey weights)

### `Audit_Responses`
Stores individual answers/scores for the sections within an audit.
* `response_id` (PK) - UUID
* `audit_id` (FK) - UUID (References `Audits`)
* `section_name` - VARCHAR(100)
* `question_identifier` - VARCHAR(100)
* `response_value` - TEXT (or INT depending on scale)
* `is_mandatory` - BOOLEAN

---

## 5. Tool-Specific Tables

### `Pre_Audit_Surveys` (Youth Enabling Environments Tool)
Stores the weights the Auditor assigns to different sections before starting.
* `survey_id` (PK) - UUID
* `audit_id` (FK) - UUID (References `Audits`)
* `section_name` - VARCHAR(100)
* `assigned_weight` - DECIMAL(3,2) (e.g., 1.5, 0.8)

### `Manager_Surveys` (Playspace Usability Tool)
Stores the external owner/manager survey results linked to a specific Place/Audit.
* `manager_survey_id` (PK) - UUID
* `place_id` (FK) - UUID (References `Places`)
* `survey_link` - VARCHAR(255) (The generated link sent to the manager)
* `status` - VARCHAR(50) (e.g., 'Pending', 'Submitted')
* `manager_score` - DECIMAL(5,2)
* `combined_total_score` - DECIMAL(5,2) (Audit score + Manager survey score)
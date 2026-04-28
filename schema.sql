--
-- PostgreSQL database dump
--

\restrict gV4SJg7f5Oz17VXPnrAdSQYdLoaOoGHGLEzTmVKBs8xyeW3veEU4UR2AwhiuMEK

-- Dumped from database version 17.8 (130b160)
-- Dumped by pg_dump version 17.9 (Homebrew)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: notification_type_enum; Type: TYPE; Schema: public; Owner: neondb_owner
--

CREATE TYPE public.notification_type_enum AS ENUM (
    'ASSIGNMENT_CREATED',
    'ASSIGNMENT_UPDATED',
    'AUDIT_COMPLETED'
);


ALTER TYPE public.notification_type_enum OWNER TO neondb_owner;

--
-- Name: shared_account_type; Type: TYPE; Schema: public; Owner: neondb_owner
--

CREATE TYPE public.shared_account_type AS ENUM (
    'ADMIN',
    'MANAGER',
    'AUDITOR'
);


ALTER TYPE public.shared_account_type OWNER TO neondb_owner;

--
-- Name: shared_audit_status; Type: TYPE; Schema: public; Owner: neondb_owner
--

CREATE TYPE public.shared_audit_status AS ENUM (
    'IN_PROGRESS',
    'PAUSED',
    'SUBMITTED'
);


ALTER TYPE public.shared_audit_status OWNER TO neondb_owner;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: accounts; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.accounts (
    id uuid NOT NULL,
    name character varying(200) NOT NULL,
    email character varying(320) NOT NULL,
    password_hash character varying(255),
    account_type public.shared_account_type NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.accounts OWNER TO neondb_owner;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


ALTER TABLE public.alembic_version OWNER TO neondb_owner;

--
-- Name: auditor_assignments; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.auditor_assignments (
    id uuid NOT NULL,
    auditor_profile_id uuid NOT NULL,
    project_id uuid NOT NULL,
    place_id uuid NOT NULL,
    assigned_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.auditor_assignments OWNER TO neondb_owner;

--
-- Name: auditor_invites; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.auditor_invites (
    id uuid NOT NULL,
    account_id uuid NOT NULL,
    invited_by_user_id uuid NOT NULL,
    auditor_id uuid,
    email character varying(320) NOT NULL,
    token_hash character varying(255) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    accepted_at timestamp with time zone
);


ALTER TABLE public.auditor_invites OWNER TO neondb_owner;

--
-- Name: auditor_profiles; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.auditor_profiles (
    id uuid NOT NULL,
    account_id uuid NOT NULL,
    user_id uuid,
    auditor_code character varying(50) NOT NULL,
    email character varying(320),
    full_name character varying(200) NOT NULL,
    age_range character varying(80),
    gender character varying(80),
    country character varying(120),
    role character varying(120),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.auditor_profiles OWNER TO neondb_owner;

--
-- Name: audits; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.audits (
    id uuid NOT NULL,
    project_id uuid NOT NULL,
    place_id uuid NOT NULL,
    auditor_profile_id uuid NOT NULL,
    audit_code character varying(120) NOT NULL,
    instrument_key character varying(80),
    instrument_version character varying(40),
    status public.shared_audit_status NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    submitted_at timestamp with time zone,
    total_minutes integer,
    summary_score double precision,
    responses_json jsonb NOT NULL,
    scores_json jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.audits OWNER TO neondb_owner;

--
-- Name: instruments; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.instruments (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    instrument_key character varying(255) NOT NULL,
    instrument_version character varying(50) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    content jsonb NOT NULL
);


ALTER TABLE public.instruments OWNER TO neondb_owner;

--
-- Name: manager_invites; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.manager_invites (
    id uuid NOT NULL,
    account_id uuid NOT NULL,
    invited_by_user_id uuid NOT NULL,
    accepted_by_user_id uuid,
    email character varying(320) NOT NULL,
    token_hash character varying(255) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    accepted_at timestamp with time zone
);


ALTER TABLE public.manager_invites OWNER TO neondb_owner;

--
-- Name: manager_profiles; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.manager_profiles (
    id uuid NOT NULL,
    account_id uuid NOT NULL,
    user_id uuid,
    full_name character varying(200) NOT NULL,
    email character varying(320) NOT NULL,
    phone character varying(50),
    "position" character varying(200),
    organization character varying(200),
    is_primary boolean NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.manager_profiles OWNER TO neondb_owner;

--
-- Name: notifications; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.notifications (
    id uuid NOT NULL,
    user_id uuid NOT NULL,
    message character varying(500) NOT NULL,
    notification_type public.notification_type_enum NOT NULL,
    is_read boolean DEFAULT false NOT NULL,
    related_entity_type character varying(50),
    related_entity_id uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.notifications OWNER TO neondb_owner;

--
-- Name: places; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.places (
    id uuid NOT NULL,
    name character varying(200) NOT NULL,
    city character varying(120),
    province character varying(120),
    country character varying(120),
    postal_code character varying(32),
    place_type character varying(100),
    lat double precision,
    lng double precision,
    start_date date,
    end_date date,
    est_auditors integer,
    auditor_description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    address text
);


ALTER TABLE public.places OWNER TO neondb_owner;

--
-- Name: playspace_audit_contexts; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.playspace_audit_contexts (
    audit_id uuid NOT NULL,
    execution_mode character varying(20),
    draft_progress_percent double precision,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.playspace_audit_contexts OWNER TO neondb_owner;

--
-- Name: playspace_audit_sections; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.playspace_audit_sections (
    id uuid NOT NULL,
    audit_id uuid NOT NULL,
    section_key character varying(120) NOT NULL,
    note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.playspace_audit_sections OWNER TO neondb_owner;

--
-- Name: playspace_pre_audit_answers; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.playspace_pre_audit_answers (
    id uuid NOT NULL,
    audit_id uuid NOT NULL,
    field_key character varying(80) NOT NULL,
    selected_value character varying(80) NOT NULL,
    sort_order integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.playspace_pre_audit_answers OWNER TO neondb_owner;

--
-- Name: playspace_question_responses; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.playspace_question_responses (
    id uuid NOT NULL,
    section_id uuid NOT NULL,
    question_key character varying(120) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.playspace_question_responses OWNER TO neondb_owner;

--
-- Name: playspace_scale_answers; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.playspace_scale_answers (
    id uuid NOT NULL,
    question_response_id uuid NOT NULL,
    scale_key character varying(40) NOT NULL,
    option_key character varying(80) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.playspace_scale_answers OWNER TO neondb_owner;

--
-- Name: playspace_submission_contexts; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.playspace_submission_contexts (
    submission_id uuid NOT NULL,
    execution_mode character varying(20),
    draft_progress_percent double precision,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.playspace_submission_contexts OWNER TO neondb_owner;

--
-- Name: playspace_submissions; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.playspace_submissions (
    id uuid NOT NULL,
    project_id uuid NOT NULL,
    place_id uuid NOT NULL,
    auditor_profile_id uuid NOT NULL,
    audit_code character varying(120) NOT NULL,
    instrument_key character varying(80),
    instrument_version character varying(40),
    status public.shared_audit_status NOT NULL,
    submission_kind character varying(40),
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    submitted_at timestamp with time zone,
    total_minutes integer,
    summary_score double precision,
    audit_play_value_score double precision,
    audit_usability_score double precision,
    survey_play_value_score double precision,
    survey_usability_score double precision,
    responses_json jsonb NOT NULL,
    scores_json jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.playspace_submissions OWNER TO neondb_owner;

--
-- Name: project_places; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.project_places (
    project_id uuid NOT NULL,
    place_id uuid NOT NULL,
    linked_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.project_places OWNER TO neondb_owner;

--
-- Name: projects; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.projects (
    id uuid NOT NULL,
    account_id uuid NOT NULL,
    created_by_user_id uuid NOT NULL,
    name character varying(200) NOT NULL,
    overview text,
    place_types character varying(100)[] NOT NULL,
    start_date date,
    end_date date,
    est_places integer,
    est_auditors integer,
    auditor_description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.projects OWNER TO neondb_owner;

--
-- Name: users; Type: TABLE; Schema: public; Owner: neondb_owner
--

CREATE TABLE public.users (
    id uuid NOT NULL,
    email character varying(320) NOT NULL,
    password_hash character varying(255) NOT NULL,
    account_id uuid,
    account_type public.shared_account_type NOT NULL,
    name character varying(200),
    email_verified boolean DEFAULT false NOT NULL,
    email_verification_token_hash character varying(255),
    email_verification_sent_at timestamp with time zone,
    email_verified_at timestamp with time zone,
    failed_login_attempts integer DEFAULT 0 NOT NULL,
    approved boolean DEFAULT false NOT NULL,
    approved_at timestamp with time zone,
    profile_completed boolean DEFAULT false NOT NULL,
    profile_completed_at timestamp with time zone,
    last_login_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.users OWNER TO neondb_owner;

--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: accounts pk_accounts; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT pk_accounts PRIMARY KEY (id);


--
-- Name: auditor_assignments pk_auditor_assignments; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.auditor_assignments
    ADD CONSTRAINT pk_auditor_assignments PRIMARY KEY (id);


--
-- Name: auditor_invites pk_auditor_invites; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.auditor_invites
    ADD CONSTRAINT pk_auditor_invites PRIMARY KEY (id);


--
-- Name: auditor_profiles pk_auditor_profiles; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.auditor_profiles
    ADD CONSTRAINT pk_auditor_profiles PRIMARY KEY (id);


--
-- Name: audits pk_audits; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.audits
    ADD CONSTRAINT pk_audits PRIMARY KEY (id);


--
-- Name: instruments pk_instruments; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.instruments
    ADD CONSTRAINT pk_instruments PRIMARY KEY (id);


--
-- Name: manager_invites pk_manager_invites; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.manager_invites
    ADD CONSTRAINT pk_manager_invites PRIMARY KEY (id);


--
-- Name: manager_profiles pk_manager_profiles; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.manager_profiles
    ADD CONSTRAINT pk_manager_profiles PRIMARY KEY (id);


--
-- Name: notifications pk_notifications; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT pk_notifications PRIMARY KEY (id);


--
-- Name: places pk_places; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.places
    ADD CONSTRAINT pk_places PRIMARY KEY (id);


--
-- Name: playspace_audit_contexts pk_playspace_audit_contexts; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.playspace_audit_contexts
    ADD CONSTRAINT pk_playspace_audit_contexts PRIMARY KEY (audit_id);


--
-- Name: playspace_audit_sections pk_playspace_audit_sections; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.playspace_audit_sections
    ADD CONSTRAINT pk_playspace_audit_sections PRIMARY KEY (id);


--
-- Name: playspace_pre_audit_answers pk_playspace_pre_audit_answers; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.playspace_pre_audit_answers
    ADD CONSTRAINT pk_playspace_pre_audit_answers PRIMARY KEY (id);


--
-- Name: playspace_question_responses pk_playspace_question_responses; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.playspace_question_responses
    ADD CONSTRAINT pk_playspace_question_responses PRIMARY KEY (id);


--
-- Name: playspace_scale_answers pk_playspace_scale_answers; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.playspace_scale_answers
    ADD CONSTRAINT pk_playspace_scale_answers PRIMARY KEY (id);


--
-- Name: playspace_submission_contexts pk_playspace_submission_contexts; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.playspace_submission_contexts
    ADD CONSTRAINT pk_playspace_submission_contexts PRIMARY KEY (submission_id);


--
-- Name: playspace_submissions pk_playspace_submissions; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.playspace_submissions
    ADD CONSTRAINT pk_playspace_submissions PRIMARY KEY (id);


--
-- Name: project_places pk_project_places; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.project_places
    ADD CONSTRAINT pk_project_places PRIMARY KEY (project_id, place_id);


--
-- Name: projects pk_projects; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT pk_projects PRIMARY KEY (id);


--
-- Name: users pk_users; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT pk_users PRIMARY KEY (id);


--
-- Name: auditor_assignments uq_auditor_assignments_auditor_project_place; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.auditor_assignments
    ADD CONSTRAINT uq_auditor_assignments_auditor_project_place UNIQUE (auditor_profile_id, project_id, place_id);


--
-- Name: auditor_invites uq_auditor_invites_token_hash; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.auditor_invites
    ADD CONSTRAINT uq_auditor_invites_token_hash UNIQUE (token_hash);


--
-- Name: auditor_profiles uq_auditor_profiles_user_id; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.auditor_profiles
    ADD CONSTRAINT uq_auditor_profiles_user_id UNIQUE (user_id);


--
-- Name: audits uq_audits_project_place_auditor; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.audits
    ADD CONSTRAINT uq_audits_project_place_auditor UNIQUE (project_id, place_id, auditor_profile_id);


--
-- Name: manager_invites uq_manager_invites_token_hash; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.manager_invites
    ADD CONSTRAINT uq_manager_invites_token_hash UNIQUE (token_hash);


--
-- Name: manager_profiles uq_manager_profiles_user_id; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.manager_profiles
    ADD CONSTRAINT uq_manager_profiles_user_id UNIQUE (user_id);


--
-- Name: playspace_audit_sections uq_playspace_audit_sections_audit_section; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.playspace_audit_sections
    ADD CONSTRAINT uq_playspace_audit_sections_audit_section UNIQUE (audit_id, section_key);


--
-- Name: playspace_pre_audit_answers uq_playspace_pre_audit_answers_audit_field_value; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.playspace_pre_audit_answers
    ADD CONSTRAINT uq_playspace_pre_audit_answers_audit_field_value UNIQUE (audit_id, field_key, selected_value);


--
-- Name: playspace_question_responses uq_playspace_question_responses_section_question; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.playspace_question_responses
    ADD CONSTRAINT uq_playspace_question_responses_section_question UNIQUE (section_id, question_key);


--
-- Name: playspace_scale_answers uq_playspace_scale_answers_question_scale; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.playspace_scale_answers
    ADD CONSTRAINT uq_playspace_scale_answers_question_scale UNIQUE (question_response_id, scale_key);


--
-- Name: playspace_submissions uq_playspace_submissions_project_place_auditor; Type: CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.playspace_submissions
    ADD CONSTRAINT uq_playspace_submissions_project_place_auditor UNIQUE (project_id, place_id, auditor_profile_id);


--
-- Name: ix_accounts_accounts_email; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE UNIQUE INDEX ix_accounts_accounts_email ON public.accounts USING btree (email);


--
-- Name: ix_auditor_assignments_auditor_assignments_auditor_profile_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_auditor_assignments_auditor_assignments_auditor_profile_id ON public.auditor_assignments USING btree (auditor_profile_id);


--
-- Name: ix_auditor_assignments_auditor_assignments_place_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_auditor_assignments_auditor_assignments_place_id ON public.auditor_assignments USING btree (place_id);


--
-- Name: ix_auditor_assignments_auditor_assignments_project_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_auditor_assignments_auditor_assignments_project_id ON public.auditor_assignments USING btree (project_id);


--
-- Name: ix_auditor_invites_auditor_invites_account_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_auditor_invites_auditor_invites_account_id ON public.auditor_invites USING btree (account_id);


--
-- Name: ix_auditor_invites_auditor_invites_auditor_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_auditor_invites_auditor_invites_auditor_id ON public.auditor_invites USING btree (auditor_id);


--
-- Name: ix_auditor_invites_auditor_invites_email; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_auditor_invites_auditor_invites_email ON public.auditor_invites USING btree (email);


--
-- Name: ix_auditor_invites_auditor_invites_invited_by_user_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_auditor_invites_auditor_invites_invited_by_user_id ON public.auditor_invites USING btree (invited_by_user_id);


--
-- Name: ix_auditor_profiles_auditor_profiles_account_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_auditor_profiles_auditor_profiles_account_id ON public.auditor_profiles USING btree (account_id);


--
-- Name: ix_auditor_profiles_auditor_profiles_auditor_code; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE UNIQUE INDEX ix_auditor_profiles_auditor_profiles_auditor_code ON public.auditor_profiles USING btree (auditor_code);


--
-- Name: ix_auditor_profiles_auditor_profiles_email; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE UNIQUE INDEX ix_auditor_profiles_auditor_profiles_email ON public.auditor_profiles USING btree (email);


--
-- Name: ix_audits_audits_audit_code; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE UNIQUE INDEX ix_audits_audits_audit_code ON public.audits USING btree (audit_code);


--
-- Name: ix_audits_audits_auditor_profile_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_audits_audits_auditor_profile_id ON public.audits USING btree (auditor_profile_id);


--
-- Name: ix_audits_audits_place_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_audits_audits_place_id ON public.audits USING btree (place_id);


--
-- Name: ix_audits_audits_project_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_audits_audits_project_id ON public.audits USING btree (project_id);


--
-- Name: ix_manager_invites_manager_invites_accepted_by_user_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_manager_invites_manager_invites_accepted_by_user_id ON public.manager_invites USING btree (accepted_by_user_id);


--
-- Name: ix_manager_invites_manager_invites_account_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_manager_invites_manager_invites_account_id ON public.manager_invites USING btree (account_id);


--
-- Name: ix_manager_invites_manager_invites_email; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_manager_invites_manager_invites_email ON public.manager_invites USING btree (email);


--
-- Name: ix_manager_invites_manager_invites_invited_by_user_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_manager_invites_manager_invites_invited_by_user_id ON public.manager_invites USING btree (invited_by_user_id);


--
-- Name: ix_manager_profiles_account_primary_true; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE UNIQUE INDEX ix_manager_profiles_account_primary_true ON public.manager_profiles USING btree (account_id) WHERE (is_primary = true);


--
-- Name: ix_manager_profiles_manager_profiles_account_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_manager_profiles_manager_profiles_account_id ON public.manager_profiles USING btree (account_id);


--
-- Name: ix_manager_profiles_manager_profiles_email; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE UNIQUE INDEX ix_manager_profiles_manager_profiles_email ON public.manager_profiles USING btree (email);


--
-- Name: ix_notifications_notifications_created_at; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_notifications_notifications_created_at ON public.notifications USING btree (created_at);


--
-- Name: ix_notifications_notifications_is_read; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_notifications_notifications_is_read ON public.notifications USING btree (is_read);


--
-- Name: ix_notifications_notifications_user_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_notifications_notifications_user_id ON public.notifications USING btree (user_id);


--
-- Name: ix_notifications_user_unread; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_notifications_user_unread ON public.notifications USING btree (user_id, is_read);


--
-- Name: ix_playspace_audit_sections_playspace_audit_sections_audit_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_playspace_audit_sections_playspace_audit_sections_audit_id ON public.playspace_audit_sections USING btree (audit_id);


--
-- Name: ix_playspace_pre_audit_answers_playspace_pre_audit_answ_d6df; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_playspace_pre_audit_answers_playspace_pre_audit_answ_d6df ON public.playspace_pre_audit_answers USING btree (audit_id);


--
-- Name: ix_playspace_question_responses_playspace_question_resp_69b2; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_playspace_question_responses_playspace_question_resp_69b2 ON public.playspace_question_responses USING btree (section_id);


--
-- Name: ix_playspace_scale_answers_playspace_scale_answers_ques_c9b8; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_playspace_scale_answers_playspace_scale_answers_ques_c9b8 ON public.playspace_scale_answers USING btree (question_response_id);


--
-- Name: ix_playspace_submissions_playspace_submissions_audit_code; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE UNIQUE INDEX ix_playspace_submissions_playspace_submissions_audit_code ON public.playspace_submissions USING btree (audit_code);


--
-- Name: ix_playspace_submissions_playspace_submissions_auditor__cdd5; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_playspace_submissions_playspace_submissions_auditor__cdd5 ON public.playspace_submissions USING btree (auditor_profile_id);


--
-- Name: ix_playspace_submissions_playspace_submissions_place_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_playspace_submissions_playspace_submissions_place_id ON public.playspace_submissions USING btree (place_id);


--
-- Name: ix_playspace_submissions_playspace_submissions_project_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_playspace_submissions_playspace_submissions_project_id ON public.playspace_submissions USING btree (project_id);


--
-- Name: ix_projects_projects_account_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_projects_projects_account_id ON public.projects USING btree (account_id);


--
-- Name: ix_projects_projects_created_by_user_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_projects_projects_created_by_user_id ON public.projects USING btree (created_by_user_id);


--
-- Name: ix_users_users_account_id; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE INDEX ix_users_users_account_id ON public.users USING btree (account_id);


--
-- Name: ix_users_users_email; Type: INDEX; Schema: public; Owner: neondb_owner
--

CREATE UNIQUE INDEX ix_users_users_email ON public.users USING btree (email);


--
-- Name: auditor_assignments fk_auditor_assignments_auditor_profile_id_auditor_profiles; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.auditor_assignments
    ADD CONSTRAINT fk_auditor_assignments_auditor_profile_id_auditor_profiles FOREIGN KEY (auditor_profile_id) REFERENCES public.auditor_profiles(id) ON DELETE CASCADE;


--
-- Name: auditor_assignments fk_auditor_assignments_place_id_places; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.auditor_assignments
    ADD CONSTRAINT fk_auditor_assignments_place_id_places FOREIGN KEY (place_id) REFERENCES public.places(id) ON DELETE CASCADE;


--
-- Name: auditor_assignments fk_auditor_assignments_project_id_projects; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.auditor_assignments
    ADD CONSTRAINT fk_auditor_assignments_project_id_projects FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: auditor_assignments fk_auditor_assignments_project_place_pair; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.auditor_assignments
    ADD CONSTRAINT fk_auditor_assignments_project_place_pair FOREIGN KEY (project_id, place_id) REFERENCES public.project_places(project_id, place_id) ON DELETE CASCADE;


--
-- Name: auditor_invites fk_auditor_invites_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.auditor_invites
    ADD CONSTRAINT fk_auditor_invites_account_id_accounts FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: auditor_invites fk_auditor_invites_auditor_id_auditor_profiles; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.auditor_invites
    ADD CONSTRAINT fk_auditor_invites_auditor_id_auditor_profiles FOREIGN KEY (auditor_id) REFERENCES public.auditor_profiles(id) ON DELETE SET NULL;


--
-- Name: auditor_invites fk_auditor_invites_invited_by_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.auditor_invites
    ADD CONSTRAINT fk_auditor_invites_invited_by_user_id_users FOREIGN KEY (invited_by_user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: auditor_profiles fk_auditor_profiles_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.auditor_profiles
    ADD CONSTRAINT fk_auditor_profiles_account_id_accounts FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: auditor_profiles fk_auditor_profiles_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.auditor_profiles
    ADD CONSTRAINT fk_auditor_profiles_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: audits fk_audits_auditor_profile_id_auditor_profiles; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.audits
    ADD CONSTRAINT fk_audits_auditor_profile_id_auditor_profiles FOREIGN KEY (auditor_profile_id) REFERENCES public.auditor_profiles(id) ON DELETE CASCADE;


--
-- Name: audits fk_audits_place_id_places; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.audits
    ADD CONSTRAINT fk_audits_place_id_places FOREIGN KEY (place_id) REFERENCES public.places(id) ON DELETE CASCADE;


--
-- Name: audits fk_audits_project_id_projects; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.audits
    ADD CONSTRAINT fk_audits_project_id_projects FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: audits fk_audits_project_place_pair; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.audits
    ADD CONSTRAINT fk_audits_project_place_pair FOREIGN KEY (project_id, place_id) REFERENCES public.project_places(project_id, place_id) ON DELETE CASCADE;


--
-- Name: manager_invites fk_manager_invites_accepted_by_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.manager_invites
    ADD CONSTRAINT fk_manager_invites_accepted_by_user_id_users FOREIGN KEY (accepted_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: manager_invites fk_manager_invites_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.manager_invites
    ADD CONSTRAINT fk_manager_invites_account_id_accounts FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: manager_invites fk_manager_invites_invited_by_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.manager_invites
    ADD CONSTRAINT fk_manager_invites_invited_by_user_id_users FOREIGN KEY (invited_by_user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: manager_profiles fk_manager_profiles_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.manager_profiles
    ADD CONSTRAINT fk_manager_profiles_account_id_accounts FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: manager_profiles fk_manager_profiles_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.manager_profiles
    ADD CONSTRAINT fk_manager_profiles_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: notifications fk_notifications_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.notifications
    ADD CONSTRAINT fk_notifications_user_id_users FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: playspace_submissions fk_playspace_submissions_auditor_profile_id_auditor_profiles; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.playspace_submissions
    ADD CONSTRAINT fk_playspace_submissions_auditor_profile_id_auditor_profiles FOREIGN KEY (auditor_profile_id) REFERENCES public.auditor_profiles(id) ON DELETE CASCADE;


--
-- Name: playspace_submissions fk_playspace_submissions_place_id_places; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.playspace_submissions
    ADD CONSTRAINT fk_playspace_submissions_place_id_places FOREIGN KEY (place_id) REFERENCES public.places(id) ON DELETE CASCADE;


--
-- Name: playspace_submissions fk_playspace_submissions_project_id_projects; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.playspace_submissions
    ADD CONSTRAINT fk_playspace_submissions_project_id_projects FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: playspace_submissions fk_playspace_submissions_project_place_pair; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.playspace_submissions
    ADD CONSTRAINT fk_playspace_submissions_project_place_pair FOREIGN KEY (project_id, place_id) REFERENCES public.project_places(project_id, place_id) ON DELETE CASCADE;


--
-- Name: project_places fk_project_places_place_id_places; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.project_places
    ADD CONSTRAINT fk_project_places_place_id_places FOREIGN KEY (place_id) REFERENCES public.places(id) ON DELETE CASCADE;


--
-- Name: project_places fk_project_places_project_id_projects; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.project_places
    ADD CONSTRAINT fk_project_places_project_id_projects FOREIGN KEY (project_id) REFERENCES public.projects(id) ON DELETE CASCADE;


--
-- Name: projects fk_projects_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT fk_projects_account_id_accounts FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE CASCADE;


--
-- Name: projects fk_projects_created_by_user_id_users; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT fk_projects_created_by_user_id_users FOREIGN KEY (created_by_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- Name: playspace_audit_sections fk_ps_audit_section_audit; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.playspace_audit_sections
    ADD CONSTRAINT fk_ps_audit_section_audit FOREIGN KEY (audit_id) REFERENCES public.audits(id) ON DELETE CASCADE;


--
-- Name: playspace_audit_contexts fk_ps_context_audit; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.playspace_audit_contexts
    ADD CONSTRAINT fk_ps_context_audit FOREIGN KEY (audit_id) REFERENCES public.audits(id) ON DELETE CASCADE;


--
-- Name: playspace_pre_audit_answers fk_ps_pre_audit_answer_audit; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.playspace_pre_audit_answers
    ADD CONSTRAINT fk_ps_pre_audit_answer_audit FOREIGN KEY (audit_id) REFERENCES public.audits(id) ON DELETE CASCADE;


--
-- Name: playspace_question_responses fk_ps_question_response_section; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.playspace_question_responses
    ADD CONSTRAINT fk_ps_question_response_section FOREIGN KEY (section_id) REFERENCES public.playspace_audit_sections(id) ON DELETE CASCADE;


--
-- Name: playspace_scale_answers fk_ps_scale_answer_question_response; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.playspace_scale_answers
    ADD CONSTRAINT fk_ps_scale_answer_question_response FOREIGN KEY (question_response_id) REFERENCES public.playspace_question_responses(id) ON DELETE CASCADE;


--
-- Name: playspace_submission_contexts fk_ps_submission_context_submission; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.playspace_submission_contexts
    ADD CONSTRAINT fk_ps_submission_context_submission FOREIGN KEY (submission_id) REFERENCES public.playspace_submissions(id) ON DELETE CASCADE;


--
-- Name: users fk_users_account_id_accounts; Type: FK CONSTRAINT; Schema: public; Owner: neondb_owner
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT fk_users_account_id_accounts FOREIGN KEY (account_id) REFERENCES public.accounts(id) ON DELETE SET NULL;


--
-- PostgreSQL database dump complete
--

\unrestrict gV4SJg7f5Oz17VXPnrAdSQYdLoaOoGHGLEzTmVKBs8xyeW3veEU4UR2AwhiuMEK


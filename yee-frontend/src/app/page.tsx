"use client";

import * as React from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

interface InstrumentItem {
	item_id: string;
	base_question_id: string;
	block: string;
	question_text: string;
	choices: Record<string, { Display?: string }>;
	answers: Record<string, { Display?: string }>;
}

interface InstrumentResponse {
	survey_name: string;
	version: string;
	scoring_items: InstrumentItem[];
}

interface AuditScore {
	total_score: number;
	section_scores: Record<string, number>;
	category_scores: Record<string, number>;
	matched_scored_answers: number;
}

interface SubmitResponse {
	id: string;
	score: AuditScore;
}

type ResponsesState = Record<string, string | Record<string, string>>;

function getChoiceLabel(choice: { Display?: string } | undefined, fallback: string): string {
	if (!choice?.Display) return fallback;
	return choice.Display;
}

export default function HomePage() {
	const [instrument, setInstrument] = React.useState<InstrumentResponse | null>(null);
	const [responses, setResponses] = React.useState<ResponsesState>({});
	const [participantName, setParticipantName] = React.useState("");
	const [loading, setLoading] = React.useState(true);
	const [submitting, setSubmitting] = React.useState(false);
	const [error, setError] = React.useState<string | null>(null);
	const [result, setResult] = React.useState<SubmitResponse | null>(null);

	React.useEffect(() => {
		async function loadInstrument() {
			try {
				setLoading(true);
				setError(null);
				const response = await fetch("/api/yee/instrument", { cache: "no-store" });
				if (!response.ok) {
					const body = await response.json().catch(() => null);
					const message =
						body && typeof body === "object" && "error" in body
							? `${String(body.error)} (${response.status})`
							: `Failed to load instrument (${response.status})`;
					throw new Error(message);
				}
				const data: InstrumentResponse = await response.json();
				setInstrument(data);
			} catch (err) {
				setError(err instanceof Error ? err.message : "Failed to load instrument.");
			} finally {
				setLoading(false);
			}
		}

		loadInstrument();
	}, []);

	function updateSingleResponse(itemId: string, choiceId: string) {
		setResponses(prev => ({ ...prev, [itemId]: choiceId }));
	}

	function updateMatrixResponse(itemId: string, rowId: string, answerId: string) {
		setResponses(prev => {
			const existing = prev[itemId];
			const matrix = typeof existing === "object" && existing ? { ...existing } : {};
			matrix[rowId] = answerId;
			return { ...prev, [itemId]: matrix };
		});
	}

	async function submitAudit() {
		try {
			setSubmitting(true);
			setError(null);
			setResult(null);
			const payload = {
				participant_info: {
					auditor_name: participantName.trim() || null
				},
				responses
			};

			const response = await fetch("/api/yee/audits", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify(payload)
			});

			if (!response.ok) {
				const body = await response.text();
				throw new Error(`Submit failed (${response.status}): ${body}`);
			}

			const data: SubmitResponse = await response.json();
			setResult(data);
		} catch (err) {
			setError(err instanceof Error ? err.message : "Failed to submit audit.");
		} finally {
			setSubmitting(false);
		}
	}

	if (loading) {
		return <main className="mx-auto max-w-5xl p-6">Loading YEE instrument...</main>;
	}

	if (error && !instrument) {
		return <main className="mx-auto max-w-5xl p-6 text-red-700">{error}</main>;
	}

	return (
		<main className="mx-auto max-w-5xl space-y-6 p-6">
			<header className="space-y-2">
				<h1 className="text-3xl font-semibold">Youth Enabling Environments Audit Tool</h1>
				<p className="text-sm text-muted-foreground">
					Survey: {instrument?.survey_name} | Version: {instrument?.version}
				</p>
			</header>

			<Card>
				<CardHeader>
					<CardTitle>Participant Info</CardTitle>
				</CardHeader>
				<CardContent>
					<div className="space-y-2">
						<Label htmlFor="auditor_name">Auditor Name (optional)</Label>
						<Input
							id="auditor_name"
							value={participantName}
							onChange={event => setParticipantName(event.target.value)}
							placeholder="Your name"
						/>
					</div>
				</CardContent>
			</Card>

			{instrument?.scoring_items.map(item => {
				const choices = Object.entries(item.choices || {});
				const answers = Object.entries(item.answers || {});
				const isMatrix = answers.length > 0;
				const currentValue = responses[item.item_id];

				return (
					<Card key={item.item_id}>
						<CardHeader>
							<CardTitle className="text-base">{item.block}</CardTitle>
							<p className="text-sm text-muted-foreground">{item.question_text || item.item_id}</p>
						</CardHeader>
						<CardContent className="space-y-4">
							{isMatrix ? (
								choices.map(([choiceId, choice]) => {
									const selected =
										typeof currentValue === "object" && currentValue ? currentValue[choiceId] || "" : "";
									return (
										<div key={`${item.item_id}-${choiceId}`} className="grid gap-2 md:grid-cols-[1fr_240px] md:items-center">
											<p className="text-sm">{getChoiceLabel(choice, choiceId)}</p>
											<select
												className="rounded-md border px-3 py-2 text-sm"
												value={selected}
												onChange={event => updateMatrixResponse(item.item_id, choiceId, event.target.value)}
											>
												<option value="">Select one</option>
												{answers.map(([answerId, answer]) => (
													<option key={answerId} value={answerId}>
														{getChoiceLabel(answer, answerId)}
													</option>
												))}
											</select>
										</div>
									);
								})
							) : (
								<div className="space-y-2">
									{choices.map(([choiceId, choice]) => (
										<label key={`${item.item_id}-${choiceId}`} className="flex items-center gap-2 text-sm">
											<input
												type="radio"
												name={item.item_id}
												value={choiceId}
												checked={currentValue === choiceId}
												onChange={() => updateSingleResponse(item.item_id, choiceId)}
											/>
											{getChoiceLabel(choice, choiceId)}
										</label>
									))}
								</div>
							)}
						</CardContent>
					</Card>
				);
			})}

			<div className="flex items-center gap-3">
				<Button onClick={submitAudit} disabled={submitting || !instrument}>
					{submitting ? "Submitting..." : "Submit YEE Audit"}
				</Button>
				{error ? <span className="text-sm text-red-700">{error}</span> : null}
			</div>

			{result ? (
				<Card>
					<CardHeader>
						<CardTitle>Submission Saved</CardTitle>
					</CardHeader>
					<CardContent className="space-y-3 text-sm">
						<p>
							Submission ID: <span className="font-mono">{result.id}</span>
						</p>
						<p className="text-lg font-semibold">Total Score: {result.score.total_score}</p>
						<div className="space-y-1">
							{Object.entries(result.score.section_scores).map(([section, score]) => (
								<p key={section}>
									{section}: <span className="font-medium">{score}</span>
								</p>
							))}
						</div>
					</CardContent>
				</Card>
			) : null}
		</main>
	);
}

import { NextResponse } from "next/server";

function getApiBaseUrl(): string {
	return process.env.API_BASE_URL || process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";
}

export async function GET() {
	const targetUrl = `${getApiBaseUrl()}/yee/instrument`;
	try {
		const response = await fetch(targetUrl, {
			headers: { "Content-Type": "application/json" },
			cache: "no-store"
		});

		if (!response.ok) {
			const body = await response.text();
			return NextResponse.json(
				{ error: `Backend returned ${response.status}`, details: body, targetUrl },
				{ status: response.status }
			);
		}

		const data = await response.json();
		return NextResponse.json(data);
	} catch (error) {
		return NextResponse.json(
			{
				error: "Could not reach backend",
				details: error instanceof Error ? error.message : String(error),
				targetUrl
			},
			{ status: 502 }
		);
	}
}

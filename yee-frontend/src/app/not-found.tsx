import Link from "next/link";

import { Button } from "@/components/ui/button";

export default function NotFound() {
	return (
		<div className="flex min-h-dvh items-center justify-center bg-background px-4">
			<div className="w-full max-w-md space-y-4 rounded-xl border bg-card p-6 text-card-foreground">
				<div className="space-y-1">
					<h1 className="text-xl font-semibold tracking-tight">Page not found</h1>
					<p className="text-sm text-muted-foreground">The page you&apos;re looking for doesn&apos;t exist.</p>
				</div>
				<Button asChild>
					<Link href="/">Back to survey</Link>
				</Button>
			</div>
		</div>
	);
}

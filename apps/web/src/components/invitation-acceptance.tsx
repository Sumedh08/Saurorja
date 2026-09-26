"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api, csrfHeaders, type AuthState } from "@/lib/api";

type Attempt = {
  id: string;
  organization: { id: string; name: string };
  email: string;
  role: string;
  expires_at: string;
};

export function InvitationAcceptance() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const invitationToken = useRef<string | null>(null);
  const tokenProcessingStarted = useRef(false);
  const [attemptId, setAttemptId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const session = useQuery({ queryKey: ["auth-session"], queryFn: () => api<AuthState>("/api/v1/auth/session"), retry: false });
  const csrf = useQuery({
    queryKey: ["csrf-token"],
    queryFn: () => api<{ csrf_token: string }>("/api/v1/auth/csrf"),
    enabled: session.data?.state === "authenticated" || session.data?.state === "pending_identity",
    retry: false,
  });
  const attemptFromUrl = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("attempt") : null;
  const attemptIdToLoad = attemptId ?? attemptFromUrl;
  const attempt = useQuery({
    queryKey: ["invitation-attempt", attemptIdToLoad],
    queryFn: () => api<Attempt>(`/api/v1/invitation-acceptance-attempts/${attemptIdToLoad}`),
    enabled: Boolean(attemptIdToLoad) && (session.data?.state === "authenticated" || session.data?.state === "pending_identity"),
    retry: false,
  });

  useEffect(() => {
    const hash = new URLSearchParams(window.location.hash.slice(1));
    const value = hash.get("t");
    if (window.location.hash) window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
    if (value) invitationToken.current = value;
  }, []);

  useEffect(() => {
    const token = invitationToken.current;
    if (!token || !session.data || tokenProcessingStarted.current) return;
    if (session.data.state === "anonymous") {
      const form = document.createElement("form");
      form.method = "POST";
      form.action = "/api/v1/auth/login";
      const input = document.createElement("input");
      input.type = "hidden";
      input.name = "token";
      input.value = token;
      form.appendChild(input);
      document.body.appendChild(form);
      invitationToken.current = null;
      tokenProcessingStarted.current = true;
      form.submit();
      return;
    }
    if (!csrf.data?.csrf_token) return;
    tokenProcessingStarted.current = true;
    invitationToken.current = null;
    void api<{ id: string; expires_at: string }>("/api/v1/invitation-acceptance-attempts", {
      method: "POST",
      headers: csrfHeaders(csrf.data.csrf_token),
      body: JSON.stringify({ token }),
    }).then((created) => {
      setAttemptId(created.id);
      router.replace(`/invitations/accept?attempt=${created.id}`);
    }).catch((issue: unknown) => {
      setError(issue instanceof Error ? issue.message : "This invitation is unavailable.");
    });
  }, [session.data, csrf.data, router]);

  async function accept() {
    if (!attemptIdToLoad || !csrf.data?.csrf_token) return;
    setBusy(true);
    setError(null);
    try {
      const accepted = await api<{ organization_id: string }>(`/api/v1/invitation-acceptance-attempts/${attemptIdToLoad}/accept`, {
        method: "POST",
        headers: csrfHeaders(csrf.data.csrf_token),
        body: JSON.stringify({ confirm: true }),
      });
      queryClient.removeQueries({ queryKey: ["csrf-token"] });
      await queryClient.invalidateQueries({ queryKey: ["auth-session"] });
      router.replace(`/organizations/${accepted.organization_id}`);
    } catch (issue) {
      const message = issue instanceof ApiError && issue.code === "identity_conflict"
        ? "This verified identity conflicts with an existing Saurorja account. Contact the installation operator for assistance."
        : issue instanceof Error ? issue.message : "The invitation could not be accepted.";
      setError(message);
    } finally {
      setBusy(false);
    }
  }

  if (session.isPending) return <InvitationPage message="Checking your sign-in status…" />;
  if (session.isError) return <InvitationPage message="The Saurorja API is unavailable. Refresh to try again." error />;
  if (session.data.state === "anonymous" && !attemptIdToLoad) {
    return <InvitationPage message="Checking the invitation link. If no invitation was supplied, open the original link again." />;
  }
  if (busy) return <InvitationPage message="Processing your invitation…" />;
  if (error) return <InvitationPage message={error} error />;
  if (!attemptIdToLoad) return <InvitationPage message="Verifying the invitation…" />;
  if (attempt.isPending) return <InvitationPage message="Loading invitation details…" />;
  if (attempt.isError) return <InvitationPage message="This invitation is unavailable or no longer bound to this sign-in." error />;

  return (
    <main className="page-shell">
      <section className="status-card">
        <h1 className="brand">Accept invitation</h1>
        <p className="tagline">Review your Saurorja access</p>
        <dl className="status-list">
          <div className="status-row"><dt>Organization</dt><dd>{attempt.data.organization.name}</dd></div>
          <div className="status-row"><dt>Invited email</dt><dd>{attempt.data.email}</dd></div>
          <div className="status-row"><dt>Role</dt><dd>{attempt.data.role}</dd></div>
        </dl>
        <p className="muted">Your OIDC identity must have a verified email matching this invitation.</p>
        <button className="button primary" type="button" disabled={!csrf.data || busy} onClick={() => void accept()}>Accept invitation</button>
      </section>
    </main>
  );
}

function InvitationPage({ message, error = false }: Readonly<{ message: string; error?: boolean }>) {
  return <main className="page-shell"><section className="status-card"><h1 className="brand">Saurorja</h1><p className="tagline">Invitation access</p><p role={error ? "alert" : "status"} className={error ? "notice" : "muted"}>{message}</p></section></main>;
}

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { ApiError, api, type AuthState, type Organization, type UserProfile } from "@/lib/api";

export function AuthShell() {
  const router = useRouter();
  const session = useQuery({
    queryKey: ["auth-session"],
    queryFn: () => api<AuthState>("/api/v1/auth/session"),
    retry: false,
    refetchOnWindowFocus: true,
  });
  const profile = useQuery({
    queryKey: ["me"],
    queryFn: () => api<UserProfile>("/api/v1/me"),
    enabled: session.data?.state === "authenticated",
    retry: false,
  });
  const organizations = useQuery({
    queryKey: ["organizations"],
    queryFn: () => api<{ items: Organization[]; next_cursor: null }>("/api/v1/organizations"),
    enabled: session.data?.state === "authenticated",
  });
  const onlyOrganizationId = organizations.data?.items.length === 1 ? organizations.data.items[0].id : null;
  useEffect(() => {
    if (onlyOrganizationId) router.replace(`/organizations/${onlyOrganizationId}`);
  }, [onlyOrganizationId, router]);
  const pending = useQuery({
    queryKey: ["pending-identity"],
    queryFn: () => api<{ invitation: { attempt_id: string; organization_name: string; email: string; role: string } | null }>("/api/v1/auth/pending"),
    enabled: session.data?.state === "pending_identity",
    retry: false,
  });

  if (session.isPending) return <ShellMessage title="Saurorja" message="Checking your sign-in status…" />;
  if (session.isError) return <ShellMessage title="Saurorja" message="The Saurorja API is unavailable. Refresh to try again." />;
  if (session.data.state === "anonymous") {
    return (
      <main className="page-shell">
        <section className="status-card" aria-labelledby="product-name">
          <h1 className="brand" id="product-name">Saurorja</h1>
          <p className="tagline">Distributed Solar Intelligence</p>
          <p className="muted">Sign in with your organization’s identity provider to continue.</p>
          <a className="button primary" href="/api/v1/auth/login">Sign in</a>
          <p className="footer">Access is provided by an Organization Owner or installation operator.</p>
        </section>
      </main>
    );
  }
  if (session.data.state === "pending_identity") {
    if (pending.isPending) return <ShellMessage title="Complete your invitation" message="Checking for an invitation associated with this sign-in…" />;
    if (pending.isError) return <ShellMessage title="Complete your invitation" message="Your identity is signed in but does not yet have Saurorja access." />;
    if (!pending.data.invitation) {
      return <ShellMessage title="No Saurorja access yet" message="Your identity is authenticated, but Saurorja access requires an invitation. Ask an Organization Owner or installation operator." />;
    }
    return (
      <main className="page-shell">
        <section className="status-card">
          <h1 className="brand">Invitation ready</h1>
          <p className="tagline">You’ve been invited to {pending.data.invitation.organization_name} as {pending.data.invitation.role}.</p>
          <Link className="button primary" href={`/invitations/accept?attempt=${pending.data.invitation.attempt_id}`}>Review invitation</Link>
          <LogoutButton />
        </section>
      </main>
    );
  }

  if (profile.isPending || organizations.isPending) {
    return <ShellMessage title="Saurorja" message="Loading your Organization access…" />;
  }
  if (profile.isError || organizations.isError) {
    return <ShellMessage title="Saurorja" message="Your account is signed in, but Organization access could not be loaded." />;
  }
  const items = organizations.data.items;
  if (items.length === 1) {
    return <ShellMessage title="Saurorja" message="Opening your Organization…" />;
  }
  return (
    <main className="page-shell">
      <section className="status-card">
        <div className="topbar">
          <div>
            <h1 className="brand">Saurorja</h1>
            <p className="tagline">Choose an Organization</p>
          </div>
          <LogoutButton />
        </div>
        <p className="muted">Signed in as {profile.data.display_name ?? profile.data.email ?? "Saurorja user"}.</p>
        {items.length === 0 ? (
          <p className="notice" role="status">Your account does not currently have an active Organization membership.</p>
        ) : (
          <ul className="org-list">
            {items.map((organization) => (
              <li key={organization.id}>
                <Link className="org-link" href={`/organizations/${organization.id}`}>
                  <span>{organization.name}</span><span className="role-pill">{organization.role}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}

function ShellMessage({ title, message }: Readonly<{ title: string; message: string }>) {
  return <main className="page-shell"><section className="status-card"><h1 className="brand">{title}</h1><p className="tagline">Distributed Solar Intelligence</p><p className="muted" role="status">{message}</p></section></main>;
}

export function LogoutButton() {
  const csrf = useQuery({
    queryKey: ["csrf-token"],
    queryFn: () => api<{ csrf_token: string }>("/api/v1/auth/csrf"),
    retry: false,
  });
  return (
    <button
      className="button quiet"
      type="button"
      disabled={csrf.isPending}
      onClick={() => {
        const form = document.createElement("form");
        form.method = "POST";
        form.action = "/api/v1/auth/logout";
        if (csrf.data?.csrf_token) {
          const input = document.createElement("input");
          input.type = "hidden";
          input.name = "csrf_token";
          input.value = csrf.data.csrf_token;
          form.appendChild(input);
        }
        document.body.appendChild(form);
        form.submit();
      }}
    >
      Sign out
    </button>
  );
}

export function isNoAccess(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 401 || error.status === 403);
}

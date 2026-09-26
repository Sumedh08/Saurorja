"use client";

import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import { LogoutButton } from "@/components/auth-shell";
import { api, csrfHeaders, type Invitation, type Membership, type Organization, type Role } from "@/lib/api";

type Csrf = { csrf_token: string };

export function OrganizationScreen({ organizationId }: Readonly<{ organizationId: string }>) {
  const queryClient = useQueryClient();
  const organizationQuery = useQuery({
    queryKey: ["organization", organizationId],
    queryFn: () => api<Organization>(`/api/v1/organizations/${organizationId}`),
    retry: false,
  });
  const ownQuery = useQuery({
    queryKey: ["membership-me", organizationId],
    queryFn: () => api<Membership>(`/api/v1/organizations/${organizationId}/memberships/me`),
    retry: false,
  });
  const csrfQuery = useQuery({ queryKey: ["csrf-token"], queryFn: () => api<Csrf>("/api/v1/auth/csrf"), retry: false });
  const role = ownQuery.data?.role;
  const canSeeMembers = role === "OWNER" || role === "ADMIN" || role === "MANAGER";
  const canAdminister = role === "OWNER" || role === "ADMIN";
  const membersQuery = useQuery({
    queryKey: ["members", organizationId],
    queryFn: () => api<{ items: Membership[] }>(`/api/v1/organizations/${organizationId}/memberships`),
    enabled: canSeeMembers,
  });
  const invitationsQuery = useQuery({
    queryKey: ["invitations", organizationId],
    queryFn: () => api<{ items: Invitation[] }>(`/api/v1/organizations/${organizationId}/invitations`),
    enabled: canAdminister,
  });
  const [actionError, setActionError] = useState<string | null>(null);

  async function mutate(path: string, method: string, body?: unknown) {
    setActionError(null);
    if (!csrfQuery.data?.csrf_token) return;
    try {
      await api(path, {
        method,
        headers: csrfHeaders(csrfQuery.data.csrf_token),
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["organization", organizationId] }),
        queryClient.invalidateQueries({ queryKey: ["membership-me", organizationId] }),
        queryClient.invalidateQueries({ queryKey: ["members", organizationId] }),
        queryClient.invalidateQueries({ queryKey: ["invitations", organizationId] }),
      ]);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "The operation could not be completed.");
    }
  }

  if (organizationQuery.isPending || ownQuery.isPending) return <ShellState message="Loading Organization…" />;
  if (organizationQuery.isError || ownQuery.isError) {
    return <ShellState message="This Organization is unavailable or you do not have active access." />;
  }

  return (
    <main className="page-shell page-shell-top">
      <section className="status-card wide-card">
        <div className="topbar">
          <div>
            <p className="eyebrow"><Link href="/">Organizations</Link> / {organizationQuery.data.name}</p>
            <h1 className="brand">{organizationQuery.data.name}</h1>
            <p className="tagline">Organization access · {ownQuery.data.role}</p>
          </div>
          <LogoutButton />
        </div>
        {actionError && <p className="notice" role="alert">{actionError}</p>}
        {canAdminister && <OrganizationNameForm organization={organizationQuery.data} onSave={(name) => mutate(`/api/v1/organizations/${organizationId}`, "PATCH", { name })} />}
        <section className="panel">
          <h2>Your membership</h2>
          <p>{ownQuery.data.display_name ?? "Saurorja member"} · {ownQuery.data.role} · {ownQuery.data.status}</p>
          {ownQuery.data.email && <p className="muted">{ownQuery.data.email}</p>}
        </section>
        {canSeeMembers && (
          <section className="panel">
            <h2>Members</h2>
            {membersQuery.isPending && <p className="muted">Loading members…</p>}
            {membersQuery.isError && <p className="notice">Member information is unavailable.</p>}
            {membersQuery.data && <ul className="rows-list">{membersQuery.data.items.map((member) => (
              <MemberRow key={member.membership_id} member={member} organizationId={organizationId} actorRole={role!} ownId={ownQuery.data.membership_id} onMutate={mutate} />
            ))}</ul>}
          </section>
        )}
        {canAdminister && (
          <InvitationPanel
            organizationId={organizationId}
            invitations={invitationsQuery.data?.items ?? []}
            onMutate={mutate}
            onCreated={() => queryClient.invalidateQueries({ queryKey: ["invitations", organizationId] })}
          />
        )}
      </section>
    </main>
  );
}

function MemberRow({ member, organizationId, actorRole, ownId, onMutate }: Readonly<{ member: Membership; organizationId: string; actorRole: Role; ownId: string; onMutate: (path: string, method: string, body?: unknown) => Promise<void> }>) {
  const actorIsOwner = actorRole === "OWNER";
  const mayManage = actorIsOwner || actorRole === "ADMIN";
  const canTouch = mayManage && (actorIsOwner || member.role !== "OWNER");
  const allowedRoles: Role[] = actorIsOwner ? ["OWNER", "ADMIN", "MANAGER", "TECHNICIAN", "VIEWER"] : ["ADMIN", "MANAGER", "TECHNICIAN", "VIEWER"];
  if (!canTouch) {
    return <li className="member-row"><MemberSummary member={member} /></li>;
  }
  return (
    <li className="member-row">
      <MemberSummary member={member} />
      {member.status !== "REMOVED" && <div className="member-actions">
        <select aria-label={`Role for ${member.display_name ?? "member"}`} value={member.role} onChange={(event) => void onMutate(`/api/v1/organizations/${organizationId}/memberships/${member.membership_id}/role`, "PATCH", { role: event.target.value })}>
          {allowedRoles.map((value) => <option key={value} value={value}>{value}</option>)}
        </select>
        {member.status === "ACTIVE" ? (
          <button className="button quiet small" type="button" onClick={() => void onMutate(`${membershipPath(organizationId, member)}/suspend`, "POST", {})}>Suspend</button>
        ) : (
          <button className="button quiet small" type="button" onClick={() => void onMutate(`${membershipPath(organizationId, member)}/reactivate`, "POST", {})}>Reactivate</button>
        )}
        <button className="button danger small" type="button" onClick={() => void onMutate(membershipPath(organizationId, member), "DELETE")}>Remove</button>
      </div>}
      {member.status === "REMOVED" && <span className="muted">Removed</span>}
      {member.membership_id === ownId && <span className="muted">You</span>}
    </li>
  );
}

function membershipPath(organizationId: string, member: Membership): string {
  return `/api/v1/organizations/${organizationId}/memberships/${member.membership_id}`;
}

function MemberSummary({ member }: Readonly<{ member: Membership }>) {
  return <div><strong>{member.display_name ?? "Saurorja member"}</strong>{member.email && <span className="member-email">{member.email}</span>}<span className="member-meta">{member.role} · {member.status}</span></div>;
}

function OrganizationNameForm({ organization, onSave }: Readonly<{ organization: Organization; onSave: (name: string) => Promise<void> }>) {
  const [name, setName] = useState(organization.name);
  return <form className="inline-form" onSubmit={(event) => { event.preventDefault(); void onSave(name); }}><label htmlFor="org-name">Organization name</label><input id="org-name" value={name} onChange={(event) => setName(event.target.value)} maxLength={200} required /><button className="button quiet" type="submit">Save</button></form>;
}

function InvitationPanel({ organizationId, invitations, onMutate, onCreated }: Readonly<{ organizationId: string; invitations: Invitation[]; onMutate: (path: string, method: string, body?: unknown) => Promise<void>; onCreated: () => void | Promise<unknown> }>) {
  const csrf = useQuery({ queryKey: ["csrf-token"], queryFn: () => api<Csrf>("/api/v1/auth/csrf"), retry: false });
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Exclude<Role, "OWNER">>("VIEWER");
  const [createdUrl, setCreatedUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setCreatedUrl(null);
    try {
      const result = await api<{ invitation_url: string }>(`/api/v1/organizations/${organizationId}/invitations`, {
        method: "POST",
        headers: csrfHeaders(csrf.data?.csrf_token ?? ""),
        body: JSON.stringify({ email, role }),
      });
      setCreatedUrl(result.invitation_url);
      setEmail("");
      await onCreated();
    } catch (issue) {
      setError(issue instanceof Error ? issue.message : "The invitation could not be created.");
    }
  }
  return (
    <section className="panel">
      <h2>Invitations</h2>
      <form className="inline-form invitation-form" onSubmit={create}>
        <label htmlFor="invite-email">Email address</label><input id="invite-email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} required />
        <label htmlFor="invite-role">Role</label><select id="invite-role" value={role} onChange={(event) => setRole(event.target.value as Exclude<Role, "OWNER">)}><option>VIEWER</option><option>TECHNICIAN</option><option>MANAGER</option><option>ADMIN</option></select>
        <button className="button primary" type="submit" disabled={!csrf.data}>Create invitation</button>
      </form>
      {error && <p role="alert" className="notice">{error}</p>}
      {createdUrl && <div className="copy-once"><p>Copy this invitation link now. It will not be shown again.</p><input aria-label="One-time invitation link" readOnly value={createdUrl} /><button className="button quiet" type="button" onClick={() => void navigator.clipboard.writeText(createdUrl)}>Copy link</button></div>}
      {invitations.length > 0 && <ul className="rows-list">{invitations.map((invitation) => <li className="member-row" key={invitation.id}><div><strong>{invitation.email}</strong><span className="member-meta">{invitation.role} · {invitation.status} · expires {new Date(invitation.expires_at).toLocaleDateString()}</span></div>{invitation.status === "PENDING" && <button className="button quiet small" type="button" onClick={() => void onMutate(`/api/v1/organizations/${organizationId}/invitations/${invitation.id}/revoke`, "POST", {})}>Revoke</button>}</li>)}</ul>}
    </section>
  );
}

function ShellState({ message }: Readonly<{ message: string }>) {
  const bad = message.includes("unavailable");
  return <main className="page-shell"><section className="status-card"><h1 className="brand">Saurorja</h1><p className="tagline">Distributed Solar Intelligence</p><p role={bad ? "alert" : "status"} className={bad ? "notice" : "muted"}>{message}</p></section></main>;
}

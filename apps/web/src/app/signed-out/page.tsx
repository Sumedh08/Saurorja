import Link from "next/link";

export default function SignedOutPage() {
  return <main className="page-shell"><section className="status-card"><h1 className="brand">Signed out</h1><p className="tagline">Your Saurorja session has ended.</p><Link className="button primary" href="/">Return to Saurorja</Link></section></main>;
}

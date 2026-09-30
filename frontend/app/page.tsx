/**
 * Root role-router: forwards authenticated users to their shell home.
 *
 * Flow:
 *   1. Read the gst_role cookie on the server.
 *   2. CA → /ca, CLIENT → /app, anything else → /login.
 *
 * Debug:
 *   This cookie is only a hint. Each shell still calls silentRefresh and /auth/me.
 */
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

export default async function RootPage() {
  const cookieStore = await cookies();
  const role = cookieStore.get("gst_role")?.value ?? null;
  if (role === "CA") redirect("/ca");
  if (role === "CLIENT") redirect("/app");
  redirect("/login");
}
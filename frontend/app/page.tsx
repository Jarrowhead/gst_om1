/** Root role-router: forwards authenticated users to their shell home. */
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

export default async function RootPage() {
  const cookieStore = await cookies();
  const role = cookieStore.get("gst_role")?.value ?? null;
  if (role === "CA") redirect("/ca");
  if (role === "CLIENT") redirect("/app");
  redirect("/login");
}
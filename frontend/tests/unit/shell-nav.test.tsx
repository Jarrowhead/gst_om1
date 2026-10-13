import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ShellNav } from "@/components/shared/ShellNav";

// Mock next/navigation hooks
vi.mock("next/navigation", () => ({
  usePathname: () => "/app",
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
  }),
}));

// Mock Link from next/link
vi.mock("next/link", () => ({
  default: ({ children, href, ...rest }: React.ComponentProps<"a">) => (
    <a href={href} {...rest}>
      {children}
    </a>
  ),
}));

describe("<ShellNav /> Component", () => {
  const defaultAccounts = [
    { gstin: "27AAPFU0939F1ZV", legal_name: "Sunrise Traders Pvt Ltd", role: "ADMIN" },
  ];

  it("renders the brand title and role badge", () => {
    render(
      <ShellNav
        roleLabel="GST Filing"
        userName="Tony Stark"
        onSignOut={vi.fn()}
        gstAccounts={defaultAccounts}
      />
    );

    expect(screen.getAllByText("GST Filing").length).toBe(2);
    expect(screen.getByTestId("shell-role")).toHaveTextContent("GST Filing");
  });

  it("renders the user name in the user chip", () => {
    render(
      <ShellNav
        roleLabel="GST Filing"
        userName="Tony Stark"
        onSignOut={vi.fn()}
        gstAccounts={defaultAccounts}
      />
    );

    expect(screen.getByText("Tony Stark")).toBeInTheDocument();
  });

  it("renders primary navigation links", () => {
    render(
      <ShellNav
        roleLabel="GST Filing"
        userName="Tony Stark"
        onSignOut={vi.fn()}
        gstAccounts={defaultAccounts}
      />
    );

    expect(screen.getByRole("link", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Businesses" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Upload" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Returns" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Filing" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "ITC" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Profile" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Settings" })).toBeInTheDocument();
  });

  it("surfaces Clients link only when user has multiple GST accounts", () => {
    const { rerender } = render(
      <ShellNav
        roleLabel="GST Filing"
        userName="Tony Stark"
        onSignOut={vi.fn()}
        gstAccounts={defaultAccounts} // single account
      />
    );
    expect(screen.queryByRole("link", { name: "Clients" })).not.toBeInTheDocument();

    const multiAccounts = [
      ...defaultAccounts,
      { gstin: "29AABCU9603R1ZJ", legal_name: "Karnataka Components LLP", role: "OWNER" },
    ];

    rerender(
      <ShellNav
        roleLabel="GST Filing"
        userName="Tony Stark"
        onSignOut={vi.fn()}
        gstAccounts={multiAccounts} // multi accounts
      />
    );
    expect(screen.getByRole("link", { name: "Clients" })).toBeInTheDocument();
  });

  it("fires onSignOut callback when clicking Sign out button", () => {
    const handleSignOut = vi.fn();
    render(
      <ShellNav
        roleLabel="GST Filing"
        userName="Tony Stark"
        onSignOut={handleSignOut}
        gstAccounts={defaultAccounts}
      />
    );

    const signOutBtn = screen.getByRole("button", { name: /sign out/i });
    fireEvent.click(signOutBtn);
    expect(handleSignOut).toHaveBeenCalledTimes(1);
  });
});

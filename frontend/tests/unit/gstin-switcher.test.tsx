import React from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { GstinSwitcher } from "@/components/shared/GstinSwitcher";

const mockAccounts = [
  { gstin: "27AAPFU0939F1ZV", legal_name: "Sunrise Traders Pvt Ltd", role: "ADMIN" },
  { gstin: "29AABCU9603R1ZJ", legal_name: "Karnataka Components LLP", role: "OWNER" },
  { gstin: "07AAACR5055K1Z9", legal_name: "Delhi Retail House", role: "ADMIN" },
];

describe("<GstinSwitcher /> Component", () => {
  it("renders trigger button with current GSTIN value", () => {
    const onSelect = vi.fn();
    render(<GstinSwitcher accounts={mockAccounts} value="27AAPFU0939F1ZV" onSelect={onSelect} />);

    const trigger = screen.getByTestId("gstin-switcher");
    expect(trigger).toBeInTheDocument();
    expect(trigger).toHaveTextContent("27AAPFU0939F1ZV");
  });

  it("opens dropdown panel on click and lists all accounts", () => {
    const onSelect = vi.fn();
    render(<GstinSwitcher accounts={mockAccounts} value="27AAPFU0939F1ZV" onSelect={onSelect} />);

    const trigger = screen.getByTestId("gstin-switcher");
    fireEvent.click(trigger);

    expect(screen.getByTestId("gstin-switcher-search")).toBeInTheDocument();
    expect(screen.getByText("Sunrise Traders Pvt Ltd")).toBeInTheDocument();
    expect(screen.getByText("Karnataka Components LLP")).toBeInTheDocument();
    expect(screen.getByText("Delhi Retail House")).toBeInTheDocument();
  });

  it("filters accounts live as user types in the search input", () => {
    const onSelect = vi.fn();
    render(<GstinSwitcher accounts={mockAccounts} value="27AAPFU0939F1ZV" onSelect={onSelect} />);

    fireEvent.click(screen.getByTestId("gstin-switcher"));
    const searchInput = screen.getByTestId("gstin-switcher-search");

    fireEvent.change(searchInput, { target: { value: "Karnataka" } });
    expect(screen.getByText("Karnataka Components LLP")).toBeInTheDocument();
    expect(screen.queryByText("Delhi Retail House")).not.toBeInTheDocument();
  });

  it("fires onSelect callback when clicking an account option", () => {
    const onSelect = vi.fn();
    render(<GstinSwitcher accounts={mockAccounts} value="27AAPFU0939F1ZV" onSelect={onSelect} />);

    fireEvent.click(screen.getByTestId("gstin-switcher"));
    const targetOption = screen.getByText("Karnataka Components LLP");
    fireEvent.click(targetOption);

    expect(onSelect).toHaveBeenCalledWith("29AABCU9603R1ZJ");
  });

  it("shows empty state when no accounts match search query", () => {
    const onSelect = vi.fn();
    render(<GstinSwitcher accounts={mockAccounts} value="27AAPFU0939F1ZV" onSelect={onSelect} />);

    fireEvent.click(screen.getByTestId("gstin-switcher"));
    const searchInput = screen.getByTestId("gstin-switcher-search");

    fireEvent.change(searchInput, { target: { value: "NonExistentCompany" } });
    expect(screen.getByText(/no gstin matches/i)).toBeInTheDocument();
  });
});
